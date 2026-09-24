"""Tool: List exported reports and generate fresh download links.

Lists artifacts previously saved to the reports bucket, and returns a
Cognito-authed download URL for a specific file on request.

Downloads are served by the ``GET /downloads/{proxy+}`` API Gateway
route (see ``src/download.py``). This tool returns URLs pointing at that
route — NOT S3 presigned URLs. Presigned URLs were dropped after boto3
1.42.97 began producing role-chained STS-signed URLs that S3 rejected
as ``InvalidToken``; head_object with the same creds worked, so the URL
itself was the fault. The API GW proxy sidesteps that class entirely.
"""

import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_REGION = os.environ.get("AWS_REGION", "us-east-1")
REPORTS_BUCKET = os.environ.get("REPORTS_BUCKET", "")
# API Gateway invoke URL, injected by ApiConstruct via add_environment
# after both this Lambda and the API are created. Empty means the CDK
# wiring hasn't been applied.
API_ENDPOINT = os.environ.get("API_ENDPOINT", "")


def _coverage(state: str, detail: str, count: int | None = None) -> dict:
    """Build an S3 coverage entry per the #171 contract."""
    entry = {"source": "s3", "state": state, "detail": detail.format(region=_REGION)}
    if count is not None:
        entry["count"] = count
    return entry


# Module-level S3 client for warm-invocation reuse.
s3_client = boto3.client("s3")


def handler(event, context=None):
    """List exported reports or generate a fresh download link.

    Args:
        event: {
            action: "list" (default) or "get_link"
            filename: str  — required for get_link
            prefix: str    — optional S3 prefix filter (e.g. "policies/")
            limit: int     — max files to return (default 20, max 50)
        }

    Returns (list):    {files: [...], total_count, bucket, note, coverage}
    Returns (get_link):{filename, s3_path, download_url, coverage}
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

    logger.info("list_exports action=%s prefix=%r limit=%d", action, prefix, limit)

    try:
        if action == "get_link":
            return _get_fresh_link(filename)
        return _list_files(prefix, limit)
    except Exception as e:
        logger.error(f"Error in list_exports: {e}", exc_info=True)
        return {
            "error": str(e),
            "coverage": [_coverage(
                "unavailable",
                f"S3 exports listing failed in {{region}}: {type(e).__name__}: {e}",
            )],
        }


def _list_files(prefix: str, limit: int) -> dict:
    """List exported files. Metadata only — no URLs. Customers ask for a
    fresh link on any specific file via the get_link action.
    """
    try:
        params = {"Bucket": REPORTS_BUCKET, "MaxKeys": limit}
        if prefix:
            params["Prefix"] = prefix

        response = s3_client.list_objects_v2(**params)
        contents = response.get("Contents", [])

        if not contents:
            return {
                "files": [],
                "total_count": 0,
                "message": (
                    "No exported reports found. Generate a policy or action "
                    "plan, then ask me to export it."
                ),
                "coverage": [_coverage(
                    "empty",
                    "S3 reports bucket in {region}: 0 objects",
                    count=0,
                )],
            }

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
                "File list only (no download URLs). To download a file, ask "
                "for a link for a specific filename."
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


def _get_fresh_link(filename: str) -> dict:
    """Resolve `filename` to its S3 key and return a Cognito-authed
    download URL. If the CDK wiring for API_ENDPOINT isn't in place,
    return a clear error so a customer sees actionable text rather than
    a broken link.
    """
    if not filename:
        return {
            "error": "filename is required for get_link action",
            "coverage": [_coverage(
                "unavailable",
                "S3 not reached in {region}: missing required 'filename' argument",
            )],
        }

    try:
        target_key = None
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=REPORTS_BUCKET):
            for obj in page.get("Contents", []):
                # Match by suffix or substring — the user usually pastes
                # just the basename ("policy-ApolloRole-...json"), not the
                # full "policies/policy-ApolloRole-...json" key.
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

        download_url = _build_download_url(target_key)

        return {
            "filename": filename,
            "s3_path": f"s3://{REPORTS_BUCKET}/{target_key}",
            "download_url": download_url,
            "coverage": [_coverage(
                "checked",
                "S3 reports bucket in {region}: matched key",
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


def _build_download_url(s3_key: str) -> str:
    """Construct a Cognito-authed download URL. Same helper as
    export_report._build_download_url — the two tools are the only
    callers of the /downloads/ route.
    """
    if not API_ENDPOINT:
        return ""
    endpoint = API_ENDPOINT if API_ENDPOINT.endswith("/") else API_ENDPOINT + "/"
    return f"{endpoint}downloads/{s3_key}"
