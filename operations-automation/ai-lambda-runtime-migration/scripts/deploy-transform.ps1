# AI Lambda Runtime Migration Assistant - Transform Build Plane Deploy/Destroy Script
#
# Opt-in Phase-2 build plane: a persistent CodeBuild project running AWS
# Transform (atx), its IAM role, and an EventBridge rule reporting terminal
# build state. The two Phase-2 Lambdas (orchestrator + completion handler) are
# created in the Pipeline stack under the same `transform` CDK context.
#
# This is NOT part of deploy-all. It deploys ONLY the Transform stack
# (LambdaRuntimeMigrationTransform-<region>), gated behind the `transform` CDK
# context, through the SHARED deploy-cdk script (never `cdk deploy` directly),
# then uploads the buildspec to the artifacts bucket.
#
#   .\scripts\deploy-transform.ps1            # deploy the build plane
#   .\scripts\deploy-transform.ps1 -Destroy   # tear it down
#
# Prerequisite: deploy the mock fixture and run a scan first, so there are
# functions to migrate. Migrations are SLOW (tens of minutes to hours).
param(
    [switch]$Destroy
)

# Run from the demo root so shared-script relative paths and frontend/dist
# resolve exactly as deploy-all.ps1 sees them (..\..\shared\...).
Set-Location (Join-Path $PSScriptRoot '..')

Write-Host "=== AI Lambda Runtime Migration - Transform Build Plane ===" -ForegroundColor Cyan

# Run shared prerequisites check (transform service: the pinned Transform CLI versions the buildspec installs).
Write-Host "`nRunning prerequisites check..." -ForegroundColor Yellow
& "..\..\shared\scripts\check-prerequisites.ps1" -RequiredService "transform" -MinAwsCliVersion "2.31.13" -MinPythonVersion "3.12" -RequireCDK -MinCdkVersion "2.1030.0"
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

# Region + stack names
$region = $global:AWS_REGION
$stackName = "LambdaRuntimeMigrationTransform-$region"
$dataStackName = "LambdaRuntimeMigrationData-$region"

if ($Destroy) {
    Write-Host "`nDestroying Transform build-plane stack..." -ForegroundColor Yellow
    Write-Host "      (Removing the CodeBuild project, build role and EventBridge rule)" -ForegroundColor Gray
    # Pass the same transform=true context so the stack synthesizes during destroy.
    & "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $stackName -SkipBootstrap -DestroyStack -CdkContext "transform=true"
    if ($LASTEXITCODE -ne 0) { Write-Host "Transform stack destroy failed" -ForegroundColor Red; exit 1 }

    Write-Host "`n========================================" -ForegroundColor Green
    Write-Host "  Transform Build Plane Destroyed" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Stack:  $stackName" -ForegroundColor Cyan
    Write-Host "  Region: $region" -ForegroundColor Cyan
    exit 0
}

# Deploy ONLY the Transform stack via the SHARED script (never `cdk deploy` directly).
Write-Host "`nDeploying Transform build-plane stack..." -ForegroundColor Yellow
Write-Host "      (Creating the CodeBuild project, build role and EventBridge rule)" -ForegroundColor Gray
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $stackName -SkipBootstrap -CdkContext "transform=true"
if ($LASTEXITCODE -ne 0) { Write-Host "Transform stack deployment failed" -ForegroundColor Red; exit 1 }

# Read the artifacts bucket name from the DataStack output, then upload the
# buildspec the CodeBuild project reads from config/.
$bucket = aws cloudformation describe-stacks --stack-name $dataStackName --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text --no-cli-pager
$projectName = aws cloudformation describe-stacks --stack-name $stackName --query "Stacks[0].Outputs[?OutputKey=='TransformProjectName'].OutputValue" --output text --no-cli-pager

Write-Host "`nUploading buildspec to the artifacts bucket..." -ForegroundColor Yellow
# Object MUST be named buildspec.yml: CodeBuild's S3 single-file source looks for
# a file named buildspec.yml in the downloaded source root.
aws s3 cp cdk/buildspec/transform-migrate.buildspec.yml "s3://$bucket/config/buildspec.yml" --no-cli-pager
if ($LASTEXITCODE -ne 0) { Write-Host "Buildspec upload failed" -ForegroundColor Red; exit 1 }

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  Transform Build Plane Deployed!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  CodeBuild project: $projectName" -ForegroundColor Cyan
Write-Host "  Artifacts bucket:  $bucket" -ForegroundColor Cyan
Write-Host "  Region:            $region" -ForegroundColor Cyan
Write-Host "`nNote: Migrations run AWS Transform in CodeBuild and are SLOW -" -ForegroundColor Yellow
Write-Host "      tens of minutes to hours per function. Deploy the mock fixture" -ForegroundColor Yellow
Write-Host "      (scripts\deploy-mock.ps1) and run a scan first so there are" -ForegroundColor Yellow
Write-Host "      functions to migrate." -ForegroundColor Yellow
