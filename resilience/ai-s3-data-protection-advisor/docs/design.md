# AI S3 Data Protection Advisor: design

Status: approved design, 2026-11-25. Source: an S3 data protection review method, turned into a
self-service demo that customers deploy in their own account.

## Goal

Answer, for every S3 bucket of an account or an AWS Organization: which of the three risks S3 data protection addresses
is this bucket protected from, is that the level its role requires, and what is the change to make. The three risks:

| Risk | Main controls |
|---|---|
| Human or application errors (accidental delete, overwrite) | Versioning, delete guard (bucket policy, MFA Delete), backup copy |
| Malicious activity (ransomware, compromised credentials) | Object Lock, AWS Backup vault lock, isolated copy |
| Regional disruption | Cross-region replication, backup copy in another region |

The scan is read-only. Scoring is deterministic (two scans of the same estate give the same result). Amazon Nova
classifies bucket roles the rules cannot decide and writes the narrative text.

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Shape | Deployed solution (CDK), not a local tool or a DevOps Agent capability | Main pattern of the repository; capabilities must live in the Agent Tools repository |
| Demo data | Real scan of the account, plus a bundled sample report (fictitious AnyCompany estate), plus an optional mock estate for sandbox accounts (`--mock-data`) | A sandbox account has few buckets; presenters always have a rich report to show; the mock estate validates data gathering end to end against real AWS answers |
| Scope | Single account by default, `--multi-account` (hub and spoke) in v1 | Real estates are multi-account; contributor guide pattern |
| Generative AI role | Classify undecided bucket roles + write summary, governance and finding text | Numbers stay deterministic; Nova adds value where rules are weak (untagged estates) |
| Frontend | React + Cloudscape, same tabs as the review report | Repository copy rules (no emoji, Cloudscape components); no third-party template code |
| Pipeline | One Lambda durable function, checkpointed steps | Same pattern as `ai-lambda-runtime-migration`; resumes after failure; per-account blast radius |

Internal sales and messaging content is out of scope (including business opportunity sizing), as are internal tool
references and any customer data. "Customer spotlight" becomes "Priority focus".

## Placement

- Folder: `resilience/ai-s3-data-protection-advisor/`; catalog entries in the root `README.md` and `llms.txt`.
- Branch: `feat/ai-s3-data-protection-advisor` from `origin/main`.
- Layout: `cdk/` (TypeScript app, `bin/app.ts`, `lib/*-stack.ts`, `lib/scan-permissions.ts`), `backend/` (Python 3.13
  Lambda code and tests), `frontend/` (Vite + React + Cloudscape), `sample-data/` (bundled AnyCompany `data.json`),
  `deploy-all.*`, `destroy-all.*`, `validation.yaml`, `README.md`, `ARCHITECTURE.md`, `.gitignore`.

## Infrastructure (CDK, TypeScript)

| Stack | Contents |
|---|---|
| `S3DataProtectionData-<region>` | Reports bucket (private, SSE, versioned, `runs/<run-id>/{inventory,data}.json`, `report.xlsx`, `report.html`), DynamoDB runs table. Main stack: carries the solution adoption tracking description |
| `S3DataProtectionAuth-<region>` | Cognito user pool, self sign-up off, one admin user created by the deploy script |
| `S3DataProtectionPipeline-<region>` | Durable scan function (`live` alias), read-only scan role, Bedrock `InvokeModel` on the Nova model only |
| `S3DataProtectionApi-<region>` | HTTP API, Cognito JWT authorizer on every route, API Lambda |
| `S3DataProtectionFrontend-<region>` | SPA on S3 behind CloudFront (origin access control, security headers, CSP); `config.js` generated at deploy |
| `S3DataProtectionSpoke` (bootstrapless) | `--accounts` only, deployed by hand in a member account (`-c spoke=true`): read-only role with a fixed name, no tracking tag |
| `S3DataProtectionOrg-<region>` (bootstrapless) | `--multi-account` only: service-managed, auto-deploying StackSet of the Spoke template, hub excluded, no tracking tag. A management account can deploy it for a hub with `-c orgOnly=true` |

API routes: `POST /scans` (409 while a run is in progress), `GET /scans`, `GET /scans/{id}` (status and steps from the
durable execution history), `GET /scans/{id}/report?format=json|xlsx|html` (presigned URL, short expiry),
`GET /sample` (bundled sample report).

Scripts: `deploy-all.sh` / `deploy-all.ps1` with `--multi-account [--org-targets r-...,ou-...]` /
`-MultiAccount [-OrgTargets ...]`, or `--accounts <id>,...` / `-Accounts` for an explicit account list without
Organizations access; `destroy-all.*` (destroys the Org stack first when it exists). `POST /scans` takes
`{"scope": "account" | "organization"}`; the organization scope is refused unless the deployment is multi-account. Both call `shared/scripts/check-prerequisites.*`; multi-account calls
`shared/scripts/check-org-access.*` first and branches on its exit code; every stack goes through
`shared/scripts/deploy-cdk.*`. Region from the shared utilities, never hardcoded. The scripts end with the standard
summary (URL, region, admin user).

`--mock-data` / `-MockData` (sandbox accounts only, opt-in): before the summary, the deploy script runs
`python -m s3dp.cli seed --yes` with the deploy credentials to create the mock estate (below), so the first scan has
known gaps to find. `destroy-all.*` with the same flag runs `seed-cleanup --yes` first. The pipeline and scan roles
never get the seed permissions.

### Mock estate (`backend/s3dp/seed.py`)

Twelve small private buckets named `s3dp-<account>-*`, tagged `s3dp-demo=mock`, in the deploy region and a
second region (default us-west-2, or us-east-1 when the deploy region is us-west-2), plus the IAM role
`s3dp-demo-replication`. Each bucket carries one deliberate case: production never versioned with access logs to a
log bucket, production by tag with versioning suspended and ACLs enabled, a Veeam landing zone without Object Lock, a
Rubrik landing zone with Object Lock and no default retention, a cross-region replication pair, production twins in
two regions with different settings (one logging to itself), a production bucket whose policy cannot be read (must
stay unconfirmed, never a finding), a non-production bucket by tag, and a bucket no rule decides (for the generative
AI assistant). Block Public Access stays on for every bucket; no policy grants access to `Principal: "*"`.
`seed-check --out <scan folder>` compares the scan's `inventory.json` with what each bucket must show (role,
versioning, Object Lock, replication, logging, policy readability, findings raised and not raised).
`seed-cleanup` deletes only buckets carrying both the prefix and the tag, and the role only when tagged. The seed
refuses an account whose alias reads as production. Commercial partition only; scan as an IAM principal, not the root user (root can always read
the hidden policy). Write permissions: `SEED_ACTIONS` in `permissions.py`. Cost:
cents (a few KB of objects, replication and access logs). Storage sizes appear one to two days after seeding (daily
CloudWatch metrics).

### Scan permissions (one source: `backend/s3dp/permissions.py`, mirrored by `cdk/lib/scan-permissions.ts`)

Read-only, used by both the hub scan role and the spoke role (`SCAN_ACTIONS`). IAM action names, with the API name in
parentheses where it differs. `tests/test_permissions.py` checks that every AWS call in the backend is in this list.

- STS: `sts:GetCallerIdentity`
- S3, per bucket: `s3:ListAllMyBuckets` (ListBuckets), `s3:GetBucketLocation`, `s3:GetBucketVersioning`,
  `s3:GetBucketObjectLockConfiguration` (GetObjectLockConfiguration), `s3:GetReplicationConfiguration`
  (GetBucketReplication), `s3:GetEncryptionConfiguration` (GetBucketEncryption), `s3:GetLifecycleConfiguration`
  (GetBucketLifecycleConfiguration), `s3:GetBucketPublicAccessBlock` (GetPublicAccessBlock), `s3:GetBucketPolicy`,
  `s3:GetBucketPolicyStatus`, `s3:GetBucketOwnershipControls`, `s3:GetBucketTagging`, `s3:GetBucketLogging`,
  `s3:GetBucketNotification` (GetBucketNotificationConfiguration)
- S3 Control, per account: `s3:GetAccountPublicAccessBlock` (GetPublicAccessBlock)
- IAM: `iam:ListAccountAliases`, `iam:ListRoles` (third-party backup detection)
- Cost Explorer: `ce:GetCostAndUsage` (region discovery, S3 spend)
- AWS Backup, per region: `backup:ListProtectedResources`, `backup:ListBackupPlans`, `backup:GetBackupPlan`,
  `backup:ListBackupSelections`, `backup:GetBackupSelection`, `backup:ListBackupVaults`,
  `backup:DescribeRegionSettings`, `backup:ListRestoreTestingPlans`, `backup:ListFrameworks`
- CloudFormation: `cloudformation:ListStacks`
- AWS Config: `config:DescribeConfigurationRecorders`, `config:DescribeConfigurationRecorderStatus`,
  `config:DescribeConfigRules`
- Security Hub: `securityhub:GetEnabledStandards`
- CloudWatch: `cloudwatch:ListMetrics`, `cloudwatch:GetMetricData`

Pipeline function only (`BEDROCK_ACTIONS`): `bedrock:InvokeModel` (Converse), on the Amazon Nova Lite inference
profile and its foundation model only.

Multi-account only, hub pipeline role only, granted when deployed with `--multi-account` (`ORG_ACTIONS` and
`ASSUME_ACTIONS`): `organizations:ListAccounts`, and `sts:AssumeRole` on the fixed-name spoke role
`S3DataProtectionScanRole` in any account. The spoke role carries `SCAN_ACTIONS` only. SCPs and
organization backup and tag policies are not collected yet (`organizations:ListPolicies` and
`organizations:DescribePolicy` are not granted): the report shows them as not readable, never as absent.

The demo never modifies a bucket. Spoke trust: the hub account root with an `aws:PrincipalArn` condition on the hub
pipeline role's fixed, region-suffixed name (`S3DataProtectionHub-<region>`).

## Pipeline (durable function)

Interface between collection and scoring: an `inventory.json` in the shape the existing sample engine reads.
The engine turns it into `data.json`. Each step's label is the string the
dashboard renders.

Between collection and scoring, a derive layer turns raw API answers into the engine vocabulary: setting
normalization, rule-based roles, third-party backup signatures, write pattern and versioning cost estimate, per-bucket
findings (the S3 data protection check catalog), cost optimization items and governance signals. The sample engine received these
pre-computed; the demo computes them.

1. **Determine the accounts to scan**: hub only by default; with multi-account, active Organizations members or an
   explicit list. Hub scanned with its own credentials, members through the spoke role.
2. **Find the regions in use**: Cost Explorer S3 spend by region, merged with bucket locations. If Cost Explorer is
   denied, bucket locations only, and the fallback is recorded.
3. **Collect bucket settings**: one durable unit per (account, region, batch of 200 buckets). Per bucket: the S3
   settings above; CloudWatch 30-day `BucketSizeBytes` and `NumberOfObjects` per storage class, request metrics when
   enabled. Per region: AWS Backup protected resources, plans, vault lock. Per account: third-party backup signals
   (IAM role names, bucket names, CloudFormation stacks). A denied or failed call records the setting as unknown,
   never as "not configured"; an explicit "not configured" answer (for example `ReplicationConfigurationNotFoundError`)
   is evidence of absence. Unit output goes to S3, not to the checkpoint. An unreachable account fails its own units
   only and is reported as not checked.
4. **Classify bucket roles**: rules first (tags, name patterns, replication and backup relationships, first match
   wins). Buckets still undecided go to Nova Lite (`get_bedrock_model_id()` in the CDK stack, passed as environment) in
   batches of 50, with structured output: `role`, `confidence`, `reason`. Only names, tags and relationships are sent,
   never object data. The reason is shown as evidence ("Role inferred by the generative AI assistant from name and
   tags"). Bedrock failure: role `unknown`, recorded in the run.
5. **Score**: port of the sample engine, deterministic: coverage per threat, role-expected protection, priority score
   and tier, priority focus, bucket families and inconsistencies, critical exposures, versioning cost estimate,
   governance maturity levels, cost optimization items, risk review.
6. **Write the narrative**: Nova writes the executive summary, the governance summary, each dimension's current and next
   level text, and plain-language finding descriptions, from the step 5 output only. Post-check: every number in a
   generated text must appear in its input; on failure the rule-written text is kept and the run is marked
   "generative AI text unavailable" for that item.
7. **Publish**: write `data.json`, `report.xlsx` (sheet per tab, text never evaluated as a formula) and a self-contained
   `report.html`; update the run record (counts, per-account outcomes, duration).

One run at a time. Retries: SDK adaptive mode for throttling.

## Dashboard (React + Cloudscape)

- **Runs** (home): Start scan (this account, or the Organization when deployed with multi-account), runs table (date,
  scope, buckets, accounts scanned / not checked, status), View sample report (labeled "Sample data, fictitious
  estate").
- **Run in progress**: the seven steps with status, per-account outcomes.
- **Report**: tabs Instructions, Buckets, Risk Review, Recommendations, Inconsistencies, Governance, Cost Optimization.
  - Buckets: summary figures; table with text filter, exclude patterns field (comma-separated, `*` wildcard, stored in
    the browser per report), filters by account, region, role, status, priority; sortable columns; expandable row with
    the threat × control matrix and the evidence.
  - Status shown with `StatusIndicator` (success / warning / error / pending) and a text label; threat columns named
    "Human error", "Malicious activity", "Regional disruption"; priority focus as a `Badge`.
  - Downloads: Excel and self-contained HTML through presigned links.
  - An `Alert` on every report: responses generated by a generative AI assistant, verify before acting.
- Copy follows the contributor guide: sentence case, no exclamation marks, no "click", no "please", no emoji.

## Error handling and honesty

- Per (account, region) outcome recorded and shown: scanned, denied (with the API), not checked.
- Unknown is never shown as missing; the Instructions tab lists checks not run and assumptions made.
- Every cost figure is labeled as an estimate with its basis.

## Security

- Every API route behind the Cognito JWT authorizer; no AWS credentials in the browser.
- Reports bucket private, accessed only through short-lived presigned URLs; CloudFront with origin access control and a
  content security policy.
- Read-only scan and spoke roles from one permissions file.
- UI escapes all report text; Excel cells stored as text.
- The builder-security guidance is applied before writing the API, the CloudFront distribution and the deploy scripts.

## Testing

- Engine unit tests on fixture inventories (coverage rules, roles, inconsistencies, cost estimates), ported from the
  existing risk-coverage tests.
- Collector tests with recorded API responses, including a denied call that must yield unknown.
- Narrative post-check test: a generated text with a number absent from its input is rejected.
- Frontend type-check and build; CDK synth test.
- Live gate before release: deploy, scan, destroy in a test account (single and multi-account), run by the builder.

## Cost (to confirm with the AWS Pricing API in the README)

Idle: cents per month (Lambda, DynamoDB on demand, S3, CloudFront, Cognito free tier). One scan of 1,000 buckets: about
$0.50 to $2, mostly CloudWatch `GetMetricData` and Nova Lite.

## Out of scope for v1

Automatic remediation, an AWS DevOps Agent skill, a chat assistant, scheduled scans.
