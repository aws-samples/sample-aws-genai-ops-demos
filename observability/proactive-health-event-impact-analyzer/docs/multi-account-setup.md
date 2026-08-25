# Multi-Account Setup Guide

## Overview

This solution supports multi-account AWS Organizations deployments using a **hybrid agent space routing** strategy. Health events from member accounts are automatically routed to the correct DevOps Agent space for investigation, and notifications are sent to the appropriate account's alternate contacts.

## Architecture: Hybrid Agent Space Routing

```
                    AWS Organization
┌─────────────────────────────────────────────────────────┐
│                                                         │
│  Management / Delegated Admin Account                   │
│  ┌───────────────────────────────────────────────────┐  │
│  │  Health Event Analyzer Stack                      │  │
│  │                                                   │  │
│  │  EventBridge ← Health events (org view)           │  │
│  │       ↓                                           │  │
│  │  Event Router (captures sourceAccountId)          │  │
│  │       ↓                                           │  │
│  │  Investigation Trigger                            │  │
│  │       ↓                                           │  │
│  │  ┌─────────────────────────────┐                  │  │
│  │  │ Agent Spaces Routing Table  │                  │  │
│  │  │                             │                  │  │
│  │  │ accountId → webhookUrl      │                  │  │
│  │  │ 111222333 → space-team-a    │                  │  │
│  │  │ 444555666 → space-team-b    │                  │  │
│  │  │ (default) → shared-space    │                  │  │
│  │  └─────────────────────────────┘                  │  │
│  │       ↓                                           │  │
│  │  Route to correct Agent Space                     │  │
│  └───────────────────────────────────────────────────┘  │
│                    ↓              ↓              ↓       │
│           ┌──────────┐   ┌──────────┐   ┌──────────┐   │
│           │ Shared   │   │ Team A   │   │ Team B   │   │
│           │ Space    │   │ Space    │   │ Space    │   │
│           │(default) │   │(acct-111)│   │(acct-444)│   │
│           └──────────┘   └──────────┘   └──────────┘   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## Routing Strategy

### How It Works

1. **Health event arrives** via EventBridge (organizational view aggregates events from all member accounts)
2. **Event Router** extracts the `sourceAccountId` from the event and passes it to the workflow
3. **Investigation Trigger** looks up the agent spaces routing table:
   - If an entry exists for the source account → use that account's dedicated agent space
   - If no entry exists → use the default shared agent space
4. **Notifier** (default routing fallback) uses the `AccountId` parameter to fetch alternate contacts from the correct member account

### When to Use Each Approach

| Scenario | Recommended Approach |
|----------|---------------------|
| Small org (< 10 accounts) | Single shared space |
| Large org with team isolation needs | Per-account spaces for critical teams, shared for the rest |
| Regulated environments (compliance boundaries) | Per-account spaces |
| Cross-account dependencies (shared VPC, transit gateway) | Single shared space (sees full topology) |
| Teams managing their own Agent Spaces | Per-account overrides |

## Setup Instructions

### Step 1: Enable Health Organizational View

In the **management account** (or delegated admin):

```bash
aws health enable-health-service-access-for-organization
```

This aggregates Health events from all member accounts to the central account's EventBridge.

### Step 2: Deploy the Stack

Deploy in the management account or a delegated admin account:

```bash
npx ts-node scripts/setup-wizard.ts
```

The setup wizard handles webhook URL, SSM secrets, and all parameters interactively. For manual deployment:

```bash
cd infrastructure/cdk
npx cdk deploy HealthEventAnalyzerStack-$AWS_REGION \
  --parameters DevOpsAgentWebhookUrl=YOUR_SHARED_SPACE_URL \
  --require-approval=broadening
```

The webhook HMAC secret is stored in SSM Parameter Store SecureString at `/health-analyzer/{env}/webhook-secret` (created by the setup wizard or manually via `aws ssm put-parameter --type SecureString`).

### Step 3: Configure Per-Account Agent Spaces (Optional)

For accounts that need their own dedicated agent space, add entries to the `health-analyzer-agent-spaces` DynamoDB table:

```bash
aws dynamodb put-item \
  --table-name health-analyzer-agent-spaces \
  --item '{
    "accountId": {"S": "111222333444"},
    "webhookUrl": {"S": "https://devops-agent-webhook-url-for-team-a"},
    "webhookSecret": {"S": "hmac-secret-for-team-a"},
    "spaceName": {"S": "team-a-production"},
    "description": {"S": "Dedicated space for Team A production workloads"}
  }'
```

#### Table Schema

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `accountId` | String (PK) | Yes | AWS account ID (12 digits) |
| `webhookUrl` | String | Yes | DevOps Agent webhook URL for this space |
| `webhookSecret` | String | Yes | HMAC secret for webhook authentication |
| `spaceName` | String | No | Human-readable name for logging |
| `description` | String | No | Why this account has a dedicated space |

### Step 4: Configure Cross-Account Permissions for Default Routing

For the default routing fallback to fetch alternate contacts from member accounts, the Lambda role needs cross-account access. This is automatically handled if:

- The stack is deployed in the **management account**, OR
- The stack is deployed in a **delegated administrator** for AWS Account Management

If deployed elsewhere, you'll need to create a role in each member account that trusts the Lambda execution role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::CENTRAL_ACCOUNT:role/HealthEventAnalyzer-NotifierRole"
      },
      "Action": [
        "account:GetAlternateContact",
        "account:GetContactInformation"
      ],
      "Resource": "*"
    }
  ]
}
```

### Step 5: Forward Health Events from Member Accounts (Alternative to Org View)

If you can't enable organizational view, forward events from each member account:

```bash
# In each member account
aws events put-rule \
  --name forward-health-events \
  --event-pattern '{"source":["aws.health"]}' \
  --event-bus-name default

aws events put-targets \
  --rule forward-health-events \
  --targets '[{
    "Id": "central-bus",
    "Arn": "arn:aws:events:REGION:CENTRAL_ACCOUNT:event-bus/default",
    "RoleArn": "arn:aws:iam::MEMBER_ACCOUNT:role/EventBridgeForwardRole"
  }]'
```

## DevOps Agent Space Configuration

### Shared Space (Recommended Default)

A single Agent Space with cross-account resource discovery:

1. Create the space in the central account
2. Enable cross-account topology discovery:
   - Use AWS Resource Explorer with multi-account aggregator
   - Or deploy CloudFormation StackSets that register resources
3. The shared space sees the full dependency graph across accounts

**Advantages:**
- Traces cross-account dependencies (shared VPC → ECS in account B)
- Single investigation covers the full blast radius
- Simpler to manage

### Per-Account Spaces

Dedicated Agent Spaces for specific accounts:

1. Create a space in (or for) the target account
2. Configure its webhook and add to the routing table
3. That space only sees resources within its scope

**Advantages:**
- Security isolation between teams
- Independent topology and investigation capacity
- Teams manage their own space configuration

## Troubleshooting

### Investigation uses wrong agent space
- Check the `health-analyzer-agent-spaces` table for the source account ID
- Verify the `sourceAccountId` field in the Step Functions execution input
- Check Investigation Trigger Lambda logs for "Using agent space:" messages

### Default routing can't fetch member account contacts
- Verify the stack is deployed in the management account or delegated admin
- Check IAM permissions: `account:GetAlternateContact` with cross-account access
- Ensure member accounts have alternate contacts configured

### Health events from member accounts not arriving
- Verify organizational view is enabled: `aws health describe-health-service-status-for-organization`
- Check EventBridge rules are capturing events with the correct source account
- Verify the event bus policy allows cross-account event delivery

### Agent space webhook returns 403/401
- Verify the webhook URL and secret in the routing table match the Agent Space configuration
- Check if the webhook credentials have expired (regenerate in Agent Space console)
- Ensure the Agent Space has available investigation capacity

## Cost Considerations

| Component | Single Account | Multi-Account (10 accounts) |
|-----------|---------------|----------------------------|
| DynamoDB (agent spaces table) | ~$0 | ~$0 (minimal reads) |
| Lambda (additional API calls) | ~$0 | ~$0.50/month |
| DevOps Agent (per space) | Included | Included per space |
| EventBridge (cross-account) | ~$0 | ~$1/month |
| **Additional cost** | **$0** | **~$1.50/month** |

The hybrid approach adds negligible cost — the DynamoDB lookup is a single GetItem per event, and the Account API calls are free tier.
