"""Tool: Export generated policies, reports, or change requests to S3.

Saves artifacts to the reports S3 bucket with a timestamped filename
so users can reference them in tickets, share with teams, or audit later.
"""

import json
import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Pin SigV4 + the regional endpoint. The default global endpoint (s3.amazonaws.com)
# combined with the Lambda's temporary role credentials was producing malformed
# SigV2 presigned URLs (missing the Expires param), which S3 rejects with
# AccessDenied. SigV4 generates a valid X-Amz-* signed URL that includes the
# security token and expiry.
_REGION = os.environ.get("AWS_REGION", "us-east-1")
s3_client = boto3.client("s3", region_name=_REGION, config=Config(signature_version="s3v4"))
REPORTS_BUCKET = os.environ.get("REPORTS_BUCKET", "")


def handler(event, context=None):
    """Export content to S3 reports bucket.

    Args:
        event: {
            content: str - The content to export (policy JSON, markdown report, etc.) (required)
            filename: str - Desired filename (optional, auto-generated if not provided)
            content_type: str - Type of content: policy, change_request, action_plan, blast_radius, comparison (default: report)
            role_name: str - Associated role name for filename generation (optional)
            format: str - File format: json, md, txt (default: auto-detected)
        }

    Returns:
        {
            success: bool,
            s3_key: str,
            s3_uri: str,
            presigned_url: str (valid 1 hour),
            filename: str,
            exported_at: str
        }
    """
    content = event.get("content")
    if not content:
        return {"error": "content is required"}

    # Limit content size to prevent timeout issues
    if len(content) > 50000:
        content = content[:50000] + "\n\n[... truncated for size ...]"

    if not REPORTS_BUCKET:
        return {"error": "REPORTS_BUCKET environment variable not configured"}

    content_type = event.get("content_type", "report")
    role_name = event.get("role_name", "")
    file_format = event.get("format", "")
    custom_filename = event.get("filename", "")

    try:
        # Determine file format
        if not file_format:
            if _is_json(content):
                file_format = "json"
            elif content.startswith("#") or "**" in content:
                file_format = "md"
            else:
                file_format = "txt"

        # Generate filename
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        if custom_filename:
            filename = custom_filename
        else:
            prefix = _get_prefix(content_type)
            role_part = f"-{_sanitize(role_name)}" if role_name else ""
            filename = f"{prefix}{role_part}-{timestamp}.{file_format}"

        # Build S3 key with folder structure
        folder = _get_folder(content_type)
        s3_key = f"{folder}/{filename}"

        # Upload to S3
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

        # Generate presigned URL (valid 1 hour — max reliable with Lambda STS credentials)
        presigned_url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": REPORTS_BUCKET, "Key": s3_key},
            ExpiresIn=3600,
        )

        s3_uri = f"s3://{REPORTS_BUCKET}/{s3_key}"

        return {
            "success": True,
            "filename": filename,
            "s3_path": f"s3://{REPORTS_BUCKET}/{s3_key}",
            "download_url": presigned_url,
            "valid_for": "1 hour",
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "note": "File stored permanently. Say 'list my exports' or 'get a new link for [filename]' anytime to retrieve it.",
        }

    except Exception as e:
        logger.error(f"Error exporting to S3: {e}", exc_info=True)
        return {"error": str(e), "success": False}


def _is_json(content: str) -> bool:
    """Check if content is valid JSON."""
    try:
        json.loads(content)
        return True
    except (json.JSONDecodeError, TypeError):
        return False


def _sanitize(name: str) -> str:
    """Sanitize a name for use in filenames."""
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name).strip("-")


def _get_prefix(content_type: str) -> str:
    """Get filename prefix based on content type."""
    return {
        "policy": "policy",
        "change_request": "change-request",
        "action_plan": "action-plan",
        "blast_radius": "blast-radius",
        "comparison": "role-comparison",
        "report": "report",
    }.get(content_type, "export")


def _get_folder(content_type: str) -> str:
    """Get S3 folder based on content type."""
    return {
        "policy": "policies",
        "change_request": "change-requests",
        "action_plan": "action-plans",
        "blast_radius": "blast-radius-reports",
        "comparison": "comparisons",
        "report": "reports",
    }.get(content_type, "exports")
