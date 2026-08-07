# PowerShell deployment script for AWS Feature Relevance Notification System
param(
    [string]$KnowledgeBaseId = "",
    [string]$SlackWebhookUrl = "",
    [switch]$DestroyInfra
)

$ErrorActionPreference = "Stop"

Write-Host "=== AWS Feature Relevance Notification System Deployment ===" -ForegroundColor Green

# Use shared prerequisites check (validates AWS CLI, region, service availability)
# Region is available as $global:AWS_REGION after this call
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& "$ScriptDir\..\..\shared\scripts\check-prerequisites.ps1" -RequiredService bedrock

if ($LASTEXITCODE -ne 0) {
    Write-Host "Prerequisites check failed" -ForegroundColor Red
    exit 1
}

$Region = $global:AWS_REGION
$StackName = "FeatureRelevanceNotification-$Region"
$CdkDir = Join-Path $ScriptDir "infrastructure" "cdk"

Write-Host "Using region: $Region" -ForegroundColor Cyan

# Destroy mode
if ($DestroyInfra) {
    Write-Host "Destroying infrastructure..." -ForegroundColor Red
    Push-Location $CdkDir
    cdk destroy $StackName --force
    Pop-Location
    Write-Host "Infrastructure destruction completed" -ForegroundColor Green
    exit 0
}

# Install CDK dependencies
Write-Host "`nInstalling CDK dependencies..." -ForegroundColor Yellow
Push-Location $CdkDir
python -m pip install -r requirements.txt -q
Pop-Location

# Set PYTHONPATH for shared utilities
$WorkspaceRoot = (Resolve-Path "$ScriptDir\..\..\..\..").Path
$env:PYTHONPATH = "$WorkspaceRoot;$env:PYTHONPATH"

# Build context args
$contextArgs = @()
if (-not [string]::IsNullOrEmpty($KnowledgeBaseId)) {
    $contextArgs += "--context"
    $contextArgs += "knowledge_base_id=$KnowledgeBaseId"
}
if (-not [string]::IsNullOrEmpty($SlackWebhookUrl)) {
    $contextArgs += "--context"
    $contextArgs += "slack_webhook_url=$SlackWebhookUrl"
}

# Deploy CDK stack
Write-Host "`nDeploying CDK stack..." -ForegroundColor Yellow
Push-Location $CdkDir
cdk deploy $StackName --require-approval never @contextArgs
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Host "ERROR: CDK deployment failed" -ForegroundColor Red
    exit 1
}
Pop-Location

# Get stack outputs
Write-Host "`nGetting stack outputs..." -ForegroundColor Yellow
$outputs = aws cloudformation describe-stacks --stack-name $StackName --query "Stacks[0].Outputs" --output json --region $Region --no-cli-pager 2>&1

$rssFunction = "N/A"
$workloadManager = "N/A"

if ($LASTEXITCODE -eq 0) {
    $outputsJson = $outputs | ConvertFrom-Json
    foreach ($output in $outputsJson) {
        switch ($output.OutputKey) {
            "RSSIngestionFunctionName" { $rssFunction = $output.OutputValue }
            "WorkloadManagerFunctionName" { $workloadManager = $output.OutputValue }
        }
    }
}

# Print deployment summary
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Region:              $Region" -ForegroundColor Cyan
Write-Host "  Stack Name:          $StackName" -ForegroundColor Cyan
Write-Host "  RSS Ingestion:       $rssFunction" -ForegroundColor Cyan
Write-Host "  Workload Manager:    $workloadManager" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "  1. Configure Slack webhook in Secrets Manager" -ForegroundColor White
Write-Host "  2. Create workload profiles (see README.md)" -ForegroundColor White
Write-Host "  3. Test with:" -ForegroundColor White
Write-Host "     aws lambda invoke --function-name $rssFunction \" -ForegroundColor Gray
Write-Host "       --cli-binary-format raw-in-base64-out \" -ForegroundColor Gray
Write-Host "       --payload '{""test_mode"":true}' /tmp/test.json" -ForegroundColor Gray
Write-Host ""
Write-Host "To destroy the infrastructure later, run:" -ForegroundColor Yellow
Write-Host "  .\deploy-all.ps1 -DestroyInfra" -ForegroundColor White
