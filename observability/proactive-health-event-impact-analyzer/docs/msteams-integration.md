# Microsoft Teams Integration Guide

## Overview

This solution sends notifications to Microsoft Teams using **Adaptive Cards** — rich, interactive messages with color-coded priority, structured findings, and a clickable button to view the investigation in DevOps Agent.

## Setup Options

| Method | URL Pattern | Availability |
|--------|-------------|--------------|
| **Workflows (new Teams)** | `*.logic.azure.com:443/workflows/...` | Current method |
| **Incoming Webhook (classic)** | `*.webhook.office.com/webhookb2/...` | Being deprecated by Microsoft |

Both URL formats are supported by the notifier.

---

## Option 1: Workflows (Recommended — New Teams)

Microsoft is replacing Connectors with Power Automate Workflows. This is the recommended approach.

### Step 1: Create a Workflow

1. Open **Microsoft Teams**
2. Go to the channel where you want notifications
3. Click **⋯** (more options on the channel) → **Workflows**
4. Search for **"Post to a channel when a webhook request is received"**
5. Click the template → **Next**
6. Name: `Health Event Analyzer`
7. Select the **Team** and **Channel**
8. Click **Add workflow**

### Step 2: Copy the Webhook URL

After creation, you'll see the webhook URL. It looks like:

```
https://prod-XX.westus.logic.azure.com:443/workflows/XXXXXXXX/triggers/manual/paths/invoke?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=XXXXXXXX
```

Copy this URL — you'll need it for configuration.

### Step 3: Configure the Stack

The MS Teams webhook URL is stored in SSM Parameter Store SecureString. The setup wizard handles this automatically. For manual configuration:

```bash
aws ssm put-parameter \
  --name "/health-analyzer/production/msteams-webhook-url" \
  --type SecureString \
  --value "YOUR_TEAMS_WEBHOOK_URL" \
  --overwrite
```

> **Note**: The Lambda reads the SSM parameter name from the `MSTEAMS_WEBHOOK_PARAM_NAME` environment variable and fetches the actual URL at runtime with a 5-minute cache.

---

## Option 2: Incoming Webhook (Classic Teams)

> ⚠️ Microsoft is deprecating Connectors. Use Workflows (Option 1) for new setups.

### Step 1: Add Incoming Webhook Connector

1. In Microsoft Teams, right-click the channel
2. Select **Connectors** (or **Manage channel** → **Connectors**)
3. Find **Incoming Webhook** → click **Configure**
4. Name: `Health Event Analyzer`
5. Optionally upload a custom icon
6. Click **Create**

### Step 2: Copy the Webhook URL

```
https://TENANT.webhook.office.com/webhookb2/XXXXXXXX/IncomingWebhook/XXXXXXXX/XXXXXXXX
```

### Step 3: Configure

Same as Option 1, Step 3 — store the URL in SSM Parameter Store SecureString.

---

## Message Format: Adaptive Card

The notifier sends an **Adaptive Card** to Microsoft Teams with the following structure:

```
┌─────────────────────────────────────────────────┐
│ ⚠️ Health Event Impact — Platform Team          │  (color-coded header)
├─────────────────────────────────────────────────┤
│ Priority:   HIGH                                │
│ Summary:    EC2 scheduled maintenance affects   │  (facts table)
│             test-service in us-east-1a          │
│ Root Cause: Hardware degradation in AZ          │
├─────────────────────────────────────────────────┤
│ Affected Resources:                             │
│ - [HIGH] test-service instances will be         │  (findings)
│   restarted during maintenance window           │
│   Resources: i-0test123456789ab                 │
├─────────────────────────────────────────────────┤
│ Recommended Actions:                            │
│ 1. [HIGH] Pre-warm standby instances in         │  (recommendations)
│    us-east-1b                                   │
├─────────────────────────────────────────────────┤
│ [View Investigation in DevOps Agent]            │  (clickable button)
└─────────────────────────────────────────────────┘
```

### Card Features

- **Color-coded container**: Red (CRITICAL), Orange (HIGH), Yellow (MEDIUM), Blue (LOW)
- **FactSet**: Priority, summary, and root cause in a structured table
- **Findings section**: Affected resources with severity levels
- **Recommendations section**: Numbered action items
- **Action button**: Direct link to the DevOps Agent investigation

### JSON Payload (what the Lambda sends)

```json
{
  "type": "message",
  "attachments": [
    {
      "contentType": "application/vnd.microsoft.card.adaptive",
      "content": {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
          {
            "type": "Container",
            "style": "warning",
            "items": [
              {
                "type": "TextBlock",
                "text": "⚠️ Health Event Impact — All Teams",
                "weight": "Bolder",
                "size": "Large"
              }
            ]
          },
          {
            "type": "FactSet",
            "facts": [
              {"title": "Priority", "value": "HIGH"},
              {"title": "Summary", "value": "EC2 scheduled maintenance affects test-service"},
              {"title": "Root Cause", "value": "Hardware degradation in AZ us-east-1a"}
            ]
          },
          {
            "type": "TextBlock",
            "text": "**Affected Resources:**",
            "weight": "Bolder"
          },
          {
            "type": "TextBlock",
            "text": "- **[HIGH]** test-service instances will be restarted\n  Resources: `i-0test123456789ab`"
          },
          {
            "type": "TextBlock",
            "text": "**Recommended Actions:**",
            "weight": "Bolder"
          },
          {
            "type": "TextBlock",
            "text": "1. **[HIGH]** Pre-warm standby instances in us-east-1b"
          }
        ],
        "actions": [
          {
            "type": "Action.OpenUrl",
            "title": "View Investigation in DevOps Agent",
            "url": "https://abc123.aidevops.global.app.aws/investigation/inv-456"
          }
        ]
      }
    }
  ]
}
```

---

## Per-Team Microsoft Teams Channels

For team-specific routing, add the MS Teams webhook URL to the teams DynamoDB table:

```bash
aws dynamodb put-item \
  --table-name health-analyzer-teams \
  --item '{
    "teamId": {"S": "payments"},
    "teamName": {"S": "Payments Team"},
    "email": {"S": "payments@example.com"},
    "slackWebhookUrl": {"S": "https://hooks.slack.com/triggers/YOUR/SLACK/WEBHOOK"},
    "msTeamsWebhookUrl": {"S": "https://prod-XX.westus.logic.azure.com:443/workflows/YOUR/TEAMS/WEBHOOK"},
    "notifyOn": {"SS": ["CRITICAL", "HIGH", "MEDIUM"]}
  }' \
  --no-cli-pager
```

### Recommended Channel Structure

```
General Ops Channel     ← Default MS Teams webhook (all events)
Payments Alerts         ← Payments team webhook (only their resources)
Platform Alerts         ← Platform team webhook
Security Incidents      ← Security team (CRITICAL/HIGH only via notifyOn)
```

---

## Testing

### Invoke the Notifier Directly

```bash
aws lambda invoke \
  --function-name "HealthEventAnalyzerStack--InvestigationNotifierE3A-XXXX" \
  --payload '{
    "investigationStatus": "IMPACT_DETECTED",
    "summary": "EC2 scheduled maintenance affects test-service in us-east-1a",
    "rootCause": "Hardware degradation in AZ us-east-1a",
    "priority": "HIGH",
    "findings": [{
      "description": "test-service instances will be restarted during maintenance window",
      "severity": "HIGH",
      "affectedResources": ["i-0test123456789ab"],
      "owningTeam": "platform"
    }],
    "recommendations": [{
      "description": "Pre-warm standby instances in us-east-1b",
      "priority": "HIGH"
    }],
    "teamsToNotify": [],
    "sourceAccountId": "YOUR_ACCOUNT_ID",
    "investigationLink": "https://YOUR_SPACE.aidevops.global.app.aws/investigation/YOUR_ID"
  }' \
  --cli-binary-format raw-in-base64-out \
  notifier-response.json \
  --log-type Tail \
  --no-cli-pager
```

### Verify Response

```bash
cat notifier-response.json
```

Expected:
```json
{
  "defaultChannel": {"sns": true, "slack": true, "msTeams": true},
  ...
}
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `"msTeams": false` | No webhook URL configured | Ensure SSM parameter `/health-analyzer/{env}/msteams-webhook-url` exists and Lambda has `MSTEAMS_WEBHOOK_PARAM_NAME` env var set |
| Teams returns 400 | Invalid Adaptive Card JSON | Check Lambda logs for payload details |
| Teams returns 401/403 | Webhook URL expired or revoked | Regenerate in Teams Workflows |
| Card shows but no button | `investigationLink` is null | Ensure callback passes the link |
| No message appears | Workflow not active | Check Power Automate → ensure workflow is turned on |
| Message appears in wrong channel | Workflow configured for different channel | Edit workflow → change target channel |

---

## Comparison: Slack vs Microsoft Teams

| Feature | Slack | Microsoft Teams |
|---------|-------|-----------------|
| Message format | Block Kit (JSON blocks) | Adaptive Cards (JSON) |
| Color coding | Attachment color bar | Container style (attention/warning/default) |
| Clickable links | `<url|text>` markdown | `Action.OpenUrl` button |
| Workflow support | Workflow Triggers (`/triggers/`) | Power Automate Workflows |
| Direct webhook | Incoming Webhooks (`/services/`) | Incoming Webhook connector (deprecated) |
| Rich formatting | mrkdwn (Slack markdown) | Standard markdown in TextBlocks |
