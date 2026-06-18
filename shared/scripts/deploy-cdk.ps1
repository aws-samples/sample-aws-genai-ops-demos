# GenAI Ops Demo Library - Shared CDK Deployment Script
# This script handles CDK bootstrap, dependency installation, and deployment

param(
    [Parameter(Mandatory=$true)]
    [string]$CdkDirectory,
    [string]$StackName = "",
    [switch]$DestroyStack = $false,
    [switch]$SkipBootstrap = $false,
    [string]$ExtraArgs = ""
)

# Set PYTHONPATH to include shared utilities
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH = $repoRoot

# Get AWS account and region
$accountId = aws sts get-caller-identity --query Account --output text --no-cli-pager
$currentRegion = aws configure get region

if ([string]::IsNullOrEmpty($currentRegion)) {
    Write-Host "ERROR: No AWS region configured" -ForegroundColor Red
    exit 1
}

# Set CDK environment variables so app.ts can resolve account/region during synthesis
$env:CDK_DEFAULT_ACCOUNT = $accountId
$env:CDK_DEFAULT_REGION = $currentRegion

# Ensure CDK's Node.js SDK can resolve credentials.
# If no AWS_PROFILE is set, detect the active SSO profile so CDK doesn't fail
# with "no credentials have been configured" when the [default] profile is incomplete.
if ([string]::IsNullOrEmpty($env:AWS_PROFILE)) {
    # Check if default profile can resolve credentials via the Node.js SDK path
    # by looking for a complete SSO profile. If [default] lacks sso_session/sso_account_id,
    # find the named profile that matches our account.
    $profileList = aws configure list-profiles 2>$null
    if ($profileList) {
        foreach ($awsProfile in $profileList -split "`n") {
            $awsProfile = $awsProfile.Trim()
            if ([string]::IsNullOrEmpty($awsProfile) -or $awsProfile -eq "default") { continue }
            $profileAccount = aws configure get sso_account_id --profile $awsProfile 2>$null
            if ($profileAccount -eq $accountId) {
                $profileSession = aws configure get sso_session --profile $awsProfile 2>$null
                if (-not [string]::IsNullOrEmpty($profileSession)) {
                    $env:AWS_PROFILE = $awsProfile
                    break
                }
            }
        }
    }
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
        pip install -r requirements.txt -q 2>$null
        Write-Host "      OK: Python CDK dependencies installed" -ForegroundColor Green
    } elseif (Test-Path "package.json") {
        if (-not (Test-Path "node_modules")) {
            npm install 2>$null
        }
        Write-Host "      OK: Node.js CDK dependencies installed" -ForegroundColor Green
    } else {
        Write-Host "      WARN: No requirements.txt or package.json found" -ForegroundColor Yellow
    }

    # Bootstrap CDK (always run to ensure latest version)
    if (-not $SkipBootstrap) {
        Write-Host ""
        Write-Host "Ensuring CDK bootstrap is up to date..." -ForegroundColor Yellow
        # CDK writes progress/emoji to stderr - capture output for error reporting
        $prevErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $cdkOutput = npx -y cdk bootstrap "aws://$accountId/$currentRegion" --no-cli-pager 2>&1
        $cdkExitCode = $LASTEXITCODE
        $ErrorActionPreference = $prevErrorAction
        # CDK may return non-zero for warnings (e.g. "Unknown option --cliPager")
        # Only treat as failure if output contains real error indicators
        $hasRealError = $false
        if ($cdkExitCode -ne 0) {
            $cdkOutput | ForEach-Object {
                $line = $_.ToString()
                if ($line -match "Error|fail|Unable|Cannot|denied|credentials" -and $line -notmatch "^\[Warning" -and $line -notmatch "Unknown option") {
                    $hasRealError = $true
                }
            }
        }
        if ($hasRealError) {
            Write-Host "      ERROR: CDK bootstrap failed" -ForegroundColor Red
            $cdkOutput | ForEach-Object {
                $line = $_.ToString()
                if ($line -match "Error|error|fail|Unable|Cannot|denied|credentials" -and $line -notmatch "^\[Warning") {
                    Write-Host "      $line" -ForegroundColor Red
                }
            }
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
            $cdkOutput = npx -y cdk destroy --force --no-cli-pager 2>&1
        } else {
            $cdkOutput = npx -y cdk destroy $StackName --force --no-cli-pager 2>&1
        }
        $cdkExitCode = $LASTEXITCODE
        $ErrorActionPreference = $prevErrorAction
        if ($cdkExitCode -ne 0) {
            Write-Host "      ERROR: CDK destroy failed" -ForegroundColor Red
            # Display error details (filter out noise like warnings and progress)
            $cdkOutput | ForEach-Object {
                $line = $_.ToString()
                if ($line -match "Error|error|fail|Unable|Cannot|denied|not found" -and $line -notmatch "^\[Warning") {
                    Write-Host "      $line" -ForegroundColor Red
                }
            }
            exit 1
        }
        Write-Host "      OK: Stack destroyed" -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "Deploying CDK stack..." -ForegroundColor Yellow
        $prevErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        if ([string]::IsNullOrEmpty($StackName)) {
            $cdkOutput = npx -y cdk deploy --require-approval never --no-cli-pager $ExtraArgs 2>&1
        } else {
            $cdkOutput = npx -y cdk deploy $StackName --require-approval never --no-cli-pager $ExtraArgs 2>&1
        }
        $cdkExitCode = $LASTEXITCODE
        $ErrorActionPreference = $prevErrorAction
        if ($cdkExitCode -ne 0) {
            # Check if it's a real error or just CDK warnings
            $hasRealError = $false
            $cdkOutput | ForEach-Object {
                $line = $_.ToString()
                if ($line -match "Error|error|fail|Unable|Cannot|denied|not found|credentials" -and $line -notmatch "^\[Warning" -and $line -notmatch "Unknown option" -and $line -notmatch "will be ignored") {
                    $hasRealError = $true
                }
            }
            if ($hasRealError) {
                Write-Host "      ERROR: CDK deployment failed" -ForegroundColor Red
                $cdkOutput | ForEach-Object {
                    $line = $_.ToString()
                    if ($line -match "Error|error|fail|Unable|Cannot|denied|not found|credentials" -and $line -notmatch "^\[Warning" -and $line -notmatch "Unknown option") {
                        Write-Host "      $line" -ForegroundColor Red
                    }
                }
                exit 1
            }
        }
        Write-Host "      OK: Stack deployed successfully" -ForegroundColor Green
    }
} finally {
    Pop-Location
}

# Export variables for use by calling script
$global:CDK_ACCOUNT_ID = $accountId
$global:CDK_REGION = $currentRegion
