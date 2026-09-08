# Architecture — Proactive Health Event Impact Analyzer

## Overview

This sample automates the assessment of AWS Health events by integrating EventBridge, Step Functions, and AWS DevOps Agent. When AWS Health publishes an event (scheduled maintenance, operational issue, or abuse notification), the system automatically triggers an AI-powered investigation that determines blast radius, identifies affected teams, and routes notifications through team-specific channels.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AWS Account                                         │
│                                                                             │
│  ┌─────────────┐     ┌──────────────────┐     ┌──────────────────────┐     │
│  │ AWS Health  │────▶│   EventBridge    │────▶│   Event Router       │     │
│  │ Service     │     │   Rules          │     │   (Lambda)           │     │
│  └─────────────┘     └──────────────────┘     └──────────┬───────────┘     │
│                                                           │                  │
│                                                           ▼                  │
│                                                ┌──────────────────┐          │
│                                                │  Step Functions   │          │
│                                                │  State Machine    │          │
│                                                └────────┬─────────┘          │
│                                                         │                    │
│                                                         ▼                    │
│  ┌──────────────────┐                       ┌──────────────────────┐        │
│  │  DynamoDB        │◀─────────────────────▶│ Investigation        │        │
│  │  Task Tokens     │                       │ Trigger (Lambda)     │        │
│  └──────────────────┘                       └──────────┬───────────┘        │
│                                                         │                    │
│                                                         │ HMAC Webhook       │
│                                                         ▼                    │
│                                              ┌──────────────────────┐        │
│                                              │   AWS DevOps Agent   │        │
│                                              │                      │        │
│                                              │  • Topology Query    │        │
│                                              │  • Blast Radius      │        │
│                                              │  • Team Detection    │        │
│                                              │  • Recommendations   │        │
│                                              └──────────┬───────────┘        │
│                                                         │                    │
│                                                         │ EventBridge        │
│                                                         │ (aws.aidevops)     │
│                                                         ▼                    │
│  ┌──────────────────┐                       ┌──────────────────────┐        │
│  │  DynamoDB        │◀─────────────────────▶│ Investigation        │        │
│  │  Task Tokens     │                       │ Callback (Lambda)    │        │
│  └──────────────────┘                       └──────────┬───────────┘        │
│                                                         │                    │
│                                                         │ SendTaskSuccess    │
│                                                         ▼                    │
│                                              ┌──────────────────────┐        │
│                                              │   Has Findings?      │        │
│                                              └───┬─────────────┬────┘        │
│                                                  │             │             │
│                                            YES   │             │ NO          │
│                                                  ▼             ▼             │
│                                        ┌──────────────────┐  ┌────────┐     │
│                                        │ OpsCenter Creator │  │  Skip  │     │
│                                        │   (Lambda)        │  └────────┘     │
│                                        └────────┬─────────┘                  │
│                                                  │                           │
│                                                  ▼                           │
│  ┌──────────────────┐                  ┌──────────────┐                     │
│  │  DynamoDB        │◀────────────────▶│   Notifier   │                     │
│  │  Teams Config    │                  │   (Lambda)   │                     │
│  └──────────────────┘                  └──────┬───────┘                     │
│                                               │                              │
│                              ┌────────────────┼────────────────┐             │
│                              ▼                ▼                 ▼             │
│                        ┌──────────┐    ┌──────────┐     ┌────────────┐      │
│                        │  SNS     │    │  Slack   │     │ MS Teams   │      │
│                        │  (Email) │    │ Webhooks │     │ Webhooks   │      │
│                        └──────────┘    └──────────┘     └────────────┘      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Component Details

### Event Ingestion Layer

| Component | Service | Purpose |
|-----------|---------|---------|
| Health Event Rule | EventBridge | Captures `aws.health` events |
| Scheduled Change Rule | EventBridge | Captures scheduled maintenance specifically |
| Event Router | Lambda (Node.js 24) | Normalizes events, starts workflow |

### Investigation Layer

| Component | Service | Purpose |
|-----------|---------|---------|
| State Machine | Step Functions | Orchestrates investigation workflow |
| Investigation Trigger | Lambda (Node.js 24) | Sends HMAC webhook to DevOps Agent |
| Investigation Callback | Lambda (Node.js 24) | Receives results via EventBridge |
| Task Token Table | DynamoDB | Stores Step Functions task tokens |

### Notification Layer

| Component | Service | Purpose |
|-----------|---------|---------|
| OpsCenter Creator | Lambda (Node.js 24) | Creates OpsItem in Systems Manager OpsCenter |
| Notifier | Lambda (Node.js 24) | Routes alerts to teams |
| Teams Table | DynamoDB | Team notification preferences |
| Impact Topic | SNS | Email notifications |
| Slack Integration | HTTPS Webhooks | Slack channel notifications |
| MS Teams Integration | HTTPS Webhooks | Microsoft Teams Adaptive Cards |

## Data Flow

1. **Ingestion**: AWS Health → EventBridge → Event Router Lambda → Step Functions
2. **Investigation**: Step Functions → Investigation Trigger → DevOps Agent (webhook)
3. **Callback**: DevOps Agent → EventBridge → Investigation Callback → Step Functions
4. **OpsItem**: Step Functions → OpsCenter Creator Lambda → Systems Manager OpsCenter
5. **Notification**: Step Functions → Notifier Lambda → SNS/Slack/MS Teams (per team, with OpsItem link)

## Integration Pattern: Wait for Task Token

The Step Functions workflow uses the **Wait for Task Token** pattern to integrate with DevOps Agent asynchronously:

1. Investigation Trigger Lambda receives a task token from Step Functions
2. Stores the token in DynamoDB keyed by incident ID
3. Sends the investigation request to DevOps Agent via webhook
4. Step Functions pauses (up to 30 min heartbeat timeout)
5. When DevOps Agent completes, it emits an EventBridge event
6. Investigation Callback Lambda retrieves the token from DynamoDB
7. Calls `SendTaskSuccess` or `SendTaskFailure` to resume the workflow

## AWS DevOps Agent Provisioning

The Agent Space, its IAM roles, the AWS account monitor association, the
operator app, and the generic `eventChannel` webhook are provisioned by a
dedicated CDK stack — `DevOpsAgentSpaceStack`
(`infrastructure/cdk/lib/devops-agent-space-stack.ts`, backed by the
`DevOpsAgentSpace` construct in `lib/constructs/devops-agent-space.ts`) — using
AWS CloudFormation's `AWS::DevOpsAgent::AgentSpace`, `AWS::DevOpsAgent::Association`,
and IAM L1/L2 constructs. This replaced an earlier design where the setup
wizard drove these steps with the `aws devops-agent` CLI directly.

This stack deploys independently of the main `HealthEventAnalyzerStack` and
can target a different Region (`DEVOPS_AGENT_REGION`; see the README) since an
Agent Space monitors resources across *all* Regions of an associated account.
`scripts/setup-wizard.ts` deploys it first, reads its `AgentSpaceId`,
`WebhookUrl`, and `WebhookSecretArn` outputs via `aws cloudformation
describe-stacks`, and threads them into the main stack's synthesis as CDK
context (`-c devOpsAgentWebhookUrl=... -c devOpsAgentWebhookSecretArn=...`) —
there is no CloudFormation parameter for either value.

**Why the webhook still needs a Lambda-backed custom resource**: AWS DevOps
Agent returns the webhook's HMAC secret exactly once, in the `AssociateService`
API response — no `Describe`/`List` call ever returns it again, and
`AWS::DevOpsAgent::Association` exposes no webhook attributes for
CloudFormation to surface. (`AWS::DevOpsAgent::Service` also has no
`eventChannel` service-details type, so registering that service can't be
expressed as a CloudFormation resource either.) The custom resource
(`lambda/devops-agent-webhook-provisioner/index.ts`) performs
`RegisterService(eventChannel)` + `AssociateService` at deploy time and writes
the secret directly into a Secrets Manager secret — the value never enters
CloudFormation state, template outputs, or this repository's scripts. Any
property change on the custom resource rotates the webhook (replacement =
delete + recreate), since the secret can't be re-read to preserve it across an
update.

## Security

- HMAC-SHA256 webhook authentication (secret stored in a CDK-managed Secrets
  Manager secret, written once by the webhook provisioning custom resource
  and never transmitted in plaintext through this repository's scripts)
- Least-privilege IAM roles per Lambda function (scoped to specific resource ARNs)
- Slack/MS Teams webhook secrets stored in SSM Parameter Store SecureString
  (set by the setup wizard); the DevOps Agent webhook secret is Secrets
  Manager instead (see "AWS DevOps Agent Provisioning" above). Neither is a
  CloudFormation parameter.
- DynamoDB TTL on task tokens (1 hour expiry)
- CloudWatch log retention: 90 days (production), 14 days (staging)
- PAY_PER_REQUEST billing (no over-provisioning)
- KMS-encrypted SNS topic with restrictive resource policy
- SQS Dead Letter Queues on all event-driven Lambdas (14-day retention)

## Cost Optimization

- All resources use on-demand/pay-per-request pricing — zero idle cost
- No reserved concurrency on Lambda functions (relies on account pool + retry/DLQ)
- DynamoDB tables use PAY_PER_REQUEST with PITR enabled
- EventBridge rules: max event age 24h, 185 retry attempts
- Step Functions task states: 3 retries with exponential backoff (5s base, rate 2)
