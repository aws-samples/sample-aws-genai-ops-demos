# AWS Services Lifecycle Tracker - Complete Deployment Script
#
# Stacks (in order): Data -> Auth -> Pipeline -> [Org] -> Api -> Frontend
# No Docker needed: the Lambda bundle is built locally with pip (pure-Python
# dependencies resolved as Linux/arm64 wheels).
#
# Default: scans the account you deploy into (single-account).
# -MultiAccount: hub-and-spoke scan of an AWS Organization from this account
#   (the "hub"). Runs shared/scripts/check-org-access.ps1 first; when the hub
#   may run service-managed StackSets, the Org stack rolls the read-only spoke
#   role out to every member account. Otherwise the hub stacks still deploy and
#   the one command a management-account admin has to run is printed.
# -OrgTargets: comma-separated organization root (r-xxxx) and/or OU ids to scan
#   and roll out to. Default: the whole organization (its root).
param(
    [switch]$MultiAccount,
    [string]$OrgTargets = ""
)

Write-Host "=== AWS Services Lifecycle Tracker Deployment ===" -ForegroundColor Cyan

# Run shared prerequisites check
Write-Host "`nRunning prerequisites check..." -ForegroundColor Yellow
& "..\..\shared\scripts\check-prerequisites.ps1" -RequiredService "bedrock" -MinAwsCliVersion "2.33.22" -MinPythonVersion "3.11" -RequireCDK -MinCdkVersion "2.1030.0"

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

# Multi-account preflight (read-only; exit codes documented in check-org-access.ps1)
$orgRollout = $false
if ($MultiAccount) {
    Write-Host "`nChecking multi-account (organization) access..." -ForegroundColor Yellow
    & "..\..\shared\scripts\check-org-access.ps1"
    $orgCheck = $LASTEXITCODE
    if ($orgCheck -eq 2) {
        Write-Host "This account cannot list the organization's accounts, so -MultiAccount is not possible from here." -ForegroundColor Red
        Write-Host "Apply the fix printed above, or deploy without -MultiAccount and add accounts manually (see README, 'Manual account list')." -ForegroundColor Yellow
        exit 1
    } elseif ($orgCheck -ne 0 -and $orgCheck -ne 3) {
        Write-Host "Multi-account preflight failed" -ForegroundColor Red
        exit 1
    }
    $orgRollout = ($orgCheck -eq 0)
    if ([string]::IsNullOrEmpty($OrgTargets)) {
        $OrgTargets = aws organizations list-roots --query "Roots[0].Id" --output text --no-cli-pager
    }
    Write-Host "      Scan targets: $OrgTargets $(if ($orgRollout) { '(spoke role rolled out from this account)' } else { '(spoke role rollout must be run by the management account)' })" -ForegroundColor Gray
}

# Deploy data stack
Write-Host "`nDeploying data stack..." -ForegroundColor Yellow
Write-Host "      (Creating DynamoDB tables and populating service configurations)" -ForegroundColor Gray
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName "AWSServicesLifecycleTrackerData-$region"

if ($LASTEXITCODE -ne 0) {
    Write-Host "Data stack deployment failed" -ForegroundColor Red
    exit 1
}

# Deploy auth stack
Write-Host "`nDeploying authentication stack..." -ForegroundColor Yellow
Write-Host "      (Creating Cognito User Pool with email verification and password policies)" -ForegroundColor Gray
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName "AWSServicesLifecycleTrackerAuth-$region" -SkipBootstrap

if ($LASTEXITCODE -ne 0) {
    Write-Host "Auth deployment failed" -ForegroundColor Red
    exit 1
}

# Deploy pipeline stack (durable refresh pipeline + API function + schedules)
Write-Host "`nDeploying pipeline stack..." -ForegroundColor Yellow
Write-Host "      (Bundling Python code with pip, creating the Lambda durable function, API function, SNS topic and schedules)" -ForegroundColor Gray
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName "AWSServicesLifecycleTrackerPipeline-$region" -SkipBootstrap

if ($LASTEXITCODE -ne 0) {
    Write-Host "Pipeline deployment failed" -ForegroundColor Red
    exit 1
}

# Multi-account: roll the spoke role out (StackSet) and tell the pipeline what to scan
if ($MultiAccount) {
    if ($orgRollout) {
        Write-Host "`nDeploying org stack (spoke role StackSet)..." -ForegroundColor Yellow
        Write-Host "      (Service-managed StackSet placing the read-only LifecycleTrackerScanRole in every account under $OrgTargets)" -ForegroundColor Gray
        & "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName "AWSServicesLifecycleTrackerOrg-$region" -SkipBootstrap -CdkContext "orgTargets=$OrgTargets"

        if ($LASTEXITCODE -ne 0) {
            Write-Host "Org stack deployment failed" -ForegroundColor Red
            exit 1
        }
    }

    Write-Host "`nConfiguring scan targets..." -ForegroundColor Yellow
    $targetIds = $OrgTargets.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    $regionsJson = "[{""S"": ""$region""}]"
    if ($targetIds | Where-Object { $_ -like "ou-*" }) {
        $ouJson = ($targetIds | ForEach-Object { "{""S"": ""$_""}" }) -join ","
        $item = "{""service_name"": {""S"": ""_scan_targets""}, ""source"": {""S"": ""ou""}, ""ou_ids"": {""L"": [$ouJson]}, ""regions"": {""L"": $regionsJson}}"
    } else {
        $item = "{""service_name"": {""S"": ""_scan_targets""}, ""source"": {""S"": ""organization""}, ""regions"": {""L"": $regionsJson}}"
    }
    $itemFile = Join-Path ([System.IO.Path]::GetTempPath()) "lifecycle-scan-targets.json"
    $item | Out-File -FilePath $itemFile -Encoding ascii
    aws dynamodb put-item --table-name "service-extraction-state" --item "file://$itemFile" --no-cli-pager
    Remove-Item $itemFile -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to write scan targets" -ForegroundColor Red
        exit 1
    }
    Write-Host "      Scan targets set: $OrgTargets in $region (editable later in the UI, Sources & coverage)" -ForegroundColor Gray
}

# Deploy API stack (HTTP API + Cognito JWT authorizer)
Write-Host "`nDeploying API stack..." -ForegroundColor Yellow
Write-Host "      (Creating the HTTP API with Cognito JWT authorization in front of the API function)" -ForegroundColor Gray
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName "AWSServicesLifecycleTrackerApi-$region" -SkipBootstrap

if ($LASTEXITCODE -ne 0) {
    Write-Host "API deployment failed" -ForegroundColor Red
    exit 1
}

# Build and deploy frontend (after backend is complete)
Write-Host "`nBuilding and deploying frontend..." -ForegroundColor Yellow
Write-Host "      (Retrieving API URL and Cognito config, building React app, deploying to S3 + CloudFront)" -ForegroundColor Gray
$apiUrl = aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerApi-$region" --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text --no-cli-pager
$userPoolId = aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerAuth-$region" --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text --no-cli-pager
$userPoolClientId = aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerAuth-$region" --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" --output text --no-cli-pager

if ([string]::IsNullOrEmpty($apiUrl)) {
    Write-Host "Failed to get API URL from stack outputs" -ForegroundColor Red
    exit 1
}

if ([string]::IsNullOrEmpty($userPoolId) -or [string]::IsNullOrEmpty($userPoolClientId)) {
    Write-Host "Failed to get Cognito config from stack outputs" -ForegroundColor Red
    exit 1
}

Write-Host "API URL: $apiUrl" -ForegroundColor Green
Write-Host "User Pool ID: $userPoolId" -ForegroundColor Green
Write-Host "User Pool Client ID: $userPoolClientId" -ForegroundColor Green

# Build frontend with API URL and Cognito config
& .\scripts\build-frontend.ps1 -UserPoolId $userPoolId -UserPoolClientId $userPoolClientId -ApiUrl $apiUrl -Region $region

if ($LASTEXITCODE -ne 0) {
    Write-Host "Frontend build failed" -ForegroundColor Red
    exit 1
}

# Deploy frontend stack
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName "AWSServicesLifecycleTrackerFrontend-$region" -SkipBootstrap

if ($LASTEXITCODE -ne 0) {
    Write-Host "Frontend deployment failed" -ForegroundColor Red
    exit 1
}

# Gather outputs
$websiteUrl = aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerFrontend-$region" --query "Stacks[0].Outputs[?OutputKey=='WebsiteUrl'].OutputValue" --output text --no-cli-pager
$pipelineArn = aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerPipeline-$region" --query "Stacks[0].Outputs[?OutputKey=='PipelineFunctionAliasArn'].OutputValue" --output text --no-cli-pager
$topicArn = aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerPipeline-$region" --query "Stacks[0].Outputs[?OutputKey=='NotificationTopicArn'].OutputValue" --output text --no-cli-pager

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Open the demo:      $websiteUrl" -ForegroundColor Cyan
Write-Host "  API URL:            $apiUrl" -ForegroundColor Cyan
Write-Host "  Pipeline (durable): $pipelineArn" -ForegroundColor Cyan
Write-Host "  Notifications:      $topicArn" -ForegroundColor Cyan
Write-Host "  Region:             $region" -ForegroundColor Cyan
Write-Host "  User Pool ID:       $userPoolId" -ForegroundColor Cyan
Write-Host "`nNext Steps:" -ForegroundColor Yellow
Write-Host "  1. Create an admin user (copy-paste these two commands):" -ForegroundColor Gray
Write-Host ""
Write-Host "     aws cognito-idp admin-create-user --user-pool-id $userPoolId --username admin --user-attributes Name=email,Value=admin@company.com Name=email_verified,Value=true --message-action SUPPRESS" -ForegroundColor White
Write-Host ""
Write-Host "     aws cognito-idp admin-set-user-password --user-pool-id $userPoolId --username admin --password ""YourSecurePassword123!"" --permanent" -ForegroundColor White
Write-Host ""
Write-Host "     (Replace the email and password with your own values)" -ForegroundColor DarkGray
Write-Host "  2. Sign in at the Website URL above and click Refresh to run the first end-to-end pipeline" -ForegroundColor Gray
Write-Host "  3. Optional: subscribe an email to the notifications topic to receive run summaries:" -ForegroundColor Gray
Write-Host "     aws sns subscribe --topic-arn $topicArn --protocol email --notification-endpoint you@example.com" -ForegroundColor White
Write-Host "  4. The weekly schedule runs the same pipeline automatically; Health events are polled hourly" -ForegroundColor Gray
if ($MultiAccount) {
    $hubAccount = aws sts get-caller-identity --query Account --output text --no-cli-pager
    Write-Host "`nMulti-account:" -ForegroundColor Yellow
    Write-Host "  Hub account $hubAccount scans every active account under $OrgTargets in $region" -ForegroundColor Gray
    if ($orgRollout) {
        Write-Host "  Spoke role: StackSet aws-services-lifecycle-tracker-spoke (auto-deploys to accounts joining later)" -ForegroundColor Gray
        Write-Host "     aws cloudformation list-stack-instances --stack-set-name aws-services-lifecycle-tracker-spoke --region $region" -ForegroundColor White
    } else {
        Write-Host "  ACTION REQUIRED: this account cannot run service-managed StackSets. From the MANAGEMENT account, run once:" -ForegroundColor Yellow
        Write-Host "     cd cdk; npx cdk deploy AWSServicesLifecycleTrackerOrg-$region --context orgTargets=$OrgTargets --context hubAccountId=$hubAccount --require-approval never" -ForegroundColor White
        Write-Host "     (or register this account as a StackSets delegated administrator, see the preflight output above, and re-run with -MultiAccount)" -ForegroundColor DarkGray
        Write-Host "  Until then, spoke accounts are reported as failed in each scan; the hub itself is scanned normally." -ForegroundColor Gray
    }
}
