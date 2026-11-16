<#
.SYNOPSIS
  Hands-free Amazon DevOps Agent setup for the Aurora demo.

  Creates (or reuses) the Agent Space, IAM roles, Operator App, AWS-account
  association, a generic webhook, and the aurora-incident-business-context
  skill from aws/tools-for-devops-agent via the AWS SDK. The webhook URL + secret are
  written to .devops-agent.env so deploy-all.ps1 picks them up automatically.

  If your AWS SDK build lacks the DevOps Agent API, the equivalent one-time
  console steps are printed instead.

.EXAMPLE
  ./setup-devops-agent.ps1 -Region <region>
#>
param(
  [string]$Region = "",
  [string]$SpaceName = "aurora-demo"
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot

if ([string]::IsNullOrEmpty($Region)) {
  $Region = $env:AWS_DEFAULT_REGION
  if ([string]::IsNullOrEmpty($Region)) { $Region = (aws configure get region 2>$null) }
}
if ([string]::IsNullOrEmpty($Region)) { Write-Host "ERROR: -Region required" -ForegroundColor Red; exit 1 }

Write-Host "==> Configuring Amazon DevOps Agent (region: $Region)..." -ForegroundColor Yellow
python "$ScriptDir\devops_agent_setup.py" --region $Region --space-name $SpaceName


Write-Host "`nDone. Now deploy the demo (it auto-loads the webhook):" -ForegroundColor Green
Write-Host "    ./deploy-all.ps1 -KeyFile `"$HOME\.ssh\your-key.pem`""
