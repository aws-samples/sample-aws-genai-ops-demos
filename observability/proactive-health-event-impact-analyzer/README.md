# Use AWS DevOps Agent to triage and route AWS Health event impact

> **This is a sample application** demonstrating how to build automated AWS Health event impact assessment and multi-team notification routing using AWS DevOps Agent. Use it as a reference architecture or starting point for your own implementation.

## Overview

When AWS Health publishes an event — scheduled maintenance, operational issues, or service degradation — this sample solution automatically triggers an AI-powered investigation using AWS DevOps Agent. The agent analyzes your application topology to determine blast radius, identifies affected teams from resource tags, and routes notifications through team-specific channels (email, Slack, MS Teams).

## At a Glance

| | |
|---|---|
| **Duration** | 20 minutes (deployment) |
| **Difficulty** | Intermediate |
| **Target Audience** | SREs, Platform Engineers, DevOps Engineers |
| **Key Technologies** | AWS DevOps Agent, Step Functions, EventBridge, Lambda, DynamoDB, SNS, Systems Manager OpsCenter |
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

- AWS account with permissions to create IAM roles, Lambda, Step Functions, DynamoDB, SNS, and SSM resources
- AWS CLI v2.34.20+ installed and authenticated (`aws sts get-caller-identity` should work)
- Node.js 24+ and npm installed (Lambda functions run on Node.js 24)
- An active [CloudTrail trail](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-create-a-trail-using-the-console-first-time.html) capturing management events in the deployment region

The [setup wizard](#deployment) handles everything else automatically:
- Creates the DevOps Agent Space and configures topology discovery
- Creates IAM roles with correct trust policies
- Generates the webhook for triggering investigations
- Bootstraps and deploys the CDK stack

## Deployment

### Setup Wizard (Recommended)

The interactive setup wizard guides you through the entire deployment process:

```bash
npx ts-node scripts/setup-wizard.ts
```

The wizard will:
1. Prompt for target AWS region (always first)
2. Check prerequisites (AWS CLI v2.34.20+, CDK, credentials)
3. Create or select a DevOps Agent Space
4. Create IAM roles (if needed)
5. Associate your AWS account for topology discovery
6. Generate a webhook for triggering investigations
7. Enable the operator app
8. (Optional) Register the Atlassian Jira MCP server and associate it with the Agent Space
9. Configure notification channels (email, Slack, MS Teams)
10. Store secrets in SSM Parameter Store SecureString
11. Deploy the CDK stack with `--require-approval broadening`

### Cleanup

To remove all resources created by the setup wizard:

```bash
npx ts-node scripts/cleanup.ts
```

### Manual CDK Deployment

> **Note**: The setup wizard is the recommended deployment path. Manual deployment requires you to create SSM SecureString parameters and IAM roles yourself.

```bash
cd infrastructure/cdk
npm install
npx cdk deploy HealthEventAnalyzerStack-$AWS_REGION \
  --parameters DevOpsAgentWebhookUrl=YOUR_URL \
  --no-cli-pager --require-approval broadening
```

Secrets (webhook secret, Slack URL, MS Teams URL) are stored in **SSM Parameter Store SecureString** — not passed as CloudFormation parameters. For manual deployment, create them before deploying:

```bash
aws ssm put-parameter --name "/health-analyzer/production/webhook-secret" \
  --type SecureString --value "YOUR_HMAC_SECRET"
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
| Lambda | ~$1-3 | 5 functions, invoked per Health event |
| Step Functions | ~$1 | Standard workflow, ~100 executions/month |
| DynamoDB | ~$1 | On-demand, three tables with minimal storage |
| EventBridge | ~$0.50 | Rule evaluations |
| SNS | ~$0.50 | Email notifications |
| Systems Manager OpsCenter | ~$0 | Free tier covers typical usage |
| DevOps Agent | Included | Part of AWS DevOps Agent pricing |
| **Total** | **~$5-15/month** | Varies with Health event volume |

Cost optimization: All resources use on-demand/pay-per-request pricing. No idle costs when no Health events occur.

## Project Structure

```
├── infrastructure/cdk/          # AWS CDK infrastructure (TypeScript)
│   ├── bin/app.ts              # CDK app entry point
│   ├── lib/                    # Stack and construct definitions
│   └── lambda/                 # Lambda function source code
│       ├── event-router/       # Normalizes Health events → starts workflow
│       ├── investigation-trigger/  # HMAC webhook to DevOps Agent
│       ├── investigation-callback/ # Handles agent completion
│       ├── opscenter-creator/  # Creates OpsItem in Systems Manager OpsCenter
│       ├── notifier/           # Routes notifications to teams
│       └── default-contact-resolver/ # AWS Account alternate contacts
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
| `events/test-lambda-deprecation-event.json` | Lambda runtime EOL |
| `events/test-sfn-deprecation-event.json` | Step Functions deprecation |
| `events/test-iam-admin-deprecation-event.json` | IAM admin role enforcement |
| `events/test-security-event.json` | IAM overly permissive policies |
| `events/test-lambda-throttle-event.json` | Lambda throttling |
| `events/test-stepfunctions-issue-event.json` | Step Functions API errors |
| `events/test-rds-ca-expiry-event.json` | RDS CA certificate expiry |

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
