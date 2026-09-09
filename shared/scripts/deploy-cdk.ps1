# GenAI Ops Demo Library - Shared CDK Deployment Script
# This script handles CDK bootstrap, dependency installation, and deployment

param(
    [Parameter(Mandatory=$true)]
    [string]$CdkDirectory,
    [string]$StackName = "",
    [switch]$DestroyStack = $false,
    [switch]$SkipBootstrap = $false
)

# Set PYTHONPATH to include shared utilities
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH = $repoRoot

# Get AWS account and region.
# Resolve region with the same priority the rest of the repo uses (AWS_REGION /
# AWS_DEFAULT_REGION env vars win over the CLI's configured region). A caller that sets
# $env:AWS_REGION to target a specific region would otherwise be silently overridden by
# whatever `aws configure get region` returns, deploying the stack to the wrong region
# and breaking the caller's subsequent region-suffixed stack lookup. Mirrors deploy-cdk.sh.
$accountId = aws sts get-caller-identity --query Account --output text --no-cli-pager
$currentRegion = if (-not [string]::IsNullOrEmpty($env:AWS_REGION)) {
    $env:AWS_REGION
} elseif (-not [string]::IsNullOrEmpty($env:AWS_DEFAULT_REGION)) {
    $env:AWS_DEFAULT_REGION
} else {
    aws configure get region
}

if ([string]::IsNullOrEmpty($currentRegion)) {
    Write-Host "ERROR: No AWS region configured" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== CDK Deployment (Shared Script) ===" -ForegroundColor Cyan
Write-Host "      Directory: $CdkDirectory" -ForegroundColor Gray
Write-Host "      Region: $currentRegion" -ForegroundColor Gray
Write-Host "      Account: $accountId" -ForegroundColor Gray

# Verify CDK directory exists
if (-not (Test-Path $CdkDirectory)) {
    Write-Host "ERROR: CDK directory not found: $CdkDirectory" -ForegroundColor Red
    exit 1
}

Push-Location $CdkDirectory

try {
    # Install dependencies
    Write-Host ""
    Write-Host "Installing CDK dependencies..." -ForegroundColor Yellow
    if (Test-Path "requirements.txt") {
        # Python CDK project. Install deps and FAIL LOUDLY if the install does not succeed.
        # Previously stderr was silenced (2>$null) and $LASTEXITCODE was never checked, so a
        # failed install still printed "OK" and the script proceeded to `cdk bootstrap`/`deploy`
        # -> `python3 app.py`, which then died with `ModuleNotFoundError: No module named
        # 'aws_cdk'`. That made a broken install look like a success on a clean host.
        #
        # On failure we do NOT silently force it through. A common failure is PEP 668
        # ("externally-managed-environment"): pip refuses to install into an OS-managed Python.
        # The safe default is to STOP and guide the user to a virtual environment. The
        # --break-system-packages bypass (which mutates the system Python) is available ONLY
        # when the user explicitly opts in via DEPLOY_CDK_ALLOW_BREAK_SYSTEM_PACKAGES=1 --
        # intended for throwaway/ephemeral hosts such as CI runners. Mirrors deploy-cdk.sh.
        $prevErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        pip install -r requirements.txt -q
        $pipExitCode = $LASTEXITCODE
        if ($pipExitCode -ne 0) {
            if ($env:DEPLOY_CDK_ALLOW_BREAK_SYSTEM_PACKAGES -eq "1") {
                Write-Host "      Normal pip install failed; DEPLOY_CDK_ALLOW_BREAK_SYSTEM_PACKAGES=1 set," -ForegroundColor Yellow
                Write-Host "      retrying with --break-system-packages (mutates the system Python)..." -ForegroundColor Yellow
                pip install -r requirements.txt -q --break-system-packages
                $pipExitCode = $LASTEXITCODE
                if ($pipExitCode -ne 0) {
                    $ErrorActionPreference = $prevErrorAction
                    Write-Host "      ERROR: Failed to install Python CDK dependencies (requirements.txt)" -ForegroundColor Red
                    exit 1
                }
            } else {
                $ErrorActionPreference = $prevErrorAction
                Write-Host "      ERROR: Failed to install Python CDK dependencies (requirements.txt)." -ForegroundColor Red
                Write-Host "      If this is an 'externally-managed-environment' (PEP 668) error, do NOT force it" -ForegroundColor Yellow
                Write-Host "      into your system Python. Create and activate a virtual environment first:" -ForegroundColor Yellow
                Write-Host "          python3 -m venv .venv; .\.venv\Scripts\Activate.ps1" -ForegroundColor Gray
                Write-Host "      then re-run this command. On a throwaway/ephemeral host (e.g. a CI runner)" -ForegroundColor Gray
                Write-Host "      where mutating the system Python is acceptable, you may instead re-run with:" -ForegroundColor Gray
                Write-Host "          `$env:DEPLOY_CDK_ALLOW_BREAK_SYSTEM_PACKAGES = '1'" -ForegroundColor Gray
                exit 1
            }
        }
        $ErrorActionPreference = $prevErrorAction
        # Verify the CDK library is actually importable by the interpreter that will synth the
        # app (cdk.json runs `python3 app.py`). A green pip does not guarantee this if pip and
        # python3 resolve to different environments, so check the real precondition, not a proxy.
        python3 -c "import aws_cdk" 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "      ERROR: 'aws_cdk' is not importable by python3 after installing requirements.txt." -ForegroundColor Red
            Write-Host "             pip and python3 may resolve to different environments." -ForegroundColor Red
            exit 1
        }
        Write-Host "      OK: Python CDK dependencies installed" -ForegroundColor Green
    } elseif (Test-Path "package.json") {
        # Install when node_modules is missing OR incomplete. A previous interrupted
        # install can leave a partial node_modules that lacks declared dependencies,
        # which then causes confusing CDK synth/compile errors. Verify completeness
        # with `npm ls` (non-zero exit => missing/unmet deps) instead of only checking
        # for the directory's existence.
        $nodeModulesComplete = $false
        if (Test-Path "node_modules") {
            npm ls --prod --silent 2>$null | Out-Null
            $nodeModulesComplete = ($LASTEXITCODE -eq 0)
        }
        if (-not $nodeModulesComplete) {
            if (Test-Path "package-lock.json") {
                # Deterministic, clean install from the lockfile.
                npm ci 2>$null
            } else {
                npm install 2>$null
            }
        }
        Write-Host "      OK: Node.js CDK dependencies installed" -ForegroundColor Green
    } else {
        Write-Host "      WARN: No requirements.txt or package.json found" -ForegroundColor Yellow
    }

    # Bootstrap CDK (always run to ensure latest version)
    if (-not $SkipBootstrap) {
        Write-Host ""
        Write-Host "Ensuring CDK bootstrap is up to date..." -ForegroundColor Yellow
        # CDK writes progress/emoji to stderr - suppress PowerShell error handling for native commands
        $prevErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        npx -y cdk bootstrap "aws://$accountId/$currentRegion" --no-cli-pager 2>$null
        $cdkExitCode = $LASTEXITCODE
        $ErrorActionPreference = $prevErrorAction
        if ($cdkExitCode -ne 0) {
            Write-Host "      ERROR: CDK bootstrap failed" -ForegroundColor Red
            exit 1
        }
        Write-Host "      OK: CDK bootstrap is up to date" -ForegroundColor Green
    }

    # Deploy or destroy stack
    if ($DestroyStack) {
        Write-Host ""
        Write-Host "Destroying CDK stack..." -ForegroundColor Yellow
        $prevErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        if ([string]::IsNullOrEmpty($StackName)) {
            npx -y cdk destroy --force --no-cli-pager 2>$null
        } else {
            npx -y cdk destroy $StackName --force --no-cli-pager 2>$null
        }
        $cdkExitCode = $LASTEXITCODE
        $ErrorActionPreference = $prevErrorAction
        if ($cdkExitCode -ne 0) {
            Write-Host "      ERROR: CDK destroy failed" -ForegroundColor Red
            exit 1
        }
        Write-Host "      OK: Stack destroyed" -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "Deploying CDK stack..." -ForegroundColor Yellow
        $prevErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        if ([string]::IsNullOrEmpty($StackName)) {
            npx -y cdk deploy --require-approval never --no-cli-pager 2>$null
        } else {
            npx -y cdk deploy $StackName --require-approval never --no-cli-pager 2>$null
        }
        $cdkExitCode = $LASTEXITCODE
        $ErrorActionPreference = $prevErrorAction
        if ($cdkExitCode -ne 0) {
            Write-Host "      ERROR: CDK deployment failed" -ForegroundColor Red
            exit 1
        }
        Write-Host "      OK: Stack deployed successfully" -ForegroundColor Green
    }
} finally {
    Pop-Location
}

# Export variables for use by calling script
$global:CDK_ACCOUNT_ID = $accountId
$global:CDK_REGION = $currentRegion
