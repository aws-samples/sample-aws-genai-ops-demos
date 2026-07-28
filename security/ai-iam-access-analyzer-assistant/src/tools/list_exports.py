"""Tool: List exported reports and generate fresh download links.

Lists all previously exported artifacts from the reports S3 bucket,
with the ability to generate fresh presigned URLs for any file.
"""

import json
import logging
import os
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")
REPORTS_BUCKET = os.environ.get("REPORTS_BUCKET", "")


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
        return {"error": "S3 export not configured. Use the 'Save as .md' button for local downloads instead."}

    action = event.get("action", "list")
    filename = event.get("filename", "")
    prefix = event.get("prefix", "")
    limit = min(event.get("limit", 20), 50)

    try:
        if action == "get_link":
            return _get_fresh_link(filename)
        else:
            return _list_files(prefix, limit)

    except Exception as e:
        logger.error(f"Error in list_exports: {e}", exc_info=True)
        return {"error": str(e)}


def _list_files(prefix: str, limit: int) -> dict:
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
            }

        files = []
        for obj in sorted(contents, key=lambda x: x["LastModified"], reverse=True):
            key = obj["Key"]
            # Generate fresh presigned URL
            url = s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": REPORTS_BUCKET, "Key": key},
                ExpiresIn=3600,
            )

            # Parse folder and filename from key
            parts = key.split("/")
            folder = parts[0] if len(parts) > 1 else ""
            fname = parts[-1]

            files.append({
                "filename": fname,
                "folder": folder,
                "s3_path": f"s3://{REPORTS_BUCKET}/{key}",
                "size_bytes": obj["Size"],
                "last_modified": obj["LastModified"].strftime("%Y-%m-%d %H:%M UTC"),
                "download_url": url,
            })

        return {
            "files": files,
            "total_count": len(files),
            "bucket": REPORTS_BUCKET,
        }

    except Exception as e:
        return {"error": str(e)}


def _get_fresh_link(filename: str) -> dict:
    """Generate a fresh presigned URL for a specific file."""
    if not filename:
        return {"error": "filename is required for get_link action"}

    try:
        # Try to find the file — it might be in a subfolder
        response = s3_client.list_objects_v2(
            Bucket=REPORTS_BUCKET,
            MaxKeys=10,
        )

        # Search for the filename across all prefixes
        target_key = None
        for obj in response.get("Contents", []):
            if obj["Key"].endswith(filename) or filename in obj["Key"]:
                target_key = obj["Key"]
                break

        if not target_key:
            return {"error": f"File '{filename}' not found in exports bucket."}

        url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": REPORTS_BUCKET, "Key": target_key},
            ExpiresIn=3600,
        )

        return {
            "filename": filename,
            "s3_path": f"s3://{REPORTS_BUCKET}/{target_key}",
            "download_url": url,
            "valid_for": "1 hour",
        }

    except Exception as e:
        return {"error": str(e)}
