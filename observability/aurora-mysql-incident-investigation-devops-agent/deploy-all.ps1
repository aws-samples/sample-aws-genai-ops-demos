<#
.SYNOPSIS
  Deploy the Aurora MySQL incident-investigation demo via CDK, then configure
  the bastion load-generator and CloudWatch alarms.

.EXAMPLE
  ./deploy-all.ps1 -KeyFile "$HOME\.ssh\your-key.pem" -KeyPair your-key
  ./deploy-all.ps1 -KeyFile ... -WebhookUrl '<url>' -WebhookSecret '<secret>'
#>
param(
  [Parameter(Mandatory = $true)][string]$KeyFile,
  [string]$KeyPair = "",
  [string]$WebhookUrl = "",
  [string]$WebhookSecret = "",
  [string]$SshCidr = "",
  [switch]$SshOpen
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot

if (-not (Test-Path $KeyFile)) { Write-Host "ERROR: key file not found: $KeyFile" -ForegroundColor Red; exit 1 }

# Auto-load the DevOps Agent webhook written by setup-devops-agent.ps1, unless supplied explicitly.
$envFile = Join-Path $ScriptDir ".devops-agent.env"
if ([string]::IsNullOrEmpty($WebhookUrl) -and (Test-Path $envFile)) {
  foreach ($line in Get-Content $envFile) {
    if ($line -match "^\s*(?:export\s+)?WEBHOOK_URL\s*=\s*'?([^'\r\n]+?)'?\s*$") { $WebhookUrl = $Matches[1] }
    if ($line -match "^\s*(?:export\s+)?WEBHOOK_SECRET\s*=\s*'?([^'\r\n]+?)'?\s*$") { $WebhookSecret = $Matches[1] }
  }
  if ($WebhookUrl) { Write-Host "  Loaded DevOps Agent webhook from .devops-agent.env" }
}

Write-Host "==> Step 1: Check prerequisites..." -ForegroundColor Yellow
& "$ScriptDir\..\..\shared\scripts\check-prerequisites.ps1" -RequireCDK -SkipServiceCheck
$region = $global:AWS_REGION

if ([string]::IsNullOrEmpty($KeyPair)) {
  Write-Host "Available key pairs in ${region}:"
  aws ec2 describe-key-pairs --region $region --query 'KeyPairs[].KeyName' --output table --no-cli-pager
  $KeyPair = Read-Host "Enter key pair name"
  if ([string]::IsNullOrEmpty($KeyPair)) { Write-Host "ERROR: key pair required" -ForegroundColor Red; exit 1 }
}

if ($SshOpen) {
  $SshCidr = "0.0.0.0/0"
  Write-Host "  SSH open to 0.0.0.0/0 (not recommended for production)" -ForegroundColor Yellow
} elseif ([string]::IsNullOrEmpty($SshCidr)) {
  try { $myIp = (Invoke-RestMethod -Uri "https://checkip.amazonaws.com" -TimeoutSec 5).Trim() } catch { $myIp = "" }
  if ($myIp) { $SshCidr = "$myIp/32"; Write-Host "  SSH restricted to your IP: $SshCidr" }
  else { $SshCidr = "0.0.0.0/0"; Write-Host "  Could not detect IP — SSH open to 0.0.0.0/0" -ForegroundColor Yellow }
}

Write-Host "`n==> Step 2: Deploy Aurora infrastructure via CDK (cluster creation ~10-15 min)..." -ForegroundColor Yellow
$CdkDir = Join-Path $ScriptDir "infrastructure\cdk"
$StackName = "AuroraDemoStack-$region"
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$env:PYTHONPATH = $RepoRoot

python -m venv "$CdkDir\.venv"
& "$CdkDir\.venv\Scripts\Activate.ps1"
pip install -r "$CdkDir\requirements.txt"

$accountId = (aws sts get-caller-identity --query Account --output text --no-cli-pager)
Push-Location $CdkDir
npx -y cdk bootstrap "aws://$accountId/$region" --no-cli-pager

$cdkArgs = @("deploy", $StackName, "--require-approval", "never", "--no-cli-pager",
  "--context", "keyPairName=$KeyPair", "--context", "sshCidr=$SshCidr")
if ($WebhookUrl) { $cdkArgs += @("--context", "webhookUrl=$WebhookUrl") }
if ($WebhookSecret) { $cdkArgs += @("--context", "webhookSecret=$WebhookSecret") }
npx -y cdk @cdkArgs
Pop-Location

Write-Host "`n==> Step 3: Fetch stack outputs..." -ForegroundColor Yellow
function Get-Output($key) {
  aws cloudformation describe-stacks --region $region --stack-name $StackName `
    --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" --output text --no-cli-pager
}
$ClusterId = Get-Output "ClusterIdentifier"
$WriterEndpoint = Get-Output "WriterEndpoint"
$ReaderEndpoint = Get-Output "ReaderEndpoint"
$DbPort = Get-Output "DbPort"
$DbName = Get-Output "DefaultDatabaseName"
$SecretArn = Get-Output "SecretArn"
$BastionIp = Get-Output "BastionPublicIp"

Write-Host "  Cluster: $ClusterId"
Write-Host "  Writer : $WriterEndpoint | Reader: $ReaderEndpoint"
Write-Host "  Bastion: $BastionIp"

$sshOpts = @("-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-i", $KeyFile)
$sshTarget = "ec2-user@$BastionIp"

Write-Host "`n==> Step 4: Wait for SSH on $BastionIp..." -ForegroundColor Yellow
for ($i = 1; $i -le 30; $i++) {
  ssh @sshOpts -o ConnectTimeout=5 $sshTarget "true" 2>$null
  if ($LASTEXITCODE -eq 0) { break }
  Start-Sleep -Seconds 10; Write-Host "  Waiting... $($i*10)s"
}

Write-Host "==> Step 5: Wait for UserData (mysql client install) to complete..." -ForegroundColor Yellow
for ($i = 1; $i -le 30; $i++) {
  ssh @sshOpts $sshTarget "grep -q USERDATA_COMPLETE /var/log/aurora-userdata.log 2>/dev/null"
  if ($LASTEXITCODE -eq 0) { Write-Host "  Done."; break }
  Start-Sleep -Seconds 10; Write-Host "  Installing packages... ($i/30)"
}

Write-Host "==> Step 6: Push bastion scripts + write environment file..." -ForegroundColor Yellow
scp @sshOpts (Join-Path $ScriptDir "bastion-scripts\*") "${sshTarget}:/tmp/"
ssh @sshOpts $sshTarget "sudo mkdir -p /opt/aurora-demo && sudo cp /tmp/lib.sh /tmp/inject /tmp/rollback /tmp/status /tmp/list /tmp/seed-data /opt/aurora-demo/ && sudo chmod +x /opt/aurora-demo/inject /opt/aurora-demo/rollback /opt/aurora-demo/status /opt/aurora-demo/list /opt/aurora-demo/seed-data"

$envContent = "REGION=$region`nCLUSTER_ID=$ClusterId`nWRITER_ENDPOINT=$WriterEndpoint`nREADER_ENDPOINT=$ReaderEndpoint`nDB_PORT=$DbPort`nDB_NAME=$DbName`nSECRET_ARN=$SecretArn"
ssh @sshOpts $sshTarget "sudo tee /opt/aurora-demo/env >/dev/null" <<< $envContent

Write-Host "==> Step 7: Seed demo schema/data..." -ForegroundColor Yellow
ssh @sshOpts $sshTarget "sudo /opt/aurora-demo/seed-data"

Write-Host "==> Step 8: Disable actions on dedicated alarms (enabled per-scenario)..." -ForegroundColor Yellow
aws cloudwatch disable-alarm-actions --region $region --alarm-names aurora-demo-memory-pressure aurora-demo-replica-lag --no-cli-pager

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  Aurora Incident Demo Ready" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Region        : $region" -ForegroundColor Cyan
Write-Host "  Cluster       : $ClusterId" -ForegroundColor Cyan
Write-Host "  Writer        : $WriterEndpoint" -ForegroundColor Cyan
Write-Host "  Reader        : $ReaderEndpoint" -ForegroundColor Cyan
Write-Host "  Bastion (SSH) : ssh -i $KeyFile ec2-user@$BastionIp" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Inject:  ./scripts/inject-failure.ps1 -Scenario connection-storm -KeyFile $KeyFile" -ForegroundColor Cyan
Write-Host "  Rollback:./scripts/inject-failure.ps1 -Scenario connection-storm -KeyFile $KeyFile -Rollback" -ForegroundColor Cyan
Write-Host "  Status:  ./scripts/inject-failure.ps1 -Scenario status -KeyFile $KeyFile" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Green
