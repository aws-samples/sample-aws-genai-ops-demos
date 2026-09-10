#!/usr/bin/env bash
# inject-failure.sh — SSH wrapper to run /opt/aurora-demo/ scripts on the bastion.
# Usage:
#   ./inject-failure.sh <scenario> --key-file <path> [--region <region>] [--rollback]
#   ./inject-failure.sh status --key-file <path> [--region <region>]
#   ./inject-failure.sh list
set -euo pipefail

[[ "${1:-}" == "list" ]] && exec "$(dirname "$0")/../bastion-scripts/list"

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

STACK="AuroraDemoStack-$REGION"

BASTION_IP=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='BastionPublicIp'].OutputValue" --output text --no-cli-pager)
[[ -z "$BASTION_IP" || "$BASTION_IP" == "None" ]] && { echo "ERROR: could not find bastion IP. Is $STACK deployed in $REGION?"; exit 1; }

SSH="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i $KEY_FILE ec2-user@${BASTION_IP}"

# Map scenarios to dedicated alarms
case "$ACTION" in
  memory-pressure) ALARM="aurora-demo-memory-pressure";;
  replica-lag)     ALARM="aurora-demo-replica-lag";;
  *)               ALARM="";;
esac

case "$ACTION" in
  status)
    $SSH "sudo /opt/aurora-demo/status"
    echo ""
    echo "=== CloudWatch Alarms ==="
    aws cloudwatch describe-alarms --alarm-name-prefix aurora-demo \
      --query 'MetricAlarms[].{Name:AlarmName,State:StateValue}' \
      --output table --region "$REGION" --no-cli-pager 2>/dev/null || true
    ;;
  *)
    if [[ "$ROLLBACK" == "yes" ]]; then
      $SSH "sudo /opt/aurora-demo/rollback $ACTION"
      [[ -n "$ALARM" ]] && aws cloudwatch disable-alarm-actions --alarm-names "$ALARM" --region "$REGION" --no-cli-pager && echo "Disabled alarm: $ALARM"
      echo ""
      echo "Verifying recovery..."
      sleep 5
      $SSH "sudo /opt/aurora-demo/status"
    else
      # Pre-inject safety: warn if any alarm is still firing
      ALARMS_FIRING=$(aws cloudwatch describe-alarms --alarm-name-prefix aurora-demo \
        --state-value ALARM --query 'MetricAlarms[].AlarmName' --output text \
        --region "$REGION" --no-cli-pager 2>/dev/null) || true
      if [[ -n "$ALARMS_FIRING" ]]; then
        echo "⚠ Alarms still firing: $ALARMS_FIRING"
        read -rp "Continue anyway? (y/N) " CONFIRM
        [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]] && echo "Aborted." && exit 0
      fi
      [[ -n "$ALARM" ]] && aws cloudwatch enable-alarm-actions --alarm-names "$ALARM" --region "$REGION" --no-cli-pager && echo "Enabled alarm: $ALARM"
      # Launch detached (setsid) so background load generators don't hold the SSH channel open
      $SSH "sudo setsid /opt/aurora-demo/inject $ACTION </dev/null >/tmp/aurora-inject.log 2>&1 &" || true
      echo ""
      echo "Injected '$ACTION' (running in background on the bastion). Watch the alarm transition to ALARM (typically 1-3 min), then the DevOps Agent investigation."
    fi
    ;;
esac
