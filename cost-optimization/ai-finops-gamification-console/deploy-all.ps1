# PowerShell deployment script for FinOps Gamification Console Demo
param(
    [switch]$DestroyInfra
)

$ErrorActionPreference = "Stop"

Write-Host "=== FinOps Gamification Console Deployment ===" -ForegroundColor Green
Write-Host ""

if ($DestroyInfra) {
    Write-Host "Destroying infrastructure..." -ForegroundColor Red
    
    # Use shared CDK destroy script
    & "$PSScriptRoot\..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "infrastructure/cdk" -DestroyStack
    
    Write-Host "Infrastructure destruction completed" -ForegroundColor Green
    exit 0
}

# Use shared prerequisites check (requires CDK)
Write-Host "Checking prerequisites..." -ForegroundColor Yellow
& "$PSScriptRoot\..\..\shared\scripts\check-prerequisites.ps1" -RequireCDK

if ($LASTEXITCODE -ne 0) {
    Write-Host "Prerequisites check failed" -ForegroundColor Red
    exit 1
}

# Get region from shared prerequisites
$Region = $global:AWS_REGION
$AccountId = $global:AWS_ACCOUNT_ID

Write-Host ""
Write-Host "Deployment Configuration:" -ForegroundColor Cyan
Write-Host "  Region:  $Region" -ForegroundColor White
Write-Host "  Account: $AccountId" -ForegroundColor White
Write-Host ""

# Deploy CDK infrastructure using shared script
Write-Host "Deploying AWS infrastructure (CDK)..." -ForegroundColor Yellow
& "$PSScriptRoot\..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "infrastructure/cdk"

if ($LASTEXITCODE -ne 0) {
    Write-Error "CDK deployment failed"
    exit 1
}

# Get CDK outputs
Write-Host ""
Write-Host "Getting CDK stack outputs..." -ForegroundColor Yellow

$stackName = "FinOpsGamificationConsole-$Region"
$outputs = aws cloudformation describe-stacks --stack-name $stackName --query "Stacks[0].Outputs" --output json --no-cli-pager 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "Warning: Could not retrieve stack outputs" -ForegroundColor Yellow
    Write-Host $outputs -ForegroundColor Gray
    exit 1
}

$outputsJson = $outputs | ConvertFrom-Json

# Parse outputs
$UserPoolId = ""
$UserPoolClientId = ""
$ApiEndpoint = ""
$WebsiteUrl = ""
$S3Bucket = ""
$CloudFrontId = ""
$SlackSecretArn = ""

foreach ($output in $outputsJson) {
    switch ($output.OutputKey) {
        "UserPoolId" { $UserPoolId = $output.OutputValue }
        "UserPoolClientId" { $UserPoolClientId = $output.OutputValue }
        "ApiEndpoint" { $ApiEndpoint = $output.OutputValue }
        "WebsiteUrl" { $WebsiteUrl = $output.OutputValue }
        "WebsiteBucketName" { $S3Bucket = $output.OutputValue }
        "CloudFrontDistributionId" { $CloudFrontId = $output.OutputValue }
        "SlackSecretArn" { $SlackSecretArn = $output.OutputValue }
    }
}

# Build and deploy frontend
Write-Host ""
Write-Host "Building React frontend..." -ForegroundColor Yellow

Push-Location frontend

# Install dependencies if needed
if (-not (Test-Path "node_modules")) {
    Write-Host "  Installing npm dependencies..." -ForegroundColor Gray
    npm install
}

# Create .env.production.local with deployment values
$envContent = @"
VITE_USER_POOL_ID=$UserPoolId
VITE_USER_POOL_CLIENT_ID=$UserPoolClientId
VITE_API_ENDPOINT=$ApiEndpoint
"@
$envContent | Out-File -FilePath ".env.production.local" -Encoding UTF8
Write-Host "  Generated .env.production.local with Cognito and API config" -ForegroundColor Green

# Build the frontend
Write-Host "  Running Vite build..." -ForegroundColor Gray
npm run build

if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Error "Frontend build failed"
    exit 1
}

Pop-Location

# Upload frontend to S3
if (-not [string]::IsNullOrEmpty($S3Bucket)) {
    Write-Host ""
    Write-Host "Uploading frontend to S3..." -ForegroundColor Yellow
    aws s3 sync frontend/dist/ "s3://$S3Bucket" --delete --no-cli-pager
    
    if ($LASTEXITCODE -ne 0) {
        Write-Error "S3 upload failed"
        exit 1
    }
    Write-Host "  Frontend uploaded to S3 bucket: $S3Bucket" -ForegroundColor Green
    
    # Invalidate CloudFront cache
    if (-not [string]::IsNullOrEmpty($CloudFrontId)) {
        Write-Host "  Invalidating CloudFront cache..." -ForegroundColor Gray
        aws cloudfront create-invalidation --distribution-id $CloudFrontId --paths "/*" --no-cli-pager | Out-Null
        Write-Host "  CloudFront cache invalidated" -ForegroundColor Green
    }
}

# Deployment summary
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Console URL:        $WebsiteUrl" -ForegroundColor Cyan
Write-Host "  API Endpoint:       $ApiEndpoint" -ForegroundColor Cyan
Write-Host "  Region:             $Region" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Cognito User Pool:  $UserPoolId" -ForegroundColor Gray
Write-Host "  User Pool Client:   $UserPoolClientId" -ForegroundColor Gray
Write-Host "  Slack Secret:       $SlackSecretArn" -ForegroundColor Gray
Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  Post-Deployment Steps:" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""
Write-Host "1. Create Cognito Users:" -ForegroundColor White
Write-Host "   aws cognito-idp admin-create-user \" -ForegroundColor Gray
Write-Host "     --user-pool-id $UserPoolId \" -ForegroundColor Gray
Write-Host "     --username admin@example.com \" -ForegroundColor Gray
Write-Host "     --user-attributes Name=email,Value=admin@example.com \" -ForegroundColor Gray
Write-Host "                        Name=given_name,Value=Admin \" -ForegroundColor Gray
Write-Host "                        Name=family_name,Value=User" -ForegroundColor Gray
Write-Host ""
Write-Host "2. Add Users to Groups:" -ForegroundColor White
Write-Host "   aws cognito-idp admin-add-user-to-group \" -ForegroundColor Gray
Write-Host "     --user-pool-id $UserPoolId \" -ForegroundColor Gray
Write-Host "     --username admin@example.com \" -ForegroundColor Gray
Write-Host "     --group-name finops-admin" -ForegroundColor Gray
Write-Host ""
Write-Host "3. Configure Slack Integration (Optional):" -ForegroundColor White
Write-Host "   Update the Slack secret with your bot token:" -ForegroundColor Gray
Write-Host "   aws secretsmanager put-secret-value \" -ForegroundColor Gray
Write-Host "     --secret-id `"$SlackSecretArn`" \" -ForegroundColor Gray
Write-Host "     --secret-string '{`"token`":`"xoxb-your-slack-bot-token`"}'" -ForegroundColor Gray
Write-Host ""
Write-Host "========================================" -ForegroundColor White
Write-Host ""
Write-Host "To destroy the infrastructure later, run:" -ForegroundColor Yellow
Write-Host "  .\deploy-all.ps1 -DestroyInfra" -ForegroundColor White
Write-Host ""
