#!/usr/bin/env bash
# inject-failure.sh — SSH wrapper to run /opt/vpn-demo/ scripts on the CGW
# Usage:
#   ./inject-failure.sh <scenario> --key-file <path> [--region <region>] [--rollback]
#   ./inject-failure.sh status --key-file <path> [--region <region>]
#   ./inject-failure.sh list
set -euo pipefail

[[ "${1:-}" == "list" ]] && exec "$(dirname "$0")/../cgw-scripts/list"

ACTION="${1:-}"
[[ -z "$ACTION" ]] && { echo "Usage: $0 <scenario|status|list> --key-file <path> [--region <region>] [--rollback]"; exit 1; }
shift

KEY_FILE=""
REGION=""
ROLLBACK=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --key-file) KEY_FILE="$2"; shift 2;;
    --region) REGION="$2"; shift 2;;
    --rollback) ROLLBACK="yes"; shift;;
    *) echo "Unknown: $1"; exit 1;;
  esac
done

[[ -z "$KEY_FILE" || ! -f "$KEY_FILE" ]] && echo "ERROR: --key-file required (valid path)" && exit 1
[[ -z "$REGION" ]] && REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-$(aws configure get region 2>/dev/null)}}"
[[ -z "$REGION" ]] && { echo "ERROR: --region required (or set via 'aws configure' or AWS_DEFAULT_REGION)"; exit 1; }

STACK="VpnDemoStack-$REGION"

CGW_EIP=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='CgwPublicIp'].OutputValue" --output text --no-cli-pager)
SSH="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i $KEY_FILE ec2-user@${CGW_EIP}"

case "$ACTION" in
  status)
    $SSH "sudo /opt/vpn-demo/status"
    echo ""
    echo "=== CloudWatch Alarms ==="
    aws cloudwatch describe-alarms --alarm-name-prefix vpn-demo \
      --query 'MetricAlarms[].{Name:AlarmName,State:StateValue}' \
      --output table --region "$REGION" --no-cli-pager 2>/dev/null || true
    ;;
  *)
    # Enable dedicated alarms before inject, disable after rollback
    if [[ "$ACTION" == "throughput-degradation" ]]; then
      ALARM="vpn-demo-throughput-drop"
    elif [[ "$ACTION" == "bgp-route-withdraw" ]]; then
      ALARM="vpn-demo-route-withdrawn"
    else
      ALARM=""
    fi

    if [[ "$ROLLBACK" == "yes" ]]; then
      $SSH "sudo /opt/vpn-demo/rollback $ACTION"
      [[ -n "$ALARM" ]] && aws cloudwatch disable-alarm-actions --alarm-names "$ALARM" --region "$REGION" --no-cli-pager && echo "Disabled alarm: $ALARM"

      # Post-rollback health check
      echo ""
      echo "Verifying recovery..."
      sleep 5
      $SSH "sudo /opt/vpn-demo/status"
      echo ""
      echo "=== CloudWatch Alarms ==="
      ALARM_OUTPUT=$(aws cloudwatch describe-alarms --alarm-name-prefix vpn-demo \
        --query 'MetricAlarms[].{Name:AlarmName,State:StateValue}' \
        --output table --region "$REGION" --no-cli-pager 2>/dev/null) || true
      echo "$ALARM_OUTPUT"
      if echo "$ALARM_OUTPUT" | grep -q "ALARM"; then
        echo ""
        echo "⚠ Some alarms are still recovering — wait for all alarms to show OK before injecting the next scenario."
      fi
    else
      # Pre-inject safety checks
      WARNINGS=""

      # Check CGW tunnel health
      CGW_STATUS=$($SSH "sudo /opt/vpn-demo/status" 2>/dev/null) || true
      ESTABLISHED_COUNT=$(echo "$CGW_STATUS" | grep -c "ESTABLISHED" || true)
      TUNNEL1_OK=$(echo "$CGW_STATUS" | grep -c "Tunnel1: reachable" || true)
      TUNNEL2_OK=$(echo "$CGW_STATUS" | grep -c "Tunnel2: reachable" || true)
      if [[ "$ESTABLISHED_COUNT" -lt 2 || "$TUNNEL1_OK" -lt 1 || "$TUNNEL2_OK" -lt 1 ]]; then
        WARNINGS="${WARNINGS}  ⚠ CGW: not all tunnels are healthy\n"
      fi

      # Check BGP (only if routing is BGP)
      BGP_ESTABLISHED=$(echo "$CGW_STATUS" | grep -c "Establ" || true)
      BGP_RUNNING=$(echo "$CGW_STATUS" | grep -c "GoBGP not running" || true)
      if [[ "$BGP_RUNNING" -eq 0 && "$BGP_ESTABLISHED" -lt 2 ]]; then
        WARNINGS="${WARNINGS}  ⚠ CGW: not all BGP peers are established\n"
      fi

      # Check CloudWatch alarms
      ALARMS_FIRING=$(aws cloudwatch describe-alarms --alarm-name-prefix vpn-demo \
        --state-value ALARM --query 'MetricAlarms[].AlarmName' --output text \
        --region "$REGION" --no-cli-pager 2>/dev/null) || true
      if [[ -n "$ALARMS_FIRING" ]]; then
        WARNINGS="${WARNINGS}  ⚠ Alarms still firing: ${ALARMS_FIRING}\n"
      fi

      if [[ -n "$WARNINGS" ]]; then
        echo ""
        echo "Pre-inject checks found issues:"
        echo -e "$WARNINGS"
        read -rp "Continue anyway? (y/N) " CONFIRM
        [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]] && echo "Aborted." && exit 0
      fi

      [[ -n "$ALARM" ]] && aws cloudwatch enable-alarm-actions --alarm-names "$ALARM" --region "$REGION" --no-cli-pager && echo "Enabled alarm: $ALARM"
      $SSH "sudo /opt/vpn-demo/inject $ACTION"
    fi
    ;;
esac
