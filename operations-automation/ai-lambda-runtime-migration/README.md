# AI Lambda Runtime Migration Assistant
*Discover, assess, and transform AWS Lambda functions running deprecated runtimes using Amazon Bedrock AgentCore and Nova 2 Lite*

## Overview

AWS Lambda periodically deprecates older runtimes, leaving teams with dozens or hundreds of functions to evaluate and migrate. This demo showcases an automated triage pipeline that discovers deprecated-runtime functions via Trusted Advisor, enriches each with Lambda API and CloudWatch data, runs AI-powered code assessment, and generates migrated code validated through a secure sandbox. A React dashboard built with Cloudscape Design System provides real-time visibility into the migration pipeline.

The demo does not modify your Lambda functions — it produces migrated source code, changelogs, and validation reports in S3 for your team to review and apply.

## At a Glance

- **Duration**: ~5 minutes deployment (direct code deploy — no container build)
- **Difficulty**: Intermediate
- **Target Audience**: DevOps Engineers, Platform Engineers, SREs
- **Key Technologies**: Amazon Bedrock AgentCore, Amazon Nova 2 Lite, AgentCore Code Interpreter, Amazon Cognito, Amazon CloudFront, Cloudscape Design System
- **Estimated Cost**: ~$27 for 1,000 functions (see [Cost Estimate](#cost-estimate))

## Architecture

An editable draw.io diagram is available at [img/architecture.drawio](img/architecture.drawio).

![Architecture Diagram](img/architecture.drawio.svg)

## Prerequisites

- AWS CLI v2.31.13+ ([Installation Guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html))
- Node.js 22+
- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager (for agent dependency packaging)
- AWS Business or Enterprise Support plan (required for Trusted Advisor API access)
- AWS credentials configured with permissions for CloudFormation, Lambda, S3, DynamoDB, Cognito, Bedrock, and IAM
- Amazon Bedrock AgentCore available in your target region ([Check availability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html))

## Quick Start

### Deploy

**Windows (PowerShell):**
```powershell
.\deploy-all.ps1
```

**macOS/Linux:**
```bash
chmod +x deploy-all.sh scripts/package-agent.sh scripts/build-frontend.sh
./deploy-all.sh
```

The script deploys 4 CDK stacks, packages 3 agent runtimes, builds the React frontend, and outputs the CloudFront dashboard URL.

### Create a Dashboard User

The deployment output includes the commands to create a Cognito user. Replace the email and password:

```bash
aws cognito-idp admin-create-user \
  --user-pool-id <USER_POOL_ID> \
  --username your.email@example.com \
  --user-attributes Name=email,Value=your.email@example.com \
  --message-action SUPPRESS --no-cli-pager

aws cognito-idp admin-set-user-password \
  --user-pool-id <USER_POOL_ID> \
  --username your.email@example.com \
  --password YourPass123! \
  --permanent --no-cli-pager
```

### Use the Dashboard

1. Open the CloudFront URL from the deployment output
2. Sign in with the credentials you just created
3. Click **Scan** on the Dashboard to trigger Phase 1 discovery
4. Select a function in the Functions table to view enrichment data
5. Click **Assess** to run Phase 2 AI-powered code assessment
6. Click **Transform** to generate migrated code with validation

## Three-Phase Pipeline

Each phase runs as a dedicated AgentCore Runtime, deployed as a Python zip to S3 (no containers, no Docker).

### Phase 1 — Discover ([agent/discover/main.py](agent/discover/main.py))

Queries Trusted Advisor check `L4dfs2Q4C5` for deprecated-runtime Lambda functions, enriches each with Lambda API configuration and CloudWatch metrics, then uses Nova 2 Lite with constrained decoding (tool use) to assign AI-powered priority scores.

- **Diff-based scan**: NEW functions get full enrichment (Lambda API + CloudWatch), EXISTING get TA field refresh only (no API calls), RESOLVED get marked green
- **RESOLVED (Green)**: When a function disappears from Trusted Advisor on two consecutive scans, it's marked as RESOLVED with a Green alert — meaning the team has already migrated or deleted it. The original `ta_last_updated` timestamp is preserved so you can see when it was last seen by TA.
- **Priority scores**: 0–100 with reasoning — CRITICAL (80+), HIGH (60–79), MEDIUM (40–59), LOW (20–39), INACTIVE (0–19)
- **Container image functions**: Automatically SKIPPED (cannot migrate via code)
- **TA retry logic**: Retries once on empty results and before resolving functions, to handle TA API caching

### Phase 2 — Assess ([agent/analyze/main.py](agent/analyze/main.py))

Downloads the Lambda deployment package via presigned URL, uploads source files to S3, then sends the actual code to Nova 2 Lite for AI-powered assessment with constrained decoding.

- **Source filtering**: Excludes `node_modules`, `__pycache__`, `vendor`, `dist`, and other dependency directories
- **AI assessment**: Classifies complexity (LOW/MEDIUM/HIGH), identifies deprecated APIs, breaking changes, and dependency issues
- **Target runtime**: Recommends the latest supported Lambda runtime for the function's language
- **S3 artifacts**: Stores source code at `functions/{name}/original/` and assessment at `functions/{name}/analysis.json`

### Phase 3 — Transform ([agent/transform/main.py](agent/transform/main.py))

Generates migrated code file-by-file using Nova 2 Lite (one LLM call per source file — no output parsing needed), validates Python files through AgentCore Code Interpreter, and retries up to 3 times on validation failure.

- **File-by-file generation**: Each source file is migrated individually with explicit instructions to output only the migrated code
- **Code Interpreter validation**: Runs `ast.parse()` + import completeness check in a secure sandbox
- **Non-Python files**: JS/TS files skip validation (noted as "skipped" in results)
- **S3 artifacts**: Stores migrated code at `functions/{name}/migrated/`, plus `changelog.md` and `validation.json`
- **Status**: READY_TO_MIGRATE on success, TRANSFORM_FAILED on failure

## CDK Stacks

All stack IDs include the region suffix for multi-region deployment support.

| Stack | Purpose | Key Resources | Dependencies |
|-------|---------|---------------|--------------|
| `LambdaRuntimeMigrationData-{region}` | State storage | DynamoDB table (`lambda-runtime-migration`), S3 bucket | None |
| `LambdaRuntimeMigrationAuth-{region}` | Authentication | Cognito User Pool, Identity Pool | None |
| `LambdaRuntimeMigrationRuntime-{region}` | Agent hosting | 3 AgentCore Runtimes (S3 zip deploy), IAM role | Data |
| `LambdaRuntimeMigrationFrontend-{region}` | Dashboard | CloudFront distribution, S3 website bucket | Data, Auth, Runtime |

## Frontend

The React dashboard calls AgentCore runtimes directly via SigV4 — there is no API Gateway in the middle.

**Authentication flow**: Cognito User Pool → ID Token → Cognito Identity Pool → Temporary AWS Credentials → AgentCore (IAM).

| Page | Description |
|------|-------------|
| **Dashboard** | Draggable board widgets with runtime distribution pie chart, deprecation timeline scatter chart, migration status and complexity breakdowns |
| **Functions** | Sortable/filterable table with split panel showing data from all 4 sources: Trusted Advisor, Lambda API, CloudWatch, and Bedrock |
| **Function Detail** | Tabbed view with Overview (Phase 1 enrichment), Assessment (Phase 2 AI analysis), and Transformation (Phase 3 migrated code, changelog, validation) |
| **Migration Plan** | Prioritized list of functions ordered by AI priority score |

Each data point in the UI is labeled with its source (Trusted Advisor, Lambda API, CloudWatch, or Amazon Bedrock) and AI-generated content is marked with the Cloudscape GenAI label pattern.

## Project Structure

```
ai-lambda-runtime-migration/
├── agent/                              # 3 dedicated AgentCore runtimes
│   ├── _shared/                        # Shared constants (copied into each package)
│   │   ├── constants.py                # MigrationStatus, PriorityLabel, AlertStatus
│   │   └── __init__.py
│   ├── discover/                       # Phase 1: Discovery + Enrichment + Prioritization
│   │   ├── main.py
│   │   └── requirements.txt
│   ├── analyze/                        # Phase 2: Code Download + AI Assessment
│   │   ├── main.py
│   │   └── requirements.txt
│   └── transform/                      # Phase 3: Code Generation + Validation
│       ├── main.py
│       └── requirements.txt
├── cdk/                                # CDK TypeScript infrastructure (4 stacks)
│   ├── bin/app.ts                      # Entry point with solution adoption tracking
│   ├── lib/
│   │   ├── data-stack.ts               # DynamoDB + S3
│   │   ├── auth-stack.ts               # Cognito User Pool + Identity Pool
│   │   ├── runtime-stack.ts            # 3 AgentCore Runtimes
│   │   └── frontend-stack.ts           # CloudFront + S3 website
│   ├── lambda/api/                     # Python Lambda handlers for dashboard API
│   ├── cdk.json
│   ├── package.json
│   └── tsconfig.json
├── frontend/                           # React + Cloudscape dashboard
│   ├── src/
│   │   ├── main.tsx                    # Entry point
│   │   ├── App.tsx                     # Layout, navigation, routing
│   │   ├── AuthModal.tsx               # Cognito sign-in modal
│   │   ├── agentcore.ts                # Direct AgentCore invocation (SigV4)
│   │   ├── auth.ts                     # Cognito authentication
│   │   ├── constants.ts                # Status, priority, alert constants
│   │   ├── SplitPanelContext.tsx        # Split panel state management
│   │   └── pages/
│   │       ├── Dashboard.tsx           # Board widgets, charts
│   │       ├── Functions.tsx           # Table with split panel
│   │       ├── FunctionDetail.tsx      # Assessment + Transformation tabs
│   │       └── MigrationPlan.tsx       # Prioritized migration plan
│   ├── index.html
│   ├── package.json
│   └── vite.config.ts
├── img/                                # Architecture diagrams
│   ├── architecture.drawio             # Editable draw.io source
│   └── architecture.drawio.svg         # Rendered SVG for README
├── scripts/
│   ├── package-agent.ps1 / .sh         # Package agent zips (ARM64 deps via uv)
│   ├── build-frontend.ps1 / .sh        # Build frontend with Vite env injection
│   └── deploy-agents.ps1              # Quick agent redeploy without CDK
├── deploy-all.ps1                      # Deployment script (PowerShell)
├── deploy-all.sh                       # Deployment script (Bash)
└── README.md
```

## Cost Estimate

Pricing based on Amazon Nova 2 Lite on-demand rates ($0.30/1M input tokens, $2.50/1M output tokens) and AgentCore Runtime consumption-based pricing. Actual costs vary based on function code size and complexity.

### Scenario: 1,000 Lambda Functions — Full Pipeline (One-Time)

Assumes average function has 3 source files, ~500 lines of code.

| Phase | What happens | Nova 2 Lite | AgentCore Runtime | Total |
|-------|-------------|------------|-------------------|-------|
| Phase 1 — Discover | TA query + Lambda/CW enrichment + AI prioritization | ~$1 | ~$1 | ~$2 |
| Phase 2 — Assess | Code download + S3 upload + AI assessment (×1,000) | ~$3 | ~$1 | ~$4 |
| Phase 3 — Transform | File-by-file code gen + Code Interpreter validation (×1,000) | ~$19 | ~$1 | ~$20 |
| **Total** | | **~$23** | **~$3** | **~$27** |

Infrastructure costs (CloudFront, Cognito, DynamoDB, S3) are negligible — all services are either free tier eligible or on-demand with zero cost when idle.

The main cost driver is Phase 3 output tokens — generating migrated code for ~3,000 source files.

## Cleanup

Destroy stacks in reverse dependency order:

**Bash:**
```bash
cd cdk
npx cdk destroy "LambdaRuntimeMigrationFrontend-$(aws configure get region)" --no-cli-pager
npx cdk destroy "LambdaRuntimeMigrationRuntime-$(aws configure get region)" --no-cli-pager
npx cdk destroy "LambdaRuntimeMigrationAuth-$(aws configure get region)" --no-cli-pager
npx cdk destroy "LambdaRuntimeMigrationData-$(aws configure get region)" --no-cli-pager
```

**PowerShell:**
```powershell
cd cdk
$region = aws configure get region
npx cdk destroy "LambdaRuntimeMigrationFrontend-$region" --no-cli-pager
npx cdk destroy "LambdaRuntimeMigrationRuntime-$region" --no-cli-pager
npx cdk destroy "LambdaRuntimeMigrationAuth-$region" --no-cli-pager
npx cdk destroy "LambdaRuntimeMigrationData-$region" --no-cli-pager
```

## Design Decisions

### Why Bedrock + AgentCore Instead of AWS Transform?

This demo uses Amazon Bedrock (Nova 2 Lite) and AgentCore to handle code assessment and transformation directly. Lambda functions are typically small, focused pieces of code where this lightweight approach works well — a single Converse API call per file is enough to generate the migrated code.

For functions with high complexity — large codebases, extensive dependency chains, or critical business logic — consider [AWS Transform](https://aws.amazon.com/transform/) which provides enterprise-grade code transformation with built-in validation.

AWS Transform offers [managed transformations](https://docs.aws.amazon.com/transform/latest/userguide/transform-aws-customs.html) that are directly relevant to Lambda runtime migration:

- `AWS/python-version-upgrade` — Migrate Python projects from 3.8/3.9 to 3.11/3.12/3.13
- `AWS/nodejs-version-upgrade` — Upgrade Node.js applications to any target version
- `AWS/python-boto2-to-boto3` — Migrate from boto2 to boto3
- `AWS/nodejs-aws-sdk-v2-to-v3` — Upgrade AWS SDK for JavaScript v2 to v3

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
