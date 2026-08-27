# Architecture

## Overview

The AWS Services Lifecycle Tracker is a serverless application built on Amazon Bedrock AgentCore that automatically monitors and extracts AWS service deprecation information. It uses a hybrid approach combining HTML parsing (BeautifulSoup) with AI normalization (Amazon Nova 2 Lite) for reliable data extraction.

## High-Level Architecture

```
┌─────────────────┐     ┌────────────────────────────────────────────────────┐
│   Admin User    │────▶│  CloudFront ──▶ S3 (React + Cloudscape UI)         │
└─────────────────┘     └────────────────────────────────────────────────────┘
                                          │
                                          ▼
                        ┌────────────────────────────────────────────────────┐
                        │  Cognito User Pool ──▶ Identity Pool ──▶ IAM Creds │
                        └────────────────────────────────────────────────────┘
                                          │
                                          ▼
┌─────────────────┐     ┌────────────────────────────────────────────────────┐
│ AWS Docs Pages  │◀────│  AgentCore Runtime (BeautifulSoup + Nova 2 Lite)   │
└─────────────────┘     └────────────────────────────────────────────────────┘
                                          │
┌─────────────────┐                       ▼
│ AWS Health API  │────▶┌────────────────────────────────────────────────────┐
│ (us-east-1)     │     │  DynamoDB Tables                                   │
└─────────────────┘     │  ├─ aws-services-lifecycle (deprecation data)      │
                        │  ├─ service-extraction-config (service settings)   │
                        │  ├─ deprecation-action-plans (remediation)         │
                        │  └─ health-events (Health API events)              │
                        └────────────────────────────────────────────────────┘
                                          │
                        ┌────────────────────────────────────────────────────┐
                        │  EventBridge Scheduler (weekly) ──▶ AgentCore      │
                        └────────────────────────────────────────────────────┘
```

## CDK Stack Decomposition

The infrastructure is organized into independent stacks for separation of concerns:

| Stack | Purpose | Key Resources |
|-------|---------|---------------|
| **Infra** | Build pipeline | ECR repository, CodeBuild project (ARM64), IAM roles, S3 source bucket |
| **Auth** | Authentication | Cognito User Pool, Identity Pool, IAM authenticated role |
| **Data** | Storage | DynamoDB tables (lifecycle, configs, action plans, health events), GSIs |
| **Runtime** | Agent execution | AgentCore runtime, Lambda waiter for CodeBuild |
| **Scheduler** | Automation | EventBridge Scheduler (weekly), SNS notifications, SQS DLQ |
| **Frontend** | Admin UI | S3 static hosting, CloudFront distribution with OAC |

### Dependency Graph

```
Infra ─────┐
Auth  ─────┼──▶ Runtime ──▶ Scheduler
Data  ─────┘                    │
                                ▼
Auth  ──────────▶ Frontend ◀── Runtime (ARN output)
Data  ──────────────────────────┘
```

## Data Flow

### Extraction Flow (Manual or Scheduled)

1. **Trigger**: Admin UI button or EventBridge Scheduler (weekly)
2. **AgentCore receives** payload: `{"service_name": "lambda", "force_refresh": true}`
3. **Router** (`main.py`): Routes to extraction or read operations
4. **Orchestrator** (`workflow_orchestrator.py`): Coordinates the extraction workflow
5. **Config lookup**: Reads service configuration from `service-extraction-config` table
6. **HTML fetch** (`data_extractor.py`): Downloads AWS documentation pages
7. **Hybrid extraction**:
   - BeautifulSoup parses HTML tables (structured data)
   - Amazon Nova 2 Lite normalizes and enriches extracted data
8. **Status categorization** (`database_writes.py`): Assigns `deprecated`, `extended_support`, or `end_of_life` based on date analysis
9. **Storage**: Writes to `aws-services-lifecycle` DynamoDB table
10. **Metadata update**: Updates extraction count and timestamp in config table

### Health Events Flow

1. **HealthCollector** (`health_collector.py`) connects to AWS Health API (global endpoint, us-east-1)
2. Filters events by configured services using `health_event_mapping`
3. Paginates through `DescribeEvents` with exponential backoff on throttling
4. Enriches events via `DescribeEventDetails` (batched, max 10 ARNs per call)
5. Stores formatted events in `health-events` DynamoDB table with 90-day TTL

### Read Flow (Dashboard, Metrics)

1. Frontend authenticates via Cognito → receives temporary IAM credentials
2. Frontend invokes AgentCore directly using AWS SDK (SigV4)
3. AgentCore routes to read operations (`database_reads.py`)
4. Returns metrics, deprecation lists, or service configs from DynamoDB

## DynamoDB Table Relationships

```
┌──────────────────────────────┐
│ service-extraction-config    │    Defines what to extract
│ PK: service_name             │
│ Fields: documentation_urls,  │
│   extraction_focus,          │
│   schema_key, enabled,       │
│   health_event_mapping       │
└──────────────┬───────────────┘
               │ 1:N
               ▼
┌──────────────────────────────┐
│ aws-services-lifecycle       │    Extracted deprecation items
│ PK: service_name             │
│ SK: item_id (schema_key#id)  │
│ GSI: status-index            │
│ Fields: status, source_url,  │
│   extraction_date,           │
│   service_specific (nested)  │
└──────────────┬───────────────┘
               │ 1:N (optional)
               ▼
┌──────────────────────────────┐
│ deprecation-action-plans     │    Remediation tracking
│ PK: plan_id (UUID)           │
│ GSI: owner-index             │
│ GSI: plan-status-index       │
│ Fields: service_name,        │
│   item_id, owner, priority,  │
│   target_date, plan_status   │
└──────────────────────────────┘

┌──────────────────────────────┐
│ health-events                │    AWS Health API events
│ PK: event_arn                │
│ Fields: health_service,      │
│   event_type_code, region,   │
│   status_code, description,  │
│   ttl (90-day expiry)        │
└──────────────────────────────┘
```

## Authentication Architecture

```
User ──▶ Cognito User Pool (email/password, no self-signup)
              │
              ▼ JWT ID Token
         Cognito Identity Pool
              │
              ▼ Temporary AWS Credentials (1h)
         AWS SDK (SigV4 signing)
              │
              ▼ bedrock-agentcore:InvokeAgentRuntime
         AgentCore Runtime
```

## Agent Module Structure

| Module | Responsibility |
|--------|----------------|
| `main.py` | AgentCore entry point, request routing |
| `workflow_orchestrator.py` | High-level extraction workflow coordination |
| `data_extractor.py` | HTML parsing + AI normalization engine |
| `health_collector.py` | AWS Health API integration with backoff |
| `database_reads.py` | Read operations (metrics, configs, listings) |
| `database_writes.py` | Write operations + intelligent status categorization |
| `action_plans.py` | Plan of Action CRUD operations |
| `account_discovery.py` | AWS account resource discovery |

## Status Categorization Logic

The system analyzes date fields to determine lifecycle status:

- **`deprecated`**: Announced for deprecation, retirement date > 1 year away
- **`extended_support`**: Within 1 year of retirement date (extra costs may apply)
- **`end_of_life`**: Past retirement date (immediate action required)

Date fields recognized: `target_retirement_date`, `retirement_date`, `end_of_support_date`, `end_of_life_date`, `block_function_create_date`, `block_function_update_date`, `end_of_extended_support_date`.

## Observability

- **CloudWatch Logs**: `/aws/bedrock-agentcore/runtimes/aws_services_lifecycle_agent-*`
- **X-Ray Tracing**: Distributed tracing enabled on AgentCore
- **CloudWatch Metrics**: Custom metrics in `bedrock-agentcore` namespace
- **SNS Notifications**: Extraction summaries sent after scheduled runs
