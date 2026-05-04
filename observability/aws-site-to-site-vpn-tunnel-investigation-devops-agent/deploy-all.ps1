# deploy-all.ps1 — Deploy VPN demo infrastructure via CDK + configure CGW + create alarms
#
# Usage:
#   .\deploy-all.ps1 -KeyFile <path> [-KeyPair <name>] [-Routing bgp|static]
#                    [-WebhookUrl <url>] [-WebhookSecret <secret>]

param(
    [Parameter(Mandatory=$true)]
    [string]$KeyFile,
    [string]$KeyPair = "",
    [ValidateSet("bgp","static")]
    [string]$Routing = "bgp",
    [string]$WebhookUrl = "",
    [string]$WebhookSecret = "",
    [string]$SshCidr = "",
    [switch]$SshOpen
)

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path $KeyFile)) {
    Write-Host "ERROR: key file not found: $KeyFile" -ForegroundColor Red
    exit 1
}

# =============================================================================
Write-Host "==> Step 1: Check prerequisites..." -ForegroundColor Cyan
# =============================================================================
& "$ScriptDir/../../shared/scripts/check-prerequisites.ps1"
$region = $global:AWS_REGION

if ([string]::IsNullOrEmpty($KeyPair)) {
    Write-Host "Available key pairs in ${region}:"
    aws ec2 describe-key-pairs --region $region --query 'KeyPairs[].KeyName' --output table --no-cli-pager
    $KeyPair = Read-Host "Enter key pair name"
    if ([string]::IsNullOrEmpty($KeyPair)) {
        Write-Host "ERROR: key pair required" -ForegroundColor Red
        exit 1
    }
}

# Resolve SSH CIDR
if ($SshOpen) {
    $SshCidr = "0.0.0.0/0"
    Write-Host "  WARNING: SSH open to 0.0.0.0/0 (not recommended for production)" -ForegroundColor Yellow
} elseif ([string]::IsNullOrEmpty($SshCidr)) {
    try {
        $myIp = (Invoke-WebRequest -Uri "https://checkip.amazonaws.com" -TimeoutSec 5 -UseBasicParsing).Content.Trim()
        $SshCidr = "$myIp/32"
        Write-Host "  SSH restricted to your IP: $SshCidr"
    } catch {
        $SshCidr = "0.0.0.0/0"
        Write-Host "  WARNING: Could not detect your IP - SSH open to 0.0.0.0/0" -ForegroundColor Yellow
    }
}

# =============================================================================
Write-Host ""
Write-Host "==> Step 2: Deploy VPN infrastructure via CDK..." -ForegroundColor Cyan
# =============================================================================
$stackName = "VpnDemoStack-$region"
# Verify CDK dependencies
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Node.js is required for CDK. Install from https://nodejs.org" -ForegroundColor Red
    exit 1
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Python 3 is required for CDK. Install from https://python.org" -ForegroundColor Red
    exit 1
}

$cdkDir = "$ScriptDir/infrastructure/cdk"

# Set PYTHONPATH so CDK app can import shared/utils
$repoRoot = (Resolve-Path "$ScriptDir/../..").Path
$env:PYTHONPATH = $repoRoot

# Install CDK dependencies in virtual environment
if (-not (Test-Path "$cdkDir\.venv\Scripts\Activate.ps1")) {
    python -m venv "$cdkDir\.venv"
}
& "$cdkDir\.venv\Scripts\Activate.ps1"
pip install -r "$cdkDir\requirements.txt"

# Bootstrap CDK (idempotent)
$accountId = aws sts get-caller-identity --query Account --output text --no-cli-pager
Push-Location $cdkDir
npx -y cdk bootstrap "aws://$accountId/$region" --no-cli-pager --app ".venv\Scripts\python.exe app.py"

# Deploy with context params (direct call, no Invoke-Expression)
$cdkArgs = @("deploy", $stackName, "--require-approval", "never", "--no-cli-pager",
    "--app", ".venv\Scripts\python.exe app.py",
    "--context", "keyPairName=$KeyPair", "--context", "routingType=$Routing", "--context", "sshCidr=$SshCidr")
if (-not [string]::IsNullOrEmpty($WebhookUrl)) {
    $cdkArgs += @("--context", "webhookUrl=$WebhookUrl")
}
if (-not [string]::IsNullOrEmpty($WebhookSecret)) {
    $cdkArgs += @("--context", "webhookSecret=$WebhookSecret")
}
npx -y cdk @cdkArgs
Pop-Location

# =============================================================================
Write-Host ""
Write-Host "==> Step 3: Fetch stack outputs..." -ForegroundColor Cyan
# =============================================================================
function Get-StackOutput($key) {
    aws cloudformation describe-stacks --region $region --stack-name $stackName `
        --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" --output text --no-cli-pager
}

$vpnId = Get-StackOutput "VpnConnectionId"
$cgwEip = Get-StackOutput "CgwPublicIp"
$cloudPrivateIp = Get-StackOutput "CloudInstancePrivateIp"
$snsTopicArn = Get-StackOutput "AlarmSnsTopicArn"

Write-Host "  VPN: $vpnId | CGW: $cgwEip | Cloud: $cloudPrivateIp"

$sshOpts = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i $KeyFile"
$sshUser = "ec2-user"

# =============================================================================
Write-Host ""
Write-Host "==> Step 4: Wait for SSH on $cgwEip..." -ForegroundColor Cyan
# =============================================================================
for ($i = 1; $i -le 30; $i++) {
    $result = ssh $sshOpts.Split(" ") -o ConnectTimeout=5 "${sshUser}@${cgwEip}" "true" 2>$null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 10
    Write-Host "  Waiting... $($i*10)s"
}

# =============================================================================
Write-Host "==> Step 5: Wait for UserData to complete..." -ForegroundColor Cyan
# =============================================================================
for ($i = 1; $i -le 30; $i++) {
    ssh $sshOpts.Split(" ") "${sshUser}@${cgwEip}" "grep -q USERDATA_COMPLETE /var/log/vpn-userdata.log 2>/dev/null" 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Host "  Done."; break }
    Start-Sleep -Seconds 10
    Write-Host "  Packages installing... ($i/30)"
}
if ($i -gt 30) {
    Write-Host "ERROR: UserData did not complete after 5 minutes. Check /var/log/vpn-userdata.log on the CGW." -ForegroundColor Red
    exit 1
}

# =============================================================================
Write-Host "==> Step 6: Fetch VPN tunnel details..." -ForegroundColor Cyan
# =============================================================================
$vpnJson = aws ec2 describe-vpn-connections --region $region `
    --vpn-connection-ids $vpnId --query 'VpnConnections[0]' --output json --no-cli-pager | ConvertFrom-Json

$t1Ip = $vpnJson.Options.TunnelOptions[0].OutsideIpAddress
$t2Ip = $vpnJson.Options.TunnelOptions[1].OutsideIpAddress
$t1Psk = $vpnJson.Options.TunnelOptions[0].PreSharedKey
$t2Psk = $vpnJson.Options.TunnelOptions[1].PreSharedKey

Write-Host "  Tunnel 1: $t1Ip | Tunnel 2: $t2Ip"

# =============================================================================
Write-Host "==> Step 7: Configure Libreswan on CGW..." -ForegroundColor Cyan
# =============================================================================
$libreswanScript = @"
set -e
cat > /etc/ipsec.d/vpn-demo.conf <<CONF
conn tunnel1
  authby=secret
  auto=start
  left=%defaultroute
  leftid=${cgwEip}
  right=${t1Ip}
  rightid=${t1Ip}
  leftsubnet=0.0.0.0/0
  rightsubnet=0.0.0.0/0
  type=tunnel
  ikelifetime=8h
  salifetime=1h
  ikev2=yes
  mark=100/0xffffffff
  vti-interface=vti1
  vti-routing=no
  dpddelay=10
  dpdtimeout=30
  dpdaction=restart

conn tunnel2
  authby=secret
  auto=start
  left=%defaultroute
  leftid=${cgwEip}
  right=${t2Ip}
  rightid=${t2Ip}
  leftsubnet=0.0.0.0/0
  rightsubnet=0.0.0.0/0
  type=tunnel
  ikelifetime=8h
  salifetime=1h
  ikev2=yes
  mark=200/0xffffffff
  vti-interface=vti2
  vti-routing=no
  dpddelay=10
  dpdtimeout=30
  dpdaction=restart
CONF

cat > /etc/ipsec.d/vpn-demo.secrets <<SECRETS
${cgwEip} ${t1Ip} : PSK "${t1Psk}"
${cgwEip} ${t2Ip} : PSK "${t2Psk}"
SECRETS
chmod 600 /etc/ipsec.d/vpn-demo.secrets

sysctl -w net.ipv4.conf.default.rp_filter=0
sysctl -w net.ipv4.conf.all.rp_filter=0

systemctl enable ipsec
systemctl restart ipsec
sleep 10

ip addr add 169.254.10.2/30 dev vti1 2>/dev/null || true
ip link set vti1 up mtu 1400
sysctl -w net.ipv4.conf.vti1.disable_policy=1
sysctl -w net.ipv4.conf.vti1.rp_filter=0

ip addr add 169.254.10.6/30 dev vti2 2>/dev/null || true
ip link set vti2 up mtu 1400
sysctl -w net.ipv4.conf.vti2.disable_policy=1
sysctl -w net.ipv4.conf.vti2.rp_filter=0

echo "=== IPsec ==="
ipsec whack --status 2>&1 | grep -E "ESTABLISHED|Total"
ping -c 1 -W 3 169.254.10.1 >/dev/null && echo "Tunnel1: OK" || echo "Tunnel1: FAIL"
ping -c 1 -W 3 169.254.10.5 >/dev/null && echo "Tunnel2: OK" || echo "Tunnel2: FAIL"
"@

$libreswanScript | ssh $sshOpts.Split(" ") "${sshUser}@${cgwEip}" "sed 's/\r$//' | sudo bash -s"

# =============================================================================
if ($Routing -eq "bgp") {
    Write-Host "==> Step 8: Configure GoBGP..." -ForegroundColor Cyan
    ssh $sshOpts.Split(" ") "${sshUser}@${cgwEip}" "test -f /usr/local/bin/gobgpd" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: gobgpd not found on CGW. UserData may have failed." -ForegroundColor Red
        exit 1
    }
    $bgpScript = @"
set -e
cat > /etc/gobgp.toml <<TOML
[global.config]
  as = 65000
  router-id = "${cgwEip}"

[[neighbors]]
  [neighbors.config]
    neighbor-address = "169.254.10.1"
    peer-as = 64512
  [neighbors.timers.config]
    hold-time = 30
    keepalive-interval = 10
  [[neighbors.afi-safis]]
    [neighbors.afi-safis.config]
      afi-safi-name = "ipv4-unicast"

[[neighbors]]
  [neighbors.config]
    neighbor-address = "169.254.10.5"
    peer-as = 64512
  [neighbors.timers.config]
    hold-time = 30
    keepalive-interval = 10
  [[neighbors.afi-safis]]
    [neighbors.afi-safis.config]
      afi-safi-name = "ipv4-unicast"
TOML

cat > /etc/systemd/system/gobgpd.service <<SVC
[Unit]
Description=GoBGP Daemon
After=network.target ipsec.service

[Service]
ExecStart=/usr/local/bin/gobgpd -f /etc/gobgp.toml -r
ExecStartPost=/bin/bash -c 'sleep 5 && /usr/local/bin/gobgp global rib add 172.16.0.0/16 origin igp -a ipv4 && ip route replace 10.0.0.0/16 via 169.254.10.1 dev vti1'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVC

systemctl daemon-reload
systemctl enable gobgpd
systemctl start gobgpd
sleep 15

echo "=== BGP ==="
/usr/local/bin/gobgp neighbor
"@
    $bgpScript | ssh $sshOpts.Split(" ") "${sshUser}@${cgwEip}" "sed 's/\r$//' | sudo bash -s"
} else {
    Write-Host "==> Step 8: Adding static route..." -ForegroundColor Cyan
    ssh $sshOpts.Split(" ") "${sshUser}@${cgwEip}" "sudo ip route add 10.0.0.0/16 via 169.254.10.1 dev vti1"
}

# =============================================================================
Write-Host "==> Step 9: Install inject/rollback scripts on CGW..." -ForegroundColor Cyan
# =============================================================================
scp $sshOpts.Split(" ") "$ScriptDir/cgw-scripts/*" "${sshUser}@${cgwEip}:/tmp/"
ssh $sshOpts.Split(" ") "${sshUser}@${cgwEip}" "sudo mkdir -p /opt/vpn-demo && for f in inject rollback status list; do sudo sed 's/\r$//' /tmp/`$f > /opt/vpn-demo/`$f; done && sudo chmod +x /opt/vpn-demo/*"

# =============================================================================
Write-Host "==> Step 10: Create per-tunnel CloudWatch alarms..." -ForegroundColor Cyan
# =============================================================================
foreach ($tnum in 1, 2) {
    $tip = if ($tnum -eq 1) { $t1Ip } else { $t2Ip }
    aws cloudwatch put-metric-alarm --region $region `
        --alarm-name "vpn-demo-tunnel${tnum}-down" `
        --namespace "AWS/VPN" --metric-name "TunnelState" `
        --dimensions "Name=VpnId,Value=$vpnId" "Name=TunnelIpAddress,Value=$tip" `
        --statistic Maximum --period 60 --evaluation-periods 1 `
        --threshold 1 --comparison-operator LessThanThreshold `
        --treat-missing-data breaching --alarm-actions $snsTopicArn --no-cli-pager
    Write-Host "  Created: vpn-demo-tunnel${tnum}-down"
}

$metricsJson = '[{"Id":"m1","MetricStat":{"Metric":{"Namespace":"AWS/VPN","MetricName":"TunnelDataIn","Dimensions":[{"Name":"VpnId","Value":"' + $vpnId + '"}]},"Period":300,"Stat":"Sum"},"ReturnData":false},{"Id":"m2","MetricStat":{"Metric":{"Namespace":"AWS/VPN","MetricName":"TunnelDataOut","Dimensions":[{"Name":"VpnId","Value":"' + $vpnId + '"}]},"Period":300,"Stat":"Sum"},"ReturnData":false},{"Id":"throughput","Expression":"(m1+m2)*8/300","Label":"VPN Throughput bps","ReturnData":true}]'
$metricsFile = Join-Path $env:TEMP "vpn-demo-metrics.json"
$metricsJson | Set-Content -Path $metricsFile -Encoding ASCII -NoNewline
aws cloudwatch put-metric-alarm --region $region `
    --alarm-name vpn-demo-throughput-drop `
    --metrics "file://$($metricsFile.Replace('\', '/'))" `
    --comparison-operator LessThanThreshold --threshold 100 `
    --evaluation-periods 1 --datapoints-to-alarm 1 `
    --treat-missing-data breaching --alarm-actions $snsTopicArn --no-cli-pager
aws cloudwatch disable-alarm-actions --region $region --alarm-names vpn-demo-throughput-drop --no-cli-pager
Write-Host "  Created: vpn-demo-throughput-drop (actions disabled)"

$vpnLogGroup = Get-StackOutput "VpnLogGroupName"
aws logs put-metric-filter --region $region `
    --log-group-name $vpnLogGroup `
    --filter-name vpn-demo-route-withdrawn `
    --filter-pattern '"WITHDRAWN"' `
    --metric-transformations metricName=RouteWithdrawn,metricNamespace=VPNDemo,metricValue=1,defaultValue=0 --no-cli-pager

aws cloudwatch put-metric-alarm --region $region `
    --alarm-name vpn-demo-route-withdrawn `
    --namespace VPNDemo --metric-name RouteWithdrawn `
    --statistic Sum --period 60 --evaluation-periods 1 `
    --threshold 1 --comparison-operator GreaterThanOrEqualToThreshold `
    --treat-missing-data notBreaching --alarm-actions $snsTopicArn --no-cli-pager
aws cloudwatch disable-alarm-actions --region $region --alarm-names vpn-demo-route-withdrawn --no-cli-pager
Write-Host "  Created: vpn-demo-route-withdrawn (actions disabled)"

# =============================================================================
Write-Host "==> Step 11: Start baseline ping traffic..." -ForegroundColor Cyan
# =============================================================================
$cgwPrivateIp = ssh $sshOpts.Split(" ") "${sshUser}@${cgwEip}" "ip -4 addr show ens5 | grep inet | awk '{print `$2}' | cut -d/ -f1"
ssh $sshOpts.Split(" ") "${sshUser}@${cgwEip}" "nohup ping -I $cgwPrivateIp $cloudPrivateIp -i 0.5 > /dev/null 2>&1 &"
Write-Host "  Baseline ping: $cgwPrivateIp -> $cloudPrivateIp (every 0.5s)"

# =============================================================================
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  VPN Demo Ready" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Region           : $region" -ForegroundColor Cyan
Write-Host "  VPN Connection   : $vpnId" -ForegroundColor Cyan
Write-Host "  CGW (SSH)        : ssh -i $KeyFile ${sshUser}@$cgwEip" -ForegroundColor Cyan
Write-Host "  Cloud Instance   : $cloudPrivateIp" -ForegroundColor Cyan
Write-Host "  Tunnel 1         : $t1Ip" -ForegroundColor Cyan
Write-Host "  Tunnel 2         : $t2Ip" -ForegroundColor Cyan
Write-Host "  Routing          : $Routing" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Commands on CGW:"
Write-Host "    sudo /opt/vpn-demo/list"
Write-Host "    sudo /opt/vpn-demo/status"
Write-Host "    sudo /opt/vpn-demo/inject <scenario>"
Write-Host "    sudo /opt/vpn-demo/rollback <scenario>"
Write-Host ""
Write-Host "  Or from your laptop:"
Write-Host "    .\scripts\inject-failure.ps1 <scenario> -KeyFile $KeyFile"
Write-Host "    .\scripts\inject-failure.ps1 <scenario> -KeyFile $KeyFile -Rollback"
Write-Host "========================================" -ForegroundColor Green
