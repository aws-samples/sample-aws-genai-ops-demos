# AI Load Test Generator Agent
*Turn an API spec into a runnable load test — and optionally launch it — using Amazon Bedrock AgentCore*

## Overview

An AI agent that reads an API description (OpenAPI/Swagger or a HAR recording) and
**generates a load-test script** (JMeter `.jmx` by default; k6 or Locust on
request). Optionally, it
connects to **[AWS Distributed Load Testing (DLT)](https://docs.aws.amazon.com/solutions/latest/distributed-load-testing-on-aws/solution-overview.html)** to register and [run the test](https://docs.aws.amazon.com/solutions/latest/distributed-load-testing-on-aws/run-test-scenario.html).
The agent runs on **Amazon Bedrock AgentCore Runtime** with a **least-privilege**
execution role. Inbound is always private — the only entry point is
`InvokeAgentRuntime` with IAM SigV4; there is no public inbound endpoint.
**Network placement is a deploy choice**: `public` (default) uses AWS-managed
egress with no VPC, or `vpc` places the runtime in a private VPC for egress
control and private-target reach (see [Networking](#networking)).

The core value — script generation — works with **no DLT**. DLT wiring is opt-in.

## At a Glance
- **Duration**: ~20 minutes (first deploy)
- **Difficulty**: Intermediate
- **Target Audience**: SREs, performance engineers, platform/DevOps teams
- **Key Technologies**: Bedrock AgentCore Runtime, Amazon Bedrock (Anthropic Claude), AWS CDK / CloudFormation, VPC, (optional) AWS Distributed Load Testing
- **Estimated Cost**: see [Cost](#cost) below

## Business Value

Writing and maintaining load-test scripts by hand is slow and drifts from the
real API surface. This agent derives the endpoint inventory from the spec,
templatizes path parameters, aggregates traffic weights from HAR, and emits a
ready-to-run script — cutting the time from "we have an API" to "we have a load
test" from hours to minutes, with private inbound (IAM SigV4) and an optional
private-VPC egress mode when you need it.

## What You'll See
1. Deploy the agent (CDK **or** CloudFormation) — `public` by default (no VPC),
   or `--network-mode vpc` for a private-VPC runtime.
2. Invoke it with an API spec (inline in the payload, or via an S3 URI).
3. The agent parses the spec → classifies endpoints → generates a JMeter script.
4. (Optional) With DLT wired, it uploads the script and registers a scenario.

## Prerequisites
- An **AWS account + credentials** with permission to create IAM and
  `bedrock-agentcore` resources — plus VPC/NAT/endpoints in `vpc` network mode
  (`aws sts get-caller-identity` should resolve).
- **AWS CLI v2** and **git**.
- A container engine — **Docker** (preferred), finch, or nerdctl — for the ARM64
  image build. **CDK path (Method A) only** — the CloudFormation path builds the
  image in-stack via CodeBuild and needs no local container engine.
- Method-specific tooling: **CDK** needs Node 18+ and Python 3.9+; **CloudFormation**
  needs nothing beyond the AWS CLI (image is built in-stack by CodeBuild).
- In `vpc` mode, first VPC use auto-creates the service-linked role
  `AWSServiceRoleForBedrockAgentCoreNetwork` (in `BedrockAgentCoreFullAccess`).
- *(Optional)* An existing **DLT** deployment if you want to register/run tests.

---

## Deploy

Two interchangeable IaC paths produce the same runtime. **CDK is the canonical
path**; CloudFormation is a fully-working alternative. Both keep DLT optional and
support connecting DLT later (re-run with the DLT flags — an idempotent update
that adds the DLT env vars and DLT-scoped IAM, then rolls the runtime version).

### Method A — AWS CDK (`infrastructure/cdk/`)

```bash
# script-only agent (no DLT) — model is OPTIONAL; defaults to a Claude Opus
# profile resolved to your deploy region (no need to know the us./eu./apac. prefix)
./deploy-all.sh

# pick a model — a bare name or a full profile id; either way it is re-resolved
# to the deploy region's inference profile
./deploy-all.sh --bedrock-model anthropic.claude-opus-4-8

# with DLT wired (DLT ARNs auto-derived from the stack outputs)
./deploy-all.sh --dlt-stack LaunchWizard-dlt-poc --dlt-region us-west-2

# private-VPC mode (egress control / private targets); default is --network-mode public
./deploy-all.sh --network-mode vpc
```

Windows: `./deploy-all.ps1 [-BedrockModel anthropic.claude-opus-4-8] [-DltStack ... -DltRegion ...] [-NetworkMode vpc]` (all optional).

CDK builds the ARM64 image with your container engine (`CDK_DOCKER`, auto-detected)
and creates the runtime. **By default (`--network-mode public`) no VPC is created** —
the runtime uses AWS-managed egress. With `--network-mode vpc` CDK also creates the
private VPC (NAT + interface/S3 endpoints) and a runtime-egress security group.
Stack: `AILoadTestGen-<region>`. Teardown: `cd infrastructure/cdk &&
. .venv/bin/activate && npx aws-cdk@latest destroy AILoadTestGen-<region>`
(activate `.venv` first — `deploy-all.sh` installs the CDK Python deps there and
`cdk destroy` re-synths `app.py`; no `CDK_DOCKER` needed — destroy does not
rebuild the image; deploy auto-detects docker/finch/nerdctl).

> **Before you deploy (CDK path):** your container engine must be **running** —
> the script checks `docker`/`finch`/`nerdctl info` and stops early with a hint if
> not (start Docker Desktop, or run `finch vm start`). `--bedrock-model` is
> **optional**: it defaults to a Claude Opus profile and is always re-resolved to
> the deploy region, so you don't need the `us.`/`eu.`/`apac.` prefix. If no
> single matching inference profile exists in that region, the deploy stops and
> lists what to pass. Both `.sh` and `.ps1` behave identically. See
> [Supported cross-Region inference profiles](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html).

### Method B — CloudFormation (`infrastructure/cloudformation/`)

```bash
# script-only agent (no DLT) — model optional (auto-resolved), no local engine
# (region auto-detected; stack name defaults to ai-load-test-gen)
infrastructure/cloudformation/deploy.sh

# with DLT wired
infrastructure/cloudformation/deploy.sh --dlt-stack LaunchWizard-dlt-poc --dlt-region us-west-2
```
Windows: `./deploy.ps1 [-DltStack ... -DltRegion ...] [-NetworkMode vpc]` (all optional; same behavior as `deploy.sh`).

The CloudFormation path builds the image **in-stack** via CodeBuild (**no local
container engine needed** — use this path if you don't have Docker/finch/nerdctl).
The Bedrock model is **optional and auto-resolved to the deploy region**, exactly
like the CDK path (override with `--bedrock-model` / `-BedrockModel`). There is no
`aws cloudformation package` step — the build-trigger Lambda is inlined. Teardown:
`infrastructure/cloudformation/teardown.sh` (or `teardown.ps1` on Windows) —
auto-detects region; defaults to the `ai-load-test-gen` stack.

CloudFormation `deploy.sh` flags (PowerShell `deploy.ps1` takes the same options as PascalCase parameters, e.g. `-BedrockModel`, `-NetworkMode`, `-DltStack`):

| Flag | Default | Meaning |
|---|---|---|
| `--dlt-stack` | (optional) | DLT stack name. Omit for a script-only agent; pass it (now or later) to wire DLT. |
| `--dlt-region` | — | Required only when `--dlt-stack` is given |
| `--bedrock-region` | = `--region` | Region whose inference profiles are listed / invoked |
| `--bedrock-model` | (optional) | PRIMARY model/profile id; omit to auto-resolve a region-appropriate default |
| `--bedrock-fallback` | (optional) | FALLBACK id; omit to auto-resolve a default |
| `--region` | (auto-detect) | Deploy region. Defaults to `AWS_DEFAULT_REGION`/`AWS_REGION`, else `aws configure get region`, else `us-east-1` |
| `--stack-name` | `ai-load-test-gen` | CloudFormation stack name |
| `--network-mode public\|vpc` | `public` | Runtime network placement. `public` creates no VPC (AWS-managed egress); `vpc` creates the private VPC (NAT + endpoints + SG). Inbound is IAM SigV4 either way. |
| `--create-vpc` | `true` | New vs existing VPC — **`vpc` mode only** |
| `--source-bucket` | `<stack>-src-<acct>-<region>` | Reuse a specific source bucket |
| `--enable-xray true\|false` | `false` | Opt in to X-Ray IAM perms for OTEL traces |
| `--force-xray-without-ts` | (off) | Allow `--enable-xray true` even when Transaction Search is disabled |
| _advanced overrides_ | — | `--dlt-api-arn` / `--dlt-bucket-arn` / `--dlt-stack-arn` / `--bedrock-profiles` skip the auto-derivation if you must supply ARNs by hand |

---

## Invoke the agent (same for both methods)

The runtime is invoked identically regardless of how it was deployed. Get the
`AgentRuntimeArn` from the stack outputs (`AILoadTestGen-<region>` for CDK, or
your `--stack-name` for CloudFormation), then send an `InvokeAgentRuntime` request.
The snippets below assume you've set `REGION` (the region you deployed to) and
`ARN` — the setup block further down shows how to derive both.

Iterating across turns (e.g. "proceed 70/30")? Reuse a single
`--runtime-session-id` so the agent keeps the prior turn's context — the per-call
`uuidgen` in the one-shot snippets below deliberately starts fresh. See
[Keep the same session id across turns](#keep-the-same-session-id-across-turns).

The payload is a single JSON body: a `prompt` plus **either** an inline spec
(`spec` / `swagger` object, or base64 `spec_b64`) **or** an `s3://` URI in the
prompt (large specs). Inline needs no staging bucket.

### Accepted inputs

Two formats, and the choice changes what the agent can work out for itself.
Format is detected from the **content**, not the filename — `spec_filename` only
sets the temp-file suffix, so a HAR still parses as a HAR if you leave it off.

| Input | Detected by | What it gives you |
|---|---|---|
| **OpenAPI / Swagger** (JSON or YAML) | `openapi` / `swagger` key | Declared paths, parameters and response schemas. **No call frequency**, so the agent will ask you for the load ratio. Remember that a spec states intent: this repo's own fixture declares `200` for all 16 operations while the service really returns 401/404/500. |
| **HAR** (browser recording) | `log.entries` | Real traffic: observed call counts (so you can **omit the ratio** and get the recorded traffic shape), real response bodies to assert on, and real parameter values. Static assets are dropped and repeated calls to one endpoint are merged with path params templated (`/api/concerts/36` → `/api/concerts/{concertId}`). |

Anything else is refused by name rather than half-parsed, with a message telling
you to export one of the two formats above.

You can also skip the file and describe the endpoints in the prompt. Give the
host, a method and path each, any request body, and the success marker to assert
on, and the agent writes the TestSpec itself — validation, build and the 1-VU
smoke run against the real target are unchanged. There is no inventory this way,
so there is no endpoint discovery and no recorded call frequency: the load ratio
is yours to state. Practical for a handful of endpoints.

⚠️ **A HAR is a recording of someone's real session.** The parser keeps
credential-bearing headers as a requirement but replaces their values with
`**masked**`, and warns that a multi-VU run needs a credential CSV rather than one
person's token. That protects the generated script — it does not sanitize the file
you upload. Check a HAR for cookies and `Authorization` headers before sending it
anywhere.

Same call, HAR instead of a spec — `spec_b64` takes any format as raw bytes:

```bash
jq -n --arg b64 "$(base64 < recording.har)" \
  '{prompt:"Build a JMeter scenario from this recording. Use the recorded traffic ratio.",
    spec_b64:$b64, spec_filename:"recording.har"}' > /tmp/req.json

aws bedrock-agentcore invoke-agent-runtime --region "$REGION" \
  --agent-runtime-arn "$ARN" --runtime-session-id "session-$(uuidgen | tr -d -)0" \
  --payload fileb:///tmp/req.json --content-type application/json --accept application/json \
  --cli-read-timeout 0 out.json
```

```bash
REGION=$(aws configure get region)   # the region you deployed to (or set AWS_REGION)
ARN=$(aws cloudformation describe-stacks --region "$REGION" \
  --stack-name "AILoadTestGen-$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text)

# inline spec in the payload (no staging bucket needed)
#
# NOTE: sample-data/swagger-unified.json is a fixture — its server URL is a
# placeholder, so it cannot be load-tested. Use it to see what the endpoint
# inventory and the measurable/setup-only/blocked/excluded table look like.
# Generating and smoke-validating a script needs a real target you own and
# authorize: pass your own spec, and state the target host in the prompt.
jq -n --slurpfile s sample-data/swagger-unified.json \
  '{prompt:"Make a JMeter scenario from this spec.", spec:$s[0], spec_filename:"swagger-unified.json"}' > /tmp/req.json

aws bedrock-agentcore invoke-agent-runtime --region "$REGION" \
  --agent-runtime-arn "$ARN" --runtime-session-id "session-$(uuidgen | tr -d -)0" \
  --payload fileb:///tmp/req.json --content-type application/json --accept application/json \
  --cli-read-timeout 0 out.json
cat out.json
```

**Alternative — S3 spec-source bucket** (for large specs, or to keep the spec
staged). Both deploy paths always provision a private `SpecInputBucket` (its name
is in the stack output `SpecInputBucketName`); upload the spec there and reference
its `s3://` URI in the prompt instead of an inline `spec` field:

```bash
BUCKET=$(aws cloudformation describe-stacks --region "$REGION" \
  --stack-name AILoadTestGen-$REGION \
  --query "Stacks[0].Outputs[?OutputKey=='SpecInputBucketName'].OutputValue" --output text)
aws s3 cp sample-data/swagger-unified.json "s3://$BUCKET/swagger.json" --region "$REGION"

aws bedrock-agentcore invoke-agent-runtime --region "$REGION" \
  --agent-runtime-arn "$ARN" --runtime-session-id "session-$(uuidgen | tr -d -)0" \
  --payload fileb:///dev/stdin --content-type application/json --accept application/json \
  --cli-read-timeout 0 out.json <<JSON
{"prompt":"Build a JMeter scenario from s3://$BUCKET/swagger.json"}
JSON
```

- Inbound is **IAM SigV4 only** — the caller needs `bedrock-agentcore:InvokeAgentRuntime`
  on the runtime ARN; unauthorized callers get `403`.
- Multi-tool invokes can exceed the CLI's default read timeout — pass
  `--cli-read-timeout 0`; the runtime completes server-side regardless.
- **Disable CLI retries on any turn that can generate load** (`AWS_MAX_ATTEMPTS=1`).
  `InvokeAgentRuntime` is not streaming, so a client-side timeout looks like a
  failure while the turn is still running server-side; each retry is a **new
  invocation**. On a turn that calls `run_scenario` that means duplicated load.
- With DLT wired, verify the [test scenario](https://docs.aws.amazon.com/solutions/latest/distributed-load-testing-on-aws/create-test-scenario.html)
  was registered (DLT DynamoDB scenarios table) and its script landed in S3
  (`public/test-scenarios/jmeter/<testId>.jmx`).

### Retrieve the generated script

The `build_*` tools write the script to a path **inside the runtime container**
(`/tmp/dlt-out/…`) that you cannot reach — there is no shell or file channel back.
So both deploy paths also provision a private `ScriptOutputBucket` (its name is in
the stack output `ScriptOutputBucketName` and reaches the runtime as the
`SCRIPT_OUTPUT_BUCKET` environment variable). Ask the agent to **save / show /
download** the script and it calls `save_generated_script`, which uploads the file
there and returns an `s3://` URI, a ready-to-run `aws s3 cp` command, and a
time-limited presigned download URL. Retrieve it with either:

```bash
OUT=$(aws cloudformation describe-stacks --region "$REGION" \
  --stack-name "AILoadTestGen-$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ScriptOutputBucketName'].OutputValue" --output text)

# use the exact key the agent reported (defaults to generated-scripts/<name>)
aws s3 cp "s3://$OUT/generated-scripts/concert-read-path.jmx" . --region "$REGION"
```

The runtime role can write only to this bucket. To land scripts elsewhere, pass a
`bucket` to the tool and grant that bucket write access to the execution role.

### Keep the same session id across turns

The agent is conversational: it keeps the script it just built, the scenario it
registered, and the approval summary it showed you. That state is keyed by
`--runtime-session-id`, so **reuse one id for the whole conversation** — a new id
starts from scratch. Ids must be at least 33 characters.

Session state lives in the runtime's per-session microVM. It survives between
invocations but is discarded when the session ends (15 min idle, or 8 h max), so
finish a test run in one sitting.

```bash
SESSION="session-$(uuidgen | tr -d -)0"

# say <prompt>  — one turn of the conversation, same session every time
say() {
  AWS_MAX_ATTEMPTS=1 aws bedrock-agentcore invoke-agent-runtime --region "$REGION" \
    --agent-runtime-arn "$ARN" --runtime-session-id "$SESSION" \
    --payload fileb:///dev/stdin --content-type application/json --accept application/json \
    --cli-read-timeout 0 /tmp/out.json > /dev/null <<JSON
{"prompt": $(jq -Rs . <<<"$1")}
JSON
  jq -r .result /tmp/out.json
}
```

### Worked example — spec to load test in five turns

This is a real session, with the target host and bucket replaced by placeholders.
It uses `sample-data/swagger-unified.json` (staged to `s3://$BUCKET/swagger.json`
as shown above), and stops at the approval gate before any load is generated.
Responses are abridged.

**To reproduce it you need your own target.** The spec supplies the endpoints and
their shapes; the host is stated separately in the prompt (turn 1 below), and
turn 1 ends in a 1-VU JMeter run against it. Against the placeholder host that
run fails at DNS resolution — by design, since a script that was never executed
is not a validated script. Substitute a service you own and authorize.

**1. Build and validate the script.** State the target, the authorization, the
scope, the split, and the load profile. Ask it to stop before DLT.

```bash
say 'Generate a JMeter load test script from this OpenAPI spec:
s3://my-spec-bucket/swagger.json

Scope: test only two endpoints — GET /api/concerts (list) and GET /api/concerts/{id} (detail).
Load ratio: equal. 50% list, 50% detail.
Total load: 10 VUs, 30s ramp-up, 5 min hold.

Target: http://my-app-alb-1234567890.us-west-2.elb.amazonaws.com
I own this service and I authorize this test. Treat the environment as prod.
Read-only: do not send any write request. Both endpoints need no authentication.

Requirements:
- the {id} must be a real, existing id — decide how and explain why
- do not rely on assertions that only check the status code
- run a 1-VU smoke validation so we know the script actually works

For this turn, stop after the script is generated and validated. Do NOT touch DLT yet.'
```

```
## Script generated and validated
Artifact: /tmp/dlt-out/concert-read-path.jmx (JMeter)

Discovered: 16 endpoints · Targeted: 2 · Out of scope: 14 (no writes generated)
GET /api/concerts             5 threads   50.0%
GET /api/concerts/{concertId} 5 threads   50.0%

Real, existing {concertId} — not hardcoded. A setup step (once per VU) calls
GET /api/concerts and extracts $.content[0].id into ${concertId}. The extractor's
default_value is the sentinel EXTRACT_FAILED_NO_CONCERT_ID, so a broken correlation
fails visibly instead of passing silently.

Assertions beyond status code: list asserts the body contains "content"; detail
asserts "title" and "id". A 200 with an error envelope would fail.

1-VU smoke validation: 4 requests, all HTTP 200, all assertions passed,
correlation variable resolved.

I stopped before any DLT step — no upload, no scenario, no load run.
```

**2. Register the scenario.** It is always registered `saveOnly` first — nothing
runs. You do not need to restate anything; it remembers turn 1.

```bash
say 'Now register that script in DLT with saveOnly and show me the approval summary.
Do not start the load in this turn. I will approve in my next message.'
```

```
## Scenario registered with saveOnly — nothing is running
Test ID: concert-read-path        Status: created
Script:  public/test-scenarios/jmeter/concert-read-path.jmx
Console: https://<dlt-console-id>.cloudfront.net

## ⚠️ Approval summary
Target host   my-app-alb-...us-west-2.elb.amazonaws.com (prod)
Total VUs     10 (task_count 1 × concurrency 10)
Profile       30s ramp-up → 5 min hold
Traffic       GET /api/concerts 50% · GET /api/concerts/{concertId} 50%
Mode          Read-only (GET only, no writes)

Estimated wall-clock: ~90s provisioning + 30s ramp + 5min hold ≈ 7 minutes.
Reply with your approval and I'll run it.
```

**3. Approve, and the load runs.** `run_scenario` is the only tool that generates
load, and it refuses to fire unless it passes the approval summary back as an audit
trail — so this turn is the point of no return. Read the summary before approving;
the guard records the decision, it cannot make it for you.

```bash
say 'Approved. I have read the summary above — prod target, 10 VUs, 30s ramp-up,
5 min hold, 50/50 read-only. Run it now. Do not block-poll; just start it and
report the run status.'
```

```
🚀 Load test started.
Test ID: concert-read-path   Status: queued   Started: 2026-01-01 12:00:00 UTC
Lifecycle: queued → provisioning (~90s) → running → complete (~7 min wall-clock).
```

**4. Check on it.** Ask for a single read rather than letting it poll in a loop —
polling inside one turn just burns model tokens waiting.

```bash
sleep 150 && say 'Status check please. Single read, no block-polling.'
```

```
Status: running — provisioning is done, load is actively being generated.
Task failures so far: 0
```

**5. Get the results.** It reports per-endpoint numbers, checks the split and the
correlation actually worked, and says what the numbers do **not** prove.

```bash
sleep 330 && say 'Status check again. If it is complete, fetch the results and
interpret them. Verify the 50/50 split and the setup seeding as you said you would.'
```

```
✅ Test complete — task_failure_count = 0. Load phase 325s.

Setup seeding ✅  TX_Seed_ConcertId: succ = 10, fail = 0 — one seed per VU; no
detail request ran against the EXTRACT_FAILED sentinel.

50/50 split ✅
TX_List_Concerts        1,086   49.7%
TX_Get_Concert_Detail   1,098   50.3%

Aggregate: avg 0.181s, p50 0.176s, p95 0.188s, p99 0.344s.
0 errors across 2,194 samples.

Caveats: these are DLT client-side metrics only — they say what the client saw,
not why. Separating server latency from network needs CloudWatch/X-Ray on the
service side.
```

Three things to know before you copy this.

**Which numbers are baked into the script, and which are not.** Turn 1 states the
load profile for convenience, but only *scope* and *ratio* actually end up in the
script — the ratio becomes JMeter thread-group weights. `concurrency`, `ramp-up`,
`hold-for` and `taskCount` are DLT execution fields, and Taurus overrides whatever
the script says. So **build the script once and re-run it at 10, 50 or 500 VUs**
without regenerating anything; only a change of ratio or scope needs a new script.
The `50/50` in the approval summary is the agent describing the script — DLT never
receives a ratio.

**Give each test run a distinct test id.** Reusing one overwrites the script in S3
and mixes two runs into a single history. If you want to compare ratios, register
them as separate ids.

**Watch the region pair.** If your DLT stack is in a different region from your
target, every latency figure includes that round trip — the agent will say so, but
it is easy to read past.

---

## Networking

Inbound is private in **both** modes — the only entry point is `InvokeAgentRuntime`
(IAM SigV4). `NetworkMode` only changes the runtime's **egress** placement:

- **`public` (default)** — no VPC is created; the runtime uses AWS-managed egress
  to reach Bedrock, ECR, the [DLT EDGE API](https://docs.aws.amazon.com/solutions/latest/distributed-load-testing-on-aws/distributed-load-testing-api.html),
  and load-test targets. No NAT/endpoint cost, and no service-managed ENIs to
  reclaim on delete. This does **not** create a public inbound endpoint.
- **`vpc`** — runtime ENIs run in **private subnets** with an egress-only security
  group (no inbound). AWS-service traffic uses **VPC endpoints** (`bedrock-runtime`,
  `ecr.api`, `ecr.dkr`, `logs`, S3 gateway, + `cloudformation` when DLT is wired);
  a **NAT gateway** (egress only) reaches the public DLT EDGE API and public
  targets. Choose this for egress control, private AWS-service traffic, or to
  reach a **private/internal test target** the `public` runtime could not.

Inbound security is identical either way (IAM SigV4); `vpc` is a net cost
increase for egress control.

## Observability

- **Runtime logs** → CloudWatch Logs at `/aws/bedrock-agentcore/runtimes/<id>-DEFAULT`
  (the image runs under `opentelemetry-instrument`).
- **X-Ray traces are opt-in.** X-Ray IAM actions cannot be resource-scoped, so they
  are granted only on request (CloudFormation: `--enable-xray true`; CDK:
  `-c enableXray=true`) and only make sense when **CloudWatch Transaction Search**
  is enabled. By default X-Ray perms are omitted (least privilege, no X-Ray cost).

## Least privilege (execution role)

- `bedrock:InvokeModel[WithResponseStream]` — the selected inference-profile ARN +
  its routed foundation-model ARNs (cross-region inference needs both).
- `s3:GetObject` — the spec-input bucket (always; swagger input).
- **DLT statements are conditional** (present only when a DLT stack is wired; a
  script-only deploy carries none): `execute-api:Invoke` (DLT API),
  `cloudformation:DescribeStacks` (DLT stack), `s3:PutObject/GetObject`
  (scenarios bucket `public/test-scenarios/*`).
- Platform baseline — ECR pull, CloudWatch Logs (runtime path), namespace-scoped
  `cloudwatch:PutMetricData`, workload-identity token; optional X-Ray (gated).
- `ecr:GetAuthorizationToken` and X-Ray use `Resource:"*"` (no resource scoping
  for those actions).

## Cost

**`public` mode (default):** no NAT/VPC-endpoint charges — cost is just per-invoke
AgentCore session compute + Bedrock inference (both scale to zero when idle), plus
negligible S3/ECR storage. This is the cheapest way to run the demo.

**`vpc` mode:** adds the **NAT gateway** (~\$32/mo + data) and **VPC interface
endpoints** (~\$7/mo each, ×~5) on top of the above. A demo of a few hours is a few
USD, dominated by NAT + endpoints.

**Optimization:** default to `public` unless you need private egress/targets; tear
down when idle; keep X-Ray off unless Transaction Search is enabled.

## Teardown

- **CDK**: `cd infrastructure/cdk && . .venv/bin/activate && npx aws-cdk@latest destroy AILoadTestGen-<region>`
  (activate `.venv` first — `deploy-all.sh` installs the CDK Python deps into
  `infrastructure/cdk/.venv`, and `cdk destroy` re-synths `app.py` with whatever
  `python3` is on `PATH`; without the venv you'll hit `ModuleNotFoundError: No
  module named 'aws_cdk'`. No `CDK_DOCKER` needed — destroy does not rebuild the
  image; deploy auto-detects the container engine: docker → finch → nerdctl).
- **CloudFormation**: `infrastructure/cloudformation/teardown.sh --stack-name <name> --region <region>`.
- Note: in **`vpc` mode**, AgentCore creates service-managed ENIs that AWS
  reclaims asynchronously (up to ~8h), so a delete can land in `DELETE_FAILED` on
  the VPC until reclaim, then a teardown re-run completes it. Hourly billing
  (NAT/endpoints/runtime) stops on the first pass regardless. `public` mode has no
  VPC/ENIs and tears down cleanly.

## Notes / known items
- The container image carries a JRE + JMeter so `validate_script` can run a real
  1-VU smoke; that smoke needs egress to the target (NAT). Registration-only flows
  can skip it.
- `/app` is read-only in the runtime; generated scripts go to `/tmp/dlt-out`
  (override with `DLT_OUT_DIR`); inline specs are staged to `/tmp`.
- Primary/fallback models chosen at deploy time set `BEDROCK_MODEL_PRIMARY/FALLBACK`;
  the IAM invoke scope is derived from the same picks, so they never drift.
- Each endpoint asserts a **set of status codes** — `success.status` is a single
  code or a list (e.g. `[200, 403]` for error-path tests). JMeter emits one Equals
  assertion with the Or bit (test_type 40); k6/Locust use set membership. A body
  check always applies, so there is no unbounded "accept any status".
- A DLT **console link exists only for the ALB/CloudFront patterns**. Under the
  **Headless** pattern `discover_dlt_config` returns `console_url: null` and
  `headless: true`; read results with `fetch_results` (metrics) plus the
  `scenarios_bucket` it returns, not a web console.

## Tests

**You do not need these to deploy or use the agent.** They are excluded from the
container image (`.dockerignore`), so nothing here runs in the runtime. They exist
for whoever edits the code.

Two directories, two different jobs — the word "test" means something different in
each place, which is worth keeping straight:

| | What it checks | Who runs it |
|---|---|---|
| `builder/validate_run.py` | the **generated script** — a 1-VU JMeter run against the real target | the deployed agent, as `validate_script` |
| `builder/tests/`, `test/` | **this repo's own code** — that a change did not break it | you, locally |

### Why they exist

JMeter accepts unknown and misspelled properties silently. A test plan can be
well-formed XML, load in the GUI, run to completion, report 0% errors — and have
measured nothing. That failure mode is invisible to `xmllint`, to a smoke run, and
to reading the file.

A real example this suite now guards: a success marker passed as a bare string
instead of a list was iterated per character, so `"content"` became seven
one-character SUBSTRING assertions ANDed together — `c` and `o` and `n` and `t`
and `e` — which almost any JSON error envelope satisfies. The plan built, ran, and
reported success. Only counting the rendered assertion strings catches it. Two
tests do exactly that now.

So these are not style checks. Several of them **execute** what the builder
produced against `builder/tests/mock_target.py`, a local server that answers HTTP
200 with error envelopes — precisely what a status-code-only assertion cannot see.

### Running them

Plain scripts, no pytest, no AWS calls, no Bedrock calls:

```bash
python3 builder/tests/test_spec_input.py        # 52 checks
python3 builder/tests/test_builder.py           # 52 checks
python3 builder/tests/test_script_builders.py   # 35 checks
python3 builder/tests/test_taurus.py            # pass/fail, no count
python3 test/test_agent_smoke.py                # 17 checks
python3 test/test_tools.py                      # 42 checks
```

198 checks total. Each script prints `N passed, M failed` and exits non-zero on
failure.

| File | Proves |
|---|---|
| `builder/tests/test_spec_input.py` | HAR / OpenAPI parsing, path templating, traffic-share merging, that an unsupported input format is refused rather than partly parsed, and that missing information becomes a warning instead of an invented value |
| `builder/tests/test_builder.py` | spec validation rejects what it must, and the built JMX is **executed** — an HTTP 200 login failure and an HTTP 200 error envelope both have to fail |
| `builder/tests/test_script_builders.py` | the k6 and Locust builders, generated scripts also executed |
| `builder/tests/test_taurus.py` | Taurus — and therefore DLT — redistributes its own concurrency in proportion to `ThreadGroup.num_threads`, so per-endpoint load ratios survive the DLT hand-off |
| `test/test_agent_smoke.py` | `agent.py` wiring: prompt loading, model resolution, clean imports |
| `test/test_tools.py` | the `@tool` contract the model depends on, and the DLT safety gates (approval required, `saveOnly` on registration) without calling AWS |

### Prerequisites

| Missing tool | Effect |
|---|---|
| `jmeter` | `test_builder.py` **fails** — its end-to-end section runs JMeter for real |
| `k6`, `locust` | the matching execution sections in `test_script_builders.py` **skip**, with a printed notice — never silently |
| `bzt` (Taurus) | `test_taurus.py` skips, with a printed notice |

`builder/tests/test_builder.py` and `test_script_builders.py` bind a mock target on
`127.0.0.1:18111`. A stranded process on that port from an interrupted run causes
confusing intermittent failures — check it before debugging anything else.

The fixture the parser tests read is `sample-data/swagger-unified.json`. It is a
made-up API whose server URL is a placeholder; it is test input, not something you
can load-test.

## Available Extensions
- Wire DLT later without redeploying from scratch (idempotent update).
- Extend script generation to k6 / Locust (builders included).
- Move smoke validation off-runtime (run it on a DLT Fargate task, which already
  has JMeter) so the agent could ship via AgentCore **direct code deployment** — a
  Python zip on S3 instead of a container image — and the Dockerfile, ECR repo and
  in-stack CodeBuild all go away. JMeter is a Java program, so the JRE it needs is
  the only reason the runtime is a container today. (Unrelated to the `.zip`
  bundles DLT accepts for load scripts; this agent uploads a single `.jmx`/`.js`/
  `.py` file, not a bundle.)

## Architecture
See [ARCHITECTURE.md](ARCHITECTURE.md).

> **Note on the footer links below.** `../../CONTRIBUTING.md` and `../../LICENSE`
> resolve once this demo is placed under `resilience/ai-load-test-generation-with-dlt/`
> in the `aws-samples/sample-aws-genai-ops-demos` monorepo (two levels up = repo
> root), where the shared CONTRIBUTING/LICENSE live. In this standalone
> repository they are intentional placeholders and will be wired up at OSS-upload
> time.

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
