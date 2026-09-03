# Shared Resources

This directory contains shared utilities, scripts, and resources used across all GenAI Ops demos to ensure consistency and reduce code duplication.

## Directory Structure

```
shared/
├── README.md                           # This file
├── scripts/                            # Shared deployment and utility scripts
│   ├── check-prerequisites.ps1         # Prerequisites validation (PowerShell)
│   ├── check-prerequisites.sh          # Prerequisites validation (Bash)
│   ├── deploy-cdk.ps1                  # CDK deployment automation (PowerShell)
│   └── deploy-cdk.sh                   # CDK deployment automation (Bash)
└── utils/                              # Shared utility functions
    ├── __init__.py                     # Python package initialization
    ├── aws_utils.py                    # AWS utilities (Python)
    ├── aws-utils.ts                    # AWS utilities (TypeScript)
    └── aws-utils.sh                    # AWS utilities (Bash)
```

## Utilities

### Region Detection

All demos use centralized region detection with consistent priority order:

1. `AWS_DEFAULT_REGION` environment variable (temporary override)
2. `AWS_REGION` environment variable (alternative)
3. AWS CLI configuration (`aws configure get region`)
4. Fallback to `us-east-1` (only if nothing configured)

#### Python Usage

```python
from shared.utils import get_region, get_account_id

# Get AWS region
region = get_region()

# Get AWS account ID
account_id = get_account_id()
```

**Note**: The shared CDK deployment scripts automatically set `PYTHONPATH` to the repository root, so imports work without path manipulation.

#### TypeScript Usage

```typescript
import { getRegion, getAccountId } from '../../../../shared/utils/aws-utils';

// Get AWS region
const region = getRegion();

// Get AWS account ID
const accountId = getAccountId();
```

#### Bash Usage

```bash
# Source the utility functions
source ../../shared/utils/aws-utils.sh

# Get AWS region
CURRENT_REGION=$(get_aws_region)

# Get AWS account ID
ACCOUNT_ID=$(get_aws_account_id)
```

#### PowerShell Usage

PowerShell scripts use the shared prerequisites check which exports region as a global variable:

```powershell
# Run prerequisites check
& "..\..\shared\scripts\check-prerequisites.ps1" -RequireCDK

# Use the exported region
$currentRegion = $global:AWS_REGION
```

For scripts that skip prerequisites (e.g., with `-SkipSetup`), detect region directly:

```powershell
$currentRegion = $env:AWS_DEFAULT_REGION
if ([string]::IsNullOrEmpty($currentRegion)) {
    $currentRegion = $env:AWS_REGION
}
if ([string]::IsNullOrEmpty($currentRegion)) {
    $currentRegion = aws configure get region 2>$null
}
```

## AWS DevOps Agent Region

DevOps Agent demos involve two regions, and they may differ:

| Variable | Controls |
|---|---|
| `AWS_REGION` / `AWS_DEFAULT_REGION` | Where the CDK stacks deploy |
| `DEVOPS_AGENT_REGION` | Where the **Agent Space** is created |

**Default: same region.** `DEVOPS_AGENT_REGION` is opt-in — leave it unset and the Agent
Space is created in the deploy region. That is safe because an Agent Space monitors
resources across *all* regions of an associated account, so it never needs to sit with
your stack.

**Override** when the deploy region cannot host an Agent Space
([supported Regions](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-supported-regions.html)),
or when the Agent Space's own data (investigations, topology, recommendations) must stay
in a specific region:

```bash
export DEVOPS_AGENT_REGION=eu-west-1
```

```powershell
$env:DEVOPS_AGENT_REGION = "eu-west-1"
```

### For demo authors

Ask for the check, then read the resolved value — do not re-derive it:

```powershell
& "..\..\shared\scripts\check-prerequisites.ps1" -RequiredService "devops-agent" -MinAwsCliVersion "2.34.20"
$region      = $global:AWS_REGION             # deploy the stacks here
$agentRegion = $global:DEVOPS_AGENT_REGION    # create the Agent Space here
```

```bash
source ../../shared/scripts/check-prerequisites.sh --required-service devops-agent --min-aws-cli-version 2.34.20
# $AWS_REGION           — deploy the stacks here
# $DEVOPS_AGENT_REGION  — create the Agent Space here
```

The check resolves the Agent Space region, probes the live API
(`aws devops-agent list-agent-spaces`) there, fails with guidance naming
`DEVOPS_AGENT_REGION` if it is unreachable, and publishes the resolved value. Pass
`--region "$DEVOPS_AGENT_REGION"` explicitly on every `aws devops-agent` call so behavior
never depends on ambient CLI config.

Two rules to preserve:

- **The value is published only when you request the `devops-agent` check.** The Bash
  script is *sourced*, so setting this name unconditionally would pre-empt a demo's own
  `${DEVOPS_AGENT_REGION:-<default>}` and silently relocate its Agent Space.
- **Never hardcode a region list**, in code or in prose — lists rot and reject regions the
  moment AWS adds them. Availability is proven by probing. Note the API can respond in
  regions that are not yet documented, so the probe catches unreachable regions, not
  undocumented-but-reachable ones.

## Bedrock Model ID (Cross-Region Inference)

Newer Bedrock models (Claude Sonnet 4, 4.5, 4.6) are often only available via cross-region inference (CRIS) profiles. Use the shared utility to get the correct CRIS-prefixed model ID for the deployment region:

### Python Usage
```python
from shared.utils.aws_utils import get_bedrock_model_id

# Default model (anthropic.claude-sonnet-4-6)
model_id = get_bedrock_model_id()
# Returns: "eu.anthropic.claude-sonnet-4-6" in eu-west-1

# Custom model
model_id = get_bedrock_model_id("anthropic.claude-sonnet-4-5-20250929-v1:0")
# Returns: "eu.anthropic.claude-sonnet-4-5-20250929-v1:0" in eu-west-1
```

### Region-to-prefix mapping

| Region prefix | CRIS prefix | Data residency |
|---|---|---|
| `us-*` | `us.` | US only |
| `eu-*` | `eu.` | EU only |
| `ap-*` | `apac.` | APAC only |
| Others (`ca-`, `me-`, `af-`, `sa-`, `il-`, `mx-`) | `global.` | Worldwide |

### CDK pattern for Lambdas

The recommended pattern is to compute the model ID in the CDK app (which has access to shared utils via PYTHONPATH) and pass it as a Lambda environment variable:

```python
# infrastructure/cdk/app.py or stack.py
from shared.utils.aws_utils import get_bedrock_model_id

lambda_.Function(
    self, "MyFunction",
    environment={"MODEL_ID": get_bedrock_model_id()},
    ...
)
```

The Lambda then reads `os.environ['MODEL_ID']` at runtime — no shared utils dependency needed in the Lambda bundle.

## Scripts

### Prerequisites Check

Validates common requirements before deployment:

**PowerShell**:
```powershell
& "..\..\shared\scripts\check-prerequisites.ps1" `
    -RequiredService "bedrock" `
    -MinAwsCliVersion "2.15.0" `
    -RequireCDK
```

**Bash**:
```bash
../../shared/scripts/check-prerequisites.sh \
    --required-service bedrock \
    --min-aws-cli-version 2.15.0 \
    --require-cdk
```

**Parameters**:
- `-RequiredService` / `--required-service`: AWS service to check — `bedrock`, `agentcore`, `agentcore-browser`, `devops-agent`, `nova-act`, `transform`
- `-MinAwsCliVersion` / `--min-aws-cli-version`: Minimum AWS CLI version required
- `-RequireCDK` / `--require-cdk`: Validate CDK installation
- `-RequireKubectl` / `--require-kubectl`: Validate kubectl installation
- `-SkipServiceCheck` / `--skip-service-check`: Skip service availability check

**Exports**:
- `$global:AWS_REGION` / `AWS_REGION`: Detected AWS region (deploy region)
- `$global:AWS_ACCOUNT_ID` / `AWS_ACCOUNT_ID`: AWS account ID
- `$global:AWS_ARN` / `AWS_ARN`: Caller identity ARN
- `$global:DEVOPS_AGENT_REGION` / `DEVOPS_AGENT_REGION`: Agent Space region — equals the
  deploy region unless overridden. See [AWS DevOps Agent Region](#aws-devops-agent-region).

> `devops-agent` probes the **Agent Space region**, not the deploy region — the two can
> differ. Every other service is probed in the deploy region.

### CDK Deployment

Automates CDK bootstrap, dependency installation, and deployment:

**PowerShell**:
```powershell
& "..\..\shared\scripts\deploy-cdk.ps1" `
    -CdkDirectory "infrastructure/cdk" `
    -StackName "MyStack" `
    -SkipBootstrap
```

**Bash**:
```bash
../../shared/scripts/deploy-cdk.sh \
    --cdk-directory infrastructure/cdk \
    --stack-name MyStack \
    --skip-bootstrap
```

**Parameters**:
- `-CdkDirectory` / `--cdk-directory`: Path to CDK directory (required)
- `-StackName` / `--stack-name`: Specific stack to deploy (optional)
- `-DestroyStack` / `--destroy`: Destroy stack instead of deploying
- `-SkipBootstrap` / `--skip-bootstrap`: Skip CDK bootstrap check

**Features**:
- Automatically detects Python or TypeScript CDK projects
- Installs dependencies (pip or npm)
- Ensures CDK bootstrap is up to date
- Sets `PYTHONPATH` for Python projects to enable clean imports
- Handles deployment with proper error checking

## Best Practices

### 1. Always Use Shared Utilities

**❌ Don't duplicate region detection logic**:
```python
# Bad - duplicated logic
region = os.environ.get('AWS_DEFAULT_REGION')
if not region:
    region = subprocess.check_output(['aws', 'configure', 'get', 'region']).strip()
```

**✅ Use shared utilities**:
```python
# Good - centralized logic
from shared.utils import get_region
region = get_region()
```

### 2. Use Shared Deployment Scripts

**❌ Don't write custom CDK deployment logic**:
```powershell
# Bad - custom deployment
cd infrastructure/cdk
npm install
npx cdk deploy --no-cli-pager
```

**✅ Use shared deployment script**:
```powershell
# Good - shared script with consistent behavior
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "infrastructure/cdk"
```

### 3. Run Prerequisites Check First

Always validate prerequisites before deployment:

```powershell
# Check prerequisites first
& "..\..\shared\scripts\check-prerequisites.ps1" -RequiredService "bedrock"

# Then use the exported region
$region = $global:AWS_REGION

# Then deploy
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "infrastructure/cdk"
```

### 4. Handle -SkipSetup Scenarios

When users skip prerequisites (e.g., `-SkipSetup` flag), you still need region detection:

```powershell
if (-not $SkipSetup) {
    # Run prerequisites check
    & "..\..\shared\scripts\check-prerequisites.ps1"
    $region = $global:AWS_REGION
} else {
    # Detect region directly when skipping prerequisites
    $region = $env:AWS_DEFAULT_REGION
    if ([string]::IsNullOrEmpty($region)) {
        $region = aws configure get region 2>$null
    }
}
```

### 5. Keep Imports Clean (Python)

The shared CDK deployment scripts set `PYTHONPATH` automatically:

```python
# ✅ Clean import - works because PYTHONPATH is set by deploy-cdk scripts
from shared.utils import get_region

# ❌ Ugly import - don't do this
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared'))
from utils import get_region
```

### 6. Consistent Error Messages

Use consistent error messages across demos:

```powershell
if ([string]::IsNullOrEmpty($region)) {
    Write-Host "ERROR: AWS region not configured" -ForegroundColor Red
    Write-Host "Please configure your AWS region: aws configure set region <your-region>" -ForegroundColor Yellow
    exit 1
}
```

## Adding New Shared Resources

When adding new shared utilities or scripts:

1. **Place in appropriate directory**:
   - Scripts → `shared/scripts/`
   - Utilities → `shared/utils/`

2. **Provide both PowerShell and Bash versions** for scripts

3. **Update this README** with usage examples

4. **Export functions properly**:
   - Python: Add to `shared/utils/__init__.py`
   - TypeScript: Export from module
   - Bash: Define as functions in sourced file

5. **Follow naming conventions**:
   - Python: `snake_case` functions
   - TypeScript: `camelCase` functions
   - Bash: `snake_case` functions
   - PowerShell: `PascalCase` or `Verb-Noun` cmdlet style

## Testing Shared Resources

Before committing changes to shared resources:

1. **Test across multiple demos** to ensure compatibility
2. **Test both PowerShell and Bash versions** on Windows
3. **Verify Python imports work** in CDK apps
4. **Check TypeScript compilation** succeeds
5. **Test with and without prerequisites check** (SkipSetup scenarios)

## Maintenance

Shared resources are maintained by the GenAI Ops Demo Library team. When updating:

- **Maintain backward compatibility** - demos depend on these
- **Update all language versions** together (Python, TypeScript, Bash, PowerShell)
- **Document breaking changes** in commit messages
- **Test thoroughly** before merging

## Questions?

For questions about shared resources, see the main repository [CONTRIBUTING.md](../CONTRIBUTING.md) or open an issue.
