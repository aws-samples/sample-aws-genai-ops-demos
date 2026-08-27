# deploy-all-terraform.ps1 - One-command Terraform deployment for SaaS Status MCP Server
# Usage: .\deploy-all-terraform.ps1
#        .\deploy-all-terraform.ps1 -AgentSpaceArn arn:aws:aidevops:eu-west-1:<acct>:agentspace/<id>

param(
    [string]$AgentSpaceArn = ""
)

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  SaaS Status MCP Server - Terraform" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot     = (Resolve-Path "$ScriptDir/../..").Path
$TerraformDir = "$ScriptDir/infrastructure/terraform"

# Check prerequisites
& "$repoRoot/shared/scripts/check-prerequisites.ps1"
$region  = $global:AWS_REGION
$account = (aws sts get-caller-identity --query "Account" --output text)

Write-Host ""
Write-Host "Deploying to region: $region (account: $account)" -ForegroundColor Yellow

# Step 1: Package the MCP server code
Write-Host ""
Write-Host "[1/4] Packaging MCP server code..." -ForegroundColor Yellow

$packageDir = "$ScriptDir/build"
$zipPath    = "$packageDir/deployment_package.zip"

if (Test-Path $packageDir) { Remove-Item -Recurse -Force $packageDir }
New-Item -ItemType Directory -Path $packageDir | Out-Null
$stageDir = "$packageDir/stage"
New-Item -ItemType Directory -Path $stageDir | Out-Null

Copy-Item "$ScriptDir/agent/*.py" "$stageDir/"
uv pip install -r "$ScriptDir/agent/requirements.txt" `
    --python-platform aarch64-unknown-linux-gnu `
    --python-version 3.13 `
    --target "$stageDir" 2>$null

Get-ChildItem -Path $stageDir -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem -Path $stageDir -Recurse -Include "*.pyc","*.pyo" | Remove-Item -Force

Compress-Archive -Path "$stageDir/*" -DestinationPath $zipPath -Force
$zipSize = [math]::Round((Get-Item $zipPath).Length / 1MB, 2)
Write-Host "      Package created: $zipSize MB" -ForegroundColor Gray

# Step 2: Upload to S3
# The bucket is also declared in main.tf; we create it here first so the zip
# upload succeeds before Terraform references it in the runtime resource.
Write-Host "[2/4] Uploading deployment package to S3..." -ForegroundColor Yellow

$bucketName = "saas-status-mcp-$account-$region"
$bucketExists = aws s3api head-bucket --bucket $bucketName 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "      Creating S3 bucket: $bucketName" -ForegroundColor Gray
    if ($region -eq "us-east-1") {
        aws s3api create-bucket --bucket $bucketName | Out-Null
    } else {
        aws s3api create-bucket --bucket $bucketName `
            --create-bucket-configuration LocationConstraint=$region | Out-Null
    }
}

aws s3 cp $zipPath "s3://$bucketName/agent/deployment_package.zip" --quiet
Write-Host "      Uploaded deployment zip" -ForegroundColor Gray
aws s3 cp "$ScriptDir/agent/providers.json" "s3://$bucketName/config/providers.json" --quiet
Write-Host "      Uploaded provider registry" -ForegroundColor Gray

# Step 3: Generate terraform.tfvars
Write-Host "[3/4] Configuring Terraform..." -ForegroundColor Yellow

if (-not $AgentSpaceArn) {
    Write-Host ""
    Write-Host "  To register with DevOps Agent, provide your Agent Space ARN." -ForegroundColor White
    Write-Host "  Open the DevOps Agent console and from your space click Actions > Copy ARN." -ForegroundColor Gray
    Write-Host "  Leave blank to deploy the runtime only." -ForegroundColor Gray
    $AgentSpaceArn = Read-Host "  Agent Space ARN (optional)"
}

$tfvarsLines = @(
    "runtime_region = `"$region`"",
    "account_id     = `"$account`""
)
if ($AgentSpaceArn) {
    $tfvarsLines += "agent_space_arn = `"$AgentSpaceArn`""
}

$tfvarsPath = "$TerraformDir/terraform.tfvars"
[System.IO.File]::WriteAllLines($tfvarsPath, $tfvarsLines, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "      terraform.tfvars written" -ForegroundColor Gray

# Step 4: Terraform init + apply
# AWS_SDK_LOAD_CONFIG=0 prevents Terraform from reading ~/.aws/config, which
# avoids a "source_profile requires role_arn" error when the config has an
# incomplete source_profile entry. Terraform falls back to static credentials
# from ~/.aws/credentials instead.
Write-Host "[4/4] Running terraform apply..." -ForegroundColor Yellow
Write-Host ""

$env:AWS_SDK_LOAD_CONFIG = "0"

Push-Location $TerraformDir
terraform init -upgrade
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) { Pop-Location; Write-Host "ERROR: terraform init failed." -ForegroundColor Red; exit 1 }
terraform apply -auto-approve
$exitCode = $LASTEXITCODE
Pop-Location
Remove-Item Env:\AWS_SDK_LOAD_CONFIG -ErrorAction SilentlyContinue

if ($exitCode -ne 0) {
    Write-Host ""
    Write-Host "ERROR: terraform apply failed. See errors above." -ForegroundColor Red
    Remove-Item -Recurse -Force $packageDir -ErrorAction SilentlyContinue
    exit 1
}

# Read outputs
Push-Location $TerraformDir
$runtimeArn = (terraform output -raw runtime_arn 2>$null)
$s3Bucket   = (terraform output -raw s3_bucket 2>$null)
$logGroup   = (terraform output -raw log_group 2>$null)
Pop-Location

# Generate local-proxy/mcp.json
$proxyPath     = "$($ScriptDir.Replace('\','/') )/local-proxy/proxy.py"
$mcpConfigPath = "$ScriptDir/local-proxy/mcp.json"
$mcpLines = @(
    '{',
    '  "mcpServers": {',
    '    "saas-status-mcp": {',
    '      "command": "python",',
    "      `"args`": [`"$proxyPath`"],",
    '      "env": {',
    "        `"SAAS_MCP_RUNTIME_ARN`": `"$runtimeArn`",",
    "        `"AWS_REGION`": `"$region`"",
    '      },',
    '      "disabled": false,',
    '      "autoApprove": [',
    '        "list_providers",',
    '        "get_service_status",',
    '        "get_active_events",',
    '        "check_all_dependencies"',
    '      ]',
    '    }',
    '  }',
    '}'
)
[System.IO.File]::WriteAllLines($mcpConfigPath, $mcpLines, (New-Object System.Text.UTF8Encoding($false)))

# Cleanup
Remove-Item -Recurse -Force $packageDir

# Summary
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Region:       $region" -ForegroundColor Cyan
Write-Host "  Runtime ARN:  $runtimeArn" -ForegroundColor Cyan
Write-Host "  S3 bucket:    $s3Bucket" -ForegroundColor Cyan
Write-Host "  Log group:    $logGroup" -ForegroundColor Cyan
Write-Host ""
Write-Host "  The runtime is IAM-protected (SigV4). See README 'Consuming the server'." -ForegroundColor Gray
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
