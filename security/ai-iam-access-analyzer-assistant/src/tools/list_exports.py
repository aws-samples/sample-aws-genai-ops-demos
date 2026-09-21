"""Tool: List exported reports and generate fresh download links.

Lists all previously exported artifacts from the reports S3 bucket,
with the ability to generate fresh presigned URLs for any file.
"""

import json
import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# See src/tools/export_report.py for the rationale: sign with a dedicated
# presigner role we assume ourselves so download URLs are reliable for their
# full X-Amz-Expires, and fall back to short-lived Lambda credentials only if
# the presigner role isn't wired in.
_REGION = os.environ.get("AWS_REGION", "us-east-1")
_S3_CONFIG = Config(signature_version="s3v4")
REPORTS_BUCKET = os.environ.get("REPORTS_BUCKET", "")
PRESIGNER_ROLE_ARN = os.environ.get("PRESIGNER_ROLE_ARN", "")
PRESIGN_TTL_SECONDS = 3600


def _coverage(state: str, detail: str, count: int | None = None) -> dict:
    """Build an S3 coverage entry per the #171 contract."""
    entry = {"source": "s3", "state": state, "detail": detail.format(region=_REGION)}
    if count is not None:
        entry["count"] = count
    return entry


class _SigningContext:
    """Split read + sign clients so bucket listing rides on the Lambda role
    and only presigning uses the assumed presigner role.
    """

    def __init__(self, read_client, sign_client, expires_in: int, source: str):
        self.read_client = read_client
        self.sign_client = sign_client
        self.expires_in = expires_in
        self.source = source


def _build_lambda_role_s3():
    session = boto3.session.Session(region_name=_REGION)
    frozen = session.get_credentials().get_frozen_credentials()
    return session.client(
        "s3",
        region_name=_REGION,
        config=_S3_CONFIG,
        aws_access_key_id=frozen.access_key,
        aws_secret_access_key=frozen.secret_key,
        aws_session_token=frozen.token,
    )


def _build_signing_context() -> "_SigningContext":
    read_client = _build_lambda_role_s3()

    if PRESIGNER_ROLE_ARN:
        try:
            sts = boto3.client("sts", region_name=_REGION)
            creds = sts.assume_role(
                RoleArn=PRESIGNER_ROLE_ARN,
                RoleSessionName="list-exports-presigner",
                DurationSeconds=PRESIGN_TTL_SECONDS,
            )["Credentials"]
            sign_client = boto3.client(
                "s3",
                region_name=_REGION,
                config=_S3_CONFIG,
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
            )
            return _SigningContext(
                read_client, sign_client, PRESIGN_TTL_SECONDS, "assumed_role"
            )
        except Exception as assume_err:
            logger.warning(
                "assume_role for presigner failed; falling back to Lambda role "
                "credentials for signing (with a short URL expiry): %s",
                assume_err,
            )

    return _SigningContext(read_client, read_client, 300, "lambda_role")


def _build_s3_client():
    """Backwards-compatible helper; returns a Lambda-role S3 client suitable
    for reads. New code should call _build_signing_context().
    """
    return _build_lambda_role_s3()


def handler(event, context=None):
    """List exported reports or generate a fresh download link.

    Args:
        event: {
            action: str - "list" (default) or "get_link"
            filename: str - specific filename to generate a link for (required for get_link)
            prefix: str - S3 prefix to filter by (optional, e.g. "policies/", "change-requests/")
            limit: int - max files to return (default: 20)
        }

    Returns:
        For "list": {files: [{filename, folder, size, last_modified, download_url}], total_count}
        For "get_link": {filename, download_url, valid_for}
    """
    if not REPORTS_BUCKET:
        return {
            "error": "S3 export not configured. Use the 'Save as .md' button for local downloads instead.",
            "coverage": [_coverage(
                "unavailable",
                "S3 not reachable in {region}: REPORTS_BUCKET env var not set",
            )],
        }

    action = event.get("action", "list")
    filename = event.get("filename", "")
    prefix = event.get("prefix", "")
    limit = min(event.get("limit", 20), 50)

    try:
        signing = _build_signing_context()
        logger.info("list_exports signing method=%s action=%s", signing.source, action)
        if action == "get_link":
            return _get_fresh_link(signing, filename)
        else:
            return _list_files(signing.read_client, prefix, limit)

    except Exception as e:
        logger.error(f"Error in list_exports: {e}", exc_info=True)
        return {
            "error": str(e),
            "coverage": [_coverage(
                "unavailable",
                f"S3 exports listing failed in {{region}}: {type(e).__name__}: {e}",
            )],
        }


def _list_files(s3_client, prefix: str, limit: int) -> dict:
    """List all exported files in the bucket."""
    try:
        params = {
            "Bucket": REPORTS_BUCKET,
            "MaxKeys": limit,
        }
        if prefix:
            params["Prefix"] = prefix

        response = s3_client.list_objects_v2(**params)
        contents = response.get("Contents", [])

        if not contents:
            return {
                "files": [],
                "total_count": 0,
                "message": "No exported reports found. Generate a policy or action plan, then ask me to export it.",
                "coverage": [_coverage(
                    "empty",
                    "S3 reports bucket in {region}: 0 objects",
                    count=0,
                )],
            }

        # Metadata ONLY — do NOT presign every file here. Presigned URLs are
        # ~1500 chars each; returning 15-20 of them produces a huge response that
        # gets truncated mid-URL by the model's output limit, breaking the links.
        # Download URLs are generated one at a time via the get_link action.
        files = []
        for obj in sorted(contents, key=lambda x: x["LastModified"], reverse=True):
            key = obj["Key"]
            parts = key.split("/")
            folder = parts[0] if len(parts) > 1 else ""
            fname = parts[-1]

            files.append({
                "filename": fname,
                "folder": folder,
                "s3_path": f"s3://{REPORTS_BUCKET}/{key}",
                "size_bytes": obj["Size"],
                "last_modified": obj["LastModified"].strftime("%Y-%m-%d %H:%M UTC"),
            })

        return {
            "files": files,
            "total_count": len(files),
            "bucket": REPORTS_BUCKET,
            "note": (
                "File list only (no download URLs). To download a file, ask for a "
                "link for a specific filename and a fresh download URL will be generated."
            ),
            "coverage": [_coverage(
                "checked",
                "S3 reports bucket in {region}: ListObjectsV2",
                count=len(files),
            )],
        }

    except Exception as e:
        return {
            "error": str(e),
            "coverage": [_coverage(
                "unavailable",
                f"S3 ListObjectsV2 failed in {{region}}: {type(e).__name__}: {e}",
            )],
        }


def _get_fresh_link(signing, filename: str) -> dict:
    """Generate a fresh presigned URL for a specific file."""
    if not filename:
        return {
            "error": "filename is required for get_link action",
            "coverage": [_coverage(
                "unavailable",
                "S3 not reached in {region}: missing required 'filename' argument",
            )],
        }

    try:
        # Bucket listing rides on the Lambda role.
        target_key = None
        paginator = signing.read_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=REPORTS_BUCKET):
            for obj in page.get("Contents", []):
                if obj["Key"].endswith(filename) or filename in obj["Key"]:
                    target_key = obj["Key"]
                    break
            if target_key:
                break

        if not target_key:
            return {
                "error": f"File '{filename}' not found in exports bucket.",
                "coverage": [_coverage(
                    "empty",
                    f"S3 reports bucket in {{region}}: no object matched '{filename}'",
                    count=0,
                )],
            }

        # Presigned URL rides on the (stable) assumed presigner role.
        url = signing.sign_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": REPORTS_BUCKET, "Key": target_key},
            ExpiresIn=signing.expires_in,
        )

        return {
            "filename": filename,
            "s3_path": f"s3://{REPORTS_BUCKET}/{target_key}",
            "download_url": url,
            "valid_for": _format_valid_for(signing.expires_in),
            "coverage": [_coverage(
                "checked",
                "S3 reports bucket in {region}: matched key + GetObject presign",
                count=1,
            )],
        }

    except Exception as e:
        return {
            "error": str(e),
            "coverage": [_coverage(
                "unavailable",
                f"S3 get-link failed in {{region}}: {type(e).__name__}: {e}",
            )],
        }


def _format_valid_for(seconds: int) -> str:
    if seconds >= 3600 and seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour" + ("s" if hours != 1 else "")
    if seconds >= 60:
        minutes = seconds // 60
        return f"{minutes} minutes"
    return f"{seconds} seconds"
