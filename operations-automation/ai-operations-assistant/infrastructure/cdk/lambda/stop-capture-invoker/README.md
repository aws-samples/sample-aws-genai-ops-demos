# StopCaptureInvokerLambda

Bridges EventBridge Scheduler's Auto_Stop_Schedule to the G.O.A.T.
Network Agent's `stop_capture` action. Provisioned by
`NetworkInfraStack` as part of Task 26 of the `goat-network-agent`
spec.

## Why this exists

EventBridge Scheduler does not yet have a native target template for
`bedrock-agent-runtime:InvokeAgentRuntime`. This Lambda is a thin shim:
the schedule targets the Lambda, the Lambda calls the AgentCore runtime,
and the Network Agent runs the same `stop_capture` handler that
user-initiated stops use.

## Files

| File | Purpose |
| --- | --- |
| `index.py` | Single-file handler implementing retry + metric emission |

## Runtime contract

**Event payload (from EventBridge Scheduler):**

```json
{ "capture_id": "<id>" }
```

**Behaviour:** invokes the Network Agent runtime with payload
`{"action": "stop_capture", "params": {"capture_id": "<id>"}}`. Three
attempts with exponential backoff (1 s, 2 s) on transient errors.
On exhaustion, emits a single CloudWatch `goat-network-auto-stop-failures`
metric data point and re-raises so the Lambda invocation is recorded
as a failure.

## Environment variables

| Name | Required | Default | Purpose |
| --- | --- | --- | --- |
| `NETWORK_AGENT_RUNTIME_ARN` | yes | — | ARN of the Network Agent runtime to invoke. The Lambda's IAM policy is scoped to this exact ARN. |
| `METRIC_NAMESPACE` | no | `GOAT/Network` | CloudWatch namespace for the failure metric. |
| `METRIC_NAME` | no | `goat-network-auto-stop-failures` | Metric name. |
| `MAX_INVOCATION_ATTEMPTS` | no | `3` | Total invocation attempts including the first try. |
| `BACKOFF_BASE_SECONDS` | no | `1.0` | Base seconds for exponential backoff between attempts. |

## IAM scope

The Lambda's role is granted exactly two permissions, both narrowly
scoped:

- `bedrock-agent-runtime:InvokeAgentRuntime` on the Network Agent
  runtime ARN only.
- `cloudwatch:PutMetricData` constrained by a `cloudwatch:namespace`
  condition to the configured metric namespace.

## Validates

Requirements 4.6, 4.7, 6.12 of the `goat-network-agent` spec.
