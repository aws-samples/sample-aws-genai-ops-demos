---
inclusion: always
---

# Contributor Guide

## Mission

Build deployable code samples demonstrating practical GenAI applications for cloud operations. Every demo must work in any AWS account and region without modification.

## Operational Pillars

All demos align with one of five pillars:

- **operations-automation/** — Lifecycle tracking, model migrations, legacy system automation
- **security/** — Shift-left security, compliance automation, vulnerability detection
- **cost-optimization/** — GenAI spend control, cost visibility, budget management
- **observability/** — Incident analysis, anomaly detection, postmortems
- **resilience/** — Failure detection, recovery automation, capacity management

---

## Technology Stack

### GenAI Services

| Service | When to Use |
|---|---|
| **Amazon Bedrock** | All demos requiring GenAI capabilities |
| **Amazon Nova Models** | Default model choice (Lite/Pro/Premier) |
| **Amazon Bedrock AgentCore** | Multi-step workflows, tool integration, agent orchestration |
| **AWS Transform** | Documentation generation, code analysis, migration assessments |
| **MCP Servers** | Tool integration, Kiro Powers |

### Bedrock Model IDs

**NEVER hardcode region-prefixed model IDs** (e.g., `us.anthropic.claude-sonnet-4-6`). Use the shared utility to get the correct cross-region inference (CRIS) prefix for the deployment region:

```python
from shared.utils.aws_utils import get_bedrock_model_id

MODEL_ID = get_bedrock_model_id()  # Auto-prefixes based on region
```

For Lambdas, compute the model ID in the CDK stack and pass it as an environment variable:

```python
environment={"MODEL_ID": get_bedrock_model_id()}
```

See `shared/README.md` for full documentation on the region-to-prefix mapping.

### Infrastructure as Code

**AWS CDK is required** — TypeScript (preferred) or Python. No Terraform or CloudFormation-only.

Why CDK:
- Stack outputs enable deployment scripts to retrieve dynamic URLs
- Higher-level abstractions reduce boilerplate
- Consistency across all existing demos

### Scripting & Deployment

- **PowerShell + Bash**: Must provide both for every demo
- **Python 3.9+**: Backend services, agents, data processing
- **TypeScript/Node.js 18+**: CDK infrastructure, frontend apps

### Frontend Technologies

- **React**: Complex interactive UIs
- **Vanilla JavaScript**: Simple portals and demos
- **Cloudscape Design System**: AWS-native UI components
- **Classic Stylesheets**: Retro-themed demos (see `classic-stylesheets-implementation-guide.md`)

---

## Project Structure

### Repository Layout

```
sample-genai-ops-demos/
├── [pillar-name]/
│   └── [demo-name]/
│       ├── README.md
│       ├── ARCHITECTURE.md
│       ├── deploy-all.ps1
│       ├── deploy-all.sh
│       ├── infrastructure/
│       │   └── cdk/
│       │       ├── app.py | app.ts      # Tracking goes here
│       │       ├── lib/
│       │       ├── requirements.txt | package.json
│       │       └── cdk.json
│       ├── frontend/                    # If applicable
│       ├── src/
│       └── power/                       # If Kiro Power
├── shared/
│   ├── scripts/
│   │   ├── check-prerequisites.ps1
│   │   ├── check-prerequisites.sh
│   │   ├── deploy-cdk.ps1
│   │   └── deploy-cdk.sh
│   └── utils/
│       ├── aws_utils.py
│       ├── aws-utils.ts
│       └── aws-utils.sh
└── .kiro/
    ├── steering/
    └── hooks/
```

### Naming Conventions

| Type | Convention | Examples |
|---|---|---|
| Demo directories | kebab-case | `ai-password-reset-chatbot`, `ai-chaos-engineering-with-fis` |
| Python files | snake_case | `data_extractor.py`, `aws_utils.py` |
| TypeScript files | kebab-case | `api-stack.ts`, `aws-utils.ts` |
| CDK Stack IDs | PascalCase + region | `PasswordResetInfra-${region}` |
| CDK Construct IDs | PascalCase | `PasswordResetInfra`, `LifecycleTrackerRuntime` |

### Required Files for Every Demo

1. `README.md` — deployment instructions, prerequisites, cost estimate
2. `ARCHITECTURE.md` — architecture diagram and design (at demo root or in `docs/`)
3. `deploy-all.ps1` — PowerShell deployment (or custom name for operational tools)
4. `deploy-all.sh` — Bash deployment (or matching custom name)
5. Solution adoption tracking in CDK app file
6. `.gitignore` including `cdk.out*`

### Deploy Script Exemption (`.no-deploy`)

Demos that are local tools with no AWS infrastructure to deploy may opt out of deployment scripts by placing a `.no-deploy` file at the demo root. The file must contain a one-line explanation of why scripts aren't needed.

**Valid use cases:**
- MCP servers installed via `uvx` or `pip`
- Local CLI tools that don't deploy AWS resources
- Kiro Powers with no cloud infrastructure

**Not valid for:**
- Demos that deploy CDK/CloudFormation resources (these always need scripts)
- Demos with AWS infrastructure of any kind

The CI workflow will flag PRs using `.no-deploy` with a `deploy-exempt` label for maintainer review. All other required files (README, ARCHITECTURE, tracking) still apply.

---

## Implementation Patterns

### Region Detection

**NEVER hardcode regions.** Use shared utilities everywhere.

Priority order (matches AWS CLI):
1. `AWS_DEFAULT_REGION` or `AWS_REGION` environment variable
2. `aws configure get region`
3. Fallback to `us-east-1` only if nothing configured

**Python** (`shared/utils/aws_utils.py`):
```python
from shared.utils import get_region, get_account_id
region = get_region()
```

**TypeScript** (`shared/utils/aws-utils.ts`):
```typescript
import { getRegion, getAccountId } from '../../../../shared/utils/aws-utils';
const region = getRegion();
```

**PowerShell** (via shared prerequisites):
```powershell
& "..\..\shared\scripts\check-prerequisites.ps1"
# Region available in $global:AWS_REGION
```

**Bash** (via shared prerequisites):
```bash
source ../../shared/scripts/check-prerequisites.sh
# Region available in $AWS_REGION
```

### CDK Stack Naming

**MUST include region suffix** in all stack IDs to prevent global resource conflicts:

```python
# Python
MyStack(app, f"MyStack-{region}", env={"region": region})
```

```typescript
// TypeScript
new MyStack(app, `MyStack-${region}`, { env: { region } });
```

### Solution Adoption Tracking

**Tracking ID**: `uksb-do9bhieqqh`

Add to the CDK **app file** (`app.py` or `app.ts`), on the **main stack only**:

```python
MyStack(
    app,
    f"MyStack-{region}",
    description="Brief description (uksb-do9bhieqqh)(tag:demo-name,pillar-name)",
)
```

Rules:
- Only one stack per demo gets tracking (prevents duplicate metrics)
- Never in stack class constructors
- Tags format: `(tag:kebab-case-demo-name,pillar-name)`

### Import Patterns

**Python**:
```python
# Standard library
import json
import os

# Third-party
import boto3
from aws_cdk import Stack, aws_lambda as lambda_

# Shared utilities (clean package-style)
from shared.utils import get_region, get_account_id

# Local
from .constructs import MyConstruct
```

- ✅ `from shared.utils import get_region`
- ❌ `sys.path.insert()` or path manipulation
- CDK deployment scripts set `PYTHONPATH` automatically

#### Agent containers: the shared-utils exception

Code that runs **inside an AgentCore/Docker container** (e.g. `agent.py` and its
helpers) cannot import from `shared/utils/` — the shared module lives outside the
image build context, so `from shared.utils import ...` fails at runtime in the
container.

For container code only, the sanctioned pattern is:

1. Ship a **container-local copy** named `aws_utils.py` at the demo/agent root so
   it is inside the build context.
2. Keep it in sync with `shared/utils/aws_utils.py` (same function names and
   behavior). When you fix a bug in one, fix it in the other.
3. `sys.path` manipulation to import that local copy **is allowed here** — this is
   the one exception to the "no `sys.path.insert()`" rule above, which otherwise
   still applies to CDK and deployment code.

This keeps "use shared utilities everywhere" intact for CDK/deploy code while
giving container code a documented, consistent path instead of per-demo
precedent. Existing examples: `aws-services-lifecycle-tracker/agent/aws_utils.py`,
`ai-password-reset-chatbot/agent/aws_utils.py`,
`ai-load-test-generation-with-dlt/aws_utils.py`.

> Note: hand-copied files can drift (an APAC CRIS-prefix bug once diverged
> between copies). Until a build-time copy mechanism exists, treat syncing these
> copies as part of any change to the shared util.

**TypeScript**:
```typescript
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { getRegion, getAccountId } from '../../../../shared/utils/aws-utils';
```

### Deployment Scripts

**Naming:**
- `deploy-all.ps1` / `deploy-all.sh` — infrastructure deployment demos (user deploys, then interacts)
- Custom descriptive names (`generate-docs.ps1`, `assess-graviton.ps1`) — deploy-and-run tools that execute an operation

**Shared prerequisites** (always call first):
```powershell
& "..\..\shared\scripts\check-prerequisites.ps1" -RequiredService "agentcore" -MinAwsCliVersion "2.31.13"
```

```bash
source ../../shared/scripts/check-prerequisites.sh agentcore 2.31.13
```

**Deployment output** — every script MUST end with a user-friendly summary:
```powershell
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Open the demo: $websiteUrl" -ForegroundColor Cyan
Write-Host "  Region:        $region" -ForegroundColor Cyan
```

**SkipSetup flag** — ONLY for deploy-and-run operational tools (not `deploy-all` scripts):
- PowerShell: `[switch]$SkipSetup` parameter
- Bash: `-s|--skip-setup` flag
- Purpose: skip deployment on subsequent runs, only execute the operation

### Frontend Configuration

**Never hardcode** API endpoints or environment-specific values.

**Option 1: Vite** (React/complex apps) — generate `.env.production.local` at deployment
**Option 2: Runtime config** (vanilla JS) — generate `config.js` at deployment:
```powershell
$configContent = @"
window.APP_CONFIG = { apiBaseUrl: '$apiEndpoint' };
"@
$configContent | Out-File -FilePath "frontend/config.js" -Encoding UTF8
```

---

## Technical Constraints

### Cross-Platform Compatibility
- **Must work on Windows** (PowerShell primary shell)
- Provide both `.ps1` and `.sh` deployment scripts
- Use `python` (not `python3`) in PowerShell scripts
- Test on Windows before considering complete

### Security Requirements
- No hardcoded credentials or account-specific values
- No hardcoded regions
- No hardcoded API endpoints
- Use Secrets Manager or environment variables for sensitive data

### Cross-Account Compatibility
- Must work in any AWS account without modification
- Must work in any AWS region (where services are available)
- Use CDK `this.region` and `this.account` for dynamic values

### Code Quality
- Demo-quality: safe and deployable, not production-grade
- Basic error handling and logging
- Clear documentation and comments
- AWS security best practices
- Include troubleshooting guidance
- Cost documentation with estimates

---

## README Standardization

All demo READMEs must end with these sections (exact wording):

```markdown
## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
```

Never create separate CONTRIBUTING.md or LICENSE files in demo directories.

---

## Anti-Patterns

❌ **Don't:**
- Hardcode region names anywhere
- Use stack IDs without region suffix
- Hardcode API endpoints in frontend code
- Put solution tracking in stack classes (use app files)
- Mix IaC tools (CDK only)
- Commit `cdk.out*` directories
- Duplicate region detection logic
- Add `-SkipSetup` to `deploy-all` scripts
- End deployment scripts silently without showing outputs
- Use `python3` in PowerShell scripts (Windows uses `python`)
- Create separate CONTRIBUTING.md or LICENSE in demo directories

✅ **Do:**
- Use shared utilities for region/account detection
- Include region suffix in CDK stack IDs
- Generate frontend configuration at deployment
- Place tracking in CDK app files only
- Gitignore all CDK output directories
- Use shared prerequisites scripts
- End scripts with user-friendly deployment summary
- Test on Windows before submitting
