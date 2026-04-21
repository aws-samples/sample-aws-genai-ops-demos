#!/usr/bin/env bash
# cleanup.sh — Delete CloudWatch alarms and the CloudFormation stack for the VPN demo.
#
# Usage: ./cleanup.sh <stack-name> <region>
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <stack-name> <region>" >&2
  exit 1
fi

STACK="$1"
REGION="$2"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo ">> Account: $ACCOUNT_ID"
echo ">> Deleting CloudWatch alarms..."
aws cloudwatch delete-alarms \
  --alarm-names vpn-demo-tunnel1-down vpn-demo-tunnel2-down vpn-demo-throughput-drop vpn-demo-route-withdrawn \
  --region "$REGION" 2>/dev/null || true

echo ">> Deleting metric filter..."
aws logs delete-metric-filter \
  --log-group-name /vpn-demo/tunnel-logs \
  --filter-name vpn-demo-route-withdrawn \
  --region "$REGION" 2>/dev/null || true

echo ">> Deleting CloudFormation stack: $STACK ..."
aws cloudformation delete-stack --stack-name "$STACK" --region "$REGION"

echo ">> Waiting for stack deletion..."
aws cloudformation wait stack-delete-complete --stack-name "$STACK" --region "$REGION"

echo ">> Done. Stack '$STACK' and associated alarms have been deleted."
