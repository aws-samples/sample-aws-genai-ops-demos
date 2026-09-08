# Proactive AWS Health Event Impact Analyzer

## Use AWS DevOps Agent to triage and route AWS Health event impact

> **This is a sample application** demonstrating how to build automated AWS Health event impact assessment and multi-team notification routing using AWS DevOps Agent. Use it as a reference architecture or starting point for your own implementation.

## Overview

When AWS Health publishes an event — scheduled maintenance, operational issues, or service degradation — this sample solution automatically triggers an AI-powered investigation using AWS DevOps Agent. The agent analyzes your application topology to determine blast radius, identifies affected teams from resource tags, and routes notifications through team-specific channels (email, Slack, MS Teams).

## At a Glance

| | |
|---|---|
| **Duration** | 20 minutes (deployment) |
| **Difficulty** | Intermediate |
| **Target Audience** | SREs, Platform Engineers, DevOps Engineers |
| **Key Technologies** | AWS DevOps Agent, Step Functions, EventBridge, Lambda, DynamoDB, SNS, Secrets Manager, Systems Manager OpsCenter |
| **Estimated Cost** | ~$5-15/month (varies with event volume) |

## Architecture

```
AWS Health → EventBridge → Event Router (Lambda)
                                    ↓
                            Step Functions
                                    ↓
                    Investigation Trigger (Lambda) → DevOps Agent (webhook)
                                                          ↓
                    Investigation Callback (Lambda) ← EventBridge (aws.aidevops)
                                    ↓
                            Has Findings?
                           /            \
                         YES             NO → Skip
                          ↓
                  OpsCenter Creator (Lambda)
                          ↓
                    Notifier (Lambda)
                    /       |        \         \        \
               Email     Slack    MS Teams   Jira    Default Routing
             (per team) (per team) (per team) (MCP)  (if no teams)
                                                          ↓
                                                    AWS Account API
                                                    (alternate contacts)
```

See [ARCHITECTURE.md](./ARCHITECTURE.md) for detailed component descriptions and data flow.

## Prerequisites

- Deploy in a Region where AWS DevOps Agent is generally available. The setup
  probes the DevOps Agent API in your resolved Region, but the API can respond
  in Regions where the service is not yet fully launched (no console support).
  **As of today, deploy only to a Region listed in the official
  [AWS DevOps Agent supported Regions](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-supported-regions.html)**
  to get full functionality including the DevOps Agent console.
- AWS account with permissions to create IAM roles, Lambda, Step Functions, DynamoDB, SNS, SSM, Secrets Manager, and AWS DevOps Agent (`AWS::DevOpsAgent::*`) resources
- AWS CLI v2.34.20+ installed and authenticated (`aws sts get-caller-identity` should work)
- Node.js 24+ and npm installed (Lambda functions run on Node.js 24)
- An active [CloudTrail trail](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-create-a-trail-using-the-console-first-time.html) capturing management events in the deployment region

### Regions

By default everything — the CDK stack *and* the DevOps Agent Space — deploys to your **current region**, resolved from your AWS config. Nothing to set.

To place the Agent Space in a different region, set `DEVOPS_AGENT_REGION` before deploying:

```powershell
$env:DEVOPS_AGENT_REGION = "eu-west-1"   # Agent Space here; stack stays in your current region
```

```bash
export DEVOPS_AGENT_REGION=eu-west-1
```

An Agent Space monitors resources across *all* regions of an associated account, so it does not need to sit with your stack. See [shared/README.md](../../shared/README.md#aws-devops-agent-region) for the full convention.

> **⚠️ Splitting the regions breaks the callback step.** `aws.aidevops` publishes investigation-completion events in the Agent Space region, but the EventBridge rule that resumes Step Functions lives in the deploy region — and EventBridge does not cross regions on its own. Keep both the same unless you have set up cross-region event forwarding. The wizard warns you when it detects a split.

The [setup wizard](#deployment) handles everything else automatically via CDK
(`DevOpsAgentSpaceStack` — see [ARCHITECTURE.md](./ARCHITECTURE.md#aws-devops-agent-provisioning)):
- Creates the DevOps Agent Space and configures topology discovery
- Creates IAM roles with correct trust policies
- Generates the webhook for triggering investigations (HMAC secret stored directly in Secrets Manager)
- Bootstraps and deploys the CDK stacks

## Deployment

### Setup Wizard (Recommended)

The interactive setup wizard guides you through the entire deployment process:

```bash
npx ts-node scripts/setup-wizard.ts
```

The wizard will:
1. Prompt for target AWS region (always first)
2. Check prerequisites (AWS CLI v2.34.20+, CDK, credentials)
3. Configure notification channels (email, Slack, MS Teams) — optional
4. Deploy the DevOps Agent Space stack (Agent Space, IAM roles, operator app,
   AWS account association, and the eventChannel webhook — see
   [ARCHITECTURE.md](./ARCHITECTURE.md#aws-devops-agent-provisioning)), then
   read back its outputs and deploy the main stack with
   `--require-approval broadening`
5. (Optional) Register the Atlassian Jira MCP server and associate it with
   the now-deployed Agent Space

Steps 3–5 handle notification and Jira secrets: Slack/MS Teams webhook URLs go
to SSM Parameter Store SecureString; the DevOps Agent webhook secret is
written directly to Secrets Manager by CDK and never passes through this
script.

### Upgrading from an Older, CLI-Based Deployment

Earlier versions of this sample created the Agent Space, its IAM roles, the
account association, and the webhook with direct `aws devops-agent` CLI calls
from the wizard. If you deployed with one of those versions, re-running the
current wizard **creates a brand-new Agent Space** (CDK-managed) rather than
adopting your existing one — `AWS::DevOpsAgent::AgentSpace` has no import
mechanism, and the DevOps Agent API allows duplicate names, so the deploy
won't fail, it'll just leave you with two Agent Spaces both named
`health-event-analyzer`.

After confirming the new stack works (test with a sample event — see
[Testing](#testing) below), clean up the old, now-unused resources:
- The old Agent Space (`aws devops-agent list-agent-spaces --region <region>`
  to find its ID, then disassociate its services and delete it)
- The old fixed-name IAM roles `DevOpsAgentRole-AgentSpace` and
  `DevOpsAgentRole-WebappAdmin` (the new roles are project-prefixed —
  `health-event-analyzer-AgentSpaceRole` / `-OperatorRole` — so there's no
  naming conflict, but the old ones are now orphaned)

### Cleanup

To remove all resources created by the setup wizard:

```bash
npx ts-node scripts/cleanup.ts
```

### Manual CDK Deployment

> **Note**: The setup wizard is the recommended deployment path. Manual deployment
> means deploying two stacks in sequence and threading the first one's outputs
> into the second's context yourself.

The app now has two stacks: `HealthEventAnalyzerAgentSpace-<region>` (the
DevOps Agent Space, IAM roles, operator app, account association, and
webhook — see [ARCHITECTURE.md](./ARCHITECTURE.md#aws-devops-agent-provisioning))
and `HealthEventAnalyzerStack-<region>` (everything else). Deploy the first,
read its outputs, then deploy the second with those outputs as context:

```bash
cd infrastructure/cdk
npm install
npm run bundle   # compiles the TypeScript Lambda handlers into dist/lambda/

# 1. Deploy the DevOps Agent Space stack first.
#    Add -c devOpsAgentRegion=eu-west-1 only if the Agent Space should live in
#    a different Region than the main stack.
npx cdk deploy HealthEventAnalyzerAgentSpace-$AWS_REGION \
  --no-cli-pager --require-approval broadening

# 2. Read back its outputs. The webhook HMAC secret is never printed — only
#    its Secrets Manager ARN is (the custom resource wrote the value directly
#    into that secret during step 1).
WEBHOOK_URL=$(aws cloudformation describe-stacks \
  --stack-name HealthEventAnalyzerAgentSpace-$AWS_REGION \
  --query "Stacks[0].Outputs[?OutputKey=='WebhookUrl'].OutputValue" --output text)
WEBHOOK_SECRET_ARN=$(aws cloudformation describe-stacks \
  --stack-name HealthEventAnalyzerAgentSpace-$AWS_REGION \
  --query "Stacks[0].Outputs[?OutputKey=='WebhookSecretArn'].OutputValue" --output text)

# 3. Deploy the main stack, passing those outputs as CDK context (there is no
#    CloudFormation parameter for either value).
npx cdk deploy HealthEventAnalyzerStack-$AWS_REGION \
  -c devOpsAgentWebhookUrl=$WEBHOOK_URL \
  -c devOpsAgentWebhookSecretArn=$WEBHOOK_SECRET_ARN \
  --no-cli-pager --require-approval broadening

# Only if the Agent Space is in a different region than the main stack:
#   -c devOpsAgentRegion=eu-west-1  (on both the step-1 and step-3 commands)
```

> **Note**: `npm run bundle` is required before any manual `cdk synth`/`cdk deploy`. The
> CDK stacks reference pre-built Lambda artifacts in `dist/lambda/`, so synthesis fails
> with a missing-asset error if you skip it. The setup wizard and `deploy-all` scripts
> run this step automatically, and `npm test` / `npm run build` run it via npm pre-hooks.

The Slack/MS Teams webhook URLs are still stored in **SSM Parameter Store
SecureString** — not passed as CloudFormation parameters. For manual
deployment, create them before deploying the main stack (unrelated to the
DevOps Agent webhook secret above, which CDK provisions directly):

```bash
aws ssm put-parameter --name "/health-analyzer/production/slack-webhook-url" \
  --type SecureString --value "https://hooks.slack.com/..."
aws ssm put-parameter --name "/health-analyzer/production/msteams-webhook-url" \
  --type SecureString --value "https://..."
```

## Configuration

### Team Notification Routing

After deployment, seed the teams table with your team configurations:

```bash
# Bash
./scripts/seed-teams.sh health-analyzer-teams

# PowerShell (Windows)
.\scripts\seed-teams.ps1 -TableName health-analyzer-teams
```

Each team entry supports:
- `teamId` — unique identifier (matches resource tag values)
- `email` — team email for SNS notifications
- `slackWebhookUrl` — team-specific Slack incoming webhook or workflow trigger
- `msTeamsWebhookUrl` — team-specific Microsoft Teams webhook
- `notifyOn` — severity levels that trigger notification (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`)

### Default Routing (No Configuration Required)

If no team routing is configured, the system automatically falls back to **default routing**:

- Fetches **alternate contacts** (Operations, Security, Billing) from the AWS Account API
- Sends notifications to each alternate contact with a valid email address
- Also sends to the default SNS topic and Slack webhook (if configured during deployment)

This ensures notifications are always delivered even in a fresh deployment with no team configuration.

### DevOps Agent Custom Skill

Upload the skill definition from `devops-agent-skill/SKILL.md` to your DevOps Agent Space. This teaches the agent the structured methodology for Health event impact assessment.

See [docs/configuration-guide.md](./docs/configuration-guide.md) for complete setup instructions.

### Jira Integration

The DevOps Agent can auto-file Jira tickets for confirmed-impact Health events using the [Atlassian Rovo MCP Server](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/getting-started-with-the-atlassian-remote-mcp-server/). The setup wizard can configure this automatically.

See [docs/jira-integration.md](./docs/jira-integration.md) for the complete setup guide.

### Multi-Account Organizations

For multi-account setups with AWS Health organizational view, this sample supports hybrid agent space routing with shared and per-account override patterns.

See [docs/multi-account-setup.md](./docs/multi-account-setup.md) for the full multi-account deployment guide.

### Notification Channels

- [Slack Integration Guide](./docs/slack-integration.md) — Incoming Webhooks and Workflow Triggers
- [MS Teams Integration Guide](./docs/msteams-integration.md) — Adaptive Cards via Workflows webhook

## How It Works

1. **Event Capture**: EventBridge rules capture AWS Health events (maintenance, issues, abuse)
2. **Workflow Start**: Event Router Lambda normalizes the event and starts a Step Functions execution
3. **Investigation**: The workflow triggers DevOps Agent via HMAC-authenticated webhook, then waits for results using the Task Token pattern
4. **AI Analysis**: DevOps Agent queries its topology to find affected workloads, traces dependency chains, checks redundancy, and identifies owning teams
5. **Callback**: When the investigation completes, an EventBridge event triggers the Callback Lambda which resumes Step Functions
6. **OpsItem Creation**: If impact is detected, an OpsItem is created in AWS Systems Manager OpsCenter with severity, findings, and investigation link
7. **Notification**: The Notifier Lambda routes alerts to each affected team through their preferred channels, including a link to the OpsItem

## Estimated Cost

| Service | Monthly Cost | Notes |
|---------|-------------|-------|
| Lambda | ~$1-3 | 5 functions invoked per Health event, plus a 6th (webhook provisioner) that only runs once per deploy/destroy — negligible cost |
| Step Functions | ~$1 | Standard workflow, ~100 executions/month |
| DynamoDB | ~$1 | On-demand, three tables with minimal storage |
| EventBridge | ~$0.50 | Rule evaluations |
| SNS | ~$0.50 | Email notifications |
| Secrets Manager | ~$0.40 | One secret for the DevOps Agent webhook HMAC key |
| Systems Manager OpsCenter | ~$0 | Free tier covers typical usage |
| DevOps Agent | Included | Part of AWS DevOps Agent pricing |
| **Total** | **~$5-15/month** | Varies with Health event volume |

Cost optimization: All resources use on-demand/pay-per-request pricing. No idle costs when no Health events occur.

## Project Structure

```
├── infrastructure/cdk/          # AWS CDK infrastructure (TypeScript)
│   ├── bin/app.ts              # CDK app entry point (both stacks)
│   ├── lib/
│   │   ├── health-event-analyzer-stack.ts   # Main stack
│   │   ├── devops-agent-space-stack.ts      # DevOps Agent Space stack
│   │   └── constructs/
│   │       ├── devops-agent-space.ts        # Agent Space, IAM roles, operator
│   │       │                                # app, account association, webhook
│   │       └── ...                          # other stack constructs
│   ├── scripts/
│   │   └── bundle-lambdas.js   # esbuild pre-compile → dist/lambda/<name>/index.js
│   └── lambda/                 # Lambda function source code
│       ├── event-router/       # Normalizes Health events → starts workflow
│       ├── investigation-trigger/  # HMAC webhook to DevOps Agent
│       ├── investigation-callback/ # Handles agent completion
│       ├── opscenter-creator/  # Creates OpsItem in Systems Manager OpsCenter
│       ├── notifier/           # Routes notifications to teams (incl. default
│       │                       # routing via AWS Account alternate contacts)
│       └── devops-agent-webhook-provisioner/  # Custom resource: provisions the
│                                               # eventChannel webhook (see
│                                               # ARCHITECTURE.md)
├── devops-agent-skill/         # DevOps Agent custom skill definition
├── scripts/                    # Setup wizard and utility scripts
├── events/                     # Sample events for testing
├── test/                       # CDK infrastructure tests
└── docs/                       # Integration and configuration guides
```

## Testing

### Unit Tests

```bash
cd infrastructure/cdk
npm test
```

### End-to-End Testing

Since EventBridge doesn't allow injecting events with source `aws.health` (reserved), you test by invoking the Event Router Lambda directly with sample events:

```bash
# Find your Event Router Lambda name
EVENT_ROUTER=$(aws lambda list-functions --region $AWS_REGION --no-cli-pager \
  --query "Functions[?contains(FunctionName,'EventRoute')].FunctionName" --output text)

# Inject a test Health event
aws lambda invoke \
  --function-name "$EVENT_ROUTER" \
  --payload file://events/test-lambda-deprecation-event.json \
  --cli-binary-format raw-in-base64-out \
  --region $AWS_REGION \
  --no-cli-pager \
  /tmp/test-response.json && cat /tmp/test-response.json
```

This triggers the full flow: Event Router → Step Functions → DevOps Agent → Callback → OpsItem → Notifications.

### Available Test Events

| File | Scenario |
|------|----------|
| `events/sample-health-event.json` | EC2 scheduled maintenance |
| `events/test-invoke-event.json` | Minimal EC2 scheduled maintenance (quick smoke test) |
| `events/test-lambda-deprecation-event.json` | Lambda runtime EOL |
| `events/test-lambda-nodejs20-lifecycle-event.json` | Lambda Node.js 20 planned lifecycle event |
| `events/test-sfn-deprecation-event.json` | Step Functions deprecation |
| `events/test-iam-admin-deprecation-event.json` | IAM admin role enforcement |
| `events/test-security-event.json` | IAM overly permissive policies |
| `events/test-lambda-throttle-event.json` | Lambda throttling |
| `events/test-stepfunctions-issue-event.json` | Step Functions API errors |
| `events/test-rds-ca-expiry-event.json` | RDS CA certificate expiry |
| `events/test-opscenter-payload.json` | Direct OpsCenter Creator Lambda payload (not an Event Router event — invoke that function directly to test OpsItem creation in isolation) |

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
