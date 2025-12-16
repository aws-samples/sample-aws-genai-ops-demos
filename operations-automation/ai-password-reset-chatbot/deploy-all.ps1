# Password Reset Chatbot - Complete Deployment Script

Write-Host "=== Password Reset Chatbot Deployment ===" -ForegroundColor Cyan

# Step 1: Verify AWS credentials
Write-Host "`n[1/10] Verifying AWS credentials..." -ForegroundColor Yellow
$callerIdentity = aws sts get-caller-identity 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "AWS credentials are not configured or have expired" -ForegroundColor Red
    Write-Host "Please configure AWS credentials using: aws configure" -ForegroundColor Yellow
    exit 1
}
$accountId = ($callerIdentity | ConvertFrom-Json).Account
$arn = ($callerIdentity | ConvertFrom-Json).Arn
Write-Host "      Authenticated as: $arn" -ForegroundColor Green

# Step 2: Check AWS CLI version
Write-Host "`n[2/10] Checking AWS CLI version..." -ForegroundColor Yellow
$awsVersion = aws --version 2>&1
$versionMatch = $awsVersion -match 'aws-cli/(\d+)\.(\d+)\.(\d+)'
if ($versionMatch) {
    $major = [int]$Matches[1]; $minor = [int]$Matches[2]; $patch = [int]$Matches[3]
    $isVersionValid = ($major -gt 2) -or ($major -eq 2 -and $minor -gt 31) -or ($major -eq 2 -and $minor -eq 31 -and $patch -ge 13)
    if (-not $isVersionValid) {
        Write-Host "      AWS CLI version 2.31.13+ required for AgentCore" -ForegroundColor Red
        exit 1
    }
    Write-Host "      AWS CLI version is compatible" -ForegroundColor Green
}

# Step 3: Check AgentCore availability
Write-Host "`n[3/10] Checking AgentCore availability..." -ForegroundColor Yellow
$currentRegion = aws configure get region
if ([string]::IsNullOrEmpty($currentRegion)) {
    Write-Host "      No AWS region configured. Run: aws configure set region <region>" -ForegroundColor Red
    exit 1
}
Write-Host "      Target region: $currentRegion" -ForegroundColor Gray
$agentCoreCheck = aws bedrock-agentcore-control list-agent-runtimes --region $currentRegion --max-results 1 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "      AgentCore is not available in region: $currentRegion" -ForegroundColor Red
    exit 1
}
Write-Host "      AgentCore is available" -ForegroundColor Green


# Step 4: Install CDK dependencies
Write-Host "`n[4/10] Installing CDK dependencies..." -ForegroundColor Yellow
if (-not (Test-Path "cdk/node_modules")) {
    Push-Location cdk
    npm install
    Pop-Location
} else {
    Write-Host "      CDK dependencies already installed" -ForegroundColor Gray
}

# Step 5: Install frontend dependencies
Write-Host "`n[5/10] Installing frontend dependencies..." -ForegroundColor Yellow
Push-Location frontend
npm install
Pop-Location

# Step 6: Create placeholder frontend build
Write-Host "`n[6/10] Creating placeholder frontend build..." -ForegroundColor Yellow
if (-not (Test-Path "frontend/dist")) {
    New-Item -ItemType Directory -Path "frontend/dist" -Force | Out-Null
    echo "<!DOCTYPE html><html><body><h1>Building...</h1></body></html>" > frontend/dist/index.html
}

# Step 7: Bootstrap CDK
Write-Host "`n[7/10] Bootstrapping CDK environment..." -ForegroundColor Yellow
Push-Location cdk
$timestamp = Get-Date -Format "yyyyMMddHHmmss"
npx cdk bootstrap --output "cdk.out.$timestamp" --no-cli-pager
Pop-Location
if ($LASTEXITCODE -ne 0) { Write-Host "CDK bootstrap failed" -ForegroundColor Red; exit 1 }

# Step 8: Deploy infrastructure stack
Write-Host "`n[8/10] Deploying infrastructure stack..." -ForegroundColor Yellow
Push-Location cdk
$timestamp = Get-Date -Format "yyyyMMddHHmmss"
npx cdk deploy PasswordResetInfra --output "cdk.out.$timestamp" --no-cli-pager --require-approval never
Pop-Location
if ($LASTEXITCODE -ne 0) { Write-Host "Infrastructure deployment failed" -ForegroundColor Red; exit 1 }

# Step 9: Deploy auth stack
Write-Host "`n[9/10] Deploying authentication stack (Cognito User Pool)..." -ForegroundColor Yellow
Push-Location cdk
$timestamp = Get-Date -Format "yyyyMMddHHmmss"
npx cdk deploy PasswordResetAuth --output "cdk.out.$timestamp" --no-cli-pager --require-approval never
Pop-Location
if ($LASTEXITCODE -ne 0) { Write-Host "Auth deployment failed" -ForegroundColor Red; exit 1 }

# Step 10: Deploy runtime stack
Write-Host "`n[10/10] Deploying AgentCore runtime (anonymous access)..." -ForegroundColor Yellow
Write-Host "      Note: CodeBuild will compile the container - this takes 5-10 minutes" -ForegroundColor DarkGray
Push-Location cdk
$timestamp = Get-Date -Format "yyyyMMddHHmmss"
npx cdk deploy PasswordResetRuntime --output "cdk.out.$timestamp" --no-cli-pager --require-approval never
Pop-Location
if ($LASTEXITCODE -ne 0) { Write-Host "Runtime deployment failed" -ForegroundColor Red; exit 1 }


# Build and deploy frontend
Write-Host "`nBuilding and deploying frontend..." -ForegroundColor Yellow
$agentRuntimeArn = aws cloudformation describe-stacks --stack-name PasswordResetRuntime --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager
$region = aws cloudformation describe-stacks --stack-name PasswordResetRuntime --query "Stacks[0].Outputs[?OutputKey=='Region'].OutputValue" --output text --no-cli-pager
$identityPoolId = aws cloudformation describe-stacks --stack-name PasswordResetAuth --query "Stacks[0].Outputs[?OutputKey=='IdentityPoolId'].OutputValue" --output text --no-cli-pager
$unauthRoleArn = aws cloudformation describe-stacks --stack-name PasswordResetAuth --query "Stacks[0].Outputs[?OutputKey=='UnauthenticatedRoleArn'].OutputValue" --output text --no-cli-pager

if ([string]::IsNullOrEmpty($agentRuntimeArn) -or [string]::IsNullOrEmpty($region) -or [string]::IsNullOrEmpty($identityPoolId) -or [string]::IsNullOrEmpty($unauthRoleArn)) {
    Write-Host "Failed to get stack outputs" -ForegroundColor Red
    exit 1
}

Write-Host "Agent Runtime ARN: $agentRuntimeArn" -ForegroundColor Green
Write-Host "Region: $region" -ForegroundColor Green
Write-Host "Identity Pool ID: $identityPoolId" -ForegroundColor Green
Write-Host "Unauth Role ARN: $unauthRoleArn" -ForegroundColor Green

# Build frontend with basic auth flow (bypasses session policy restrictions)
& .\scripts\build-frontend.ps1 -AgentRuntimeArn $agentRuntimeArn -Region $region -IdentityPoolId $identityPoolId -UnauthRoleArn $unauthRoleArn
if ($LASTEXITCODE -ne 0) { Write-Host "Frontend build failed" -ForegroundColor Red; exit 1 }

# Deploy frontend stack
Push-Location cdk
$timestamp = Get-Date -Format "yyyyMMddHHmmss"
npx cdk deploy PasswordResetFrontend --output "cdk.out.$timestamp" --no-cli-pager --require-approval never
Pop-Location
if ($LASTEXITCODE -ne 0) { Write-Host "Frontend deployment failed" -ForegroundColor Red; exit 1 }

# Get outputs
$websiteUrl = aws cloudformation describe-stacks --stack-name PasswordResetFrontend --query "Stacks[0].Outputs[?OutputKey=='WebsiteUrl'].OutputValue" --output text --no-cli-pager
$userPoolId = aws cloudformation describe-stacks --stack-name PasswordResetAuth --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text --no-cli-pager

Write-Host "`n=== Deployment Complete ===" -ForegroundColor Green
Write-Host "Website URL: $websiteUrl" -ForegroundColor Cyan
Write-Host "Agent Runtime ARN: $agentRuntimeArn" -ForegroundColor Cyan
Write-Host "User Pool ID: $userPoolId" -ForegroundColor Cyan
Write-Host "`nNOTE: This chatbot allows ANONYMOUS access (no login required)" -ForegroundColor Yellow
Write-Host "Users can reset passwords for accounts in the Cognito User Pool" -ForegroundColor Yellow
Write-Host "`nTo test, create a user in Cognito first:" -ForegroundColor Gray
Write-Host "  aws cognito-idp admin-create-user --user-pool-id $userPoolId --username test@example.com --temporary-password TempPass1!" -ForegroundColor Gray
