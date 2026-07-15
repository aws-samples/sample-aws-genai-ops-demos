# Architecture

## High-Level Flow

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────────┐
│   AWS Health    │────▶│  EventBridge │────▶│  Event Router   │
│   Service       │     │  Rule        │     │  (Lambda)       │
└─────────────────┘     └──────────────┘     └────────┬────────┘
                                                       │
                                                       ▼
                                              ┌────────────────┐
                                              │ Step Functions  │
                                              │ (Task Token)    │
                                              └────────┬────────┘
                                                       │
                                                       ▼
                                              ┌────────────────────┐
                                              │Investigation Trigger│
                                              │  (Lambda → Webhook) │
                                              └────────┬───────────┘
                                                       │ HMAC-signed POST
                                                       ▼
                                              ┌────────────────────────┐
                                              │    AWS DevOps Agent    │
                                              │                        │
                                              │  Topology + Custom     │
                                              │  Skill:                │
                                              │  • Blast radius        │
                                              │  • Team identification │
                                              │  • Redundancy check    │
                                              │  • Recommendations     │
                                              └────────┬───────────────┘
                                                       │
                                                       ▼ EventBridge (aws.aidevops)
                                              ┌────────────────────┐
                                              │Investigation       │
                                              │Callback (Lambda)   │
                                              └────────┬───────────┘
                                                       │ SendTaskSuccess
                                                       ▼
                                              ┌────────────────┐
                                              │  Has Findings? │
                                              └───┬────────┬───┘
                                                  │        │
                                            YES   │        │  NO
                                                  ▼        ▼
                                        ┌──────────────┐  ┌──────┐
                                        │  OpsCenter   │  │ Skip │
                                        │  Creator     │  └──────┘
                                        │  (Lambda)    │
                                        └──────┬───────┘
                                               │ Creates OpsItem
                                               ▼
                                          ┌──────────┐
                                          │ Notifier │
                                          │ (Lambda) │
                                          └────┬─────┘
                                               │ Team Routing
                                               │ (DynamoDB lookup)
                              ┌────────────────┼────────────────┐
                              ▼                ▼                 ▼
                        ┌──────────┐    ┌──────────┐     ┌────────────┐
                        │  Email   │    │  Slack   │     │ MS Teams   │
                        │(per team)│    │(per team)│     │(per team)  │
                        └──────────┘    └──────────┘     └────────────┘
```

## Components

### 1. Event Ingestion (EventBridge + Lambda)

- **EventBridge Rule**: Captures all `aws.health` events including scheduled maintenance, operational issues, and abuse notifications.
- **Event Router Lambda**: Normalizes the raw Health event into a structured payload and starts the Step Functions workflow.

### 2. Investigation Workflow (Step Functions)

Uses the **Wait for Task Token** integration pattern:

1. Triggers DevOps Agent investigation via webhook
2. Stores the task token in DynamoDB (keyed by incident ID)
3. Pauses execution until the investigation completes (up to 30 min heartbeat)
4. Resumes when the callback Lambda sends `SendTaskSuccess`

### 3. DevOps Agent Integration

**Custom Skill** (`devops-agent-skill/SKILL.md`):
- Teaches the agent a structured methodology for Health event impact assessment
- Instructs it to check resource tags for team ownership
- Defines severity classification criteria
- Specifies output format with team routing information

**Jira Integration (MCP Server)**:
- The agent has access to the Atlassian Rovo MCP Server (registered via setup wizard)
- When severity is MEDIUM or higher, the agent creates or comments on a Jira ticket
- Tools allow-listed: read, search, create, comment (no edit/delete)
- Routing config (project key, issue type, site URL) is read from SSM by the Investigation Trigger Lambda and inlined in the prompt as a `[JIRA_CONFIG:{...}]` tag
- The agent's session policy strips SSM access, so the config must come from the trigger Lambda

**Trigger (outbound):**
- Sends HMAC-authenticated webhook to DevOps Agent
- Includes health event details, affected resources, investigation questions, and Jira routing config

**Callback (inbound):**
- Listens for `aws.aidevops` EventBridge events
- Retrieves the stored task token from DynamoDB
- Sends investigation results back to Step Functions

### 4. OpsCenter Integration (Systems Manager)

**OpsCenter Creator Lambda** — runs after the "Has Findings?" choice when impact is detected:

- Creates an **OpsItem** in AWS Systems Manager OpsCenter for persistent tracking
- Maps DevOps Agent priority to OpsItem severity (CRITICAL→1, HIGH→2, MEDIUM→3, LOW→4)
- Maps Health event category to OpsItem category (issue→Availability, abuse→Security, etc.)
- Attaches structured **OperationalData** — health event ARN, source account, service, region, priority, findings JSON, recommendations JSON, affected resources, and the agent investigation link
- Constructs related resource ARNs from affected entity IDs for console linkage
- Truncates description gracefully at the 2048-char API limit; points operators to OperationalData for the full analysis
- Non-blocking: if OpsItem creation fails, the workflow continues to notifications

### 5. Multi-Team Notification Routing

**Teams Table** (DynamoDB `health-analyzer-teams`):
- Stores per-team notification preferences
- Supports multiple channels: email, Slack webhook, MS Teams
- Configurable severity thresholds per team

**Routing Logic:**
1. Extract team identifiers from investigation findings
2. Look up each team's config in DynamoDB
3. Filter by severity threshold
4. Send to each team's configured channels
5. Send summary to default catch-all channel

## Security Considerations

- Lambda functions use least-privilege IAM roles scoped to specific resource ARNs
- DevOps Agent webhook uses HMAC authentication
- Webhook secret stored in SSM Parameter Store SecureString (fetched at runtime with 5-min cache)
- SNS topic encrypted with KMS and enforces HTTPS-only publishing
- All Lambda logs have environment-aware retention (90 days production, 14 days staging)
- DynamoDB tables have PITR enabled and encryption at rest
- Task tokens have TTL to prevent orphaned state
- Dead letter queues on event-driven Lambdas with CloudWatch alarms
- Single composite alarm covers all components
