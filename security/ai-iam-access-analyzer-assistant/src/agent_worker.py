"""Async worker for the slow /conversation model path.

Invoked asynchronously (InvocationType='Event') by the conversation
dispatcher (agent.handler) whenever a request falls through every
deterministic short-circuit to the two-Bedrock-round model path. That path
can exceed the API Gateway 29s integration ceiling; running it here, off the
request, gives it the Lambda's full 120s budget instead.

Flow:
  event = {"job_id": "<uuid>"}
  1. read the job row (contains the stored request payload),
  2. run the exact same model path the inline handler used (agent.run_model_path),
  3. write DONE + the result envelope, or ERROR + a readable message, back to
     the row so the status endpoint / frontend can retrieve it.

The result envelope is identical to the inline-200 shape, so the frontend
renders a polled result exactly like a synchronous one.
"""

import logging

from botocore.exceptions import ClientError

import agent
import jobs

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _bedrock_access_denied_message() -> str:
    """Reuse the same actionable AccessDenied guidance the sync handler gives,
    so an async turn surfaces the real Bedrock model-access cause rather than a
    bare ERROR."""
    import os

    model_id = os.environ.get("BEDROCK_MODEL_ID", "the configured model")
    return (
        "I couldn't reach the Bedrock model due to an access-denied error. This almost "
        "always means one of two things:\n\n"
        f"1. **Model access isn't enabled** for `{model_id}` in this account/region. "
        "Open the Bedrock console → **Model access** and enable it.\n"
        "2. **Access was just enabled and is still propagating** — Bedrock can take a "
        "couple of minutes. If you just enabled it, wait ~2 minutes and retry.\n\n"
        "Note: the underlying AWS error may mention *AWS Marketplace subscriptions* "
        "(`aws-marketplace:Subscribe`). That wording is misleading — this is Bedrock "
        "model access, not a Marketplace subscription."
    )


def handler(event, context):
    """Run one async conversation turn and record its outcome on the job row."""
    job_id = (event or {}).get("job_id")
    if not job_id:
        logger.error("Worker invoked with no job_id; nothing to do. event=%s", event)
        return {"ok": False, "error": "no job_id"}

    job = jobs.get_job(job_id)
    if job is None:
        logger.error("Worker: job %s not found (expired?) — nothing to do.", job_id)
        return {"ok": False, "error": "job not found"}

    try:
        request_payload = jobs.load_request(job)
        result = agent.run_model_path(request_payload)
        jobs.complete_job(job_id, result)
        return {"ok": True, "job_id": job_id}

    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("AccessDeniedException", "AccessDenied"):
            # Store the actionable guidance as a normal DONE result so the user
            # sees the hint in the chat, mirroring the sync handler's behavior.
            jobs.complete_job(
                job_id,
                {
                    "response": _bedrock_access_denied_message(),
                    "usage": {},
                    "tools_used": [],
                    "pagination": None,
                    "coverage": [],
                    "answered_from_prior_context": False,
                },
            )
            return {"ok": True, "job_id": job_id, "note": "access_denied_surfaced"}
        logger.error("Worker ClientError on job %s: %s", job_id, e, exc_info=True)
        jobs.fail_job(job_id, f"AWS error: {code or 'unknown'}")
        return {"ok": False, "job_id": job_id}

    except Exception as e:  # noqa: BLE001 — record any failure on the row
        logger.error("Worker failed on job %s: %s", job_id, e, exc_info=True)
        jobs.fail_job(
            job_id,
            "The request could not be completed. Please try again, or narrow it to a "
            "single step.",
        )
        return {"ok": False, "job_id": job_id}
