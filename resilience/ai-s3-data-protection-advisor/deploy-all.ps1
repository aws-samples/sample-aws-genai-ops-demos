# S3 data protection advisor - complete deployment script
# Windows version - mirrors deploy-all.sh
#
# Stacks (in order): Data -> Auth -> Pipeline -> Api -> Frontend
# -AdminEmail: create the first dashboard user; Cognito emails a temporary password.
# -MockData: create the sandbox-only mock estate (tagged buckets and a role) to scan.
#   Remove it with .\destroy-all.ps1 -MockData.
param(
    [switch]$MockData,
    [string]$AdminEmail = ""
)

Set-Location $PSScriptRoot

Write-Host "=== S3 data protection advisor deployment ===" -ForegroundColor Cyan

Write-Host "`nRunning prerequisites check..." -ForegroundColor Yellow
& "..\..\shared\scripts\check-prerequisites.ps1" -RequiredService "bedrock" -MinPythonVersion "3.12" -RequireCDK -MinCdkVersion "2.1030.0"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Prerequisites check failed" -ForegroundColor Red
    exit 1
}

$region = $global:AWS_REGION
$dataStackName = "S3DataProtectionData-$region"
$authStackName = "S3DataProtectionAuth-$region"
$pipelineStackName = "S3DataProtectionPipeline-$region"
$apiStackName = "S3DataProtectionApi-$region"
$frontendStackName = "S3DataProtectionFrontend-$region"

function Get-StackOutput($stack, $key) {
    return (aws cloudformation describe-stacks --stack-name $stack --region $region --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" --output text --no-cli-pager)
}

Write-Host "`nInstalling CDK dependencies..." -ForegroundColor Yellow
Push-Location cdk
npm install
Pop-Location

# CDK synthesizes every stack, so frontend/dist must exist before the first deploy.
Write-Host "`nPreparing the dashboard build..." -ForegroundColor Yellow
if (Test-Path "frontend/package.json") {
    Push-Location frontend
    npm install
    if ($LASTEXITCODE -eq 0) { npm run build }
    $buildExit = $LASTEXITCODE
    Pop-Location
    if ($buildExit -ne 0) { Write-Host "Dashboard build failed" -ForegroundColor Red; exit 1 }
} else {
    Write-Host "      (No dashboard sources yet, writing a placeholder page)" -ForegroundColor Gray
    New-Item -ItemType Directory -Path "frontend/dist" -Force | Out-Null
    Set-Content -Path "frontend/dist/index.html" -Value '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>S3 data protection advisor</title></head><body><p>S3 data protection advisor: the dashboard is not built yet.</p></body></html>'
}

Write-Host "`nDeploying data stack..." -ForegroundColor Yellow
Write-Host "      (Reports bucket and runs table)" -ForegroundColor Gray
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $dataStackName
if ($LASTEXITCODE -ne 0) { Write-Host "Data stack deployment failed" -ForegroundColor Red; exit 1 }

Write-Host "`nDeploying authentication stack..." -ForegroundColor Yellow
Write-Host "      (Cognito user pool and app client)" -ForegroundColor Gray
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $authStackName -SkipBootstrap
if ($LASTEXITCODE -ne 0) { Write-Host "Auth stack deployment failed" -ForegroundColor Red; exit 1 }

Write-Host "`nDeploying pipeline stack..." -ForegroundColor Yellow
Write-Host "      (Durable scan function with a read-only role)" -ForegroundColor Gray
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $pipelineStackName -SkipBootstrap
if ($LASTEXITCODE -ne 0) { Write-Host "Pipeline stack deployment failed" -ForegroundColor Red; exit 1 }

Write-Host "`nDeploying API stack..." -ForegroundColor Yellow
Write-Host "      (HTTP API with a Cognito JWT authorizer)" -ForegroundColor Gray
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $apiStackName -SkipBootstrap
if ($LASTEXITCODE -ne 0) { Write-Host "API stack deployment failed" -ForegroundColor Red; exit 1 }

Write-Host "`nDeploying frontend stack..." -ForegroundColor Yellow
Write-Host "      (S3 and CloudFront, with /api forwarded to the HTTP API)" -ForegroundColor Gray
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $frontendStackName -SkipBootstrap
if ($LASTEXITCODE -ne 0) { Write-Host "Frontend stack deployment failed" -ForegroundColor Red; exit 1 }

$websiteUrl = Get-StackOutput $frontendStackName "WebsiteUrl"
$apiUrl = Get-StackOutput $apiStackName "ApiUrl"
$userPoolId = Get-StackOutput $authStackName "UserPoolId"
if (-not $userPoolId -or $userPoolId -eq "None") {
    Write-Host "Failed to get the user pool id from stack outputs" -ForegroundColor Red
    exit 1
}

if ($AdminEmail) {
    Write-Host "`nCreating the dashboard user $AdminEmail..." -ForegroundColor Yellow
    aws cognito-idp admin-create-user --user-pool-id $userPoolId --username $AdminEmail --user-attributes "Name=email,Value=$AdminEmail" "Name=email_verified,Value=true" --desired-delivery-mediums EMAIL --region $region --no-cli-pager *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "      Cognito emailed a temporary password; the first sign-in asks for a new one." -ForegroundColor Gray
    } else {
        Write-Host "      Note: the user was not created (it may already exist). Continuing." -ForegroundColor Yellow
    }
}

if ($MockData) {
    Write-Host "`nCreating the mock estate..." -ForegroundColor Yellow
    Write-Host "      (Sandbox accounts only: tagged buckets and a role, removed by destroy-all.ps1 -MockData)" -ForegroundColor Gray
    if (-not (Test-Path "backend/.venv")) { python -m venv backend/.venv }
    $venvPython = if (Test-Path "backend/.venv/Scripts/python.exe") { "backend/.venv/Scripts/python.exe" } else { "backend/.venv/bin/python" }
    $venvPython = (Resolve-Path $venvPython).Path
    & $venvPython -m pip install --quiet -r backend/requirements.txt
    Push-Location backend
    & $venvPython -m s3dp.cli seed --yes
    $seedExit = $LASTEXITCODE
    Pop-Location
    if ($seedExit -ne 0) { Write-Host "Seeding the mock estate failed" -ForegroundColor Red; exit 1 }
}

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  Deployment complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Dashboard URL:   $websiteUrl" -ForegroundColor Cyan
Write-Host "  API URL:         $apiUrl" -ForegroundColor Cyan
Write-Host "  Region:          $region" -ForegroundColor Cyan
Write-Host "  User pool ID:    $userPoolId" -ForegroundColor Cyan
Write-Host "`nNext steps:" -ForegroundColor Yellow
if (-not $AdminEmail) {
    Write-Host "  1. Create a dashboard user. Cognito emails a temporary password:" -ForegroundColor Gray
    Write-Host ""
    Write-Host "     aws cognito-idp admin-create-user --user-pool-id $userPoolId --username <email> --user-attributes Name=email,Value=<email> Name=email_verified,Value=true --desired-delivery-mediums EMAIL --region $region --no-cli-pager"
    Write-Host ""
} else {
    Write-Host "  1. Sign in as $AdminEmail with the temporary password from the email, then choose a new password." -ForegroundColor Gray
}
Write-Host "  2. Open the dashboard URL above and start a scan." -ForegroundColor Gray
Write-Host "  3. Verify the deployment end to end: python scripts/smoke_test.py --region $region" -ForegroundColor Gray
