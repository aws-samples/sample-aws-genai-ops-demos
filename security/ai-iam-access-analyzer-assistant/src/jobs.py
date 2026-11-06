"""Async job store for the hybrid /conversation flow.

The conversation Lambda runs all the deterministic short-circuits inline and
returns 200 as before. Only when a request falls through to the model path —
the one that makes two Bedrock rounds and can exceed the API Gateway 29s
integration ceiling (HTTP 504) — does it become asynchronous:

  1. the dispatcher writes a RUNNING job row to DynamoDB and asynchronously
     invokes the worker Lambda (InvocationType='Event'), then returns 202
     with a job_id immediately (well under 29s);
  2. the worker runs the model path with the Lambda's full 120s budget and
     writes DONE (+ the rendered result) or ERROR back to the row;
  3. the frontend polls GET /conversation/status/{job_id} until the row is
     terminal, then renders the result exactly like an inline 200.

This decouples the turn from the 29s ceiling for ANY prompt and any account
size, which the keyword short-circuits could only ever do one phrasing at a
time. DynamoDB is used purely as a short-lived job-state store with a TTL, so
old rows self-expire and there is nothing to operate.
"""

import json
import logging
import os
import time
import uuid

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()

JOBS_TABLE = os.environ.get("JOBS_TABLE")
WORKER_FUNCTION_NAME = os.environ.get("WORKER_FUNCTION_NAME")

# Rows self-delete this many seconds after creation. A conversation turn
# finishes in well under a minute; 1 hour is a generous ceiling that keeps a
# completed result retrievable across a brief client hiccup without letting
# the table accumulate.
JOB_TTL_SECONDS = 3600

STATUS_RUNNING = "RUNNING"
STATUS_DONE = "DONE"
STATUS_ERROR = "ERROR"

_dynamodb = boto3.resource("dynamodb")
_lambda = boto3.client("lambda")


def _table():
    if not JOBS_TABLE:
        raise RuntimeError("JOBS_TABLE env var is not set")
    return _dynamodb.Table(JOBS_TABLE)


def create_job(request_payload: dict) -> str:
    """Write a RUNNING job row and return its id.

    request_payload is the already-parsed POST body (message, history, mode,
    org_context, ...) — stored so the worker runs the exact same request the
    dispatcher received, with no second parse.
    """
    job_id = str(uuid.uuid4())
    now = int(time.time())
    _table().put_item(
        Item={
            "job_id": job_id,
            "status": STATUS_RUNNING,
            "request": json.dumps(request_payload),
            "created_at": now,
            "expires_at": now + JOB_TTL_SECONDS,
        }
    )
    return job_id


def dispatch_worker(job_id: str) -> None:
    """Asynchronously invoke the worker Lambda for this job.

    InvocationType='Event' returns immediately (fire-and-forget) so the
    dispatcher can respond 202 without waiting for the model work. Lambda's
    async invoke path has its own built-in retries on delivery failure.
    """
    if not WORKER_FUNCTION_NAME:
        raise RuntimeError("WORKER_FUNCTION_NAME env var is not set")
    _lambda.invoke(
        FunctionName=WORKER_FUNCTION_NAME,
        InvocationType="Event",
        Payload=json.dumps({"job_id": job_id}),
    )


def get_job(job_id: str) -> dict | None:
    """Return the job row, or None if it doesn't exist (unknown/expired id)."""
    resp = _table().get_item(Key={"job_id": job_id})
    return resp.get("Item")


def load_request(job: dict) -> dict:
    """Parse the stored request payload back into a dict for the worker."""
    raw = job.get("request")
    return json.loads(raw) if isinstance(raw, str) else (raw or {})


def complete_job(job_id: str, result: dict) -> None:
    """Mark a job DONE and store the rendered result envelope.

    result is the same dict the inline 200 path returns (response,
    usage, tools_used, pagination, coverage, answered_from_prior_context),
    so the status endpoint and the frontend can treat it identically.
    """
    _table().update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET #s = :s, #r = :r",
        ExpressionAttributeNames={"#s": "status", "#r": "result"},
        ExpressionAttributeValues={":s": STATUS_DONE, ":r": json.dumps(result)},
    )


def fail_job(job_id: str, error_message: str) -> None:
    """Mark a job ERROR with an operator/user-readable message."""
    try:
        _table().update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #s = :s, #e = :e",
            ExpressionAttributeNames={"#s": "status", "#e": "error"},
            ExpressionAttributeValues={":s": STATUS_ERROR, ":e": str(error_message)},
        )
    except ClientError as e:
        # A failure to record the failure must not crash the worker; log it.
        logger.error("Could not mark job %s as ERROR: %s", job_id, e)


def status_response(job: dict) -> dict:
    """Build the JSON body the status endpoint returns to the frontend.

    - RUNNING → {status: "RUNNING"}
    - DONE    → {status: "DONE", result: {<the inline-200 envelope>}}
    - ERROR   → {status: "ERROR", error: "<message>"}
    """
    status = job.get("status")
    if status == STATUS_DONE:
        raw = job.get("result")
        result = json.loads(raw) if isinstance(raw, str) else (raw or {})
        return {"status": STATUS_DONE, "result": result}
    if status == STATUS_ERROR:
        return {"status": STATUS_ERROR, "error": job.get("error") or "Unknown error"}
    return {"status": STATUS_RUNNING}
