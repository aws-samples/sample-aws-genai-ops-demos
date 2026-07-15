# deploy-all.ps1 — Proactive Health Event Impact Analyzer
# Deploys the full solution using the interactive setup wizard.
#
# Usage:
#   .\deploy-all.ps1              # Full deployment (prerequisites + wizard)
#   .\deploy-all.ps1 -SkipSetup   # Skip prerequisites, run wizard only
#
# The setup wizard handles:
#   1. DevOps Agent Space creation and configuration
#   2. IAM roles for topology discovery
#   3. Webhook generation for investigation triggers
#   4. Notification channel configuration (email, Slack, MS Teams)
#   5. CDK stack deployment

param(
    [switch]$SkipSetup
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ─── Region Detection ─────────────────────────────────────────────────────────
# Priority: Environment variable > AWS CLI config > Fallback
function Get-AwsRegion {
    if ($env:AWS_DEFAULT_REGION) { return $env:AWS_DEFAULT_REGION }
    if ($env:AWS_REGION) { return $env:AWS_REGION }

    try {
        $cliRegion = (aws configure get region 2>$null)
        if ($cliRegion) { return $cliRegion.Trim() }
    } catch {}

    return "us-east-1"
}

# ─── Prerequisites Check ─────────────────────────────────────────────────────
function Test-Prerequisites {
    Write-Host ""
    Write-Host "Checking prerequisites..." -ForegroundColor Cyan
    Write-Host ""

    # Check AWS CLI (minimum v2.34.20 required for devops-agent subcommand)
    try {
        $awsVersionOutput = (aws --version 2>&1) | Out-String
        Write-Host "  [OK] AWS CLI: $($awsVersionOutput.Trim())" -ForegroundColor Green

        if ($awsVersionOutput -match "aws-cli/(\d+\.\d+\.\d+)") {
            $installedVersion = $Matches[1]
            $requiredVersion = "2.34.20"
            $installed = [Version]$installedVersion
            $required = [Version]$requiredVersion
            if ($installed -lt $required) {
                Write-Host "  [WARN] AWS CLI $requiredVersion+ recommended (devops-agent commands). You have $installedVersion" -ForegroundColor Yellow
            }
        }
    } catch {
        Write-Host "  [FAIL] AWS CLI not found. Install from https://aws.amazon.com/cli/" -ForegroundColor Red
        exit 1
    }

    # Check AWS credentials
    try {
        $identityJson = (aws sts get-caller-identity --output json 2>$null)
        $identity = $identityJson | ConvertFrom-Json
        Write-Host "  [OK] AWS Account: $($identity.Account)" -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] AWS credentials not configured. Run 'aws configure' or set environment variables." -ForegroundColor Red
        exit 1
    }

    # Check Node.js
    try {
        $nodeVersion = (node --version 2>&1)
        Write-Host "  [OK] Node.js: $nodeVersion" -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] Node.js not found. Install from https://nodejs.org/" -ForegroundColor Red
        exit 1
    }

    # Check npm
    try {
        $npmVersion = (npm --version 2>&1)
        Write-Host "  [OK] npm: $npmVersion" -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] npm not found." -ForegroundColor Red
        exit 1
    }

    # Check region
    $region = Get-AwsRegion
    Write-Host "  [OK] Region: $region" -ForegroundColor Green
    Write-Host ""

    return $region
}

# ─── Main ─────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Proactive Health Event Impact Analyzer" -ForegroundColor Cyan
Write-Host "  Deployment Script" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

if (-not $SkipSetup) {
    # Use shared prerequisites if available (monorepo), otherwise inline checks
    $sharedPrereqs = Join-Path $ScriptDir "..\..\shared\scripts\check-prerequisites.ps1"
    if (Test-Path $sharedPrereqs) {
        & $sharedPrereqs -RequiredService "bedrock" -MinAwsCliVersion "2.34.20"
        $region = $global:AWS_REGION
    } else {
        $region = Test-Prerequisites
    }

    # Install CDK dependencies
    Write-Host "Installing CDK dependencies..." -ForegroundColor Cyan

    Push-Location "$ScriptDir\infrastructure\cdk"
    try {
        npm install --silent 2>$null
        Write-Host "  [OK] CDK dependencies installed" -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] npm install failed in infrastructure/cdk" -ForegroundColor Red
        exit 1
    }
    Pop-Location

    # Install setup wizard dependencies
    Push-Location "$ScriptDir\scripts"
    try {
        npm install --silent 2>$null
        Write-Host "  [OK] Scripts dependencies installed" -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] npm install failed in scripts/" -ForegroundColor Red
        exit 1
    }
    Pop-Location
} else {
    Write-Host ""
    Write-Host "Skipping prerequisites and dependency installation..." -ForegroundColor Yellow
    $region = Get-AwsRegion
}

# Run the interactive setup wizard (handles CDK deployment)
Write-Host ""
Write-Host "Launching interactive setup wizard..." -ForegroundColor Cyan
Write-Host "The wizard will guide you through DevOps Agent configuration and CDK deployment." -ForegroundColor Gray
Write-Host ""

Push-Location $ScriptDir
try {
    npx ts-node scripts/setup-wizard.ts
    $wizardExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($wizardExitCode -ne 0) {
    Write-Host ""
    Write-Host "  Setup wizard exited with errors." -ForegroundColor Red
    Write-Host "  Review the output above for details." -ForegroundColor Red
    exit $wizardExitCode
}

# ─── Deployment Summary ───────────────────────────────────────────────────────

$stackName = "HealthEventAnalyzerStack-$region"

# Retrieve stack outputs
$stateMachineArn = aws cloudformation describe-stacks --stack-name $stackName --query "Stacks[0].Outputs[?OutputKey=='StateMachineArn'].OutputValue" --output text --no-cli-pager 2>$null
$teamsTableName = aws cloudformation describe-stacks --stack-name $stackName --query "Stacks[0].Outputs[?OutputKey=='TeamsTableName'].OutputValue" --output text --no-cli-pager 2>$null
$snsTopicArn = aws cloudformation describe-stacks --stack-name $stackName --query "Stacks[0].Outputs[?OutputKey=='NotificationTopicArn'].OutputValue" --output text --no-cli-pager 2>$null

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Region:          $region" -ForegroundColor Cyan
Write-Host "  Stack:           $stackName" -ForegroundColor Cyan
if ($stateMachineArn) {
    Write-Host "  State Machine:   $stateMachineArn" -ForegroundColor Cyan
}
if ($teamsTableName) {
    Write-Host "  Teams Table:     $teamsTableName" -ForegroundColor Cyan
}
if ($snsTopicArn) {
    Write-Host "  SNS Topic:       $snsTopicArn" -ForegroundColor Cyan
}
Write-Host ""
Write-Host "  Next Steps:" -ForegroundColor Yellow
Write-Host "    1. Seed team routing:  .\scripts\seed-teams.ps1 -TableName $teamsTableName" -ForegroundColor White
Write-Host "    2. Upload DevOps Agent skill from devops-agent-skill/SKILL.md" -ForegroundColor White
Write-Host "    3. Test with: aws lambda invoke --function-name <EventRouter> --payload file://events/test-lambda-deprecation-event.json /tmp/out.json" -ForegroundColor White
Write-Host ""
