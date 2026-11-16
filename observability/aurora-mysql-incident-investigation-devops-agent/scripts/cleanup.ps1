<#
.SYNOPSIS
  Tear down the Aurora incident-investigation demo.

.EXAMPLE
  ./cleanup.ps1 -Region <region>
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

$CdkDir = (Resolve-Path (Join-Path $ScriptDir "..\infrastructure\cdk")).Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..\..")).Path

Write-Host "==> Destroying CDK stack in $Region..." -ForegroundColor Yellow
if (-not (Test-Path "$CdkDir\.venv")) { python -m venv "$CdkDir\.venv" }
& "$CdkDir\.venv\Scripts\Activate.ps1"
$env:AWS_REGION = $Region
& "$RepoRoot\shared\scripts\deploy-cdk.ps1" -CdkDirectory $CdkDir -StackName "AuroraDemoStack-$Region" `
  -DestroyStack -SkipBootstrap -CdkContext @("keyPairName=placeholder")

Write-Host "`n==> Removing DevOps Agent space + IAM roles (if created by setup-devops-agent.ps1)..." -ForegroundColor Yellow
try { python "$ScriptDir\devops_agent_setup.py" --region $Region --teardown } catch { Write-Host "  (agent teardown skipped: $_)" }

Remove-Item -ErrorAction SilentlyContinue (Join-Path $ScriptDir "..\aurora-incident-business-context.zip")
Write-Host "`n==> Cleanup complete." -ForegroundColor Green
Write-Host "   Manually remove if desired:"
Write-Host "     - Secrets Manager secret 'aurora-demo/credentials'"
