# GenAI Ops Demo Library - Shared CDK Deployment Script
# This script handles CDK bootstrap, dependency installation, and deployment

param(
    [Parameter(Mandatory=$true)]
    [string]$CdkDirectory,
    
    [string]$StackName = "",
    [switch]$DestroyStack = $false,
    [switch]$SkipBootstrap = $false
)

$ErrorActionPreference = "Stop"

# Get AWS account and region
$accountId = aws sts get-caller-identity --query Account --output text --no-cli-pager
$currentRegion = aws configure get region

if ([string]::IsNullOrEmpty($currentRegion)) {
    Write-Host "❌ No AWS region configured" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== CDK Deployment (Shared Script) ===" -ForegroundColor Cyan
Write-Host "      Directory: $CdkDirectory" -ForegroundColor Gray
Write-Host "      Region: $currentRegion" -ForegroundColor Gray
Write-Host "      Account: $accountId" -ForegroundColor Gray

# Verify CDK directory exists
if (-not (Test-Path $CdkDirectory)) {
    Write-Host "❌ CDK directory not found: $CdkDirectory" -ForegroundColor Red
    exit 1
}

Push-Location $CdkDirectory

try {
    # Install dependencies
    Write-Host ""
    Write-Host "Installing CDK dependencies..." -ForegroundColor Yellow
    
    if (Test-Path "requirements.txt") {
        # Python CDK project
        pip install -r requirements.txt -q
        Write-Host "      ✓ Python CDK dependencies installed" -ForegroundColor Green
    } elseif (Test-Path "package.json") {
        # TypeScript/JavaScript CDK project
        if (-not (Test-Path "node_modules")) {
            npm install
        }
        Write-Host "      ✓ Node.js CDK dependencies installed" -ForegroundColor Green
    } else {
        Write-Host "      ⚠ No requirements.txt or package.json found" -ForegroundColor Yellow
    }

    # Bootstrap CDK (always run to ensure latest version)
    if (-not $SkipBootstrap) {
        Write-Host ""
        Write-Host "Ensuring CDK bootstrap is up to date..." -ForegroundColor Yellow
        $bootstrapOutput = npx -y cdk bootstrap "aws://$accountId/$currentRegion" --no-cli-pager 2>&1
        $bootstrapExitCode = $LASTEXITCODE
        if ($bootstrapExitCode -ne 0) {
            Write-Host "      ❌ CDK bootstrap failed" -ForegroundColor Red
            Write-Host $bootstrapOutput -ForegroundColor Gray
            exit 1
        }
        Write-Host "      ✓ CDK bootstrap is up to date" -ForegroundColor Green
    }

    # Deploy or destroy stack
    if ($DestroyStack) {
        Write-Host ""
        Write-Host "Destroying CDK stack..." -ForegroundColor Yellow
        if ([string]::IsNullOrEmpty($StackName)) {
            npx -y cdk destroy --force --no-cli-pager
        } else {
            npx -y cdk destroy $StackName --force --no-cli-pager
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "      ❌ CDK destroy failed" -ForegroundColor Red
            exit 1
        }
        Write-Host "      ✓ Stack destroyed" -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "Deploying CDK stack..." -ForegroundColor Yellow
        if ([string]::IsNullOrEmpty($StackName)) {
            npx -y cdk deploy --require-approval never --no-cli-pager
        } else {
            npx -y cdk deploy $StackName --require-approval never --no-cli-pager
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "      ❌ CDK deployment failed" -ForegroundColor Red
            exit 1
        }
        Write-Host "      ✓ Stack deployed successfully" -ForegroundColor Green
    }
} finally {
    Pop-Location
}

# Export variables for use by calling script
$global:CDK_ACCOUNT_ID = $accountId
$global:CDK_REGION = $currentRegion
