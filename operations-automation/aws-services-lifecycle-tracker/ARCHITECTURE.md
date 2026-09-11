# Architecture

## Overview

The AWS Services Lifecycle Tracker is a serverless application that keeps two things in sync: the **public deprecation facts** published in the AWS documentation, and the **inventory of resources in your account** that those facts affect. Both are refreshed by a single [AWS Lambda durable function](https://docs.aws.amazon.com/lambda/latest/dg/durable-functions.html) execution: extract → scan → reconcile → notify. Extraction combines HTML parsing (BeautifulSoup) with AI normalization (Amazon Nova via Amazon Bedrock).

## High-Level Architecture

```
┌────────────┐   ┌──────────────────────────────┐   ┌──────────────────────────────┐
│ Admin user │──▶│ CloudFront + S3 (React SPA)  │──▶│ Cognito User Pool (ID token) │
└────────────┘   └──────────────┬───────────────┘   └──────────────────────────────┘
                                │ HTTPS, Authorization: Bearer <ID token>
                                ▼
                 ┌──────────────────────────────────────────────────────────────┐
                 │ API Gateway HTTP API + Cognito JWT authorizer                │
                 │   POST /actions · POST /refresh · GET /refresh/{arn}         │
                 └──────────────┬───────────────────────────────────────────────┘
                                ▼
                 ┌──────────────────────────────────────────────────────────────┐
                 │ aws-services-lifecycle-api (Lambda)                          │
                 │  action router · start/adopt/observe pipeline · Health poll  │
                 └──────────────┬───────────────────────────────────────────────┘
                                │ lambda:Invoke (Event, DurableExecutionName)
                                ▼
┌───────────────┐ ┌──────────────────────────────────────────────────────────────┐
│ AWS docs      │◀┤ aws-services-lifecycle-pipeline:live (durable function)      │
│ Bedrock/Nova  │◀┤  start-run → map extract-<svc> → map scan-<Scanner>-<region> │
│ Account APIs  │◀┤  → reconcile-inventory → summarize-and-notify (SNS)          │
└───────────────┘ └──────────────┬───────────────────────────────────────────────┘
                                 ▼
                 ┌──────────────────────────────────────────────────────────────┐
                 │ DynamoDB                                                     │
                 │  aws-services-lifecycle (facts) · aws-account-inventory      │
                 │  service-extraction-config · service-extraction-state        │
                 │  deprecation-action-plans · aws-health-events                │
                 └──────────────────────────────────────────────────────────────┘

EventBridge Scheduler ──▶ API Lambda:  weekly {"action":"start_refresh"} · hourly {"action":"collect_health_events"}
```

## CDK Stack Decomposition

| Stack | Purpose | Key Resources |
|-------|---------|---------------|
| **Data** | Storage | 6 DynamoDB tables + GSIs, deploy-time config populator |
| **Auth** | Authentication | Cognito User Pool + web client |
| **Pipeline** (main) | Refresh pipeline | Durable Lambda (`durableConfig` 2 h / 14 d) + `live` alias, API Lambda, shared data-access managed policy, SNS topic, SQS DLQ, EventBridge schedules, log groups |
| **Api** | UI API | HTTP API, JWT authorizer, Lambda integration, CORS |
| **Frontend** | Admin UI | S3 static hosting, CloudFront distribution with OAC |

### Dependency Graph

```
Data ──▶ Pipeline ──▶ Api ──▶ Frontend
Auth ────────────────▶ Api
Auth ─────────────────────────▶ Frontend
```

Both Lambda functions are built from the same `agent/` directory. `pipeline-stack.ts` bundles it without Docker: `pip install --platform manylinux2014_aarch64 --only-binary=:all:` resolves Linux wheels for the arm64 Python 3.14 runtime (all dependencies are pure Python), then the `.py` sources are copied in. CDK falls back to its Docker bundling image if pip is unavailable.

## The Refresh Pipeline

`agent/lambda_pipeline.py`, one `@durable_execution` handler.

| Step | Kind | What it does |
|------|------|--------------|
| `start-run` | step | Mints `run_id`, timestamp, region list and resolves enabled services - the only place non-deterministic values are produced |
| `extract` | map, `max_concurrency=5`, `tolerated_failure_percentage=100` | One step `extract-<service>` per service (3 attempts, backoff): fetch docs → BeautifulSoup → Nova normalization → write facts + metadata. Returns counts only |
| `scan` | map, same policy | One step `scan-<Scanner>-<region>` per scanner × region (2 attempts): `discover_*()` against account APIs, matched against the facts via `LifecycleIndex`. Returns the discovered items |
| `reconcile-inventory` | step | `save_to_dynamodb(items, run_id, scanned_services)`: upserts this run's rows and removes stale rows **only** for scopes whose scan step succeeded |
| `summarize-and-notify` | step | Builds the run summary (execution result read by the UI) and publishes it to SNS |

Input contract: `{"mode": "full" | "extract" | "scan", "services"?: [...], "regions"?: [...], "refresh_origin": "manual" | "Auto"}`.

Execution names are the idempotency key: `refresh-weekly-<YYYY-MM-DD>` for the schedule, `refresh-manual-<epoch>` for the UI, arbitrary for CLI tests. The API function refuses to start a second run while one is RUNNING and returns the running execution instead.

Replay-model rules observed: no `datetime.now()`/`uuid4()` outside steps; each step is self-contained (module-level clients are recreated on cold start); no durable operation inside a step; checkpoints carry counts and identifiers, not extracted content.

## Data Flow

### Refresh (UI button, weekly schedule, or CLI)

1. `POST /refresh` (or the scheduler payload) reaches the API Lambda
2. It lists RUNNING executions of the pipeline function; if one exists it is adopted
3. Otherwise `lambda:Invoke` with `InvocationType=Event` and a `DurableExecutionName` on the `live` alias
4. The UI stores the execution ARN in `sessionStorage` and polls `GET /refresh/{arn}`, which returns status, progress (count of succeeded `extract-*`/`scan-*` steps from the execution history) and, on success, the summary
5. The pipeline runs to completion regardless of the browser; the SNS message is the offline record

### Health Events (hourly)

1. Scheduler invokes the API Lambda with `{"action": "collect_health_events"}`
2. `HealthCollector` paginates `DescribeEvents` (global endpoint) with backoff, enriches via `DescribeEventDetails`, correlates with tracked services
3. Events are stored in `aws-health-events` with a 90-day TTL; a state-table lock prevents overlapping collections

### Reads (dashboard, services, plans)

1. Frontend calls `POST /actions` with `{"action": "list_services" | "get_metrics" | "list_deprecations" | ...}`
2. API Gateway validates the Cognito ID token, the API Lambda dispatches to `actions.py` → `database_reads.py` / `action_plans.py` / `health_reads.py`

## DynamoDB Tables

```
service-extraction-config   PK service_name        What to extract (URLs, focus, schema, enabled). Repo-owned:
                                                   populated at deploy; the Lambdas may only read/UpdateItem.
service-extraction-state    PK service_name        Runtime state owned by the Lambdas: extraction metadata,
                                                   Health collection lock/cursor.
aws-services-lifecycle      PK service_name        Public deprecation facts (one row per version/runtime/
                            SK item_id             platform), GSI status-index.
aws-account-inventory       PK service_name        Resources found in the account, tagged with run_id and
                            SK item_id             matched to a fact (status, days remaining).
deprecation-action-plans    PK plan_id             Remediation tracking; GSIs owner-index, plan-status-index.
aws-health-events           PK event_arn           AWS Health events; GSIs service-index, status-index; TTL.
                            SK event_type_category
```

The facts table and the inventory table are deliberately separate: `save_to_dynamodb()` refuses to write to the facts table.

## Authentication

```
User ──▶ Cognito User Pool (email/password, no self-signup)
              │ JWT ID token (kept in the SPA)
              ▼
         API Gateway HTTP API ── Cognito user-pool JWT authorizer
              │
              ▼
         API Lambda (the only principal with DynamoDB / Bedrock / Lambda permissions)
```

The browser never receives AWS credentials. IAM boundaries: agent-owned tables full access; configuration table read + `UpdateItem` only; Bedrock invoke; Health read; discovery List/Describe only; the API function may invoke/observe the pipeline function.

## Agent Module Structure

| Module | Responsibility |
|--------|----------------|
| `lambda_pipeline.py` | Durable function handler, steps and maps |
| `lambda_api.py` | HTTP routes, pipeline start/adopt/status, scheduler entry points |
| `actions.py` | Action router used by the API function |
| `workflow_orchestrator.py` | Single-service extraction workflow |
| `data_extractor.py` | HTML parsing + AI normalization engine |
| `account_discovery.py` | Scanners, `LifecycleIndex`, inventory reconciliation |
| `health_collector.py`, `health_enricher.py`, `health_reads.py`, `health_monitoring.py` | AWS Health integration |
| `database_reads.py` | Read operations (metrics, configs, listings) |
| `database_writes.py` | Write operations + status categorization |
| `action_plans.py` | Plan of Action CRUD |
| `concurrency_lock.py` | State-table lock for the Health collection |

## Status Categorization Logic

- **`deprecated`**: Announced for deprecation, retirement date > 1 year away
- **`extended_support`**: Within 1 year of retirement date (extra costs may apply)
- **`end_of_life`**: Past retirement date (immediate action required)

Date fields recognized: `target_retirement_date`, `retirement_date`, `end_of_support_date`, `end_of_life_date`, `block_function_create_date`, `block_function_update_date`, `end_of_extended_support_date`.

## Observability

- **Durable execution history**: every run's steps, retries and errors (`aws lambda get-durable-execution-history`, or the Lambda console's Durable executions tab)
- **CloudWatch Logs**: `/aws/lambda/aws-services-lifecycle-pipeline`, `/aws/lambda/aws-services-lifecycle-api` (1-month retention)
- **SNS**: run summary after every refresh (facts and inventory counts, failed services/scanners)
- **SQS DLQ**: failed scheduler invocations
