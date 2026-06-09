"""
StopCaptureInvokerLambda — bridges EventBridge Scheduler's Auto_Stop_Schedule
to the G.O.A.T. Network Agent's ``stop_capture`` action (Task 26, Reqs 3.5,
4.6, 4.7, 6.12).

Why this Lambda exists
----------------------
EventBridge Scheduler cannot directly invoke a Bedrock AgentCore runtime
today: ``bedrock-agent-runtime:InvokeAgentRuntime`` is not in the set of
native Scheduler target templates. The Network_Infra_Stack therefore
provisions this small Python Lambda as a thin shim. The Auto_Stop_Schedule
created at ``start_capture`` time targets this Lambda; this Lambda then
calls the AgentCore runtime so the Network Agent can run the same
``stop_capture`` handler that user-initiated stops use, ensuring exactly
one cleanup code path regardless of trigger source.

Workflow position::

    EventBridge Scheduler ──(at deadline)──► [StopCaptureInvokerLambda]
                                                    │
                                                    ▼
                                            Bedrock AgentCore runtime
                                            (Network Agent stop_capture)

Input contract
--------------
The Auto_Stop_Schedule payload defined by the agent at
``scheduler:CreateSchedule`` time is::

    { "capture_id": "<id>" }

EventBridge Scheduler delivers the payload as the Lambda ``event``
argument unchanged.

Output contract
---------------
On success::

    {
        "capture_id":          "<id>",
        "runtime_arn":         "<network agent runtime ARN>",
        "invocation_attempt":  <int 1..3>,
        "agent_success":       <bool>     # the inner success flag from the
                                          # Network Agent's response envelope
    }

Failure contract
----------------
Three invocation attempts are made with exponential backoff (Req 4.7).
After exhaustion, the Lambda:

1. Emits a single CloudWatch metric data point named
   ``goat-network-auto-stop-failures`` (count = 1) under namespace
   ``GOAT/Network`` so operators can alarm on auto-stop failure trends
   without parsing logs.
2. Logs a structured ERROR record naming the ``capture_id``, the
   runtime ARN, and the underlying boto3 exception class and message.
3. Re-raises the last exception so the Lambda invocation is recorded as
   a failure in CloudWatch Logs/Metrics for ad-hoc triage.

Per design.md "Auto_Stop_Schedule failure" handling, the
Capture_State_Table row is intentionally NOT touched here — the row
stays in ``active`` status and the agent's reconciler is responsible for
detecting rows where ``deadline < now AND status = active`` and running
``stop_capture`` out-of-band.

Environment variables
---------------------
``NETWORK_AGENT_RUNTIME_ARN``
    Required. The full Bedrock AgentCore runtime ARN of the Network
    Agent. Resolves to the value the CDK app wires in via the
    OrchRuntimeStack-style follow-up reference; the IAM policy on this
    Lambda's role is scoped to this exact ARN only.
``METRIC_NAMESPACE``
    Optional. CloudWatch namespace used for the
    ``goat-network-auto-stop-failures`` metric. Defaults to
    ``GOAT/Network``.
``METRIC_NAME``
    Optional. CloudWatch metric name. Defaults to
    ``goat-network-auto-stop-failures``.
``MAX_INVOCATION_ATTEMPTS``
    Optional. Total invocation attempts including the first try.
    Defaults to ``3`` (Req 4.7: "retry up to 3 times").
``BACKOFF_BASE_SECONDS``
    Optional. Base seconds for exponential backoff between attempts.
    Defaults to ``1.0``. The wait before attempt N (1-indexed) is
    ``BACKOFF_BASE_SECONDS * 2 ** (N - 2)`` for N >= 2; attempt 1 has
    no preceding wait.

Notes
-----
* AgentCore's invoke API requires an ``X-Amzn-Bedrock-AgentCore-Runtime-Session-Id``
  header (minimum 33 characters). The Lambda generates a fresh session
  id of the form ``goat-network-stop-<uuid4-hex>`` per attempt rather
  than reusing the EventBridge-provided execution id so that retries
  within this Lambda do not appear as a single multi-turn session on
  the runtime.
* The response payload from ``InvokeAgentRuntime`` is a streaming
  body. We read it fully and JSON-parse to extract the ``success``
  boolean from the Network Agent's response envelope (see design.md
  "Response envelope"). A ``success=false`` from the agent is logged
  but not retried — it indicates the agent rejected the action (e.g.
  ``capture_id`` not found because user already stopped it), which is
  not a transient failure.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Dict

import boto3
from botocore.exceptions import ClientError, BotoCoreError

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)


_AGENTCORE = None
_CLOUDWATCH = None


def _get_agentcore_client():
    """Lazy-init the bedrock-agent-runtime client (one per Lambda
    container). Boto3 picks up the deploy-region from the Lambda's
    AWS_REGION environment variable so no explicit region argument is
    needed."""
    global _AGENTCORE
    if _AGENTCORE is None:
        _AGENTCORE = boto3.client("bedrock-agentcore")
    return _AGENTCORE


def _get_cloudwatch_client():
    """Lazy-init the CloudWatch client used for the
    ``goat-network-auto-stop-failures`` metric."""
    global _CLOUDWATCH
    if _CLOUDWATCH is None:
        _CLOUDWATCH = boto3.client("cloudwatch")
    return _CLOUDWATCH


def _emit_failure_metric(
    namespace: str,
    metric_name: str,
    capture_id: str,
    runtime_arn: str,
) -> None:
    """Emit a single CloudWatch ``Count`` data point under ``namespace``
    with metric name ``metric_name`` so operators can alarm on
    auto-stop failure trends.

    Failures inside the metric emission itself are logged but never
    re-raised — the caller is already in the failure path and a
    metric-emission failure should not mask the original exception.
    """
    try:
        cw = _get_cloudwatch_client()
        cw.put_metric_data(
            Namespace=namespace,
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Dimensions": [
                        # Keep cardinality low: dimension on the runtime
                        # ARN (one value per deployment) rather than the
                        # capture_id (unbounded). The capture_id is
                        # surfaced in the structured log line below for
                        # ad-hoc triage; CloudWatch metric explorer
                        # surfaces the trend.
                        {"Name": "RuntimeArn", "Value": runtime_arn},
                    ],
                    "Value": 1.0,
                    "Unit": "Count",
                },
            ],
        )
    except (ClientError, BotoCoreError) as metric_exc:
        LOGGER.error(
            "StopCaptureInvoker: failed to emit failure metric "
            "namespace=%s name=%s capture_id=%s runtime_arn=%s exc=%s",
            namespace,
            metric_name,
            capture_id,
            runtime_arn,
            metric_exc,
        )


def _invoke_agent(
    runtime_arn: str,
    capture_id: str,
) -> Dict[str, Any]:
    """Call ``bedrock-agent-runtime:InvokeAgentRuntime`` with the
    structured payload the Network Agent expects. Returns the parsed
    response envelope; raises on transport/AWS errors so the caller's
    retry loop can act on transient failures.
    """
    payload = {
        "action": "stop_capture",
        "params": {"capture_id": capture_id},
    }

    # Generate a fresh session id per attempt so retries within this
    # Lambda are not interpreted as a multi-turn conversation by the
    # runtime. The Network Agent is stateless so a new session has no
    # functional cost.
    #
    # Per the InvokeAgentRuntime API contract, ``runtimeSessionId`` has a
    # minimum length of 33 characters. ``uuid.uuid4().hex`` produces 32
    # hex chars; we prefix a fixed marker (``goat-network-stop-``) plus
    # the hex to comfortably exceed the minimum and to make the session
    # id self-describing in CloudWatch Logs.
    session_id = f"goat-network-stop-{uuid.uuid4().hex}"

    agentcore = _get_agentcore_client()
    response = agentcore.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=session_id,
        payload=json.dumps(payload).encode("utf-8"),
    )

    # InvokeAgentRuntime returns a streaming-body response. Read fully
    # and JSON-parse to extract the agent's response envelope.
    body = response.get("response")
    if body is None:
        raise RuntimeError(
            "StopCaptureInvoker: InvokeAgentRuntime returned no "
            "'response' body for capture_id=" + capture_id
        )

    # The response stream is a botocore StreamingBody; ``.read()``
    # buffers the whole payload. The agent's response is small (a few
    # hundred bytes) so this is safe.
    raw = body.read()
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as decode_exc:
        raise RuntimeError(
            "StopCaptureInvoker: failed to decode runtime response for "
            f"capture_id={capture_id}: {decode_exc}; "
            f"raw={raw!r}"
        ) from decode_exc

    if not isinstance(envelope, dict):
        raise RuntimeError(
            "StopCaptureInvoker: runtime response was not a JSON object "
            f"for capture_id={capture_id}: {envelope!r}"
        )

    return envelope


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """Entry point invoked by EventBridge Scheduler at the
    Capture_Session deadline.

    Args:
        event: ``{"capture_id": "<id>"}`` per the Auto_Stop_Schedule
            payload defined by the agent at ``scheduler:CreateSchedule``
            time.
        _context: Lambda runtime context (unused).

    Returns:
        Dict with the invocation outcome on success.

    Raises:
        Exception: Re-raises the last underlying exception after the
            retry budget is exhausted, so the Lambda invocation is
            recorded as a failure for ad-hoc triage.
    """
    capture_id = event.get("capture_id")
    if not isinstance(capture_id, str) or not capture_id:
        raise ValueError(
            "StopCaptureInvoker: 'capture_id' is required and must be a "
            "non-empty string"
        )

    runtime_arn = os.environ.get("NETWORK_AGENT_RUNTIME_ARN")
    if not runtime_arn:
        raise RuntimeError(
            "StopCaptureInvoker: NETWORK_AGENT_RUNTIME_ARN environment "
            "variable is unset"
        )

    metric_namespace = os.environ.get("METRIC_NAMESPACE", "GOAT/Network")
    metric_name = os.environ.get(
        "METRIC_NAME", "goat-network-auto-stop-failures"
    )
    max_attempts = int(os.environ.get("MAX_INVOCATION_ATTEMPTS", "3"))
    backoff_base_s = float(os.environ.get("BACKOFF_BASE_SECONDS", "1.0"))

    if max_attempts < 1:
        raise ValueError(
            "StopCaptureInvoker: MAX_INVOCATION_ATTEMPTS must be >= 1"
        )

    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            # Exponential backoff: 1s before attempt 2, 2s before
            # attempt 3, 4s before attempt 4, ...
            wait_s = backoff_base_s * (2 ** (attempt - 2))
            LOGGER.info(
                "StopCaptureInvoker: capture_id=%s attempt=%d sleeping=%.2fs "
                "before retry after exc=%s",
                capture_id,
                attempt,
                wait_s,
                last_exc,
            )
            time.sleep(wait_s)

        try:
            envelope = _invoke_agent(runtime_arn, capture_id)
        except (ClientError, BotoCoreError, RuntimeError) as exc:
            last_exc = exc
            LOGGER.warning(
                "StopCaptureInvoker: invocation failed "
                "capture_id=%s attempt=%d/%d runtime_arn=%s exc=%s",
                capture_id,
                attempt,
                max_attempts,
                runtime_arn,
                exc,
            )
            continue

        agent_success = bool(envelope.get("success", False))
        # An ``success=false`` envelope means the agent ran but
        # rejected the action (e.g. capture_id not found, or already
        # stopped). That is not a transient failure: retrying would
        # produce the same rejection. Log loudly and return — the row
        # is presumably already in a terminal state, otherwise the
        # reconciler will catch it.
        if not agent_success:
            LOGGER.warning(
                "StopCaptureInvoker: agent returned success=false for "
                "capture_id=%s runtime_arn=%s envelope=%s",
                capture_id,
                runtime_arn,
                envelope,
            )

        LOGGER.info(
            "StopCaptureInvoker: invocation completed "
            "capture_id=%s attempt=%d agent_success=%s",
            capture_id,
            attempt,
            agent_success,
        )

        return {
            "capture_id": capture_id,
            "runtime_arn": runtime_arn,
            "invocation_attempt": attempt,
            "agent_success": agent_success,
        }

    # Retry budget exhausted. Emit failure metric, log structured
    # error, and re-raise the last exception.
    _emit_failure_metric(
        namespace=metric_namespace,
        metric_name=metric_name,
        capture_id=capture_id,
        runtime_arn=runtime_arn,
    )
    LOGGER.error(
        "StopCaptureInvoker: exhausted %d invocation attempts for "
        "capture_id=%s runtime_arn=%s last_exc=%s",
        max_attempts,
        capture_id,
        runtime_arn,
        last_exc,
    )
    # ``last_exc`` is necessarily set when we exit the loop without
    # returning — the loop only continues via the ``except`` branch.
    assert last_exc is not None  # for type checkers
    raise last_exc
