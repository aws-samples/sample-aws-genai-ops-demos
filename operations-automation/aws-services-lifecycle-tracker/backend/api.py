"""
Plain Lambda serving UI events and the weekly schedule (issue #139).

Two invocation shapes:
- API Gateway HTTP API (payload v2, Cognito JWT authorizer already enforced):
    POST /actions          body {action, ...}     -> actions.dispatch
    POST /refresh          body {mode?, services?} -> start (or adopt) a durable
                                                     pipeline execution
    GET  /refresh/{arn}                           -> condensed execution status
- EventBridge Scheduler (direct invoke):
    {"action": "start_refresh", "refresh_origin": "Auto"} -> weekly pipeline run
  Any other router payload -> actions.dispatch

The browser holds no IAM permissions: everything reaches AWS through this
function, so its role carries the permissions the UI used to need.
"""
import json
import os
import time
from typing import Optional

import boto3

from actions import dispatch

PIPELINE_FUNCTION_ARN = os.environ.get("PIPELINE_FUNCTION_ARN")  # qualified (alias) ARN
_lambda = None


def _lambda_client():
    global _lambda
    if _lambda is None:
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        _lambda = boto3.client("lambda", region_name=region)
    return _lambda


# ---------------------------------------------------------------------------
# Refresh pipeline control
# ---------------------------------------------------------------------------

def _unqualified_function_arn() -> str:
    """Strip the alias qualifier: arn:...:function:name:live -> arn:...:function:name."""
    parts = PIPELINE_FUNCTION_ARN.split(":")
    return ":".join(parts[:7]) if len(parts) > 7 else PIPELINE_FUNCTION_ARN


def _running_execution() -> Optional[dict]:
    """The currently RUNNING pipeline execution, if any (across all versions).

    The list API cannot filter by alias, so query the function itself.
    """
    resp = _lambda_client().list_durable_executions_by_function(
        FunctionName=_unqualified_function_arn(), Statuses=["RUNNING"], MaxItems=1)
    executions = resp.get("DurableExecutions", [])
    return executions[0] if executions else None


def start_refresh(body: dict) -> dict:
    """Start a pipeline execution, or adopt the one already running.

    Execution names are unique per function: a duplicate name is the SAME
    execution (AWS-enforced), which is what makes the weekly schedule
    idempotent. Manual starts get a timestamped name.
    """
    if not PIPELINE_FUNCTION_ARN:
        return {"error": "PIPELINE_FUNCTION_ARN not configured"}, 500

    running = _running_execution()
    if running:
        return {"executionArn": running["DurableExecutionArn"], "alreadyRunning": True}, 200

    payload = {
        "mode": body.get("mode", "full"),
        "refresh_origin": body.get("refresh_origin", "manual"),
    }
    if body.get("services"):
        payload["services"] = body["services"]
    if body.get("regions"):
        payload["regions"] = body["regions"]

    # Scheduled runs get a date-based name (one weekly run per day at most,
    # duplicates collapse onto the same execution); manual runs get a
    # timestamp. Allowed charset is [a-zA-Z0-9-_], max 64.
    if body.get("execution_name"):
        name = body["execution_name"]
    elif payload["refresh_origin"] == "Auto":
        name = f"refresh-weekly-{time.strftime('%Y-%m-%d', time.gmtime())}"
    else:
        name = f"refresh-manual-{int(time.time())}"
    resp = _lambda_client().invoke(
        FunctionName=PIPELINE_FUNCTION_ARN,
        InvocationType="Event",
        DurableExecutionName=name,
        Payload=json.dumps(payload).encode(),
    )
    return {"executionArn": resp.get("DurableExecutionArn"), "executionName": name,
            "alreadyRunning": False}, 202


def refresh_status(execution_arn: str) -> dict:
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

    # Progress: count completed extract/scan steps from the history
    progress = {"extract_done": 0, "scan_done": 0}
    try:
        paginator = client.get_paginator("get_durable_execution_history")
        for page in paginator.paginate(DurableExecutionArn=execution_arn):
            for ev in page.get("Events", []):
                if ev.get("EventType") == "StepSucceeded":
                    name = ev.get("Name") or ""
                    if name.startswith("extract-"):
                        progress["extract_done"] += 1
                    elif name.startswith("scan-"):
                        progress["scan_done"] += 1
    except Exception:
        pass  # progress is best-effort
    out["progress"] = progress
    return out, 200


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _http(status: int, body) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def handler(event, context):
    # API Gateway HTTP API (payload format 2.0)
    if isinstance(event, dict) and "requestContext" in event and "http" in event["requestContext"]:
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
            result, status = start_refresh(body)
            return _http(status, result)
        if method == "GET" and "/refresh/" in path:
            arn = (event.get("pathParameters") or {}).get("arn") or path.rsplit("/refresh/", 1)[1]
            result, status = refresh_status(arn)
            return _http(status, result)
        return _http(404, {"error": f"No route for {method} {path}"})

    # Direct invocation (EventBridge Scheduler, CLI, tests). The weekly
    # schedule goes through here too ({"action": "start_refresh",
    # "refresh_origin": "Auto"}) so scheduled and manual runs share the
    # naming and adopt-running logic above.
    if isinstance(event, dict) and event.get("action") == "start_refresh":
        result, _ = start_refresh(event)
        return result
    return dispatch(event)
