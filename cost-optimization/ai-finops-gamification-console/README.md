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

This demo relies on **two separate Slack integrations** — easy to conflate, but they serve different purposes:

1. **FinOps Agent's own Slack connection**: AWS-managed OAuth that lets the Agent *post* reports into a channel. One-way (post-only); the Agent cannot read replies or accept commands through it.
2. **This demo's ingestion bot**: a separate Slack app you create with a bot token, so the ingestion Lambda can *read* channel history via `conversations.history`. The Agent's own integration does not expose a reusable token, so this second app is required regardless.

**1. Create the FinOps Agent** (preview, `us-east-1` only)
- Sign in to the AWS Console, switch Region to US East (N. Virginia)
- Open the [AWS FinOps Agent console](https://docs.aws.amazon.com/finops-agent/latest/userguide/getting-started.html) and run the creation wizard (it provisions the required IAM roles automatically)

**2. Register the Agent's Slack integration**

This is a two-part flow: registering the integration (once per AWS account) and connecting a channel (once per agent). Don't confuse the two buttons in the console — **Add integration** does the account-level OAuth registration; **Add connection** (a separate step, done afterward) binds a specific Slack channel to your agent.

**2a. Register the integration** (account level, one-time per AWS account)
- Turn off multi-session mode in the console first (the OAuth flow fails while it's on)
- From the FinOps Agent console, choose **Add integration** → **Slack**
- The wizard has 3 steps: **Getting started** → **Authorize and connect** (redirects to Slack, approve the requested permissions) → **Complete**
- On completion you'll see the connected workspace name and an **Integration ID** (e.g. `Connected workspace: Crawlo`, `Integration ID: 98jsod5926hkomxssa77xnc7`) — note this down, you'll select it in the next step
- This integration is now available to any agent in the account

**2b. Get the Slack channel ID**
- In Slack, open the channel you want reports posted to (create one, e.g. `#finops-test`, if needed)
- Right-click the channel name → **View channel details** → copy the channel ID at the bottom (e.g. `C04ABCDEF12`) — you'll need this now and again later for the ingestion bot

**2c. Add a channel connection to your agent**
- Go to your agent's detail page → **Add connection** → **Slack**
- Select the Slack integration you registered in step 2a from the dropdown
- Paste the channel ID from step 2b
- Choose **Create**
- If the app hasn't been added to the channel yet, the console blocks you here with: *"You must add the AWS FinOps Agent app to the channel before connecting."* If you see this, go to step 2d and come back.

**2d. Add the AWS FinOps Agent app to the Slack channel**
- In Slack, open the channel → channel name → **Integrations** tab → **Add an App**
- Search for the app — its name is region-qualified, e.g. **"AWS FinOps Agent US EAST..."** (not just "AWS FinOps Agent"), so search for "FinOps" if the exact name doesn't autocomplete
- Add it, then return to step 2c and retry **Create**

See [Enable Slack with AWS FinOps Agent](https://docs.aws.amazon.com/finops-agent/latest/userguide/slack-integration.html) for the full reference.

**3. Create a separate Slack app for ingestion**

The AWS FinOps Agent Slack integration from step 2 is post-only by design — it posts reports but cannot read channel history back out. This demo needs its own Slack app, with its own bot token, to read what the Agent posted.

- [api.slack.com/apps](https://api.slack.com/apps) → **Create New App**
- In the "Create new app" dialog, Slack offers four starting points: **AI agent**, **Starter app**, **From a manifest**, and **Blank app**. Choose **Blank app** ("Empty app with minimal setup") — the others bundle AI/event/command features this app doesn't need; it only has to expose a bot token for reading history.
- Name it (e.g. `finops-gamification-ingestion`) and pick the same Slack workspace you used in step 2
- Left sidebar → **OAuth & Permissions** → **Bot Token Scopes** → add `channels:history` (public channel) or `groups:history` (private channel), plus `channels:read`
- Scroll up → button labeled **Install to Workspace** (it shows your actual workspace name, e.g. "Install to Crawlo") → Allow
- Copy the **Bot User OAuth Token** (`xoxb-...`)
- Invite this bot to the same channel used in step 2: `/invite @YourAppName`

At this point there are two Slack apps in the workspace, serving opposite one-way purposes:
| App | Created by | Direction | Purpose |
|---|---|---|---|
| AWS FinOps Agent [region] | AWS (step 2a) | Write-only | Posts reports into the channel |
| Your ingestion app (this step) | You | Read-only | Lets this demo's Lambda read those posts back out |

**Common error: `{"ok": false, "error": "not_in_channel"}`**

Installing the app to the workspace does not automatically add its bot user to any channel. If a test API call returns this error, the bot hasn't actually joined the channel yet:
- In Slack, open the channel and run `/invite @YourAppName`, or use channel name → **Integrations** tab → **Add an App**
- If you added `channels:history`/`groups:history` *after* the first install, you must reinstall (click **Install to Workspace** again) for the new scope to take effect — adding a scope alone does not retroactively apply it

**Verify what the ingestion Lambda will actually receive**

Before wiring the token into this demo, confirm what a real report message looks like via the raw API (not just what's visually rendered in Slack):

```bash
curl -s "https://slack.com/api/conversations.history?channel=YOUR_CHANNEL_ID&limit=5" \
  -H "Authorization: Bearer xoxb-YOUR-TOKEN" | python3 -m json.tool
```

Confirmed finding from testing this against a real report (August 2026 test run): **the report content is not in the `text` field.** The Agent posts using Slack Block Kit, and `text` is a truncated fallback string, e.g.:

```json
"text": "## AWS Cost Report — August 2026  **Total Spend:** $72.22 across 21 services | **Anomalies (>10%):*…"
```

The actual report — the "Top 10 Services by Spend" and "Cost Anomalies" tables, and critically the **Optimization Recommendations** list this demo's ingestion depends on — lives entirely in the message's `blocks` array as structured Block Kit elements (`header`, `table`, and `rich_text` blocks with a `rich_text_list`). Each recommendation is a `rich_text_list` item built from typed text runs, for example:

```json
{"type": "text", "text": "Stop idle RDS instance", "style": {"bold": true}},
{"type": "text", "text": " — "},
{"type": "text", "text": "devops-agent-eks-dev-postgres", "style": {"code": true}},
{"type": "text", "text": " (eu-west-1) — "},
{"type": "text", "text": "$13.91/month", "style": {"bold": true}},
{"type": "text", "text": " | Effort: Low"}
```

This means `ingestion_handler.py`'s original approach — regex matching against `message.get('text', '')` — never had a chance to work against real reports, regardless of pattern phrasing. **This has been fixed**: `parse_finops_report()` now walks the `blocks` array directly and extracts each `rich_text_list` item under the "Optimization Recommendations" header, rather than regexing the truncated `text` field. The old text-regex logic (`FINDING_PATTERNS`, `parse_finops_report_from_text()`) is kept only as a fallback for messages that arrive with no `blocks` at all (e.g. a manually typed test message).

Also observed: the message had `"reply_count": 1` with the reply posted by the same bot user, alongside a "Full interactive report attached" line in the blocks. Confirmed via `conversations.replies`: the reply is the uploaded `.html` artifact file (empty `text`, one entry in `files`), not additional report content. `conversations.history` never returns reply content on its own — reading it requires a separate `conversations.replies` call with the message's `ts` as `thread_ts`. Not required for the findings extracted above (those are already in the top-level message's `blocks`), but relevant if a future version wants to ingest the full interactive report.

**Validated parsing example** (from a real report, run through the actual `parse_finops_report()` function):

| Title | Service | Savings/mo | Priority | Effort |
|---|---|---|---|---|
| Stop idle RDS instance | AmazonRDS | $13.91 | low | Low |
| Migrate EC2 to Graviton | AmazonEC2 | $5.78 | low | Very High |

Both recommendations parsed with correct savings amount, service classification, region, and resource ID extraction (it correctly pulled `i-07c84273c3f3237b7` out of the free-text resource description).

**4. Schedule the report** (natural language, in the Agent's chat — not code)
```
Every Monday at 9 AM, generate a cost report with top 10 services,
anomalies over 10%, and optimization recommendations, and post it
to #your-finops-channel.
```
This creates a recurring automation. Posting to Slack does not require approval (unlike Jira ticket creation). See [Task management](https://docs.aws.amazon.com/finops-agent/latest/userguide/task-management.html).

**5. Note the Slack channel ID** for the ingestion configuration (right-click channel name → **View channel details**, ID is at the bottom, e.g. `C04ABCDEF12`)

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

Run these steps in order after `deploy-all.sh`/`deploy-all.ps1` finishes. Replace `us-east-1` and the resource IDs with your own deployment's output values (`WebsiteUrl`, `UserPoolId`, `SlackSecretArn`, etc. — printed in the deployment summary, or retrievable anytime via `aws cloudformation describe-stacks --stack-name FinOpsGamificationConsole-<region> --query Stacks[0].Outputs`).

**1. Create an admin user**

`given_name` and `family_name` are required Cognito attributes for this user pool — omitting them fails with `InvalidParameterException`.

```bash
aws cognito-idp admin-create-user \
  --user-pool-id <USER_POOL_ID> \
  --username admin@example.com \
  --user-attributes Name=email,Value=admin@example.com Name=given_name,Value=Admin Name=family_name,Value=User \
  --temporary-password 'TempPass123!'
```

**2. Add the user to the admin group**

```bash
aws cognito-idp admin-add-user-to-group \
  --user-pool-id <USER_POOL_ID> \
  --username admin@example.com \
  --group-name finops-admin
```

**3. Store your Slack ingestion bot token in Secrets Manager**

This is the app you created in step 3 of "FinOps Agent Setup" above (`xoxb-...` token) — not the AWS-managed FinOps Agent Slack integration. Get `<SLACK_SECRET_ARN>` from the deployment output.

```bash
aws secretsmanager put-secret-value \
  --secret-id "<SLACK_SECRET_ARN>" \
  --secret-string '{"token":"xoxb-your-real-token"}'
```

**4. Set the Slack channel ID**

CDK deliberately leaves `SLACK_CHANNEL_ID` empty on the ingestion Lambda so no channel is hardcoded at deploy time. `update-function-configuration` replaces the entire `Environment.Variables` map (it does not merge), so fetch the current values first and add your channel ID to them:

```bash
REGION=us-east-1   # match your deployment region

aws lambda get-function-configuration \
  --function-name "FinOpsIngestion-$REGION" \
  --query 'Environment.Variables' --output json > /tmp/finops-env.json

python3 -c "
import json
env = json.load(open('/tmp/finops-env.json'))
env['SLACK_CHANNEL_ID'] = 'C0XXXXXXXXX'  # your channel ID
json.dump({'Variables': env}, open('/tmp/finops-env-new.json', 'w'))
"

aws lambda update-function-configuration \
  --function-name "FinOpsIngestion-$REGION" \
  --environment file:///tmp/finops-env-new.json

rm /tmp/finops-env.json /tmp/finops-env-new.json
```

**5. Invite the ingestion bot to the Slack channel** (if not already done)

In Slack: `/invite @your-ingestion-app-name` in the channel matching the ID from step 4.

**6. Test ingestion manually** (don't wait for the hourly schedule)

```bash
aws lambda invoke \
  --function-name "FinOpsIngestion-$REGION" \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/ingestion-response.json
cat /tmp/ingestion-response.json

aws logs tail "/aws/lambda/FinOpsIngestion-$REGION" --since 5m
```

Expect `findingsCreated` > 0 in the response if the connected Slack channel has a FinOps Agent report with at least one optimization recommendation. If you see a `"No recommendations extracted from blocks..."` warning in the logs, the report's Block Kit format has changed since this parser was last validated — see the "Verify what the ingestion Lambda will actually receive" section above for how to inspect the raw payload.

**7. Confirm the finding landed in DynamoDB**

```bash
aws dynamodb scan --table-name "finops-findings-$REGION" --max-items 5
```

**8. Enable the hourly ingestion schedule** (once manual testing looks correct)

```bash
aws events enable-rule --name "finops-ingestion-schedule-$REGION"
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
