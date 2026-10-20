# AI Lambda Runtime Migration Assistant - Complete Deployment Script
#
# Stacks (in order): Data -> Auth -> Pipeline -> Api -> Frontend
# No Docker needed: the Lambda bundle is built locally with pip by the
# Pipeline stack (pure-Python dependencies resolved as Linux/arm64 wheels).
#
# Default (single-account): discovers and assesses Lambda functions in the
# account you deploy into. The hub pipeline ALWAYS scans whatever AWS
# Organization it can read at runtime — no deploy flag is needed for that, and
# no flag can make the scan drift out of sync after a redeploy. A standalone
# account simply scans itself.
# -MultiAccount: additionally rolls the read-only spoke role out to every member
#   account of an AWS Organization, so the hub can also scan them. Runs
#   shared/scripts/check-org-access.ps1 first (read-only preflight). When the hub
#   may run service-managed StackSets, the Org stack performs the rollout;
#   otherwise the one command a management-account admin has to run is printed.
#   The pipeline stack itself is deployed identically with or without this flag.
# -OrgTargets: comma-separated organization root (r-xxxx) and/or OU ids to roll
#   the spoke role out to. Default: the whole organization (its root).
param(
    [switch]$MultiAccount,
    [string]$OrgTargets = ""
)

Write-Host "=== AI Lambda Runtime Migration Assistant Deployment ===" -ForegroundColor Cyan

# Run shared prerequisites check
Write-Host "`nRunning prerequisites check..." -ForegroundColor Yellow
& "..\..\shared\scripts\check-prerequisites.ps1" -MinAwsCliVersion "2.31.13" -MinPythonVersion "3.12" -RequireCDK -MinCdkVersion "2.1030.0"

if ($LASTEXITCODE -ne 0) {
    Write-Host "Prerequisites check failed" -ForegroundColor Red
    exit 1
}

# Install frontend dependencies
Write-Host "`nInstalling frontend dependencies..." -ForegroundColor Yellow
Write-Host "      (Installing React, Vite, Cognito SDK, and UI component libraries)" -ForegroundColor Gray
Push-Location frontend
npm install
Pop-Location

# Create placeholder dist BEFORE any CDK commands
# (CDK synthesizes all stacks even when deploying one, so frontend/dist must exist)
Write-Host "`nCreating placeholder frontend build..." -ForegroundColor Yellow
Write-Host "      (Generating temporary HTML file - required for CDK synthesis)" -ForegroundColor Gray
if (-not (Test-Path "frontend/dist")) {
    New-Item -ItemType Directory -Path "frontend/dist" -Force | Out-Null
    echo "<!DOCTYPE html><html><body><h1>Building...</h1></body></html>" > frontend/dist/index.html
} else {
    Write-Host "      Placeholder already exists, skipping..." -ForegroundColor Gray
}

# Get region for stack names
$region = $global:AWS_REGION

# Set stack names with region suffix
$dataStackName = "LambdaRuntimeMigrationData-$region"
$authStackName = "LambdaRuntimeMigrationAuth-$region"
$pipelineStackName = "LambdaRuntimeMigrationPipeline-$region"
$apiStackName = "LambdaRuntimeMigrationApi-$region"
$frontendStackName = "LambdaRuntimeMigrationFrontend-$region"

# Multi-account preflight (read-only; exit codes documented in check-org-access.ps1).
# This only drives the OPTIONAL Org/Spoke StackSet rollout below — the pipeline
# stack is deployed identically whether or not -MultiAccount is passed, because
# the pipeline decides multi-account vs single-account at runtime.
$orgRollout = $false
if ($MultiAccount) {
    Write-Host "`nChecking multi-account (organization) access..." -ForegroundColor Yellow
    & "..\..\shared\scripts\check-org-access.ps1"
    $orgCheck = $LASTEXITCODE
    if ($orgCheck -eq 2) {
        Write-Host "This account cannot list the organization's accounts, so -MultiAccount is not possible from here." -ForegroundColor Red
        Write-Host "Apply the fix printed above, or deploy without -MultiAccount to scan just this account." -ForegroundColor Yellow
        exit 1
    } elseif ($orgCheck -ne 0 -and $orgCheck -ne 3) {
        Write-Host "Multi-account preflight failed" -ForegroundColor Red
        exit 1
    }
    $orgRollout = ($orgCheck -eq 0)
    if ([string]::IsNullOrEmpty($OrgTargets)) {
        $OrgTargets = aws organizations list-roots --query "Roots[0].Id" --output text --no-cli-pager
    }
    Write-Host "      Spoke role rollout enabled (member accounts the hub will be able to scan)" -ForegroundColor Gray
    Write-Host "      Scan targets: $OrgTargets $(if ($orgRollout) { '(spoke role rolled out from this account)' } else { '(spoke role rollout must be run by the management account)' })" -ForegroundColor Gray
}

# Deploy data stack
Write-Host "`nDeploying data stack..." -ForegroundColor Yellow
Write-Host "      (Creating the DynamoDB inventory table and S3 bucket for migration artifacts)" -ForegroundColor Gray
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $dataStackName
if ($LASTEXITCODE -ne 0) { Write-Host "Data stack deployment failed" -ForegroundColor Red; exit 1 }

# Deploy auth stack
Write-Host "`nDeploying authentication stack..." -ForegroundColor Yellow
Write-Host "      (Creating the Cognito User Pool and app client)" -ForegroundColor Gray
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $authStackName -SkipBootstrap
if ($LASTEXITCODE -ne 0) { Write-Host "Auth stack deployment failed" -ForegroundColor Red; exit 1 }

# Deploy pipeline stack (durable discover+assess function + API function)
Write-Host "`nDeploying pipeline stack..." -ForegroundColor Yellow
Write-Host "      (Bundling Python code with pip, creating the Lambda durable function and API function)" -ForegroundColor Gray
# Deployed identically with or without -MultiAccount: the pipeline role always
# carries the org-read + spoke-assume grants and decides scope at runtime.
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $pipelineStackName -SkipBootstrap
if ($LASTEXITCODE -ne 0) { Write-Host "Pipeline stack deployment failed" -ForegroundColor Red; exit 1 }

# Multi-account: roll the read-only spoke role out to every member account via a
# service-managed StackSet (the Org stack). Only when this account may run them;
# otherwise the ACTION REQUIRED command is printed in the summary below.
if ($MultiAccount -and $orgRollout) {
    $hubAccount = aws sts get-caller-identity --query Account --output text --no-cli-pager
    $orgStackName = "LambdaRuntimeMigrationOrg-$region"
    Write-Host "`nDeploying org stack (spoke role StackSet)..." -ForegroundColor Yellow
    Write-Host "      (Service-managed StackSet placing the read-only LambdaRuntimeMigrationScanRole in every account under $OrgTargets)" -ForegroundColor Gray
    & "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $orgStackName -SkipBootstrap -CdkContext @("orgTargets=$OrgTargets", "hubAccountId=$hubAccount")
    if ($LASTEXITCODE -ne 0) { Write-Host "Org stack deployment failed" -ForegroundColor Red; exit 1 }
}

# Deploy API stack (HTTP API + Cognito JWT authorizer)
Write-Host "`nDeploying API stack..." -ForegroundColor Yellow
Write-Host "      (Creating the HTTP API with Cognito JWT authorization in front of the API function)" -ForegroundColor Gray
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $apiStackName -SkipBootstrap
if ($LASTEXITCODE -ne 0) { Write-Host "API stack deployment failed" -ForegroundColor Red; exit 1 }

# Build frontend with API URL and Cognito config (after backend is complete)
Write-Host "`nBuilding frontend with stack outputs..." -ForegroundColor Yellow
Write-Host "      (Retrieving API URL and Cognito config, building the React app)" -ForegroundColor Gray
$apiUrl = aws cloudformation describe-stacks --stack-name $apiStackName --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text --no-cli-pager
$userPoolId = aws cloudformation describe-stacks --stack-name $authStackName --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text --no-cli-pager
$userPoolClientId = aws cloudformation describe-stacks --stack-name $authStackName --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" --output text --no-cli-pager

if ([string]::IsNullOrEmpty($apiUrl) -or $apiUrl -eq "None") {
    Write-Host "Failed to get API URL from stack outputs" -ForegroundColor Red
    exit 1
}

if ([string]::IsNullOrEmpty($userPoolId) -or $userPoolId -eq "None" -or [string]::IsNullOrEmpty($userPoolClientId) -or $userPoolClientId -eq "None") {
    Write-Host "Failed to get Cognito config from stack outputs" -ForegroundColor Red
    exit 1
}

Write-Host "API URL: $apiUrl" -ForegroundColor Green
Write-Host "User Pool ID: $userPoolId" -ForegroundColor Green
Write-Host "User Pool Client ID: $userPoolClientId" -ForegroundColor Green

& .\scripts\build-frontend.ps1 -ApiUrl $apiUrl -UserPoolId $userPoolId -UserPoolClientId $userPoolClientId
if ($LASTEXITCODE -ne 0) { Write-Host "Frontend build failed" -ForegroundColor Red; exit 1 }

# Deploy frontend stack (CloudFront + S3)
Write-Host "`nDeploying frontend stack..." -ForegroundColor Yellow
Write-Host "      (Deploying the built React app to S3 behind CloudFront)" -ForegroundColor Gray
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $frontendStackName -SkipBootstrap
if ($LASTEXITCODE -ne 0) { Write-Host "Frontend stack deployment failed" -ForegroundColor Red; exit 1 }

# Gather outputs
$websiteUrl = aws cloudformation describe-stacks --stack-name $frontendStackName --query "Stacks[0].Outputs[?OutputKey=='WebsiteUrl'].OutputValue" --output text --no-cli-pager

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Dashboard URL:   $websiteUrl" -ForegroundColor Cyan
Write-Host "  API URL:         $apiUrl" -ForegroundColor Cyan
Write-Host "  Region:          $region" -ForegroundColor Cyan
Write-Host "  User Pool ID:    $userPoolId" -ForegroundColor Cyan
Write-Host "`nNext Steps:" -ForegroundColor Yellow
Write-Host "  1. Create a dashboard user (copy-paste these two commands):" -ForegroundColor Gray
Write-Host ""
Write-Host "     aws cognito-idp admin-create-user --user-pool-id $userPoolId --username admin@example.com --user-attributes Name=email,Value=admin@example.com Name=email_verified,Value=true --message-action SUPPRESS --no-cli-pager" -ForegroundColor White
Write-Host ""
Write-Host "     aws cognito-idp admin-set-user-password --user-pool-id $userPoolId --username admin@example.com --password ""YourSecurePassword123!"" --permanent --no-cli-pager" -ForegroundColor White
Write-Host ""
Write-Host "     (The username is the email address; replace it and the password with your own values)" -ForegroundColor DarkGray
Write-Host "  2. Sign in at the Dashboard URL above and click Scan functions to run the first discover + assess pipeline" -ForegroundColor Gray
Write-Host "     The hub always scans whatever organization it can read at runtime; a standalone account scans itself." -ForegroundColor DarkGray
if ($MultiAccount) {
    $hubAccount = aws sts get-caller-identity --query Account --output text --no-cli-pager
    Write-Host "`nMulti-account:" -ForegroundColor Yellow
    Write-Host "  Spoke role rolled out so hub account $hubAccount can also scan every active account under $OrgTargets" -ForegroundColor Gray
    if ($orgRollout) {
        Write-Host "  Spoke role: StackSet ai-lambda-runtime-migration-spoke (auto-deploys to accounts joining later)" -ForegroundColor Gray
        Write-Host "     aws cloudformation list-stack-instances --stack-set-name ai-lambda-runtime-migration-spoke --region $region" -ForegroundColor White
    } else {
        Write-Host "  ACTION REQUIRED: this account cannot run service-managed StackSets. From the MANAGEMENT account, run once:" -ForegroundColor Yellow
        Write-Host "     cd cdk; npx cdk deploy LambdaRuntimeMigrationOrg-$region --context orgTargets=$OrgTargets --context hubAccountId=$hubAccount --require-approval never" -ForegroundColor White
        Write-Host "     (or register this account as a StackSets delegated administrator, see the preflight output above, and re-run with -MultiAccount)" -ForegroundColor DarkGray
        Write-Host "  Until then, member accounts are reported as failed in each scan; the hub itself is scanned normally." -ForegroundColor Gray
    }
}
