"""
CodeBuild completion handler (Phase 2) — a NORMAL (non-durable) Lambda.

An EventBridge rule on the Transform CodeBuild project's terminal state invokes
this handler. It locates the mig# item for the build through the buildidx# row
the transform submitter wrote, reads the build's own result.json from S3 (the
authoritative outcome the buildspec wrote), writes the terminal migration
status to the mig# item with a ConditionExpression guard, then completes the
orchestrator's transform-<i> durable callback with the result so the batch
execution resumes (migrate.py folds it). The pushed upgrade branch is the end
of the flow: TRANSFORMED maps to BRANCH_READY, nothing opens a pull request.

CodeBuild emits several state-change events per build and EventBridge can
deliver more than once, so the guard (QUEUED/IN_PROGRESS only, owned by the
batch that started the build, swallow ConditionalCheckFailedException) is
load-bearing, not decorative. The callback is always completed with Success
and the status in the payload: a FAILED transform is a business result the
orchestrator folds, not a callback failure. The Failure variant of the callback
API is never called (and not granted).
"""

import json
import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from aws_utils import get_region  # staged copy of shared/utils/aws_utils.py
from common import DIFF_SUMMARY_MAX_CHARS

logger = logging.getLogger(__name__)

TABLE_NAME = os.environ.get("TABLE_NAME", "lambda-runtime-migration")
BUCKET_NAME = os.environ.get("BUCKET_NAME", "")

NO_CHANGES_DETAIL = "AWS Transform reported no changes for this runtime upgrade."

# Callback-send errors that mean the execution is gone or already settled: the
# item is already correct, so the handler returns normally (callback_sent False).
_CALLBACK_GONE_CODES = ("ResourceNotFoundException", "CallbackTimeoutException",
                        "InvalidParameterValueException")
# Transient: re-raise so the async invoke retries (the item write is idempotent).
_CALLBACK_TRANSIENT_CODES = ("TooManyRequestsException", "ServiceException")


def _table():
    return boto3.resource("dynamodb", region_name=get_region()).Table(TABLE_NAME)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lookup_index(build_id: str) -> dict:
    """The buildidx#<build_id> row (mig_item_key, job_id, callback_id, batch_id),
    or {} when unknown. The index row is the only link from a build to its mig#
    item, job id and callback id."""
    if not build_id:
        return {}
    resp = _table().get_item(Key={"item_key": f"buildidx#{build_id}"})
    return resp.get("Item") or {}


def _read_result(job_id: str):
    """Read s3://<bucket>/migrations/<job_id>/result.json, or None when absent."""
    if not job_id or not BUCKET_NAME:
        return None
    try:
        obj = boto3.client("s3", region_name=get_region()).get_object(
            Bucket=BUCKET_NAME, Key=f"migrations/{job_id}/result.json")
        return json.loads(obj["Body"].read())
    except Exception as exc:  # noqa: BLE001 - absent/unreadable result => derive from build state
        logger.info("no result.json for job %s: %s", job_id, exc)
        return None


# The failure codes the buildspec may report in result.json. Anything else is
# coerced to TRANSFORM_FAILED so the mig# item never carries a code the UI does
# not know.
_RESULT_FAILURE_CODES = ("TRANSFORM_FAILED", "PUSH_FAILED")


def _terminal_from_result(result: dict) -> dict:
    """Fields to SET for a present result.json.

    | result status             | write                                                     |
    | TRANSFORMED + branch      | BRANCH_READY + migration_branch + migration_diff_summary  |
    | TRANSFORMED, empty branch | FAILED / TRANSFORM_FAILED, "transformed without a branch" |
    |                           | (same predicate as migrate.parse_build_result, so the     |
    |                           | mig# item and the batch summary agree)                    |
    | NO_CHANGES                | NO_CHANGES + fixed detail (no failure code)               |
    | FAILED/other              | FAILED / <failure_code in _RESULT_FAILURE_CODES, else     |
    |                           | TRANSFORM_FAILED>, detail = diff summary                  |
    """
    status = str(result.get("status", "")).upper()
    branch = result.get("branch") or ""
    diff_summary = result.get("diff_summary") or ""
    fields = {"migration_updated_at": _now()}
    if diff_summary:
        fields["migration_diff_summary"] = diff_summary
    if status == "TRANSFORMED" and branch:
        fields["migration_status"] = "BRANCH_READY"
        fields["migration_branch"] = branch
    elif status == "TRANSFORMED":
        fields["migration_status"] = "FAILED"
        fields["migration_failure_code"] = "TRANSFORM_FAILED"
        fields["migration_failure_detail"] = "transformed without a branch"
    elif status == "NO_CHANGES":
        fields["migration_status"] = "NO_CHANGES"
        fields["migration_failure_detail"] = NO_CHANGES_DETAIL
    else:
        code = result.get("failure_code")
        if code not in _RESULT_FAILURE_CODES:
            logger.info("result failure_code %r not recognised; coercing to TRANSFORM_FAILED", code)
            code = "TRANSFORM_FAILED"
        fields["migration_status"] = "FAILED"
        fields["migration_failure_code"] = code
        if diff_summary:
            fields["migration_failure_detail"] = diff_summary
    return fields


def _terminal_from_build_state(build_status: str) -> dict:
    """Fields to SET when result.json is absent (the build process died)."""
    if build_status == "TIMED_OUT":
        return {
            "migration_status": "FAILED",
            "migration_failure_code": "TIMED_OUT",
            "migration_failure_detail": "The migration build timed out.",
            "migration_updated_at": _now(),
        }
    return {
        "migration_status": "FAILED",
        "migration_failure_code": "TRANSFORM_FAILED",
        "migration_failure_detail": "The build ended without producing a result.",
        "migration_updated_at": _now(),
    }


def _write_terminal(mig_key: str, fields: dict, batch_id: str, build_id: str) -> bool:
    """Guarded field-scoped update_item. True on write, False when swallowed.

    Guard: the item exists, is live (QUEUED/IN_PROGRESS) and is still owned by
    the batch that started this build (migration_batch_id = :batch_id), so a
    build from an earlier batch can never write over an item a later batch
    re-queued after the staleness escape. A buildidx# row without a batch id
    falls back to the status-only guard with a WARNING."""
    set_parts, attr_names, attr_values = [], {}, {}
    for idx, (k, v) in enumerate(fields.items()):
        pn, pv = f"#a{idx}", f":v{idx}"
        set_parts.append(f"{pn} = {pv}")
        attr_names[pn] = k
        attr_values[pv] = v
    attr_values[":queued"] = "QUEUED"
    attr_values[":inprogress"] = "IN_PROGRESS"
    condition = "attribute_exists(item_key) AND migration_status IN (:queued, :inprogress)"
    if batch_id:
        condition += " AND migration_batch_id = :batch_id"
        attr_values[":batch_id"] = batch_id
    else:
        logger.warning("buildidx# %s has no batch id; writing without ownership check", build_id)
    try:
        _table().update_item(
            Key={"item_key": mig_key},
            UpdateExpression="SET " + ", ".join(set_parts),
            ExpressionAttributeNames=attr_names,
            ExpressionAttributeValues=attr_values,
            ConditionExpression=condition,
        )
        return True
    except ClientError as exc:
        if (exc.response.get("Error") or {}).get("Code") != "ConditionalCheckFailedException":
            raise
        # Duplicate/late event, or an older batch's build finishing after the
        # item was re-queued: re-read once to say which.
        item = _table().get_item(Key={"item_key": mig_key}).get("Item") or {}
        owner = item.get("migration_batch_id")
        if batch_id and owner and owner != batch_id:
            logger.info("build %s belongs to batch %s, item now owned by %s", build_id, batch_id, owner)
        else:
            logger.info("build %s: mig item %s already terminal (%s); not writing",
                        build_id, mig_key, item.get("migration_status"))
        return False


def _choose_callback_id(item: dict, index: dict) -> str:
    """The callback id to complete, chosen by ownership. When the mig# item is
    still owned by this build's batch (or the legacy index row has no batch id)
    the mig# copy is authoritative with the buildidx# copy as fallback. When the
    item was re-queued by a newer batch, ONLY the buildidx# copy may be used: the
    mig# copy belongs to the new batch's wait and completing it with this
    build's result would hand a stale outcome to the wrong execution."""
    index_cb = index.get("callback_id") or ""
    index_batch = index.get("batch_id") or ""
    if not index_batch or item.get("migration_batch_id") == index_batch:
        return item.get("migration_callback_id") or index_cb
    return index_cb


def _complete_callback(callback_id: str, build_id: str, payload: dict) -> bool:
    """SendDurableExecutionCallbackSuccess with the result payload. Exhaustive
    over ClientError codes (see the module docstring); True when sent."""
    try:
        boto3.client("lambda", region_name=get_region()).send_durable_execution_callback_success(
            CallbackId=callback_id, Result=json.dumps(payload).encode("utf-8"))
        return True
    except ClientError as exc:
        code = (exc.response.get("Error") or {}).get("Code", "") or ""
        if code in _CALLBACK_GONE_CODES:
            logger.warning("callback %s for build %s is gone (%s); item already written",
                           callback_id, build_id, code)
            return False
        if code in _CALLBACK_TRANSIENT_CODES:
            logger.error("callback send throttled for build %s callback %s (%s); retrying",
                         build_id, callback_id, code)
            raise
        if code == "AccessDeniedException":
            logger.error("callback send denied for build %s callback %s: fix the MigrationCallback "
                         "IAM statement", build_id, callback_id)
            raise
        logger.error("callback send failed for build %s callback %s: %s", build_id, callback_id, code)
        raise
    except BotoCoreError as exc:
        logger.error("callback send failed for build %s callback %s: %s", build_id, callback_id, exc)
        raise


def normalize_build_id(event_build_id: str) -> str:
    """The CodeBuild build id in the form StartBuild returns and the submitter
    keys the buildidx# row with: '<project>:<uuid>'.

    EventBridge's "CodeBuild Build State Change" detail.build-id is the full
    ARN 'arn:<partition>:codebuild:<region>:<account>:build/<project>:<uuid>'.
    The index is keyed on '<project>:<uuid>', so derive that id before the
    lookup by stripping everything up to and including 'build/'.
    """
    build_id = (event_build_id or "").strip()
    marker = ":build/"
    if build_id.startswith("arn:") and marker in build_id:
        return build_id.split(marker, 1)[1]
    return build_id


def handler(event, context=None):
    detail = (event or {}).get("detail") or {}
    build_id = normalize_build_id(detail.get("build-id", ""))
    build_status = str(detail.get("build-status", "")).upper()

    index = _lookup_index(build_id)
    mig_key = index.get("mig_item_key")
    if not mig_key:
        # Nothing to update (unknown build); best-effort no-op.
        logger.warning("no buildidx# row for build %s; nothing to update", build_id)
        return {"updated": False, "reason": "no mig item resolved"}
    job_id = index.get("job_id")
    batch_id = index.get("batch_id") or ""

    item = _table().get_item(Key={"item_key": mig_key}).get("Item") or {}
    callback_id = _choose_callback_id(item, index)

    result = _read_result(job_id)
    if result is not None:
        fields = _terminal_from_result(result)
        status = str(result.get("status", "")).upper()
        if status not in ("TRANSFORMED", "NO_CHANGES"):
            status = "FAILED"
        branch = result.get("branch") or ""
        diff_summary = result.get("diff_summary") or ""
    else:
        fields = _terminal_from_build_state(build_status)
        status, branch, diff_summary = "FAILED", "", ""
    # Read the code from the fields so the (possibly coerced) value written to
    # the item and the one sent in the callback payload always agree.
    failure_code = fields.get("migration_failure_code")

    updated = _write_terminal(mig_key, fields, batch_id, build_id)
    migration_status = fields["migration_status"]      # every branch sets it

    callback_sent = False
    if callback_id:
        payload = {"status": status, "branch": branch, "failure_code": failure_code,
                   "diff_summary": (diff_summary or "")[:DIFF_SUMMARY_MAX_CHARS], "build_status": build_status}
        callback_sent = _complete_callback(callback_id, build_id, payload)
    else:
        logger.warning("no callback id for build %s; updating the item only", build_id)

    logger.info("build_complete done build=%s status=%s callback_sent=%s",
                build_id, migration_status, callback_sent)
    return {"updated": updated, "mig_key": mig_key,
            "migration_status": migration_status, "callback_sent": callback_sent}
