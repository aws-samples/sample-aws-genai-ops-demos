# Slack Integration Guide

## Overview

This solution supports two types of Slack integration:

| Type | URL Pattern | Use Case |
|------|-------------|----------|
| **Incoming Webhook** | `hooks.slack.com/services/...` | Direct message posting with rich formatting (blocks, attachments, colors) |
| **Workflow Trigger** | `hooks.slack.com/triggers/...` | Triggers a Slack Workflow that you customize with variables and steps |

The notifier Lambda auto-detects the URL type and sends the appropriate payload format.

## Option 1: Slack Workflow Trigger (Recommended)

Workflow triggers give you full control over the message format, routing, and additional actions (e.g., creating a Jira ticket, paging on-call).

### Step 1: Create a Slack Workflow

1. Open Slack → **Automations** (or click the ⚡ icon in the sidebar)
2. Click **New Workflow** → **Build Workflow**
3. Choose **Starts with a webhook** as the trigger
4. Name your workflow (e.g., "Health Event Alert")

### Step 2: Configure Webhook Variables

In the webhook trigger step, add these variables:

| Variable Name | Data Type | Description |
|---------------|-----------|-------------|
| `title` | Text | Alert header (e.g., "⚠️ Health Event Impact — All Teams") |
| `priority` | Text | Severity level: CRITICAL, HIGH, MEDIUM, LOW |
| `summary` | Text | One-line description of the impact |
| `findings` | Text | Affected resources with severity |
| `recommendations` | Text | Recommended actions list |
| `investigation_link` | Text | Direct URL to DevOps Agent investigation |

### Step 3: Add a "Send Message" Step

1. Click **Add Step** → **Send a message to a channel**
2. Select your target channel (e.g., `#ops-alerts`)
3. Use this message template:

```
{{title}}

*Priority:* {{priority}}
*Summary:* {{summary}}

*Affected Resources:*
{{findings}}

*Recommended Actions:*
{{recommendations}}

🔗 <{{investigation_link}}|View Full Investigation in DevOps Agent>
```

### Step 4: Publish and Copy the Webhook URL

1. Click **Publish**
2. Copy the webhook URL from the trigger step — it looks like:
   ```
   https://hooks.slack.com/triggers/E015GUGD2V6/1234567890/abcdef123456
   ```

### Step 5: Configure the Stack

The Slack webhook URL is stored in SSM Parameter Store SecureString. The setup wizard handles this automatically. For manual configuration:

```bash
aws ssm put-parameter \
  --name "/health-analyzer/production/slack-webhook-url" \
  --type SecureString \
  --value "https://hooks.slack.com/triggers/YOUR/TRIGGER/URL" \
  --overwrite
```

Or during initial deployment, the setup wizard prompts for the Slack webhook URL and stores it in SSM.

> **Note**: The Lambda reads the SSM parameter name from the `SLACK_WEBHOOK_PARAM_NAME` environment variable and fetches the actual URL at runtime with a 5-minute cache.

### Payload Sent to Workflow Trigger

```json
{
  "title": "⚠️ Health Event Impact — All Teams",
  "priority": "HIGH",
  "summary": "EC2 scheduled maintenance affects payment-service in us-east-1a",
  "findings": "• *[HIGH]* payment-service instances will be restarted\n  Resources: `i-0abc123def456789a`",
  "recommendations": "1. [HIGH] Pre-warm standby instances in us-east-1b",
  "investigation_link": "https://abc123.aidevops.global.app.aws/investigation/inv-456"
}
```

### Advanced: Add More Workflow Steps

After the message step, you can add additional actions:

- **Route by priority**: Add a conditional step — if `priority` is "CRITICAL", also send to `#incidents`
- **Create a ticket**: Add a Jira/ServiceNow step using the `summary` and `findings` variables
- **Page on-call**: Add an MS Teams urgent notification step for CRITICAL/HIGH priority
- **Update a spreadsheet**: Log the event to a Google Sheet or Notion database

---

## Option 2: Incoming Webhook (Simple)

For direct message posting without workflow customization.

### Step 1: Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App** → **From scratch**
3. Name: `Health Event Analyzer`, select your workspace
4. Click **Create App**

### Step 2: Enable Incoming Webhooks

1. In the app settings sidebar, click **Incoming Webhooks**
2. Toggle **Activate Incoming Webhooks** → ON
3. Click **Add New Webhook to Workspace**
4. Select the target channel (e.g., `#ops-alerts`)
5. Click **Allow**
6. Copy the webhook URL:
   ```
   https://hooks.slack.com/services/TXXXXXXXXX/BXXXXXXXXX/your-webhook-token-here
   ```

### Step 3: Configure the Stack

Same as Step 5 above — store the Slack webhook URL in SSM Parameter Store.

### Message Format (Automatic)

Incoming webhooks receive rich Slack Block Kit messages with:
- Color-coded severity (red for CRITICAL, orange for HIGH, yellow for MEDIUM)
- Structured sections for findings and recommendations
- Clickable investigation link
- Context footer with DevOps Agent attribution

---

## Per-Team Slack Channels

For team-specific routing, add Slack webhook URLs to the teams DynamoDB table:

```bash
aws dynamodb put-item \
  --table-name health-analyzer-teams \
  --item '{
    "teamId": {"S": "payments"},
    "teamName": {"S": "Payments Team"},
    "email": {"S": "payments@example.com"},
    "slackWebhookUrl": {"S": "https://hooks.slack.com/triggers/YOUR/PAYMENTS/WEBHOOK"},
    "notifyOn": {"SS": ["CRITICAL", "HIGH", "MEDIUM"]}
  }' \
  --no-cli-pager
```

Each team can have its own webhook (workflow trigger or incoming webhook). The notifier sends team-specific findings only to that team's channel.

### Recommended Channel Structure

```
#health-events          ← Default webhook (all events summary)
#payments-alerts        ← Payments team webhook (only their resources)
#platform-alerts        ← Platform team webhook
#security-incidents     ← Security team (CRITICAL/HIGH only via notifyOn filter)
```

---

## Testing

### Test the Notifier Directly

Invoke the notifier with a simulated investigation result:

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
    "investigationLink": "https://YOUR_SPACE.aidevops.global.app.aws/investigation/YOUR_INVESTIGATION_ID"
  }' \
  --cli-binary-format raw-in-base64-out \
  notifier-response.json \
  --log-type Tail \
  --no-cli-pager
```

### Verify the Response

```bash
cat notifier-response.json
```

Expected output:
```json
{
  "defaultChannel": {"sns": true, "slack": true},
  "teamNotifications": [],
  "defaultRouting": {
    "rootEmail": "Account Owner",
    "alternateContacts": [{"type": "OPERATIONS", "email": "ops@example.com", ...}],
    "notifiedEmails": ["ops@example.com"]
  }
}
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `"slack": false` | No webhook URL configured | Ensure SSM parameter `/health-analyzer/{env}/slack-webhook-url` exists and Lambda has `SLACK_WEBHOOK_PARAM_NAME` env var set |
| Slack returns 403 | Webhook URL expired or revoked | Regenerate in Slack app settings |
| Slack returns 400 | Payload format mismatch | Check if URL is `/triggers/` vs `/services/` — they need different formats |
| Workflow shows but no variables | Variables not configured in trigger | Edit workflow → webhook trigger → add variables |
| Message shows `{{variable}}` literally | Variable names don't match payload keys | Ensure names match exactly: `title`, `priority`, `summary`, `findings`, `recommendations`, `investigation_link` |
| No message in channel | Workflow not published | Click Publish in Workflow Builder |
