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

## Security

- HMAC-SHA256 webhook authentication (secret stored in SSM SecureString, never transmitted in plaintext)
- Least-privilege IAM roles per Lambda function (scoped to specific resource ARNs)
- Secrets stored in SSM Parameter Store SecureString (not CloudFormation parameters)
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
