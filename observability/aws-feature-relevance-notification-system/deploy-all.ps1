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

# On Windows, cdk.json references "python3" which doesn't exist; override with "python"
$cdkAppArg = @("--app", "python app.py")

Write-Host "Using region: $Region" -ForegroundColor Cyan

# Destroy mode
if ($DestroyInfra) {
    Write-Host "Destroying infrastructure..." -ForegroundColor Red
    # `cdk destroy` re-synthesizes app.py, which does `from shared.utils.aws_utils import
    # get_region` and imports aws_cdk. Both must be importable by the interpreter cdk uses, so
    # set PYTHONPATH to the repo root (the deploy path sets it below, but destroy exits first)
    # and verify both imports -- otherwise destroy fails with a cryptic ModuleNotFoundError.
    $WorkspaceRoot = (Resolve-Path "$ScriptDir\..\..").Path
    $env:PYTHONPATH = "$WorkspaceRoot;$env:PYTHONPATH"
    python -c "import aws_cdk" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: 'aws_cdk' is not importable by python, which cdk needs to synthesize the app for destroy." -ForegroundColor Red
        Write-Host "       Install the CDK deps first (in a venv): python -m pip install -r requirements.txt" -ForegroundColor Yellow
        exit 1
    }
    python -c "import shared.utils.aws_utils" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: the repo's 'shared' package is not importable, which app.py needs to synthesize for destroy." -ForegroundColor Red
        Write-Host "       Run this script from within the repo so PYTHONPATH can reach the repo root." -ForegroundColor Yellow
        exit 1
    }
    Push-Location $CdkDir
    cdk destroy $StackName @cdkAppArg --force
    $destroyExit = $LASTEXITCODE
    Pop-Location
    if ($destroyExit -ne 0) {
        Write-Host "ERROR: CDK destroy failed" -ForegroundColor Red
        exit 1
    }
    Write-Host "Infrastructure destruction completed" -ForegroundColor Green
    exit 0
}

# Install CDK dependencies and FAIL LOUDLY. $ErrorActionPreference="Stop" does NOT trap a
# native command's non-zero exit, so we must check $LASTEXITCODE explicitly -- otherwise a
# failed pip install falls through to `cdk deploy` and dies with ModuleNotFoundError:
# No module named 'aws_cdk'. We also do not auto-force --break-system-packages (which mutates
# the system Python); that bypass is opt-in only via DEPLOY_CDK_ALLOW_BREAK_SYSTEM_PACKAGES=1.
Write-Host "`nInstalling CDK dependencies..." -ForegroundColor Yellow
Push-Location $CdkDir
python -m pip install -r requirements.txt -q
if ($LASTEXITCODE -ne 0) {
    if ($env:DEPLOY_CDK_ALLOW_BREAK_SYSTEM_PACKAGES -eq "1") {
        Write-Host "Normal pip install failed; DEPLOY_CDK_ALLOW_BREAK_SYSTEM_PACKAGES=1 set, retrying with --break-system-packages (mutates system Python)..." -ForegroundColor Yellow
        python -m pip install -r requirements.txt -q --break-system-packages
        if ($LASTEXITCODE -ne 0) {
            Pop-Location
            Write-Host "ERROR: Failed to install CDK dependencies (requirements.txt)" -ForegroundColor Red
            exit 1
        }
    } else {
        Pop-Location
        Write-Host "ERROR: Failed to install CDK dependencies (requirements.txt)." -ForegroundColor Red
        Write-Host "If this is an 'externally-managed-environment' (PEP 668) error, do NOT force it into" -ForegroundColor Yellow
        Write-Host "your system Python. Create and activate a virtual environment first:" -ForegroundColor Yellow
        Write-Host "    python -m venv .venv; .\.venv\Scripts\Activate.ps1" -ForegroundColor Gray
        Write-Host "then re-run. On a throwaway/ephemeral host you may instead re-run with:" -ForegroundColor Gray
        Write-Host "    `$env:DEPLOY_CDK_ALLOW_BREAK_SYSTEM_PACKAGES = '1'" -ForegroundColor Gray
        exit 1
    }
}
# Verify aws_cdk is importable by the interpreter that will synth the app.
python -c "import aws_cdk" 2>$null
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Host "ERROR: 'aws_cdk' is not importable by python after installing requirements.txt." -ForegroundColor Red
    Write-Host "       Use a virtual environment so pip and python are the same interpreter." -ForegroundColor Yellow
    exit 1
}
Pop-Location

# Install Lambda dependencies (no Docker required). These are vendored into the function dir
# via -t and zipped into the Lambda, so a SILENT failure ships a Lambda missing its deps that
# only fails at RUNTIME in AWS. Fail loudly. (--break-system-packages is irrelevant for -t.)
Write-Host "Installing Lambda dependencies..." -ForegroundColor Yellow
python -m pip install -r "$ScriptDir\lambdas\rss-ingestion\requirements.txt" -t "$ScriptDir\lambdas\rss-ingestion" -q --no-compile
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to install Lambda dependencies for rss-ingestion" -ForegroundColor Red
    exit 1
}

# Set PYTHONPATH for shared utilities
$WorkspaceRoot = (Resolve-Path "$ScriptDir\..\..").Path
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
cdk deploy $StackName @cdkAppArg --require-approval never @contextArgs
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