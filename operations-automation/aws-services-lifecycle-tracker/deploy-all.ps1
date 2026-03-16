# AWS Services Lifecycle Tracker - Complete Deployment Script

Write-Host "=== AWS Services Lifecycle Tracker Deployment ===" -ForegroundColor Cyan

# Run shared prerequisites check
Write-Host "`nRunning prerequisites check..." -ForegroundColor Yellow
& "..\..\shared\scripts\check-prerequisites.ps1" -RequiredService "agentcore" -MinAwsCliVersion "2.31.13" -RequireCDK

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

# Deploy infrastructure stack
Write-Host "`nDeploying infrastructure stack..." -ForegroundColor Yellow
Write-Host "      (Creating ECR repository, CodeBuild project, S3 bucket for agent builds, and IAM roles)" -ForegroundColor Gray
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName "AWSServicesLifecycleTrackerInfra-$region"

if ($LASTEXITCODE -ne 0) {
    Write-Host "Infrastructure deployment failed" -ForegroundColor Red
    exit 1
}

# Deploy data stack
Write-Host "`nDeploying data stack..." -ForegroundColor Yellow
Write-Host "      (Creating DynamoDB tables and populating service configurations for 7 AWS services)" -ForegroundColor Gray
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName "AWSServicesLifecycleTrackerData-$region" -SkipBootstrap

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

# Deploy runtime stack (triggers build and waits via Lambda)
Write-Host "`nDeploying AgentCore runtime stack..." -ForegroundColor Yellow
Write-Host "      (Uploading agent code, building ARM64 Docker image via CodeBuild, creating AgentCore runtime)" -ForegroundColor Gray
Write-Host "      Note: CodeBuild will compile the container image - this takes 5-10 minutes" -ForegroundColor DarkGray
Write-Host "      The deployment will pause while waiting for the build to complete..." -ForegroundColor DarkGray

$deployOutput = & "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName "AWSServicesLifecycleTrackerRuntime-$region" -SkipBootstrap 2>&1 | Tee-Object -Variable cdkOutput

if ($LASTEXITCODE -ne 0) {
    # Check if the error is about unrecognized resource type
    if ($cdkOutput -match "Unrecognized resource types.*BedrockAgentCore") {
        Write-Host "`n❌ DEPLOYMENT FAILED: AgentCore is not available in region '$region'" -ForegroundColor Red
        Write-Host ""
        Write-Host "Please verify AgentCore availability in your target region:" -ForegroundColor Yellow
        Write-Host "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "To deploy to a supported region, configure your AWS CLI:" -ForegroundColor Yellow
        Write-Host "  aws configure set region <your-supported-region>" -ForegroundColor Gray
        Write-Host "  .\deploy-all.ps1" -ForegroundColor Gray
        exit 1
    }
    # Re-throw other errors
    Write-Host "Backend deployment failed" -ForegroundColor Red
    exit 1
}

# Build and deploy frontend (after backend is complete)
Write-Host "`nBuilding and deploying frontend..." -ForegroundColor Yellow
Write-Host "      (Retrieving AgentCore Runtime ID and Cognito config, building React app, deploying to S3 + CloudFront)" -ForegroundColor Gray
$agentRuntimeArn = aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerRuntime-$region" --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager
$outputRegion = aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerRuntime-$region" --query "Stacks[0].Outputs[?OutputKey=='Region'].OutputValue" --output text --no-cli-pager
$userPoolId = aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerAuth-$region" --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text --no-cli-pager
$userPoolClientId = aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerAuth-$region" --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" --output text --no-cli-pager
$identityPoolId = aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerAuth-$region" --query "Stacks[0].Outputs[?OutputKey=='IdentityPoolId'].OutputValue" --output text --no-cli-pager

if ([string]::IsNullOrEmpty($agentRuntimeArn)) {
    Write-Host "Failed to get Agent Runtime ARN from stack outputs" -ForegroundColor Red
    exit 1
}

if ([string]::IsNullOrEmpty($outputRegion)) {
    Write-Host "Failed to get Region from stack outputs" -ForegroundColor Red
    exit 1
}

if ([string]::IsNullOrEmpty($userPoolId) -or [string]::IsNullOrEmpty($userPoolClientId) -or [string]::IsNullOrEmpty($identityPoolId)) {
    Write-Host "Failed to get Cognito config from stack outputs" -ForegroundColor Red
    exit 1
}

Write-Host "Agent Runtime ARN: $agentRuntimeArn" -ForegroundColor Green
Write-Host "Region: $outputRegion" -ForegroundColor Green
Write-Host "User Pool ID: $userPoolId" -ForegroundColor Green
Write-Host "User Pool Client ID: $userPoolClientId" -ForegroundColor Green
Write-Host "Identity Pool ID: $identityPoolId" -ForegroundColor Green

# Build frontend with AgentCore Runtime ARN and Cognito config
& .\scripts\build-frontend.ps1 -UserPoolId $userPoolId -UserPoolClientId $userPoolClientId -IdentityPoolId $identityPoolId -AgentRuntimeArn $agentRuntimeArn -Region $outputRegion

if ($LASTEXITCODE -ne 0) {
    Write-Host "Frontend build failed" -ForegroundColor Red
    exit 1
}

# Deploy scheduler stack (optional but recommended for automated extractions)
Write-Host "`nDeploying scheduler stack..." -ForegroundColor Yellow
Write-Host "      (Creating EventBridge rules for automated weekly/monthly extractions)" -ForegroundColor Gray
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName "AWSServicesLifecycleTrackerScheduler-$region" -SkipBootstrap

if ($LASTEXITCODE -ne 0) {
    Write-Host "Scheduler deployment failed" -ForegroundColor Red
    exit 1
}

# Deploy frontend stack
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName "AWSServicesLifecycleTrackerFrontend-$region" -SkipBootstrap

if ($LASTEXITCODE -ne 0) {
    Write-Host "Frontend deployment failed" -ForegroundColor Red
    exit 1
}

# Get CloudFront URL
$websiteUrl = aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerFrontend-$region" --query "Stacks[0].Outputs[?OutputKey=='WebsiteUrl'].OutputValue" --output text --no-cli-pager

Write-Host "`n=== Deployment Complete ===" -ForegroundColor Green
Write-Host "Website URL: $websiteUrl" -ForegroundColor Cyan
Write-Host "Agent Runtime ARN: $agentRuntimeArn" -ForegroundColor Cyan
Write-Host "Region: $region" -ForegroundColor Cyan
Write-Host "User Pool ID: $userPoolId" -ForegroundColor Cyan
Write-Host "User Pool Client ID: $userPoolClientId" -ForegroundColor Cyan
Write-Host "`nNext Steps:" -ForegroundColor Yellow
Write-Host "  1. Create an admin user (copy-paste these two commands):" -ForegroundColor Gray
Write-Host ""
Write-Host "     aws cognito-idp admin-create-user --user-pool-id $userPoolId --username admin --user-attributes Name=email,Value=admin@company.com Name=email_verified,Value=true --message-action SUPPRESS" -ForegroundColor White
Write-Host ""
Write-Host "     aws cognito-idp admin-set-user-password --user-pool-id $userPoolId --username admin --password ""YourSecurePassword123!"" --permanent" -ForegroundColor White
Write-Host ""
Write-Host "     (Replace the email and password with your own values)" -ForegroundColor DarkGray
Write-Host "  2. Sign in at the Website URL above with your created admin credentials" -ForegroundColor Gray
Write-Host "  3. Initial extraction is running in background - results will appear in DynamoDB" -ForegroundColor Gray
Write-Host "  4. View extracted data via admin UI or query DynamoDB table aws-services-lifecycle" -ForegroundColor Gray
