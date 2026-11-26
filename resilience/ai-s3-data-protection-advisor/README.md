# AI S3 Data Protection Advisor
*Score every Amazon S3 bucket against three data protection risks — accidental loss, ransomware, regional disruption — with a durable scan pipeline and Amazon Nova, then review the gaps in a Cloudscape dashboard*

## Overview

Amazon S3 data protection answers three risks: human or application error, malicious activity, and regional disruption. Which of those a given bucket is protected from depends on the combination of versioning, delete guards, Object Lock, replication, and backup — and the right combination depends on the bucket's role (production, logs, backup landing zone, build artifacts, …). Reviewing hundreds of buckets by hand is the usual bottleneck.

This demo runs a read-only scan of one account, or of every account of an AWS Organization, as one checkpointed [Lambda durable function](https://docs.aws.amazon.com/lambda/latest/dg/durable-functions.html). Deterministic rules collect bucket settings, infer bucket roles from tags, names and relationships, and score coverage per threat against the role-expected level. Amazon Nova Lite classifies the bucket roles rules cannot decide and writes the narrative text (executive summary, governance summary, finding descriptions). The results land in a React + Cloudscape dashboard with tabs for Buckets, Risk Review, Recommendations, Inconsistencies, Governance and Cost Optimization, plus Excel and self-contained HTML exports.

Numbers stay deterministic: two scans of the same estate produce the same scores. Generative AI only adds text and classifies buckets no rule could decide.

## At a Glance

- **Duration**: ~5 minutes deployment (direct code deploy — no container build)
- **Difficulty**: Intermediate
- **Target Audience**: Cloud architects, SREs, Security and Resilience engineers, AWS TAMs
- **Key Technologies**: Amazon S3, AWS Backup, Lambda durable functions, Amazon Nova, Amazon API Gateway (HTTP API), Amazon Cognito, Amazon CloudFront, Cloudscape Design System
- **Estimated Cost**: cents per month idle; ~$0.50 – $2 per scan of 1,000 buckets (mostly CloudWatch `GetMetricData` and Nova Lite) — see [Cost Estimate](#cost-estimate)

## Architecture

The browser holds no AWS credentials. The React SPA calls an HTTP API protected by a Cognito JWT authorizer; the API Lambda starts (or adopts) the durable scan execution and reads the run records. The durable pipeline Lambda runs the scan as seven checkpointed steps, so a mid-run failure resumes rather than restarts. Reports are written to a private S3 bucket and served through short-lived presigned URLs; CloudFront fronts the dashboard with origin access control and a content security policy.

See [docs/design.md](docs/design.md) for the full design and the rationale behind each decision.

## Prerequisites

- AWS CLI v2.31.13+ ([Installation Guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html))
- Node.js 20+
- Python 3.12+
- AWS CDK 2.1030.0+
- AWS credentials configured with permissions to deploy CloudFormation, Lambda, S3, DynamoDB, Cognito, API Gateway, CloudFront, Bedrock and IAM
- Amazon Bedrock access to Amazon Nova Lite enabled in your target region ([Model access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html))
- Organization scans only (`--multi-account`): deploy from the management account or a delegated administrator, with trusted access between AWS Organizations and CloudFormation StackSets. The deploy script checks both, read-only, and prints the management-account command for anything missing

The deploy script verifies your credentials with `aws sts get-caller-identity`; admin access is required to deploy the demo. The scan itself runs with a read-only role.

## Quick Start

### Deploy

**Windows (PowerShell):**
```powershell
.\deploy-all.ps1
```

**macOS/Linux:**
```bash
chmod +x deploy-all.sh destroy-all.sh
./deploy-all.sh
```

One command deploys the five CDK stacks (Data → Auth → Pipeline → Api → Frontend) through the shared deployment scripts and the React frontend built with the API URL and Cognito config. It prints the CloudFront dashboard URL, the API URL, the region, and the Cognito user pool id.

Optional switches:

| PowerShell | Bash | What it adds |
|------------|------|--------------|
| `-AdminEmail <email>` | `--admin-email <email>` | Creates the first dashboard user. Cognito emails a temporary password; the first sign-in asks for a new one. |
| `-MockData` | `--mock-data` | Creates a sandbox-only mock estate (tagged buckets and a role) so the first scan finds known gaps. Sandbox accounts only. See [Mock estate (optional)](#mock-estate-optional). |
| `-MultiAccount` (with optional `-OrgTargets r-xxxx,ou-...`) | `--multi-account` (with optional `--org-targets ...`) | Lets **Start scan** cover the whole AWS Organization: runs a read-only organization preflight, then rolls a read-only spoke role out to the member accounts with a StackSet. See [Multi-account scanning](#multi-account-scanning). |
| `-Accounts <id>,<id>` | `--accounts <id>,<id>` | Scans the listed member accounts instead of listing the organization, for estates without Organizations access. You deploy the spoke role in each account by hand. See [Without Organizations access](#without-organizations-access). |

**Windows (PowerShell):**
```powershell
.\deploy-all.ps1 -AdminEmail admin@example.com
.\deploy-all.ps1 -AdminEmail admin@example.com -MockData
```

**macOS/Linux:**
```bash
./deploy-all.sh --admin-email admin@example.com
./deploy-all.sh --admin-email admin@example.com --mock-data
```

### Create a dashboard user (if you did not pass `--admin-email`)

The deployment output prints the command. Replace the email:

```bash
aws cognito-idp admin-create-user \
  --user-pool-id <USER_POOL_ID> \
  --username admin@example.com \
  --user-attributes Name=email,Value=admin@example.com Name=email_verified,Value=true \
  --desired-delivery-mediums EMAIL --region <REGION> --no-cli-pager
```

### Use the dashboard

1. Open the CloudFront URL from the deployment output
2. Sign in with the credentials you created
3. Choose **View sample report** to walk the report against the bundled AnyCompany estate, or **Start scan** to run the durable pipeline against your own account. On a multi-account deployment, **Start scan** offers **Scan the organization** and **Scan this account**
4. Watch the seven steps advance on the run page, then the outcome of each account; a failure fails its own account or unit, never the whole run
5. Open the report when the scan finishes. The Buckets tab is the main surface: filter by account, region, role, status and priority; expand a row to see the threat × control matrix and the evidence; download Excel or a self-contained HTML copy

## The scan pipeline (durable 7-step)

A single **Start scan** runs as one Lambda durable execution. The dashboard renders these step labels as it polls the execution history:

1. **Determine the accounts to scan** — this account (the hub), or for an organization scan the hub plus every active member account (or the accounts listed with `--accounts`). When the organization cannot be listed, the scan covers the hub alone and the report says why
2. **Find the regions in use** — one durable step per account: Cost Explorer S3 spend by region, merged with bucket locations. A denied Cost Explorer call falls back to bucket locations and is recorded. A member account whose spoke role cannot be assumed is reported as not scanned here
3. **Collect bucket settings** — one durable unit per (account, region, batch of 200 buckets). Per bucket: versioning, Object Lock, replication, encryption, lifecycle, Block Public Access, policy, policy status, ownership controls, tagging, logging, notification, plus CloudWatch 30-day `BucketSizeBytes` and `NumberOfObjects` per storage class. Per region: AWS Backup protected resources, plans and vault lock. Per account: third-party backup signals (IAM role names, bucket names, CloudFormation stacks)
4. **Classify bucket roles** — rules first (tags, name patterns, replication and backup relationships, first match wins). Buckets no rule decides go to Nova Lite in batches of 50 with structured output (`role`, `confidence`, `reason`). Only names, tags and relationships are sent, never object data
5. **Score** — coverage per threat, role-expected protection, priority score and tier, priority focus, bucket families and inconsistencies, critical exposures, versioning cost estimate, governance maturity levels, cost optimization items, risk review
6. **Write the narrative** — Nova writes the executive summary, the governance summary, the current and next level of each governance dimension, and plain-language finding descriptions, from the step 5 output only. Every number in generated text must appear in its input; on failure the rule-written text is kept and the item is marked "generative AI text unavailable"
7. **Publish** — write `data.json`, `report.xlsx` (one sheet per tab, text never evaluated as a formula) and a self-contained `report.html`; update the run record

Each account and each (account, region, batch) is a checkpointed unit, so a failure on one account or unit fails only that account or unit — the run reports it and continues. A denied or failed API call records the setting as **unknown**, never as "not configured"; an explicit absence response (for example `ReplicationConfigurationNotFoundError`) is evidence of absence.

One scan at a time. Retries use the SDK's adaptive mode for throttling.

## Multi-account scanning

Single account is the default: a deployment without `--multi-account` never lists the organization and its pipeline role has no organization or assume-role permissions.

With `--multi-account`, the account you deploy into is the **hub**. An organization scan reads the hub with the pipeline's own role and every active member account through a read-only **spoke role** with a fixed name, `S3DataProtectionScanRole`:

- **The hub lists the organization's accounts** with `organizations:ListAccounts`, so it must be the management account or a delegated administrator for AWS Organizations. Suspended and closed accounts are skipped.
- **The spoke role carries the scan's read-only actions only**, the same list as the hub (`backend/s3dp/permissions.py`, mirrored in `cdk/lib/scan-permissions.ts`). It cannot list the organization or assume other roles. It trusts the hub account root, with an `aws:PrincipalArn` condition on the hub pipeline role `S3DataProtectionHub-<region>`.
- **The rollout is a service-managed, auto-deploying StackSet** (`S3DataProtectionOrg-<region>`) over the organization root, or the roots and OUs passed with `--org-targets`. The hub is excluded, because it scans itself. Accounts that join later get the role automatically. The spoke template is synthesized bootstrapless, so member accounts need no CDK bootstrap. From a StackSets delegated administrator the StackSet is created with `CallAs: DELEGATED_ADMIN`, from the management account with `CallAs: SELF`; the deploy script picks the right one.
- **Blast radius is one account.** A member account whose spoke role cannot be assumed (not rolled out yet, trust mismatch) is reported as **not scanned** with the error code; its account-level settings show as not readable, never as compliant, and every other account is scanned as usual.
- **Not collected yet:** SCPs and organization backup and tag policies. The Governance tab shows them as not readable from this account.

`--multi-account` starts with the read-only preflight `shared/scripts/check-org-access.*`, then branches on its result:

| Preflight result | What the deploy does |
|---|---|
| The hub can list accounts and run service-managed StackSets | Deploys the hub with the organization scope, then the Org stack |
| The hub can list accounts but cannot run StackSets | Deploys the hub with the organization scope and prints the one command a management-account administrator runs to deploy the Org stack (`orgOnly=true`). Until then, member accounts are reported as not scanned |
| The hub cannot list the organization's accounts | Stops with the fix the preflight printed. Use `--accounts` instead (next section) |

An organization scan visits every member account, so it takes longer than a single-account one. The durable execution timeout is 8 hours on a multi-account deployment, 2 hours otherwise.

### Without Organizations access

`--accounts 111122223333,444455556666` (`-Accounts` in PowerShell) scans the listed member accounts instead of listing the organization. No Organizations access is needed. Deploy the spoke role in each listed account by hand, with that account's credentials, from the demo folder (the deploy script prints the command with your hub account id):

```bash
AWS_REGION=<HUB_REGION> AWS_DEFAULT_REGION=<HUB_REGION> ../../shared/scripts/deploy-cdk.sh --cdk-directory cdk --stack-name S3DataProtectionSpoke --skip-bootstrap --cdk-context spoke=true --cdk-context hubAccountId=<HUB_ACCOUNT_ID>
```

```powershell
$env:AWS_REGION = '<HUB_REGION>'; $env:AWS_DEFAULT_REGION = '<HUB_REGION>'; & ..\..\shared\scripts\deploy-cdk.ps1 -CdkDirectory cdk -StackName S3DataProtectionSpoke -SkipBootstrap -CdkContext @('spoke=true', 'hubAccountId=<HUB_ACCOUNT_ID>')
```

The hub region is part of the hub role name the spoke trusts, so set it to the region the hub is deployed in.

## The report

The report groups the gaps by the role the bucket plays, so a reader can decide where to invest first.

| Tab | What it shows |
|---|---|
| **Instructions** | How to read the report, what the scan did not check (per-account outcomes, denied calls), the assumptions made |
| **Buckets** | Summary strip + sortable, filterable inventory. Expand a row for the threat × control matrix (Human error, Malicious activity, Regional disruption) and the evidence. Property filter by account, region, role, status and priority; `*` wildcard exclude patterns stored in the browser per report |
| **Risk Review** | Buckets grouped by risk exposure — what the generative AI assistant recommends looking at first |
| **Recommendations** | The concrete changes to apply, ordered by impact and effort |
| **Inconsistencies** | Twin buckets with different settings (same name in two regions, same role tag with different versioning, …) |
| **Governance** | Account-wide signals: AWS Config, Security Hub, organization policies, Backup Audit Manager frameworks, restore testing |
| **Cost Optimization** | Versioning cost estimate, lifecycle gaps, storage class mix |

Every report carries a Cloudscape `Alert`: responses are generated by a generative AI assistant, verify before acting. AI-generated content uses the Cloudscape GenAI label pattern. Numbers come from the deterministic engine; only the surrounding text is generative.

Downloads are presigned Excel and HTML links; the HTML is self-contained so it can be emailed or attached to a review.

## Mock estate (optional)

The mock estate is sandbox-only test data so you can scan a known set of gaps without touching real buckets. `--mock-data` / `-MockData` creates twelve small private buckets named `s3dp-<account>-*`, tagged `s3dp-demo=mock`, in the deploy region and a second region (default `us-west-2`, or `us-east-1` when the deploy region is `us-west-2`), plus the IAM role `s3dp-demo-replication`. Each bucket carries one deliberate case — a production bucket never versioned, a Veeam landing zone without Object Lock, a cross-region replication pair, production twins in two regions with different settings, a bucket no rule decides (so the generative AI assistant has something to classify), and more.

Block Public Access stays on for every bucket; no policy grants access to `Principal: "*"`. The seed refuses an account whose alias reads as production, and runs in the commercial partition only. Removal is `destroy-all.sh --mock-data` / `.\destroy-all.ps1 -MockData`, which deletes only buckets carrying both the prefix and the tag, and the role only when tagged.

Storage sizes appear one to two days after seeding (daily CloudWatch metrics), so the first scan right after `--mock-data` reports zero bytes for the mock buckets; this is expected.

## CDK Stacks

All stack IDs include the region suffix for multi-region deployment support.

| Stack | Purpose | Key Resources | Dependencies |
|---|---|---|---|
| `S3DataProtectionData-{region}` | Reports and runs | S3 reports bucket (private, SSE, versioned), DynamoDB runs table. Main stack: carries the solution adoption tracking description | None |
| `S3DataProtectionAuth-{region}` | Authentication | Cognito user pool (self sign-up off), app client | None |
| `S3DataProtectionPipeline-{region}` | Durable scan | Lambda durable scan function (`live` alias), API Lambda, hub pipeline role `S3DataProtectionHub-{region}` (read-only scan actions; plus `organizations:ListAccounts` and `sts:AssumeRole` on the spoke role on a multi-account deployment), Bedrock `InvokeModel` grant scoped to the Nova Lite inference profile only | Data |
| `S3DataProtectionApi-{region}` | HTTP API | API Gateway HTTP API, Cognito JWT authorizer on every route | Auth, Pipeline |
| `S3DataProtectionFrontend-{region}` | Dashboard | S3 website bucket, CloudFront distribution (origin access control, security headers, CSP), `/api` forwarded to the HTTP API | Auth, Api |
| `S3DataProtectionOrg-{region}` | Multi-account rollout (`--multi-account` only) | Service-managed, auto-deploying StackSet placing the spoke role in member accounts. Bootstrapless, no tracking description | Pipeline (trusts its role) |
| `S3DataProtectionSpoke` | Spoke role deployed by hand (`--accounts` only) | `S3DataProtectionScanRole`, read-only. Bootstrapless, no tracking description | None |

The scan's IAM actions are kept in one source file (`backend/s3dp/permissions.py`) mirrored by the CDK stack (`cdk/lib/scan-permissions.ts`); the test suite checks every AWS call the backend makes is on that list. The demo never modifies a bucket.

## Project Structure

```
ai-s3-data-protection-advisor/
├── README.md
├── deploy-all.ps1                       # Deployment script (PowerShell)
├── deploy-all.sh                        # Deployment script (Bash)
├── destroy-all.ps1                      # Teardown script (PowerShell)
├── destroy-all.sh                       # Teardown script (Bash)
├── validation.yaml                      # Demo validation contract
├── docs/
│   └── design.md                        # Approved design and decisions
├── backend/                             # Python 3.13 Lambda bundle
│   ├── s3dp/
│   │   ├── ai/                          # Bedrock client, Nova classification, narrative writing
│   │   ├── collect/                     # Account / backup / metrics / S3 / scan collectors, organization
│   │   │                                #   account listing (org.py), spoke role sessions (session.py)
│   │   ├── derive/                      # Normalize raw answers, derive roles, vendors, findings, costs
│   │   ├── engine/                      # Deterministic scoring and governance
│   │   ├── report/                      # Excel and self-contained HTML writers
│   │   ├── service/                     # Durable pipeline, API handler, steps, store
│   │   ├── cli.py                       # CLI (seed, seed-check, seed-cleanup, scan)
│   │   ├── model.py                     # Shared data model
│   │   ├── permissions.py               # Scan IAM actions (single source, mirrored in CDK)
│   │   ├── run.py                       # End-to-end scan entry point
│   │   └── seed.py                      # Sandbox mock estate
│   ├── requirements.txt
│   └── tests/                           # pytest suite with recorded API responses
├── cdk/                                 # CDK TypeScript infrastructure (5 stacks, plus Org/Spoke for multi-account)
│   ├── bin/app.ts                       # Entry point with solution adoption tracking
│   ├── lib/
│   │   ├── data-stack.ts
│   │   ├── auth-stack.ts
│   │   ├── pipeline-stack.ts
│   │   ├── api-stack.ts
│   │   ├── frontend-stack.ts
│   │   ├── spoke-stack.ts               # Multi-account: read-only role in each member account
│   │   ├── org-stack.ts                 # Multi-account: StackSet rolling the Spoke stack out
│   │   ├── model-id.ts                  # Nova Lite CRIS prefix resolution
│   │   └── scan-permissions.ts          # Scan, organization and spoke role names (mirrors permissions.py)
│   ├── cdk.json
│   ├── package.json
│   └── tsconfig.json
├── frontend/                            # React + Cloudscape dashboard (Vite)
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── api.ts                       # HTTP API client (Cognito ID token)
│   │   ├── auth.ts                      # Cognito authentication
│   │   ├── config.ts                    # Runtime config loaded from /config.js
│   │   ├── components/                  # RunSteps + report tabs
│   │   ├── lib/                         # Pure helpers (buckets, exclude, format, runs, status, tabs)
│   │   └── pages/                       # Runs, Run, Sample, SignIn
│   ├── index.html
│   ├── package.json
│   └── vite.config.ts
├── sample-data/                         # Bundled AnyCompany sample report (fictitious estate)
│   ├── anycompany-inventory.json
│   └── anycompany-data.json
└── scripts/
    └── smoke_test.py                    # End-to-end verification against the deployed demo
```

## Cost Estimate

Pricing based on Amazon Nova Lite on-demand rates and on consumption pricing for the other services. Actual costs vary with the number of buckets and the number of regions in use.

### Idle (no scans running)

Cents per month. Lambda and DynamoDB are on-demand with near-zero idle cost; Cognito and CloudFront have free-tier allowances that cover demo usage; the reports bucket holds a handful of small JSON and HTML files.

### Scenario: one scan of 1,000 buckets across a few regions

| Item | Approximate cost |
|---|---|
| CloudWatch `GetMetricData` (per-bucket `BucketSizeBytes` and `NumberOfObjects` for 30 days) | $0.30 – $1.00 |
| Amazon Nova Lite (role classification for undecided buckets + narrative writing) | $0.10 – $0.50 |
| Lambda durable function (seven steps, ~1,000 buckets) | <$0.10 |
| HTTP API, Cognito, CloudFront | Negligible (free tier or cents) |
| **Total per scan** | **~$0.50 – $2** |

Scale the estimate linearly with the number of buckets and the number of regions scanned. An organization scan costs the sum of its accounts: the per-bucket CloudWatch reads dominate, and the spoke role and StackSet cost nothing. CloudWatch dominates when request metrics are enabled on many buckets; Nova Lite stays cheap because only names, tags and relationships are sent.

## Cleanup

Destroy the five stacks in reverse dependency order through the demo's teardown script:

**Windows (PowerShell):**
```powershell
.\destroy-all.ps1
# or with the mock estate
.\destroy-all.ps1 -MockData
```

**macOS/Linux:**
```bash
./destroy-all.sh
# or with the mock estate
./destroy-all.sh --mock-data
```

The reports bucket empties itself on destroy; the DynamoDB runs table is removed with the stack. When the Org stack exists in the hub, the teardown destroys it first, which deletes the StackSet and the spoke role in every member account. Two pieces stay with their owners: an Org stack a management-account administrator deployed (`orgOnly=true`) is destroyed by them with the same command plus `--destroy` (`-DestroyStack`), and a spoke role deployed by hand with `--accounts` is destroyed in its account with the spoke command plus `--destroy`. `--mock-data` / `-MockData` deletes only the buckets carrying both the mock prefix and the `s3dp-demo=mock` tag, and the IAM role only when tagged, before destroying the stacks.

## Design Decisions

Recorded here so the next presenter and the next contributor know why the demo is shaped the way it is. The full rationale lives in [docs/design.md](docs/design.md).

| Decision | Choice | Reason |
|---|---|---|
| Shape | Deployed solution (CDK), not a local tool or a DevOps Agent capability | Main pattern of the repository; DevOps Agent capabilities live in the Agent Tools repository, not here |
| Demo data | Real scan of the account, plus a bundled sample AnyCompany report, plus an optional mock estate for sandbox accounts (`--mock-data`) | A sandbox account has few buckets; presenters always have a rich report to show; the mock estate validates data gathering end to end against real AWS answers |
| Scope | Single account by default; organization scans with `--multi-account` (hub and spoke), or an explicit account list with `--accounts` | Real estates are multi-account; the hub-and-spoke shape, the read-only preflight and the StackSet rollout follow the repository's shared multi-account pattern |
| Organization policies | SCPs and organization backup and tag policies are not collected | Reported as not readable rather than absent, so no governance level is raised or lowered on data the scan did not read |
| Generative AI role | Classify undecided bucket roles + write summary, governance and finding text | Numbers stay deterministic; Nova adds value where rules are weak (untagged estates) |
| Frontend | React + Cloudscape, same tabs as the review report | Repository copy rules (no emoji, Cloudscape components); no third-party template code |
| Pipeline | One Lambda durable function, checkpointed steps | Same pattern as `ai-lambda-runtime-migration`; resumes after failure; per-(account, region, batch) blast radius |

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
