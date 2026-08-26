<#
.SYNOPSIS
  SSH wrapper to run /opt/aurora-demo/ scenario scripts on the bastion.

.EXAMPLE
  ./inject-failure.ps1 -Scenario connection-storm -KeyFile "$HOME\.ssh\key.pem"
  ./inject-failure.ps1 -Scenario connection-storm -KeyFile ... -Rollback
  ./inject-failure.ps1 -Scenario status -KeyFile ...
#>
param(
  [Parameter(Mandatory = $true)][string]$Scenario,
  [Parameter(Mandatory = $true)][string]$KeyFile,
  [string]$Region = "",
  [switch]$Rollback
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $KeyFile)) { Write-Host "ERROR: key file not found: $KeyFile" -ForegroundColor Red; exit 1 }
if ([string]::IsNullOrEmpty($Region)) {
  $Region = $env:AWS_DEFAULT_REGION
  if ([string]::IsNullOrEmpty($Region)) { $Region = (aws configure get region 2>$null) }
}
if ([string]::IsNullOrEmpty($Region)) { Write-Host "ERROR: -Region required (or set AWS default region)" -ForegroundColor Red; exit 1 }

$Stack = "AuroraDemoStack-$Region"
$BastionIp = aws cloudformation describe-stacks --stack-name $Stack --region $Region `
  --query "Stacks[0].Outputs[?OutputKey=='BastionPublicIp'].OutputValue" --output text --no-cli-pager
if ([string]::IsNullOrEmpty($BastionIp) -or $BastionIp -eq "None") {
  Write-Host "ERROR: could not find bastion IP. Is $Stack deployed in ${Region}?" -ForegroundColor Red; exit 1
}

$sshOpts = @("-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-i", $KeyFile)
$sshTarget = "ec2-user@$BastionIp"

switch ($Scenario) {
  "memory-pressure" { $alarm = "aurora-demo-memory-pressure" }
  "replica-lag"     { $alarm = "aurora-demo-replica-lag" }
  default           { $alarm = "" }
}

if ($Scenario -eq "status") {
  ssh @sshOpts $sshTarget "sudo /opt/aurora-demo/status"
  Write-Host "`n=== CloudWatch Alarms ==="
  aws cloudwatch describe-alarms --alarm-name-prefix aurora-demo `
    --query 'MetricAlarms[].{Name:AlarmName,State:StateValue}' --output table --region $Region --no-cli-pager
  return
}

if ($Rollback) {
  ssh @sshOpts $sshTarget "sudo /opt/aurora-demo/rollback $Scenario"
  if ($alarm) { aws cloudwatch disable-alarm-actions --alarm-names $alarm --region $Region --no-cli-pager; Write-Host "Disabled alarm: $alarm" }
  Write-Host "`nVerifying recovery..."
  Start-Sleep -Seconds 5
  ssh @sshOpts $sshTarget "sudo /opt/aurora-demo/status"
} else {
  $firing = aws cloudwatch describe-alarms --alarm-name-prefix aurora-demo --state-value ALARM `
    --query 'MetricAlarms[].AlarmName' --output text --region $Region --no-cli-pager
  if ($firing) {
    Write-Host "⚠ Alarms still firing: $firing" -ForegroundColor Yellow
    $confirm = Read-Host "Continue anyway? (y/N)"
    if ($confirm -ne "y" -and $confirm -ne "Y") { Write-Host "Aborted."; return }
  }
  if ($alarm) { aws cloudwatch enable-alarm-actions --alarm-names $alarm --region $Region --no-cli-pager; Write-Host "Enabled alarm: $alarm" }
  # Launch detached (setsid) so background load generators don't hold the SSH channel open
  ssh @sshOpts $sshTarget "sudo setsid /opt/aurora-demo/inject $Scenario </dev/null >/tmp/aurora-inject.log 2>&1 &"
  Write-Host "`nInjected '$Scenario' (running in background on the bastion). Watch the alarm transition to ALARM (1-3 min), then the DevOps Agent investigation." -ForegroundColor Cyan
}
