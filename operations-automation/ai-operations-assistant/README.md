# GenAI Operations Analytics Tool (G.O.A.T.)

Unified conversational interface for querying AWS operational data across Cost Explorer, Health Dashboard, Support Cases, Trusted Advisor, and Cost & Usage Reports — powered by Amazon Bedrock AgentCore and Amazon Nova models.

## Overview

Operations teams juggle multiple AWS consoles to monitor costs, health events, support cases, and optimization recommendations. G.O.A.T. solves this by providing a single conversational interface where natural language questions are routed to specialized AI agents that query the right AWS services and return correlated, formatted results.

The solution uses a **hybrid multi-agent architecture**:
- An **Orchestration Agent** (Strands Agent SDK + Amazon Nova Pro) handles intent classification, sub-agent coordination, cross-domain correlation, and natural language response generation
- Five **Sub-Agents** (plain Python handlers on AgentCore) each handle a specific operational domain — Cost, Health, Support, Trusted Advisor, and CUR — calling AWS APIs directly via boto3

A React + Cloudscape frontend provides streaming chat, prompt templates, knowledge management, conversation history, and data visualization.

## At a Glance

| | |
|---|---|
| **Duration** | 25-30 minutes (full deployment) |
| **Difficulty** | Intermediate |
| **Target Audience** | DevOps Engineers, SREs, FinOps Engineers, Cloud Architects |
| **Key Technologies** | Amazon Bedrock AgentCore, Amazon Nova Pro/Lite, Strands Agent SDK, AWS CDK (TypeScript), React + Cloudscape |
| **Estimated Cost** | ~$15-40/month for occasional use (see breakdown below) |

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

**Step 3: Configure the CUR agent environment variables**

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
| Bedrock AgentCore (6 runtimes) | $2-8 | ~2 hours active runtime × 2 sessions; tear down between demos |
| Bedrock Nova Pro (orchestration inference) | $1-3 | ~20-40 queries per session at ~$0.03-0.08 each |
| Bedrock Nova Lite (sub-agent inference) | $0.50-1 | Lightweight per-query cost, minimal at low volume |
| DynamoDB (3 tables, on-demand) | $0-1 | Near-zero at low read/write volume |
| S3 (source buckets + frontend) | $1-2 | Storage persists between sessions |
| CloudFront (frontend distribution) | $0-1 | Minimal traffic for 2 sessions |
| ECR (6 container images) | $1-3 | ~500MB per image, stored between sessions |
| CodeBuild (container builds) | $2-8 | Only runs during deploy; ~5 min per agent × 6 agents × 2 deploys |
| Cognito (user authentication) | $0 | Free tier covers < 50,000 MAUs |
| **Total** | **~$8-27/month** | |

### Per-Query Cost
- Single-domain query: ~$0.01-0.03
- Cross-domain query (2-3 agents): ~$0.03-0.08

### Always-On vs. Tear-Down Comparison

| Scenario | Monthly Cost |
|----------|-------------|
| 2 demo sessions, tear down between | ~$8-27 |
| Runtimes running 24/7 (continuous) | ~$110-240 |

### Cost Optimization Tips
- Tear down AgentCore runtimes after each demo session (`cdk destroy` the RuntimeStacks)
- Deploy only the modules you need (individual module deployment)
- Use Nova Lite for sub-agents (already configured — lower cost than Pro)
- DynamoDB on-demand pricing scales to zero when idle
- Keep ECR images and InfraStacks deployed between sessions to speed up re-deployment (only RuntimeStacks need re-deploy)

## Deployment

G.O.A.T. supports three deployment modes to match your needs.

### Full Deployment (All Modules)

Deploys all 5 sub-agents, the orchestration agent, and the frontend.

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
```

**Windows (PowerShell):**
```powershell
.\deploy-all.ps1 -DeploymentMode cost
.\deploy-all.ps1 -DeploymentMode health
.\deploy-all.ps1 -DeploymentMode support
.\deploy-all.ps1 -DeploymentMode trusted-advisor
.\deploy-all.ps1 -DeploymentMode cur
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

## Demo Scenarios

G.O.A.T. includes pre-built demo scenarios with provisioning scripts that create controlled sets of AWS resources, generating data across all five agent domains. This lets you demonstrate cross-domain correlation with real operational data.

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

**Expected agent correlation:** The orchestration agent correlates the Support case referencing monitoring gaps with the real CloudWatch planned lifecycle event from April 1, 2026 visible in the Health API.

> **Note:** Scenario B depends on the real CloudWatch health event from April 1, 2026 being visible in the AWS Health API history. If the event has aged out of the Health API retention window, the Health agent will not return it — but the Support case still demonstrates cross-domain querying. Scenario B creates no billable resources.

### Cleanup

Remove all demo resources from both scenarios with a single command:

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

The cleanup script finds all resources tagged with `goat-demo=true` and removes them in dependency order. Scripts are idempotent — safe to re-run.

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
└───┬──────────┬──────────┬──────────┬──────────┬─────────────────────┘
    │          │          │          │          │
    ▼          ▼          ▼          ▼          ▼
┌────────┐┌────────┐┌────────┐┌────────┐┌────────┐
│  Cost  ││ Health ││Support ││  T.A.  ││  CUR   │
│ Agent  ││ Agent  ││ Agent  ││ Agent  ││ Agent  │
│(boto3) ││(boto3) ││(boto3) ││(boto3) ││(boto3) │
└───┬────┘└───┬────┘└───┬────┘└───┬────┘└───┬────┘
    │         │         │         │         │
    ▼         ▼         ▼         ▼         ▼
  Cost      Health   Support   Trusted   Athena
 Explorer  Dashboard  API     Advisor   (CUR Data)
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
│   ├── orchestration-agent/           # Strands Agent SDK orchestrator
│   └── shared/                        # Shared utilities (aws_utils.py)
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
| DynamoDB | Conversations, knowledge articles, preferences |
| CloudFront + S3 | Frontend hosting |
| Cognito | User authentication |
| CodeBuild + ECR | Container image builds |

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
