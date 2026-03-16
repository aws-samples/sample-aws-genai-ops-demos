# Password Reset Chatbot - Complete Deployment Script

Write-Host "=== Password Reset Chatbot Deployment ===" -ForegroundColor Cyan

# Check prerequisites using shared script
& "..\..\shared\scripts\check-prerequisites.ps1" -RequiredService "agentcore" -MinAwsCliVersion "2.31.13" -RequireCDK

# Use region from shared prerequisites
$region = $global:AWS_REGION

# Set stack names with region suffix for multi-region support
$infraStackName = "PasswordResetInfra-$region"
$authStackName = "PasswordResetAuth-$region"
$runtimeStackName = "PasswordResetRuntime-$region"
$frontendStackName = "PasswordResetFrontend-$region"

# Step 1: Install CDK dependencies
Write-Host "`n[1/7] Installing CDK dependencies..." -ForegroundColor Yellow
if (-not (Test-Path "cdk/node_modules")) {
    Push-Location cdk
    npm install
    Pop-Location
} else {
    Write-Host "      CDK dependencies already installed" -ForegroundColor Gray
}

# Step 2: Install frontend dependencies
Write-Host "`n[2/7] Installing frontend dependencies..." -ForegroundColor Yellow
Push-Location frontend
npm install
Pop-Location

# Step 3: Create placeholder frontend build
Write-Host "`n[3/7] Creating placeholder frontend build..." -ForegroundColor Yellow
if (-not (Test-Path "frontend/dist")) {
    New-Item -ItemType Directory -Path "frontend/dist" -Force | Out-Null
    echo "<!DOCTYPE html><html><body><h1>Building...</h1></body></html>" > frontend/dist/index.html
}

# Step 4: Bootstrap CDK
Write-Host "`n[4/7] Bootstrapping CDK environment..." -ForegroundColor Yellow
Push-Location cdk
$timestamp = Get-Date -Format "yyyyMMddHHmmss"
npx cdk bootstrap --output "cdk.out.$timestamp" --no-cli-pager
Pop-Location
if ($LASTEXITCODE -ne 0) { Write-Host "CDK bootstrap failed" -ForegroundColor Red; exit 1 }

# Step 5: Deploy infrastructure stack
Write-Host "`n[5/7] Deploying infrastructure stack..." -ForegroundColor Yellow
Push-Location cdk
$timestamp = Get-Date -Format "yyyyMMddHHmmss"
npx cdk deploy $infraStackName --output "cdk.out.$timestamp" --no-cli-pager --require-approval never
Pop-Location
if ($LASTEXITCODE -ne 0) { Write-Host "Infrastructure deployment failed" -ForegroundColor Red; exit 1 }

# Step 6: Deploy auth stack
Write-Host "`n[6/7] Deploying authentication stack (Cognito User Pool)..." -ForegroundColor Yellow
Push-Location cdk
$timestamp = Get-Date -Format "yyyyMMddHHmmss"
npx cdk deploy $authStackName --output "cdk.out.$timestamp" --no-cli-pager --require-approval never
Pop-Location
if ($LASTEXITCODE -ne 0) { Write-Host "Auth deployment failed" -ForegroundColor Red; exit 1 }

# Step 7: Deploy runtime stack
Write-Host "`n[7/7] Deploying AgentCore runtime (anonymous access)..." -ForegroundColor Yellow
Write-Host "      Note: CodeBuild will compile the container - this takes 5-10 minutes" -ForegroundColor DarkGray
Push-Location cdk
$timestamp = Get-Date -Format "yyyyMMddHHmmss"
npx cdk deploy $runtimeStackName --output "cdk.out.$timestamp" --no-cli-pager --require-approval never
Pop-Location
if ($LASTEXITCODE -ne 0) { Write-Host "Runtime deployment failed" -ForegroundColor Red; exit 1 }


# Build and deploy frontend
Write-Host "`nBuilding and deploying frontend..." -ForegroundColor Yellow
$agentRuntimeArn = aws cloudformation describe-stacks --stack-name $runtimeStackName --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager
$region = aws cloudformation describe-stacks --stack-name $runtimeStackName --query "Stacks[0].Outputs[?OutputKey=='Region'].OutputValue" --output text --no-cli-pager
$identityPoolId = aws cloudformation describe-stacks --stack-name $authStackName --query "Stacks[0].Outputs[?OutputKey=='IdentityPoolId'].OutputValue" --output text --no-cli-pager
$unauthRoleArn = aws cloudformation describe-stacks --stack-name $authStackName --query "Stacks[0].Outputs[?OutputKey=='UnauthenticatedRoleArn'].OutputValue" --output text --no-cli-pager

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
npx cdk deploy $frontendStackName --output "cdk.out.$timestamp" --no-cli-pager --require-approval never
Pop-Location
if ($LASTEXITCODE -ne 0) { Write-Host "Frontend deployment failed" -ForegroundColor Red; exit 1 }

# Get outputs
$websiteUrl = aws cloudformation describe-stacks --stack-name $frontendStackName --query "Stacks[0].Outputs[?OutputKey=='WebsiteUrl'].OutputValue" --output text --no-cli-pager
$userPoolId = aws cloudformation describe-stacks --stack-name $authStackName --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text --no-cli-pager

Write-Host "`n=== Deployment Complete ===" -ForegroundColor Green
Write-Host "Website URL: $websiteUrl" -ForegroundColor Cyan
Write-Host "Agent Runtime ARN: $agentRuntimeArn" -ForegroundColor Cyan
Write-Host "User Pool ID: $userPoolId" -ForegroundColor Cyan
Write-Host "`nNOTE: This chatbot allows ANONYMOUS access (no login required)" -ForegroundColor Yellow
Write-Host "Users can reset passwords for accounts in the Cognito User Pool" -ForegroundColor Yellow
Write-Host "`nTo test the password reset flow:" -ForegroundColor Cyan
Write-Host "1. Create a test user with an email address you can access:" -ForegroundColor Gray
Write-Host "   aws cognito-idp admin-create-user --user-pool-id $userPoolId --username your.email@example.com --user-attributes Name=email,Value=your.email@example.com --message-action SUPPRESS" -ForegroundColor Gray
Write-Host "`n2. Set a permanent password (REQUIRED - users with FORCE_CHANGE_PASSWORD status cannot receive reset emails):" -ForegroundColor Gray
Write-Host "   aws cognito-idp admin-set-user-password --user-pool-id $userPoolId --username your.email@example.com --password TempPass123! --permanent" -ForegroundColor Gray
Write-Host "`n3. Open the website and initiate a password reset" -ForegroundColor Gray
Write-Host "`nCRITICAL: Step 2 is mandatory. Cognito will not send password reset emails to users in FORCE_CHANGE_PASSWORD status." -ForegroundColor Red
