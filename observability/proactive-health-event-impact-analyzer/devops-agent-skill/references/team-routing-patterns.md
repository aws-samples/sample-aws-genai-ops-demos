# Team Routing Patterns

## Resource Tag Conventions for Team Identification

When determining which team owns a resource, check these common tag patterns:

| Tag Key | Example Value | Purpose |
|---------|---------------|---------|
| `Team` | `payments`, `platform` | Primary team ownership |
| `Owner` | `payments-team@example.com` | Direct contact |
| `Department` | `engineering-payments` | Organizational unit |
| `CostCenter` | `CC-1234` | Financial ownership |
| `slack-channel` | `#payments-alerts` | Slack notification target |
| `msteams-channel` | `payments-alerts` | MS Teams notification target |
| `oncall-email` | `payments-oncall@example.com` | On-call rotation email |
| `notification-priority` | `critical`, `high`, `low` | Override default priority |

## Stack Name Patterns

CloudFormation stack names often encode team ownership:

- `{team}-{service}-{env}` → e.g., `payments-api-prod`
- `{org}/{team}/{service}` → e.g., `engineering/payments/processor`

## Notification Priority Matrix

| Workload Criticality | Event Category | Notification Priority |
|---------------------|----------------|----------------------|
| CRITICAL | issue | P1 — Immediate (MS Teams + Slack + Email) |
| CRITICAL | scheduledChange | P2 — Urgent (Slack + Email) |
| HIGH | issue | P2 — Urgent (Slack + Email) |
| HIGH | scheduledChange | P3 — Normal (Email) |
| MEDIUM | issue | P3 — Normal (Email) |
| MEDIUM | scheduledChange | P4 — Low (Email digest) |
| LOW | any | P4 — Low (Email digest) |

## Multi-Team Notification Rules

When an event affects multiple teams:

1. **Shared infrastructure**: Notify the platform/infrastructure team AND all consuming teams
2. **Dependency chains**: Notify upstream teams (they may need to reroute) AND downstream teams (they may see degradation)
3. **Cross-team coordination**: If 3+ teams are affected, also notify the engineering leadership channel for coordination

## Default Routing Fallback

When **no team routing configuration** is found (empty teams table or no matching team IDs from investigation findings), the system uses a default routing strategy:

### Resolution Order
1. **AWS Account Alternate Contacts** — Operations, Security, and Billing contacts configured in the AWS Account settings
2. **SNS Topic Subscribers** — The catch-all SNS topic (if an email was provided during deployment)
3. **Slack Webhook** — The default Slack webhook (if configured during deployment)

### When Default Routing Activates
- No entries in the `health-analyzer-teams` DynamoDB table
- DevOps Agent investigation findings don't include `owningTeam` fields
- Team IDs from findings don't match any entries in the teams table

### Notification Content Differences
Default routing notifications include:
- A clear banner indicating "DEFAULT ROUTING" was used
- A recommendation to configure team-specific routing
- All findings (not filtered by team)
- Full recommendations list

### Configuring Alternate Contacts
AWS Account alternate contacts are the primary fallback target. Configure them via:

```bash
# Operations contact (receives Health event notifications)
aws account put-alternate-contact \
  --alternate-contact-type OPERATIONS \
  --name "Platform Team" \
  --email-address "platform-ops@example.com" \
  --phone-number "+1-555-0100" \
  --title "Platform Engineering Lead"

# Security contact (receives security-related Health events)
aws account put-alternate-contact \
  --alternate-contact-type SECURITY \
  --name "Security Team" \
  --email-address "security@example.com" \
  --phone-number "+1-555-0200" \
  --title "Security Operations Lead"
```
