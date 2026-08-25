#!/bin/bash
# Seed the teams DynamoDB table with sample team configurations.
# Usage: ./scripts/seed-teams.sh [TABLE_NAME]

TABLE_NAME="${1:-health-analyzer-teams}"

echo "Seeding teams table: $TABLE_NAME"

# Payments team â€” critical service, gets MS Teams + Slack for CRITICAL/HIGH
aws dynamodb put-item \
  --table-name "$TABLE_NAME" \
  --item '{
    "teamId": {"S": "payments"},
    "teamName": {"S": "Payments Team"},
    "email": {"S": "payments-oncall@example.com"},
    "slackWebhookUrl": {"S": "https://hooks.slack.com/services/T00/B00/payments"},
    "slackChannel": {"S": "#payments-alerts"},
    "msTeamsWebhookUrl": {"S": "https://your-org.webhook.office.com/webhookb2/payments-channel"},
    "notifyOn": {"SS": ["CRITICAL", "HIGH", "MEDIUM"]}
  }'

# Data/Analytics team â€” medium criticality
aws dynamodb put-item \
  --table-name "$TABLE_NAME" \
  --item '{
    "teamId": {"S": "data-team"},
    "teamName": {"S": "Data & Analytics"},
    "email": {"S": "data-team@example.com"},
    "slackWebhookUrl": {"S": "https://hooks.slack.com/services/T00/B00/data"},
    "slackChannel": {"S": "#data-alerts"},
    "notifyOn": {"SS": ["CRITICAL", "HIGH"]}
  }'

# Identity/Auth team â€” high criticality
aws dynamodb put-item \
  --table-name "$TABLE_NAME" \
  --item '{
    "teamId": {"S": "identity-team"},
    "teamName": {"S": "Identity & Auth"},
    "email": {"S": "identity-oncall@example.com"},
    "slackWebhookUrl": {"S": "https://hooks.slack.com/services/T00/B00/identity"},
    "slackChannel": {"S": "#identity-alerts"},
    "msTeamsWebhookUrl": {"S": "https://your-org.webhook.office.com/webhookb2/identity-channel"},
    "notifyOn": {"SS": ["CRITICAL", "HIGH", "MEDIUM"]}
  }'

# Platform/Infra team â€” gets everything (they own shared infra)
aws dynamodb put-item \
  --table-name "$TABLE_NAME" \
  --item '{
    "teamId": {"S": "platform"},
    "teamName": {"S": "Platform Engineering"},
    "email": {"S": "platform@example.com"},
    "slackWebhookUrl": {"S": "https://hooks.slack.com/services/T00/B00/platform"},
    "slackChannel": {"S": "#platform-ops"},
    "msTeamsWebhookUrl": {"S": "https://your-org.webhook.office.com/webhookb2/platform-channel"},
    "notifyOn": {"SS": ["CRITICAL", "HIGH", "MEDIUM", "LOW"]}
  }'

echo "Done! Seeded 4 teams."
