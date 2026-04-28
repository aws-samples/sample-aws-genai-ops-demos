#!/usr/bin/env bash
# cleanup.sh — Delete CloudWatch alarms, metric filter, and both CDK stacks.
#
# Usage: ./cleanup.sh <region>
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <region>" >&2
  exit 1
fi

REGION="$1"
VPN_STACK="VpnDemoStack-$REGION"
MCP_STACK="VpnDemoMcpServer-$REGION"

echo ">> Region: $REGION"

echo ">> Deleting CloudWatch alarms..."
aws cloudwatch delete-alarms \
  --alarm-names vpn-demo-tunnel1-down vpn-demo-tunnel2-down vpn-demo-throughput-drop vpn-demo-route-withdrawn \
  --region "$REGION" --no-cli-pager 2>/dev/null || true

echo ">> Deleting metric filter..."
aws logs delete-metric-filter \
  --log-group-name /vpn-demo/tunnel-logs \
  --filter-name vpn-demo-route-withdrawn \
  --region "$REGION" --no-cli-pager 2>/dev/null || true

echo ">> Deleting MCP server stack: $MCP_STACK ..."
aws cloudformation delete-stack --stack-name "$MCP_STACK" --region "$REGION" --no-cli-pager 2>/dev/null || true
aws cloudformation wait stack-delete-complete --stack-name "$MCP_STACK" --region "$REGION" --no-cli-pager 2>/dev/null || true
echo "  Done."

echo ">> Deleting VPN stack: $VPN_STACK ..."
aws cloudformation delete-stack --stack-name "$VPN_STACK" --region "$REGION" --no-cli-pager

echo ">> Waiting for stack deletion..."
aws cloudformation wait stack-delete-complete --stack-name "$VPN_STACK" --region "$REGION" --no-cli-pager

echo ""
echo ">> Cleanup complete."
echo "   Deleted: $VPN_STACK, $MCP_STACK, 4 alarms, 1 metric filter"
