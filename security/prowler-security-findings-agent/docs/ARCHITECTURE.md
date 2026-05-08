# Architecture — Prowler + DevOps Agent + Bedrock Nova

## Goals

1. Run Prowler against the deployed AWS account on a schedule and on-demand from the UI.
2. Persist findings in a queryable, dashboard-ready store.
3. For the findings that matter (CRITICAL / HIGH / failing), produce an AI-generated remediation playbook with Amazon Bedrock Nova Pro, and feed it into Amazon DevOps Agent so the investigation starts with concrete next steps.
4. Expose the whole thing as a modern single-page dashboard.

## Data flow

```
                                      ┌─────────────────────────┐
  EventBridge (cron daily) ──────────►│                         │
                                      │   ECS Fargate Prowler   │──► S3 raw-reports/{scan}/*.ocsf.json
  POST /scans (from dashboard) ──────►│     (SecurityAudit +    │
                                      │      ViewOnlyAccess)    │──► Security Hub (ASFF, via -S flag)
                                      └─────────────────────────┘
                                                                                │
                                                                                ▼
                                                                    S3 ObjectCreated event
                                                                                │
                                                                                ▼
                                                            ┌────────────────────────────────────────┐
                                                            │   ingest-findings Lambda               │
                                                            │   Parse OCSF → DynamoDB BatchWriteItem │
                                                            └───────────┬────────────────────────────┘
                                                                        │
                                  severity ∈ {CRITICAL, HIGH}            │ all findings
                                  status == FAIL                         │
                                                                        │
                       ┌────────────────────────────┬────────────────────┤
                       │                            │                    │
                       ▼                            ▼                    ▼
    ┌─────────────────────────────┐      ┌───────────────────┐   ┌──────────────────────────┐
    │ remediation-context Lambda  │      │ SNS topic         │   │ DynamoDB findings table  │
    │ - Bedrock Converse Nova Pro │      │ (trigger)         │   │ GSI: severity-index      │
    │ - Writes markdown to S3     │      └─────────┬─────────┘   │ GSI: status-index        │
    │ - Updates DynamoDB with     │                │             └──────────────────────────┘
    │   remediation_s3_key         │               ▼                    ▲
    └─────────────────────────────┘    ┌──────────────────────┐          │
                                       │ devops-agent-trigger │          │
                                       │  Lambda (HMAC-SHA256)│          │
                                       └─────────┬────────────┘          │
                                                 │                       │
                                                 ▼                       │
                                       Amazon DevOps Agent               │
                                       webhook (payload includes         │
                                       Nova remediation markdown)        │
                                                                         │
                                                                         │
    React Dashboard (CloudFront + S3 + OAC)                              │
         │                                                               │
         │ SigV4 with Cognito Identity Pool credentials                  │
         ▼                                                               │
    dashboard-api Lambda (Function URL, IAM auth)────────────────────────┤
         │                                                               │
         ├─ GET /findings                                                │
         ├─ GET /findings/{uid} → presigned URL to remediation markdown ─┤
         ├─ GET /scans                                                   │
         └─ POST /scans → ecs:RunTask (Prowler on-demand) ───────────────┘
```

## Authentication & authorization

- Admin creates users in Cognito (no self-sign-up). Users sign in with email + password.
- The browser exchanges the User Pool ID token for temporary AWS credentials via the Identity Pool.
- Every API call is SigV4-signed against the Lambda Function URL (IAM auth).
- The authenticated IAM role only grants `lambda:InvokeFunctionUrl`. All DynamoDB / S3 / ECS access happens server-side through the `dashboard-api` Lambda's role.

## Data model

DynamoDB `prowler-security-findings` (one item per finding):

| Attribute | Type | Notes |
|---|---|---|
| `finding_uid` (PK) | S | Prowler's stable hash of check_id + resource_uid |
| `scan_id` | S | Timestamp of the scan that produced this snapshot |
| `severity` | S | CRITICAL / HIGH / MEDIUM / LOW / INFO |
| `status` | S | FAIL / PASS / MANUAL |
| `check_id`, `check_title`, `check_description`, `status_extended` | S | Prowler metadata |
| `service_name`, `resource_uid`, `region`, `account_id` | S | Subject of the finding |
| `compliance_frameworks` | L<S> | CIS / PCI / NIST / etc. inferred from OCSF unmapped.compliance |
| `last_seen_at` | S | ISO8601 of latest scan that reported this UID |
| `raw` | S | Truncated OCSF JSON (<350 KB) for forensics in the detail page |
| `remediation_s3_key`, `remediation_generated_at`, `remediation_model` | S | Present only for CRITICAL/HIGH after Nova generates a playbook |

Secondary indexes: `severity-index` (PK severity, SK last_seen_at) and `status-index` (PK status, SK severity) support the dashboard's most common queries without scanning.

## Prowler execution

- Official `toniblyx/prowler` image (built once into ECR by the CodeBuild project).
- Task role: `SecurityAudit` + `ViewOnlyAccess` managed policies plus a small `ProwlerExtras` statement for the permissions Prowler needs that aren't in those two (credential report generation, service last accessed, etc.).
- Command: `prowler aws --output-formats json-ocsf csv html -S` — `-S` additionally sends ASFF to Security Hub so findings are queryable there too.
- The entrypoint uploads `{account}.ocsf.json` (and human-readable csv/html) to `s3://raw-reports/raw-reports/{scan_id}/`.

## Bedrock prompt

System prompt fixes the playbook to three sections: **Impact**, **Root cause**, **Remediation steps** with bash and TypeScript (CDK v2) snippets. Temperature 0.2, max tokens 1500.

The user message includes the key OCSF fields (severity, check id, description, resource, service, region, compliance frameworks) plus the first 8 KB of raw OCSF. Truncation is intentional — Nova needs context but the check title + description usually tell it everything.

## DevOps Agent payload

The HMAC-signed POST body schema matches what the EKS demo uses (eventType/incidentId/priority/title/description), but:

- `incidentId` is `prowler-{finding_uid}` so the agent deduplicates if the same finding reappears.
- `description` embeds the Nova markdown when available (up to 20 KB).
- `data.remediationS3Key` is passed so the agent can fetch the full markdown directly if needed.

## Extending this demo

Ideas intentionally **out of scope** for the first version:

- **Multi-account scans** — Prowler supports `--role-arn` to assume a role per account. Extend the task definition env to accept an array of target accounts and loop.
- **Autonomous remediation** — today Nova only *describes* the fix. Wiring an additional AgentCore Runtime with action groups that actually apply the fix (behind an approval gate) is the natural phase 2.
- **Continuous drift detection** — rather than daily cron, use Config Rules as the trigger, scan only the changed resource, and update the single DynamoDB item.
