#Requires -Version 7.0
<#
.SYNOPSIS
    Deploys the AI IAM Access Analyzer Assistant infrastructure and frontend.

.DESCRIPTION
    This script deploys the complete solution: CDK infrastructure (Lambda, API Gateway,
    Cognito, CloudFront, S3) and builds/deploys the React frontend.

    Default: single-account deploy, unchanged from before this feature existed.
    -MultiAccount: opt-in Level 2 hub-and-spoke (#173). This account becomes the
      "hub" — the assistant can read IAM/CloudTrail data in OTHER accounts of an
      AWS Organization, one named account_id at a time (never "every account" in
      one request — see agent.py's MULTI-ACCOUNT system-prompt rule). Runs
      shared/scripts/check-org-access.ps1 first; when this account may roll out
      service-managed StackSets, the Org stack places a read-only spoke role in
      every account under -OrgTargets. Otherwise the hub stack still deploys and
      the one command a management-account admin has to run is printed.
    -OrgTargets: comma-separated organization root (r-xxxx) and/or OU ids to
      roll the spoke role out to. Default: the whole organization (its root).

    Security Hub findings aggregation does NOT need any of this — that's
    already solved by Level 1 (#171/#173), which works automatically when this
    hub account is the Security Hub delegated administrator. -MultiAccount
    only matters for IAM/CloudTrail reads in a DIFFERENT account.

.EXAMPLE
    .\deploy-all.ps1

.EXAMPLE
    .\deploy-all.ps1 -MultiAccount -OrgTargets "ou-xxxx-yyyyyyyy"
#>

param(
    [switch]$MultiAccount,
    [string]$OrgTargets = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Check prerequisites
$sharedScript = Join-Path $PSScriptRoot "..\..\shared\scripts\check-prerequisites.ps1"
if (Test-Path $sharedScript) {
    & $sharedScript -RequiredService "bedrock" -MinAwsCliVersion "2.31.13"
} else {
    # Standalone mode — inline checks
    Write-Host "Checking prerequisites..." -ForegroundColor Cyan
    if (-not (Get-Command aws -ErrorAction SilentlyContinue)) { throw "AWS CLI not found." }
    if (-not (Get-Command python3 -ErrorAction SilentlyContinue)) { throw "Python 3 not found." }
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw "Node.js not found." }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "npm not found." }
    aws sts get-caller-identity | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "AWS credentials not configured." }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " AI IAM Access Analyzer Assistant" -ForegroundColor Cyan
Write-Host " Deployment Script" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Get region from shared prerequisites (or detect)
$region = if ($global:AWS_REGION) { $global:AWS_REGION } `
    elseif ($env:AWS_REGION) { $env:AWS_REGION } `
    elseif ($env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION } `
    else {
        $awsRegion = aws configure get region 2>$null
        if ($awsRegion) { $awsRegion } else { "us-east-1" }
    }

$accountId = aws sts get-caller-identity --query "Account" --output text
Write-Host " Region: $region" -ForegroundColor Yellow
Write-Host " Account: $accountId" -ForegroundColor Yellow
Write-Host ""

# Multi-account preflight (read-only; exit codes documented in
# check-org-access.ps1). Reused as-is from the hub-and-spoke pattern in
# operations-automation/aws-services-lifecycle-tracker (#144).
$orgRollout = $false
if ($MultiAccount) {
    Write-Host "Checking multi-account (organization) access..." -ForegroundColor Cyan
    $sharedOrgScript = Join-Path $PSScriptRoot "..\..\shared\scripts\check-org-access.ps1"
    if (Test-Path $sharedOrgScript) {
        & $sharedOrgScript
        $orgCheck = $LASTEXITCODE
        if ($orgCheck -eq 2) {
            Write-Host "This account cannot list the organization's accounts, so -MultiAccount is not possible from here." -ForegroundColor Red
            Write-Host "Apply the fix printed above, or deploy without -MultiAccount." -ForegroundColor Yellow
            exit 1
        } elseif ($orgCheck -ne 0 -and $orgCheck -ne 3) {
            Write-Host "Multi-account preflight failed." -ForegroundColor Red
            exit 1
        }
        $orgRollout = ($orgCheck -eq 0)
    } else {
        Write-Host " Warning: shared/scripts/check-org-access.ps1 not found - skipping the preflight check." -ForegroundColor Yellow
        Write-Host "   Proceeding on the assumption this account can run service-managed StackSets;" -ForegroundColor Yellow
        Write-Host "   if that's wrong, the Org stack deploy below will fail with a clear CloudFormation error." -ForegroundColor Yellow
        $orgRollout = $true
    }
    if ([string]::IsNullOrEmpty($OrgTargets)) {
        $OrgTargets = aws organizations list-roots --query "Roots[0].Id" --output text --no-cli-pager 2>$null
        if ([string]::IsNullOrEmpty($OrgTargets)) {
            Write-Host "Could not determine -OrgTargets automatically (not in an AWS Organization, or no permission to list roots)." -ForegroundColor Red
            Write-Host "Pass -OrgTargets r-xxxx or -OrgTargets ou-xxxx-yyyyyyyy explicitly." -ForegroundColor Yellow
            exit 1
        }
    }
    if ($orgRollout) {
        Write-Host " Multi-account enabled. Spoke-role targets: $OrgTargets (rolled out from this account)." -ForegroundColor Green
    } else {
        Write-Host " Multi-account enabled, but this account cannot roll out the spoke-role StackSet itself." -ForegroundColor Yellow
        Write-Host "   Targets: $OrgTargets - see the management-account command printed at the end of this run." -ForegroundColor Yellow
    }
    Write-Host ""
}

# Step 1: Install CDK dependencies
Write-Host "[1/5] Installing CDK dependencies..." -ForegroundColor Cyan
Push-Location "$PSScriptRoot\infrastructure\cdk"
try {
    if (-not (Test-Path ".venv")) {
        python -m venv .venv
    }
    if ($IsWindows -or $env:OS -match "Windows") {
        & ".venv\Scripts\Activate.ps1"
    } else {
        & ".venv/bin/Activate.ps1"
    }
    pip install -r requirements.txt --quiet
} finally {
    Pop-Location
}
Write-Host " CDK dependencies installed." -ForegroundColor Green

# Step 2: Build frontend
Write-Host "[2/5] Building React frontend..." -ForegroundColor Cyan
Push-Location "$PSScriptRoot\frontend"
try {
    if (-not (Test-Path "node_modules")) {
        npm install
    }
    npm run build
} finally {
    Pop-Location
}
Write-Host " Frontend built." -ForegroundColor Green

# Step 3: Deploy CDK stack
Write-Host "[3/5] Deploying CDK infrastructure..." -ForegroundColor Cyan
Push-Location "$PSScriptRoot\infrastructure\cdk"
try {
    $env:AWS_REGION = $region
    $env:CDK_DEFAULT_ACCOUNT = $accountId
    npx cdk deploy "IamAnalyzerAssistantStack-$region" --require-approval never --outputs-file outputs.json

    # Multi-account: roll the spoke role out via StackSet (#173 Level 2).
    # Only when this account can actually run service-managed StackSets
    # itself — otherwise $orgRollout is false and the management-account
    # command is printed at the end of this run instead.
    if ($MultiAccount -and $orgRollout) {
        Write-Host ""
        Write-Host "Deploying org stack (spoke-role StackSet)..." -ForegroundColor Cyan
        npx cdk deploy "IamAnalyzerAssistantOrg-$region" --require-approval never --context "orgTargets=$OrgTargets"
        Write-Host " Spoke role rolled out to accounts under $OrgTargets." -ForegroundColor Green
    }
} finally {
    Pop-Location
}
Write-Host " Infrastructure deployed." -ForegroundColor Green

# Step 4: Get stack outputs and configure frontend
Write-Host "[4/5] Configuring frontend with stack outputs..." -ForegroundColor Cyan
$outputsFile = "$PSScriptRoot\infrastructure\cdk\outputs.json"
$outputs = Get-Content $outputsFile | ConvertFrom-Json
$stackOutputs = $outputs."IamAnalyzerAssistantStack-$region"

$apiEndpoint = $stackOutputs.ApiEndpoint
$userPoolId = $stackOutputs.UserPoolId
$userPoolClientId = $stackOutputs.UserPoolClientId
$identityPoolId = $stackOutputs.IdentityPoolId
$websiteUrl = $stackOutputs.WebsiteUrl

# Generate frontend environment config
$envContent = @"
VITE_API_ENDPOINT=$apiEndpoint
VITE_USER_POOL_ID=$userPoolId
VITE_USER_POOL_CLIENT_ID=$userPoolClientId
VITE_IDENTITY_POOL_ID=$identityPoolId
VITE_REGION=$region
"@
$envContent | Out-File -FilePath "$PSScriptRoot\frontend\.env.production.local" -Encoding UTF8
Write-Host " Frontend configured." -ForegroundColor Green

# Step 5: Deploy frontend to S3 + invalidate CloudFront
Write-Host "[5/5] Uploading frontend to S3..." -ForegroundColor Cyan
$frontendBucket = $stackOutputs.FrontendBucketName

# Rebuild with production env vars
Push-Location "$PSScriptRoot\frontend"
try {
    npm run build
    aws s3 sync dist/ "s3://$frontendBucket" --delete --region $region
} finally {
    Pop-Location
}

# Invalidate CloudFront cache
$distributionId = $stackOutputs.DistributionId

if ($distributionId) {
    aws cloudfront create-invalidation --distribution-id $distributionId --paths "/*" --region $region | Out-Null
}
Write-Host " Frontend deployed." -ForegroundColor Green

# Create a default demo user (avoids Amplify force-change-password UI bug)
Write-Host ""
Write-Host "Creating demo user..." -ForegroundColor Cyan
$demoEmail = "admin@example.com"
$demoPassword = "IamAnalyzer2024!"

# Create user (ignore error if already exists)
try {
    aws cognito-idp admin-create-user `
        --user-pool-id $userPoolId `
        --username $demoEmail `
        --user-attributes Name=email_verified,Value=true `
        --message-action SUPPRESS `
        --region $region 2>$null | Out-Null
} catch { }

# Set permanent password (bypasses force-change-password flow)
aws cognito-idp admin-set-user-password `
    --user-pool-id $userPoolId `
    --username $demoEmail `
    --password $demoPassword `
    --permanent `
    --region $region 2>$null | Out-Null
Write-Host " Demo user created." -ForegroundColor Green

# Done
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host " Open the demo: $websiteUrl" -ForegroundColor Cyan
Write-Host " Region: $region" -ForegroundColor Cyan
Write-Host ""
Write-Host " Sign in with:" -ForegroundColor Yellow
Write-Host "   Email:    $demoEmail" -ForegroundColor Yellow
Write-Host "   Password: $demoPassword" -ForegroundColor Yellow
Write-Host ""
Write-Host " (You can create additional users via the Cognito console or CLI)" -ForegroundColor Yellow
Write-Host ""

if ($MultiAccount) {
    Write-Host "Multi-account (#173 Level 2):" -ForegroundColor Cyan
    Write-Host "  Hub account $accountId can read IAM/CloudTrail data in accounts under $OrgTargets," -ForegroundColor Gray
    Write-Host "  one named account_id at a time (ask the assistant about a specific account)." -ForegroundColor Gray
    if ($orgRollout) {
        Write-Host "  Spoke role: StackSet IamAnalyzerAssistantOrg-$region-SpokeRole (auto-deploys to accounts joining later)." -ForegroundColor Gray
        Write-Host "    aws cloudformation list-stack-instances --stack-set-name IamAnalyzerAssistantOrg-$region-SpokeRole --region $region" -ForegroundColor Gray
    } else {
        Write-Host "  ACTION REQUIRED: this account cannot run service-managed StackSets. From the MANAGEMENT account, run once:" -ForegroundColor Yellow
        Write-Host "    cd infrastructure/cdk && npx cdk deploy IamAnalyzerAssistantOrg-$region --context orgTargets=$OrgTargets --require-approval never" -ForegroundColor Gray
        Write-Host "  Until then, accounts under $OrgTargets have no spoke role, and cross-account reads will fail with a clear" -ForegroundColor Gray
        Write-Host "  'spoke role not deployed' message rather than silently returning no data." -ForegroundColor Gray
    }
    Write-Host ""
}
