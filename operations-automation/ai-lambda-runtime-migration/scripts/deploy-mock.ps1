# AI Lambda Runtime Migration Assistant - Mock Fixture Deploy/Destroy Script
#
# Opt-in, self-contained test fixture: a CodeCommit repository plus three
# deprecated-runtime Lambda functions (.NET / Node.js / Python), each tagged so
# the demo's code-behind detection resolves the function to that repository.
#
# This is NOT part of deploy-all. It deploys ONLY the mock stack
# (LambdaRuntimeMigrationMock-<region>), gated behind the `mockFixture` CDK
# context, through the SHARED deploy-cdk script (never `cdk deploy` directly).
#
#   .\scripts\deploy-mock.ps1            # deploy the mock fixture
#   .\scripts\deploy-mock.ps1 -Destroy   # tear it down
#
# Note: Trusted Advisor's deprecated-runtime detection has a lag, so the mock
# functions may take time (often hours) to appear in the dashboard inventory.
param(
    [switch]$Destroy
)

# Run from the demo root so shared-script relative paths and frontend/dist
# resolve exactly as deploy-all.ps1 sees them (..\..\shared\...).
Set-Location (Join-Path $PSScriptRoot '..')

Write-Host "=== AI Lambda Runtime Migration - Mock Fixture ===" -ForegroundColor Cyan

# Run shared prerequisites check (same signature as deploy-all.ps1)
Write-Host "`nRunning prerequisites check..." -ForegroundColor Yellow
& "..\..\shared\scripts\check-prerequisites.ps1" -MinAwsCliVersion "2.31.13" -MinPythonVersion "3.12" -RequireCDK -MinCdkVersion "2.1030.0"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Prerequisites check failed" -ForegroundColor Red
    exit 1
}

# Create placeholder dist BEFORE any CDK commands. CDK synthesizes ALL stacks in
# the app even when targeting one, and FrontendStack references frontend/dist,
# so it must exist or synth fails on a missing asset.
Write-Host "`nEnsuring placeholder frontend build exists..." -ForegroundColor Yellow
if (-not (Test-Path "frontend/dist")) {
    New-Item -ItemType Directory -Path "frontend/dist" -Force | Out-Null
    echo "<!DOCTYPE html><html><body><h1>Building...</h1></body></html>" > frontend/dist/index.html
} else {
    Write-Host "      Placeholder already exists, skipping..." -ForegroundColor Gray
}

# Region + stack name
$region = $global:AWS_REGION
$stackName = "LambdaRuntimeMigrationMock-$region"

if ($Destroy) {
    Write-Host "`nDestroying mock fixture stack..." -ForegroundColor Yellow
    Write-Host "      (Removing the CodeCommit repo and the three deprecated-runtime Lambda functions)" -ForegroundColor Gray
    # Pass the same mockFixture=true context so the stack synthesizes during destroy.
    & "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $stackName -SkipBootstrap -DestroyStack -CdkContext "mockFixture=true"
    if ($LASTEXITCODE -ne 0) { Write-Host "Mock fixture destroy failed" -ForegroundColor Red; exit 1 }

    Write-Host "`n========================================" -ForegroundColor Green
    Write-Host "  Mock Fixture Destroyed" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Stack:  $stackName" -ForegroundColor Cyan
    Write-Host "  Region: $region" -ForegroundColor Cyan
    exit 0
}

# Deploy ONLY the mock stack via the SHARED script (never `cdk deploy` directly).
Write-Host "`nDeploying mock fixture stack..." -ForegroundColor Yellow
Write-Host "      (Creating the CodeCommit repo and the three deprecated-runtime Lambda functions)" -ForegroundColor Gray
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $stackName -SkipBootstrap -CdkContext "mockFixture=true"
if ($LASTEXITCODE -ne 0) { Write-Host "Mock fixture deployment failed" -ForegroundColor Red; exit 1 }

# Read back outputs for a user-friendly summary.
$repoUrl = aws cloudformation describe-stacks --stack-name $stackName --query "Stacks[0].Outputs[?OutputKey=='FixtureRepoCloneUrlHttp'].OutputValue" --output text --no-cli-pager
$pythonFn = aws cloudformation describe-stacks --stack-name $stackName --query "Stacks[0].Outputs[?OutputKey=='MockPythonFunctionName'].OutputValue" --output text --no-cli-pager
$nodejsFn = aws cloudformation describe-stacks --stack-name $stackName --query "Stacks[0].Outputs[?OutputKey=='MockNodejsFunctionName'].OutputValue" --output text --no-cli-pager
$dotnetFn = aws cloudformation describe-stacks --stack-name $stackName --query "Stacks[0].Outputs[?OutputKey=='MockDotnetFunctionName'].OutputValue" --output text --no-cli-pager

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  Mock Fixture Deployed!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  CodeCommit repo URL: $repoUrl" -ForegroundColor Cyan
Write-Host "  Functions:" -ForegroundColor Cyan
Write-Host "    - $pythonFn  (python3.9)" -ForegroundColor Cyan
Write-Host "    - $nodejsFn  (nodejs16.x)" -ForegroundColor Cyan
Write-Host "    - $dotnetFn  (dotnet6)" -ForegroundColor Cyan
Write-Host "  Region:              $region" -ForegroundColor Cyan
Write-Host "`nNote: Trusted Advisor's deprecated-runtime detection has a lag," -ForegroundColor Yellow
Write-Host "      so these functions may take time (often hours) to appear in the" -ForegroundColor Yellow
Write-Host "      dashboard inventory after the next scan." -ForegroundColor Yellow
