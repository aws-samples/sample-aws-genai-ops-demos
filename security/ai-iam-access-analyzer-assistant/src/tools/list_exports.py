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

# Pin SigV4 + regional endpoint so presigned download links are valid. The
# default global endpoint + temporary Lambda credentials produced malformed
# SigV2 URLs that S3 rejected with AccessDenied.
_REGION = os.environ.get("AWS_REGION", "us-east-1")
s3_client = boto3.client("s3", region_name=_REGION, config=Config(signature_version="s3v4"))
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
        }

    except Exception as e:
        return {"error": str(e)}


def _get_fresh_link(filename: str) -> dict:
    """Generate a fresh presigned URL for a specific file."""
    if not filename:
        return {"error": "filename is required for get_link action"}

    try:
        # Search ALL objects (paginated) for the filename — the old code only
        # looked at the first 10 keys, so files beyond that were "not found".
        target_key = None
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=REPORTS_BUCKET):
            for obj in page.get("Contents", []):
                if obj["Key"].endswith(filename) or filename in obj["Key"]:
                    target_key = obj["Key"]
                    break
            if target_key:
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
