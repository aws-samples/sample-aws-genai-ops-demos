"""Best-effort DynamoDB audit trail.

``write_audit_record`` persists a single audit item for a run. It is best-effort: any
failure is logged as structured JSON and NEVER raised to the caller, so a missing table or
denied permission can never fail an otherwise-successful audit run. The item TTL is a
hardcoded 365 days (not an env var), matching the S3 ``reports/`` lifecycle expiry.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from config import Config

logger = logging.getLogger("dart.audit")

_TTL_SECONDS = 365 * 24 * 3600


def write_audit_record(
    run_id: str,
    status: str,
    recommendation: str | None = None,
    quality_score: int | None = None,
    dataset_path: str | None = None,
    target_model: str | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    """Write one audit item to DynamoDB. Returns True on success, False otherwise.

    Never raises: all errors (unset table, ClientError, missing boto3) are swallowed and
    logged as structured JSON.
    """
    if not Config.AUDIT_TABLE:
        logger.warning(
            json.dumps({"event": "audit_skipped", "run_id": run_id, "reason": "no_table"})
        )
        return False

    now = int(time.time())
    item: dict[str, Any] = {
        "run_id": {"S": run_id},
        "timestamp": {"S": str(now)},
        "status": {"S": status},
        "ttl": {"N": str(now + _TTL_SECONDS)},
    }
    if recommendation is not None:
        item["recommendation"] = {"S": recommendation}
    if quality_score is not None:
        item["quality_score"] = {"N": str(int(quality_score))}
    if dataset_path is not None:
        item["dataset_path"] = {"S": dataset_path}
    if target_model is not None:
        item["target_model"] = {"S": target_model}
    if extra:
        for key, value in extra.items():
            item[key] = {"S": json.dumps(value) if not isinstance(value, str) else value}

    try:
        import boto3

        client = boto3.client("dynamodb", region_name=Config.AWS_REGION)
        client.put_item(TableName=Config.AUDIT_TABLE, Item=item)
        logger.info(json.dumps({"event": "audit_written", "run_id": run_id, "status": status}))
        return True
    except Exception as err:  # noqa: BLE001 - best-effort, never raises to caller
        logger.error(
            json.dumps({"event": "audit_write_error", "run_id": run_id, "error": str(err)})
        )
        return False
