# AWS Services Lifecycle Tracker

Automatically track AWS service deprecations, find the resources in your account that are affected, and manage remediation. The whole refresh (web extraction → account scan → reconcile → notify) runs as **one [AWS Lambda durable function](https://docs.aws.amazon.com/lambda/latest/dg/durable-functions.html) execution**, using hybrid HTML parsing + Amazon Nova AI normalization for the extraction step.

## 🚀 Key Features

- **🔁 One-click end-to-end Refresh**: A single durable execution extracts deprecation facts from the AWS documentation, scans your account for affected resources, reconciles the inventory and publishes a summary - checkpointed step by step, so a failing service or scanner never aborts the run
- **🔍 Account Resource Discovery**: Scans 11 services (Lambda, RDS/Aurora, EKS, ElastiCache, OpenSearch, MSK, DocumentDB, Neptune, Glue, Elastic Beanstalk, EC2) and keeps the inventory separate from the public deprecation facts
- **📋 Plan of Action**: Assign deprecations to team members with ownership, priority, target dates and notes
- **🤖 Hybrid AI Extraction**: BeautifulSoup HTML parsing + Amazon Nova AI normalization for reliable data extraction
- **🧠 Intelligent Status Categorization**: deprecated / extended_support / end_of_life based on retirement dates
- **🎛️ Admin Interface**: React + Cloudscape UI behind Cognito; all calls go through an HTTP API with a JWT authorizer (the browser holds no AWS credentials)
- **🩺 AWS Health integration**: Hourly poll of Health events correlated with tracked deprecations (paid Support plan required)
- **📦 No Docker, no container registry**: Python code is bundled locally with pip; deploys in a few minutes

## Interactive Demo

Experience this demo in an interactive click-through walkthrough:

▶️ [Launch Interactive Demo](https://app.storylane.io/share/jtv9je6phpy4)


## Architecture

```
                         AWS Services Lifecycle Tracker

┌────────────┐   ┌──────────────────────────────┐   ┌──────────────────────────────────┐
│ Admin user │──>│ Frontend stack               │──>│ Auth stack                       │
└────────────┘   │ CloudFront + S3 (React SPA)  │   │ Cognito User Pool (ID token)     │
                 └──────────────┬───────────────┘   └──────────────────────────────────┘
                                │ HTTPS + Authorization: Bearer <ID token>
                                ▼
                 ┌──────────────────────────────────────────────────────────────────────┐
                 │ Api stack: API Gateway HTTP API + Cognito JWT authorizer             │
                 │   POST /actions   POST /refresh   GET /refresh/{arn}                 │
                 └──────────────┬───────────────────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ Pipeline stack (main)                                                                │
│                                                                                      │
│  ┌─ aws-services-lifecycle-api (Lambda) ─────────────────────────────────────────┐   │
│  │  UI actions (reads/writes) · start/observe pipeline runs · hourly Health poll │   │
│  └───────────────┬───────────────────────────────────────────────────────────────┘   │
│                  │ lambda:Invoke (Event, DurableExecutionName)                       │
│                  ▼                                                                   │
│  ┌─ aws-services-lifecycle-pipeline:live (Lambda durable function, 2 h / 14 d) ──┐   │
│  │  start-run ─> map extract-<service> ─> map scan-<Scanner>-<region>            │   │
│  │            ─> reconcile-inventory ─> summarize-and-notify (SNS)               │   │
│  └───────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                      │
│  EventBridge Scheduler: weekly refresh · hourly Health    SNS topic · SQS DLQ        │
└──────────────────────────────┬───────────────────────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ Data stack (DynamoDB)                                                                │
│  aws-services-lifecycle (public deprecation facts)  · aws-account-inventory (yours)  │
│  service-extraction-config · service-extraction-state · deprecation-action-plans     │
│  aws-health-events                                                                   │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

**System flow:**
1. **Refresh** (UI button, weekly schedule, or CLI) starts one durable execution of the pipeline function
2. **Extract**: one checkpointed step per enabled service fetches the AWS docs and normalizes them with Amazon Nova (parallel, max 5)
3. **Scan**: one checkpointed step per scanner × region discovers your resources and matches them against the facts
4. **Reconcile**: this run's inventory is upserted and stale rows from successfully scanned scopes are removed
5. **Notify**: the run summary is published to SNS and stored as the execution result the UI shows

Facts and inventory live in separate tables: the public deprecation data is never mixed with what was found in your account.

## Quick Start

### Prerequisites
- **AWS CLI v2.33.22 or later** ([Installation Guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)) - durable function APIs need this version. Check with `aws --version`
- **Node.js 22+** and **AWS CDK CLI** (`npm install -g aws-cdk`, check with `cdk --version`)
- **Python 3.11+** with `pip` - used to bundle the Lambda code locally (no Docker needed)
- **AWS credentials** with permissions for CloudFormation, Lambda, API Gateway, DynamoDB, Cognito, EventBridge Scheduler, SNS, SQS, CloudFront, S3 and IAM
- **Amazon Bedrock** model access for Amazon Nova in your region
- **Paid AWS Support plan** (**Business, Enterprise On-Ramp, Enterprise, or Unified Operations**) - only for the AWS Health panel. Without it the Health poll gets a `SubscriptionRequiredException` and the panel stays empty; everything else works. See [What is AWS Health](https://docs.aws.amazon.com/health/latest/ug/what-is-aws-health.html)

### ⚠️ Region Requirements

Lambda durable functions and Amazon Nova must be available in your target region. Check the [Lambda durable functions](https://docs.aws.amazon.com/lambda/latest/dg/durable-functions.html) and [Bedrock model support by region](https://docs.aws.amazon.com/bedrock/latest/userguide/models-regions.html) pages. The deploy scripts use your configured AWS CLI region (`AWS_REGION` / `aws configure get region`).

### One-Command Deploy

**Windows (PowerShell):**
```powershell
.\deploy-all.ps1
```

**macOS/Linux (Bash):**
```bash
chmod +x deploy-all.sh scripts/build-frontend.sh
./deploy-all.sh
```

**Time:** ~5 minutes. The scripts deploy Data → Auth → Pipeline → Api, build the frontend with the API URL and Cognito IDs, then deploy Frontend, and finish with the website URL, the pipeline alias ARN and the SNS topic.

### Test Your System

1. **Create an admin user** with the two `aws cognito-idp` commands printed at the end of the deployment, then sign in at the CloudFront URL.

2. **Click Refresh** on the dashboard. The button shows live progress (`N extracted, M scanned`); you can navigate away - the run continues server-side and the UI re-attaches when you come back. When it finishes you get a summary such as *Facts: 11/11 services (323 items). Inventory: 8 assets scanned, 1 need attention.*

3. **Refresh a single service** from the Services page - this runs the same pipeline scoped to one service (`mode: extract`).

4. **Command-line testing (optional):**
   ```bash
   region=$(aws configure get region)
   pipeline=$(aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerPipeline-$region" \
     --query "Stacks[0].Outputs[?OutputKey=='PipelineFunctionAliasArn'].OutputValue" --output text)

   # Start a scoped run (durable functions need a qualified ARN + an execution name)
   aws lambda invoke --function-name "$pipeline" --invocation-type Event \
     --durable-execution-name "cli-$(date +%s)" \
     --payload '{"mode":"full","services":["lambda"],"refresh_origin":"manual"}' \
     --cli-binary-format raw-in-base64-out out.json && cat out.json

   # Follow it
   aws lambda list-durable-executions-by-function --function-name aws-services-lifecycle-pipeline --max-items 3
   aws lambda get-durable-execution --durable-execution-arn <arn from above> --query '[Status,Result]'
   aws lambda get-durable-execution-history --durable-execution-arn <arn> --query 'Events[].[EventType,Name]' --output text
   ```

5. **Subscribe to run summaries (optional):**
   ```bash
   aws sns subscribe --topic-arn <NotificationTopicArn output> --protocol email --notification-endpoint you@example.com
   ```

## Stack Architecture

| Stack Name | Purpose | Key Resources | Dependencies |
|------------|---------|---------------|--------------|
| **AWSServicesLifecycleTrackerData-{region}** | Data storage | 6 DynamoDB tables + service config populator | None |
| **AWSServicesLifecycleTrackerAuth-{region}** | Authentication | Cognito User Pool + web client | None |
| **AWSServicesLifecycleTrackerPipeline-{region}** | Refresh pipeline (main stack) | Lambda durable function + `live` alias, API Lambda, SNS topic, SQS DLQ, EventBridge schedules, IAM | Data |
| **AWSServicesLifecycleTrackerApi-{region}** | UI API | API Gateway HTTP API + Cognito JWT authorizer | Pipeline, Auth |
| **AWSServicesLifecycleTrackerFrontend-{region}** | Admin interface | S3 bucket, CloudFront distribution, React UI | Api, Auth |

## Project Structure

```
project-root/
├── agent/                          # Python code shared by both Lambda functions
│   ├── lambda_pipeline.py          # Durable function: extract -> scan -> reconcile -> notify
│   ├── lambda_api.py               # API Lambda: HTTP routes, pipeline control, scheduler entry
│   ├── actions.py                  # Action router (list/update services, plans, health, ...)
│   ├── workflow_orchestrator.py    # Single-service extraction workflow
│   ├── data_extractor.py           # HTML parsing + Amazon Nova normalization
│   ├── account_discovery.py        # Account scanners + inventory reconciliation
│   ├── database_reads.py           # READ operations (metrics, configs, deprecations)
│   ├── database_writes.py          # WRITE operations + status categorization
│   ├── action_plans.py             # Plan of Action CRUD
│   ├── health_*.py                 # AWS Health collection, enrichment, reads
│   ├── requirements.txt            # boto3, aws-durable-execution-sdk-python, bs4, requests
│   └── tests/                      # pytest (incl. DurableFunctionTestRunner pipeline tests)
│
├── cdk/                            # Infrastructure as Code (TypeScript)
│   ├── bin/app.ts                  # Stack wiring (tracking tag on the Pipeline stack)
│   ├── lib/
│   │   ├── data-stack.ts           # DynamoDB tables + service config population
│   │   ├── auth-stack.ts           # Cognito User Pool
│   │   ├── pipeline-stack.ts       # Durable pipeline + API Lambda + schedules (pip bundling, no Docker)
│   │   ├── api-stack.ts            # HTTP API + JWT authorizer
│   │   └── frontend-stack.ts       # CloudFront + S3
│   └── test/                       # CDK assertions (jest)
│
├── frontend/                       # React admin interface (Cloudscape Design System)
│   └── src/
│       ├── api.ts                  # fetch() to the HTTP API with the Cognito ID token
│       ├── auth.ts                 # Cognito User Pool authentication
│       └── pages/                  # Dashboard, Services, Deprecations, Timeline, PlanOfAction
│
├── scripts/
│   ├── service_configs.json        # 🔧 KEY FILE: service definitions
│   ├── populate_service_configs.py # Loads service configs into DynamoDB at deploy time
│   └── build-frontend.{ps1,sh}     # Frontend build with API URL / Cognito injection
│
├── deploy-all.{ps1,sh}             # Complete deployment
├── ARCHITECTURE.md                 # Design notes
└── README.md
```

### 🔧 Key Files for Customization

| File | Purpose | When to Modify |
|------|---------|----------------|
| **`scripts/service_configs.json`** | Service definitions - documentation URLs, extraction focus, schema | Add new services to monitor |
| **`agent/account_discovery.py`** + **`cdk/lib/pipeline-stack.ts`** | Scanners and their read-only IAM grants | Add a service to the account scan |
| **`agent/database_writes.py`** | Status categorization logic | Customize status thresholds or date fields |
| **`agent/lambda_pipeline.py`** | Pipeline steps, concurrency, retry and failure-tolerance settings | Change how a refresh runs |
| **`frontend/src/pages/Dashboard.tsx`** | Dashboard UI | Customize layout or metrics |

## Service Configuration Management

### 🔧 Adding New AWS Services

All service definitions live in **`scripts/service_configs.json`**. The tracker ships with 11 services, each meeting two rules that every addition must meet too:

1. **The documentation page contains an HTML table with lifecycle dates** (end of support, retirement, deprecation). The extractor only sends tables to the model; on a page without one the model has nothing real to work with and will invent rows. `scripts/audit_service_configs.py` checks this for you.
2. **An account scanner exists for the service** (`agent/account_discovery.py`). Without one the service only ever produces a facts list, never ""what you own that is affected"", which is the point of the demo.

#### Service Configuration Schema

```json
{
  "services": {
    "lambda": {
      "name": "AWS Lambda",
      "documentation_urls": [
        "https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html#runtimes-deprecated"
      ],
      "extraction_focus": "Locate the 'Deprecated runtimes' table (columns: Name, Identifier, Operating system, Deprecation date, Block function create, Block function update). Extract EVERY row of that table, regardless of how old the deprecation date is. For each runtime, extract: runtime name, runtime identifier, operating system, deprecation_date, block_create_date, and block_update_date.",
      "schema_key": "runtimes",
      "item_properties": {
        "name": "Runtime name",
        "identifier": "Runtime identifier like nodejs18.x, python3.8",
        "os": "Operating system",
        "deprecation_date": "Deprecation date",
        "block_create_date": "Block function create date",
        "block_update_date": "Block function update date",
        "status": "Current status"
      },
      "required_fields": ["name", "identifier", "deprecation_date", "status"],
      "enabled": true
    },
    "elasticbeanstalk": {
      "name": "AWS Elastic Beanstalk",
      "documentation_urls": [
        "https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/platforms-schedule.html"
      ],
      "extraction_focus": "Locate the 'Retiring' or 'Retired' tables or similar. Focus on the most important retirement items from 2025 and later. For each platform branch, extract: Runtime version, Platform branch name, Operating system, and retirement dates (both target retirement dates and actual retirement dates).",
      "schema_key": "platform_branches",
      "item_properties": {
        "name": "Full platform branch name (e.g., 'Corretto 17 AL2', 'PHP 8.1 AL2023')",
        "identifier": "Platform branch identifier (e.g., 'corretto-17-al2', 'php-8.1-al2023')",
        "runtime_version": "Runtime version (e.g., 'Corretto 17', 'PHP 8.1')",
        "operating_system": "Operating system (e.g., 'Amazon Linux 2', 'AL2023')",
        "target_retirement_date": "Target retirement date",
        "retirement_date": "Actual retirement date"
      },
      "required_fields": ["name", "identifier", "runtime_version"],
      "enabled": true
    }
  }
}
```

#### Configuration Fields Explained

| Field | Purpose | Example |
|-------|---------|---------|
| **`name`** | Human-readable service name | `"AWS Lambda"` |
| **`documentation_urls`** | AWS documentation pages to parse | `["https://docs.aws.amazon.com/lambda/..."]` |
| **`extraction_focus`** | **AI instructions** - Tells the LLM exactly what to extract | `"Extract ALL deprecated Lambda runtimes from the 'Deprecated runtimes' table..."` |
| **`schema_key`** | Database key prefix for items | `"runtimes"` → `"runtimes#nodejs18.x"` |
| **`item_properties`** | Expected fields in extracted data | Maps field names to descriptions for AI |
| **`required_fields`** | Fields that must be present | `["name", "identifier"]` |
| **`enabled`** | Whether service is active for automated extraction | `true` or `false` |
| **`*_date` fields** | Any field ending in `_date` is normalized to ISO `YYYY-MM-DD` at storage time, whatever spelling the page uses (`28 February 2027`, `February 28, 2027`, `April 2032`) | `"end_of_standard_support_date"` |

#### Adding a New Service

**Mental Model: The Human-in-the-Loop Approach**

Adding a new service follows a simple two-step process:

1. **🔍 Identify the Documentation Page**
   - Find the AWS documentation page that contains the deprecation/lifecycle information you want to track
   - Look for pages with titles like "Deprecated features", "Runtime support policy", "Version lifecycle", etc.
   - Example: `https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html#runtimes-deprecated`

2. **👁️ Human Analysis & Prompt Engineering**
   - Manually review the page to understand its structure
   - Identify the key information: table names, column headers, date formats, identifiers
   - Craft the `extraction_focus` prompt to guide the AI on what to extract and how
   - Think of it as writing instructions for a smart assistant who can see the page

**The system combines your human insight (knowing where to look and what matters) with AI capabilities (parsing HTML and normalizing data).**

---

**Step-by-Step Process:**

1. **Check the page before writing any config.** Put the candidate URL in a scratch entry (or just run the audit on an existing one) and make sure it reports `OK` with a lifecycle table:
```bash
cd operations-automation/aws-services-lifecycle-tracker
pip install requests beautifulsoup4 boto3
python scripts/audit_service_configs.py --service your-new-service
#   your-new-service   OK   ...  -> OK: 9 rows: Type | Version | End of support | End of life
```
   `NO-TABLE` or `WEAK` means the page will not work - find the service's release calendar / version support page instead (they usually contain "release-calendar", "version-support", "supported-versions", "deprecated" or "platforms-schedule" in the URL). If no such page exists, the service cannot be tracked.

2. **Edit `scripts/service_configs.json`**:
```json
{
  "services": {
    "your-new-service": {
      "name": "Your AWS Service",
      "documentation_urls": [
        "https://docs.aws.amazon.com/your-service/latest/userguide/version-support-dates.html"
      ],
      "extraction_focus": "Use the table with columns 'Engine version | Release date | End of standard support | End of Extended Support'. Extract every row. For each version, extract: version, release date, end of standard support date, end of extended support date; use null where the page says N/A.",
      "schema_key": "versions",
      "item_properties": {
        "name": "Version name",
        "identifier": "Version identifier",
        "release_date": "Release date",
        "end_of_standard_support_date": "End of standard support",
        "end_of_extended_support_date": "End of extended support"
      },
      "required_fields": ["name", "identifier", "end_of_standard_support_date"],
      "enabled": true,
      "health_event_mapping": "YOURSERVICE"
    }
  }
}
```
   Put at least one date field in `required_fields`: an item without a lifecycle date is not a lifecycle fact.

3. **Add a scanner** in `agent/account_discovery.py` (`discover_<service>()` + an entry in `SCANNERS` / `SCANNER_SERVICE_KEYS`) and its read-only IAM actions in `cdk/lib/pipeline-stack.ts`.

4. **Redeploy** Data (config) and Pipeline (code):
```bash
cd cdk && region=$(aws configure get region)
npx cdk deploy "AWSServicesLifecycleTrackerData-$region" "AWSServicesLifecycleTrackerPipeline-$region" --no-cli-pager
```

5. **Run a scoped extraction** and verify what was stored really comes from the page:
```bash
aws lambda invoke --function-name aws-services-lifecycle-api --cli-binary-format raw-in-base64-out \
  --payload '{"action":"start_refresh","mode":"extract","services":["your-new-service"]}' out.json && cat out.json
# wait for the execution to finish (aws lambda get-durable-execution ...), then:
python scripts/audit_service_configs.py --check-stored --service your-new-service
#   your-new-service   OK   ...  12/12   <- every stored row found on the page
```
   A low `stored-on-page` ratio means the model padded the result: tighten `extraction_focus` (name the table and its columns explicitly) and re-run.
#### Writing Effective Extraction Focus

The `extraction_focus` field is crucial - it's the AI prompt that guides data extraction:

**✅ Good extraction focus:**
```json
"extraction_focus": "Extract ALL deprecated Lambda runtimes from the 'Deprecated runtimes' table. Include: runtime name, identifier (e.g., nodejs18.x), OS, deprecation date, block dates. Expected ~24 deprecated runtimes including Node.js, Python, .NET, Java, Ruby, Go versions."
```

**❌ Poor extraction focus:**
```json
"extraction_focus": "Get Lambda stuff"
```

**Best Practices:**
- **Be specific** about table names, sections, or content areas
- **Include expected count** ("Expected ~24 items")
- **List required fields** explicitly
- **Provide examples** of identifiers or formats
- **Mention variations** the AI should handle


## How It Works

### The refresh pipeline (Lambda durable function)

`agent/lambda_pipeline.py` is a single `@durable_execution` handler. Every unit of work is a named, checkpointed step, so a crash or timeout resumes from the last checkpoint instead of restarting, and the execution history is the audit trail of the run.

```
start-run                       run_id, timestamp, region, enabled services (one step: non-deterministic values)
map "extract"                   one step per service: extract-<service>   (max 5 in parallel, 3 attempts)
map "scan"                      one step per scanner x region: scan-<Scanner>-<region>   (max 5, 2 attempts)
reconcile-inventory             upsert this run's assets, drop stale rows only for scopes that scanned OK
summarize-and-notify            run summary -> execution result + SNS message
```

Design points:
- **Failure isolation**: both maps use `tolerated_failure_percentage=100`. A documentation page that changed or a scanner without permissions is reported in the summary; the run still succeeds and everything else is reconciled.
- **Slim checkpoints**: extraction steps write the items to DynamoDB themselves and return only counts, so execution state stays small.
- **Deterministic reconciliation**: the `run_id` minted in `start-run` tags all inventory rows; stale rows are removed only for `(service, region)` scopes whose scan step succeeded.
- **Idempotent starts**: the execution name is the idempotency key (Lambda enforces it). The weekly schedule uses `refresh-weekly-<date>`, manual runs `refresh-manual-<epoch>`; a second start while one is RUNNING adopts the running execution instead.
- **Input contract**: `{"mode": "full|extract|scan", "services"?: [...], "regions"?: [...], "refresh_origin": "manual|Auto"}`. The UI's Refresh button sends `full`; per-service refresh sends `extract` with one service.

Local tests use `aws-durable-execution-sdk-python-testing` (`agent/tests/test_lambda_pipeline.py`):

```bash
cd agent && pip install -r requirements.txt aws-durable-execution-sdk-python-testing pytest && python -m pytest -q
```

### The API function

`agent/lambda_api.py` is a plain Lambda serving three things:
- **HTTP API routes** (JWT-protected): `POST /actions` (router actions such as `list_services`, `update_service`, action plans, health reads), `POST /refresh` (start or adopt a pipeline execution), `GET /refresh/{arn}` (status, progress, final summary).
- **Weekly schedule**: `{"action": "start_refresh", "refresh_origin": "Auto"}` - same naming and adopt-running logic as the UI.
- **Hourly Health poll**: `{"action": "collect_health_events"}`.

Long-running work never runs behind the API (30 s limit): anything that extracts or scans goes through the pipeline.

### Agent modules

- **`workflow_orchestrator.py`** - `extract_service_lifecycle()`: config → fetch → normalize → store → metadata, for one service
- **`data_extractor.py`** - BeautifulSoup table parsing + Amazon Nova normalization with service-specific prompts
- **`account_discovery.py`** - one `discover_*` scanner per service, a `LifecycleIndex` to match versions against the facts, and `save_to_dynamodb()` for run-scoped reconciliation
- **`database_reads.py` / `database_writes.py`** - DynamoDB access; `categorize_item_status()` lives in writes
- **`actions.py`** - the action router used by the API function

### 🧠 Intelligent Status Categorization

The system automatically categorizes deprecation items based on their lifecycle dates:

```python
def categorize_item_status(item: Dict[str, Any]) -> str:
    """
    Intelligently categorize item status based on dates
    Returns: 'deprecated', 'extended_support', or 'end_of_life'
    """
    # Analyzes fields like:
    # - target_retirement_date, retirement_date
    # - end_of_support_date, end_of_life_date  
    # - block_function_create_date, block_function_update_date
    
    # Logic examples:
    # - Within 6 months of retirement → 'extended_support'
    # - Past retirement date → 'end_of_life'
    # - Otherwise → 'deprecated'
```

**Result**: Dashboard shows actionable status breakdown:
- **75 Deprecated** - Plan migration within timeline
- **19 Extended Support** - Extra costs apply, upgrade recommended  
- **2 End of Life** - Immediate action required



## Operations

### Schedules

| Schedule | Cadence | Target | Payload |
|----------|---------|--------|---------|
| `aws-services-lifecycle-weekly-refresh` | every 7 days | API function | `{"action":"start_refresh","refresh_origin":"Auto"}` → full pipeline run |
| `aws-health-events-collection` | hourly | API function | `{"action":"collect_health_events"}` |

Failed scheduler invocations land in the `aws-services-lifecycle-scheduler-dlq` SQS queue.

```bash
aws scheduler list-schedules --name-prefix aws-
aws scheduler get-schedule --name aws-services-lifecycle-weekly-refresh
```

### Monitoring a run

```bash
# Executions (most recent first) and their status
aws lambda list-durable-executions-by-function --function-name aws-services-lifecycle-pipeline --max-items 5

# Result / error of one execution
aws lambda get-durable-execution --durable-execution-arn <arn> --query '[Status,Error,Result]'

# Step-by-step history (which extract-*/scan-* steps succeeded or failed)
aws lambda get-durable-execution-history --durable-execution-arn <arn> \
  --query 'Events[?contains(EventType, `Step`)].[EventType,Name]' --output text

# Logs
aws logs tail /aws/lambda/aws-services-lifecycle-pipeline --follow
aws logs tail /aws/lambda/aws-services-lifecycle-api --follow
```

Every execution is also visible in the Lambda console under the function's **Durable executions** tab, including a timeline of the steps.

### Manual Deployment

```bash
cd cdk && npm install
npx cdk bootstrap                                          # one-time per account/region
npx cdk deploy AWSServicesLifecycleTrackerData-<region>
npx cdk deploy AWSServicesLifecycleTrackerAuth-<region>
npx cdk deploy AWSServicesLifecycleTrackerPipeline-<region> # bundles agent/ with pip (no Docker)
npx cdk deploy AWSServicesLifecycleTrackerApi-<region>
cd .. && ./scripts/build-frontend.sh <UserPoolId> <UserPoolClientId> <ApiUrl> <region>
cd cdk && npx cdk deploy AWSServicesLifecycleTrackerFrontend-<region>
```

Updating the Python code is just `cdk deploy` of the Pipeline stack: CDK re-bundles `agent/`, publishes a new function version and moves the `live` alias. Executions started on the previous version finish on that version.

### Optional: a test fleet of databases to scan

A fresh account has nothing on a deprecated version, so the scan phase finds nothing. `scripts/create_test_databases.py` creates 12 small RDS-family databases on engine versions whose standard support ends within the next year (plus a few current ones as controls), so a Refresh shows real matches:

| Identifier | Engine / version | Class | Expected status |
|---|---|---|---|
| `lt-mysql-8-4` | MySQL 8.4.5 | db.t4g.micro | end of support announced (2026-10-31) |
| `lt-mariadb-10-6`, `lt-mariadb-11-4` | MariaDB 10.6.22 / 11.4.7 | db.t4g.micro | end of support announced |
| `lt-postgres-14` | PostgreSQL 14.18 | db.t4g.micro | end of support announced (2026-10-31) |
| `lt-postgres-18` | PostgreSQL 18.6 | db.t4g.micro | supported (control) |
| `lt-sqlserver-2017` | SQL Server 2017 Express | db.t3.small | supported (RDS end of support 2027-10-12, control) |
| `lt-aurora-mysql-3-08`, `lt-aurora-mysql-3-10` | Aurora MySQL 3.08.2 / 3.10.5 | Serverless v2, min 0 ACU | extended support / supported (control) |
| `lt-aurora-postgres-14`, `lt-aurora-postgres-15` | Aurora PostgreSQL 14.17 / 15.10 | Serverless v2, min 0 ACU | end of support announced / supported (control) |
| `lt-docdb-4-0` | DocumentDB 4.0 | db.t3.medium | supported (no fee-free deprecated version exists) |
| `lt-neptune-1-2` | Neptune 1.2.1.2 | db.t4g.medium | end of life 2026-12-04 |

```bash
python scripts/create_test_databases.py            # create what is missing (idempotent)
python scripts/create_test_databases.py --status
python scripts/create_test_databases.py --teardown # delete everything, no snapshots
```

Every resource is tagged `auto-delete=false`, `Project=aws-services-lifecycle-tracker`, `Purpose=lifecycle-test-fleet`; nothing is publicly accessible and the random master passwords are never stored (nobody connects to these). Only versions **inside** standard support are used: a version past it would incur RDS Extended Support fees (~$0.10 per vCPU-hour) - that is also why the fleet has no MySQL 5.7/8.0, PostgreSQL 13 or DocumentDB 3.6, even though those would show as `extended_support`.

**Cost:** about **$210-240/month** - six RDS instances (~$95 incl. 20 GB gp3 each), DocumentDB db.t3.medium (~$60), Neptune db.t4g.medium (~$65), and the four Aurora Serverless v2 clusters at min 0 ACU, which scale to zero after 5 minutes idle (storage and backup only, a few dollars). Tear it down when you are done demoing.
### Cleanup

```bash
cd cdk
npx cdk destroy AWSServicesLifecycleTrackerFrontend-<region> AWSServicesLifecycleTrackerApi-<region> \
  AWSServicesLifecycleTrackerPipeline-<region> AWSServicesLifecycleTrackerAuth-<region> AWSServicesLifecycleTrackerData-<region>
```

Deleting the pipeline function waits for RUNNING executions to finish (stop them first with `aws lambda stop-durable-execution` if you are in a hurry). DynamoDB tables are removed with the Data stack.

### Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `Durable execution requires qualified function identifier` | Invoke the `live` alias (the `PipelineFunctionAliasArn` output), never the bare function name |
| Pipeline bundling fails during `cdk deploy` | `python`/`python3` with `pip` must be on PATH (3.11+). If pip is unavailable CDK falls back to Docker bundling |
| Refresh summary lists failed services | Open the execution history: `extract-<service>` shows the error (docs page changed, Bedrock access, ...). The rest of the run is unaffected |
| `failed_cells` in the scan summary | Scanner had no permission or the service isn't available in that region; inventory for that scope is left untouched |
| Health panel empty, `SubscriptionRequiredException` in the API logs | AWS Health API needs a Business/Enterprise Support plan |
| UI shows `401` | Session expired - sign in again. The HTTP API only accepts a valid Cognito ID token |
| A second Refresh says "already in progress" | Intended: one run at a time; the UI attaches to the running execution |

## Data Model & Intelligent Status System

### 🧠 Intelligent Status Categorization

The system automatically analyzes date fields to categorize each deprecation item:

```typescript
type DeprecationStatus = 
  | "deprecated"        // Announced for deprecation, plan migration
  | "extended_support"  // Within 1 year of retirement, extra costs may apply  
  | "end_of_life"       // Past retirement date, immediate action required
```

### Status Logic Examples

**Elastic Beanstalk Platform (target_retirement_date: 2025-12-01)**
- Current date: 2025-10-30
- Days until retirement: ~32 days
- **Status**: `extended_support` (within 1 year threshold)

**Lambda Runtime (block_function_update_date: 2026-03-09)**  
- Current date: 2025-10-30
- Days until blocking: ~130 days
- **Status**: `extended_support` (within 1 year threshold)

**Hypothetical Item (retirement_date: 2024-06-01)**
- Current date: 2025-10-30  
- **Status**: `end_of_life` (past retirement date)

### Real Dashboard Results

Based on actual extracted data:
- **75 Deprecated** - Standard deprecation announcements, plan migration
- **19 Extended Support** - Within 1 year of retirement, prioritize migration
- **2 End of Life** - Past retirement dates, immediate action required

### Service-Specific Date Fields

Different AWS services use different date field names. The system recognizes these patterns:

| Service | Key Date Fields | Status Logic |
|---------|-----------------|--------------|
| **Lambda** | `deprecation_date`, `block_function_create_date`, `block_function_update_date` | Block dates determine end_of_life |
| **Elastic Beanstalk** | `target_retirement_date`, `retirement_date` | Retirement dates determine lifecycle stage |
| **EKS** | `end_of_support_date`, `end_of_extended_support_date` | Support periods determine status |
| **RDS** | `end_of_standard_support_date`, `end_of_extended_support_date` | Support periods with cost implications |

The intelligent categorization logic in `agent/database_writes.py` automatically recognizes these patterns and applies consistent status classification.

### DynamoDB Table Structure

**Table: `aws-services-lifecycle`**

```json
{
  "service_name": "lambda",                    // Partition Key
  "item_id": "runtimes#nodejs18.x",           // Sort Key (schema_key#identifier)
  "status": "deprecated",                      // Universal status (indexed)
  "source_url": "https://docs.aws.amazon.com/...",
  "extraction_date": "2025-10-27T22:33:57Z",
  "last_verified": "2025-10-27T22:33:57Z",
  "service_specific": {                        // Service-specific fields
    "name": "Node.js 18",
    "identifier": "nodejs18.x",
    "operating_system": "Amazon Linux 2",
    "architecture": null,
    "deprecation_date": "Sep 1, 2025",
    "block_function_create_date": "Feb 3, 2026",
    "block_function_update_date": "Mar 9, 2026"
  }
}
```

### Querying by Status

The `status-index` GSI enables efficient cross-service queries:

```bash
# Get all items requiring immediate attention (end of life)
aws dynamodb query \
  --table-name aws-services-lifecycle \
  --index-name status-index \
  --key-condition-expression "#status = :status" \
  --expression-attribute-names '{"#status":"status"}' \
  --expression-attribute-values '{":status":{"S":"end_of_life"}}'

# Get all items in extended support (extra costs)
aws dynamodb query \
  --table-name aws-services-lifecycle \
  --index-name status-index \
  --key-condition-expression "#status = :status" \
  --expression-attribute-names '{"#status":"status"}' \
  --expression-attribute-values '{":status":{"S":"extended_support"}}'

# Get all deprecated items (plan migration)
aws dynamodb query \
  --table-name aws-services-lifecycle \
  --index-name status-index \
  --key-condition-expression "#status = :status" \
  --expression-attribute-names '{"#status":"status"}' \
  --expression-attribute-values '{":status":{"S":"deprecated"}}'

# Get items by status and date range
aws dynamodb query \
  --table-name aws-services-lifecycle \
  --index-name status-index \
  --key-condition-expression "#status = :status AND deprecation_date BETWEEN :start AND :end" \
  --expression-attribute-names '{"#status":"status"}' \
  --expression-attribute-values '{":status":{"S":"deprecated"},":start":{"S":"2025-01-01"},":end":{"S":"2025-12-31"}}'
```

### Query Patterns

| Use Case | Query Method | Index Used |
|----------|-------------|------------|
| All items for one service | `service_name = "lambda"` | Main table (PK) |
| One specific item | `service_name = "lambda" AND item_id = "runtimes#nodejs18.x"` | Main table (PK+SK) |
| All deprecated items (any service) | `status = "deprecated"` | GSI: status-index |
| All items needing immediate action | `status = "end_of_life"` | GSI: status-index |
| All items with extra costs | `status = "extended_support"` | GSI: status-index |
| Items by status and date range | `status = "deprecated" AND deprecation_date BETWEEN ...` | GSI: status-index |

### Design Rationale

**Why a universal status enum?**
- Enables cross-service queries (e.g., "show me everything that's end of life")
- Provides consistent filtering in admin UI and APIs
- Simplifies alerting and reporting logic

**Why service-specific details in a nested object?**
- Each AWS service has unique lifecycle fields (block dates, support periods, etc.)
- Keeps the data model flexible for adding new services
- Preserves service-specific terminology from AWS documentation

**Why both `item_id` and `service_specific.identifier`?**
- `item_id` = Technical DynamoDB key with prefix (e.g., `runtimes#nodejs18.x`)
- `service_specific.identifier` = Clean identifier for display (e.g., `nodejs18.x`)
- Follows database best practice of having both technical and human-readable identifiers


## Architecture Decisions

### Why a Lambda durable function for the pipeline?

The refresh is a multi-step batch (dozens of extractions, a dozen scans, then a reconciliation that must only trust successful scans). A durable function gives that batch checkpointed steps, per-step retries, a completion policy for partial failures and a queryable execution history - in plain Python, in one deployable unit, with no state machine definition to keep in sync with the code. Earlier versions of this demo used Step Functions plus an Amazon Bedrock AgentCore runtime for the same job; the durable function replaced both with less infrastructure and faster deploys.

### Why an HTTP API in front of the UI?

The browser only ever holds a Cognito ID token. API Gateway validates it, and the API Lambda is the only principal with DynamoDB, Bedrock and Lambda permissions. Anything that can take longer than the API's 30 s limit (extraction, scanning) is pushed to the pipeline and observed asynchronously.

### Why Hybrid Extraction (HTML + AI)?

**Reliability**: BeautifulSoup HTML parsing provides deterministic table extraction
**Intelligence**: AI normalization handles variations in AWS documentation formats
**Cost Efficiency**: Minimal token usage while maintaining high data quality

### Why no Docker?

All Python dependencies are pure Python, so `pipeline-stack.ts` bundles them locally with `pip --platform manylinux2014_aarch64` into a zip. Contributors don't need Docker or a container registry, and a code change deploys in about a minute.

### Stack Organization

| Stack | Purpose | Update Frequency |
|-------|---------|------------------|
| **Data** | DynamoDB + service configs | When adding services |
| **Auth** | Cognito User Pool | Rarely |
| **Pipeline** | Lambda functions, schedules, notifications | When updating agent code |
| **Api** | HTTP API + authorizer | Rarely |
| **Frontend** | React UI + CloudFront | When updating UI |

## Security

### Authentication & Authorization
- **Admin-only access** - Cognito User Pool with self-signup disabled
- **JWT authorizer** - API Gateway validates the Cognito ID token on every request; the browser never receives AWS credentials
- **Password policy** - Minimum 8 characters, uppercase, lowercase, digit required
- **Least privilege IAM** - the API function can only invoke/observe the pipeline function and access the demo's tables; the configuration table is read + update only (no Put/Delete) so deploy-time config and runtime state stay separated
- **Read-only discovery** - scanners use List/Describe permissions only

### Network & Data Security
- **HTTPS only** - CloudFront and API Gateway with TLS
- **Origin Access Control (OAC)** - S3 bucket only accessible via CloudFront
- **DynamoDB encryption** - Data encrypted at rest using AWS managed keys
- **Durable execution state** - checkpoints hold counts and identifiers only, never extracted content

### Admin User Management
Admin users must be created manually to prevent unauthorized access:

```bash
# Create admin user via AWS CLI
aws cognito-idp admin-create-user \
  --user-pool-id <USER_POOL_ID> \
  --username admin \
  --user-attributes Name=email,Value=admin@company.com Name=email_verified,Value=true \
  --message-action SUPPRESS

# Set permanent password
aws cognito-idp admin-set-user-password \
  --user-pool-id <USER_POOL_ID> \
  --username admin \
  --password <SECURE_PASSWORD> \
  --permanent
```

### Security Best Practices
1. **Use strong passwords** for admin accounts (consider password manager)
2. **Enable MFA** for admin users (optional, via Cognito Console)
3. **Restrict CORS** in `cdk/lib/api-stack.ts` to your CloudFront domain once it is known
4. **Monitor CloudWatch Logs** for suspicious activity
5. **Review IAM policies** periodically to ensure least privilege
6. **Enable CloudTrail** for audit logging of AWS API calls

## Cost Estimate

Approximate monthly costs with the default weekly schedule (11 services, one region):

- **Lambda (pipeline + API)**: a full refresh is ~1 minute of compute at 1 GB plus a few dozen durable-execution checkpoints; the hourly Health poll and UI calls add a few hundred short invocations. Well under $1/month
- **Bedrock (Amazon Nova)**: pay per token; ~$1-3/month for weekly extraction of ~30 services
- **DynamoDB** (on-demand): ~$1-3/month
- **API Gateway HTTP API**: $1.00 per million requests - negligible
- **EventBridge Scheduler / SNS / SQS**: negligible (a few hundred invocations per month)
- **CloudFront + S3**: ~$1/month
- **CloudWatch Logs**: ~$1/month (1-month retention on both log groups)
- **Cognito**: free tier

**Total: roughly $5/month.** Running the refresh daily multiplies the Bedrock and Lambda lines by ~7 (~$15-20/month).

## Frontend Architecture

The admin interface is built with [AWS Cloudscape Design System](https://cloudscape.design/), AWS's open-source design system for building intuitive, accessible web applications.

### Key Features

- **Dashboard**: Real-time metrics with status breakdown (deprecated, extended support, end of life)
- **Services Management**: Configure, enable/disable, and test AWS service extractions
- **Deprecations Viewer**: Browse and filter deprecation data with advanced search
- **Timeline View**: Visualize upcoming deprecation deadlines
- **Authentication**: Cognito User Pool; the ID token is sent to the HTTP API on every call
- **Refresh with re-attach**: fire-and-forget pipeline start, progress polled via `GET /refresh/{arn}`, survives navigation and reloads

### Cloudscape Benefits

- **AWS Native**: Built by AWS for AWS applications
- **Accessibility**: WCAG 2.1 AA compliant out of the box
- **Responsive**: Works seamlessly across devices
- **Rich Components**: 50+ pre-built components (tables, forms, modals, notifications)

### Cloudscape Resources

- [Component Library](https://cloudscape.design/components/)
- [Design Tokens](https://cloudscape.design/foundation/visual-foundation/design-tokens/)
- [GitHub Repository](https://github.com/cloudscape-design/components)

## Next Steps & Customization Ideas

### Adding More AWS Services
- **Edit** `scripts/service_configs.json` to add new services
- **Focus on** services with clear deprecation documentation
- **Run `scripts/audit_service_configs.py`** before and after (`--check-stored`) - a service only ships when its page has a lifecycle table, it has a scanner, and every stored row is found on the page

### Enhancing Status Logic  
- **Customize thresholds** in `agent/database_writes.py` (e.g., 3 months vs 6 months for extended_support)
- **Add service-specific logic** for different AWS service lifecycle patterns
- **Implement cost impact scoring** based on service usage and deprecation urgency

### UI Improvements
- **Add filtering** by urgency level or cost impact
- **Create timeline views** showing deprecation schedules
- **Build alerting** for items approaching end-of-life
- **Add export capabilities** for compliance reporting

### Integration Options
- **Webhook notifications** for Slack/Teams when new deprecations are detected
- **JIRA integration** to automatically create migration tickets
- **Cost analysis** integration with AWS Cost Explorer
- **Custom dashboards** with service-specific deprecation views


## Plan of Action - From Awareness to Remediation

The Plan of Action feature transforms passive deprecation awareness into actionable team workflows. Instead of just knowing what's deprecated, teams can now assign ownership, set priorities, and track remediation progress.

### Why Plan of Action?

Discovering deprecations is only half the battle. The real challenge is:
- **Who** is responsible for fixing each deprecation?
- **When** should it be completed?
- **What's the priority** compared to other work?
- **What's the status** of ongoing remediation efforts?

Plan of Action bridges the gap between awareness and action.

### Key Features

| Feature | Description |
|---------|-------------|
| **Ownership Assignment** | Assign deprecations to team members by email/alias |
| **Priority Levels** | Low, Medium, High, Critical - prioritize what matters most |
| **Status Tracking** | Not Started, In Progress, Completed, Blocked |
| **Target Dates** | Set deadlines for remediation completion |
| **Notes** | Add migration plans, blockers, or context |
| **Bulk Selection** | Select multiple deprecations from the Deprecations page and add them to Plan of Action in one action |

### Two Ways to Create Action Plans

**1. From Deprecations Page (Recommended)**
- Navigate to **Deprecations** in the sidebar
- Select one or more deprecations using the checkboxes
- Click **"Add to Plan of Action (N)"** button
- Fill in owner, priority, target date, and notes
- All selected items are added with the same assignment

**2. From Plan of Action Page**
- Navigate to **Plan of Action** in the sidebar
- Click **"Create Action Plan"**
- Select a deprecation from the dropdown
- Fill in assignment details

### Workflow Example

```
1. Discovery Phase
   └─ Run "Discover My Resources" to find deprecated resources in your account
   
2. Triage Phase
   └─ Review Deprecations page, filter by status (deprecated, end_of_life)
   └─ Select high-priority items (e.g., Lambda runtimes blocking soon)
   
3. Assignment Phase
   └─ Click "Add to Plan of Action"
   └─ Assign to team members: "john@company.com"
   └─ Set priority: "High" for items blocking in < 3 months
   └─ Set target date: 2 weeks before block date
   
4. Execution Phase
   └─ Team members view their assignments in Plan of Action
   └─ Update status as work progresses
   └─ Add notes about migration approach or blockers
   
5. Completion Phase
   └─ Mark items as "Completed" when remediation is done
   └─ Delete action plans for resolved deprecations
```

### Data Model

Action plans are stored in the `deprecation-action-plans` DynamoDB table:

```json
{
  "plan_id": "uuid",
  "service_name": "lambda",
  "item_id": "runtimes#nodejs18.x",
  "item_name": "Node.js 18",
  "owner": "john@company.com",
  "plan_status": "in_progress",
  "priority": "high",
  "target_date": "2025-12-01",
  "notes": "Migrating to Node.js 20, testing in progress",
  "created_at": "2025-10-30T10:00:00Z",
  "updated_at": "2025-11-15T14:30:00Z"
}
```

### Querying Action Plans

```bash
# Get all action plans
aws dynamodb scan --table-name deprecation-action-plans

# Get action plans by owner
aws dynamodb query \
  --table-name deprecation-action-plans \
  --index-name owner-index \
  --key-condition-expression "#owner = :owner" \
  --expression-attribute-names '{"#owner":"owner"}' \
  --expression-attribute-values '{":owner":{"S":"john@company.com"}}'

# Get action plans by status
aws dynamodb query \
  --table-name deprecation-action-plans \
  --index-name plan-status-index \
  --key-condition-expression "plan_status = :status" \
  --expression-attribute-values '{":status":{"S":"blocked"}}'
```

### Infrastructure

The Plan of Action feature is fully integrated into the CDK deployment:

- **Data Stack** (`cdk/lib/data-stack.ts`): Creates `deprecation-action-plans` table with GSIs for owner and status queries
- **Pipeline Stack** (`cdk/lib/pipeline-stack.ts`): Grants the Lambda functions IAM permissions to read/write action plans
- **Agent** (`agent/action_plans.py`): CRUD operations for action plans
- **Frontend** (`frontend/src/pages/PlanOfAction.tsx`): UI for managing action plans
- **Frontend** (`frontend/src/pages/Deprecations.tsx`): Bulk selection and "Add to Plan of Action" button

No manual setup required - everything is created automatically during stack deployment.


## Resources

### Key Documentation
- **[AWS Lambda durable functions](https://docs.aws.amazon.com/lambda/latest/dg/durable-functions.html)** - Execution model, steps, maps, retries
- **[Durable Execution SDK for Python](https://github.com/aws/aws-durable-execution-sdk-python)** - SDK and local test runner used by the pipeline
- **[Cloudscape Design System](https://cloudscape.design/)** - UI component library used in admin interface

### AWS Service Documentation  
- **[AWS Lambda Runtimes](https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html)** - Lambda deprecation source
- **[Elastic Beanstalk Platforms](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/platforms-retiring.html)** - Platform retirement schedules
- **[EKS Version Calendar](https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html)** - Kubernetes version lifecycle

### Development Resources
- **[CDK API Reference](https://docs.aws.amazon.com/cdk/api/v2/)** - Infrastructure as Code
- **[Bedrock Model IDs](https://docs.aws.amazon.com/bedrock/latest/userguide/model-ids.html)** - Available AI models
- **[BeautifulSoup Documentation](https://www.crummy.com/software/BeautifulSoup/bs4/doc/)** - HTML parsing library

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
