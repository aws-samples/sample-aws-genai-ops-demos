# deploy-all.ps1 — One-command deployment for SaaS Status MCP Server
# Usage: .\deploy-all.ps1

param()

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  SaaS Status MCP Server - Deployment" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Resolve paths
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path "$ScriptDir/../..").Path

# Set PYTHONPATH so CDK app can import shared/utils
$env:PYTHONPATH = $repoRoot

# Check prerequisites
& "$repoRoot/shared/scripts/check-prerequisites.ps1"
$region = $global:AWS_REGION
$account = (aws sts get-caller-identity --query "Account" --output text)

Write-Host ""
Write-Host "Deploying to region: $region (account: $account)" -ForegroundColor Yellow

# ─── Step 1: Package the MCP server code ───
Write-Host ""
Write-Host "[1/4] Packaging MCP server code..." -ForegroundColor Yellow

$packageDir = "$ScriptDir/build"
$zipPath = "$packageDir/deployment_package.zip"

# Clean previous build
if (Test-Path $packageDir) { Remove-Item -Recurse -Force $packageDir }
New-Item -ItemType Directory -Path $packageDir | Out-Null

# Create a staging directory with the code
$stageDir = "$packageDir/stage"
New-Item -ItemType Directory -Path $stageDir | Out-Null

# Copy agent code flat into the staging dir (main.py must be at the zip root).
# NOTE: providers.json is deliberately NOT bundled — it lives in S3 as the single
# runtime source of truth (uploaded separately below). This avoids shipping the
# registry in two places.
Copy-Item "$ScriptDir/agent/*.py" "$stageDir/"

# Install dependencies for Linux target (AgentCore runs on Linux)
uv pip install -r "$ScriptDir/agent/requirements.txt" --python-platform aarch64-unknown-linux-gnu --python-version 3.13 --target "$stageDir" 2>$null

# Remove Python cache files incompatible with target runtime
Get-ChildItem -Path $stageDir -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem -Path $stageDir -Recurse -Include "*.pyc","*.pyo" | Remove-Item -Force

# Create zip
Compress-Archive -Path "$stageDir/*" -DestinationPath $zipPath -Force
$zipSize = [math]::Round((Get-Item $zipPath).Length / 1MB, 2)
Write-Host "      Package created: $zipSize MB" -ForegroundColor Gray

# ─── Step 2: Create S3 bucket and upload deployment package ───
Write-Host "[2/4] Uploading deployment package to S3..." -ForegroundColor Yellow

$bucketName = "saas-status-mcp-$account-$region"

# Create bucket if it doesn't exist
$bucketExists = aws s3api head-bucket --bucket $bucketName 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "      Creating S3 bucket: $bucketName" -ForegroundColor Gray
    if ($region -eq "us-east-1") {
        aws s3api create-bucket --bucket $bucketName | Out-Null
    } else {
        aws s3api create-bucket --bucket $bucketName --create-bucket-configuration LocationConstraint=$region | Out-Null
    }
}

aws s3 cp $zipPath "s3://$bucketName/agent/deployment_package.zip" --quiet
Write-Host "      Uploaded to s3://$bucketName/agent/deployment_package.zip" -ForegroundColor Gray

# Upload the provider registry as a standalone config object.
# The running server reads this via conditional GET, so updating providers
# later is just a re-upload (see scripts/refresh-providers.ps1) — no redeploy needed.
aws s3 cp "$ScriptDir/agent/providers.json" "s3://$bucketName/config/providers.json" --quiet
Write-Host "      Uploaded to s3://$bucketName/config/providers.json" -ForegroundColor Gray

# ─── Step 3: Deploy CDK stack (creates IAM role + AgentCore Runtime) ───
Write-Host "[3/4] Deploying CDK stack (IAM + AgentCore Runtime)..." -ForegroundColor Yellow

Push-Location "$ScriptDir/infrastructure/cdk"
python -m pip install -r requirements.txt --quiet 2>$null
npx cdk deploy --require-approval never
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Host ""
    Write-Host "ERROR: CDK deployment failed. See errors above." -ForegroundColor Red
    exit 1
}
Pop-Location

# ─── Step 4: Retrieve outputs and display config ───
Write-Host "[4/4] Retrieving deployment outputs..." -ForegroundColor Yellow

$stackName = "SaasStatusMcpStack-$region"
$outputs = aws cloudformation describe-stacks --stack-name $stackName --query "Stacks[0].Outputs" --output json 2>$null | ConvertFrom-Json

$runtimeArn = ($outputs | Where-Object { $_.OutputKey -eq "RuntimeArn" }).OutputValue
$runtimeEndpoint = ($outputs | Where-Object { $_.OutputKey -eq "RuntimeEndpoint" }).OutputValue
$runtimeRoleArn = ($outputs | Where-Object { $_.OutputKey -eq "RuntimeRoleArn" }).OutputValue
$logGroupName = ($outputs | Where-Object { $_.OutputKey -eq "LogGroupName" }).OutputValue

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Stack:          $stackName" -ForegroundColor Cyan
Write-Host "  Region:         $region" -ForegroundColor Cyan
Write-Host "  Runtime ARN:    $runtimeArn" -ForegroundColor Cyan
Write-Host "  MCP Endpoint:   $runtimeEndpoint" -ForegroundColor Cyan
Write-Host "  Runtime Role:   $runtimeRoleArn" -ForegroundColor Cyan
Write-Host "  Log Group:      $logGroupName" -ForegroundColor Cyan
Write-Host ""
Write-Host "  The runtime is IAM-protected (SigV4) - callers sign requests via the" -ForegroundColor Gray
Write-Host "  InvokeAgentRuntime API. See README 'Consuming the server' for details." -ForegroundColor Gray

# Clean up build artifacts
Remove-Item -Recurse -Force $packageDir

# ─── Register with AWS DevOps Agent (optional, interactive) ───
Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  Register with AWS DevOps Agent" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""
$register = Read-Host "  Register this MCP server with a DevOps Agent Space now? (y/N)"
if ($register -match '^[Yy]') {
    & "$ScriptDir/scripts/setup-devops-agent.ps1" -RuntimeRegion $region
} else {
    Write-Host ""
    Write-Host "  Skipped. Run it anytime with:" -ForegroundColor Gray
    Write-Host "    .\scripts\setup-devops-agent.ps1 -RuntimeRegion $region" -ForegroundColor Cyan
}

# ─── Kiro / local MCP clients ───
# Generate local-proxy/mcp.json with the real runtime ARN and region baked in.
# Users just point Kiro at this file — no manual copy-paste or URL construction.
$mcpConfigPath = "$ScriptDir/local-proxy/mcp.json"

$mcpConfig = @"
{
  "mcpServers": {
    "saas-status-mcp": {
      "command": "python",
      "args": ["$ScriptDir/local-proxy/proxy.py"],
      "env": {
        "SAAS_MCP_RUNTIME_ARN": "$runtimeArn",
        "AWS_REGION": "$region"
      },
      "disabled": false,
      "autoApprove": [
        "list_providers",
        "get_service_status",
        "get_active_events",
        "check_all_dependencies"
      ]
    }
  }
}
"@

$mcpConfig | Out-File -FilePath $mcpConfigPath -Encoding utf8 -Force

Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  Test locally from Kiro (optional)" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""
Write-Host "  mcp.json written to: local-proxy/mcp.json" -ForegroundColor Green
Write-Host ""
Write-Host "  To connect Kiro to the deployed MCP Server on Bedrock AgentCore Runtime:" -ForegroundColor White
Write-Host "    1) pip install -r local-proxy/requirements.txt" -ForegroundColor White
Write-Host "    2) Merge local-proxy/mcp.json into your Kiro mcp.json" -ForegroundColor White
Write-Host ""
