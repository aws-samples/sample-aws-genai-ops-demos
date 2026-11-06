"""Tool: Export generated policies, reports, or change requests to S3.

Saves artifacts to the reports bucket with a timestamped filename so
customers can reference them in tickets, share with teams, or audit later.

Downloads are served by the Cognito-authed ``GET /downloads/{proxy+}``
API Gateway route (see ``src/download.py``). ``download_url`` in this
tool's response points at that route, NOT an S3 presigned URL. Presigned
URLs used to be the transport but were removed after boto3 1.42.97's
``generate_presigned_url`` began producing role-chained STS-signed URLs
that S3 rejected as ``InvalidToken`` — head_object with the same creds
worked fine, so the URL variant was specifically the fault. The proxy
route sidesteps the entire class of presigned-URL fragility.
"""

import json
import logging
import os
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_REGION = os.environ.get("AWS_REGION", "us-east-1")
REPORTS_BUCKET = os.environ.get("REPORTS_BUCKET", "")
# API Gateway invoke URL, injected by ApiConstruct via add_environment
# after both this Lambda and the API are created. Empty string means the
# CDK wiring hasn't been applied — customers see a clear error in that
# case rather than a broken URL.
API_ENDPOINT = os.environ.get("API_ENDPOINT", "")


def _coverage(state: str, detail: str) -> dict:
    """Build an S3 coverage entry per the #171 contract."""
    return {"source": "s3", "state": state, "detail": detail.format(region=_REGION)}


# Module-level S3 client for warm-invocation reuse. Uses the Lambda role's
# own credentials, which have PutObject on the reports bucket (see
# ToolExecutionRole in tools_construct.py).
s3_client = boto3.client("s3")


def handler(event, context=None):
    """Export content to S3 reports bucket.

    Args:
        event: {
            content: str            - The content to export (required)
            filename: str           - Desired filename (optional)
            content_type: str       - policy / change_request / action_plan /
                                       blast_radius / comparison / report
                                       (default: report)
            role_name: str          - Associated role name for filename
                                       (optional)
            format: str             - json / md / txt (default: auto-detect)
        }

    Returns:
        {
            success: bool,
            filename: str,
            s3_path: "s3://<bucket>/<key>",
            download_url: "<api-endpoint>downloads/<key>",  # Cognito-authed
            exported_at: ISO timestamp,
            note: str,
            coverage: [ {source: "s3", state, detail} ]
        }
    """
    content = event.get("content")
    if not content:
        return {
            "error": "content is required",
            "coverage": [_coverage(
                "unavailable",
                "S3 not reached in {region}: missing required 'content' argument",
            )],
        }

    # Guard runaway payloads. Real artifacts are far under this.
    if len(content) > 50000:
        content = content[:50000] + "\n\n[... truncated for size ...]"

    if not REPORTS_BUCKET:
        return {
            "error": "REPORTS_BUCKET environment variable not configured",
            "coverage": [_coverage(
                "unavailable",
                "S3 not reachable in {region}: REPORTS_BUCKET env var not set",
            )],
        }

    content_type = event.get("content_type", "report")
    role_name = event.get("role_name", "")
    file_format = event.get("format", "")
    custom_filename = event.get("filename", "")

    try:
        # File format auto-detect
        if not file_format:
            if _is_json(content):
                file_format = "json"
            elif content.startswith("#") or "**" in content:
                file_format = "md"
            else:
                file_format = "txt"

        # Filename
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        if custom_filename:
            filename = custom_filename
        else:
            prefix = _get_prefix(content_type)
            role_part = f"-{_sanitize(role_name)}" if role_name else ""
            filename = f"{prefix}{role_part}-{timestamp}.{file_format}"

        folder = _get_folder(content_type)
        s3_key = f"{folder}/{filename}"

        content_type_header = {
            "json": "application/json",
            "md": "text/markdown",
            "txt": "text/plain",
        }.get(file_format, "text/plain")

        s3_client.put_object(
            Bucket=REPORTS_BUCKET,
            Key=s3_key,
            Body=content.encode("utf-8"),
            ContentType=content_type_header,
            Metadata={
                "content-type": content_type,
                "role-name": role_name or "none",
                "exported-by": "iam-analyzer-assistant",
                "timestamp": timestamp,
            },
        )

        download_url = _build_download_url(s3_key)

        logger.info(
            "export_report saved: key=%s size=%d content_type=%s "
            "download_via=%s",
            s3_key,
            len(content.encode("utf-8")),
            content_type_header,
            "api_gw_proxy" if API_ENDPOINT else "unavailable",
        )

        return {
            "success": True,
            "filename": filename,
            "s3_path": f"s3://{REPORTS_BUCKET}/{s3_key}",
            "download_url": download_url,
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "note": (
                "File stored permanently. Say 'list my exports' anytime to "
                "retrieve it, or 'get me a link for [filename]' for a fresh "
                "download."
            ),
            "coverage": [_coverage(
                "checked",
                "S3 reports bucket in {region}: PutObject succeeded",
            )],
        }

    except Exception as e:
        logger.error(f"Error exporting to S3: {e}", exc_info=True)
        return {
            "error": str(e),
            "success": False,
            "coverage": [_coverage(
                "unavailable",
                f"S3 export failed in {{region}}: {type(e).__name__}: {e}",
            )],
        }


def _build_download_url(s3_key: str) -> str:
    """Construct a Cognito-authed download URL.

    ``API_ENDPOINT`` from CDK already ends with a slash (RestApi.url
    convention). If it doesn't for some reason, we normalize once here so
    the resulting URL is always well-formed. Missing API_ENDPOINT → empty
    string; the tool response makes the S3 path visible so a customer can
    still retrieve via AWS CLI.
    """
    if not API_ENDPOINT:
        return ""
    endpoint = API_ENDPOINT if API_ENDPOINT.endswith("/") else API_ENDPOINT + "/"
    return f"{endpoint}downloads/{s3_key}"


def _is_json(content: str) -> bool:
    try:
        json.loads(content)
        return True
    except (json.JSONDecodeError, TypeError):
        return False


def _sanitize(name: str) -> str:
    """Sanitize a name for use in filenames."""
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name).strip("-")


def _get_prefix(content_type: str) -> str:
    return {
        "policy": "policy",
        "change_request": "change-request",
        "action_plan": "action-plan",
        "blast_radius": "blast-radius",
        "comparison": "role-comparison",
        "report": "report",
    }.get(content_type, "export")


def _get_folder(content_type: str) -> str:
    """S3 folder for a given content_type. Must stay in sync with
    download.py's _ALLOWED_PREFIXES — a folder outside the allowlist would
    save a file that customers can't retrieve via the download endpoint.
    """
    return {
        "policy": "policies",
        "change_request": "change-requests",
        "action_plan": "action-plans",
        "blast_radius": "blast-radius-reports",
        "comparison": "comparisons",
        "report": "reports",
    }.get(content_type, "exports")
