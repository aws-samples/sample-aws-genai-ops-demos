#Requires -Version 7.0
<#
.SYNOPSIS
    Deploys the AI IAM Access Analyzer Assistant infrastructure and frontend.

.DESCRIPTION
    This script deploys the complete solution: CDK infrastructure (Lambda, API Gateway,
    Cognito, CloudFront, S3) and builds/deploys the React frontend.

.EXAMPLE
    .\deploy-all.ps1
#>

param()

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

$stackName = "IamAnalyzerAssistantStack-$region"

# Step 1: Build frontend
Write-Host "[1/4] Building React frontend..." -ForegroundColor Cyan
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

# Step 2: Deploy CDK stack via the shared script (installs CDK deps, bootstraps, deploys)
Write-Host "[2/4] Deploying CDK infrastructure..." -ForegroundColor Cyan
$env:AWS_REGION = $region
$env:CDK_DEFAULT_ACCOUNT = $accountId
& "$PSScriptRoot\..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "$PSScriptRoot\infrastructure\cdk" -StackName $stackName
if ($LASTEXITCODE -ne 0) {
    Write-Host "CDK deployment failed" -ForegroundColor Red
    exit 1
}
Write-Host " Infrastructure deployed." -ForegroundColor Green

# Step 3: Get stack outputs and configure frontend
Write-Host "[3/4] Configuring frontend with stack outputs..." -ForegroundColor Cyan
function Get-StackOutput($key) {
    aws cloudformation describe-stacks --stack-name $stackName --region $region --no-cli-pager `
        --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" --output text
}

$apiEndpoint = Get-StackOutput "ApiEndpoint"
$userPoolId = Get-StackOutput "UserPoolId"
$userPoolClientId = Get-StackOutput "UserPoolClientId"
$identityPoolId = Get-StackOutput "IdentityPoolId"
$websiteUrl = Get-StackOutput "WebsiteUrl"
$frontendBucket = Get-StackOutput "FrontendBucketName"
$distributionId = Get-StackOutput "DistributionId"

if ([string]::IsNullOrEmpty($userPoolId) -or [string]::IsNullOrEmpty($frontendBucket)) {
    Write-Host "Failed to read outputs from stack $stackName" -ForegroundColor Red
    exit 1
}

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

# Step 4: Deploy frontend to S3 + invalidate CloudFront
Write-Host "[4/4] Uploading frontend to S3..." -ForegroundColor Cyan

# Rebuild with production env vars
Push-Location "$PSScriptRoot\frontend"
try {
    npm run build
    aws s3 sync dist/ "s3://$frontendBucket" --delete --region $region
} finally {
    Pop-Location
}

# Invalidate CloudFront cache
if (-not [string]::IsNullOrEmpty($distributionId) -and $distributionId -ne "None") {
    aws cloudfront create-invalidation --distribution-id $distributionId --paths "/*" --region $region | Out-Null
}
Write-Host " Frontend deployed." -ForegroundColor Green

# Create a default demo user (avoids Amplify force-change-password UI bug)
Write-Host ""
Write-Host "Creating demo user..." -ForegroundColor Cyan
$demoEmail = "admin@example.com"
# Generate a unique, strong password per deployment instead of shipping a hardcoded
# credential in the repo. Cognito's default policy requires >=8 chars with upper,
# lower, digit, and symbol, so we guarantee one of each and add random entropy.
$demoRandom = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 12 | ForEach-Object { [char]$_ })
$demoPassword = "Demo$demoRandom!9"

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
