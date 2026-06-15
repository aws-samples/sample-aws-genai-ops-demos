# GenAI Operations Analytics Tool (G.O.A.T.)

Unified conversational interface for querying AWS operational data across Cost Explorer, Health Dashboard, Support Cases, Trusted Advisor, Cost & Usage Reports, and VPC Network Captures — powered by Amazon Bedrock AgentCore and Amazon Nova models.

## Overview

Operations teams juggle multiple AWS consoles to monitor costs, health events, support cases, and optimization recommendations. G.O.A.T. solves this by providing a single conversational interface where natural language questions are routed to specialized AI agents that query the right AWS services and return correlated, formatted results.

The solution uses a **hybrid multi-agent architecture**:
- An **Orchestration Agent** (Strands Agent SDK + Amazon Nova Pro) handles intent classification, sub-agent coordination, cross-domain correlation, and natural language response generation
- Six **Sub-Agents** (plain Python handlers on AgentCore) each handle a specific operational domain — Cost, Health, Support, Trusted Advisor, CUR, and Network — calling AWS APIs directly via boto3

A React + Cloudscape frontend provides streaming chat, prompt templates, knowledge management, conversation history, and data visualization.

## At a Glance

| | |
|---|---|
| **Duration** | 25-30 minutes (full deployment) |
| **Difficulty** | Intermediate |
| **Target Audience** | DevOps Engineers, SREs, FinOps Engineers, Cloud Architects |
| **Key Technologies** | Amazon Bedrock AgentCore, Amazon Nova Pro/Lite, Strands Agent SDK, AWS CDK (TypeScript), React + Cloudscape, VPC Traffic Mirroring, Athena |
| **Estimated Cost** | ~$8-48/month for occasional use (see breakdown below) |

## Prerequisites

### Software Requirements
- [**Node.js 20+**](https://nodejs.org/) with npm
- [**Python 3.11+**](https://www.python.org/downloads/) with pip
- [**AWS CLI v2.31.13+**](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials
- [**AWS CDK**](https://docs.aws.amazon.com/cdk/v2/guide/getting-started.html) (installed automatically by deployment scripts)
- [**Docker**](https://www.docker.com/products/docker-desktop/) (must be running during deployment — used for building agent container images)

### AWS Requirements
- AWS account with appropriate permissions
- Amazon Bedrock model access enabled for **Amazon Nova Pro** and **Amazon Nova Lite** — enable in the [Bedrock console](https://console.aws.amazon.com/bedrock/home#/modelaccess) under "Model access"
- Amazon Bedrock AgentCore available in your region
- **Cost Explorer enabled** — activate in the [Billing console](https://console.aws.amazon.com/billing/home#/costexplorer) if not already enabled (first-time activation takes up to 24 hours to populate data)
- **Support Agent & Trusted Advisor Agent**: Require an AWS Business, Enterprise On-Ramp, or Enterprise Support plan. Without one, these agents will return subscription errors. The other agents work on any plan.
- **CUR module only**: A Cost and Usage Report delivered to S3 with an Athena/Glue table (see [CUR Setup](#cur-setup) below)
- Services used: Bedrock AgentCore, Bedrock (Nova models), Cognito, DynamoDB, S3, CloudFront, CodeBuild, ECR, Athena (for CUR)

### IAM Permissions

Your IAM user/role needs permissions for:
- CloudFormation (CDK deployment)
- Amazon Bedrock and Bedrock AgentCore
- Amazon Cognito (User Pool, Identity Pool)
- DynamoDB (table creation and access)
- S3 (bucket creation, static hosting)
- CloudFront (distribution creation)
- ECR (repository creation, image push)
- CodeBuild (project creation, build execution)
- IAM (role creation for AgentCore runtimes)
- Cost Explorer, Health, Support, Trusted Advisor, Athena (agent API access)

### AWS Credentials Setup

Configure your AWS credentials before deploying:

**Option 1: AWS SSO (recommended)**
```bash
aws configure sso
aws sso login --profile YOUR-PROFILE-NAME
export AWS_PROFILE="YOUR-PROFILE-NAME"  # macOS/Linux
$env:AWS_PROFILE="YOUR-PROFILE-NAME"    # Windows PowerShell
```

**Option 2: IAM access keys**
```bash
aws configure
# Enter Access Key ID, Secret Access Key, and default region
```

Verify credentials:
```bash
aws sts get-caller-identity
```

### CUR Setup

The CUR (Cost & Usage Reports) agent queries cost data via Amazon Athena. This requires a Glue table cataloging your CUR data in S3. Without this setup, the CUR agent will return "table not found" errors. The other four agents (Cost Explorer, Health, Support, Trusted Advisor) work without CUR configuration.

**Step 1: Ensure you have a Cost and Usage Report delivering to S3**

If you don't have one, create it in the [AWS Billing console](https://console.aws.amazon.com/billing/home#/reports). Enable "Amazon Athena" integration when creating the report — this automatically creates the Glue table.

**Step 2: If you already have CUR data in S3 but no Glue table**

Create a Glue database and crawler to catalog the data:

```bash
# Create the Glue database
aws glue create-database --database-input '{"Name": "goat_cur_database", "Description": "G.O.A.T. Cost and Usage Report data"}'

# Create an IAM role for the crawler
aws iam create-role --role-name GoatGlueCrawlerRole \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"glue.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

aws iam attach-role-policy --role-name GoatGlueCrawlerRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole

aws iam put-role-policy --role-name GoatGlueCrawlerRole --policy-name S3CURAccess \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:GetObject","s3:ListBucket"],"Resource":["arn:aws:s3:::YOUR-CUR-BUCKET","arn:aws:s3:::YOUR-CUR-BUCKET/*"]}]}'

# Wait for IAM propagation
sleep 10

# Create and run the crawler (replace YOUR-CUR-BUCKET with your S3 bucket name)
aws glue create-crawler --name goat-cur-crawler --role GoatGlueCrawlerRole \
  --database-name goat_cur_database \
  --targets '{"S3Targets":[{"Path":"s3://YOUR-CUR-BUCKET/"}]}'

aws glue start-crawler --name goat-cur-crawler
```

Wait 1-3 minutes for the crawler to finish, then verify:
```bash
aws glue get-tables --database-name goat_cur_database --query "TableList[].Name" --output table
```

**Step 3: Configure the Athena output bucket permissions**

The CUR agent writes query results to an S3 bucket. The Athena workgroup's default output bucket must be accessible by the CUR agent's IAM role. If you see "Unable to verify/create output bucket" errors, add a bucket policy:

```bash
# Replace ACCOUNT_ID and BUCKET_NAME with your values
# Default bucket name is: athena-query-results-ACCOUNT_ID-REGION
aws s3api put-bucket-policy \
  --bucket athena-query-results-YOUR_ACCOUNT_ID-us-east-1 \
  --policy '{
    "Version": "2012-10-17",
    "Statement": [{
      "Sid": "AllowAthenaQueryResults",
      "Effect": "Allow",
      "Principal": {"AWS": "arn:aws:iam::YOUR_ACCOUNT_ID:root"},
      "Action": ["s3:GetBucketLocation","s3:GetObject","s3:ListBucket","s3:PutObject","s3:AbortMultipartUpload"],
      "Resource": ["arn:aws:s3:::athena-query-results-YOUR_ACCOUNT_ID-us-east-1","arn:aws:s3:::athena-query-results-YOUR_ACCOUNT_ID-us-east-1/*"]
    }]
  }'
```

If the bucket doesn't exist, create it first:
```bash
aws s3 mb s3://athena-query-results-YOUR_ACCOUNT_ID-us-east-1 --region us-east-1
```

**Step 4: Configure the CUR agent environment variables**

The CUR agent reads these environment variables (set in the CDK stack or runtime configuration):

| Variable | Default | Description |
|----------|---------|-------------|
| `ATHENA_DATABASE` | `athenacurcfn_cost_and_usage_report` | Glue database name containing the CUR table |
| `ATHENA_TABLE` | `cost_and_usage_report` | Glue table name for CUR data |
| `ATHENA_WORKGROUP` | `primary` | Athena workgroup to use for queries |

Update these to match your Glue database and table names if they differ from the defaults.

## Estimated Cost Breakdown

The costs below assume you run the demo twice a month (e.g., two 1-hour demo sessions) and tear down AgentCore runtimes between sessions. If you leave runtimes running 24/7, costs will be significantly higher.

### Monthly Costs (2 Demo Sessions/Month)

| Service | Estimated Cost | Notes |
|---------|---------------|-------|
| Bedrock AgentCore (7 runtimes) | $2-10 | ~2 hours active runtime × 2 sessions; tear down between demos |
| Bedrock Nova Pro (orchestration inference) | $1-3 | ~20-40 queries per session at ~$0.03-0.08 each |
| Bedrock Nova Lite (sub-agent inference) | $0.50-1 | Lightweight per-query cost, minimal at low volume |
| DynamoDB (5 tables, on-demand) | $0-1 | Near-zero at low read/write volume |
| S3 (source buckets + frontend + pcap data) | $1-3 | Storage persists between sessions |
| CloudFront (frontend distribution) | $0-1 | Minimal traffic for 2 sessions |
| ECR (7 container images) | $1-4 | ~500MB per image, stored between sessions |
| CodeBuild (container builds) | $2-10 | Only runs during deploy; ~5 min per agent × 7 agents × 2 deploys |
| EC2 Collector (t3.small, Network Agent) | $0-15 | $15/month if running 24/7; $0 if stopped between sessions |
| Cognito (user authentication) | $0 | Free tier covers < 50,000 MAUs |
| **Total** | **~$8-48/month** | |

### Per-Query Cost
- Single-domain query: ~$0.01-0.03
- Cross-domain query (2-3 agents): ~$0.03-0.08

### Always-On vs. Tear-Down Comparison

| Scenario | Monthly Cost |
|----------|-------------|
| 2 demo sessions, tear down between | ~$8-48 |
| Runtimes running 24/7 (continuous) | ~$130-280 |

### Cost Optimization Tips
- Tear down AgentCore runtimes after each demo session (`cdk destroy` the RuntimeStacks)
- Stop the Network Agent EC2 collector between sessions to save ~$15/month
- Deploy only the modules you need (individual module deployment)
- Use Nova Lite for sub-agents (already configured — lower cost than Pro)
- DynamoDB on-demand pricing scales to zero when idle
- Keep ECR images and InfraStacks deployed between sessions to speed up re-deployment (only RuntimeStacks need re-deploy)

## Deployment

G.O.A.T. supports three deployment modes to match your needs.

### Full Deployment (All Modules)

Deploys all 6 sub-agents, the orchestration agent, and the frontend.

**macOS / Linux:**
```bash
cd operations-automation/ai-operations-assistant
chmod +x deploy-all.sh
./deploy-all.sh --mode full
```

**Windows (PowerShell):**
```powershell
cd operations-automation/ai-operations-assistant
.\deploy-all.ps1 -DeploymentMode full
```

### Individual Module Deployment

Deploy a single operational domain (e.g., just Cost or Health).

**macOS / Linux:**
```bash
./deploy-all.sh --mode cost      # Cost module only
./deploy-all.sh --mode health    # Health module only
./deploy-all.sh --mode support   # Support module only
./deploy-all.sh --mode trusted-advisor  # Trusted Advisor only
./deploy-all.sh --mode cur       # CUR module only
./deploy-all.sh --mode network   # Network Agent only
```

**Windows (PowerShell):**
```powershell
.\deploy-all.ps1 -DeploymentMode cost
.\deploy-all.ps1 -DeploymentMode health
.\deploy-all.ps1 -DeploymentMode support
.\deploy-all.ps1 -DeploymentMode trusted-advisor
.\deploy-all.ps1 -DeploymentMode cur
.\deploy-all.ps1 -DeploymentMode network
```

### Progressive Deployment

Start with one module and add more over time. Each module deploys independently without disrupting existing modules.

```bash
# Start with Cost module
./deploy-all.sh --mode cost

# Later, add Health module
./deploy-all.sh --mode health

# When ready, deploy full solution (adds orchestration + frontend)
./deploy-all.sh --mode full
```

### Post-Deployment

After deployment completes, the script displays:
- **Website URL** — CloudFront URL for the frontend
- **Region** — Deployed AWS region
- **Cognito User Pool** — Create a user via AWS Console to sign in

> **Data availability timing**: Cost Explorer data has a 24-48 hour delay. Trusted Advisor findings for new resources can take up to 24 hours to appear. Health events are real-time. Support cases are available immediately.

To create a Cognito user:
```bash
aws cognito-idp admin-create-user \
  --user-pool-id YOUR_USER_POOL_ID \
  --username your@email.com \
  --temporary-password TempPass123! \
  --user-attributes Name=email,Value=your@email.com
```

### Network Capture Authorization

The Network Agent's capture lifecycle actions (`start_capture`, `stop_capture`, `transform_capture`) require membership in the `GOATNetworkCaptureUsers` Cognito group. Read-only actions (ENI inventory, pcap queries) are available to all authenticated users.

**Add a user to the capture group via CLI:**
```bash
aws cognito-idp admin-add-user-to-group \
  --user-pool-id YOUR_USER_POOL_ID \
  --username your@email.com \
  --group-name GOATNetworkCaptureUsers
```

**Add a user via the AWS Console:**
1. Open the [Amazon Cognito console](https://console.aws.amazon.com/cognito/v2/idp/user-pools)
2. Select the **goat-admin-users** user pool
3. Navigate to **Groups** and select **GOATNetworkCaptureUsers**
4. Click **Add user to group** and select the user

**Verify group membership:**
```bash
aws cognito-idp admin-list-groups-for-user \
  --user-pool-id YOUR_USER_POOL_ID \
  --username your@email.com
```

Users not in this group will see capture-related prompt templates in a disabled state in the frontend, and the Orchestration Agent will refuse capture lifecycle requests with a message identifying the required group.

## Demo Scenarios

G.O.A.T. includes pre-built demo scenarios with provisioning scripts that create controlled sets of AWS resources, generating data across all six agent domains. This lets you demonstrate cross-domain correlation with real operational data.

### Prerequisites

- AWS CLI configured with credentials (`aws sts get-caller-identity`)
- G.O.A.T. deployed (at least the relevant modules)
- **Support case creation** requires a Business or Enterprise Support plan — scripts skip case creation gracefully if no plan is active

### Scenario A: Full Account Health Check

Creates EC2 instances, an RDS instance, an unattached EBS volume, an unassociated Elastic IP, and a resolved Support case — triggering Trusted Advisor findings and cost data across all five agent domains (Cost Explorer, Health Dashboard, Support Cases, Trusted Advisor, CUR).

**Setup:**

```bash
# macOS / Linux
cd operations-automation/ai-operations-assistant/demo-scenarios
chmod +x setup-scenario-a.sh
./setup-scenario-a.sh
```

```powershell
# Windows (PowerShell)
cd operations-automation\ai-operations-assistant\demo-scenarios
.\setup-scenario-a.ps1
```

**Suggested demo query:**

> Give me a complete health check of my AWS account

**Expected agent correlation:** The orchestration agent invokes all five sub-agents — Cost Explorer reports new resource spend, Trusted Advisor flags the unattached EBS volume and unassociated Elastic IP, Health Dashboard shows account status, Support Cases returns the resolved demo case, and CUR provides usage detail.

> **Cost note:** Scenario A creates billable resources — 2× EC2 t3.micro, 1× RDS db.t3.micro, 1× EBS 10GB gp2 volume, and 1× Elastic IP. Run the cleanup script after your demo to avoid ongoing charges. All resources are tagged with `goat-demo=true` for easy identification.

### Scenario B: CloudWatch Apr 1 Incident Correlation

Creates a resolved Support case referencing a real CloudWatch health event from April 1, 2026, enabling cross-domain incident correlation between Health Dashboard events and Support case data. No AWS resources are created — zero cost.

**Setup:**

```bash
# macOS / Linux
cd operations-automation/ai-operations-assistant/demo-scenarios
chmod +x setup-scenario-b.sh
./setup-scenario-b.sh
```

```powershell
# Windows (PowerShell)
cd operations-automation\ai-operations-assistant\demo-scenarios
.\setup-scenario-b.ps1
```

**Suggested demo query:**

> We had monitoring gaps on April 1st — was there an AWS issue?

Other suggested queries for Scenario B:
- "I had a CloudWatch problem in April. Was it linked to a health event or a support case?"
- "Show me support cases related to CloudWatch"

**Expected agent correlation:** The orchestration agent correlates the Support case referencing monitoring gaps with the real CloudWatch planned lifecycle event from April 1, 2026 visible in the Health API.

> **Note:** Scenario B depends on the real CloudWatch health event from April 1, 2026 being visible in the AWS Health API history. If the event has aged out of the Health API retention window, the Health agent will not return it — but the Support case still demonstrates cross-domain querying. Scenario B creates no billable resources.

### Scenario C: TLS Fragmentation Reproduction

Reproduces the AWS Network Firewall + Amazon Linux 2023 OpenSSL 3.5.5 ML-KEM TLS Client Hello fragmentation failure mode. Provisions an EKS cluster, an AWS Network Firewall with the legacy `drop established` configuration, and a test pod that triggers the failure against ECR — enabling the Network Agent to detect and correlate the issue with Health events and Support data.

**Setup:**

```bash
# macOS / Linux
cd operations-automation/ai-operations-assistant/demo-scenarios
chmod +x setup-scenario-tls.sh
./setup-scenario-tls.sh
```

```powershell
# Windows (PowerShell)
cd operations-automation\ai-operations-assistant\demo-scenarios
.\setup-scenario-tls.ps1
```

**Suggested demo query:**

```
Capture traffic from the EKS test pod and explain why ECR connections fail
```

Other suggested queries:
- "Why does my EKS pod fail to reach ECR?"
- "Start a capture on the EKS node ENI and check for TLS fragmentation"

**Expected agent correlation:** The orchestration agent correlates the Amazon Linux 2023 update event (OpenSSL 3.5.5 with ML-KEM) from the Health agent, the TLS Client Hello fragmentation and middlebox RSTs from the Network agent, and the known issue identifier from the Support agent into a single root-cause explanation.

> **Cost note:** Scenario C creates billable resources — an EKS cluster (1–2 t3.medium nodes), an AWS Network Firewall, and a dedicated VPC. Run the cleanup script after your demo to avoid ongoing charges. All resources are tagged with `goat-demo=true` and `goat-scenario=tls-fragmentation`.

### Cleanup

Remove all demo resources from all scenarios with a single command:

```bash
# macOS / Linux
cd operations-automation/ai-operations-assistant/demo-scenarios
chmod +x cleanup-scenarios.sh
./cleanup-scenarios.sh
```

```powershell
# Windows (PowerShell)
cd operations-automation\ai-operations-assistant\demo-scenarios
.\cleanup-scenarios.ps1
```

The cleanup script finds all resources tagged with `goat-demo=true` (including `goat-scenario=tls-fragmentation` resources) and removes them in dependency order. Scripts are idempotent — safe to re-run.

For detailed scenario descriptions, step-by-step instructions, and expected agent correlations, see the [Demo Scenarios Guide](./demo-scenarios/README.md).

## Architecture Overview

G.O.A.T. uses a hybrid multi-agent pattern where each component uses the best approach for its role:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Frontend (CloudFront + S3)                       │
│  React + Cloudscape: Chat, Templates, Knowledge, Visualization     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ Cognito Auth
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│              Orchestration Agent (AgentCore Runtime)                │
│     Strands Agent SDK + Nova Pro + @tool functions + Streaming     │
└───┬──────────┬──────────┬──────────┬──────────┬──────────┬─────────┘
    │          │          │          │          │          │
    ▼          ▼          ▼          ▼          ▼          ▼
┌────────┐┌────────┐┌────────┐┌────────┐┌────────┐┌────────┐
│  Cost  ││ Health ││Support ││  T.A.  ││  CUR   ││Network │
│ Agent  ││ Agent  ││ Agent  ││ Agent  ││ Agent  ││ Agent  │
│(boto3) ││(boto3) ││(boto3) ││(boto3) ││(boto3) ││(boto3) │
└───┬────┘└───┬────┘└───┬────┘└───┬────┘└───┬────┘└───┬────┘
    │         │         │         │         │         │
    ▼         ▼         ▼         ▼         ▼         ▼
  Cost      Health   Support   Trusted   Athena    EC2/S3/
 Explorer  Dashboard  API     Advisor   (CUR)    Athena/SFN
```

- **Orchestration Agent**: Uses LLM reasoning (Nova Pro) to classify intent, decide which sub-agents to invoke, correlate cross-domain results, and stream natural language responses
- **Sub-Agents**: Plain Python handlers that receive structured JSON, call AWS APIs via boto3, and return formatted results — no LLM reasoning needed
- **Frontend**: React 18 + Cloudscape with streaming chat, prompt templates, knowledge articles, conversation history, and data visualization

For detailed architecture, see [ARCHITECTURE.md](./ARCHITECTURE.md).

## Troubleshooting

### Deployment Issues

**CDK bootstrap fails**
- Ensure Docker is running (required for CodeBuild container builds)
- Verify AWS credentials: `aws sts get-caller-identity`
- Check region supports Bedrock AgentCore: `aws bedrock-agent-runtime help`

**CodeBuild fails during container build**
- Check CodeBuild logs in CloudWatch
- Ensure ECR repository was created (check InfraStack deployment)
- Verify Docker image builds locally: `cd agents/cost-agent && docker build .`

**Stack deployment timeout**
- AgentCore runtime creation can take 5-10 minutes per agent
- The BuildWaiterFunction Lambda polls CodeBuild — check its CloudWatch logs
- If a stack is stuck, check CloudFormation events in the AWS Console

### Runtime Issues

**"No prompt found in payload" error**
- Verify the frontend is sending `{ "prompt": "your question" }` format
- Check orchestration agent CloudWatch logs for payload details

**Sub-agent timeout (30s)**
- The orchestration agent returns partial results from successful agents
- Check the specific sub-agent's CloudWatch logs for API errors
- Verify the sub-agent's IAM role has the required AWS API permissions

**"Support plan required" error**
- Support Agent and Trusted Advisor Agent require an active AWS Support plan
- Deploy without these modules if you don't have a Support plan

**Frontend shows "Connecting..." indefinitely**
- Verify Cognito User Pool ID and Client ID match deployment outputs
- Check browser console for CORS or authentication errors
- Ensure the Identity Pool allows authenticated access

### Cost-Specific Issues

**Cost Explorer returns empty data**
- Cost Explorer data has a 24-48 hour delay
- Ensure the queried time range is within the last 12 months
- Verify the account has Cost Explorer enabled

**CUR queries fail**
- The CUR agent requires a Glue table cataloging your CUR data — see [CUR Setup](#cur-setup)
- **"Unable to verify/create output bucket"** — Add a bucket policy to the Athena output bucket granting write access (see Step 3 in CUR Setup)
- If you have CUR data in S3 but no Glue table, create one using a Glue crawler
- Verify the `ATHENA_DATABASE` and `ATHENA_TABLE` environment variables match your Glue catalog
- Check the CUR Agent's IAM role has Athena, Glue, and S3 permissions
- Test directly: `aws athena start-query-execution --query-string "SELECT * FROM your_table LIMIT 1" --query-execution-context Database=your_database --work-group primary`

## Project Structure

```
operations-automation/ai-operations-assistant/
├── README.md                          # This file
├── ARCHITECTURE.md                    # Detailed architecture documentation
├── deploy-all.ps1                     # PowerShell deployment script
├── deploy-all.sh                      # Bash deployment script
├── package.json                       # Root package (TypeScript, fast-check, vitest)
├── tsconfig.json                      # TypeScript configuration
├── vitest.config.ts                   # Test configuration
│
├── demo-scenarios/                    # Demo scenario provisioning scripts
│   ├── README.md                      # Demo scenarios guide with queries
│   ├── setup-scenario-a.ps1           # Scenario A: Full Account Health Check
│   ├── setup-scenario-a.sh
│   ├── setup-scenario-b.ps1           # Scenario B: CloudWatch Incident Correlation
│   ├── setup-scenario-b.sh
│   ├── cleanup-scenarios.ps1          # Remove all demo resources
│   └── cleanup-scenarios.sh
│
├── agents/                            # Agent containers
│   ├── cost-agent/                    # Cost Explorer + Cost Optimization Hub
│   ├── health-agent/                  # AWS Health Dashboard
│   ├── support-agent/                 # AWS Support Cases
│   ├── ta-agent/                      # Trusted Advisor
│   ├── cur-agent/                     # Cost & Usage Reports (Athena)
│   ├── network-agent/                 # VPC Packet Capture + Pcap Analysis
│   ├── orchestration-agent/           # Strands Agent SDK orchestrator
│   └── shared/                        # Shared utilities (aws_utils.py, prices.py)
│
├── frontend/                          # React + Cloudscape frontend
│   └── src/
│       ├── App.tsx                    # Main app with auth + routing
│       ├── components/
│       │   ├── ChatInterface.tsx      # Streaming chat UI
│       │   ├── PromptTemplatePanel.tsx # Template library
│       │   ├── KnowledgeManager.tsx   # Knowledge articles
│       │   ├── DataVisualization.tsx   # Charts, tables, cards
│       │   ├── ConversationHistory.tsx # Past conversations
│       │   ├── AccountSelector.tsx     # Cross-account selector
│       │   └── UserPreferences.tsx     # User settings
│       └── lib/dynamodb/              # DynamoDB data access layer
│
├── infrastructure/cdk/                # AWS CDK infrastructure
│   ├── bin/app.ts                     # CDK app entry point
│   └── lib/                           # Stack definitions
│       ├── auth-stack.ts              # Cognito User Pool + Identity Pool
│       ├── base-infra-stack.ts        # Shared InfraStack pattern
│       ├── base-runtime-stack.ts      # Shared RuntimeStack pattern
│       ├── orch-infra-stack.ts        # Orchestration InfraStack
│       └── frontend-stack.ts          # S3 + CloudFront
│
├── scripts/                           # Build utilities
│   ├── build-frontend.ps1             # Frontend build (PowerShell)
│   └── build-frontend.sh              # Frontend build (Bash)
│
├── tests/                             # Test suite
│   ├── properties/                    # Property-based tests (fast-check)
│   ├── unit/                          # Unit tests
│   └── generators/                    # Test data generators
│
└── sample-data/                       # Sample data for testing
```

## Cleanup

To remove all deployed resources:

**macOS / Linux:**
```bash
cd operations-automation/ai-operations-assistant/infrastructure/cdk
npx cdk destroy --all --force
```

**Windows (PowerShell):**
```powershell
cd operations-automation\ai-operations-assistant\infrastructure\cdk
npx cdk destroy --all --force
```

> **Note**: This destroys all stacks. ECR repositories and S3 buckets with `removalPolicy: DESTROY` are cleaned up automatically.

## Key Technologies

| Technology | Purpose |
|------------|---------|
| Amazon Bedrock AgentCore | Agent runtime hosting and orchestration |
| Amazon Nova Pro | LLM reasoning for orchestration agent |
| Amazon Nova Lite | Lightweight processing for sub-agents |
| Strands Agent SDK | Agent framework with `@tool` decorators |
| AWS CDK (TypeScript) | Infrastructure as code |
| React 18 + Cloudscape | Frontend UI framework |
| DynamoDB | Conversations, knowledge articles, preferences, capture state |
| CloudFront + S3 | Frontend hosting and pcap data storage |
| Cognito | User authentication and capture authorization |
| CodeBuild + ECR | Container image builds |
| VPC Traffic Mirroring | On-demand packet capture |
| Athena + Glue | Pcap data querying and cataloging |
| Step Functions | Pcap transformation workflow |

## Network Agent

The Network Agent is the sixth G.O.A.T. sub-agent, providing on-demand VPC packet capture and tshark/Athena-based pcap analysis through the same conversational interface used by the existing five domain agents.

### ENI Inventory

The `list_enis` action enumerates all Elastic Network Interfaces visible in the current AWS account and region. It returns each ENI's identifier, VPC, subnet, availability zone, private IP, status, attachment state, and attached instance ID.

**Optional filter parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `vpc_id` | string | Filter to ENIs in a specific VPC |
| `instance_id` | string | Filter to ENIs attached to a specific EC2 instance |
| `attachment_status` | `attached` \| `unattached` | Filter by attachment state |

Filters compose freely — supply any combination to narrow results. The action paginates the EC2 API exhaustively so no ENIs are truncated regardless of account size.

### Capture Lifecycle Actions

| Action | Purpose |
|--------|---------|
| `start_capture` | Creates VPC Traffic Mirror sessions on 1–3 ENIs for a specified duration (1–60 min), arms an auto-stop schedule, and returns a `capture_id` |
| `stop_capture` | Tears down mirror sessions, deletes the auto-stop schedule, and marks the capture as stopped |
| `list_captures` | Returns all capture session records filtered by status (`all`, `active`, or `historical`), ordered by start time descending |
| `transform_capture` | Triggers the Step Functions workflow that converts raw pcap files to Parquet and registers the Athena partition |
| `get_capture_progress` | Returns real-time progress including time remaining, S3 objects uploaded, and bytes captured |

### Pcap Query Actions

| Action | Purpose |
|--------|---------|
| `query_pcap` | Executes caller-supplied read-only SQL against the `pcap_logs` Athena table with automatic partition pruning |
| `search_fragmented_packets` | Finds packets exceeding a size threshold (default 1400 bytes), useful for detecting TLS Client Hello fragmentation |
| `correlate_tcp_streams` | Returns all packets in a TCP stream ordered by timestamp for full conversation reconstruction |
| `detect_retransmissions` | Groups TCP retransmissions by destination IP and port, ordered by count descending |
| `check_tls_hello_size` | Returns one row per TLS Client Hello with frame size, fragment count, and endpoint details |
| `get_conversation_stats` | Returns the top N conversations by total bytes with packet counts |
| `reconstruct_tcp_handshake` | Returns SYN/SYN-ACK/ACK frames with computed handshake outcome and duration |
| `classify_tcp_resets` | Classifies each TCP RST by origin side (client, server, middlebox, or unknown) |
| `detect_out_of_order_packets` | Reports out-of-order, duplicate ACK, DSACK, and fast retransmit counts per stream |
| `detect_zero_window` | Reports zero-window events, durations, and window-full/update counts per stream |
| `analyze_tcp_options` | Reports MSS, window scale, SACK, timestamps, and effective MSS per direction |
| `get_rtt_distribution` | Computes min/p50/p95/max RTT and sample count per stream |
| `get_request_response_latency` | Measures time-to-first-byte and full response time for request/response pairs |
| `diagnose_tcp_stream` | Produces a comprehensive TCP Stream Health Report combining all analysis actions |

### Bucket Strategy

The Network Agent stores raw pcap files under prefix `raw/` and transformed Parquet files under prefix `parquet/` in a single S3 bucket. At CDK synthesis time, the stack queries the CloudFormation export named `GOATSharedDataBucketName` from the existing `GOATData-${region}` stack. If the export is present and returns a non-empty string, the existing shared bucket is reused and no additional S3 bucket is provisioned. If the export is absent (not found or empty), a dedicated `GOATNetworkData-${region}` stack is instantiated to provision a new bucket. Lifecycle rules automatically delete `raw/` objects after 7 days and `parquet/` objects after 30 days to bound storage costs.

### Monthly Cost Estimate

Assumes 2 demo sessions per month with 3 captures per session (1 ENI, 15 minutes each) at the heuristic throughput of 1 Mbps per ENI.

| Item | Unit Assumption | Unit Price (USD) | Monthly Subtotal (USD) |
|------|----------------|-----------------|----------------------|
| EC2 Collector (t3.small, 24/7) | 730 hours | $0.0208/hour | $15.18 |
| Traffic Mirror (per-ENI-hour) | 6 captures × 1 ENI × 0.25 hr | $0.015/ENI-hour | $0.02 |
| Traffic Mirror (data) | 6 captures × 0.11 GB each | $0.015/GB | $0.01 |
| S3 Storage (raw, 7-day retention) | ~0.66 GB peak | $0.023/GB-month | $0.02 |
| S3 Storage (parquet, 30-day retention) | ~0.20 GB peak | $0.023/GB-month | $0.005 |
| Glue Crawler (per run) | 6 runs | $0.44/DPU-hour (min 1 min) | $0.04 |
| Athena (queries) | ~60 queries × 10 MB scanned | $5.00/TB scanned | $0.003 |
| AgentCore Runtime (Network Agent) | ~2 hours active × 2 sessions | ~$1-2/hour | $4.00 |
| **Total** | | | **~$19.28** |

> **Cost optimization tip:** Stop the EC2 collector instance between demo sessions to eliminate the $15.18/month EC2 cost. The collector is only needed while captures are active.

### Switching the Orchestration Model

The Orchestration Agent reads its foundation model identifier from the `ORCH_MODEL_ID` environment variable. When unset or empty, it defaults to `global.amazon.nova-pro-v1:0`.

To switch models, redeploy the OrchRuntimeStack with the `--orch-model-id` parameter:

```bash
# macOS / Linux
./deploy-all.sh --mode full --orch-model-id global.anthropic.claude-opus-4-7

# Windows (PowerShell)
.\deploy-all.ps1 -DeploymentMode full -OrchModelId global.anthropic.claude-opus-4-7
```

Any Amazon Bedrock-supported foundation model identifier is accepted — the deployment scripts do not restrict the value to a closed list. The IAM role grants `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` on all Bedrock foundation models and inference profiles.

**Example model identifiers:**

| Identifier | Description |
|------------|-------------|
| `global.amazon.nova-pro-v1:0` | Amazon Nova Pro (default) |
| `global.anthropic.claude-opus-4-7` | Anthropic Claude Opus 4 (global) |
| `us.anthropic.claude-opus-4-7` | Anthropic Claude Opus 4 (US inference profile) |
| `eu.anthropic.claude-opus-4-7` | Anthropic Claude Opus 4 (EU inference profile) |

For the full list of supported model identifiers, see the [Amazon Bedrock supported foundation models documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html).

### Targeting Flows by Hostname or IP

The `flow_selector` parameter lets you identify TCP flows by hostname, IP address, and/or port instead of requiring an internal stream identifier. Any Pcap Query Action that accepts targeting parameters accepts a `flow_selector`.

**Flow_Selector fields:**

| Field | Type | Description |
|-------|------|-------------|
| `source_ip` | IPv4 or IPv6 string | Source endpoint IP address |
| `source_hostname` | DNS hostname | Source endpoint hostname (resolved to IPs) |
| `source_port` | integer 0–65535 | Source port number |
| `destination_ip` | IPv4 or IPv6 string | Destination endpoint IP address |
| `destination_hostname` | DNS hostname | Destination endpoint hostname (resolved to IPs) |
| `destination_port` | integer 0–65535 | Destination port number |
| `stream_id` | string 1–64 chars `[A-Za-z0-9_-]` | TCP stream identifier |

At least one field must be supplied. All supplied fields are combined with logical AND.

**Hostname Resolution Strategy:**

When a hostname is supplied, the agent resolves it to IP addresses using the `combined` strategy in this order:

1. **`dns_in_capture`** — Extract A/AAAA answers from DNS responses observed in the same capture
2. **`tls_sni_in_capture`** — Match TLS Client Hello SNI values in the capture to their TCP destination IPs
3. **`active_dns_lookup`** — Perform a runtime DNS lookup from the agent (5-second per-hostname timeout, 15-second overall budget)

All resolved IPs are unioned into the Athena predicate. The response includes `metadata.resolved_flow_set` showing exactly which IPs were used.

**Role-inference rules:**

The Orchestration Agent infers source vs. destination from natural language:
- Words like "from", "source", "client", "originating from" → populates `source_hostname` or `source_ip`
- Words like "to", "destination", "server", "reaching" → populates `destination_hostname` or `destination_ip`
- "port" or "on port" → populates `destination_port` (unless qualified by "source port")
- When only source fields are supplied, constraints match either direction of each flow
- When only destination fields are supplied, constraints match only the responder side

**Example transcript — hostname-only selector:**

```
User: Diagnose the flow to ecr.eu-west-3.amazonaws.com in capture abc123

Agent: Resolved ecr.eu-west-3.amazonaws.com → [52.95.150.1, 52.95.150.2]
       (strategy: active_dns_lookup)
       Resolved 52.95.150.1:443 → 52.95.150.2:443 across 3 stream(s)

       TCP Stream Health Report for stream tcp-0042:
       • Handshake: complete (12 ms)
       • Connection close: RST observed (origin: middlebox)
       • RTT: min=11ms, p50=14ms, p95=22ms, max=45ms
       • Retransmissions: 0
       • Anomalies: connection_reset_by_middlebox, tls_client_hello_fragmented
       ...
```

**Example transcript — source IP + destination port:**

```
User: Find resets from 10.0.1.5 to port 443 in capture xyz789

Agent: Resolved 10.0.1.5 → source (either direction)
       Resolved port 443 → destination_port
       Found 2 TCP RST packets across 1 stream:

       | Time | Stream | Source | Dest | Origin | Preceded by FIN |
       |------|--------|--------|------|--------|-----------------|
       | 12:34:56.789 | tcp-0018 | 10.0.1.5:49832 | 52.95.150.1:443 | middlebox | false |
       | 12:34:57.012 | tcp-0018 | 10.0.1.5:49832 | 52.95.150.1:443 | middlebox | false |
```

### TCP Exchange Diagnosis

The `diagnose_tcp_stream` action produces a comprehensive **Tcp_Stream_Health_Report** combining all lower-level analysis queries into a single structured result.

**Tcp_Stream_Health_Report keys:**

| Key | Type | Description |
|-----|------|-------------|
| `stream_id` | string | TCP stream identifier |
| `client_endpoint` | `{ip, port}` | Client (SYN initiator) endpoint |
| `server_endpoint` | `{ip, port}` | Server (SYN-ACK responder) endpoint |
| `handshake` | `{complete, duration_ms, failure_reason}` | TCP handshake outcome |
| `connection_close` | `{state, reset_origin_side}` | How the connection ended |
| `rtt` | `{min_ms, p50_ms, p95_ms, max_ms, sample_count}` | Round-trip time distribution |
| `retransmissions` | `{total_count, fast_retransmit_count, spurious_count, sack_retransmit_count}` | Retransmission breakdown |
| `out_of_order` | `{out_of_order_count, duplicate_ack_count, dsack_count}` | Packet ordering issues |
| `zero_window` | `{event_count, total_duration_ms}` | Receiver buffer exhaustion events |
| `tcp_options` | `{mss_advertised, window_scale, sack_permitted, timestamps_enabled, mss_effective_min}` | Negotiated TCP options |
| `mss_clamping_mismatch` | boolean | True when `mss_effective_min < 80% of mss_advertised` |
| `anomalies` | array of `{category, description}` | Detected anomalies with classification |

**Tcp_Anomaly_Category enumeration:**

| Category | Trigger Rule |
|----------|-------------|
| `handshake_failed` | `handshake.complete` is false |
| `handshake_slow` | `handshake.duration_ms` exceeds 500 ms |
| `connection_reset_by_client` | RST origin side is `client` |
| `connection_reset_by_server` | RST origin side is `server` |
| `connection_reset_by_middlebox` | RST origin side is `middlebox` |
| `idle_timeout_close` | `connection_close.state` is `idle_timeout` |
| `excessive_retransmissions` | `retransmissions.total_count` exceeds 5% of total packets |
| `spurious_retransmissions` | `retransmissions.spurious_count` > 0 |
| `out_of_order_packets` | `out_of_order.out_of_order_count` exceeds 1% of total packets |
| `duplicate_acks` | `out_of_order.duplicate_ack_count` exceeds 5 |
| `zero_window_stall` | `zero_window.total_duration_ms` exceeds 100 ms |
| `mss_clamping_mismatch` | `mss_effective_min < 80% of mss_advertised` |
| `tls_client_hello_fragmented` | At least one TLS Client Hello has fragment count > 1 |
| `none` | No other rule matches |

**Example chat transcript:**

```
User: Diagnose TCP stream tcp-0042 from capture cap_tls_demo

Agent: TCP Stream Health Report for `tcp-0042`:

       **Handshake**
       • Complete: yes (12 ms)
       • No handshake anomalies

       **Connection Close**
       • State: rst_observed
       • Reset origin: middlebox
       • The RST source IP/port matches neither stream endpoint — likely a
         network firewall or NAT device terminated the connection

       **RTT**
       • min=11ms, p50=14ms, p95=22ms, max=45ms (128 samples)
       • RTT is healthy for an intra-region flow

       **Retransmissions**
       • Total: 2, Fast retransmit: 1, Spurious: 0, SACK: 1
       • Below the 5% threshold — not a concern

       **Out-of-Order**
       • Out-of-order: 0, Duplicate ACKs: 3, DSACK: 0
       • Within normal range

       **Zero Window**
       • Events: 0, Duration: 0 ms
       • No receiver buffer pressure

       **TCP Options**
       • MSS advertised: 1460, Window scale: 7, SACK: yes, Timestamps: yes
       • MSS effective min: 1380 (94.5% of advertised — no clamping mismatch)

       **Anomalies**
       • connection_reset_by_middlebox: RST originated from a middlebox
         (source IP 10.0.0.1 is neither client 10.0.1.5 nor server 52.95.150.1)
       • tls_client_hello_fragmented: TLS Client Hello split across 3 fragments
         (3,547 bytes total — exceeds typical 1,400-byte threshold)

       Root cause: The TLS Client Hello (3.5 KB due to ML-KEM key share) was
       fragmented across multiple TCP segments. The Network Firewall uses a
       legacy "drop established" rule that inspects SNI only in the first
       record — when SNI spans fragments, the firewall cannot match the domain
       and drops the connection with a RST.
```

### Support-Case-Driven Investigation

The Orchestration Agent can drive a multi-domain investigation starting from a support case identifier. It extracts context from the case body and automatically invokes Health, Network, and Trusted Advisor agents using that context.

**Support_Case_Context fields:**

| Field | Type | Description |
|-------|------|-------------|
| `case_id` | string | AWS Support case identifier |
| `account_id` | string | Affected AWS account |
| `affected_hostnames` | list of strings | DNS hostnames mentioned in the case |
| `affected_ips` | list of strings | IPv4/IPv6 addresses mentioned in the case |
| `affected_ports` | list of integers | Port numbers mentioned in the case |
| `affected_services` | list of strings | AWS service names mentioned |
| `affected_regions` | list of strings | AWS region names mentioned |
| `incident_window_start` | ISO 8601 timestamp | Start of the incident time window |
| `incident_window_end` | ISO 8601 timestamp | End of the incident time window |
| `error_signatures` | list of strings | Distinct error messages extracted |
| `severity` | string | Case severity level |

**Workflow:**

1. User supplies a support case identifier (e.g., `case-123456789012-2024-000001`)
2. Orchestration Agent invokes the Support Agent to retrieve the case body and communications
3. The model extracts a `Support_Case_Context` from the case content
4. If `affected_hostnames` or `affected_ips` are found, a `flow_selector` is constructed automatically
5. Health Agent is invoked with the incident time window and affected services/regions
6. If a `capture_id` is available, `diagnose_tcp_stream` is invoked with the case-derived flow selector
7. Trusted Advisor results are filtered to relevant categories and affected resources
8. A structured response is produced with four sections: Case summary, Health correlation, Network analysis, and Recommended next actions

**Prompt template:** "Investigate the network problem described in support case `{case_id}` and capture `{capture_id}` if relevant"

**Support plan and access constraints:**
- The Support Agent requires an AWS Business, Enterprise On-Ramp, or Enterprise Support plan
- If the case identifier does not exist or is not accessible, the agent responds with the access constraint and does not proceed with Health or Network investigations
- If the user lacks the required Support plan, the agent offers to proceed using only user-supplied endpoints (skipping case extraction)

**Example chat transcript:**

```
User: Investigate the network problem described in support case
      case-123456789012-2024-000001 and capture cap_ecr_issue

Agent: **Case Summary**
       Case case-123456789012-2024-000001 reports intermittent ECR pull
       failures from EKS pods in eu-west-1 starting 2026-04-18T14:00Z.
       Error signature: "connection reset by peer" when pulling container
       images from 123456789012.dkr.ecr.eu-west-1.amazonaws.com.

       **Health Correlation**
       • [Health] Amazon Linux 2023 update AL2023-2026-004 (2026-04-17):
         OpenSSL upgraded to 3.5.5 with ML-KEM (Kyber-768) key share
         enabled by default — increases TLS Client Hello to ~3.5 KB

       **Network Analysis**
       • [Network] diagnose_tcp_stream for flow to
         123456789012.dkr.ecr.eu-west-1.amazonaws.com:443:
         - Anomaly: tls_client_hello_fragmented (3,547 bytes, 3 fragments)
         - Anomaly: connection_reset_by_middlebox (RST from 10.0.0.1,
           matches Network Firewall ENI)
         - Handshake: failed (failure_reason: syn_ack_missing after RST)

       **Recommended Next Actions**
       • Update the Network Firewall stateful rule group from the legacy
         "drop established" default action to "aws:drop_established_app_layer"
         which reassembles multi-packet TLS Client Hello messages before rule evaluation
       • Alternatively, add a `pass` rule for the ECR endpoint that does
         not rely on SNI inspection
       • Verify the fix by re-running the capture after the rule change
       • Consider disabling ML-KEM temporarily via OpenSSL config if an
         immediate workaround is needed before the firewall rule update
```

### TLS Fragmentation Reproduction Scenario

**Purpose:**
Reproduces the AWS Network Firewall + Amazon Linux 2023 OpenSSL 3.5.5 ML-KEM TLS Client Hello fragmentation failure mode, demonstrating how the Network Agent detects and correlates the issue with Health events and Support case data.

**Prerequisites:**
- G.O.A.T. deployed with the Network Agent module (`--mode network` or `--mode full`)
- Network capture authorization (user in `GOATNetworkCaptureUsers` group)
- EKS cluster access permissions for the scenario VPC
- At least one ENI tagged with `goat-network-capture-allowed=true` in the scenario VPC

**Expected Agent Correlation:**
- **Health domain:** Amazon Linux 2023 update event (AL2023-2026-004) showing OpenSSL 3.5.5 upgrade with ML-KEM enabled by default
- **Network domain:** TLS Client Hello fragmentation detected — `check_tls_hello_size` returns frames exceeding 1400 bytes with fragment count > 1; `classify_tcp_resets` shows middlebox-originated RSTs
- **Support domain:** Known issue identifier linking the AL2023 OpenSSL update to Network Firewall SNI inspection failures with the legacy `drop established` configuration

**Suggested Demo Queries:**

```
Capture traffic from the EKS test pod and explain why ECR connections fail
```

```
Why does my EKS pod fail to reach ECR?
```

```
Our instance in goat-demo-vpc is failing to establish HTTPS connections to ECR (endpoint: ecr.us-east-1.amazonaws.com on port 443). The connexion is going through the TGW and the NFW in goat-demo-tls-inspection-vpc but it is dropped.
```

**Expected Combined Output:**
- The agent identifies the AL2023 OpenSSL update as the triggering change
- Packet capture shows TLS Client Hello messages of ~3.5 KB split across multiple TCP segments
- The Network Firewall's legacy `drop established` rule fails to extract SNI from fragmented records
- RST packets are classified as originating from a middlebox (the Network Firewall)
- The agent recommends switching to `aws:drop_established_app_layer` which reassembles multi-packet TLS Client Hello messages before rule evaluation
- The agent links the finding to the known Support issue for this configuration
- A complete root-cause chain is presented: AL2023 update → ML-KEM key share → oversized Client Hello → fragmentation → NFW SNI inspection failure → connection reset

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
