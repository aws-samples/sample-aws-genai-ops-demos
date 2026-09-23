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

# SigV4 + regional endpoint. Required so presigned URLs include valid X-Amz-*
# parameters. Signing with a Lambda role's rolling STS session token gave S3
# "InvalidToken" once that token rotated, even inside the URL's own
# X-Amz-Expires window. To make longer-lived download links reliable we sign
# with credentials from a dedicated presigner role we assume ourselves; those
# credentials are stable for their full DurationSeconds.
_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _coverage(state: str, detail: str) -> dict:
    """Build an S3 coverage entry per the #171 contract."""
    return {"source": "s3", "state": state, "detail": detail.format(region=_REGION)}
_S3_CONFIG = Config(signature_version="s3v4")
REPORTS_BUCKET = os.environ.get("REPORTS_BUCKET", "")
PRESIGNER_ROLE_ARN = os.environ.get("PRESIGNER_ROLE_ARN", "")
# Duration to request from AssumeRole and to use as X-Amz-Expires. Must be
# <= the presigner role's MaxSessionDuration.
PRESIGN_TTL_SECONDS = 3600


class _SigningContext:
    """Result of preparing credentials for a single invocation.

    Splits write vs presign clients so an upload never rides on the read-only
    presigner role's credentials, which was a regression when both roles used
    a single client.

    Attributes:
        write_client: S3 client backed by the Lambda's own IAM role. Used
            for put_object; that role has s3:PutObject on the reports bucket.
        sign_client: S3 client used only for generate_presigned_url. Backed
            by the assumed presigner role's credentials when available (stable
            for the full X-Amz-Expires window), otherwise falls back to the
            Lambda role's rotating credentials.
        expires_in: Matching URL expiry — 3600s when signing with assumed
            role, 300s when signing with the Lambda role.
        source: "assumed_role" or "lambda_role", for diagnostics.
    """

    def __init__(self, write_client, sign_client, expires_in: int, source: str):
        self.write_client = write_client
        self.sign_client = sign_client
        self.expires_in = expires_in
        self.source = source


def _build_lambda_role_s3():
    """S3 client that uses the Lambda's own IAM role credentials.

    A fresh session per invocation + frozen credentials avoids some Lambda
    warm-container edge cases where the cached credentials object hands back
    an already-rotating session token at signing time.
    """
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
    """Return distinct write + sign S3 clients and the matching URL expiry.

    Uploads always use the Lambda role (it has PutObject on the bucket).
    Presigned URLs prefer the assumed presigner role so they remain valid for
    the full requested expiry — Lambda's own STS token can rotate underneath
    us, which is what produced S3 InvalidToken errors on previously-issued
    URLs even inside their own X-Amz-Expires window.
    """
    write_client = _build_lambda_role_s3()

    if PRESIGNER_ROLE_ARN:
        try:
            sts = boto3.client("sts", region_name=_REGION)
            creds = sts.assume_role(
                RoleArn=PRESIGNER_ROLE_ARN,
                RoleSessionName="export-report-presigner",
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
                write_client, sign_client, PRESIGN_TTL_SECONDS, "assumed_role"
            )
        except Exception as assume_err:
            logger.warning(
                "assume_role for presigner failed; falling back to Lambda role "
                "credentials for signing (with a short URL expiry): %s",
                assume_err,
            )

    # Fallback: sign with the Lambda role, but keep the URL expiry short so
    # we stay inside the Lambda token's own lifetime.
    return _SigningContext(write_client, write_client, 300, "lambda_role")


def _build_s3_client():
    """Backwards-compatible helper: returns a Lambda-role S3 client suitable
    for reads and writes. New code should call _build_signing_context() and
    use its write_client / sign_client explicitly.
    """
    return _build_lambda_role_s3()


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
        return {
            "error": "content is required",
            "coverage": [_coverage(
                "unavailable",
                "S3 not reached in {region}: missing required 'content' argument",
            )],
        }

    # Limit content size to prevent timeout issues
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
        signing = _build_signing_context()
        # Diagnostics reflect the SIGNING credentials (that's the thing that
        # can rotate underneath a presigned URL). The upload rides on the
        # separate write_client backed by the Lambda role.
        _log_signing_diagnostics(signing.sign_client)
        logger.info("export_report signing method=%s", signing.source)
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

        # Upload runs as the Lambda role — the presigner role is read-only.
        signing.write_client.put_object(
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

        # URL expiry matches the signing credentials' lifetime (1 hour when
        # using the presigner role, or 5 minutes if we had to fall back to
        # Lambda's own rotating token).
        presigned_url = signing.sign_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": REPORTS_BUCKET, "Key": s3_key},
            ExpiresIn=signing.expires_in,
        )
        _log_url_diagnostics(presigned_url)

        s3_uri = f"s3://{REPORTS_BUCKET}/{s3_key}"

        return {
            "success": True,
            "filename": filename,
            "s3_path": f"s3://{REPORTS_BUCKET}/{s3_key}",
            "download_url": presigned_url,
            "valid_for": _format_valid_for(signing.expires_in),
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "note": "File stored permanently. Say 'list my exports' or 'get a new link for [filename]' anytime to retrieve it.",
            "coverage": [_coverage(
                "checked",
                f"S3 reports bucket in {{region}}: PutObject + GetObject presign",
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


def _format_valid_for(seconds: int) -> str:
    """Human-readable "valid for" string for user copy."""
    if seconds >= 3600 and seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour" + ("s" if hours != 1 else "")
    if seconds >= 60:
        minutes = seconds // 60
        return f"{minutes} minutes"
    return f"{seconds} seconds"


def _log_url_diagnostics(url: str) -> None:
    """Log non-secret shape info about the presigned URL so we can detect any
    encoding mismatch between what Lambda produced and what the browser opens.
    """
    try:
        params = {}
        query_start = url.find("?")
        query = url[query_start + 1 :] if query_start != -1 else ""
        for chunk in query.split("&"):
            if "=" in chunk:
                key, value = chunk.split("=", 1)
                params[key] = value
        token_param = params.get("X-Amz-Security-Token", "")
        logger.info(
            "export_report url diagnostics: "
            "url_len=%s query_param_count=%s "
            "has_algorithm=%s has_credential=%s has_signature=%s "
            "token_param_len=%s token_has_percent=%s "
            "token_prefix=%s token_suffix=%s",
            len(url),
            len(params),
            "X-Amz-Algorithm" in params,
            "X-Amz-Credential" in params,
            "X-Amz-Signature" in params,
            len(token_param),
            "%" in token_param,
            token_param[:12],
            token_param[-12:],
        )
    except Exception as diag_err:  # pragma: no cover - diagnostics only
        logger.warning("export_report url diagnostics failed: %s", diag_err)


def _log_signing_diagnostics(s3_client) -> None:
    """Emit a redacted diagnostic snapshot before a presigned URL is created.

    This intentionally logs identity metadata and non-secret token shape so we
    can pin down why S3 rejects presigned URLs with InvalidToken. It never
    logs the raw secret_key or the full session_token — only their lengths and
    the first/last few characters so we can detect truncation, wrapping, or
    boundary/partition mismatch.
    """
    try:
        import boto3 as _boto3

        session = _boto3.session.Session(region_name=_REGION)
        credentials = session.get_credentials()
        frozen = credentials.get_frozen_credentials()
        method = getattr(credentials, "method", "unknown")
        access_key = frozen.access_key or ""
        token = frozen.token or ""

        sts = session.client("sts", region_name=_REGION)
        identity = sts.get_caller_identity()

        logger.info(
            "export_report signing diagnostics: "
            "boto3_version=%s botocore_version=%s method=%s "
            "region=%s caller_arn=%s caller_account=%s "
            "access_key_prefix=%s access_key_len=%s "
            "token_present=%s token_len=%s token_prefix=%s token_suffix=%s "
            "bucket=%s",
            _boto3.__version__,
            getattr(_boto3, "__version__", "?"),
            method,
            _REGION,
            identity.get("Arn"),
            identity.get("Account"),
            access_key[:6],
            len(access_key),
            bool(token),
            len(token),
            token[:8] if token else "",
            token[-8:] if token else "",
            REPORTS_BUCKET,
        )
    except Exception as diag_err:  # pragma: no cover - diagnostics only
        logger.warning("export_report diagnostics failed: %s", diag_err)


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
