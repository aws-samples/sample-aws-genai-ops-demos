# AI-Powered Security Posture with Prowler + DevOps Agent
*Continuous security scanning of your AWS account with [Prowler](https://github.com/prowler-cloud/prowler), AI-generated remediation playbooks via Amazon Bedrock (Nova Pro), and automated incident response through Amazon DevOps Agent — all surfaced in a React dashboard.*

## Overview

Most AWS security posture tooling stops at "here's a list of 5,000 findings, good luck." This demo closes the loop:

1. **Scan** — a scheduled (and on-demand) [Prowler](https://github.com/prowler-cloud/prowler) ECS Fargate task runs against your AWS account, emitting OCSF JSON and ASFF findings to S3 and Security Hub.
2. **Ingest** — an S3 event fires a Lambda that upserts every finding into DynamoDB and triages by severity.
3. **Contextualize** — for every CRITICAL/HIGH failing finding, a second Lambda calls **Amazon Nova Pro** via the Bedrock Converse API and produces a markdown remediation playbook (Impact / Root cause / Remediation steps with CLI + CDK snippets).
4. **Dispatch** — the same finding is published to an SNS topic. A HMAC-SHA256-signing Lambda forwards it to your [Amazon DevOps Agent](https://aws.amazon.com/devops-agent/) webhook with the Nova playbook embedded, so the agent starts its investigation with a remediation proposal in hand.
5. **Explore** — a React/Cloudscape dashboard (CloudFront + S3 + Cognito) lets you browse findings, read the AI playbook, and trigger scans on-demand.

## At a Glance

- **Duration**: ~8 min deployment (CDK + one CodeBuild to build the Prowler image) + ~3-10 min for the first scan
- **Difficulty**: Intermediate
- **Target Audience**: Security Engineers, Cloud SecOps, DevOps Engineers, SREs
- **Key Technologies**: Prowler, Amazon DevOps Agent, Amazon Bedrock (Nova Pro), Amazon ECS Fargate, AWS Lambda, Amazon Cognito, CloudFront, AWS CDK (TypeScript)
- **Estimated Cost**: ~$1/day idle, ~$0.50 per on-demand scan + Nova usage per CRITICAL/HIGH finding (see [Cost](#cost))

## Architecture

![Architecture Diagram](docs/architecture.drawio.svg)

```
                           ┌─────────────────────────┐
  EventBridge schedule ───►│                         │     raw-reports/{scan}/*.ocsf.json
  (on-demand from UI) ────►│   ECS Fargate Prowler   │────────────────► S3 (raw-reports)
                           │     (SecurityAudit)     │                         │
                           └─────────────────────────┘                         ▼
                                                                   S3 ObjectCreated
                                                                         │
                                                                         ▼
                                                          ┌─────────────────────────┐
                                                          │  ingest-findings Lambda │
                                                          │  OCSF → DynamoDB        │
                                                          └──────────┬──────────────┘
                                                                     │
                      ┌──────────────────────────────────────────────┤
                      │ severity ∈ {CRITICAL, HIGH}                   │ all findings
                      ▼                                               ▼
  ┌──────────────────────────────────┐                  DynamoDB (prowler-security-findings)
  │  remediation-context Lambda      │                             │
  │  Bedrock Converse (Nova Pro)     │                             │
  │  → markdown playbook to S3       │                             │
  └─────────────┬────────────────────┘                             │
                │                                                   │
                │              SNS ──► devops-agent-trigger Lambda  │
                │                             (HMAC-SHA256)         │
                │                             │                     │
                │                             ▼                     ▼
                │                   Amazon DevOps Agent   React Dashboard
                │                   webhook (with Nova    (CloudFront + Cognito +
                └───────────────────►  remediation MD)    SigV4 to dashboard-api)
```

## Prerequisites

- **AWS CLI ≥ 2.34.21** with credentials + default region configured
- **Node.js 20+** and **npm**
- **Python 3.12+** (for Lambda packaging inspection; not required at runtime)
- **zip** utility
- **CDK bootstrap** for the target account/region. If you haven't bootstrapped yet:
  ```bash
  npx cdk bootstrap aws://<account-id>/<region>
  ```
- **Bedrock model access** — enable the `amazon.nova-pro-v1:0` model in your region via the Bedrock console (Model access > Manage model access).
- **Amazon DevOps Agent** is available in `us-east-1`, `us-west-2`, and `eu-west-1`. If your infrastructure region is different, set `DEVOPS_AGENT_REGION` before deploying.

## Quick Start

```bash
git clone https://github.com/aws-samples/sample-aws-genai-ops-demos.git
cd sample-aws-genai-ops-demos/security/prowler-security-findings-agent

# Optional overrides
export AWS_REGION=eu-west-1
export AWS_DEFAULT_REGION=eu-west-1
export DEVOPS_AGENT_REGION=eu-west-1
export BEDROCK_MODEL_ID=amazon.nova-pro-v1:0   # default

# Deploy
bash deploy-all.sh          # macOS / Linux
./deploy-all.ps1            # Windows / PowerShell
```

The script walks through:

1. Prerequisites check (region, CDK, AWS CLI version, zip)
2. **Interactive DevOps Agent setup** — creates the Agent Space + IAM roles, prompts you to generate a webhook URL and secret in the DevOps Agent console, then captures them
3. Deploys 6 CDK stacks (everything except frontend)
4. Builds the Prowler container image via CodeBuild and pushes to ECR
5. Rebuilds the React dashboard with the CDK outputs baked in
6. Deploys the frontend stack (CloudFront + S3 + OAC)
7. Creates a default Cognito user so the dashboard is immediately usable

At the end you get the CloudFront dashboard URL and the demo login credentials printed to the console.

### Demo login

The deploy script provisions a default Cognito user for the dashboard:

- **Username**: `demo@prowler-security.local`
- **Password**: `ProwlerDemo2026!`

The email address is a synthetic non-routable value (Cognito sends no verification email because `--message-action SUPPRESS` is used). Override by exporting `DEMO_USERNAME` / `DEMO_PASSWORD` before running `deploy-all.sh`, or `$env:DEMO_USERNAME` / `$env:DEMO_PASSWORD` before `deploy-all.ps1`. The password must satisfy the Cognito policy (minimum 8 characters, at least one uppercase, one lowercase, and one digit).

To create additional users after deployment, use:

```bash
aws cognito-idp admin-create-user \
  --user-pool-id <USER_POOL_ID> \
  --username you@example.com \
  --user-attributes Name=email,Value=you@example.com \
  --message-action SUPPRESS --no-cli-pager

aws cognito-idp admin-set-user-password \
  --user-pool-id <USER_POOL_ID> \
  --username you@example.com \
  --password 'YourPass123!' \
  --permanent --no-cli-pager
```

### Trigger your first scan

1. Open the dashboard URL and sign in.
2. On **Dashboard**, click **Run scan now**. The Fargate task starts; first-time pulls of the Prowler image take ~90s.
3. Findings arrive in 3-10 minutes depending on account size.
4. Open **Findings** → click any CRITICAL/HIGH item → **AI remediation (Nova)** tab to see the Nova-generated playbook.
5. Open the DevOps Agent console for the Agent Space — there will be a new incident per CRITICAL/HIGH finding, with the Nova playbook embedded in the description.

## CDK Stacks

All stack IDs include the region suffix for multi-region deployments.

| Stack | Purpose | Key Resources |
|---|---|---|
| `ProwlerSecurityData-{region}` | State | DynamoDB `prowler-security-findings` (PK `finding_uid`, GSIs `severity-index` + `status-index`), S3 raw-reports + remediations buckets |
| `ProwlerSecurityAuth-{region}` | Auth | Cognito User Pool + Identity Pool + authenticated IAM role |
| `ProwlerSecurityDevOpsAgent-{region}` | Agent webhook | SNS topic, HMAC-SHA256 Lambda, Secrets Manager secret |
| `ProwlerSecurityScanner-{region}` | Prowler | ECR repo, CodeBuild image build, ECS cluster + Fargate Task Definition (SecurityAudit + ViewOnlyAccess), EventBridge schedule |
| `ProwlerSecurityIngest-{region}` | Pipeline | `ingest-findings` Lambda (S3-triggered), `remediation-context` Lambda (Bedrock Converse / Nova Pro) |
| `ProwlerSecurityApi-{region}` | Dashboard API | `dashboard-api` Lambda with IAM-auth Function URL (SigV4 from browser) |
| `ProwlerSecurityFrontend-{region}` | Dashboard | CloudFront + S3 website + OAC |

## Project Structure

```
prowler-security-findings-agent/
├── README.md
├── deploy-all.sh / deploy-all.ps1
├── cdk/
│   ├── bin/app.ts
│   ├── cdk.json, package.json, tsconfig.json
│   ├── lib/
│   │   ├── data-stack.ts
│   │   ├── auth-stack.ts
│   │   ├── devops-agent-stack.ts       ← mirrors observability/eks-investigation-devops-agent
│   │   ├── scanner-stack.ts
│   │   ├── ingest-stack.ts
│   │   ├── api-stack.ts
│   │   └── frontend-stack.ts
│   └── lambda/
│       ├── ingest-findings/index.py
│       ├── remediation-context/index.py
│       ├── devops-agent-trigger/index.py
│       └── dashboard-api/index.py
├── scanner/
│   ├── Dockerfile                      ← FROM toniblyx/prowler
│   └── entrypoint.sh                   ← prowler aws --output-formats json-ocsf -S
├── scripts/
│   ├── setup-devops-agent.sh / .ps1
│   ├── build-scanner-image.sh / .ps1
│   ├── build-frontend.sh / .ps1
│   └── cleanup.sh / .ps1
├── frontend/
│   ├── index.html, package.json, vite.config.ts, tsconfig.json
│   └── src/
│       ├── main.tsx, App.tsx, auth.ts, api.ts, AuthModal.tsx, constants.ts
│       └── pages/
│           ├── Dashboard.tsx
│           ├── Findings.tsx
│           ├── FindingDetail.tsx
│           └── Compliance.tsx
└── docs/
    ├── ARCHITECTURE.md
    ├── architecture.drawio
    └── architecture.drawio.svg
```

## How it maps to other demos in this repo

This demo was built to slot cleanly next to the existing patterns:

- **DevOps Agent webhook** (`cdk/lib/devops-agent-stack.ts` + `cdk/lambda/devops-agent-trigger/index.py`) is an adaptation of [`observability/eks-investigation-devops-agent`](../../observability/eks-investigation-devops-agent/), replacing CloudWatch-alarm payloads with Prowler OCSF findings.
- **React/Cloudscape dashboard** follows [`operations-automation/ai-lambda-runtime-migration`](../../operations-automation/ai-lambda-runtime-migration/): Cognito User Pool → Identity Pool → SigV4 → IAM-authorized Lambda Function URL (instead of AgentCore Runtime).
- **Shared tooling**: `shared/scripts/check-prerequisites.sh` and `shared/utils/aws-utils.sh` are used verbatim for region detection and prereq validation.

## Cost

All costs approximate, `us-east-1` pricing.

### Idle (~$1/day)

| Resource | Note | $/month |
|---|---|---|
| DynamoDB | On-demand | ~$0.25 |
| S3 | <1 GB total | <$0.10 |
| CloudFront + Cognito | Minimal traffic | ~$1 |
| ECR image | 1 GB image stored | ~$0.10 |
| Secrets Manager | 1 secret | $0.40 |

### Per scan (~$0.50 + Nova usage)

- Fargate task: 1 vCPU, 2 GB RAM, ~5 min runtime → ~$0.02
- **Nova Pro remediation** (Converse API, 1 call per CRITICAL/HIGH): ~$0.002–0.01 per finding depending on OCSF size
- CodeBuild: only runs on image rebuilds

### DevOps Agent usage

Per the [DevOps Agent pricing](https://aws.amazon.com/devops-agent/pricing/) — charged per investigation hour. The webhook simply enqueues incidents; cost depends on how much the agent actually investigates.

## Cleanup

```bash
bash scripts/cleanup.sh        # macOS / Linux
./scripts/cleanup.ps1          # Windows
```

Destroys all 7 stacks in reverse dependency order and (after confirmation) deletes the DevOps Agent Space and its IAM roles.

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `amazon.nova-pro-v1:0` is not available | Model access not enabled | Bedrock console > Model access > Manage → enable Nova Pro. Re-deploy. |
| CodeBuild fails to pull `toniblyx/prowler` | Docker Hub rate limits | Re-run `bash scripts/build-scanner-image.sh`; or switch to an ECR Public mirror in `scanner/Dockerfile`. |
| Fargate task fails with `AccessDenied` on S3 | Bucket region mismatch | Confirm `RawReportsBucket` is in the same region as the scanner task; rerun `deploy-all.sh`. |
| DevOps Agent never receives incidents | Webhook URL/secret still placeholders | Run `bash scripts/setup-devops-agent.sh` — it live-updates the deployed Lambda and Secrets Manager. |
| Dashboard returns 403 calling the API | Authenticated role missing `lambda:InvokeFunctionUrl` | Redeploy `ProwlerSecurityApi-{region}` and `ProwlerSecurityAuth-{region}`. |
| `SSM parameter /cdk-bootstrap/... not found` | CDK not bootstrapped | `npx cdk bootstrap aws://<account>/<region>` once. |

## Design decisions

### Why direct Converse (Nova Pro) instead of AgentCore Runtimes?

The [`ai-lambda-runtime-migration`](../../operations-automation/ai-lambda-runtime-migration/) demo is the canonical example of multi-step AgentCore Runtimes. Here, the "agent" loop is already provided by Amazon DevOps Agent itself — Bedrock's role is a single contextualization step per finding. A Converse API call with a constrained system prompt is the cheaper, lower-latency option.

### Why a Lambda Function URL (not API Gateway)?

Same reason the runtime-migration demo does not have API Gateway: fewer moving parts, lower idle cost, and direct SigV4 auth against IAM using credentials the Identity Pool already mints for the browser.

### Why ECS Fargate for Prowler (not Lambda)?

Prowler scans a medium-sized account in 3–10 minutes — too long for Lambda's 15-minute hard limit when scanning multiple regions, and awkward to package with its dependencies. Fargate runs the official `toniblyx/prowler` image as-is.

## Contributing

See [CONTRIBUTING.md](../../CONTRIBUTING.md).

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications).

## License

MIT-0. See [LICENSE](../../LICENSE).
