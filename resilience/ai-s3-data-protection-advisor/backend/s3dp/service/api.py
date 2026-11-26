"""HTTP API (payload 2.0) for runs and reports. The JWT authorizer has already validated the caller; nothing here
reads claims. Routes come from event["routeKey"]. Our code logs error types only, never messages, and never reads execution data (the SDK logs step exceptions itself)."""
import json
import logging
import os
import re
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import boto3

from s3dp.service import labels, store

logger = logging.getLogger(__name__)

RUN_ID = re.compile(r"^scan-\d{8}-\d{6}$")
FORMATS = {"json": "data.json", "xlsx": "report.xlsx", "html": "report.html"}
FAILED = ("FAILED", "TIMED_OUT", "STOPPED")      # the record status is FAILED once reconciled
HEADERS = {"Content-Type": "application/json", "Cache-Control": "no-store"}
# Tests and local runs only: the deploy stages the sample as s3dp/sample_data.json, which wins when present.
_SAMPLE_FALLBACK = Path(__file__).resolve().parents[3] / "sample-data" / "anycompany-data.json"


def _store():
    return store.from_env()


def _lambda():
    return boto3.client("lambda")


def _now():
    return datetime.now(timezone.utc)


def _resp(code, body):
    return {"statusCode": code, "headers": dict(HEADERS), "body": json.dumps(body, default=str)}


def _unqualified(arn):
    """arn:...:function:name:live -> arn:...:function:name (listing spans every version)."""
    parts = (arn or "").split(":")
    return ":".join(parts[:7])


ACTIVE_STATUSES = ("RUNNING", "PAUSING", "PAUSED")


def start():
    arn = os.environ["PIPELINE_FUNCTION_ARN"]
    lam = _lambda()
    # Check-then-act: two POSTs seconds apart can both start a run. Acceptable for a single-user demo; POSTs in the
    # same second share a DurableExecutionName and are idempotent.
    # The service accepts one status per call ("Cannot filter by more than one status") and no alias qualifier.
    running = []
    for status in ACTIVE_STATUSES:
        running = lam.list_durable_executions_by_function(
            FunctionName=_unqualified(arn), Statuses=[status], MaxItems=1).get("DurableExecutions") or []
        if running:
            break
    if running:
        return _resp(409, {"error": "a scan is already running", "run_id": running[0].get("DurableExecutionName", "")})
    now = _now()
    run_id = now.strftime("scan-%Y%m%d-%H%M%S")
    out = lam.invoke(FunctionName=arn, InvocationType="Event", DurableExecutionName=run_id,
                     Payload=json.dumps({"run_id": run_id}))
    _store().update_run(run_id, execution_arn=out.get("DurableExecutionArn"), status="RUNNING",
                        started_at=now.isoformat(timespec="seconds"))
    return _resp(202, {"run_id": run_id, "status": "RUNNING"})


def _reconcile(st, lam, rec):
    """A RUNNING record whose execution ended without the pipeline writing the outcome (crash, timeout, stop)."""
    if rec.get("status") != "RUNNING" or not rec.get("execution_arn"):
        return rec
    try:
        status = lam.get_durable_execution(DurableExecutionArn=rec["execution_arn"]).get("Status")
    except Exception as e:
        logger.warning("execution lookup failed: %s", type(e).__name__)
        return rec
    if status in FAILED:
        st.update_run(rec["run_id"], status="FAILED", error=status)
        rec = dict(rec, status="FAILED", error=status)
    return rec


def list_runs():
    st, lam = _store(), _lambda()
    return _resp(200, {"runs": [_reconcile(st, lam, r) for r in st.list_runs(50)]})


def _history(lam, arn):
    """name -> "started" | "succeeded" | "failed", folded from the execution history."""
    state = {}
    for page in lam.get_paginator("get_durable_execution_history").paginate(
            DurableExecutionArn=arn, IncludeExecutionData=False):
        for e in page.get("Events", []):
            kind, name = e.get("EventType"), e.get("Name") or ""
            if kind == "StepStarted":
                if state.get(name) != "succeeded":
                    state[name] = "started"      # a retry after a failure is in progress again
            elif kind == "StepSucceeded":
                state[name] = "succeeded"
            elif kind == "StepFailed":
                state[name] = "failed"
    return state


def _fold(state, run_status):
    """The seven labels with a status each. See the contract: success needs every step done and the next label
    started (or the run succeeded); a failed unit collect is tolerated by the pipeline, so only `assemble` closes
    the collect label."""
    per = [[] for _ in labels.STEPS]
    for name, st in state.items():
        label = labels.label_for(name)
        if label:
            per[labels.STEPS.index(label)].append((name, st))
    started = [bool(p) for p in per]
    out = []
    for i, label in enumerate(labels.STEPS):
        items = per[i]
        core = [(n, s) for n, s in items if not n.startswith("collect-")]
        collected = sum(1 for n, s in items if n.startswith("collect-") and s == "succeeded")
        in_flight = any(s == "started" for _, s in items)
        core_ok = bool(core) and all(s == "succeeded" for _, s in core)    # for label 3 core is `assemble` only
        done = bool(items) and core_ok and not in_flight and (run_status == "SUCCEEDED" or any(started[i + 1:]))
        if done:
            status = "success"
        elif run_status in FAILED:
            status = "error" if items else "pending"
        else:
            status = "in-progress" if items else "pending"
        detail = f"{collected} unit{'s' if collected != 1 else ''} collected" if i == 2 and collected else ""
        out.append({"label": label, "status": status, "detail": detail})
    return out


def detail(run_id):
    st = _store()
    rec = st.get_run(run_id)
    if not rec:
        return _resp(404, {"error": "run not found"})
    lam = _lambda()
    rec = _reconcile(st, lam, rec)
    try:
        if not rec.get("execution_arn"):
            raise LookupError("no execution")
        steps = _fold(_history(lam, rec["execution_arn"]), rec.get("status"))
    except Exception as e:      # the run view never fails on a history read
        logger.warning("history read failed: %s", type(e).__name__)
        steps = [{"label": s, "status": "pending", "detail": ""} for s in labels.STEPS]
    return _resp(200, {"run": rec, "steps": steps})


def report(run_id, fmt):
    if fmt not in FORMATS:
        return _resp(400, {"error": "format must be json, xlsx or html"})
    st = _store()
    key = f"runs/{run_id}/{FORMATS[fmt]}"
    if not st.exists(key):
        return _resp(404, {"error": "no report for this run"})
    params = {"Bucket": st.bucket, "Key": key}
    if fmt != "json":
        params["ResponseContentDisposition"] = f'attachment; filename="{run_id}-report.{fmt}"'
    url = st.s3.generate_presigned_url("get_object", Params=params, ExpiresIn=300)
    return _resp(200, {"url": url, "expires_in": 300})


def sample():
    try:
        text = resources.files("s3dp").joinpath("sample_data.json").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        text = _SAMPLE_FALLBACK.read_text(encoding="utf-8")
    return {"statusCode": 200, "headers": dict(HEADERS), "body": text}


def _route(event):
    route = event.get("routeKey")
    if route == "POST /api/scans":
        return start()
    if route == "GET /api/scans":
        return list_runs()
    if route == "GET /api/sample":
        return sample()
    if route in ("GET /api/scans/{id}", "GET /api/scans/{id}/report"):
        run_id = (event.get("pathParameters") or {}).get("id", "")
        if not RUN_ID.fullmatch(run_id):
            return _resp(400, {"error": "invalid run id"})
        if route.endswith("/report"):
            return report(run_id, (event.get("queryStringParameters") or {}).get("format"))
        return detail(run_id)
    return _resp(404, {"error": "not found"})


def handler(event, context):
    try:
        return _route(event or {})
    except Exception as e:
        logger.error("request failed: %s", type(e).__name__)
        return _resp(500, {"error": "internal error"})
