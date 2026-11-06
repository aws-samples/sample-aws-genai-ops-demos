"""Pure helpers shared by more than one Lambda module. Imports only stdlib (plus
the durable SDK's BatchItemStatus enum), so any module can import it without
paying another module's import-time cost or creating a cycle.
"""

import re
import time
from decimal import Decimal
from typing import Any, List

from aws_durable_execution_sdk_python.concurrency.models import BatchItemStatus
from botocore.exceptions import ClientError


def error_code(exc: Exception) -> str:
    """The AWS error code of a failed call, or the exception class name.

    Log lines carry this instead of the exception itself: a botocore error
    message can echo request parameters, and the session behind the call may
    hold assumed-role credentials, so a raw exception never reaches a log.
    """
    if isinstance(exc, ClientError):
        return (exc.response.get("Error") or {}).get("Code") or exc.__class__.__name__
    return exc.__class__.__name__

# Caps on free-text fields stored on the mig# item or carried in the build
# callback payload, so one verbose diff or stack trace cannot bloat a row.
DIFF_SUMMARY_MAX_CHARS = 1000
FAILURE_DETAIL_MAX_CHARS = 500


def convert_floats(obj: Any) -> Any:
    """DynamoDB needs Decimal, not float."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: convert_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [convert_floats(item) for item in obj]
    return obj


def decimals_to_native(obj: Any) -> Any:
    """The inverse of convert_floats for rows read back: a whole-number Decimal
    becomes int, any other Decimal float, recursively through dicts and lists."""
    if isinstance(obj, Decimal):
        return int(obj) if obj == int(obj) else float(obj)
    if isinstance(obj, dict):
        return {k: decimals_to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decimals_to_native(v) for v in obj]
    return obj


def extract_region(arn: str, default: str) -> str:
    """Region segment of an ARN, or `default` when the ARN has none."""
    parts = arn.split(":")
    return parts[3] if len(parts) >= 4 and parts[3] else default


def extract_function_name(arn: str) -> str:
    """Function name segment of a Lambda ARN, or the input when not an ARN."""
    parts = arn.split(":")
    return parts[6] if len(parts) >= 7 else arn


def results_by_index(batch, count: int) -> List:
    """BatchResult.all as a list aligned to the input index; None where the item
    failed (or never started because a completion policy fired)."""
    aligned = [None] * count
    for item in batch.all:
        if item.status is BatchItemStatus.SUCCEEDED and item.result is not None:
            aligned[item.index] = item.result
    return aligned


def transform_has_branch(result: dict) -> bool:
    """True when a transform result produced a pushed branch: status TRANSFORMED
    with a non-empty branch. Pure. The orchestrator (parse_build_result) and the
    API (migration progress) must agree on this predicate, so it lives here."""
    return (result or {}).get("status") == "TRANSFORMED" and bool((result or {}).get("branch"))


def sanitize_execution_name(function_arn: str) -> str:
    """Build a DurableExecutionName from the function ARN.

    A DurableExecutionName cannot carry the ':' / '/' an ARN has, so it is built
    from the function name (last ARN segment), lowercased, every non-alphanumeric
    char replaced by '-', capped to ~40 chars, then suffixed with the epoch:
    'migrate-<sanitized-fn-name>-<epoch>'. Pure except for the clock.
    """
    fn_name = extract_function_name(function_arn or "")
    slug = re.sub(r"[^a-z0-9]+", "-", fn_name.lower()).strip("-")
    slug = slug[:40].strip("-") or "fn"
    return f"migrate-{slug}-{int(time.time())}"
