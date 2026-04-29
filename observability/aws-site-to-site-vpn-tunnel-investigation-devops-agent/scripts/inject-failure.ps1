# inject-failure.ps1 — SSH wrapper to run /opt/vpn-demo/ scripts on the CGW
# Usage:
#   .\inject-failure.ps1 <scenario> -KeyFile <path> [-Region <region>] [-Rollback]
#   .\inject-failure.ps1 status -KeyFile <path> [-Region <region>]
#   .\inject-failure.ps1 list

param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$Action,
    [string]$KeyFile = "",
    [string]$Region = "",
    [switch]$Rollback
)

$ErrorActionPreference = "Continue"

if ($Action -eq "list") {
    Write-Host "=== IKE Scenarios ==="
    Write-Host "  psk-mismatch             - Inject wrong pre-shared key"
    Write-Host "  dpd-timeout              - Block IKE ports (UDP 500/4500) to trigger DPD timeout"
    Write-Host "  proposal-mismatch        - Set incompatible IKE/ESP proposals"
    Write-Host "  traffic-selector         - Change local subnet to cause TS mismatch"
    Write-Host "  tunnel-down              - Bring down both IPsec tunnels"
    Write-Host ""
    Write-Host "=== BGP Scenarios ==="
    Write-Host "  bgp-down                 - Stop BGP daemon"
    Write-Host "  bgp-asn-mismatch         - Change local ASN from 65000 to 65999"
    Write-Host "  bgp-hold-timer           - Block TCP 179 to prevent BGP keepalives"
    Write-Host "  bgp-route-withdraw       - Withdraw 172.16.0.0/16 route advertisement"
    Write-Host ""
    Write-Host "=== Throughput Scenario (run last) ==="
    Write-Host "  throughput-degradation   - Add 1000ms delay + 99% packet loss on non-BGP traffic"
    Write-Host ""
    Write-Host "Usage:"
    Write-Host "  .\inject-failure.ps1 <scenario> -KeyFile <path>"
    Write-Host "  .\inject-failure.ps1 <scenario> -KeyFile <path> -Rollback"
    Write-Host "  .\inject-failure.ps1 status -KeyFile <path>"
    exit 0
}

if ([string]::IsNullOrEmpty($KeyFile) -or -not (Test-Path $KeyFile)) {
    Write-Host "ERROR: -KeyFile required (valid path)" -ForegroundColor Red
    exit 1
}

if ([string]::IsNullOrEmpty($Region)) {
    $Region = $env:AWS_DEFAULT_REGION
    if ([string]::IsNullOrEmpty($Region)) { $Region = $env:AWS_REGION }
    if ([string]::IsNullOrEmpty($Region)) { $Region = aws configure get region 2>$null }
}
if ([string]::IsNullOrEmpty($Region)) {
    Write-Host "ERROR: -Region required (or set via 'aws configure' or AWS_DEFAULT_REGION)" -ForegroundColor Red
    exit 1
}

$stack = "VpnDemoStack-$Region"
$cgwEip = aws cloudformation describe-stacks --stack-name $stack --region $Region `
    --query "Stacks[0].Outputs[?OutputKey=='CgwPublicIp'].OutputValue" --output text --no-cli-pager

$sshOpts = @("-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-i", $KeyFile)
$sshTarget = "ec2-user@$cgwEip"

if ($Action -eq "status") {
    ssh @sshOpts $sshTarget "sudo /opt/vpn-demo/status"
    exit 0
}

# Determine dedicated alarm for special scenarios
$alarm = ""
if ($Action -eq "throughput-degradation") { $alarm = "vpn-demo-throughput-drop" }
elseif ($Action -eq "bgp-route-withdraw") { $alarm = "vpn-demo-route-withdrawn" }

if ($Rollback) {
    ssh @sshOpts $sshTarget "sudo /opt/vpn-demo/rollback $Action"
    if (-not [string]::IsNullOrEmpty($alarm)) {
        aws cloudwatch disable-alarm-actions --alarm-names $alarm --region $Region --no-cli-pager
        Write-Host "Disabled alarm: $alarm"
    }

    # Post-rollback health check
    Write-Host ""
    Write-Host "Verifying recovery..."
    Start-Sleep -Seconds 5
    ssh @sshOpts $sshTarget "sudo /opt/vpn-demo/status"
    Write-Host ""
    Write-Host "=== CloudWatch Alarms ==="
    $alarmOutput = aws cloudwatch describe-alarms --alarm-name-prefix vpn-demo `
        --query 'MetricAlarms[].{Name:AlarmName,State:StateValue}' `
        --output table --region $Region --no-cli-pager 2>$null
    Write-Host $alarmOutput
    if ($alarmOutput -match "ALARM") {
        Write-Host ""
        Write-Host "WARNING: Some alarms are still recovering - wait for all alarms to show OK before injecting the next scenario." -ForegroundColor Yellow
    }
} else {
    # Pre-inject safety checks
    $warnings = ""

    $cgwStatus = ssh @sshOpts $sshTarget "sudo /opt/vpn-demo/status" 2>$null
    $establishedCount = ($cgwStatus | Select-String "ESTABLISHED").Count
    $tunnel1Ok = ($cgwStatus | Select-String "Tunnel1: reachable").Count
    $tunnel2Ok = ($cgwStatus | Select-String "Tunnel2: reachable").Count
    if ($establishedCount -lt 2 -or $tunnel1Ok -lt 1 -or $tunnel2Ok -lt 1) {
        $warnings += "  WARNING: CGW: not all tunnels are healthy`n"
    }

    $bgpEstablished = ($cgwStatus | Select-String "Establ").Count
    $bgpNotRunning = ($cgwStatus | Select-String "GoBGP not running").Count
    if ($bgpNotRunning -eq 0 -and $bgpEstablished -lt 2) {
        $warnings += "  WARNING: CGW: not all BGP peers are established`n"
    }

    $alarmsFiring = aws cloudwatch describe-alarms --alarm-name-prefix vpn-demo `
        --state-value ALARM --query 'MetricAlarms[].AlarmName' --output text `
        --region $Region --no-cli-pager 2>$null
    if (-not [string]::IsNullOrEmpty($alarmsFiring)) {
        $warnings += "  WARNING: Alarms still firing: $alarmsFiring`n"
    }

    if (-not [string]::IsNullOrEmpty($warnings)) {
        Write-Host ""
        Write-Host "Pre-inject checks found issues:" -ForegroundColor Yellow
        Write-Host $warnings
        $confirm = Read-Host "Continue anyway? (y/N)"
        if ($confirm -ne "y" -and $confirm -ne "Y") {
            Write-Host "Aborted."
            exit 0
        }
    }

    if (-not [string]::IsNullOrEmpty($alarm)) {
        aws cloudwatch enable-alarm-actions --alarm-names $alarm --region $Region --no-cli-pager
        Write-Host "Enabled alarm: $alarm"
    }
    ssh @sshOpts $sshTarget "sudo /opt/vpn-demo/inject $Action"
}
