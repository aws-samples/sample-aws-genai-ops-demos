"""Provider registry loader.

At runtime (on AgentCore) the registry lives in S3 as the single source of
truth. The loader uses an S3 conditional GET (If-None-Match / ETag) so it can
poll cheaply: when the object hasn't changed, S3 returns 304 Not Modified with
no body and we keep the cached list. Editing providers.json in S3 propagates
within one poll interval — no redeploy.

Configuration (env vars, set by CDK on the runtime):
    PROVIDERS_BUCKET          S3 bucket holding the registry (required for S3 mode)
    PROVIDERS_KEY             object key (default: config/providers.json)
    PROVIDERS_POLL_INTERVAL   seconds between conditional refreshes (default: 60)

Local development: if PROVIDERS_BUCKET is not set, the loader reads the
repo-local providers.json sitting next to this file. That same file is the
seed the deploy script uploads to S3 — so it is never a runtime duplicate.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_BUCKET = os.environ.get("PROVIDERS_BUCKET")
_KEY = os.environ.get("PROVIDERS_KEY", "config/providers.json")
_POLL_INTERVAL = float(os.environ.get("PROVIDERS_POLL_INTERVAL", "60"))
_LOCAL_PATH = Path(__file__).resolve().parent / "providers.json"

_providers: Optional[list[dict]] = None
_etag: Optional[str] = None
_last_check: float = 0.0
_s3 = None


def _s3_client():
    global _s3
    if _s3 is None:
        import boto3

        _s3 = boto3.client("s3")
    return _s3


def _load_from_s3() -> None:
    """Conditional GET from S3. On 304 (unchanged) keep the cache; on 200 refresh it."""
    global _providers, _etag
    from botocore.exceptions import ClientError

    kwargs = {"Bucket": _BUCKET, "Key": _KEY}
    if _etag:
        kwargs["IfNoneMatch"] = _etag

    try:
        resp = _s3_client().get_object(**kwargs)
        body = resp["Body"].read().decode("utf-8")
        _providers = json.loads(body).get("providers", [])
        _etag = resp.get("ETag")
        logger.info("Loaded %d providers from s3://%s/%s", len(_providers), _BUCKET, _KEY)
    except ClientError as e:
        status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 304:
            logger.debug("providers.json unchanged (304), keeping cache")
        else:
            logger.error("S3 get_object failed: %s", e)
            if _providers is None:
                _providers = []
    except json.JSONDecodeError as e:
        logger.error("Failed to parse providers.json from S3: %s", e)
        if _providers is None:
            _providers = []


def _load_local() -> None:
    """Local-dev fallback: read the repo-local providers.json once."""
    global _providers
    if _providers is not None:
        return
    try:
        with open(_LOCAL_PATH, "r", encoding="utf-8") as f:
            _providers = json.load(f).get("providers", [])
        logger.info("Loaded %d providers from local file", len(_providers))
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("Local providers load failed: %s", e)
        _providers = []


def _refresh() -> None:
    """Refresh the cache from the active source, respecting the poll interval."""
    global _last_check
    if _BUCKET:
        now = time.time()
        if _providers is None or (now - _last_check) >= _POLL_INTERVAL:
            _load_from_s3()
            _last_check = now
    else:
        _load_local()


def get_providers() -> list[dict]:
    _refresh()
    return _providers or []


def get_provider(name: str) -> Optional[dict]:
    for p in get_providers():
        if p["name"] == name.lower().strip():
            return p
    return None


def get_provider_names() -> list[str]:
    return [p["name"] for p in get_providers()]
