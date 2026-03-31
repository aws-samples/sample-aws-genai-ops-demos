# Intelligent EKS Incident Investigation with Amazon DevOps Agent
*Automatically detect, investigate, and diagnose EKS infrastructure incidents using Amazon DevOps Agent — reducing mean time to resolution from hours to minutes*

## Overview

When a microservice running on EKS fails, on-call engineers spend 30–60 minutes manually checking pods, logs, database connectivity, and security groups before identifying the root cause. This demo deploys a 3-service payment platform on Amazon EKS, wires CloudWatch alarms to the Amazon DevOps Agent, and lets you inject real incidents to watch the agent investigate automatically.

The demo does not require any manual investigation — the DevOps Agent checks pod status, reads container logs, tests RDS connectivity, reviews security groups, and delivers a root cause analysis with remediation steps.

## At a Glance

- **Duration**: ~25 min deployment + ~5 min demo
- **Difficulty**: Intermediate
- **Target Audience**: SREs, DevOps Engineers, Platform Engineers
- **Key Technologies**: Amazon EKS, Amazon DevOps Agent, CloudWatch, RDS PostgreSQL, Cognito, CloudFront, AWS CDK (TypeScript)
- **Estimated Cost**: ~$6.50/day while running — see [Cost Estimate](#cost-estimate)

## Architecture

![Architecture Overview](docs/architecture-overview.drawio.svg)

The platform has three layers:

- **Application Layer** — CloudFront serves the React portal from S3 and routes API traffic through an NLB to three microservices running on EKS (Merchant Gateway, Payment Processor, Webhook Service), backed by RDS PostgreSQL and SQS for async webhook delivery.
- **Observability & Incident Response** — Fluent Bit ships container logs to CloudWatch. Metric filters trigger alarms that flow through SNS → Lambda (HMAC-signed) → Amazon DevOps Agent, which automatically investigates pods, logs, RDS connectivity, and security groups to deliver a root cause analysis.
- **CI/CD Pipeline** — CodeBuild builds container images from S3 source bundles into ECR. AWS CDK (8 stacks) provisions all infrastructure, and Kustomize manages Kubernetes manifests per environment.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full architecture documentation including network design, security model, and data flows. Editable diagrams are available in [docs/architecture-overview.drawio](docs/architecture-overview.drawio) (high-level) and [docs/architecture.drawio](docs/architecture.drawio) (detailed).

## Prerequisites

- AWS CLI v2 with configured credentials and default region
- kubectl v1.31+
- Node.js 20+ with npm
- `zip` utility
- Git

No local Docker or Java required — container images are built in the cloud via AWS CodeBuild.

## Quick Start

### 1. Clone the repository and navigate to the demo

```bash
git clone https://github.com/<org>/sample-aws-genai-ops-demos.git
cd sample-aws-genai-ops-demos/observability/eks-investigation-devops-agent
```

### 2. Deploy

> **⚠️ Interactive step required during deployment:** The script will pause early on and ask you to generate a DevOps Agent webhook from the AWS console. Have a browser ready — the script prints the exact console URL to open. See details below.

```bash
# macOS / Linux
bash deploy-all.sh

# Windows / PowerShell
.\deploy-all.ps1
```

The script deploys 8 CDK stacks, builds 3 container images via CodeBuild, applies Kubernetes manifests, seeds the database, and builds the React frontend. Total: ~25 minutes.

### 3. Generate the DevOps Agent webhook (interactive step)

Early in the deployment, the script sets up the DevOps Agent integration automatically (IAM roles, Agent Space, account association). It then opens a link to the DevOps Agent console and pauses:

1. Open the console link printed by the script
2. Select your Agent Space → Capabilities → Webhook → Add
3. Click Next, then click "Generate URL and secret key"
4. Copy and paste the webhook URL and secret key back into the terminal

The deployment will not continue without the webhook — it's the link between CloudWatch alarms and the DevOps Agent investigation.

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

Save the Portal URL — you'll need it for the demo.

## How the Incident Detection Works

Understanding this flow is key to explaining the demo:

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

## Run the Demo (~5 min)

### 1. Open the DevOps Agent console first

Before injecting the incident, open the DevOps Agent console in a separate browser tab so you can watch the investigation appear in real-time:

`https://<your-region>.console.aws.amazon.com/devops-agent/home`

### 2. Verify the platform

Open the Portal URL, log in with `demo-merchant-1` / `DemoPass2026!`, browse the catalog, and authorize a payment. This proves the platform is healthy before the incident.

### 3. Inject a database connection failure

```bash
bash scripts/demo-incident.sh inject          # Bash
.\scripts\demo-incident.ps1 -Action inject    # PowerShell
```

This sets a wrong DB password and restarts the payment-processor pod so it genuinely cannot connect to RDS.

### 4. Watch the alarm fire (~2 min)

```bash
bash scripts/demo-incident.sh status          # Bash
.\scripts\demo-incident.ps1 -Action status    # PowerShell
```

You should see `payment-processor` in **CrashLoopBackOff** and the CloudWatch alarm in **ALARM** state.

You can also verify the alarm directly in the CloudWatch console (replace `<your-region>` with your deployment region):
`https://<your-region>.console.aws.amazon.com/cloudwatch/home#alarmsV2:alarm/devops-agent-eks-dev-database-connection-errors`

### 5. Watch the DevOps Agent investigate

Switch to the **DevOps Agent console tab** you opened in step 1. The agent automatically:

- Checks pod status and reads container logs on the EKS cluster
- Inspects RDS database connectivity and status
- Reviews security group rules
- Analyzes credentials and IAM permissions
- Delivers a **root cause analysis** with remediation steps

### 6. Rollback

```bash
bash scripts/demo-incident.sh rollback        # Bash
.\scripts\demo-incident.ps1 -Action rollback  # PowerShell
```

Before running the demo again: `bash scripts/demo-incident.sh reset`

## CDK Stacks

All stack IDs include the region suffix for multi-region deployment support (e.g., `DevOpsAgentEksNetwork-us-east-1`).

| Stack | Purpose | Key Resources |
|-------|---------|---------------|
| `DevOpsAgentEksNetwork-{region}` | Networking | VPC (10.0.0.0/16), 2 AZs, public + private + data subnets, 1 NAT gateway, security groups |
| `DevOpsAgentEksCompute-{region}` | Compute | EKS cluster (K8s 1.33), managed node group (Graviton or x86), IRSA roles |
| `DevOpsAgentEksPipeline-{region}` | CI/CD | 3 CodeBuild projects, 3 ECR repositories |
| `DevOpsAgentEksDatabase-{region}` | Data | RDS PostgreSQL 15 (db.t3.micro, encrypted), Secrets Manager |
| `DevOpsAgentEksAuth-{region}` | Auth | Cognito User Pool with custom attributes |
| `DevOpsAgentEksFrontend-{region}` | Frontend | CloudFront distribution, S3 bucket with OAC |
| `DevOpsAgentEksMonitoring-{region}` | Observability | CloudWatch log groups, metric filters, alarms |
| `DevOpsAgentEksDevOpsAgent-{region}` | Incident response | SNS → Lambda → DevOps Agent webhook |

## Project Structure

```
├── deploy-all.sh / .ps1              # One-command deployment
├── cdk/
│   ├── bin/app.ts                    # CDK entry point (8 stacks, region-suffixed)
│   ├── lib/                          # Stack definitions
│   ├── lambda/                       # DevOps Agent trigger Lambda
│   └── test/                         # Property-based tests (fast-check)
├── k8s/                              # Kubernetes manifests (Kustomize)
│   ├── base/                         # Deployments, services, configmap, Fluent Bit
│   └── overlays/dev|staging|prod     # Environment-specific patches
├── services/
│   ├── merchant-portal/              # React 18 + Vite + TypeScript (CloudFront)
│   ├── merchant-gateway/             # Node.js 20 + Express + TypeScript (EKS)
│   ├── payment-processor/            # Java 21 + Spring Boot 3.5 (EKS)
│   └── webhook-service/              # Node.js 20 + TypeScript (EKS)
├── scripts/
│   ├── demo-incident.sh / .ps1       # Inject / rollback demo incidents
│   ├── setup-devops-agent.sh / .ps1  # DevOps Agent Space + IAM setup
│   └── cleanup.sh / .ps1             # Delete all resources
└── docs/
    ├── ARCHITECTURE.md               # Full architecture documentation
    ├── architecture-overview.drawio   # High-level architecture diagram
    └── architecture.drawio            # Detailed architecture diagram
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
| CloudFront + S3 + ECR + Secrets | Minimal demo traffic | ~$8 |

### Cost Optimization

- **Graviton (ARM64)**: Default architecture saves ~20% on EC2 vs x86
- **Single NAT Gateway**: 1 instead of 2 to reduce cost
- **Single-AZ RDS**: No Multi-AZ standby (demo only)
- **Tear down when not in use**: `bash scripts/cleanup.sh` deletes everything

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| Pods stuck in **Pending** | Nodes scaled to 0 | Scale up: `aws eks update-nodegroup-config --cluster-name devops-agent-eks-dev-cluster --nodegroup-name $(aws eks list-nodegroups --cluster-name devops-agent-eks-dev-cluster --query 'nodegroups[0]' --output text) --scaling-config desiredSize=2,minSize=1,maxSize=5` |
| **504** Gateway Timeout | Backend pods not running | Check: `kubectl get pods -n payment-demo` |
| **500** on payment | DB credential mismatch | Check logs: `kubectl logs -l app.kubernetes.io/name=payment-processor -n payment-demo --tail=50` |
| CloudFront returns **403** | S3/OAC misconfigured | Re-run: `bash deploy-all.sh` |

## Cleanup

```bash
bash scripts/cleanup.sh              # Bash
.\scripts\cleanup.ps1                # PowerShell
```

The cleanup script deletes all resources in reverse dependency order: ECR images, EKS cluster, RDS instance, CloudFormation stacks, S3 buckets, Secrets Manager secrets, CloudWatch log groups, and DevOps Agent resources.

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
