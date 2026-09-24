# AWS Services Lifecycle Tracker - Terraform deployment (alternative to deploy-all.ps1)
# Usage: .\deploy-all-terraform.ps1
#
# Same result as the CDK path, single-account only (multi-account rollout is a
# CloudFormation StackSet and stays CDK-only, see deploy-all.ps1 -MultiAccount).
# No Docker needed: the Lambda bundle is built locally with pip (pure-Python
# dependencies resolved as Linux/arm64 wheels), then zipped by Terraform.
#
# Steps: stage backend -> terraform apply -> build frontend with the API URL and
# Cognito IDs -> upload to S3 + invalidate CloudFront.

$ErrorActionPreference = "Stop"

Write-Host "=== AWS Services Lifecycle Tracker Deployment (Terraform) ===" -ForegroundColor Cyan

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot     = (Resolve-Path "$ScriptDir/../..").Path
$TerraformDir = "$ScriptDir/terraform"
$StageDir     = "$TerraformDir/.backend-stage"
Set-Location $ScriptDir

# Run shared prerequisites check (AWS CLI, credentials, region, Python, Bedrock)
Write-Host "`nRunning prerequisites check..." -ForegroundColor Yellow
& "$repoRoot/shared/scripts/check-prerequisites.ps1" -RequiredService "bedrock" -MinAwsCliVersion "2.33.22" -MinPythonVersion "3.11"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Prerequisites check failed" -ForegroundColor Red
    exit 1
}

# Terraform + Node.js (frontend build) are specific to this path
if (-not (Get-Command terraform -ErrorAction SilentlyContinue)) {
    Write-Host "Terraform is not installed or not on PATH (https://developer.hashicorp.com/terraform/install)" -ForegroundColor Red
    exit 1
}
$tfVersion = (terraform version -json | ConvertFrom-Json).terraform_version
Write-Host "      Terraform: $tfVersion" -ForegroundColor Gray
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "Node.js/npm is required to build the frontend (https://nodejs.org)" -ForegroundColor Red
    exit 1
}

$region  = $global:AWS_REGION
$account = $global:AWS_ACCOUNT_ID
Write-Host "`nDeploying to region: $region (account: $account)" -ForegroundColor Yellow

# Step 1: Stage the Lambda code (mirrors cdk/lib/pipeline-stack.ts bundling)
Write-Host "`n[1/5] Staging Lambda code..." -ForegroundColor Yellow
Write-Host "      (backend/*.py + shared aws_utils.py + pip wheels for Linux/arm64, Python 3.14)" -ForegroundColor Gray
if (Test-Path $StageDir) { Remove-Item -Recurse -Force $StageDir }
New-Item -ItemType Directory -Path $StageDir | Out-Null
Copy-Item "$ScriptDir/backend/*.py" $StageDir
Copy-Item "$ScriptDir/backend/requirements.txt" $StageDir
# Repo-wide region/account helpers: the bundle cannot import from outside its
# own directory, so the shared file is copied in (one source of truth, no local copy).
Copy-Item "$repoRoot/shared/utils/aws_utils.py" $StageDir

python -m pip install --quiet --disable-pip-version-check --no-warn-conflicts `
    --platform manylinux2014_aarch64 --only-binary=:all: `
    --python-version 3.14 --implementation cp `
    --target $StageDir -r "$StageDir/requirements.txt"
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install of the Lambda dependencies failed (python with pip must be on PATH)" -ForegroundColor Red
    exit 1
}
Get-ChildItem -Path $StageDir -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Write-Host "      Staged $((Get-ChildItem $StageDir -Filter *.py).Count) Python files + dependencies" -ForegroundColor Gray

# Step 2: Frontend dependencies (the build itself needs the API URL, after apply)
Write-Host "`n[2/5] Installing frontend dependencies..." -ForegroundColor Yellow
Write-Host "      (Installing React, Vite, Cognito SDK, and UI component libraries)" -ForegroundColor Gray
Push-Location frontend
npm install
$npmExit = $LASTEXITCODE
Pop-Location
if ($npmExit -ne 0) {
    Write-Host "npm install failed" -ForegroundColor Red
    exit 1
}

# Step 3: terraform.tfvars
Write-Host "`n[3/5] Configuring Terraform..." -ForegroundColor Yellow
# Only the region is written; any other variable you set in terraform.tfvars
# (e.g. pipeline_role_name) is preserved across runs.
$tfvarsPath = "$TerraformDir/terraform.tfvars"
$tfvarsLines = @()
if (Test-Path $tfvarsPath) {
    $tfvarsLines = Get-Content $tfvarsPath | Where-Object { $_ -notmatch '^\s*region\s*=' }
}
$tfvarsLines = @("region = `"$region`"") + $tfvarsLines
[System.IO.File]::WriteAllLines($tfvarsPath, $tfvarsLines, (New-Object System.Text.UTF8Encoding($false)))
New-Item -ItemType Directory -Path "$TerraformDir/build" -Force | Out-Null
Write-Host "      terraform.tfvars written (region = $region)" -ForegroundColor Gray

# Step 4: terraform init + apply
Write-Host "`n[4/5] Running terraform apply..." -ForegroundColor Yellow
Write-Host "      (DynamoDB tables + config seed, Cognito, durable pipeline + API functions, HTTP API, S3 + CloudFront)" -ForegroundColor Gray
Push-Location $TerraformDir
terraform init -upgrade -input=false
if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Host "terraform init failed" -ForegroundColor Red; exit 1 }
terraform apply -auto-approve -input=false
if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Host "terraform apply failed. See errors above." -ForegroundColor Red; exit 1 }

$websiteUrl       = terraform output -raw website_url
$websiteBucket    = terraform output -raw website_bucket
$distributionId   = terraform output -raw distribution_id
$apiUrl           = terraform output -raw api_url
$userPoolId       = terraform output -raw user_pool_id
$userPoolClientId = terraform output -raw user_pool_client_id
$pipelineArn      = terraform output -raw pipeline_function_alias_arn
$topicArn         = terraform output -raw notification_topic_arn
Pop-Location

if ([string]::IsNullOrEmpty($apiUrl) -or [string]::IsNullOrEmpty($userPoolId) -or [string]::IsNullOrEmpty($userPoolClientId)) {
    Write-Host "Failed to read API URL / Cognito config from terraform outputs" -ForegroundColor Red
    exit 1
}
Write-Host "API URL: $apiUrl" -ForegroundColor Green
Write-Host "User Pool ID: $userPoolId" -ForegroundColor Green
Write-Host "User Pool Client ID: $userPoolClientId" -ForegroundColor Green

# Step 5: Build the frontend with the API URL and Cognito config, upload, invalidate
Write-Host "`n[5/5] Building and uploading frontend..." -ForegroundColor Yellow
& .\scripts\build-frontend.ps1 -UserPoolId $userPoolId -UserPoolClientId $userPoolClientId -ApiUrl $apiUrl -Region $region
if ($LASTEXITCODE -ne 0) {
    Write-Host "Frontend build failed" -ForegroundColor Red
    exit 1
}

aws s3 sync frontend/dist "s3://$websiteBucket" --delete --no-cli-pager
if ($LASTEXITCODE -ne 0) {
    Write-Host "Upload to S3 failed" -ForegroundColor Red
    exit 1
}
aws cloudfront create-invalidation --distribution-id $distributionId --paths "/*" --no-cli-pager | Out-Null
Write-Host "      Uploaded to s3://$websiteBucket and invalidated CloudFront" -ForegroundColor Gray

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
Write-Host "     aws cognito-idp admin-create-user --user-pool-id $userPoolId --username admin --user-attributes Name=email,Value=admin@example.com Name=email_verified,Value=true --message-action SUPPRESS" -ForegroundColor White
Write-Host ""
Write-Host "     aws cognito-idp admin-set-user-password --user-pool-id $userPoolId --username admin --password ""YourSecurePassword123!"" --permanent" -ForegroundColor White
Write-Host ""
Write-Host "     (Replace the email and password with your own values)" -ForegroundColor DarkGray
Write-Host "  2. Sign in at the Website URL above and click Refresh to run the first end-to-end pipeline" -ForegroundColor Gray
Write-Host "  3. Optional: subscribe an email to the notifications topic to receive run summaries:" -ForegroundColor Gray
Write-Host "     aws sns subscribe --topic-arn $topicArn --protocol email --notification-endpoint you@example.com" -ForegroundColor White
Write-Host "  4. The weekly schedule runs the same pipeline automatically" -ForegroundColor Gray
Write-Host "`nCleanup: cd terraform; terraform destroy -auto-approve   (removes the DynamoDB tables and their data too)" -ForegroundColor DarkGray
