#!/usr/bin/env bash
# inject-failure.sh — SSH wrapper to run /opt/vpn-demo/ scripts on the CGW
# Usage:
#   ./inject-failure.sh <scenario> <stack-name> <region> --key-file <path> [--rollback]
#   ./inject-failure.sh status <stack-name> <region> --key-file <path>
#   ./inject-failure.sh list
set -euo pipefail

[[ "${1:-}" == "list" ]] && exec "$(dirname "$0")/../cgw-scripts/list"

ACTION="${1:-}"; STACK="${2:-}"; REGION="${3:-}"
shift 3 2>/dev/null || { echo "Usage: $0 <scenario|status> <stack-name> <region> --key-file <path> [--rollback]"; exit 1; }

KEY_FILE=""; ROLLBACK=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --key-file) KEY_FILE="$2"; shift 2;;
    --rollback) ROLLBACK="yes"; shift;;
    *) echo "Unknown: $1"; exit 1;;
  esac
done
[[ -z "$KEY_FILE" || ! -f "$KEY_FILE" ]] && echo "ERROR: --key-file required (valid path)" && exit 1

CGW_EIP=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='CgwPublicIp'].OutputValue" --output text)
SSH="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i $KEY_FILE ec2-user@${CGW_EIP}"

case "$ACTION" in
  status) $SSH "sudo /opt/vpn-demo/status" ;;
  *) [[ "$ROLLBACK" == "yes" ]] && $SSH "sudo /opt/vpn-demo/rollback $ACTION" || $SSH "sudo /opt/vpn-demo/inject $ACTION" ;;
esac
