# AI IAM Access Analyzer Assistant — Architecture

## Architecture Diagram

![AI IAM Access Analyzer Assistant — Architecture](docs/architecture-diagram.svg)

## Conversation Flow

```
User: "What are my critical IAM findings?"
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│ 1. Frontend sends message to API Gateway            │
│ 2. Cognito authorizer validates JWT token           │
│ 3. Conversation Lambda invoked                      │
│ 4. Bedrock Converse called with message + tools     │
│ 5. Bedrock returns toolUse: list_findings           │
│    with params: {severity: "CRITICAL"}              │
│ 6. Lambda invokes list_findings tool                │
│ 7. Tool queries Security Hub GetFindings API        │
│ 8. Results returned to Bedrock as toolResult        │
│ 9. Bedrock synthesizes natural language response    │
│ 10. Response streamed back to frontend              │
└─────────────────────────────────────────────────────────┘
```

## Tool Definitions

### list_findings

Queries Security Hub for IAM Access Analyzer findings with filtering.

| Parameter | Type | Description |
|-----------|------|-------------|
| severity | string | Filter by severity (CRITICAL, HIGH, MEDIUM, LOW, INFORMATIONAL) |
| resource_type | string | Filter by resource type (IAMRole, IAMUser, IAMPolicy, S3Bucket, KMSKey) |
| status | string | Filter by status (ACTIVE, ARCHIVED, RESOLVED) |
| limit | integer | Max findings to return (default: 20) |

**AWS APIs Used**: `securityhub:GetFindings` with IAM Access Analyzer product filter

### generate_policy

Generates a least-privilege policy based on CloudTrail activity analysis.

| Parameter | Type | Description |
|-----------|------|-------------|
| role_name | string | IAM role to analyze |
| lookback_days | integer | Days of CloudTrail history to analyze (default: 90) |
| output_format | string | Policy format: json, cdk_python, cdk_typescript, cloudformation |

**AWS APIs Used**: `cloudtrail:LookupEvents`, `iam:GetRole`, `iam:ListAttachedRolePolicies`, `iam:GetPolicy`, `iam:GetPolicyVersion`, `access-analyzer:GeneratePolicy` (if available), `bedrock:Converse` (for policy synthesis)

### check_dependencies

Maps what roles, users, and resources depend on a given IAM entity.

| Parameter | Type | Description |
|-----------|------|-------------|
| entity_arn | string | ARN of the IAM role, user, or policy to analyze |
| depth | integer | How many levels of dependency to traverse (default: 2) |
| include_service_linked | boolean | Include service-linked roles in analysis (default: false) |

**AWS APIs Used**: `iam:ListEntitiesForPolicy`, `iam:ListAttachedRolePolicies`, `iam:ListRolePolicies`, `iam:GetRole` (trust policy), `iam:ListRoles` (cross-reference AssumeRole)

### validate_policy

Validates a proposed IAM policy for correctness and security best practices.

| Parameter | Type | Description |
|-----------|------|-------------|
| policy_document | string | JSON policy document to validate |
| validation_type | string | Type: syntax, access_level, least_privilege, all (default: all) |
| context_role | string | Optional role ARN to validate against existing permissions |

**AWS APIs Used**: `access-analyzer:ValidatePolicy`, `access-analyzer:CheckAccessNotGranted`, `bedrock:Converse` (for security analysis beyond what API provides)

## CDK Stack Breakdown

### Stack: IamAnalyzerAssistantStack-{region}

Single stack with constructs for logical separation:

| Construct | Resources | Purpose |
|-----------|-----------|---------|
| AuthConstruct | Cognito User Pool, Identity Pool, App Client | User authentication |
| ApiConstruct | API Gateway REST API, Conversation Lambda, Cognito Authorizer | Request routing and orchestration |
| ToolsConstruct | 4 tool Lambda functions, shared IAM role (read-only) | IAM analysis capabilities |
| StorageConstruct | S3 bucket (reports), bucket policy | Generated policy/report storage |
| FrontendConstruct | S3 bucket (static), CloudFront distribution, OAC | Web UI hosting |

### IAM Permissions (Tool Lambda Role)

```json
{
  "Effect": "Allow",
  "Action": [
    "securityhub:GetFindings",
    "securityhub:BatchGetFindings",
    "access-analyzer:ListFindings",
    "access-analyzer:GetFinding",
    "access-analyzer:ValidatePolicy",
    "access-analyzer:CheckAccessNotGranted",
    "cloudtrail:LookupEvents",
    "iam:GetRole",
    "iam:GetPolicy",
    "iam:GetPolicyVersion",
    "iam:ListRoles",
    "iam:ListPolicies",
    "iam:ListAttachedRolePolicies",
    "iam:ListRolePolicies",
    "iam:ListEntitiesForPolicy",
    "iam:GetRolePolicy"
  ],
  "Resource": "*"
}
```

### Conversation Lambda Permissions

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock:InvokeModel",
    "bedrock:InvokeModelWithResponseStream"
  ],
  "Resource": "arn:aws:bedrock:*::foundation-model/anthropic.claude-*"
}
```

## Data Flow — Policy Generation Example

```
User: "Generate a least-privilege policy for role DataPipelineRole"
  │
  ├─► Bedrock Converse → toolUse: generate_policy(role_name="DataPipelineRole")
  │
  ├─► generate_policy Lambda:
  │     1. iam:GetRole → get current trust policy + attached policies
  │     2. iam:ListAttachedRolePolicies → enumerate current permissions
  │     3. cloudtrail:LookupEvents → last 90 days of API calls by role
  │     4. Group API calls by service + action
  │     5. Compare actual usage vs. granted permissions
  │     6. Build minimal policy covering actual usage
  │     7. Return: {current_policy, proposed_policy, removed_permissions[], usage_stats}
  │
  ├─► Bedrock receives toolResult
  │     - Synthesizes explanation of changes
  │     - Highlights removed permissions with risk assessment
  │     - Suggests validation steps
  │
  └─► Response to user:
        "Based on 90 days of CloudTrail activity, DataPipelineRole only uses
         12 of the 47 actions granted. Here's a least-privilege policy that
         removes 35 unused permissions..."
```

## Data Flow — Export

```
User: "Export that to S3"
  │
  ├─► Bedrock Converse → toolUse: export_report(content="...", content_type="policy")
  │
  ├─► export_report Lambda:
  │     1. Receives content as string from Bedrock
  │     2. Determines file format (json/md/txt) and folder (policies/, reports/, etc.)
  │     3. Generates timestamped filename: report-{YYYY-MM-DD}_{HHMMSS}.md
  │     4. Writes to S3: s3://{bucket}/{folder}/{filename}
  │     5. Generates presigned URL (1 hour validity, STS role-based signing)
  │     6. Returns: {filename, s3_path, download_url, valid_for}
  │
  └─► list_exports Lambda (on "show my exports"):
        1. Lists all objects in reports/ prefix
        2. Generates fresh presigned URLs for each
        3. Returns: [{filename, folder, last_modified, download_url}]
```

**Key behaviors:**
- File persists permanently in S3; only the download link expires (1 hour)
- Users can request fresh links anytime: "get a new link for report-2026-07-24.md"
- Local "Save as .md" button in UI provides instant download without S3

## Frontend Architecture

```
frontend/
├── src/
│   ├── App.tsx                    # Root with Cognito auth wrapper
│   ├── components/
│   │   ├── ChatInterface.tsx      # Main chat container
│   │   ├── MessageList.tsx        # Conversation history
│   │   ├── MessageBubble.tsx      # Individual message rendering
│   │   ├── PolicyViewer.tsx       # JSON policy with syntax highlighting
│   │   ├── FindingsTable.tsx      # Cloudscape table for findings
│   │   └── DependencyGraph.tsx    # Visual dependency mapping
│   ├── hooks/
│   │   ├── useConversation.ts     # API interaction + state
│   │   └── useAuth.ts            # Cognito auth flow
│   ├── services/
│   │   └── api.ts                # API client with auth headers
│   └── types/
│       └── index.ts              # TypeScript interfaces
├── package.json
├── vite.config.ts
└── tsconfig.json
```

## Authentication & Authorization

- Cognito User Pool (email/password, self-signup enabled for demo — disable for production)
- Cognito Identity Pool provides temporary AWS credentials for frontend API signing
- API Gateway uses Cognito Authorizer to validate JWT tokens on every request
- Authenticated users only get `lambda:InvokeFunction` via Identity Pool role
- All AWS service access happens server-side through Lambda execution roles — users never directly call Security Hub, IAM, or CloudTrail

## Bedrock Configuration

- **Model**: `us.anthropic.claude-sonnet-4-5-20250929-v1:0` (configurable via `BEDROCK_MODEL_ID` env var)
- **Max output tokens**: 2048 (keeps responses within API Gateway timeout)
- **Conversation history**: Last 8 messages sent to Bedrock (older messages trimmed to prevent payload overflow)
- **System prompt**: Instructs the model to act as an IAM security analyst with tool access, educational capabilities (Discovery/Direct modes), change management guidance, and scope boundaries. Protected from extraction.
- **Tool orchestration**: Bedrock Converse API with `toolConfig` — supports multi-turn `tool_use` loops where the model decides which tools to invoke based on user intent
- **Performance rule**: Max 2 tool calls per response turn to stay within timeout limits

## Security Model

| Boundary | Enforcement |
|----------|------------|
| Lambda execution role | READ-ONLY access to Security Hub, IAM, CloudTrail, Access Analyzer |
| No IAM write permissions | Cannot modify policies, roles, or users — analysis only |
| No cross-account access | Operates only within the deployed account |
| S3 write scope | Limited to the single reports bucket (or disabled entirely) |
| Bedrock data privacy | Inputs/outputs not used for model training; encrypted in transit and at rest |
| System prompt protection | Model refuses to reveal internal instructions or operational rules |
| Input scope | Off-topic questions politely redirected to IAM security domain |
| Frontend auth | Every API call requires valid Cognito JWT; expired tokens rejected |

## Error Handling

| Scenario | Behavior |
|----------|----------|
| API Gateway timeout (29s hard limit) | Frontend shows "Failed to fetch" — user retries |
| Conversation Lambda timeout (120s) | Returns 504; should not occur with history trimming |
| Tool Lambda error | Returns `{"error": "..."}` to Bedrock; model explains gracefully |
| Role not found | Tool returns specific error; model suggests checking the name |
| Security Hub not enabled | Tool returns advisory; model explains prerequisite |
| Bedrock model unavailable | Lambda catches exception; returns 500 with generic message |
| Cold start | ~3-5 seconds on first invocation; subsequent calls warm |
| Large result sets | Tools cap at 10-50 items; model offers to page or export full set |

## Cost Estimate

| Component | Unit Cost | Typical Session (5-10 turns) |
|-----------|-----------|------------------------------|
| Bedrock (input tokens) | $3.00 / million tokens | ~$0.01-0.03 |
| Bedrock (output tokens) | $15.00 / million tokens | ~$0.02-0.10 |
| Lambda invocations | $0.20 / million requests | negligible |
| Lambda compute | $0.0000167 / GB-second | negligible |
| API Gateway | $3.50 / million requests | negligible |
| CloudFront | $0.085 / GB transfer | negligible |
| S3 storage | $0.023 / GB-month | < $0.01 |
| Cognito | Free (< 50,000 MAU) | $0 |
| **Typical session total** | | **~$0.05-0.15** |

Monthly estimate for a team of 5 using it daily: ~$10-30/month. Infrastructure idle cost: ~$6-12/month.

*Costs assume us-east-1 pricing. Bedrock costs scale linearly with conversation volume.*
