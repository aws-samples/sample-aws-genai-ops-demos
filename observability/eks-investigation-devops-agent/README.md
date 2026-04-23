# Intelligent EKS Incident Investigation with Amazon DevOps Agent
*Automatically detect, investigate, and diagnose EKS infrastructure incidents using Amazon DevOps Agent — reducing mean time to resolution from hours to minutes*

## Overview

When a microservice running on EKS fails, on-call engineers spend 30–60 minutes manually checking pods, logs, database connectivity, and security groups before identifying the root cause. This demo deploys a 3-service payment platform on Amazon EKS, wires CloudWatch alarms to the Amazon DevOps Agent, and lets you inject real incidents to watch the agent investigate automatically.

The demo includes a **DevOps Agent Lab** — a built-in control center for injecting failures, managing agent skills, viewing investigation logs, and monitoring account usage. No manual investigation required.

## At a Glance

- **Duration**: ~25 min deployment + ~10 min demo
- **Difficulty**: Intermediate
- **Target Audience**: SREs, DevOps Engineers, Platform Engineers
- **Key Technologies**: Amazon EKS, Amazon DevOps Agent, Amazon Bedrock AgentCore Gateway, CloudWatch, RDS PostgreSQL, Cognito, CloudFront, AWS CDK (TypeScript), Kiro Power (AWS MCP Server)
- **Estimated Cost**: ~$6.50/day while running — see [Cost Estimate](#cost-estimate)

## DevOps Agent Features Demonstrated

| Feature | How It's Shown |
|---------|---------------|
| **Automated Incident Investigation** | Inject DB or DNS failures → alarm fires → agent investigates autonomously |
| **Custom Skills** | Create a business-context skill → agent uses it in Chat to produce executive-ready reports |
| **On-demand Chat** | Ask the agent about platform issues → get structured reports with SLA and revenue impact |
| **Account Usage & Quotas** | Live usage dashboard in the Lab UI (investigation, evaluation, on-demand hours) |
| **Investigation Logs** | Compact view of recent investigations with tool calls, skills loaded, and summaries |
| **Kiro Power (IDE Integration)** | Use the AWS DevOps Agent Power in Kiro to investigate, map topology, and review architecture — without leaving the IDE |
| **Custom MCP Server Integration** | Agent queries a custom MCP server via AgentCore Gateway to assess business impact from the payment database during incidents |

## Architecture

![Architecture Overview](docs/architecture-overview.drawio.svg)

The platform has three layers:

- **Application Layer** — CloudFront serves the React portal from S3 and routes API traffic through an NLB to three microservices running on EKS (Merchant Gateway, Payment Processor, Webhook Service), backed by RDS PostgreSQL and SQS for async webhook delivery.
- **Observability & Incident Response** — Fluent Bit ships container logs to CloudWatch. Metric filters trigger alarms that flow through SNS → Lambda (HMAC-signed) → Amazon DevOps Agent, which automatically investigates pods, logs, RDS connectivity, and security groups to deliver a root cause analysis. A custom MCP server powered by AgentCore Gateway and Lambda gives the agent read-only access to the payment database, enabling business impact assessment (transaction volumes, failure rates, processing gaps) alongside infrastructure diagnostics.
- **CI/CD Pipeline** — CodeBuild builds container images from S3 source bundles into ECR. AWS CDK (10 stacks) provisions all infrastructure, and Kustomize manages Kubernetes manifests per environment.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full architecture documentation including network design, security model, and data flows.

## Prerequisites

- **AWS CLI v2.34.21+** with configured credentials and default region
- kubectl v1.31+
- Node.js 20+ with npm
- `zip` utility
- `jq` utility
- Git

No local Docker or Java required — container images are built in the cloud via AWS CodeBuild.

## Quick Start

### 1. Clone the repository and navigate to the demo

```bash
git clone https://github.com/<org>/sample-aws-genai-ops-demos.git
cd sample-aws-genai-ops-demos/observability/eks-investigation-devops-agent
```

### 2. Deploy

> **⚠️ Interactive step required during deployment:** The script will pause and ask you to generate a DevOps Agent webhook from the AWS console. Have a browser ready — the script prints the exact console URL to open.

> **DevOps Agent region:** By default, the Agent Space is created in `us-east-1` regardless of your current default region for all the other stacks. To use a different supported region:
> ```powershell
> # PowerShell
> $env:DEVOPS_AGENT_REGION = "eu-west-1"
> .\deploy-all.ps1
> ```
> ```bash
> # Bash
> export DEVOPS_AGENT_REGION=eu-west-1
> bash deploy-all.sh
> ```

```bash
# macOS / Linux
bash deploy-all.sh

# Windows / PowerShell
.\deploy-all.ps1
```

The deployment script:
1. Creates the DevOps Agent Space, IAM roles, Operator Access, and account association (in the DevOps Agent region)
2. Prompts for the webhook URL and secret (generated in the DevOps Agent console)
3. Deploys 10 CDK stacks (EKS, RDS, CloudFront, monitoring, MCP server infra, etc.) in current region
4. Builds 3 container images via CodeBuild
5. Applies Kubernetes manifests and seeds the database (including the MCP read-only DB user)
6. Builds and deploys the React frontend
7. Deploys the Payment Transaction Insights MCP server via AgentCore Gateway and registers it with DevOps Agent

Total: ~25 minutes.

### Deployment Output

```
==============================================
 Deployment Complete
==============================================

Portal URL:     https://<id>.cloudfront.net
API Endpoint:   https://<id>.cloudfront.net/api/v1

Demo Login:
  Username: demo-merchant-1
  Password: DemoPass2026!
```

## How the Incident Detection Works

```
Payment Processor crashes (wrong DB password)
        │
        ▼
Fluent Bit ships error logs → CloudWatch Logs
        │
        ▼
Metric Filter detects "database connection" errors
        │
        ▼
CloudWatch Alarm triggers (threshold breached)
        │
        ▼
SNS Topic notifies Lambda function
        │
        ▼
Lambda signs payload with HMAC-SHA256 → calls DevOps Agent webhook
        │
        ▼
DevOps Agent investigates: pods, logs, RDS, security groups
        │
        ▼
Root cause analysis + remediation steps delivered
```

No human intervention needed between the crash and the diagnosis.

## DevOps Agent Lab

The Lab is a built-in demo control center accessible via the 🧪 icon in the portal (lower right hand corner). It's powered by a separate Lambda-based API (outside the EKS cluster) that uses kubectl to inject and rollback failures.

**How it works:**
- A Lambda function in VPC runs kubectl commands against the EKS cluster via a kubectl Lambda layer
- EKS authentication uses STS presigned URLs (same mechanism as `aws eks get-token`)
- API Gateway exposes routes for inject, rollback, status, usage, and investigation logs
- CloudFront routes `/admin/*` requests to the API Gateway
- DynamoDB stores scenario timers for server-side auto-revert (10 minutes)
- The Lambda calls the DevOps Agent API (SigV4-signed, cross-region) for usage and investigation data

**Why Lambda outside the cluster:** If we put the simulator inside EKS, a DNS failure scenario would kill the simulator too. The Lambda is isolated from cluster failures.

**Lab sections:**

| Section | API Endpoint | Data Source |
|---------|-------------|-------------|
| 🔥 Scenarios | `POST/DELETE /admin/scenarios/{id}/inject` | kubectl → EKS |
| Status cards | `GET /admin/status` | kubectl + CloudWatch |
| 🧠 Skills | N/A (copy-paste to DevOps Agent Console) | Static content in UI |
| 🔌 MCP Tools | N/A (enable in DevOps Agent Console) | Static content in UI |
| 📋 Logs | `GET /admin/logs` | DevOps Agent API (list-backlog-tasks, list-executions, list-journal-records) |
| 📊 Usage | `GET /admin/usage` | DevOps Agent API (get-account-usage) |

## Run the Demo

Here is a suggested demonstration workflow:

### 1. Verify the platform

Open the Portal URL, log in with `demo-merchant-1` / `DemoPass2026!`, browse the catalog, and authorize a payment. This proves the platform is healthy before the incident.

### 2. Open the DevOps Agent Lab

Click the 🧪 lab icon in the bottom-right corner of the portal. The Lab has four sections:

- **🔥 Scenarios** — inject real infrastructure failures
- **🧠 Skills** — create a custom skill to teach the agent your business context
- **📋 Logs** — view recent investigations with metrics (tool calls, skills loaded, duration)
- **📊 Usage** — monitor DevOps Agent account usage and estimated cost

### 3. Inject a failure (automated investigation)

Pick a scenario and click **Inject**:

| Scenario | What Breaks | How the Agent Finds It |
|----------|------------|----------------------|
| **Database Connection Failure** | Wrong DB password → CrashLoopBackOff | Reads pod logs, traces to credential mismatch |
| **DNS Resolution Failure** | CoreDNS scaled to 0 → all DNS fails | Traces from app errors across namespaces to kube-system |

Wait ~2 minutes for the CloudWatch alarm to fire. The agent starts investigating automatically.

### 4. Watch the investigation

Open the DevOps Agent Operator Access (link in the Lab UI) to watch the agent:
- Check pod status and read container logs
- Inspect RDS database connectivity
- Review security groups and recent changes
- Deliver a root cause analysis with remediation steps

The Lab's **Logs** section shows a compact summary of each investigation with metrics.

### 5. Demo the Skills feature

This demonstrates how custom skills add business context the agent can't discover from infrastructure alone. The demo includes a ready-made skill that teaches the agent about the Helios commerce platform — revenue figures, SLA budgets, merchant tiers, and PCI-DSS requirements — so it can format investigation findings into executive-ready incident reports.

1. In the Lab's **Skills** section, expand the skill card
2. Copy-paste the fields into the Operator Access Skills page (3 clicks)
3. Open **Chat** in Operator Access and ask: *"Users are reporting slowness on the Helios commerce platform, can you investigate and format a proper report?"*
4. The agent produces an executive-ready report with:
   - Revenue impact (€1,667/min based on €2.4M/day)
   - SLA budget tracking (21.6 min/month)
   - Severity classification (P1/P2/P3/SECURITY)
   - PCI-DSS compliance assessment
   - Merchant-specific impact analysis

Without the skill, the agent reports technical findings only. With the skill, it adds business context, SLA tracking, and compliance assessment.

### 6. Demo the Custom MCP Server Integration

DevOps Agent natively reads CloudWatch logs and metrics — it can tell you *what broke* and *how often*. But logs can't answer the questions that matter most during an outage: how many transactions failed, which merchants are affected, and how long payment processing has been stalled. That information lives in the database, not in logs. Worse, when processing stops entirely, there are no errors to log — the absence of successful transactions is invisible to traditional observability.

This demo bridges that gap with a custom MCP server that gives the agent read-only access to the payment database through AgentCore Gateway. The agent goes from reporting "Payment Processor pods are in CrashLoopBackOff" to reporting "payment processing has been stalled for 12 minutes, 847 transactions are stuck, 3 merchants affected." The first is an ops finding. The second is something you can hand to a VP.

The infrastructure is designed for private connectivity — the Gateway is accessible via a VPC endpoint (PrivateLink), and the Lambda runs in private subnets with a locked-down security group. A private connection (VPC Lattice) is created during deployment for DevOps Agent to reach the Gateway without traversing the public internet. The MCP server is registered with OAuth (Cognito client credentials) for authentication.

**Setup**: The MCP server infrastructure is fully automated during deployment (step 10). Tools must be manually enabled in the DevOps Agent Console — open Capabilities → MCP Servers → pay-txn-mcp → select the tools you want the agent to use. The Lab's MCP Tools section has step-by-step instructions.

**MCP Tools — what the agent can (and can't) access:**

| Tool | What It Returns | Why It Matters |
|------|----------------|----------------|
| `get_transaction_summary` | Transaction counts and totals grouped by status for the last N minutes (max 60 min) | Shows whether payments are flowing normally or stalled — the health pulse of the business |
| `get_recent_failures` | Failed transactions with error codes, merchant names, and amounts (max 50 rows, max 60 min) | Identifies which merchants are affected and the nature of failures during an outage |
| `get_processing_gap` | Time since last successfully captured payment + count/value of stuck authorizations | Detects the *absence of activity* — the signal that's invisible in logs and metrics |
| `get_incident_impact` | Post-incident analysis over an absolute time range: transaction counts by status, dollar amounts, affected merchants, baseline comparison, and state transition latency | Enables post-mortem business impact reports — "what was the cost of the incident between 14:00 and 14:25?" |

These are the *only* operations available. There is no `run_query` tool — the agent cannot execute arbitrary SQL. Each tool runs a fixed, parameterized query with clamped inputs. The Lambda connects as a dedicated `mcp_readonly` PostgreSQL user with `default_transaction_read_only = on`, and its security group restricts egress to RDS (port 5432) and Secrets Manager (HTTPS) only. This gives the agent a narrow, read-only window into business state without exposing the full database.

The Payment Processor records every state transition (PENDING → AUTHORIZED → CAPTURED, etc.) in a `transaction_events` table with timestamps. This enables `get_incident_impact` to measure how long transactions were stuck and compare capture latency during an incident against the baseline.

**The demo flow:**

1. Establish a baseline — open **Chat** in Operator Access and ask:
   > *"What's the current state of payment processing on the EKS cluster? Check both infrastructure health and transaction activity."*
   The agent queries CloudWatch (native) and the MCP server (custom) to show healthy infrastructure alongside normal transaction flow.

2. Inject the **Database Connection Failure** scenario from the Lab
3. Wait ~2 minutes for the automated investigation to start
4. Ask the business impact question:
   > *"What's the business impact of this incident? How many transactions are affected and what's the revenue exposure?"*
   The agent uses the MCP tools to discover: no successful captures since the outage started, transactions stuck in AUTHORIZED, and which merchants are affected — business impact evidence that isn't in logs or metrics.

**Why this matters:** Logs tell you what failed. Metrics tell you how often. But the database tells you what *didn't happen* — the absence of successful transactions, the growing backlog of stuck payments. That's the gap the MCP server fills.

### 7. Demo the Kiro Power for DevOps Agent

This demonstrates how a developer can leverage the DevOps Agent directly from their IDE to review architecture, map topology, and understand dependencies before making changes — the daily workflow, not just emergency incident response. No need to switch to the AWS console or Operator Access.

**Setup** (one-time): Install the AWS DevOps Agent Power in Kiro:

[![Add to Kiro](https://kiro.dev/images/add-to-kiro.svg)](https://kiro.dev/launch/powers/aws-devops-agent)

**The demo prompt** — open Kiro in this project and ask:

> *"I'm new to this project. Can you map the Helios payment platform topology — what's running on EKS, what databases and queues it uses, how traffic flows from CloudFront to the backend, and flag anything that looks unhealthy?"*

The Power will:
1. Discover the Agent Space and trigger an investigation
2. Stream findings in real-time as the agent maps the topology: EKS services, RDS PostgreSQL, security groups, CloudFront distribution, NLB routing
3. Deliver a complete architecture overview with health status — all inside Kiro

**Why this matters:** The developer has the CDK stacks, Kubernetes manifests, and service code open in their editor. The Power bridges local workspace knowledge with the agent's live AWS discovery. This is the daily workflow — understanding your architecture before making changes — not just emergency incident response.

**Follow-up prompts to try:**
- *"Before I change the RDS instance type, what depends on the database and are there any active alarms?"*
- *"What's the blast radius if I modify the EKS node security group?"*
- *"The payment-processor pods are in CrashLoopBackOff — can you investigate? Here's what I see in the logs..."* (after injecting a failure from the Lab)

### 8. Rollback

Click **Rollback** on the scenario card, or wait for the auto-revert timer (10 minutes).

## CDK Stacks

All stack IDs include the region suffix for multi-region deployment support.

| Stack | Purpose | Key Resources |
|-------|---------|---------------|
| `DevOpsAgentEksNetwork-{region}` | Networking | VPC, 2 AZs, public + private + data subnets, NAT gateway |
| `DevOpsAgentEksCompute-{region}` | Compute | EKS cluster (K8s 1.33), managed node group (Graviton), IRSA |
| `DevOpsAgentEksPipeline-{region}` | CI/CD | 3 CodeBuild projects, 3 ECR repositories |
| `DevOpsAgentEksDatabase-{region}` | Data | RDS PostgreSQL 15 (db.t3.micro, encrypted), Secrets Manager |
| `DevOpsAgentEksAuth-{region}` | Auth | Cognito User Pool with custom attributes |
| `DevOpsAgentEksFrontend-{region}` | Frontend | CloudFront distribution, S3 bucket with OAC |
| `DevOpsAgentEksMonitoring-{region}` | Observability | CloudWatch log groups, metric filters, alarms, SNS topic |
| `DevOpsAgentEksDevOpsAgent-{region}` | Incident response | SNS → Lambda → DevOps Agent webhook, Secrets Manager |
| `DevOpsAgentEksFailureSimulatorApi-{region}` | Lab API | API Gateway, Lambda (kubectl), DynamoDB (timers) |
| `DevOpsAgentEksMcpServer-{region}` | MCP server infra | AgentCore Gateway, Lambda (VPC), Secrets Manager (read-only DB creds), Cognito (OAuth), VPC endpoint |

## Project Structure

```
├── deploy-all.sh / .ps1              # One-command deployment
├── cdk/
│   ├── bin/app.ts                    # CDK entry point (10 stacks, region-suffixed)
│   ├── lib/                          # Stack definitions
│   └── lambda/
│       ├── devops-agent-trigger/     # Alarm → webhook Lambda
│       ├── failure-simulator-api/    # Lab API Lambda (inject/rollback/status/usage/logs)
│       └── mcp-transaction-insights/ # Payment DB query Lambda (AgentCore Gateway target)
├── k8s/                              # Kubernetes manifests (Kustomize)
│   ├── base/                         # Deployments, services, configmap, Fluent Bit
│   └── overlays/dev|staging|prod     # Environment-specific patches
├── services/
│   ├── merchant-portal/              # React 18 + Vite + TypeScript (CloudFront)
│   ├── merchant-gateway/             # Node.js 20 + Express + TypeScript (EKS)
│   ├── payment-processor/            # Java 21 + Spring Boot 3.5 (EKS)
│   └── webhook-service/              # Node.js 20 + TypeScript (EKS)
├── scripts/
│   ├── setup-devops-agent.sh / .ps1  # Agent Space + IAM + webhook setup (6 steps)
│   ├── deploy-mcp-server.sh          # MCP server deployment to AgentCore Runtime
│   └── cleanup.sh / .ps1             # Delete all resources
└── docs/
    ├── ARCHITECTURE.md               # Full architecture documentation
    ├── architecture-overview.drawio   # High-level architecture diagram (editable)
    └── architecture.drawio            # Detailed architecture diagram (editable)
```

## Cost Estimate

All costs approximate, based on `us-east-1` pricing.

### Running Cost (~$6.50/day)

| Resource | Specification | $/month |
|----------|--------------|---------|
| EKS Cluster | Control plane | $73 |
| EC2 Instances | 2× t4g.medium (Graviton) | $40 |
| NAT Gateway | 1 gateway + data processing | $32 |
| CloudWatch | Log groups, metrics, alarms | $15 |
| RDS PostgreSQL | db.t3.micro, single-AZ | $14 |
| DevOps Agent | Investigation hours ($29.88/hr) | ~$5 (demo usage) |
| CloudFront + S3 + ECR + Secrets | Minimal demo traffic | ~$8 |

### Cost Optimization

- **Graviton (ARM64)**: Default architecture saves ~20% on EC2 vs x86
- **Single NAT Gateway**: 1 instead of 2 to reduce cost
- **Single-AZ RDS**: No Multi-AZ standby (demo only)
- **Tear down when not in use**: `.\scripts\cleanup.ps1` deletes everything

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| Pods stuck in **Pending** | Nodes scaled to 0 | Scale up: `aws eks update-nodegroup-config --cluster-name devops-agent-eks-dev-cluster --nodegroup-name $(aws eks list-nodegroups --cluster-name devops-agent-eks-dev-cluster --query 'nodegroups[0]' --output text) --scaling-config desiredSize=2,minSize=1,maxSize=5` |
| **504** Gateway Timeout | Backend pods not running | Check: `kubectl get pods -n payment-demo` |
| **500** on payment | DB credential mismatch | Check logs: `kubectl logs -l app.kubernetes.io/name=payment-processor -n payment-demo --tail=50` |
| CloudFront returns **403** | S3/OAC misconfigured | Re-run deployment |
| Agent Space not found | CLI too old | Upgrade AWS CLI to >= 2.34.21 |
| Investigation shows "no AWS account access" | Missing association | Re-run `.\scripts\setup-devops-agent.ps1` |
| CDK bootstrap "S3 bucket already exists" | Broken CDK bootstrap stack | Run `npx cdk bootstrap --force` or delete the orphaned S3 bucket `cdk-hnb659fds-assets-*` and re-bootstrap. See [CDK bootstrap troubleshooting](https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping-troubleshoot.html) |

## Recreating the Agent Space

To start fresh with a clean Agent Space (e.g., for A/B testing skills):

```powershell
# Delete the old space
aws devops-agent delete-agent-space --agent-space-id <space-id> --region us-east-1

# Re-run setup — creates new space, prompts for webhook, updates deployed Lambdas
.\scripts\setup-devops-agent.ps1          # PowerShell
bash scripts/setup-devops-agent.sh        # Bash
```

The setup script detects the deployed demo and live-updates Lambda env vars and Secrets Manager — no CDK redeploy needed.

## Cleanup

```bash
bash scripts/cleanup.sh              # Bash
.\scripts\cleanup.ps1                # PowerShell
```

The cleanup script deletes all resources in reverse dependency order: AgentCore Gateway MCP server registration, private connections, CloudFormation stacks, ECR images, EKS kubectl-managed resources, RDS instance, S3 buckets, Secrets Manager secrets, CloudWatch log groups, DevOps Agent Space, and IAM roles.

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
