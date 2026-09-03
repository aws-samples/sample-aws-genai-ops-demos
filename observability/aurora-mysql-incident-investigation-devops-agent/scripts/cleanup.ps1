<#
.SYNOPSIS
  Tear down the Aurora incident-investigation demo.

.EXAMPLE
  ./cleanup.ps1 -Region us-east-1
#>
param(
  [string]$Region = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot

if ([string]::IsNullOrEmpty($Region)) {
  $Region = $env:AWS_DEFAULT_REGION
  if ([string]::IsNullOrEmpty($Region)) { $Region = (aws configure get region 2>$null) }
}
if ([string]::IsNullOrEmpty($Region)) { Write-Host "ERROR: -Region required" -ForegroundColor Red; exit 1 }

$CdkDir = Join-Path $ScriptDir "..\infrastructure\cdk"
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..\..")).Path
$env:PYTHONPATH = $RepoRoot

Write-Host "==> Destroying CDK stacks in $Region..." -ForegroundColor Yellow
if (Test-Path "$CdkDir\.venv\Scripts\Activate.ps1") { & "$CdkDir\.venv\Scripts\Activate.ps1" }
Push-Location $CdkDir
npx -y cdk destroy "AuroraDemoStack-$Region" "AuroraDemoMcpServer-$Region" `
  --force --no-cli-pager --context "keyPairName=placeholder"
Pop-Location

Write-Host "`n==> Removing DevOps Agent space + IAM roles (if created by setup-devops-agent.ps1)..." -ForegroundColor Yellow
try { python "$ScriptDir\devops_agent_setup.py" --region $Region --teardown } catch { Write-Host "  (agent teardown skipped: $_)" }

Write-Host "`n==> Cleanup complete." -ForegroundColor Green
Write-Host "   Manually remove if desired:"
Write-Host "     - MCP server registration in the DevOps Agent console"
Write-Host "     - Secrets Manager secret 'aurora-demo/credentials'"
