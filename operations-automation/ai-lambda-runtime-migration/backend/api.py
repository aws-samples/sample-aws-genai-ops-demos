"""
Plain Lambda serving the UI's HTTP API (v1.1 Phase 1).

Two invocation shapes:
- API Gateway HTTP API (payload v2, Cognito JWT authorizer already enforced):
    POST /actions          body {action, ...}         -> dispatch
    POST /refresh          body {scope?, refresh_origin?} -> start (or adopt) a
                                                           durable pipeline run
    GET  /refresh/{arn+}                               -> condensed status
- Direct invocation (tests, CLI): a {action: ...} payload goes through dispatch;
  a {action: "start_refresh", ...} payload starts a refresh.

The browser holds no AWS credentials: everything reaches AWS through this
function, so its role carries the permissions the UI needs.
"""

import json
import logging
import os
import re
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

import boto3

from aws_utils import get_region  # staged copy of shared/utils/aws_utils.py
from common import sanitize_execution_name, transform_has_branch, decimals_to_native as _decimals_to_native
from inventory import table as _table  # the one DynamoDB Table accessor (reads live in inventory.py)
from pipeline import normalize_spec, SpecValidationError
import discover
import inventory
import org_accounts

logger = logging.getLogger(__name__)

PIPELINE_FUNCTION_ARN = os.environ.get("PIPELINE_FUNCTION_ARN")  # qualified (alias) ARN
MIGRATE_FUNCTION_ARN = os.environ.get("MIGRATE_FUNCTION_ARN")   # qualified (alias) ARN, Phase 2

_lambda = None


def _lambda_client():
    global _lambda
    if _lambda is None:
        _lambda = boto3.client("lambda", region_name=get_region())
    return _lambda


# ---------------------------------------------------------------------------
# Refresh pipeline control
# ---------------------------------------------------------------------------

def _unqualified(arn: str) -> str:
    """Strip the alias qualifier: arn:...:function:name:live -> arn:...:function:name."""
    parts = (arn or "").split(":")
    return ":".join(parts[:7]) if len(parts) > 7 else arn


def _unqualified_function_arn() -> str:
    """The pipeline function's unqualified ARN (listing spans every version)."""
    return _unqualified(PIPELINE_FUNCTION_ARN)


def _running_execution() -> Optional[dict]:
    """The currently RUNNING pipeline execution, if any (across all versions)."""
    resp = _lambda_client().list_durable_executions_by_function(
        FunctionName=_unqualified_function_arn(), Statuses=["RUNNING"], MaxItems=1)
    executions = resp.get("DurableExecutions", [])
    return executions[0] if executions else None


def start_refresh(body: dict) -> tuple:
    """Start a pipeline execution, or adopt the one already running.

    The scope is validated (normalize_spec) BEFORE the pipeline is invoked, so a
    bad spec never reaches a durable step; a violation returns 400 with the
    offending field named.
    """
    if not PIPELINE_FUNCTION_ARN:
        return {"error": "PIPELINE_FUNCTION_ARN not configured"}, 500

    # Validate the scope here so the 400 carries the field name.
    payload = {"scope": body.get("scope") or {},
               "refresh_origin": body.get("refresh_origin", "manual")}
    normalize_spec(payload)  # raises SpecValidationError -> handled by caller

    running = _running_execution()
    if running:
        return {"executionArn": running["DurableExecutionArn"], "alreadyRunning": True}, 200

    name = body.get("execution_name") or f"refresh-manual-{int(time.time())}"
    resp = _lambda_client().invoke(
        FunctionName=PIPELINE_FUNCTION_ARN,
        InvocationType="Event",
        DurableExecutionName=name,
        Payload=json.dumps(payload).encode(),
    )
    return {"executionArn": resp.get("DurableExecutionArn"), "executionName": name,
            "alreadyRunning": False}, 202


def refresh_status(execution_arn: str) -> tuple:
    """Condensed view of a pipeline execution for UI polling."""
    client = _lambda_client()
    desc = client.get_durable_execution(DurableExecutionArn=execution_arn)
    status = desc.get("Status")
    out = {
        "executionArn": execution_arn,
        "status": status,
        "startDate": desc.get("StartTimestamp").isoformat() if desc.get("StartTimestamp") else None,
        "stopDate": desc.get("EndTimestamp").isoformat() if desc.get("EndTimestamp") else None,
    }
    if status == "SUCCEEDED" and desc.get("Result"):
        try:
            out["summary"] = json.loads(desc["Result"])
        except (TypeError, ValueError):
            out["summary"] = None
    if status in ("FAILED", "TIMED_OUT", "STOPPED"):
        out["error"] = desc.get("Error")

    out["progress"] = _progress_from_history(client, execution_arn, status)
    return out, 200


def list_scans(body: dict) -> tuple:
    """Recent pipeline (durable) executions for the scan log timeline.

    Lists the function's durable executions newest-first (capped), and for each
    returns a bounded view: arn, status, start/finish timestamps, and the
    summarize-step result (discovered / assessed counts, accounts scanned /
    failed) when the run has produced one. Read-only; mirrors how
    refresh_status reads a single execution's state.
    """
    if not PIPELINE_FUNCTION_ARN:
        return {"error": "PIPELINE_FUNCTION_ARN not configured"}, 500

    limit = min(int(body.get("limit", 10) or 10), 10)
    client = _lambda_client()
    resp = client.list_durable_executions_by_function(
        FunctionName=_unqualified_function_arn(), MaxItems=limit)
    executions = resp.get("DurableExecutions", []) or []

    scans = []
    for ex in executions[:limit]:
        arn = ex.get("DurableExecutionArn")
        status = ex.get("Status")
        started = ex.get("StartTimestamp")
        finished = ex.get("EndTimestamp")
        scan = {
            "executionArn": arn,
            "status": status,
            "startedAt": started.isoformat() if started else None,
            "finishedAt": finished.isoformat() if finished else None,
            "summary": None,
        }
        # The bounded summary only exists once the summarize step succeeded. The
        # list item may already carry the result; otherwise read it per-arn.
        result = ex.get("Result")
        if result is None and status == "SUCCEEDED" and arn:
            try:
                result = client.get_durable_execution(DurableExecutionArn=arn).get("Result")
            except Exception:  # pragma: no cover - best effort, never fail the list
                logger.warning("could not read result of scan execution %s", arn, exc_info=True)
                result = None
        if result:
            try:
                scan["summary"] = json.loads(result)
            except (TypeError, ValueError):
                scan["summary"] = None
        scans.append(scan)

    # Newest first. The API orders by start time; be defensive if it does not.
    scans.sort(key=lambda s: s.get("startedAt") or "", reverse=True)
    return {"scans": scans}, 200


def list_migration_runs(body: dict) -> tuple:
    """Recent migrate BATCH executions, newest first (READ ONLY).

    Two consumers: the My functions Steps card re-attaches to the most recent
    batch on page load (limit 1) and the Activity log lists one entry per batch
    (limit 10). Mirrors list_scans over the migrate function. Returns
    {"runs": []} 200 (not 503) when migration is not deployed: this is a passive
    page-load fetch and must not surface an error in a scan-only deployment.
    `limit` is clamped to 1..10; a non-numeric value is a 400.
    """
    if not MIGRATE_FUNCTION_ARN:
        return {"runs": []}, 200

    raw_limit = body.get("limit")
    try:
        limit = int(raw_limit) if raw_limit not in (None, "") else 10
    except (TypeError, ValueError):
        return {"error": "limit: must be an integer"}, 400
    limit = max(1, min(limit, 10))

    client = _lambda_client()
    resp = client.list_durable_executions_by_function(
        FunctionName=_unqualified(MIGRATE_FUNCTION_ARN), MaxItems=limit)
    executions = resp.get("DurableExecutions", []) or []

    runs = []
    for ex in executions[:limit]:
        arn = ex.get("DurableExecutionArn")
        status = ex.get("Status")
        started = ex.get("StartTimestamp")
        finished = ex.get("EndTimestamp")
        run = {
            "executionArn": arn,
            "status": status,
            "startedAt": started.isoformat() if started else None,
            "finishedAt": finished.isoformat() if finished else None,
            "summary": None,
        }
        # The bounded summary (migrate.summarize_migration) only exists once the
        # execution SUCCEEDED. The list item may already carry the result;
        # otherwise read it per-arn (best effort, never fail the list).
        result = ex.get("Result")
        if result is None and status == "SUCCEEDED" and arn:
            try:
                result = client.get_durable_execution(DurableExecutionArn=arn).get("Result")
            except Exception:  # best effort, never fail the list
                logger.warning("could not read result of migrate execution %s", arn, exc_info=True)
                result = None
        if result:
            try:
                run["summary"] = json.loads(result)
            except (TypeError, ValueError):
                run["summary"] = None
        runs.append(run)

    # Newest first. The API orders by start time; be defensive if it does not.
    runs.sort(key=lambda r: r.get("startedAt") or "", reverse=True)
    return {"runs": runs}, 200


# ---------------------------------------------------------------------------
# Migration control (Phase 2)
# ---------------------------------------------------------------------------

_LAMBDA_ARN_RE = re.compile(
    r"^arn:[a-z0-9-]+:lambda:[a-z0-9-]+:\d+:function:[a-zA-Z0-9_-]+(:[a-zA-Z0-9$_-]+)?$")


def _find_fn_row(canonical: str):
    """The fn# inventory row whose canonical ARN matches, or None."""
    for row in inventory.scan_inventory("fn#"):
        if discover.canonical_arn(row.get("function_arn", "")) == canonical:
            return row
    return None


def start_migration(body: dict) -> tuple:
    """Start a migration durable execution for one function OR a batch.

    503 when MIGRATE_FUNCTION_ARN is unset (Phase 2 / Transform not deployed).

    Single-function (function_arn): 400 (field named) for a malformed ARN; 404
    {error:'unknown function'} when no fn# row; else invokes the orchestrator with
    InvocationType='Event' + a sanitized DurableExecutionName, returning
    {executionArn, migItemKey} 202.

    Batch (function_arns, a non-empty list): each ARN must match _LAMBDA_ARN_RE
    (400, field named, otherwise); unknown ARNs go into a `skipped` list; 404 only
    when EVERY ARN is unknown. Invokes the orchestrator ONCE with
    {function_arns:[canonical...]} and a batch DurableExecutionName,
    returning {executionArn, batchSize, overCap, skipped} 202. overCap is enforced
    server-side by start_batch, so the API reports 0 and the UI reads the
    authoritative count from the progress summary. The job pushes an upgrade
    branch per transformed function; nothing is merged and no pull request is
    opened. Any other body key an older client still sends is ignored.
    """
    if not MIGRATE_FUNCTION_ARN:
        return {"error": "migration not enabled"}, 503

    # --- batch path: function_arns is present ---
    if body.get("function_arns") is not None:
        return _start_migration_batch(body.get("function_arns"))

    function_arn = body.get("function_arn")
    if not isinstance(function_arn, str) or not _LAMBDA_ARN_RE.match(function_arn):
        return {"error": "function_arn: must be a Lambda function ARN"}, 400

    canonical = discover.canonical_arn(function_arn)
    fn_row = _find_fn_row(canonical)
    if fn_row is None:
        return {"error": "unknown function"}, 404

    account_id = fn_row.get("account_id", "") or ""
    region = fn_row.get("region", "") or get_region()
    mig_item_key = discover.mig_item_key(account_id, region, canonical)

    name = sanitize_execution_name(canonical)
    payload = {"function_arn": canonical}
    resp = _lambda_client().invoke(
        FunctionName=MIGRATE_FUNCTION_ARN,
        InvocationType="Event",
        DurableExecutionName=name,
        Payload=json.dumps(payload).encode(),
    )
    return {"executionArn": resp.get("DurableExecutionArn"),
            "migItemKey": mig_item_key, "executionName": name}, 202


def _start_migration_batch(function_arns) -> tuple:
    """One durable execution fanning out over a list of ARNs (see start_migration)."""
    if (not isinstance(function_arns, list)
            or not all(isinstance(a, str) for a in function_arns)
            or not [a for a in function_arns if a.strip()]):
        return {"error": "function_arns: must be a non-empty list of Lambda function ARNs"}, 400
    for a in function_arns:
        if not _LAMBDA_ARN_RE.match(a):
            return {"error": "function_arns: must be a non-empty list of Lambda function ARNs"}, 400

    # Resolve each against the inventory; unknown ARNs are reported, not dropped.
    accepted, skipped = [], []
    for a in function_arns:
        canonical = discover.canonical_arn(a)
        if _find_fn_row(canonical) is None:
            skipped.append(a)
        else:
            accepted.append(canonical)
    if not accepted:
        return {"error": "unknown function"}, 404

    name = f"migrate-batch-{int(time.time())}"
    payload = {"function_arns": accepted}
    resp = _lambda_client().invoke(
        FunctionName=MIGRATE_FUNCTION_ARN,
        InvocationType="Event",
        DurableExecutionName=name,
        Payload=json.dumps(payload).encode(),
    )
    return {"executionArn": resp.get("DurableExecutionArn"),
            "batchSize": len(accepted), "overCap": 0, "skipped": skipped}, 202


def migration_status(arn: str) -> tuple:
    """Single mig# item for a function ARN, or {migration_status:'NOT_STARTED'}.

    The write path keys mig# on the fn# row's stored account_id/region, so this
    read first recovers those from the fn# row (hub rows carry an empty
    account_id, so the ARN alone is insufficient), then composes the mig# key. A
    key mismatch or a missing fn# row degrades to NOT_STARTED, never a wrong
    status."""
    canonical = discover.canonical_arn(urllib.parse.unquote(arn or ""))
    fn_row = _find_fn_row(canonical)
    if fn_row is None:
        return {"migration_status": "NOT_STARTED"}, 200
    account_id = fn_row.get("account_id", "") or ""
    region = fn_row.get("region", "") or get_region()
    mig_key = discover.mig_item_key(account_id, region, canonical)
    resp = _table().get_item(Key={"item_key": mig_key})
    item = resp.get("Item")
    if not item:
        return {"migration_status": "NOT_STARTED"}, 200
    return _decimals_to_native(item), 200


def _decode_step_result(payload: str) -> dict:
    """The durable SDK stores step results in a typed envelope ({"t":"m","v":{...}}); flatten it."""
    def _dec(node):
        t, v = node.get("t"), node.get("v")
        if t == "m":
            return {k: _dec(val) for k, val in v.items()}
        if t == "l":
            return [_dec(x) for x in v]
        return v
    try:
        return _dec(json.loads(payload))
    except Exception:
        return {}


def _progress_from_history(client, execution_arn: str, status: str) -> dict:
    """Phases of a run, read from the execution history (best effort, never raises).

    Folds StepStarted/StepSucceeded/StepFailed into the four UI phases
    (Prepare / Discover / Assess / Summarize)."""
    steps = {}          # name -> "started" | "succeeded" | "failed"
    accounts_found = None  # count of accounts resolved by the start-run step
    try:
        paginator = client.get_paginator("get_durable_execution_history")
        for page in paginator.paginate(DurableExecutionArn=execution_arn, IncludeExecutionData=True):
            for ev in page.get("Events", []):
                et, name = ev.get("EventType"), ev.get("Name") or ""
                if et == "StepStarted":
                    steps.setdefault(name, "started")
                elif et == "StepSucceeded":
                    steps[name] = "succeeded"
                    if name == "start-run":
                        run = _decode_step_result(((ev.get("StepSucceededDetails") or {}).get("Result") or {}).get("Payload") or "")
                        accts = run.get("accounts") or []
                        # The hub-only default is a single placeholder account with an
                        # empty id; count it as one account found.
                        accounts_found = len(accts) if isinstance(accts, list) else None
                elif et == "StepFailed":
                    steps[name] = "failed"  # a later StepStarted/StepSucceeded (retry) overrides
    except Exception:  # best effort: the run view must never 500 on a history read
        logger.warning("history read failed for %s", execution_arn, exc_info=True)

    def phase(label: str, prefix: str, total, single: bool = False):
        names = [n for n in steps if (n == prefix if single else n.startswith(prefix))]
        done = sum(1 for n in names if steps[n] == "succeeded")
        failed = sum(1 for n in names if steps[n] == "failed")
        running = [n[len(prefix):] if not single else n for n in names if steps[n] == "started"]
        total = total if total is not None else (len(names) or None)
        if not names:
            st = "pending"
        elif running or (total and done + failed < total):
            st = "in-progress"
        elif failed:
            st = "error" if done == 0 else "warning"
        else:
            st = "success"
        return {"label": label, "status": st, "done": done, "failed": failed,
                "total": total, "running": running[:5]}

    # Every fan-out phase passes total=None so it derives its size from the
    # checkpointed step count (support-*, discover-*, assess-*). This is also the
    # Discover "bare Running" fix: when there is a single discover cell, total is
    # 1 derived from the step, and the UI shows "N of M done · running: ..."
    # exactly as assess does, instead of collapsing to a bare "Running".
    phases = [
        phase("Determine the accounts to scan", "start-run", 1, single=True),
        phase("Check each account's AWS Support tier", "support-", None),
        phase("Discover functions using deprecated runtimes and prioritize", "discover-", None),
        phase("Assess migration complexity and risks", "assess-", None),
        phase("Summarize findings and update the inventory", "summarize", 1, single=True),
    ]
    # The first phase polls AWS Organizations once, so "1 of 1 account done" is
    # meaningless; surface the number of accounts it resolved instead.
    if accounts_found is not None:
        phases[0]["accountsFound"] = accounts_found
    if status != "RUNNING":
        for ph in phases:
            if ph["status"] == "pending":
                ph["status"] = "stopped"
    return {"support_done": phases[1]["done"], "discover_done": phases[2]["done"],
            "assess_done": phases[3]["done"], "phases": phases}


# ---------------------------------------------------------------------------
# Migration-run progress (Phase 2 batch): folded from the execution history
# ---------------------------------------------------------------------------

def _decode_context_result(payload: str) -> dict:
    """ContextSucceeded payload of a wait_for_callback with no serdes. The SDK
    json.dumps() the raw callback string, so the root is a JSON string whose
    content is itself JSON (what build_complete sent). A dict root is a typed
    envelope (a child context that returned a dict) and goes through
    _decode_step_result. Anything else -> {} (counted as failed by the caller)."""
    try:
        root = json.loads(payload)
    except Exception:
        return {}
    if isinstance(root, str):
        try:
            inner = json.loads(root)
        except Exception:
            return {}
        return inner if isinstance(inner, dict) else {}
    if isinstance(root, dict):
        return _decode_step_result(payload)
    return {}


# Anchored on purpose: the SDK records child contexts named "transform-<i>
# submitter" (a step) and "transform-<i> create callback id" (a callback)
# under each transform-<i> wait_for_callback, and the map emits "migrate" /
# "map-item-<i>" contexts; none of them may match a phase, so a bare prefix
# match would over-count.
_PREPARE_RE = re.compile(r"^prepare-batch$")
_RESOLVE_RE = re.compile(r"^resolve-(\d+)$")
_TRANSFORM_RE = re.compile(r"^transform-(\d+)$")

_MIGRATION_PHASE_LABELS = (
    "Prepare the migration batch",
    "Resolve source repositories",
    "Transform with AWS Transform",
)

_NOTE_ENDED_BEFORE_START = "Run ended before this phase started"
_NOTE_ENDED_BEFORE_DONE = "Run ended before this phase completed"


def _step_payload(ev: dict) -> str:
    return ((ev.get("StepSucceededDetails") or {}).get("Result") or {}).get("Payload") or ""


def _context_payload(ev: dict) -> str:
    return ((ev.get("ContextSucceededDetails") or {}).get("Result") or {}).get("Payload") or ""


def _migration_progress(client, execution_arn: str, status: str) -> dict:
    """The three fixed migration phases, folded from the execution history only.
    Sibling of _progress_from_history; never raises.

    Phase 0 (prepare-batch) and 1 (resolve-<i>) read Step* events; phase 2
    (transform-<i>) is a wait_for_callback CONTEXT and reads Context* events,
    its result being the raw string build_complete sent
    (_decode_context_result). Phase 1 splits resolved vs rejected by DECODED
    result (a rejected resolve SUCCEEDS returning {ok:False}); phase 2 by the
    decoded transform status (TRANSFORMED-with-branch / NO_CHANGES done, FAILED
    or undecodable failed). The history is the single source for the run view;
    the mig# items remain the source for the per-row Migration column. The
    decoded prepare-batch result also yields total_selected / accepted for the
    card header."""
    prepare = None          # "started" | "succeeded" | "failed"
    total_selected = None   # from the decoded prepare-batch result
    accepted = None
    resolve, resolve_result = {}, {}        # index -> state, index -> decoded dict
    transform, transform_result = {}, {}    # index -> state, index -> decoded dict

    def _fold_step(states, results, idx, et, ev):
        # A retried step emits a second StepStarted; setdefault keeps "started"
        # and the later terminal event overrides.
        if et == "StepStarted":
            states.setdefault(idx, "started")
        elif et == "StepSucceeded":
            states[idx] = "succeeded"
            results[idx] = _decode_step_result(_step_payload(ev))
        elif et == "StepFailed":
            states[idx] = "failed"

    try:
        paginator = client.get_paginator("get_durable_execution_history")
        for page in paginator.paginate(DurableExecutionArn=execution_arn, IncludeExecutionData=True):
            for ev in page.get("Events", []):
                et, name = ev.get("EventType"), ev.get("Name") or ""
                if _PREPARE_RE.match(name):
                    if et == "StepStarted":
                        prepare = prepare or "started"
                    elif et == "StepSucceeded":
                        prepare = "succeeded"
                        decoded = _decode_step_result(_step_payload(ev))
                        if isinstance(decoded.get("total_selected"), int):
                            total_selected = decoded["total_selected"]
                        if isinstance(decoded.get("accepted"), list):
                            accepted = len(decoded["accepted"])
                    elif et == "StepFailed":
                        prepare = "failed"
                    continue
                m = _RESOLVE_RE.match(name)
                if m:
                    _fold_step(resolve, resolve_result, m.group(1), et, ev)
                    continue
                m = _TRANSFORM_RE.match(name)
                if m:
                    idx = m.group(1)
                    if et == "ContextStarted":
                        transform.setdefault(idx, "started")
                    elif et == "ContextSucceeded":
                        transform[idx] = "succeeded"
                        transform_result[idx] = _decode_context_result(_context_payload(ev))
                    elif et == "ContextFailed":        # callback timed out / submitter exhausted
                        transform[idx] = "failed"
    except Exception:  # best effort: the run view must never 500 on a history read
        logger.warning("migration history read failed for %s", execution_arn, exc_info=True)

    # Phase 0 - the single prepare-batch step, by status.
    p0_done = 1 if prepare == "succeeded" else 0
    p0_failed = 1 if prepare == "failed" else 0
    p0_in_progress = prepare == "started"

    def _ok(results, idx):
        # None when the decoded result carries no `ok` (undecodable payload).
        res = results.get(idx) or {}
        return bool(res["ok"]) if "ok" in res else None

    # Phase 1 - split resolved vs rejected by DECODED result, not step status.
    # A resolve-<i> step that is "failed" (crashed, not a business rejection)
    # also counts as failed.
    p1_done = sum(1 for i, st in resolve.items() if st == "succeeded" and _ok(resolve_result, i) is True)
    p1_failed = sum(1 for i, st in resolve.items()
                    if st == "failed" or (st == "succeeded" and _ok(resolve_result, i) is False))
    p1_in_progress = any(st == "started" for st in resolve.values())
    p1_total = len(resolve) or None

    # Phase 2 - the transform-<i> wait_for_callback contexts, by decoded status.
    # Mirrors migrate.parse_build_result: the status must be TRANSFORMED,
    # NO_CHANGES or FAILED and a TRANSFORMED payload must carry a branch, else
    # the unit counts as failed (the orchestrator settles it TRANSFORM_FAILED).
    # Unknown-value guard: an undecodable payload or a status this fold does
    # not know counts as failed, never raises.
    p2_done = p2_failed = no_changes = 0
    for i, st in transform.items():
        if st == "failed":
            p2_failed += 1
        elif st == "succeeded":
            res = transform_result[i]
            t_status = res.get("status")
            if t_status == "NO_CHANGES":
                p2_done += 1
                no_changes += 1
            elif t_status == "TRANSFORMED" and transform_has_branch(res):
                p2_done += 1
            else:                                      # FAILED, no branch, unknown or undecodable
                p2_failed += 1
    p2_in_progress = any(st == "started" for st in transform.values())
    # Survivors of phase 1. A REAL zero (nothing resolved, so nothing to
    # transform) must be 0, not None: None means "size unknown yet" and makes
    # the UI render a bare label, whereas 0 lets it say "Nothing to transform".
    # Only use None while phase 1 has not produced any result at all.
    p2_total = p1_done if resolve else None

    # rejected_by_code from the decoded resolve-<i> results (a rejection is a
    # SUCCEEDED resolve returning {ok: False, failure_code}).
    rejected_by_code = {}
    for i, st in resolve.items():
        res = resolve_result.get(i) or {}
        if st == "succeeded" and _ok(resolve_result, i) is False and res.get("failure_code"):
            code = res["failure_code"]
            rejected_by_code[code] = rejected_by_code.get(code, 0) + 1

    def _status(done, failed, total, in_progress) -> str:
        if in_progress:
            return "in-progress"
        # Size not known yet: the phase has not produced anything, so it is
        # pending — not a success. (A real zero is handled next.)
        if total is None:
            return "pending"
        # A REAL zero means the phase had nothing to work on (everything upstream
        # was rejected). That is "skipped", rendered with the neutral stopped
        # icon — NOT a green success, which would contradict a "Nothing to
        # start" message. Icon and message must agree.
        if total == 0:
            return "stopped"
        if done + failed < total:
            return "in-progress"
        if failed and done:
            return "warning"
        if failed and not done:
            return "error"
        return "success"

    def _running_list(states):
        # Indices (<i>) of the started units, capped at 5.
        return [i for i, st in states.items() if st == "started"][:5]

    def _phase(label, done, failed, total, in_progress, running):
        return {"label": label, "status": _status(done, failed, total, in_progress),
                "done": done, "failed": failed, "total": total, "running": running}

    p2 = _phase(_MIGRATION_PHASE_LABELS[2], p2_done, p2_failed, p2_total, p2_in_progress,
                _running_list(transform))
    if no_changes:
        p2["noChanges"] = no_changes
    # Branches ready = transforms done minus the no-change ones, computed here so
    # the UI never subtracts.
    p2["branchesReady"] = p2_done - no_changes

    phases = [
        _phase(_MIGRATION_PHASE_LABELS[0], p0_done, p0_failed, 1, p0_in_progress, []),
        _phase(_MIGRATION_PHASE_LABELS[1], p1_done, p1_failed, p1_total, p1_in_progress,
               _running_list(resolve)),
        p2,
    ]
    # The execution is over (SUCCEEDED / FAILED / STOPPED / TIMED_OUT): no event
    # will ever arrive, so no phase may stay pending or in-progress (the UI stops
    # polling as soon as none is). All three phases come from the history, so all
    # three settle. A mixed outcome gets the same answer _status gives while
    # running (done and failed -> warning, only failed -> error).
    if status != "RUNNING":
        for ph in phases:
            if ph["status"] == "pending":
                ph["status"] = "stopped"
                ph["note"] = _NOTE_ENDED_BEFORE_START
            elif ph["status"] == "in-progress":
                if ph["failed"] and ph["done"]:
                    ph["status"] = "warning"
                elif ph["failed"]:
                    ph["status"] = "error"
                elif ph["done"]:
                    ph["status"] = "warning"
                else:
                    ph["status"] = "stopped"
                ph["note"] = _NOTE_ENDED_BEFORE_DONE
    return {"phases": phases, "rejected_by_code": rejected_by_code,
            "total_selected": total_selected, "accepted": accepted}


def migration_run_status(execution_arn: str) -> tuple:
    """Condensed view of a migration BATCH execution for UI polling.

    Mirrors refresh_status's shape (executionArn/status/startDate/stopDate/summary)
    and adds progress: the three fixed phases from _migration_progress."""
    execution_arn = urllib.parse.unquote(execution_arn or "")
    client = _lambda_client()
    desc = client.get_durable_execution(DurableExecutionArn=execution_arn)
    status = desc.get("Status")
    out = {
        "executionArn": execution_arn,
        "status": status,
        "startDate": desc.get("StartTimestamp").isoformat() if desc.get("StartTimestamp") else None,
        "stopDate": desc.get("EndTimestamp").isoformat() if desc.get("EndTimestamp") else None,
    }
    if status == "SUCCEEDED" and desc.get("Result"):
        try:
            out["summary"] = json.loads(desc["Result"])
        except (TypeError, ValueError):
            out["summary"] = None
    out["progress"] = _migration_progress(client, execution_arn, status)
    return out, 200


# ---------------------------------------------------------------------------
# UI actions router
# ---------------------------------------------------------------------------

def _matches_scope(row: dict, scope: dict) -> bool:
    accounts = scope.get("accounts") or []
    if accounts:
        wanted = {a.get("id") for a in accounts if isinstance(a, dict)}
        if row.get("account_id") not in wanted:
            return False
    regions = scope.get("regions") or []
    if regions and row.get("region") not in regions:
        return False
    for flt in scope.get("tags") or []:
        key, values = flt.get("key"), flt.get("values") or []
        tags = row.get("tags") or {}
        if key not in tags:
            return False
        if values and tags[key] not in values:
            return False
    return True


def _hub_account() -> tuple:
    """(account_id, friendly_name) for the hub (the account the API runs in).

    Derived at runtime via STS (never hardcoded). discover._caller_account
    returns "" when STS is unreachable, so hub rows can be keyed on an EMPTY
    account_id (they are re-keyed on the next scan that resolves it); this
    resolves the real id for DISPLAY so the home table shows the real account
    instead of '-' in the meantime. Best effort: an unavailable STS call leaves
    rows as-is.
    """
    region = get_region()
    account_id = discover._caller_account(region)
    if not account_id:
        return "", ""
    # Prefer the real AWS Organizations account name (e.g. "payments-prod"),
    # the same source member accounts get their names from. Fall back to a
    # self-descriptive placeholder only when the org can't be read.
    org_name = org_accounts.account_name(account_id, region)
    name = f"{org_name} (hub)" if org_name else f"This account (hub) · {account_id}"
    return account_id, name


def _fill_hub_display(rows: list) -> list:
    """Resolve empty-id hub rows to the real hub id/name for DISPLAY only.

    An empty account_id is a valid key (STS unreachable at scan time, see
    _hub_account); rows that already carry the real id are left alone. Only the
    in-memory response is rewritten — stored item_key, account_id
    and scope matching (which ran above on the stored id) are untouched.
    """
    if not any(not r.get("account_id") for r in rows):
        return rows
    hub_id, hub_name = _hub_account()
    if not hub_id:
        return rows
    for r in rows:
        if not r.get("account_id"):
            r["account_id"] = hub_id
            r["account_name"] = r.get("account_name") or hub_name
    return rows


def _merge_migration_runs(rows: list) -> list:
    """Merge the mig# items onto their fn# rows under distinct migration_run_* names.

    The mig# enum (QUEUED/IN_PROGRESS/REJECTED/FAILED/NO_CHANGES/BRANCH_READY; a
    value this build does not know is passed through unchanged and the frontend
    renders the raw value) is a DIFFERENT axis from the fn# row's own migration_status (the Phase-1 assessment axis),
    so it is surfaced under migration_run_* and NEVER overwrites fn#'s own
    migration_status. A function with no mig# item defaults to NOT_STARTED. One
    extra table scan per inventory load (acceptable at demo volume)."""
    mig_by_arn = discover.index_migration_runs(inventory.scan_inventory("mig#"))
    for row in rows:
        mig = discover.migration_run_for(row, mig_by_arn)
        row["migration_run_status"] = (mig or {}).get("migration_status", "NOT_STARTED")
        row["migration_failure_code"] = (mig or {}).get("migration_failure_code")
        row["migration_failure_detail"] = (mig or {}).get("migration_failure_detail")
        row["migration_branch"] = (mig or {}).get("migration_branch")
        row["migration_repo"] = (mig or {}).get("migration_repo")
        row["migration_diff_summary"] = (mig or {}).get("migration_diff_summary")
        row["migration_build_id"] = (mig or {}).get("migration_build_id")
        # The target the run used: the family default of the managed
        # transformation (transform_runtimes.TRANSFORMS[family]["defaultTarget"]).
        row["migration_target"] = (mig or {}).get("migration_target")
        row["migration_run_updated_at"] = (mig or {}).get("migration_updated_at")
    return rows


def list_functions(body: dict) -> dict:
    """Scoped inventory read for the home table."""
    scope = body.get("scope") or {}
    rows = [r for r in inventory.scan_inventory() if _matches_scope(r, scope)]
    rows = _fill_hub_display(rows)
    rows = _merge_migration_runs(rows)
    rows.sort(key=lambda r: r.get("priority_score", 0), reverse=True)
    return {"functions": rows, "total": len(rows)}


def get_stats(_body: dict) -> dict:
    """The immutable per-scan rollup series (measurement history for the charts).

    Scans the table for the 'rollup#' rows only, orders them ascending by
    rollup_at (so the UI draws a left-to-right time series), and returns decimals
    as native numbers. Reuses the same raw scan as the functions read.
    """
    rollups = inventory.scan_inventory(prefix="rollup#")
    rollups.sort(key=lambda r: r.get("rollup_at") or "")
    return {"rollups": rollups}


# ---------------------------------------------------------------------------
# Settings (repository detection: the tag keys the migration reads)
# ---------------------------------------------------------------------------

# The settings row key, whitelist and read live in inventory.py (shared with the
# migration orchestrator); only the write stays here.

def get_settings(_body: dict) -> dict:
    """Return the stored repository-detection config, or {} when unset.

    Never 404s: an absent item is an empty object so the UI renders defaults.
    """
    return {"settings": inventory.read_settings()}


def put_settings(body: dict) -> dict:
    """Persist ONLY the whitelisted fields.

    Anything outside inventory.SETTINGS_ALLOWED (e.g. a stray `token`) is
    dropped before the write, so the table never holds a credential.
    """
    incoming = body.get("settings") or {}
    allowed = {k: str(incoming[k]) for k in inventory.SETTINGS_ALLOWED if incoming.get(k) is not None}
    item = {"item_key": inventory.SETTINGS_KEY, **allowed,
            "updated_at": datetime.now(timezone.utc).isoformat()}
    _table().put_item(Item=item)
    return {"settings": allowed, "saved": True}


_ACTIONS = {
    "list_functions": list_functions,
    "get_stats": get_stats,
    "get_settings": get_settings,
    "put_settings": put_settings,
}


def dispatch(body: dict) -> dict:
    if not isinstance(body, dict):
        return {"error": "Action payload must be an object"}
    action = body.get("action")
    fn = _ACTIONS.get(action)
    if fn is None:
        return {"error": f"Unknown action: {action}"}
    return fn(body)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _http(status: int, body) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def handler(event, context=None):
    # API Gateway HTTP API (payload format 2.0)
    if isinstance(event, dict) and "requestContext" in event and "http" in event.get("requestContext", {}):
        method = event["requestContext"]["http"]["method"]
        path = event.get("rawPath", "")
        body = {}
        if event.get("body"):
            try:
                body = json.loads(event["body"])
            except ValueError:
                return _http(400, {"error": "Body must be JSON"})

        if method == "POST" and path.endswith("/actions"):
            return _http(200, dispatch(body))
        if method == "POST" and path.endswith("/refresh"):
            try:
                result, status = start_refresh(body)
            except SpecValidationError as e:
                return _http(400, {"error": str(e)})
            return _http(status, result)
        if method == "GET" and path.endswith("/scans"):
            qs = event.get("queryStringParameters") or {}
            result, status = list_scans({"limit": qs.get("limit")})
            return _http(status, result)
        if method == "GET" and "/refresh/" in path:
            arn = (event.get("pathParameters") or {}).get("arn") or path.rsplit("/refresh/", 1)[1]
            result, status = refresh_status(arn)
            return _http(status, result)
        if method == "POST" and path.endswith("/migrate-batch"):
            try:
                result, status = start_migration(body)
            except SpecValidationError as e:
                return _http(400, {"error": str(e)})
            return _http(status, result)
        if method == "POST" and path.endswith("/migrate"):
            try:
                result, status = start_migration(body)
            except SpecValidationError as e:
                return _http(400, {"error": str(e)})
            return _http(status, result)
        # Before the "/migrate-run/" prefix test (string-prefix safety).
        if method == "GET" and path.endswith("/migrate-runs"):
            qs = event.get("queryStringParameters") or {}
            result, status = list_migration_runs({"limit": qs.get("limit")})
            return _http(status, result)
        if method == "GET" and "/migrate-run/" in path:
            arn = (event.get("pathParameters") or {}).get("arn") or path.rsplit("/migrate-run/", 1)[1]
            result, status = migration_run_status(arn)
            return _http(status, result)
        if method == "GET" and "/migrate/" in path:
            arn = (event.get("pathParameters") or {}).get("arn") or path.rsplit("/migrate/", 1)[1]
            result, status = migration_status(arn)
            return _http(status, result)
        return _http(404, {"error": f"No route for {method} {path}"})

    # Direct invocation (tests, CLI).
    if isinstance(event, dict) and event.get("action") == "start_refresh":
        try:
            result, _ = start_refresh(event)
        except SpecValidationError as e:
            return {"error": str(e)}
        return result
    if isinstance(event, dict) and event.get("action") == "list_scans":
        result, _ = list_scans(event)
        return result
    if isinstance(event, dict) and event.get("action") == "list_migration_runs":
        result, _ = list_migration_runs(event)
        return result
    return dispatch(event)
