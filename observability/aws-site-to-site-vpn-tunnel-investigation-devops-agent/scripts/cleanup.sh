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

# CDK bootstrap resources (shared across all CDK apps in this account/region)
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --no-cli-pager)
CDK_BUCKET="cdk-hnb659fds-assets-${ACCOUNT_ID}-${REGION}"
echo ""
echo -e "\033[1;33m>> WARNING: The next step deletes CDK bootstrap resources that are SHARED across ALL CDK apps in this account/region.\033[0m"
echo -e "\033[1;33m>> If you have other CDK apps in $REGION, DO NOT delete these.\033[0m"
read -rp ">> Delete CDK bootstrap resources ($CDK_BUCKET + CDKToolkit stack)? [y/N] " CONFIRM
if [[ "$CONFIRM" == "y" || "$CONFIRM" == "Y" ]]; then
  echo ">> Emptying CDK bootstrap bucket: $CDK_BUCKET (including versioned objects)..."
  # CDK bootstrap bucket has versioning enabled — must delete all versions and delete markers
  aws s3api list-object-versions --bucket "$CDK_BUCKET" --region "$REGION" --no-cli-pager \
    --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json 2>/dev/null | \
    jq -c 'select(.Objects != null)' | while read -r batch; do
      aws s3api delete-objects --bucket "$CDK_BUCKET" --region "$REGION" --no-cli-pager \
        --delete "$batch" > /dev/null 2>&1
    done
  aws s3api list-object-versions --bucket "$CDK_BUCKET" --region "$REGION" --no-cli-pager \
    --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' --output json 2>/dev/null | \
    jq -c 'select(.Objects != null)' | while read -r batch; do
      aws s3api delete-objects --bucket "$CDK_BUCKET" --region "$REGION" --no-cli-pager \
        --delete "$batch" > /dev/null 2>&1
    done
  echo ">> Deleting CDK bootstrap bucket..."
  aws s3api delete-bucket --bucket "$CDK_BUCKET" --region "$REGION" --no-cli-pager 2>/dev/null || true
  echo ">> Deleting CDKToolkit stack..."
  aws cloudformation delete-stack --stack-name CDKToolkit --region "$REGION" --no-cli-pager 2>/dev/null || true
  aws cloudformation wait stack-delete-complete --stack-name CDKToolkit --region "$REGION" --no-cli-pager 2>/dev/null || true
  echo "  Done."
else
  echo ">> Skipped. To delete later:"
  echo "   CLI:     1. Empty bucket (versioned): aws s3api list-object-versions --bucket $CDK_BUCKET --region $REGION (then delete-objects for all versions)"
  echo "            2. Delete bucket: aws s3api delete-bucket --bucket $CDK_BUCKET --region $REGION"
  echo "            3. Delete stack: aws cloudformation delete-stack --stack-name CDKToolkit --region $REGION"
  echo "   Console: Open https://console.aws.amazon.com/s3/"
  echo "            1. Select bucket '$CDK_BUCKET'"
  echo "            2. Click 'Empty', type 'permanently delete', click Empty"
  echo "            3. Click 'Delete', type the bucket name, click Delete bucket"
  echo "            Then open https://console.aws.amazon.com/cloudformation/ (region: $REGION)"
  echo "            4. Select stack 'CDKToolkit', click Delete"
fi
