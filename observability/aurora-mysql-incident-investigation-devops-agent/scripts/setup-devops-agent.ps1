<#
.SYNOPSIS
  Hands-free Amazon DevOps Agent setup for the Aurora demo.

  Creates (or reuses) the Agent Space, IAM roles, Operator App, AWS-account
  association, and a generic webhook via the AWS SDK. The webhook URL + secret are
  written to .devops-agent.env so deploy-all.ps1 picks them up automatically.

  If your AWS SDK build lacks the DevOps Agent API, the equivalent one-time
  console steps are printed instead.

.EXAMPLE
  ./setup-devops-agent.ps1 -Region us-west-2
  ./setup-devops-agent.ps1 -Region us-west-2 -WithMcp
#>
param(
  [string]$Region = "",
  [string]$SpaceName = "aurora-demo",
  [switch]$WithMcp
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
$DemoDir = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..\..")).Path

if ([string]::IsNullOrEmpty($Region)) {
  $Region = $env:AWS_DEFAULT_REGION
  if ([string]::IsNullOrEmpty($Region)) { $Region = (aws configure get region 2>$null) }
}
if ([string]::IsNullOrEmpty($Region)) { Write-Host "ERROR: -Region required" -ForegroundColor Red; exit 1 }

Write-Host "==> Configuring Amazon DevOps Agent (region: $Region)..." -ForegroundColor Yellow
python "$ScriptDir\devops_agent_setup.py" --region $Region --space-name $SpaceName

if ($WithMcp) {
  Write-Host "`n==> Deploying MCP business-context server (AuroraDemoMcpServer-$Region)..." -ForegroundColor Yellow
  $CdkDir = Join-Path $DemoDir "infrastructure\cdk"
  $env:PYTHONPATH = $RepoRoot
  if (-not (Test-Path "$CdkDir\.venv")) { python -m venv "$CdkDir\.venv" }
  & "$CdkDir\.venv\Scripts\Activate.ps1"
  pip install -q -r "$CdkDir\requirements.txt"
  Push-Location $CdkDir
  npx -y cdk deploy "AuroraDemoMcpServer-$Region" --require-approval never --no-cli-pager
  Pop-Location

  $McpEndpoint = aws cloudformation describe-stacks --stack-name "AuroraDemoMcpServer-$Region" --region $Region --no-cli-pager `
    --query "Stacks[0].Outputs[?OutputKey=='McpEndpoint'].OutputValue" --output text 2>$null
  $ApiKeyId = aws cloudformation describe-stacks --stack-name "AuroraDemoMcpServer-$Region" --region $Region --no-cli-pager `
    --query "Stacks[0].Outputs[?OutputKey=='ApiKeyId'].OutputValue" --output text 2>$null
  if (-not [string]::IsNullOrEmpty($McpEndpoint) -and $McpEndpoint -ne "None") {
    Write-Host "`n  Register this MCP server in the Agent Space (Capabilities > MCP servers):"
    Write-Host "    Endpoint : $McpEndpoint"
    if (-not [string]::IsNullOrEmpty($ApiKeyId) -and $ApiKeyId -ne "None") {
      $ApiKeyValue = aws apigateway get-api-key --api-key $ApiKeyId --include-value --region $Region --query 'value' --output text --no-cli-pager 2>$null
      Write-Host "    Header   : x-api-key: $ApiKeyValue"
    }
  }
}

Write-Host "`nDone. Now deploy the demo (it auto-loads the webhook):" -ForegroundColor Green
Write-Host "    ./deploy-all.ps1 -KeyFile `"$HOME\.ssh\your-key.pem`""
