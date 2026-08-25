# Configuration Guide

## Prerequisites

- **CloudTrail**: The deployment account must have an active CloudTrail trail capturing management events for the deployment region. This ensures all API calls to critical resources (DynamoDB, SSM, Lambda) are auditable.
- **Recommended**: Enable CloudTrail data events for DynamoDB item-level operations on the `health-analyzer-teams`, `health-analyzer-agent-spaces`, and `health-analyzer-task-tokens` tables to support auditing of read and write access to sensitive configuration and task token data.

## Production Hardening

The solution is production-hardened with the following security and operational features:

| Feature | Detail |
|---------|--------|
| **IAM least-privilege** | All policies scoped to specific resource ARNs; no wildcards where deterministic |
| **Secrets management** | All secrets in SSM Parameter Store SecureString; fetched at runtime with 5-min cache |
| **Encryption at rest** | DynamoDB (AWS owned key), SNS (KMS `alias/aws/sns`), CloudWatch Logs (AES-256) |
| **Encryption in transit** | SNS topic denies non-SSL transport (`aws:SecureTransport` condition) |
| **Dead letter queues** | 3 SQS DLQs for event-driven Lambdas (14-day retention, CloudWatch alarms) |
| **Composite alarm** | Single alarm covering all 6 Lambdas + Step Functions errors |
| **DynamoDB protection** | PITR enabled, deletion protection in production, RETAIN removal policy |
| **Step Functions resilience** | Retry (3 attempts, 5s backoff) and Catch on all task states |
| **CDK Nag** | aws-solutions rule pack with zero Error-level findings |
| **Resource tagging** | Project, Environment, ManagedBy on all resources |

### Environment Configuration

The stack accepts an `environment` CDK context variable (`production` or `staging`):

```bash
npx cdk deploy ... -c environment=production   # 90-day logs, RETAIN, deletion protection
npx cdk deploy ... -c environment=staging      # 14-day logs, DESTROY, no deletion protection
```

## DevOps Agent Setup

This solution relies on AWS DevOps Agent's topology for workload discovery — no manual workload configuration needed.

### Step 1: Create an Agent Space

1. Open the [AWS DevOps Agent console](https://console.aws.amazon.com/devopsagent)
2. Create a new Agent Space
3. Connect your AWS account(s) for resource discovery

### Step 2: Wait for Topology Discovery

DevOps Agent automatically discovers resources through:

- **CloudFormation stacks** — all resources deployed via CloudFormation/CDK
- **Resource Explorer** — tagged resources not in CloudFormation (enable Resource Explorer in your account)

The initial topology scan takes a few minutes. You can verify it's complete in the Topology page of the Operator Web App.

### Step 3: Configure a Generic Webhook

1. In your Agent Space, go to **Capabilities** → **Webhook**
2. Click **Generate webhook** (creates HMAC credentials)
3. Save the **webhook URL** and **HMAC secret** — you won't be able to retrieve the secret again
4. The setup wizard stores the HMAC secret in SSM Parameter Store SecureString at `/health-analyzer/{env}/webhook-secret`

### Step 4: Verify EventBridge Integration

DevOps Agent automatically sends events to the default EventBridge bus when investigations complete. No additional configuration needed — the CDK stack creates the rule to capture `aws.aidevops` events.

## Deployment Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `DevOpsAgentWebhookUrl` | Yes | — | Generic webhook URL from DevOps Agent |
| `NotificationEmail` | No | (empty) | Email for SNS notifications |

### Secrets in SSM Parameter Store

Secrets are **no longer passed as CloudFormation parameters**. They are stored in SSM Parameter Store SecureString and fetched by Lambda functions at runtime with caching (5-minute TTL).

| SSM Parameter Path | Consumer | Description |
|---|---|---|
| `/health-analyzer/{env}/webhook-secret` | Investigation Trigger Lambda | DevOps Agent HMAC secret |
| `/health-analyzer/{env}/slack-webhook-url` | Notifier Lambda | Slack incoming webhook URL |
| `/health-analyzer/{env}/msteams-webhook-url` | Notifier Lambda | MS Teams webhook URL |

Where `{env}` is `production` or `staging` based on the CDK context variable.

The setup wizard creates these parameters automatically. For manual deployments:

```bash
aws ssm put-parameter --name "/health-analyzer/production/webhook-secret" \
  --type SecureString --value "YOUR_HMAC_SECRET" --overwrite
aws ssm put-parameter --name "/health-analyzer/production/slack-webhook-url" \
  --type SecureString --value "https://hooks.slack.com/..." --overwrite
aws ssm put-parameter --name "/health-analyzer/production/msteams-webhook-url" \
  --type SecureString --value "https://..." --overwrite
```

> **Security**: Lambda environment variables contain only the SSM parameter *name* (path), never the secret value itself. IAM permissions are scoped to the specific parameter ARNs each Lambda needs.

## How Topology Replaces Static Config

Traditional approach (what we removed):
```json
{
  "workloads": [
    {
      "name": "payment-service",
      "criticality": "CRITICAL",
      "resources": [{"type": "EC2", "ids": ["i-0abc123"]}],
      "redundancy": {"multiAz": false}
    }
  ]
}
```

Problems with static config:
- Drifts from reality as infrastructure changes
- Requires manual updates for every deployment
- Misses dynamically-created resources (auto-scaling, spot instances)
- Doesn't capture resource relationships

DevOps Agent topology provides:
- **Live resource inventory** — always reflects current state
- **Dependency graphs** — understands ALB → ECS → RDS chains
- **Deployment context** — links resources to CloudFormation stacks and CI/CD
- **Observability correlation** — uses CloudWatch metrics and logs during investigation

## Investigation Behavior

When a Health event triggers an investigation, DevOps Agent:

1. Receives the incident via webhook with affected resources and context
2. Queries its topology to find which applications use those resources
3. Traces dependency chains to determine blast radius
4. Checks redundancy (multi-AZ distribution, auto-scaling groups)
5. Analyzes recent metrics/logs for the affected resources
6. Produces findings with severity and recommendations

The investigation typically completes in 2-10 minutes depending on topology complexity.

## Notification Channels

### Default Routing Fallback (No Configuration Required)

When no team routing configuration is found in the DynamoDB teams table, the system automatically falls back to **default routing**. This ensures notifications are always delivered even without explicit team setup.

**Default routing resolves contacts from the AWS Account API:**

1. **Root/Primary Contact** — The account's primary contact information
2. **Operations Contact** — The alternate contact designated for operational issues
3. **Security Contact** — The alternate contact designated for security notifications
4. **Billing Contact** — The alternate contact designated for billing matters

**How it works:**
- When the Notifier Lambda detects no team configs match the investigation findings, it calls the AWS Account API
- It fetches alternate contacts (Operations, Security, Billing) and sends notifications to each one with an email address
- Notifications include a clear indicator that they were sent via default routing and recommend configuring team-specific routing

**Prerequisites for default routing:**
- The Lambda execution role has `account:GetContactInformation` and `account:GetAlternateContact` permissions (automatically configured by CDK)
- At least one alternate contact must be configured in your AWS account ([Account Settings → Alternate Contacts](https://console.aws.amazon.com/billing/home#/account))

**To configure alternate contacts:**
```bash
aws account put-alternate-contact \
  --alternate-contact-type OPERATIONS \
  --name "Ops Team" \
  --email-address "ops@example.com" \
  --phone-number "+1-555-0100" \
  --title "Operations Lead"
```

**To disable default routing:**
Set the environment variable `ENABLE_DEFAULT_ROUTING=false` on the Notifier Lambda, or remove the environment variable from the CDK construct.

### Email (SNS)

Provide `NotificationEmail` parameter. You'll receive a subscription confirmation email that must be accepted.

### Slack

1. Create an [Incoming Webhook](https://api.slack.com/messaging/webhooks) in your Slack workspace
2. The setup wizard stores the webhook URL in SSM Parameter Store at `/health-analyzer/{env}/slack-webhook-url`
3. For manual deployment: `aws ssm put-parameter --name "/health-analyzer/production/slack-webhook-url" --type SecureString --value "https://hooks.slack.com/..."`
4. Notifications include rich formatting with severity colors

### Adding More Channels

Extend the `infrastructure/cdk/lambda/notifier/index.ts` to add:
- Microsoft Teams (via Workflows webhook — see [MS Teams Integration](./msteams-integration.md))
- OpsGenie (via Alert API)

### Jira (via DevOps Agent MCP Server)

The DevOps Agent can auto-file Jira tickets when it detects MEDIUM+ impact. This is configured via the setup wizard (Step 7) or the `--jira-only` flag. The agent uses the Atlassian Rovo MCP Server to create and comment on tickets.

- Routing config (project key, issue type, site URL) stored in SSM: `/health-analyzer/jira/*`
- Tickets are created only for severity ≥ MEDIUM
- Tools allow-listed: read, search, create, comment (no edit/delete/transition)

See [docs/jira-integration.md](./jira-integration.md) for the complete setup guide.

## Multi-Account Setup

For organizations with multiple AWS accounts:

1. Deploy the main stack in your central operations account
2. In spoke accounts, create EventBridge rules that forward `aws.health` events to the central account's event bus
3. Ensure DevOps Agent has cross-account access configured for topology discovery

## Troubleshooting

### Investigation not starting
- Verify webhook URL and secret are correct
- Check the Investigation Trigger Lambda logs for HTTP errors
- Ensure DevOps Agent has available investigation capacity (monthly limits apply)

### Investigation starts but no callback
- Verify the EventBridge rule for `aws.aidevops` events exists
- Check that the investigation isn't being cancelled (rate limit)
- Review the Investigation Callback Lambda logs

### Task token expired
- Default heartbeat timeout is 30 minutes
- If investigations consistently take longer, increase `heartbeatTimeout` in the CDK construct
- Check DynamoDB TTL isn't expiring tokens prematurely
