"""API Gateway proxy download handler.

Replaces S3 presigned URLs with a Cognito-authed GET endpoint. Customers
click a `[Download here]` link in the assistant response; the frontend
recognizes the ``/downloads/`` path, fetches this endpoint with the
current Cognito session's Bearer token, and triggers a Blob download
locally.

Why not S3 presigned URLs? boto3 1.42.97's generate_presigned_url with
role-chained STS session tokens produces URLs that S3 rejects as
``InvalidToken`` — the credentials themselves work for direct API calls
(verified via head_object probes) but the URL variant is refused. This
handler sidesteps the entire class of presigned-URL fragility.

Security posture (see the SECURITY comment block below).
"""

import base64
import json
import logging
import os
import urllib.parse
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_REGION = os.environ.get("AWS_REGION", "us-east-1")
REPORTS_BUCKET = os.environ.get("REPORTS_BUCKET", "")

# --- Security constants -----------------------------------------------------
#
# Prefix allowlist. The S3 key's FIRST path segment must be one of these,
# and MUST match exactly (case-sensitive). This is the same set the export
# tools use in tools/export_report.py:_get_folder() / list_exports.py.
# Any other prefix returns 404 (do not leak existence).
_ALLOWED_PREFIXES = frozenset({
    "policies",
    "change-requests",
    "action-plans",
    "blast-radius-reports",
    "comparisons",
    "reports",
    "exports",
})

# Defensive limit on the S3 key. Real keys are under 200 chars.
_MAX_KEY_LEN = 512

# Reject the object if its raw size exceeds this. Base64 adds ~33% overhead,
# and API Gateway caps a Lambda proxy response at 10 MB total. 5 MB raw
# leaves headroom for the JSON envelope + base64 expansion. Objects larger
# than this: 413 with an aws-s3-cp fallback path.
_MAX_OBJECT_BYTES = 5 * 1024 * 1024

# Character set forbidden in the S3 key (post URL-decode). Nulls, CR/LF,
# stray percent signs (implies double-encoding), and backslashes are all
# rejected. Regular filename chars (letters, digits, `-`, `_`, `.`, `/`)
# are allowed by NOT being in this set.
_FORBIDDEN_KEY_CHARS = "\x00\r\n\t\\%"

s3_client = boto3.client("s3")


def handler(event: dict, context: Any = None) -> dict:
    """API Gateway proxy handler for GET /downloads/{proxy+}."""
    try:
        return _handle(event)
    except Exception as e:  # noqa: BLE001 — top-level guard, log and 500
        logger.exception("download handler crashed: %s", e)
        return _response(
            500,
            {"error": "Internal error retrieving the requested object."},
        )


def _handle(event: dict) -> dict:
    if not REPORTS_BUCKET:
        logger.error("REPORTS_BUCKET env var not configured")
        return _response(500, {"error": "Bucket not configured."})

    caller_email = _caller_identity(event)
    raw_proxy = _extract_proxy_path(event)

    if raw_proxy is None:
        logger.warning("download denied: no proxy path in request (caller=%s)", caller_email)
        return _not_found()

    # URL-decode ONCE. If the decoded value still contains %XX, that's
    # double-encoding — reject to avoid ambiguity attacks.
    try:
        s3_key = urllib.parse.unquote(raw_proxy, errors="strict")
    except UnicodeDecodeError:
        logger.warning(
            "download denied: undecodable proxy path (caller=%s, len=%d)",
            caller_email, len(raw_proxy),
        )
        return _not_found()

    ok, reason = _validate_key(s3_key)
    if not ok:
        # Log the reason for observability but return a uniform 404 so
        # callers can't distinguish validation failure from missing-object.
        logger.warning(
            "download denied: %s (caller=%s, key=%r)",
            reason, caller_email, s3_key,
        )
        return _not_found()

    # HEAD first to check size before pulling the object body. Also
    # naturally handles NoSuchKey without transferring bytes we won't use.
    try:
        head = s3_client.head_object(Bucket=REPORTS_BUCKET, Key=s3_key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound"):
            logger.info(
                "download 404: object not found (caller=%s, key=%s)",
                caller_email, s3_key,
            )
            return _not_found()
        logger.error(
            "download failed on head_object (caller=%s, key=%s): %s: %s",
            caller_email, s3_key, code, e,
        )
        return _response(500, {"error": "Failed to look up the object."})

    content_length = int(head.get("ContentLength", 0))
    if content_length > _MAX_OBJECT_BYTES:
        logger.info(
            "download 413: object too large (caller=%s, key=%s, size=%d, cap=%d)",
            caller_email, s3_key, content_length, _MAX_OBJECT_BYTES,
        )
        return _response(413, {
            "error": (
                f"File is {_human_bytes(content_length)}, above the "
                f"{_human_bytes(_MAX_OBJECT_BYTES)} inline-download cap. "
                f"Retrieve directly with: aws s3 cp "
                f"s3://{REPORTS_BUCKET}/{s3_key} ./"
            ),
            "s3_key": s3_key,
            "size_bytes": content_length,
        })

    content_type = head.get("ContentType") or "application/octet-stream"

    try:
        obj = s3_client.get_object(Bucket=REPORTS_BUCKET, Key=s3_key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound"):
            # Race between head_object and get_object — object was deleted.
            logger.info(
                "download 404: object deleted between head and get (caller=%s, key=%s)",
                caller_email, s3_key,
            )
            return _not_found()
        logger.error(
            "download failed on get_object (caller=%s, key=%s): %s: %s",
            caller_email, s3_key, code, e,
        )
        return _response(500, {"error": "Failed to retrieve the object."})

    body_bytes = obj["Body"].read()
    filename = s3_key.rsplit("/", 1)[-1]

    logger.info(
        "download 200 (caller=%s, key=%s, size=%d, content_type=%s)",
        caller_email, s3_key, len(body_bytes), content_type,
    )

    return _response(200, {
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(body_bytes),
        "content_b64": base64.b64encode(body_bytes).decode("ascii"),
        "s3_key": s3_key,
    })


# ---------------------------------------------------------------------------
# Request parsing + security checks
# ---------------------------------------------------------------------------


def _extract_proxy_path(event: dict) -> str | None:
    """Get the value captured by the {proxy+} path parameter.

    API Gateway REST APIs put this at ``event["pathParameters"]["proxy"]``.
    If the caller hit ``/downloads`` with no proxy at all, pathParameters
    may be None. Return None; the caller returns 404.
    """
    params = event.get("pathParameters") or {}
    proxy = params.get("proxy")
    if not proxy or not isinstance(proxy, str):
        return None
    return proxy


def _validate_key(key: str) -> tuple[bool, str]:
    """Return (ok, reason). Reasons are for LOGS ONLY; the caller returns
    a uniform 404 either way.

    SECURITY: every check here has to pass for the object to be served.
    Any change to this list should walk through:
      1. Does it prevent path-traversal into other S3 prefixes?
      2. Does it prevent path-traversal outside the bucket via URL games?
      3. Does it prevent leaking existence of unrelated objects?
      4. Does it accept every legitimate export the tools produce?

    The export tools always produce keys of the form
    ``<prefix>/<basename>`` with basename constrained to safe chars.
    """
    if not key:
        return False, "empty key"
    if len(key) > _MAX_KEY_LEN:
        return False, "key too long"
    if key.startswith("/"):
        return False, "absolute path"
    if ".." in key:
        return False, "path traversal (..)"
    if "//" in key:
        return False, "empty segment (//)"
    if any(ch in key for ch in _FORBIDDEN_KEY_CHARS):
        return False, "forbidden character"

    segments = key.split("/")
    if len(segments) < 2:
        return False, "missing prefix"
    prefix = segments[0]
    if prefix not in _ALLOWED_PREFIXES:
        return False, f"prefix {prefix!r} not in allowlist"

    # Basename check — no absolute-path escape, no empty basename.
    basename = segments[-1]
    if not basename:
        return False, "empty basename"
    if basename in (".", ".."):
        return False, "basename is dot"

    return True, ""


def _caller_identity(event: dict) -> str:
    """Extract the Cognito email/username from the authorizer claims for
    the audit log. Never fail the request if this is missing — the
    Cognito authorizer already verified auth at the API GW layer; we just
    log whatever we can find.
    """
    try:
        claims = (
            event.get("requestContext", {})
            .get("authorizer", {})
            .get("claims", {})
        )
        return (
            claims.get("email")
            or claims.get("cognito:username")
            or claims.get("sub")
            or "unknown"
        )
    except Exception:  # noqa: BLE001 — diagnostic only
        return "unknown"


# ---------------------------------------------------------------------------
# Response envelope
# ---------------------------------------------------------------------------


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": _cors_headers(),
        "body": json.dumps(body),
    }


def _not_found() -> dict:
    return _response(404, {"error": "Not found."})


def _cors_headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "GET,OPTIONS",
    }


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n} B"
