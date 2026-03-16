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
    └── get-aws-region.sh               # AWS utilities (Bash)
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
source ../../shared/utils/get-aws-region.sh

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
    $currentRegion = aws configure get region 2>$null
}
```

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
- `-RequiredService` / `--required-service`: AWS service to check (bedrock, agentcore, transform)
- `-MinAwsCliVersion` / `--min-aws-cli-version`: Minimum AWS CLI version required
- `-RequireCDK` / `--require-cdk`: Validate CDK installation
- `-SkipServiceCheck` / `--skip-service-check`: Skip service availability check

**Exports** (PowerShell only):
- `$global:AWS_REGION`: Detected AWS region
- `$global:AWS_ACCOUNT_ID`: AWS account ID
- `$global:AWS_ARN`: Caller identity ARN

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
