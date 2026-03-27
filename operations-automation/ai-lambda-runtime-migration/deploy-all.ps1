# AI Lambda Runtime Migration Assistant - Deployment Script
$ErrorActionPreference = "Stop"

Write-Host "=== AI Lambda Runtime Migration Assistant ===" -ForegroundColor Cyan

# Check prerequisites using shared script
& "..\..\shared\scripts\check-prerequisites.ps1" -RequiredService "agentcore"

# Use region from shared prerequisites
$region = $global:AWS_REGION

# Set stack names with region suffix
$dataStackName = "LambdaRuntimeMigrationData-$region"
$authStackName = "LambdaRuntimeMigrationAuth-$region"
$runtimeStackName = "LambdaRuntimeMigrationRuntime-$region"
$frontendStackName = "LambdaRuntimeMigrationFrontend-$region"

    # Step 1: Install CDK dependencies
    Write-Host "`n[1/9] Installing CDK dependencies..." -ForegroundColor Yellow
    if (-not (Test-Path "cdk/node_modules")) {
        Push-Location cdk
        npm install
        Pop-Location
    } else {
        Write-Host "       CDK dependencies already installed" -ForegroundColor Gray
    }

    # Step 2: Install frontend dependencies
    Write-Host "`n[2/9] Installing frontend dependencies..." -ForegroundColor Yellow
    Push-Location frontend
    npm install
    Pop-Location

    # Step 3: Create placeholder frontend build
    Write-Host "`n[3/9] Creating placeholder frontend build..." -ForegroundColor Yellow
    if (-not (Test-Path "frontend/dist")) {
        New-Item -ItemType Directory -Path "frontend/dist" -Force | Out-Null
        echo "<!DOCTYPE html><html><body><h1>Building...</h1></body></html>" > frontend/dist/index.html
    }

    # Step 4: Package 3 agent zips
    Write-Host "`n[4/9] Packaging agent code (3 agents)..." -ForegroundColor Yellow
    & .\scripts\package-agent.ps1
    if ($LASTEXITCODE -ne 0) { Write-Host "Agent packaging failed" -ForegroundColor Red; exit 1 }

    # Step 5: Bootstrap CDK and deploy Data stack
    Write-Host "`n[5/9] Bootstrapping CDK and deploying Data stack..." -ForegroundColor Yellow
    Push-Location cdk
    $timestamp = Get-Date -Format "yyyyMMddHHmmss"
    npx cdk bootstrap --output "cdk.out.$timestamp" --no-cli-pager
    Pop-Location
    if ($LASTEXITCODE -ne 0) { Write-Host "CDK bootstrap failed" -ForegroundColor Red; exit 1 }

    Push-Location cdk
    $timestamp = Get-Date -Format "yyyyMMddHHmmss"
    npx cdk deploy $dataStackName --output "cdk.out.$timestamp" --no-cli-pager --require-approval never
    Pop-Location
    if ($LASTEXITCODE -ne 0) { Write-Host "Data stack deployment failed" -ForegroundColor Red; exit 1 }

    # Upload 3 agent zips to S3
    Write-Host "`nUploading agent zips to S3..." -ForegroundColor Yellow
    $bucketName = aws cloudformation describe-stacks --stack-name $dataStackName --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text --no-cli-pager
    $agents = @("discover", "analyze", "transform")
    foreach ($agent in $agents) {
        $zipPath = "agent/$agent/deployment_package.zip"
        aws s3 cp $zipPath s3://$bucketName/agent/$agent/deployment_package.zip --no-cli-pager
        if ($LASTEXITCODE -ne 0) { Write-Host "Upload failed for $agent" -ForegroundColor Red; exit 1 }
        Write-Host "       Uploaded $agent agent zip" -ForegroundColor Green
    }

    # Step 6: Deploy Auth stack
    Write-Host "`n[6/9] Deploying Auth stack (Cognito)..." -ForegroundColor Yellow
    Push-Location cdk
    $timestamp = Get-Date -Format "yyyyMMddHHmmss"
    npx cdk deploy $authStackName --output "cdk.out.$timestamp" --no-cli-pager --require-approval never
    Pop-Location
    if ($LASTEXITCODE -ne 0) { Write-Host "Auth stack deployment failed" -ForegroundColor Red; exit 1 }

    # Step 7: Deploy Runtime stack (3 AgentCore Runtimes)
    Write-Host "`n[7/9] Deploying Runtime stack (3 AgentCore Runtimes)..." -ForegroundColor Yellow
    Push-Location cdk
    $timestamp = Get-Date -Format "yyyyMMddHHmmss"
    npx cdk deploy $runtimeStackName --output "cdk.out.$timestamp" --no-cli-pager --require-approval never
    Pop-Location
    if ($LASTEXITCODE -ne 0) { Write-Host "Runtime stack deployment failed" -ForegroundColor Red; exit 1 }

    # Step 8: Build frontend with runtime ARNs
    Write-Host "`n[8/9] Building frontend with stack outputs..." -ForegroundColor Yellow
    $userPoolId = aws cloudformation describe-stacks --stack-name $authStackName --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text --no-cli-pager
    $userPoolClientId = aws cloudformation describe-stacks --stack-name $authStackName --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" --output text --no-cli-pager
    $identityPoolId = aws cloudformation describe-stacks --stack-name $authStackName --query "Stacks[0].Outputs[?OutputKey=='IdentityPoolId'].OutputValue" --output text --no-cli-pager
    $discoverArn = aws cloudformation describe-stacks --stack-name $runtimeStackName --query "Stacks[0].Outputs[?OutputKey=='DiscoverRuntimeArn'].OutputValue" --output text --no-cli-pager
    $analyzeArn = aws cloudformation describe-stacks --stack-name $runtimeStackName --query "Stacks[0].Outputs[?OutputKey=='AnalyzeRuntimeArn'].OutputValue" --output text --no-cli-pager
    $transformArn = aws cloudformation describe-stacks --stack-name $runtimeStackName --query "Stacks[0].Outputs[?OutputKey=='TransformRuntimeArn'].OutputValue" --output text --no-cli-pager

    & .\scripts\build-frontend.ps1 -Region $region -UserPoolId $userPoolId -UserPoolClientId $userPoolClientId -IdentityPoolId $identityPoolId -DiscoverRuntimeArn $discoverArn -AnalyzeRuntimeArn $analyzeArn -TransformRuntimeArn $transformArn
    if ($LASTEXITCODE -ne 0) { Write-Host "Frontend build failed" -ForegroundColor Red; exit 1 }

    # Step 9: Deploy Frontend stack (CloudFront)
    Write-Host "`n[9/9] Deploying Frontend stack (CloudFront)..." -ForegroundColor Yellow
    Push-Location cdk
    $timestamp = Get-Date -Format "yyyyMMddHHmmss"
    npx cdk deploy $frontendStackName --output "cdk.out.$timestamp" --no-cli-pager --require-approval never
    Pop-Location
    if ($LASTEXITCODE -ne 0) { Write-Host "Frontend stack deployment failed" -ForegroundColor Red; exit 1 }

# Retrieve stack outputs

$websiteUrl = aws cloudformation describe-stacks --stack-name $frontendStackName --query "Stacks[0].Outputs[?OutputKey=='WebsiteUrl'].OutputValue" --output text --no-cli-pager
$bucketName = aws cloudformation describe-stacks --stack-name $dataStackName --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text --no-cli-pager
$tableName = aws cloudformation describe-stacks --stack-name $dataStackName --query "Stacks[0].Outputs[?OutputKey=='TableName'].OutputValue" --output text --no-cli-pager
$userPoolId = aws cloudformation describe-stacks --stack-name $authStackName --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text --no-cli-pager

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Dashboard URL:   $websiteUrl" -ForegroundColor Cyan
Write-Host "  S3 Bucket:       $bucketName" -ForegroundColor Cyan
Write-Host "  DynamoDB Table:  $tableName" -ForegroundColor Cyan
Write-Host "  Region:          $region" -ForegroundColor Cyan
Write-Host ""
Write-Host "  To create a dashboard user:" -ForegroundColor Yellow
Write-Host "  aws cognito-idp admin-create-user --user-pool-id $userPoolId --username your.email@example.com --user-attributes Name=email,Value=your.email@example.com --message-action SUPPRESS --no-cli-pager" -ForegroundColor Gray
Write-Host ""
Write-Host "  Then set a permanent password:" -ForegroundColor Yellow
Write-Host "  aws cognito-idp admin-set-user-password --user-pool-id $userPoolId --username your.email@example.com --password YourPass123! --permanent --no-cli-pager" -ForegroundColor Gray
Write-Host ""
