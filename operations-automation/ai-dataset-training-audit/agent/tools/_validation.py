"""Path-safety validation and S3/local resolution.

Single chokepoint every tool routes caller-supplied paths through (FR-7.3). Rejects
malformed URIs, path traversal, and off-allowlist paths before any read or write is
performed. Temporary S3 downloads are always cleaned up by the caller via the ``is_tmp``
flag returned from :func:`resolve_to_local_file`.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from config import Config

# s3://bucket/key — bucket then a non-empty key.
_S3_URI_RE = re.compile(r"^s3://([^/]+)/(.+)$")
# S3 bucket naming (simplified but strict enough to reject obviously bad names).
_S3_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$")


def _allowlist(raw: str) -> list[str]:
    """Split a comma-separated allowlist env value; empty -> empty list (allow any)."""
    return [item.strip() for item in raw.split(",") if item.strip()]


def is_s3_uri(path: str) -> bool:
    """True if ``path`` looks like an ``s3://`` URI."""
    return isinstance(path, str) and path.startswith("s3://")


def validate_s3_uri(path: str) -> tuple[str, str]:
    """Validate an ``s3://bucket/key`` URI and return ``(bucket, key)``.

    Rejects traversal (``..``), leading-slash keys, malformed buckets, and paths outside
    the ``ALLOWED_S3_BUCKETS`` / ``ALLOWED_S3_PREFIXES`` allowlists when those are set.
    """
    if not isinstance(path, str):
        raise ValueError("Rejected dataset path: not a string")

    match = _S3_URI_RE.match(path)
    if not match:
        raise ValueError(f"Rejected dataset path: malformed S3 URI: {path!r}")

    bucket, key = match.group(1), match.group(2)

    if not _S3_BUCKET_RE.match(bucket):
        raise ValueError(f"Rejected dataset path: invalid S3 bucket name: {bucket!r}")
    if key.startswith("/"):
        raise ValueError("Rejected dataset path: S3 key must not start with '/'")
    if ".." in key or ".." in bucket:
        raise ValueError("Rejected dataset path: path traversal ('..') not allowed")

    allowed_buckets = _allowlist(Config.ALLOWED_S3_BUCKETS)
    if allowed_buckets and bucket not in allowed_buckets:
        raise ValueError(
            f"Rejected dataset path: bucket {bucket!r} not in ALLOWED_S3_BUCKETS"
        )

    allowed_prefixes = _allowlist(Config.ALLOWED_S3_PREFIXES)
    if allowed_prefixes and not any(key.startswith(p) for p in allowed_prefixes):
        raise ValueError(
            f"Rejected dataset path: key {key!r} not under any ALLOWED_S3_PREFIXES"
        )

    return bucket, key


def validate_local_path(path: str) -> Path:
    """Validate a local input path and return its resolved :class:`Path`.

    Rejects traversal and paths outside ``ALLOWED_LOCAL_BASE_DIR`` when that is set.
    """
    if not isinstance(path, str) or not path:
        raise ValueError("Rejected dataset path: empty local path")
    if ".." in Path(path).parts:
        raise ValueError("Rejected dataset path: path traversal ('..') not allowed")

    resolved = Path(path).expanduser().resolve()
    _enforce_base_dir(resolved)
    return resolved


def validate_output_path(path: str) -> Path:
    """Validate an output path; its parent must exist/be inside the allowed base dir.

    The output file need not exist, but its parent directory is enforced against
    ``ALLOWED_LOCAL_BASE_DIR`` when that is set.
    """
    if not isinstance(path, str) or not path:
        raise ValueError("Rejected output path: empty path")
    if ".." in Path(path).parts:
        raise ValueError("Rejected output path: path traversal ('..') not allowed")

    resolved = Path(path).expanduser().resolve()
    _enforce_base_dir(resolved.parent)
    return resolved


def _enforce_base_dir(target: Path) -> None:
    """Raise if ``target`` is not inside ``ALLOWED_LOCAL_BASE_DIR`` (when set)."""
    base_raw = Config.ALLOWED_LOCAL_BASE_DIR.strip()
    if not base_raw:
        return
    base = Path(base_raw).expanduser().resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise ValueError(
            f"Rejected path: {target} is outside ALLOWED_LOCAL_BASE_DIR ({base})"
        ) from None


def download_s3_to_tmp(bucket: str, key: str) -> Path:
    """Download an S3 object to a fresh temp dir and return the local file path."""
    import boto3  # local import — keeps the module importable with no boto3/offline

    tmp_dir = Path(tempfile.mkdtemp(prefix="dart-"))
    local_path = tmp_dir / Path(key).name
    client = boto3.client("s3", region_name=Config.AWS_REGION)
    client.download_file(bucket, key, str(local_path))
    return local_path


def resolve_to_local_file(path: str) -> tuple[Path, bool]:
    """Resolve an input path to a local file.

    Returns ``(local_path, is_tmp)``. For ``s3://`` inputs the object is downloaded to a
    temp dir and ``is_tmp`` is ``True`` — the caller MUST remove ``local_path.parent`` in a
    ``finally`` block. For local inputs ``is_tmp`` is ``False``.
    """
    if is_s3_uri(path):
        bucket, key = validate_s3_uri(path)
        return download_s3_to_tmp(bucket, key), True
    return validate_local_path(path), False


def cleanup_tmp(local_path: Path) -> None:
    """Best-effort removal of a temp download directory created by this module."""
    try:
        parent = local_path.parent
        if parent.exists():
            for child in parent.iterdir():
                child.unlink(missing_ok=True)
            os.rmdir(parent)
    except OSError:
        pass
