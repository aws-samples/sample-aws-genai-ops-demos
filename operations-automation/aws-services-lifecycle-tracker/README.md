# AWS Services Lifecycle Tracker

Comprehensive solution for automatically monitoring, extracting, and managing AWS service deprecation information across all AWS services. Built with [Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/resources/) and hybrid HTML parsing + AI normalization.

This system transforms manual deprecation tracking into an automated, scalable, and reliable process with intelligent status categorization, centralized storage, and comprehensive administrative interfaces for proactive AWS lifecycle management.

## 🚀 Key Features

- **🤖 Hybrid AI Extraction**: BeautifulSoup HTML parsing + Amazon Nova Lite AI normalization for reliable data extraction
- **⚡ Fast Extrant Status Categorization**: Automatically categorizes items as deprecated, extended_support, or end_of_life based on dates
- **🎛️ Admin Interface**: React-based UI with Cloudscape Design System for service configuration and monitoring
- **📊 Real-time Dashboard**: Live metrics showing status breakdown (75 deprecated, 19 extended support, 2 end of life)
- **🔐 Direct AgentCore Integration**: IAM-authenticated AWS SDK calls directly to AgentCore via Cognito Identity Pool (no API Gateway complexity)
- **⚙️ Service Configuration Management**: Add/modify AWS services via UI or service configuration files
- **📈 Scalable Architecture**: Serverless design that scales from single services to enterprise-wide monitoring

## Demo

![Demo](img/LifeCycle.gif)

## Architecture

```
                                    AWS Services Lifecycle Tracker
                                         Simplified Architecture

┌─────────────────┐     ┌─────────────────────────────────────────────────────────────────┐
│   Admin User    │────>│                    Frontend Stack                               │
└─────────────────┘     │  CloudFront ──▶ S3 Static ──▶ React Admin UI (Cloudscape)      │
                        └─────────────────────────────────────────────────────────────────┘
                                                      │
                                                      ▼
                        ┌─────────────────────────────────────────────────────────────────┐
                        │                    Auth Stack                                   │
                        │  Cognito User Pool + Identity Pool ──> AWS IAM Credentials      │
                        └─────────────────────────────────────────────────────────────────┘
                                                      │
                                                      ▼
                        ┌─────────────────────────────────────────────────────────────────┐
                        │                   Runtime Stack                                 │
                        │  AgentCore Runtime (Hybrid Extraction + Amazon Nova Lite)       │
                        │  ├─ Container: ECR Image (CodeBuild ARM64)                      │
                        │  ├─ AI Model: Amazon Nova Lite                                  │
                        │  ├─ Hybrid Approach: BeautifulSoup + LLM normalization          │
                        │  └─ Environment: Table names from Data Stack                    │
                        └─────────────────────────────────────────────────────────────────┘
                                                      │
                                                      ▼
┌─────────────────┐     ┌─────────────────────────────────────────────────────────────────┐
│ AWS             │<────│                    Data Stack                                   │
│ Documentation   │     │  DynamoDB Tables:                                               │
└─────────────────┘     │  ├─ aws-services-lifecycle (deprecation data)                   │
                        │  └─ service-extraction-config (service settings)                │
                        └─────────────────────────────────────────────────────────────────┘

                        ┌─────────────────────────────────────────────────────────────────┐
                        │                 Monitoring (Built-in)                           │
                        │  CloudWatch Logs + X-Ray Tracing + CloudWatch Metrics           │
                        └─────────────────────────────────────────────────────────────────┘

Simplified Flow:
1. Admin User ──▶ React UI ──▶ Cognito User Pool ──▶ Identity Pool ──▶ AWS Credentials ──▶ AgentCore (IAM)
2. AgentCore ──▶ Hybrid Extraction (BeautifulSoup + Amazon Nova Lite) ──▶ DynamoDB
3. All operations log to CloudWatch, traced by X-Ray
```

![Architecture Diagram](img/lifecycle.drawio.svg)

**System Flow:**
1. **Admin Interface**: React app with direct AgentCore integration via JWT authentication
2. **Hybrid Extraction**: AgentCore runtime combines HTML parsing + AI normalization for reliable data extraction
3. **Intelligent Categorization**: Automatically determines status (deprecated/extended_support/end_of_life) based on retirement dates
4. **Data Storage**: DynamoDB stores structured deprecation data with intelligent status indexing

**Key Components:**
- **🤖 Hybrid Data Extraction**: BeautifulSoup HTML parsing + Amazon Nova Lite AI normalization for 80-90% success rates
- **🧠 Smart Status Logic**: Analyzes `target_retirement_date` and other date fields to categorize lifecycle stages
- **🎛️ Service Configuration**: JSON-driven service definitions in `scripts/service_configs.json`
- **📊 Real-time Dashboard**: Live status breakdown showing actionable categorization of deprecation urgency
- **🔐 Direct AgentCore Access**: JWT-authenticated HTTPS calls directly to AgentCore (simplified architecture)
- **⚙️ Configurable Services**: Add new AWS services without code changes via service configuration files
- **📈 Comprehensive Monitoring**: CloudWatch logs, X-Ray tracing, and built-in observability

## Quick Start

### Prerequisites
- **AWS CLI v2.31.13 or later** installed and configured ([Installation Guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html))
  - Check your version: `aws --version`
  - AgentCore support was added in AWS CLI v2.31.13 (January 2025)
- **Node.js 22+** installed
- **Python 3.8+** installed (for configuration scripts)
- **AWS credentials** configured with permissions for CloudFormation, Lambda, S3, ECR, CodeBuild, DynamoDB, Cognito, and IAM via:
  - `aws configure` (access key/secret key)
  - AWS SSO: `aws sso login --profile <profile-name>`
  - Environment variables: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
- **No Docker required!** (CodeBuild handles container builds)

### ⚠️ Important: Region Requirements

**Amazon Bedrock AgentCore is only available in specific AWS regions.**

Before deploying, verify AgentCore availability in your target region by checking the [AWS AgentCore Regions Documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html).

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

> **Platform Notes:**
> - **Windows users**: Use the PowerShell script (`.ps1`)
> - **macOS/Linux users**: Use the bash script (`.sh`)
> - Both scripts perform identical operations and produce the same infrastructure
> - If you prefer PowerShell on macOS: `brew install --cask powershell` then run `pwsh deploy-all.ps1`

**Time:** ~10 minutes (most time is CodeBuild creating the container image)

**Done!** Your AWS Services Lifecycle Tracker is deployed and ready to monitor AWS service deprecations.

### Test Your System

1. **Access the admin interface:**
   - Open the CloudFront URL from deployment output
   - Sign in with Cognito to access the admin dashboard
   - View system health, metrics, and recent extractions

2. **Test manual extraction via UI:**
   - Navigate to the "Extract" section
   - Click "Test Extraction" for a specific service (e.g., Lambda)
   - Monitor real-time progress and results

3. **Trigger bulk extraction via UI:**
   - Click "Refresh All Services" for comprehensive extraction
   - Monitor orchestrator progress across all enabled services

4. **Check service configurations via UI:**
   - Navigate to "Services" section
   - View, edit, or add new service configurations
   - Enable/disable services for monitoring

5. **Command-line testing (optional):**
   ```bash
   # Test direct agent invocation (single service)
   aws bedrock-agentcore invoke-agent-runtime \
     --agent-runtime-arn $(aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerRuntime --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text) \
     --payload '{"service_name": "lambda", "force_refresh": true}'
   
   # Test bulk extraction (all services)
   aws bedrock-agentcore invoke-agent-runtime \
     --agent-runtime-arn $(aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerRuntime --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text) \
     --payload '{"services": "all", "force_refresh": true}'
   
   # View extracted data
   aws dynamodb scan --table-name aws-services-lifecycle --limit 5
   ```

6. **Monitor automated scheduling:**
   - Check EventBridge Scheduler schedules: `aws scheduler list-schedules --name-prefix aws-services-lifecycle`
   - View AgentCore logs: `aws logs tail /aws/bedrock-agentcore/runtimes/aws_services_lifecycle_agent-* --follow`


## Stack Architecture

| Stack Name | Purpose | Key Resources | Dependencies |
|------------|---------|---------------|--------------|
| **AWSServicesLifecycleTrackerInfra** | Build infrastructure | ECR Repository, CodeBuild Project, IAM Roles, S3 Bucket | None |
| **AWSServicesLifecycleTrackerAuth** | Authentication | Cognito User Pool, User Pool Client, Identity Pool, IAM Role | None |
| **AWSServicesLifecycleTrackerData** | Data storage | DynamoDB Tables (lifecycle data + service configs) | None |
| **AWSServicesLifecycleTrackerRuntime** | Agent runtime | AgentCore Runtime with hybrid extraction logic | Infra, Auth, Data |
| **AWSServicesLifecycleTrackerScheduler** | Automated scheduling | EventBridge Scheduler, SNS Topic, SQS Dead Letter Queue | Runtime |
| **AWSServicesLifecycleTrackerFrontend** | Admin interface | S3 Bucket, CloudFront Distribution, React UI | Auth, Runtime, Data |

## Project Structure

```
project-root/
├── agent/                          # Agent runtime code
│   ├── main.py                     # AgentCore entry point - request routing
│   ├── workflow_orchestrator.py    # High-level extraction workflow coordination
│   ├── data_extractor.py           # Low-level HTML parsing + AI extraction engine
│   ├── database_reads.py           # READ operations (metrics, service configs)
│   ├── database_writes.py          # WRITE operations + intelligent status categorization
│   ├── requirements.txt            # Python dependencies (boto3, beautifulsoup4, requests)
│   ├── Dockerfile                  # ARM64 container definition
│   └── test_*.py                   # Testing and debugging scripts
│
├── cdk/                            # Infrastructure as Code
│   ├── lib/
│   │   ├── infra-stack.ts          # Build infrastructure (ECR, CodeBuild, IAM)
│   │   ├── data-stack.ts           # DynamoDB tables + service config population
│   │   ├── auth-stack.ts           # Cognito User Pool + Identity Pool + IAM authentication
│   │   ├── runtime-stack.ts        # AgentCore runtime with hybrid extraction
│   │   ├── scheduler-stack.ts      # EventBridge direct AgentCore invocation
│   │   └── frontend-stack.ts       # CloudFront + S3 + React admin UI
│   └── package.json                # CDK dependencies
│
├── frontend/                       # React admin interface (Cloudscape Design System)
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx       # Status breakdown dashboard (75/19/2 display)
│   │   │   ├── Services.tsx        # Service configuration management
│   │   │   ├── Deprecations.tsx    # Deprecation data viewer with filters
│   │   │   └── Timeline.tsx        # Timeline view of upcoming deprecations
│   │   ├── App.tsx                 # Main app with navigation and auth
│   │   ├── AuthModal.tsx           # Cognito login/signup modal
│   │   ├── auth.ts                 # Cognito User Pool authentication
│   │   ├── api.ts                  # API helper functions
│   │   └── agentcore.ts            # AgentCore invocation with IAM credentials
│   └── package.json                # Frontend dependencies (React, Cloudscape, Cognito)
│
├── scripts/
│   ├── service_configs.json        # 🔧 KEY FILE: Service configuration definitions
│   ├── populate_service_configs.py # Populates DynamoDB with service configs
│   ├── build-frontend.ps1          # Frontend build with config injection (Windows)
│   └── build-frontend.sh           # Frontend build with config injection (macOS/Linux)
│
├── deploy-all.ps1                  # Complete deployment orchestration (Windows)
├── deploy-all.sh                   # Complete deployment orchestration (macOS/Linux)
└── README.md                       # This documentation
```

### 🔧 Key Files for Customization

| File | Purpose | When to Modify |
|------|---------|----------------|
| **`scripts/service_configs.json`** | **Service definitions** - Add new AWS services, configure extraction focus, documentation URLs | Add new services to monitor |
| **`agent/database_writes.py`** | **Status categorization logic** - Intelligent status determination based on dates | Customize status logic or add new date field patterns |
| **`frontend/src/pages/Dashboard.tsx`** | **Dashboard UI** - Status breakdown display and metrics | Customize dashboard layout or add new metrics |
| **`agent/data_extractor.py`** | **Extraction engine** - HTML parsing + AI normalization logic | Modify extraction approach or add new documentation sources |
| **`cdk/lib/data-stack.ts`** | **Service config population** - Loads service_configs.json into DynamoDB | Change how service configurations are deployed |

## Service Configuration Management

### 🔧 Adding New AWS Services

The system is designed to monitor any AWS service without code changes. All service definitions are stored in **`scripts/service_configs.json`**.

#### Service Configuration Schema

```json
{
  "services": {
    "lambda": {
      "name": "AWS Lambda",
      "documentation_urls": [
        "https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html#runtimes-deprecated"
      ],
      "extraction_focus": "Locate the 'Supported runtimes' or 'Deprecated runtimes' tables. Focus on the most important deprecation or block items from 2024 and later. For each runtime, extract: runtime name, runtime identifier, operating system, deprecation_date, block_create_date, and block_update_date.",
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
      "enabled": true,
      "last_extraction": "",
      "extraction_count": 0
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
      "enabled": true,
      "last_extraction": "",
      "extraction_count": 0
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
| **`last_extraction`** | Timestamp of most recent extraction | `"2025-11-07T10:30:00Z"` (auto-updated) |
| **`extraction_count`** | Number of successful extractions | `42` (auto-updated) |

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

1. **Edit `scripts/service_configs.json`**:
```json
{
  "services": {
    "your-new-service": {
      "name": "Your AWS Service",
      "documentation_urls": [
        "https://docs.aws.amazon.com/your-service/latest/userguide/deprecations.html"
      ],
      "extraction_focus": "Extract deprecation information from the deprecation table. Include version numbers, deprecation dates, and replacement recommendations.",
      "schema_key": "versions",
      "item_properties": {
        "name": "Version name",
        "identifier": "Version identifier",
        "deprecation_date": "When deprecated",
        "end_of_support_date": "When support ends"
      },
      "required_fields": ["name", "identifier"],
      "enabled": true,
      "last_extraction": "",
      "extraction_count": 0
    }
  }
}
```

2. **Redeploy the data stack** to update DynamoDB:
```bash
cd cdk
npx cdk deploy AWSServicesLifecycleTrackerData --no-cli-pager
```

3. **Test extraction** via the admin UI or CLI:
```bash
# Via admin UI: Navigate to Services → Test extraction for "your-new-service"

# Via CLI:
python agent/test_direct_agent.py  # Modify service_name in the script
```

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

### Agent Architecture

The agent is organized into modular components with clear separation of concerns:

**`main.py`** - AgentCore entry point and request routing
- Routes requests to either API actions (reads) or extraction operations (writes)
- Handles payload parsing and error handling
- Minimal logic - just routing between different operation types

**`workflow_orchestrator.py`** - High-level extraction workflow coordination
- `extract_service_lifecycle()` - Main orchestration function
- Coordinates: config retrieval → data extraction → storage → metadata updates
- Handles error recovery and comprehensive result reporting
- **Role**: Workflow management and coordination

**`data_extractor.py`** - Low-level hybrid extraction engine
- `DataExtractor` class with hybrid HTML + AI approach
- `_fetch_html_tables()` - BeautifulSoup HTML parsing for structured data
- `_llm_extract_deprecation_data()` - Amazon Nova Lite AI normalization
- `_build_extraction_prompt()` - Service-specific AI prompt generation
- **Role**: Pure data extraction mechanics

**`database_reads.py`** - READ operations (future API candidates)
- `list_services()` - Get all service configurations
- `list_deprecations()` - Query deprecation items with filters
- `get_metrics()` - Calculate dashboard metrics with status breakdown
- `get_service_config()` - Retrieve service configuration
- **Cost optimization opportunity**: Could be moved to API Gateway + Lambda for 80-95% cost reduction

**`database_writes.py`** - WRITE operations + intelligent status logic
- `categorize_item_status()` - **🧠 Intelligent status categorization based on dates**
- `store_deprecation_data()` - Store extracted items with smart status assignment
- `update_service_metadata()` - Track extraction history and success rates
- `validate_item_against_config()` - Ensure data quality against service schemas
- **Keep with agent**: Core extraction workflow functionality

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

### Architecture Benefits

**Clear Separation of Concerns:**
- **Orchestration** (`workflow_orchestrator.py`) - High-level workflow
- **Extraction** (`data_extractor.py`) - Low-level data processing
- **Intelligence** (`database_writes.py`) - Smart status categorization
- **Data Access** (`database_reads.py`) - Query and metrics

**Future Optimization Path:**
When ready to optimize costs:
1. Move `database_reads.py` to API Gateway + Lambda
2. Keep extraction workflow on AgentCore for AI processing
3. **Result**: 80-95% cost reduction for read operations while maintaining AI capabilities

### Deployment Flow

The `deploy-all.ps1` script orchestrates the complete deployment:

1. **Verify AWS credentials** (checks AWS CLI configuration)
2. **Check AWS CLI version** (requires v2.31.13+ for AgentCore support)
3. **Check AgentCore availability** (verifies service is available in your configured region)
4. **Install CDK dependencies** (cdk/node_modules)
5. **Install frontend dependencies** (frontend/node_modules, includes amazon-cognito-identity-js)
6. **Create placeholder frontend build** (for initial deployment)
7. **Bootstrap CDK environment** (sets up CDK deployment resources in your AWS account/region)
8. **Deploy AWSServicesLifecycleTrackerInfra** - Creates build pipeline resources:
   - ECR repository for agent container images
   - IAM role for AgentCore runtime
   - S3 bucket for CodeBuild sources
   - CodeBuild project for ARM64 builds
9. **Deploy AWSServicesLifecycleTrackerData** - Creates data storage:
   - DynamoDB table for lifecycle data (`aws-services-lifecycle`)
   - DynamoDB table for service configurations (`service-extraction-config`)
   - Global Secondary Indexes for efficient querying
10. **Deploy AWSServicesLifecycleTrackerAuth** - Creates authentication resources:
    - Cognito User Pool (email/password, admin-only, no self-signup)
    - User Pool Client for frontend authentication
    - Cognito Identity Pool for AWS credential exchange
    - IAM Role with AgentCore invocation permissions
    - Password policy (min 8 chars, uppercase, lowercase, digit)
11. **Deploy AWSServicesLifecycleTrackerRuntime** - Deploys agent with built-in auth:
    - Uploads agent source code to S3
    - Triggers CodeBuild via Custom Resource
    - **Lambda waiter polls CodeBuild** (5-10 minutes)
    - Creates AgentCore runtime with IAM authentication and hybrid extraction
12. **Build frontend with full configuration, then deploy AWSServicesLifecycleTrackerFrontend**:
    - Retrieves all stack outputs (AgentCore ARN, Cognito config)
    - Builds React admin interface with injected configuration
    - S3 bucket for static hosting
    - CloudFront distribution with OAC
    - Deploys admin interface with direct AgentCore integration

### Request Flow

#### **Automated Extraction Flow:**
1. EventBridge Scheduler triggers weekly extraction (every 7 days)
2. EventBridge Scheduler directly invokes AgentCore with payload (no Lambda orchestrator)
3. AgentCore queries DynamoDB for enabled service configurations
4. AgentCore processes all enabled services with refresh_origin: "Auto"
5. AgentCore executes agent in isolated container (microVM)
6. Agent fetches AWS documentation and extracts deprecation data using Amazon Nova Lite
7. Agent stores structured data in DynamoDB lifecycle table
8. Orchestrator collects results and sends SNS notification with summary

#### **Manual UI-Triggered Flow:**
1. User signs in via Cognito User Pool (email/password authentication)
2. Frontend receives JWT ID token from Cognito User Pool
3. Frontend exchanges ID token for AWS credentials via Cognito Identity Pool
4. User triggers extraction via admin UI (single service, multiple services, or "all")
5. Frontend invokes AgentCore using AWS SDK with IAM credentials (SigV4 signing)
6. AgentCore validates IAM credentials and executes hybrid extraction (BeautifulSoup + AI normalization)
7. AgentCore stores results in DynamoDB and returns real-time results
8. Frontend displays live progress and results to user

#### **Configuration Management Flow:**
1. User accesses service configuration via admin UI
2. Frontend calls AgentCore directly for CRUD operations
3. AgentCore manages DynamoDB service configuration table
4. Changes immediately affect future manual extractions

### **Authentication Architecture Deep Dive**

The system uses a secure, multi-layer authentication approach:

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Authentication Flow                             │
└─────────────────────────────────────────────────────────────────────┘

1. User Login (Cognito User Pool)
   ├─ User enters email/password in frontend
   ├─ amazon-cognito-identity-js authenticates with User Pool
   └─ Returns: JWT ID Token (contains user identity + 'aud' claim)

2. Credential Exchange (Cognito Identity Pool)
   ├─ Frontend sends ID Token to Identity Pool
   ├─ Identity Pool validates token with User Pool
   ├─ Identity Pool assumes IAM Role (AuthenticatedRole)
   └─ Returns: Temporary AWS Credentials (AccessKeyId, SecretKey, SessionToken)
        └─ Valid for 1 hour, automatically refreshed

3. AgentCore Invocation (IAM Authentication)
   ├─ Frontend creates BedrockAgentCoreClient with credentials
   ├─ AWS SDK signs request with SigV4 (IAM signature)
   ├─ AgentCore validates IAM signature
   └─ Executes agent code with full AWS permissions
```

**Key Components:**

1. **Cognito User Pool** (`auth-stack.ts`)
   - Stores admin user accounts (email/password)
   - Self-signup disabled (admin-only access)
   - Issues JWT tokens (ID Token, Access Token, Refresh Token)
   - ID Token contains: `sub`, `email`, `aud` (audience), `cognito:username`

2. **Cognito Identity Pool** (`auth-stack.ts`)
   - Exchanges JWT ID Token for AWS credentials
   - Maps authenticated users to IAM Role
   - Provides temporary credentials (1 hour expiry)
   - Enables AWS SDK usage from browser

3. **IAM Role - AuthenticatedRole** (`auth-stack.ts`)
   - Assumed by authenticated users via Identity Pool
   - Permissions: `bedrock-agentcore:InvokeAgentRuntime`
   - Trust policy: Only Cognito Identity Pool can assume
   - Follows least privilege principle

4. **Frontend Authentication** (`frontend/src/auth.ts`)
   - `si

### **Simplified Architecture**
The system uses a direct integration approach for maximum simplicity and reliability:

- **Direct UI → AgentCore**: No intermediate API Gateway or Lambda
- **IAM Authentication**: Cognito Identity Pool provides AWS credentials for AgentCore invocation
- **Hybrid Extraction**: BeautifulSoup HTML parsing + AI normalization for reliable results
- **Real-time Feedback**: Direct AWS SDK calls provide immediate results
- **Environment-Driven**: Uses environment variables for table names (no hardcoded resources)
- **Comprehensive Logging**: All operations logged to CloudWatch with X-Ray tracing

**AgentCore Request Formats:**
```json
// Single service extraction
{"service_name": "lambda", "force_refresh": true}

// Multiple services extraction (handled by agent)
{"services": ["lambda", "eks"], "force_refresh": true}

// All enabled services extraction (handled by agent)
{"services": "all", "force_refresh": true}
```

### **Automated Scheduling**
EventBridge Scheduler provides reliable, serverless scheduling:

- **📅 Weekly Extraction**: Every 7 days - All enabled services with Auto refresh origin

**Schedule Configuration:**
```bash
# View current EventBridge Scheduler schedules
aws scheduler list-schedules --name-prefix aws-services-lifecycle

# Get schedule details
aws scheduler get-schedule --name aws-services-lifecycle-weekly-extraction

# Monitor AgentCore logs directly
aws logs tail /aws/bedrock-agentcore/runtimes/aws_services_lifecycle_agent-* --follow
```

### **Service Configuration Management**
Dynamic service management without code changes:

- **Add New Services**: Configure new AWS services via admin UI or CLI
- **Extraction Focus**: Service-specific AI instructions for optimal extraction
- **Documentation URLs**: Multiple URLs per service for comprehensive coverage
- **Enable/Disable**: Turn services on/off without affecting others
- **Scheduling Control**: Per-service extraction frequency configuration

**Service Configuration Schema:**
```json
{
  "name": "AWS Elastic Beanstalk",
  "documentation_urls": [
    "https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/platforms-schedule.html"
  ],
  "extraction_focus": "Locate the 'Retiring' or 'Retired' tables or similar. Focus on the most important retirement items from 2025 and later. For each platform branch, extract: Runtime version, Platform branch name, Operating system, and retirement dates.",
  "schema_key": "platform_branches",
  "item_properties": {
    "name": "Full platform branch name",
    "identifier": "Platform branch identifier",
    "runtime_version": "Runtime version",
    "operating_system": "Operating system",
    "target_retirement_date": "Target retirement date",
    "retirement_date": "Actual retirement date"
  },
  "required_fields": ["name", "identifier", "runtime_version"],
  "enabled": true,
  "last_extraction": "",
  "extraction_count": 0
}
```

### **Admin Interface Integration**
Comprehensive UI for manual operations and monitoring:

- **🔄 Manual Triggers**: Instant extraction for any service or combination
- **🧪 Test Extractions**: Validate service configurations before scheduling
- **📊 Real-time Monitoring**: Live system health, metrics, and extraction status
- **⚙️ Configuration Management**: Add, edit, delete service configurations
- **📈 System Metrics**: Track extraction success rates, data freshness, and system health

**AgentCore Operations via UI:**
```json
// Manual extraction triggers
{"service_name": "lambda", "force_refresh": true}
{"services": ["lambda", "eks"], "force_refresh": true}
{"services": "all", "force_refresh": true}

// Service configuration management
{"action": "list_services"}
{"action": "get_service_config", "service_name": "lambda"}
{"action": "update_service_config", "service_name": "lambda", "config": {...}}

// Data viewing and metrics
{"action": "list_deprecations", "service_name": "lambda"}
{"action": "get_metrics"}
{"action": "health_check"}
```

## Key Components

### 1. Authentication (`AWSServicesLifecycleTrackerAuth` stack)
- **Cognito User Pool** for admin user management (no self-signup)
- **Cognito Identity Pool** for AWS credential exchange
- **IAM Role** with AgentCore invocation permissions (`bedrock-agentcore:InvokeAgentRuntime`)
- Email-based authentication with verification
- Password policy: min 8 chars, uppercase, lowercase, digit
- **Frontend integration** via AWS SDK and amazon-cognito-identity-js
- Admin users must be created manually via AWS CLI or Console
- **IAM Authentication Flow**: User Pool → ID Token → Identity Pool → AWS Credentials → AgentCore (SigV4)

### 2. Agent (`agent/main.py`)
- AgentCore entry point with request routing
- Uses Amazon Nova Lite for AI normalization
- Hybrid extraction: BeautifulSoup + LLM
- Direct DynamoDB integration for data storage

### 3. Container Build
- ARM64 architecture (native AgentCore support)
- Python 3.13 slim base image
- Built via CodeBuild (no local Docker required)
- Automatic build on deployment
- Build history and logs in AWS Console

### 4. Lambda Waiter (Critical Component)
- Custom Resource that waits for CodeBuild completion
- Polls every 30 seconds, 15-minute timeout
- Returns minimal response to CloudFormation (<4KB)
- Ensures image exists before AgentCore runtime creation
- **Why needed:** CodeBuild's `batchGetBuilds` response exceeds CloudFormation's 4KB Custom Resource limit

### 5. Direct AgentCore Integration
- Frontend calls AgentCore directly using AWS SDK
- IAM authentication with SigV4 request signing
- Cognito Identity Pool provides temporary AWS credentials
- No API Gateway or Lambda intermediaries required

### 6. IAM Permissions
The execution role includes:
- Bedrock model invocation
- ECR image access
- CloudWatch Logs & Metrics
- X-Ray tracing
- AgentCore Identity (workload access tokens)

### 7. Built-in Observability
- **CloudWatch Logs:** 
  - AgentCore: `/aws/bedrock-agentcore/runtimes/aws_services_lifecycle_agent-*`
- **X-Ray Tracing:** Distributed tracing enabled for AgentCore operations
- **CloudWatch Metrics:** Custom metrics in `bedrock-agentcore` namespace
- **Built-in Monitoring:** AgentCore provides comprehensive observability out of the box

## Manual Deployment

If you prefer to deploy stacks individually:

### 1. Bootstrap CDK (one-time setup)
```bash
cd cdk
npx cdk bootstrap --no-cli-pager
```

### 2. Deploy Infrastructure
```bash
cd cdk
npx cdk deploy AWSServicesLifecycleTrackerInfra --no-cli-pager
```

### 3. Deploy Data Storage
```bash
cd cdk
npx cdk deploy AWSServicesLifecycleTrackerData --no-cli-pager
```

### 4. Deploy Authentication
```bash
cd cdk
npx cdk deploy AWSServicesLifecycleTrackerAuth --no-cli-pager
```

### 5. Deploy Runtime (triggers build automatically)
```bash
cd cdk
npx cdk deploy AWSServicesLifecycleTrackerRuntime --no-cli-pager
```
*Note: This will pause for 5-10 minutes while CodeBuild runs*

### 6. Deploy Scheduler
```bash
cd cdk
npx cdk deploy AWSServicesLifecycleTrackerScheduler --no-cli-pager
```

### 7. Deploy Frontend & Admin API

**Windows (PowerShell):**
```powershell
$agentRuntimeArn = aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerRuntime --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager
$region = aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerRuntime --query "Stacks[0].Outputs[?OutputKey=='Region'].OutputValue" --output text --no-cli-pager
$userPoolId = aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerAuth --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text --no-cli-pager
$userPoolClientId = aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerAuth --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" --output text --no-cli-pager
.\scripts\build-frontend.ps1 -UserPoolId $userPoolId -UserPoolClientId $userPoolClientId -AgentRuntimeArn $agentRuntimeArn -Region $region
cd cdk
npx cdk deploy AWSServicesLifecycleTrackerFrontend --no-cli-pager
```

**macOS/Linux (Bash):**
```bash
AGENT_RUNTIME_ARN=$(aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerRuntime --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager)
REGION=$(aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerRuntime --query "Stacks[0].Outputs[?OutputKey=='Region'].OutputValue" --output text --no-cli-pager)
USER_POOL_ID=$(aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerAuth --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text --no-cli-pager)
USER_POOL_CLIENT_ID=$(aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerAuth --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" --output text --no-cli-pager)
./scripts/build-frontend.sh "$USER_POOL_ID" "$USER_POOL_CLIENT_ID" "$AGENT_RUNTIME_ARN" "$REGION"
cd cdk
npx cdk deploy AWSServicesLifecycleTrackerFrontend --no-cli-pager
```

## Customizing the System

### Updating Agent Code

To modify the extraction logic or add new capabilities:

1. **Edit agent files**:
   - `agent/data_extractor.py` - Modify HTML parsing or AI extraction logic
   - `agent/database_writes.py` - Customize status categorization logic
   - `agent/workflow_orchestrator.py` - Change extraction workflow
   - `agent/requirements.txt` - Add new Python dependencies

2. **Redeploy runtime stack**:
   ```bash
   cd cdk
   npx cdk deploy AWSServicesLifecycleTrackerRuntime --no-cli-pager
   ```
   *Note: This triggers CodeBuild to rebuild the container with your changes*

### Customizing Status Categorization

The intelligent status logic is in `agent/database_writes.py`. To modify how items are categorized:

```python
def categorize_item_status(item: Dict[str, Any]) -> str:
    # Add your custom logic here
    # Example: Custom thresholds for extended_support
    if retirement_date:
        days_until_retirement = (retirement_date - current_date).days
        if days_until_retirement <= 90:  # Custom: 3 months instead of 6
            return 'extended_support'
    # ... rest of logic
```

### Adding New Date Field Patterns

To recognize new date field names from AWS documentation:

```python
# In categorize_item_status(), add to date_fields list:
date_fields = [
    'end_of_support_date', 'end_of_life_date', 'eol_date',
    'target_retirement_date', 'retirement_date',
    'your_new_date_field',  # Add custom field names here
    'another_date_field'
]
```

## Updating Service Configurations

To add or modify AWS services for monitoring:

### Via Admin UI (Recommended):
1. Access the admin interface via CloudFront URL
2. Navigate to "Services" section
3. Click "Add Service" or edit existing services
4. Configure extraction focus, documentation URLs, and scheduling
5. Enable/disable services as needed

### Via Command Line:
```bash
# Add new service configuration
aws dynamodb put-item --table-name service-extraction-config --item '{
  "service_name": {"S": "elasticbeanstalk"},
  "display_name": {"S": "AWS Elastic Beanstalk"},
  "documentation_urls": {"L": [{"S": "https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/platforms-retiring.html"}]},
  "extraction_focus": {"S": "Extract platform version deprecations and retirement schedules"},
  "enabled": {"BOOL": true}
}'

# Trigger extraction for new service
aws lambda invoke --function-name aws-services-lifecycle-orchestrator \
  --payload '{"service_name": "elasticbeanstalk", "force_refresh": true}' response.json
```

The deployment will:
- Upload new agent code to S3
- Trigger CodeBuild to rebuild container
- Wait for build completion
- Update AgentCore runtime with new image

## Cleanup

```bash
cd cdk
npx cdk destroy AWSServicesLifecycleTrackerFrontend --no-cli-pager
npx cdk destroy AWSServicesLifecycleTrackerScheduler --no-cli-pager
npx cdk destroy AWSServicesLifecycleTrackerRuntime --no-cli-pager
npx cdk destroy AWSServicesLifecycleTrackerAuth --no-cli-pager
npx cdk destroy AWSServicesLifecycleTrackerData --no-cli-pager
npx cdk destroy AWSServicesLifecycleTrackerInfra --no-cli-pager
```

**Note:** Cognito User Pool will be deleted along with all user accounts.

## Troubleshooting

### "Container failed to start"
Check CloudWatch logs:
```bash
aws logs tail /aws/bedrock-agentcore/runtimes/strands_agent-* --follow --no-cli-pager
```

### "Image not found in ECR"
Redeploy runtime stack - it will trigger a new build:
```bash
cd cdk
npx cdk deploy AgentCoreRuntime --no-cli-pager
```

### "Build timeout after 15 minutes"
Check CodeBuild console for build status. If build is still running, wait for completion and redeploy runtime stack.

### CodeBuild fails
Check build logs:
```bash
aws logs tail /aws/codebuild/bedrock-agentcore-strands-agent-builder --follow --no-cli-pager
```

### Frontend shows errors
Verify AgentCore Runtime ARN and Cognito config are correct:
```bash
aws cloudformation describe-stacks --stack-name AgentCoreRuntime --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager
aws cloudformation describe-stacks --stack-name AgentCoreRuntime --query "Stacks[0].Outputs[?OutputKey=='Region'].OutputValue" --output text --no-cli-pager
aws cloudformation describe-stacks --stack-name AgentCoreAuth --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text --no-cli-pager
aws cloudformation describe-stacks --stack-name AgentCoreAuth --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" --output text --no-cli-pager
```

### Email verification not received
- Check spam/junk folder
- Verify email address is correct
- Wait a few minutes (can take up to 5 minutes)
- Try signing up with a different email

### Verify deployment status
Check all stack statuses:
```bash
aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerInfra --query "Stacks[0].StackStatus" --no-cli-pager
aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerData --query "Stacks[0].StackStatus" --no-cli-pager
aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerAuth --query "Stacks[0].StackStatus" --no-cli-pager
aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerRuntime --query "Stacks[0].StackStatus" --no-cli-pager
aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerScheduler --query "Stacks[0].StackStatus" --no-cli-pager
aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerFrontend --query "Stacks[0].StackStatus" --no-cli-pager
```

### Monitor automated extractions
Check scheduled extraction activity:
```bash
# View EventBridge Scheduler schedules
aws scheduler list-schedules --name-prefix aws-services-lifecycle

# Get schedule details
aws scheduler get-schedule --name aws-services-lifecycle-weekly-extraction

# Check AgentCore logs for scheduled extractions
aws logs tail /aws/bedrock-agentcore/runtimes/aws_services_lifecycle_agent-* --follow --no-cli-pager

# View recent extractions
aws dynamodb scan --table-name aws-services-lifecycle \
  --filter-expression "extraction_date > :date" \
  --expression-attribute-values '{":date":{"S":"2025-10-26"}}' \
  --query "Items[*].[service_name.S,extraction_date.S,#name.S]" \
  --expression-attribute-names '{"#name":"name"}' --output table
```

## Testing and Monitoring

### Testing Extraction via Admin UI (Recommended)

1. **Access the admin interface** at the CloudFront URL from deployment output
2. **Sign in** with your Cognito account
3. **Navigate to Services** section
4. **Click "Test Extraction"** for any service (e.g., Lambda, Elastic Beanstalk)
5. **Monitor real-time progress** and results in the UI

### Manual Extraction via CLI (Advanced)

For direct AgentCore testing, use the provided test scripts:

```bash
# Test Lambda extraction
cd agent
python test_direct_agent.py

# Test Elastic Beanstalk extraction  
python test_elasticbeanstalk.py

# Check dashboard metrics
python test_metrics.py

# Debug status categorization
python debug_status.py
```

### Supported Services

The system currently monitors these AWS services:

| Service | Status | Items Tracked | Key Date Fields |
|---------|--------|---------------|-----------------|
| **AWS Lambda** | ✅ Active | ~26 deprecated runtimes | `deprecation_date`, `block_function_create_date`, `block_function_update_date` |
| **Elastic Beanstalk** | ✅ Active | ~21 platform versions | `target_retirement_date`, `retirement_date` |
| **Amazon EKS** | ⚙️ Configured | Kubernetes versions | `end_of_support_date`, `end_of_extended_support_date` |
| **Amazon RDS** | ⚙️ Configured | Database engine versions | `end_of_standard_support_date`, `end_of_extended_support_date` |
| **Amazon ECS** | ⚙️ Configured | Platform versions | `retirement_date` |
| **OpenSearch** | ⚙️ Configured | Service versions | `end_of_support_date` |
| **ElastiCache** | ⚙️ Configured | Engine versions | `end_of_support_date` |

### Viewing Extracted Data

**Via Admin UI (Recommended):**
- Navigate to "Deprecations" section
- Filter by service, status, or date ranges
- Export data as CSV or JSON

**Via CLI:**
```bash
# View all deprecations for a service
aws dynamodb query \
  --table-name aws-services-lifecycle \
  --key-condition-expression "service_name = :svc" \
  --expression-attribute-values '{":svc":{"S":"lambda"}}' \
  --no-paginate

# View items by status (cross-service)
aws dynamodb query \
  --table-name aws-services-lifecycle \
  --index-name status-index \
  --key-condition-expression "#status = :status" \
  --expression-attribute-names '{"#status":"status"}' \
  --expression-attribute-values '{":status":{"S":"extended_support"}}' \
  --no-paginate
```

### Monitoring System Health

**Dashboard Metrics:**
- **Total Services**: 7 configured services
- **Total Items**: ~96 deprecation items tracked
- **Status Breakdown**: 75 deprecated, 19 extended support, 2 end of life

**CloudWatch Logs:**
```bash
# AgentCore runtime logs
aws logs tail /aws/bedrock-agentcore/runtimes/aws_services_lifecycle_agent-* --follow

# AgentCore logs for scheduled extractions (every minute)
aws logs tail /aws/bedrock-agentcore/runtimes/aws_services_lifecycle_agent-* --follow
```

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

### Why CDK Instead of AgentCore CLI?

While AgentCore CLI (`agentcore launch`) is simpler for basic deployments, this project uses AWS CDK for:

- **Full-stack deployment**: Includes authentication, frontend, data storage, and monitoring
- **Production readiness**: Proper IAM roles, security policies, and resource organization  
- **Team collaboration**: Version-controlled, reproducible infrastructure
- **Customization flexibility**: Easy to extend with additional AWS services and features

### Why Hybrid Extraction (HTML + AI)?

**Reliability**: BeautifulSoup HTML parsing provides 100% reliable table extraction
**Intelligence**: AI normalization handles variations in AWS documentation formats  
**Success Rate**: 80-90% extraction success vs 50% with pure AI tool calling
**Cost Efficiency**: Minimal token usage while maintaining high data quality

### Stack Organization

| Stack | Purpose | Update Frequency |
|-------|---------|------------------|
| **Infra** | Build pipeline (ECR, CodeBuild) | Rarely |
| **Auth** | Cognito User Pool | Rarely |  
| **Data** | DynamoDB + service configs | When adding services |
| **Runtime** | AgentCore + extraction logic | When updating agent code |
| **Frontend** | React UI + CloudFront | When updating UI |

This separation enables independent updates without full system rebuilds.

## Security

### Authentication & Authorization
- **Admin-only access** - Cognito User Pool with self-signup disabled
- **IAM-based authentication** - AgentCore invoked with temporary AWS credentials via Cognito Identity Pool
- **Email verification** - Users must verify email before access
- **Password policy** - Minimum 8 characters, uppercase, lowercase, digit required
- **Temporary credentials** - AWS credentials automatically expire and refresh (1 hour default)
- **Least privilege IAM** - Authenticated role only has `bedrock-agentcore:InvokeAgentRuntime` permission
- **No long-lived credentials** - Frontend never stores AWS access keys

### Network & Data Security
- **HTTPS only** - Frontend served via CloudFront with TLS
- **Origin Access Control (OAC)** - S3 bucket only accessible via CloudFront
- **VPC isolation** - AgentCore Runtime runs in isolated microVMs
- **DynamoDB encryption** - Data encrypted at rest using AWS managed keys
- **CloudWatch Logs encryption** - Log data encrypted at rest

### Application Security
- **Container scanning** - ECR automatically scans images for vulnerabilities
- **Minimal IAM permissions** - Each component has only required permissions
- **No hardcoded secrets** - All configuration via environment variables and CloudFormation outputs
- **Session management** - JWT tokens stored in browser session storage (cleared on tab close)
- **Input validation** - Service configurations validated before storage
- **Error handling** - Sensitive information not exposed in error messages

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
1. **Change default admin email** in `cdk/lib/auth-stack.ts` before deployment
2. **Use strong passwords** for admin accounts (consider password manager)
3. **Enable MFA** for admin users (optional, via Cognito Console)
4. **Monitor CloudWatch Logs** for suspicious activity
5. **Rotate credentials** if compromised (delete and recreate user)
6. **Review IAM policies** periodically to ensure least privilege
7. **Enable CloudTrail** for audit logging of AWS API calls

## Cost Estimate

Approximate monthly costs for AWS Services Lifecycle Tracker:

**Core Services:**
- **AgentCore Runtime**: $0.10 per hour active + $0.000008 per request
  - With weekly scheduled extractions: ~$0.50-1/month (active only during extraction) - **Current configuration**
  - With daily scheduled extractions: ~$3-5/month (active only during extraction)
  - With hourly scheduled extractions: ~$72/month (24/7 active)
- **Bedrock Model Usage**: Pay-per-token
  - Amazon Nova Lite: ~$0.00006 per 1K input tokens, ~$0.00024 per 1K output tokens
  - Typical usage: $2-5/month depending on extraction frequency and service count
- **DynamoDB**: On-demand pricing
  - Write requests: $1.25 per million write request units
  - Read requests: $0.25 per million read request units
  - Storage: $0.25 per GB-month
  - Typical usage: $1-5/month for lifecycle data storage
- **EventBridge Scheduler**: $1.00 per million invocations
  - Weekly schedule: ~$0.004/month (4 invocations/month) - **Current configuration**
  - Daily schedule: ~$0.03/month (30 invocations/month)
  - Hourly schedule: ~$0.72/month (720 invocations/month)

**Frontend & Auth:**
- **Cognito**: Free for first 50,000 MAUs (Monthly Active Users)
- **CloudFront**: $0.085 per GB + $0.01 per 10,000 requests (~$1-2/month)
- **S3**: $0.023 per GB-month (negligible for static hosting, <$1/month)

**Build & Deployment:**
- **ECR**: $0.10 per GB-month for container image storage (~$0.50/month)
- **CodeBuild**: $0.005 per build minute (ARM64) - only during deployments (~$0.50 per deployment)

**Monitoring:**
- **CloudWatch Logs**: $0.50 per GB ingested + $0.03 per GB stored (~$2-5/month)
- **X-Ray**: $5.00 per million traces recorded + $0.50 per million traces retrieved (~$1-3/month)

**Free Services:**
- **CloudFormation**: Free for stack operations
- **IAM**: Free

**Total Estimated Monthly Cost:**
- **With weekly extraction (cost-optimized)**: ~$5-10/month - **Current configuration**
- **With daily extraction (balanced monitoring)**: ~$12-25/month
- **With hourly extraction (aggressive monitoring)**: ~$80-95/month

**Cost Optimization Tips:**
1. **Current configuration uses weekly schedule**: Already optimized for cost (~$5-10/month total)
2. **Selective service monitoring**: Only enable services you actively use
3. **Implement caching**: Skip extraction if documentation hasn't changed (check ETag headers)
4. **Optimize prompts**: Reduce token usage by making extraction prompts more concise

## Frontend Architecture

The admin interface is built with [AWS Cloudscape Design System](https://cloudscape.design/), AWS's open-source design system for building intuitive, accessible web applications.

### Key Features

- **Dashboard**: Real-time metrics with status breakdown (deprecated, extended support, end of life)
- **Services Management**: Configure, enable/disable, and test AWS service extractions
- **Deprecations Viewer**: Browse and filter deprecation data with advanced search
- **Timeline View**: Visualize upcoming deprecation deadlines
- **Authentication**: Cognito User Pool + Identity Pool for IAM-based access
- **Direct AgentCore Integration**: AWS SDK calls with temporary IAM credentials (no API Gateway)

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
- **Test extraction** via admin UI before enabling automated scheduling

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

## Resources

### Key Documentation
- **[AgentCore Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-agentcore.html)** - Core AgentCore concepts
- **[AgentCore JWT Authentication](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html#invoke-agent)** - JWT Bearer token setup
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

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## Support

For issues and questions:
- Check the troubleshooting section
- Review AWS Bedrock documentation
- Open an issue in the repository
## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.