#!/usr/bin/env bash
# setup-cgw.sh — Configure the CGW instance after stack deployment
# Configures libreswan + GoBGP, creates per-tunnel alarms, installs inject scripts
#
# Usage: ./setup-cgw.sh --key-file <path> [--region <region>]
set -euo pipefail

KEY_FILE=""
REGION=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --key-file) KEY_FILE="$2"; shift 2;;
    --region) REGION="$2"; shift 2;;
    -h|--help) echo "Usage: $0 --key-file <path> [--region <region>]"; exit 0;;
    *) echo "Unknown option: $1"; exit 1;;
  esac
done

[[ -z "$KEY_FILE" ]] && { echo "ERROR: --key-file is required"; exit 1; }
[[ ! -f "$KEY_FILE" ]] && { echo "ERROR: key file not found: $KEY_FILE"; exit 1; }
[[ -z "$REGION" ]] && REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-$(aws configure get region 2>/dev/null)}}"
[[ -z "$REGION" ]] && { echo "ERROR: --region required (or set via 'aws configure' or AWS_DEFAULT_REGION)"; exit 1; }

STACK_NAME="VpnDemoStack-$REGION"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i $KEY_FILE"
SSH_USER="ec2-user"

# =============================================================================
echo "==> Fetching stack outputs..."
get_output() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text --no-cli-pager
}

VPN_ID=$(get_output VpnConnectionId)
CGW_EIP=$(get_output CgwPublicIp)
CLOUD_PRIVATE_IP=$(get_output CloudInstancePrivateIp)
SNS_TOPIC_ARN=$(get_output AlarmSnsTopicArn)
ROUTING=$(get_output RoutingType)

echo "  VPN: $VPN_ID | CGW: $CGW_EIP | Cloud: $CLOUD_PRIVATE_IP | Routing: $ROUTING"

run_ssh() { ssh $SSH_OPTS "${SSH_USER}@${CGW_EIP}" "$1"; }

# =============================================================================
echo "==> Waiting for SSH on $CGW_EIP..."
for i in {1..30}; do
  ssh $SSH_OPTS -o ConnectTimeout=5 "${SSH_USER}@${CGW_EIP}" "true" 2>/dev/null && break
  sleep 10; echo "  Waiting... $((i*10))s"
done

# =============================================================================
echo "==> Waiting for UserData to complete..."
for i in {1..30}; do
  run_ssh "grep -q USERDATA_COMPLETE /var/log/vpn-userdata.log 2>/dev/null" && echo "  Done." && break
  sleep 10; echo "  Packages installing... ($i/30)"
done

# =============================================================================
echo "==> Fetching VPN tunnel details..."
VPN_JSON=$(aws ec2 describe-vpn-connections --region "$REGION" \
  --vpn-connection-ids "$VPN_ID" --query 'VpnConnections[0]' --output json --no-cli-pager)

T1_IP=$(echo "$VPN_JSON" | jq -r '.Options.TunnelOptions[0].OutsideIpAddress')
T2_IP=$(echo "$VPN_JSON" | jq -r '.Options.TunnelOptions[1].OutsideIpAddress')
T1_PSK=$(echo "$VPN_JSON" | jq -r '.Options.TunnelOptions[0].PreSharedKey')
T2_PSK=$(echo "$VPN_JSON" | jq -r '.Options.TunnelOptions[1].PreSharedKey')

echo "  Tunnel 1: $T1_IP | Tunnel 2: $T2_IP"

# =============================================================================
echo "==> Configuring Libreswan on CGW..."
run_ssh "sudo bash -s" <<EOF
set -e

cat > /etc/ipsec.d/vpn-demo.conf <<CONF
conn tunnel1
  authby=secret
  auto=start
  left=%defaultroute
  leftid=${CGW_EIP}
  right=${T1_IP}
  rightid=${T1_IP}
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
  leftid=${CGW_EIP}
  right=${T2_IP}
  rightid=${T2_IP}
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
${CGW_EIP} ${T1_IP} : PSK "${T1_PSK}"
${CGW_EIP} ${T2_IP} : PSK "${T2_PSK}"
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
EOF

# =============================================================================
if [[ "$ROUTING" == "bgp" ]]; then
  echo "==> Configuring GoBGP..."
  run_ssh "sudo bash -s" <<EOF
set -e

cat > /etc/gobgp.toml <<TOML
[global.config]
  as = 65000
  router-id = "${CGW_EIP}"

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
EOF
else
  echo "==> Adding static route..."
  run_ssh "sudo ip route add 10.0.0.0/16 via 169.254.10.1 dev vti1"
fi

# =============================================================================
echo "==> Installing inject/rollback scripts on CGW..."
scp $SSH_OPTS "$SCRIPT_DIR/cgw-scripts/"* "${SSH_USER}@${CGW_EIP}:/tmp/"
run_ssh "sudo mkdir -p /opt/vpn-demo && sudo cp /tmp/inject /tmp/rollback /tmp/status /tmp/list /opt/vpn-demo/ && sudo chmod +x /opt/vpn-demo/*"

# =============================================================================
echo "==> Creating per-tunnel CloudWatch alarms..."
for TNUM in 1 2; do
  TIP=$([[ $TNUM -eq 1 ]] && echo "$T1_IP" || echo "$T2_IP")
  aws cloudwatch put-metric-alarm --region "$REGION" \
    --alarm-name "vpn-demo-tunnel${TNUM}-down" \
    --namespace "AWS/VPN" --metric-name "TunnelState" \
    --dimensions "Name=VpnId,Value=$VPN_ID" "Name=TunnelIpAddress,Value=$TIP" \
    --statistic Maximum --period 60 --evaluation-periods 1 \
    --threshold 1 --comparison-operator LessThanThreshold \
    --treat-missing-data breaching --alarm-actions "$SNS_TOPIC_ARN" --no-cli-pager
  echo "  Created: vpn-demo-tunnel${TNUM}-down"
done

aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name vpn-demo-throughput-drop \
  --metrics '[{"Id":"m1","MetricStat":{"Metric":{"Namespace":"AWS/VPN","MetricName":"TunnelDataIn","Dimensions":[{"Name":"VpnId","Value":"'"$VPN_ID"'"}]},"Period":300,"Stat":"Sum"},"ReturnData":false},{"Id":"m2","MetricStat":{"Metric":{"Namespace":"AWS/VPN","MetricName":"TunnelDataOut","Dimensions":[{"Name":"VpnId","Value":"'"$VPN_ID"'"}]},"Period":300,"Stat":"Sum"},"ReturnData":false},{"Id":"throughput","Expression":"(m1+m2)*8/300","Label":"VPN Throughput bps","ReturnData":true}]' \
  --comparison-operator LessThanThreshold --threshold 100 \
  --evaluation-periods 1 --datapoints-to-alarm 1 \
  --treat-missing-data breaching --alarm-actions "$SNS_TOPIC_ARN" --no-cli-pager
aws cloudwatch disable-alarm-actions --region "$REGION" --alarm-names vpn-demo-throughput-drop --no-cli-pager
echo "  Created: vpn-demo-throughput-drop (actions disabled — enable only for throughput-degradation scenario)"

VPN_LOG_GROUP=$(get_output VpnLogGroupName)
aws logs put-metric-filter --region "$REGION" \
  --log-group-name "$VPN_LOG_GROUP" \
  --filter-name vpn-demo-route-withdrawn \
  --filter-pattern '"WITHDRAWN"' \
  --metric-transformations metricName=RouteWithdrawn,metricNamespace=VPNDemo,metricValue=1,defaultValue=0 --no-cli-pager

aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name vpn-demo-route-withdrawn \
  --namespace VPNDemo --metric-name RouteWithdrawn \
  --statistic Sum --period 60 --evaluation-periods 1 \
  --threshold 1 --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching --alarm-actions "$SNS_TOPIC_ARN" --no-cli-pager
aws cloudwatch disable-alarm-actions --region "$REGION" --alarm-names vpn-demo-route-withdrawn --no-cli-pager
echo "  Created: vpn-demo-route-withdrawn (actions disabled — enable only for bgp-route-withdraw scenario)"

# =============================================================================
echo "==> Starting baseline ping traffic (for throughput alarm)..."
CGW_PRIVATE_IP=$(run_ssh "ip -4 addr show ens5 | grep inet | awk '{print \$2}' | cut -d/ -f1")
run_ssh "nohup ping -I $CGW_PRIVATE_IP $CLOUD_PRIVATE_IP -i 0.5 > /dev/null 2>&1 &"
echo "  Baseline ping: $CGW_PRIVATE_IP → $CLOUD_PRIVATE_IP (every 0.5s)"

# =============================================================================
echo ""
echo "============================================"
echo "  CGW configured — demo ready!"
echo "============================================"
echo "  SSH into CGW:  ssh -i $KEY_FILE ${SSH_USER}@$CGW_EIP"
echo ""
echo "  Commands:"
echo "    sudo /opt/vpn-demo/list              # See scenarios"
echo "    sudo /opt/vpn-demo/status            # Check tunnel/BGP state"
echo "    sudo /opt/vpn-demo/inject <scenario> # Break something"
echo "    sudo /opt/vpn-demo/rollback <scenario>"
echo "============================================"
