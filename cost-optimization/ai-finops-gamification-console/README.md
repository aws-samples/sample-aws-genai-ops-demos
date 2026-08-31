# AI FinOps Gamification Console

*Gamify cloud cost ownership with AI-powered findings from AWS FinOps Agent, featuring team leaderboards, accountability tracking, and a learning loop that gets smarter over time.*

## Overview

This demo builds an **ownership and gamification layer** on top of [AWS FinOps Agent](https://aws.amazon.com/finops-agent/) (preview). While FinOps Agent handles cost analysis, anomaly detection, and optimization recommendations, this console adds what it doesn't provide natively:

- **Resource → Owner Mapping**: Scope services/accounts/tags to teams and champions
- **Human-in-the-Loop Acceptance**: Every finding requires manual acceptance before action
- **Learning Loop**: Rejected findings teach the system, suppressing similar future noise
- **Gamification**: Leaderboards, badges, streaks, and monthly champion recognition
- **Team-Specific Views**: Personalized dashboards showing only relevant findings

## At a Glance

| Attribute | Value |
|-----------|-------|
| **Duration** | 25-30 minutes |
| **Difficulty** | Intermediate |
| **Target Audience** | FinOps practitioners, Cloud Architects, Engineering Managers |
| **Key Technologies** | AWS FinOps Agent, Amazon Cognito, API Gateway, Lambda, DynamoDB, CloudFront, React + Cloudscape |
| **Pillar** | Cost Optimization |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│              AWS FinOps Agent (Scheduled Automation)            │
│                                                                 │
│  Weekly: Anomalies >10%, top spenders, optimization findings    │
│  Output: Slack channel post with HTML report attachment         │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│              Ingestion Layer (EventBridge + Lambda)             │
│                                                                 │
│  • Slack API retrieval (works today, no unconfirmed APIs)       │
│  • Parse HTML/JSON findings → normalize → write to backlog      │
│  • Pluggable adapter pattern for future native API support      │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│              Gamification Console (React + Cloudscape)          │
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │  Backlog    │  │  Ownership  │  │    Leaderboard          │ │
│  │  View       │  │  Scoping    │  │    & Gamification       │ │
│  │             │  │             │  │                         │ │
│  │ • NEW       │  │ • Teams     │  │ • Per-member ranking    │ │
│  │ • PENDING   │  │ • Champions │  │ • Response time         │ │
│  │ • ACCEPTED  │  │ • Services  │  │ • $ saved               │ │
│  │ • RESOLVED  │  │ • Accounts  │  │ • Badges & streaks      │ │
│  │ • REJECTED  │  │ • Tags      │  │ • Monthly champions     │ │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘ │
│                                                                 │
│  Auth: Amazon Cognito (finops-admin, champion, viewer roles)    │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Backend (API Gateway + Lambda)               │
│                                                                 │
│  • Findings CRUD with state machine transitions                 │
│  • Accept/Reject with learning loop storage                     │
│  • Ownership scoping management                                 │
│  • Leaderboard scoring engine                                   │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Data Layer (DynamoDB)                        │
│                                                                 │
│  Tables: teams, scoping, findings, learnings, scores            │
└─────────────────────────────────────────────────────────────────┘
```

## Business Value

### For FinOps Teams
- **Accountability**: Every finding has an owner; nothing falls through the cracks
- **Noise Reduction**: Learning loop suppresses irrelevant findings over time
- **Visibility**: Real-time dashboards show who's addressing what

### For Engineering Leaders
- **Gamification**: Leaderboards drive healthy competition and recognition
- **Metrics**: Track response time, $ saved, findings closed per team
- **Culture**: Make cost optimization visible and rewarding

### For Organizations
- **ROI Tracking**: Measure actual savings achieved vs. findings identified
- **Continuous Improvement**: System learns from rejections, backlog gets quieter
- **Compliance**: Audit trail of all finding dispositions

## Prerequisites

### AWS Account Requirements
- AWS account with administrative access
- AWS FinOps Agent enabled (preview, us-east-1 only)
- Slack workspace with a dedicated FinOps channel (for ingestion)

### Local Development
- Node.js 20+
- AWS CLI v2.31.13+
- AWS CDK (installed automatically)

### FinOps Agent Setup (Required)
1. Create a FinOps Agent in the AWS Console (us-east-1)
2. Connect to your Slack workspace
3. Create a scheduled automation for weekly cost reports
4. Note the Slack channel ID for the ingestion configuration

## Deployment

### Quick Start

```powershell
# Windows (PowerShell)
.\deploy-all.ps1
```

```bash
# macOS/Linux
./deploy-all.sh
```

### What Gets Deployed
- **Cognito User Pool**: Authentication with RBAC (admin/champion/viewer)
- **DynamoDB Tables**: teams, scoping, findings, learnings, scores
- **Lambda Functions**: API handlers, ingestion adapter
- **API Gateway**: REST API for the console
- **CloudFront + S3**: React frontend hosting

### Post-Deployment Setup

1. **Create Admin User**:
   ```bash
   # Get User Pool ID from deployment output
   aws cognito-idp admin-create-user \
     --user-pool-id <USER_POOL_ID> \
     --username admin@example.com \
     --user-attributes Name=email,Value=admin@example.com \
     --temporary-password TempPass123!
   
   # Add to admin group
   aws cognito-idp admin-add-user-to-group \
     --user-pool-id <USER_POOL_ID> \
     --username admin@example.com \
     --group-name finops-admin
   ```

2. **Configure Slack Integration** (for ingestion):
   - Create a Slack App with `channels:history` and `files:read` permissions
   - Store the Bot Token in Secrets Manager (created by deployment)
   - Update the ingestion Lambda environment variable with your channel ID

3. **Seed Demo Data** (optional):
   ```powershell
   .\scripts\seed-demo-data.ps1
   ```

## Demo Walkthrough

### 1. Login & Dashboard (2 min)
- Open the console URL from deployment output
- Login as admin to see the main dashboard
- Overview: pending findings, team scores, recent activity

### 2. Backlog Management (5 min)
- View incoming findings from FinOps Agent
- **Accept** a finding → assigns to team champion
- **Reject** a finding → requires reason, feeds learning loop
- Filter by team, status, severity

### 3. Ownership Scoping (3 min)
- Create teams (Platform, Data, Application)
- Assign champions with email addresses
- Scope teams to services/accounts/tags
- Automatic routing based on scoping rules

### 4. Learning Loop (3 min)
- Reject a finding with reason: "Expected spike - monthly batch job"
- See the learning record created
- Future similar findings show annotation: "Previously rejected: ..."
- Down-ranked or auto-suppressed based on confidence

### 5. Gamification & Leaderboard (5 min)
- View per-member rankings
- Metrics: findings accepted → resolved, response time, $ saved
- Badges: "Speed Demon", "Cost Killer", "Clean Slate"
- Streaks: consecutive months with all findings addressed
- Monthly champion recognition

### 6. Team Dashboard (2 min)
- Switch to champion view
- See only your team's findings
- Personal stats vs. team average
- Action items with one-click acknowledge

## Estimated Costs

### Demo Environment (one-time testing)
| Service | Estimated Cost |
|---------|---------------|
| Cognito | Free tier (50,000 MAU) |
| DynamoDB | ~$0.50 (on-demand, minimal data) |
| Lambda | Free tier (1M requests) |
| API Gateway | ~$0.50 (REST API) |
| CloudFront | ~$0.50 (minimal traffic) |
| S3 | ~$0.10 (static hosting) |
| **Total** | **~$1.60/demo** |

### Production Environment (monthly ongoing)
| Service | Estimated Cost |
|---------|---------------|
| Cognito | Free tier or ~$0.0055/MAU |
| DynamoDB | ~$5-25 (depends on findings volume) |
| Lambda | ~$2-10 (depends on API calls) |
| API Gateway | ~$3.50/million requests |
| CloudFront | ~$1-5 (depends on traffic) |
| S3 | ~$0.50 |
| **Total** | **~$15-50/month** |

### Cost Optimization Tips
- Use DynamoDB on-demand for unpredictable workloads
- Enable CloudFront caching for API responses where appropriate
- Consider Reserved Capacity for DynamoDB if consistent usage

## Finding Lifecycle

```
NEW (ingested from Slack)
  │
  ▼
PENDING_ACCEPTANCE  ← Human-in-the-loop gate
  │
  ├─► ACCEPTED → ASSIGNED (owner) → IN_PROGRESS → RESOLVED → CLOSED
  │                                                    │
  │                                              $ saved recorded
  │
  └─► REJECTED (reason required)
         │
         ▼
      LEARNING record created
         │
         ├─► Similar findings annotated
         └─► High-confidence matches suppressed
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SLACK_CHANNEL_ID` | Slack channel for FinOps Agent reports | Required |
| `INGESTION_SCHEDULE` | EventBridge schedule expression | `rate(1 hour)` |
| `LEARNING_THRESHOLD` | Confidence threshold for auto-suppression | `0.8` |

### Cognito Groups (RBAC)

| Group | Permissions |
|-------|-------------|
| `finops-admin` | Full access: governance, config, all teams |
| `champion` | Own team's findings, accept/reject, personal stats |
| `viewer` | Read-only dashboards and leaderboard |

## Troubleshooting

### Common Issues

**"No findings appearing in backlog"**
- Verify Slack Bot Token is configured in Secrets Manager
- Check ingestion Lambda logs for parsing errors
- Ensure FinOps Agent automation is posting to the correct channel

**"User can't login"**
- Verify user is confirmed in Cognito
- Check user is assigned to appropriate group
- Ensure Cognito client ID matches frontend config

**"Leaderboard not updating"**
- Scores are recalculated on finding resolution
- Check DynamoDB `scores` table for entries
- Verify Lambda has write permissions

### Logs & Debugging
```bash
# View ingestion Lambda logs
aws logs tail /aws/lambda/FinOpsIngestion-<region> --follow

# View API Lambda logs
aws logs tail /aws/lambda/FinOpsAPI-<region> --follow
```

## Cleanup

```powershell
# Windows
.\deploy-all.ps1 -Destroy

# macOS/Linux
./deploy-all.sh --destroy
```

**Note**: This will delete all data in DynamoDB tables. Export any findings or learnings you want to preserve first.

## FinOps Agent Integration Notes

### Current Limitations (Preview)
- **Region**: FinOps Agent is available only in us-east-1 during preview
- **Slack**: Delivery-only (no bi-directional conversation in Slack)
- **API**: Native APIs may not be in boto3 SDK yet; Slack retrieval is the default path

### Future Enhancements (at GA)
- Swap ingestion adapter to native `GetArtifactContent` API
- Direct EventBridge integration for task completion events
- Email/SNS notification support (on FinOps Agent roadmap)

## Extension Points

### ServiceNow Integration
Replace Jira-style assignment with ServiceNow incidents:
1. Add ServiceNow REST API credentials to Secrets Manager
2. Modify `assignment_handler.py` to create ServiceNow incidents
3. Map CMDB owners for automatic assignment

### Custom Notification Channels
Add SES email or SNS notifications:
1. Create SES identity or SNS topic
2. Extend `notification_service.py` with channel support
3. Configure per-user notification preferences in DynamoDB

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
