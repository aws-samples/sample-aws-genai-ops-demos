# Architecture

## Overview

The AI Load Test Generator Agent is a Strands agent (`BedrockAgentCoreApp`) hosted on
**Amazon Bedrock AgentCore Runtime**. Inbound is always private (IAM SigV4);
network placement is a deploy choice — `public` (default, AWS-managed egress, no
VPC) or `vpc` (private VPC). It converts an API spec into a load-test script and,
optionally, registers/runs it via **AWS Distributed Load Testing (DLT)**.

## Component diagram

The diagram below shows **`vpc` network mode**. In the default **`public` mode**
there is no VPC/NAT/endpoint box — the runtime uses AWS-managed egress — while the
IAM SigV4 inbound path, the agent tools, Bedrock, and the optional DLT stack are
identical.

```
                          IAM SigV4 (InvokeAgentRuntime)
   caller ───────────────────────────────────────────────┐
                                                          ▼
   ┌──────────────────────── VPC (private) ───────────────────────────┐
   │                                                                   │
   │   ┌───────────────────────────┐        Interface endpoints:      │
   │   │  AgentCore Runtime         │  ┌──► bedrock-runtime            │
   │   │  (ENIs in private subnets) │  ├──► ecr.api / ecr.dkr          │
   │   │  container: agent image    │  ├──► logs                       │
   │   │   - parse_spec_input       │  └──► cloudformation (if DLT)    │
   │   │   - build_jmx/k6/locust    │        S3 gateway endpoint       │
   │   │   - discover/create (DLT)  │                                  │
   │   └───────────┬───────────────┘                                  │
   │               │ egress                                           │
   │            NAT gateway ───────────► public DLT EDGE API,          │
   │                                     public load-test targets      │
   └───────────────────────────────────────────────────────────────────┘
                    │                         │
             Bedrock (models)          DLT stack (optional):
                                       API Gateway + scenarios S3 bucket
```

## Request flow

1. **Invoke** — caller sends `InvokeAgentRuntime` (IAM SigV4) with a JSON payload:
   `prompt` + optional inline `spec`/`swagger`/`spec_b64` (or an `s3://` URI).
2. **Stage** — the entrypoint writes an inline spec to `/tmp` (the `/app` image
   layer is read-only) and points the agent at the local path.
3. **Parse** — `parse_spec_input` normalizes the spec into an endpoint inventory
   (path templating, HAR traffic weights, secret masking, warnings for unknowns).
4. **Generate** — `build_jmx` (or k6/locust) emits the script under `/tmp/dlt-out`.
5. **(Optional) DLT** — `discover_dlt_config` resolves the DLT API/bucket from the
   DLT stack outputs (DescribeStacks); `upload_script` puts the script to the
   scenarios bucket; `create_scenario` registers it (saveOnly or run).

## Deployment

A single CDK path (`infrastructure/cdk/`) produces the runtime:

- In `vpc` mode: `ec2.Vpc` (private subnets + one NAT + interface/S3 endpoints) +
  a runtime-egress SG; in `public` mode none of these are created.
- Always: a least-privilege execution role; the ARM64 image built **in the cloud**
  by CodeBuild → ECR (agent source uploaded as a CDK asset, a custom resource
  starts the build and blocks until it succeeds — no local Docker); and the
  `AWS::BedrockAgentCore::Runtime` (`NetworkMode` = the chosen mode). Solution
  adoption tracking is on the app-file stack description.

## Networking & security

- **No public inbound.** Access is IAM SigV4 (`InvokeAgentRuntime`) in every mode;
  there is no public inbound endpoint. `NetworkMode` only sets **egress** placement.
- **Network mode (deploy choice, default `public`)**:
  - `public` — no VPC; AWS-managed egress. No NAT/endpoint cost and no
    service-managed ENIs to reclaim on delete.
  - `vpc` — runtime ENIs in private subnets (egress-only SG, no inbound); a single
    NAT gateway reaches the public DLT EDGE API and public targets, and AWS-service
    traffic uses VPC endpoints. Use it for egress control, private AWS-service
    traffic, or reaching private/internal targets. Inbound security is identical
    either way; `vpc` is a net cost increase.
- **Least privilege** execution role:
  - always: `bedrock:InvokeModel*` (selected inference-profile + routed
    foundation-model ARNs), spec-bucket `s3:GetObject`, ECR pull, runtime logs,
    namespace-scoped `cloudwatch:PutMetricData`, workload-identity token.
  - **DLT (only when connected)**: `execute-api:Invoke`, `cloudformation:DescribeStacks`,
    scenarios-bucket `s3:PutObject/GetObject` on `public/test-scenarios/*`.
  - **X-Ray (opt-in only)**: `xray:*` export actions (needs CloudWatch
    Transaction Search to be useful).
- **Buckets** are private (Block Public Access), encrypted, TLS-only.

## Model selection

The primary/fallback inference-profile ids are chosen at deploy time and injected
as `BEDROCK_MODEL_PRIMARY`/`BEDROCK_MODEL_FALLBACK`; the IAM invoke scope is
derived from the same picks (profile ARN + each routed foundation-model ARN, as
cross-region inference requires), so runtime config and IAM never drift.

## DLT: optional, connect-later

Deploy the agent with no DLT (script-only). To connect DLT later, re-run the
deploy with `--dlt-stack/--dlt-region`: it is an idempotent update that adds the
DLT env vars and the DLT-scoped IAM statements, and rolls the runtime version.
