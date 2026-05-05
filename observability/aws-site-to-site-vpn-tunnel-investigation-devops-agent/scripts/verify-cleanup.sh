#!/usr/bin/env bash
# verify-cleanup.sh — Check for leftover VPN demo resources in a region
#
# Usage: ./verify-cleanup.sh <region>
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <region>" >&2
  exit 1
fi

REGION="$1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --no-cli-pager)
FOUND=0

echo "Checking for leftover VPN demo resources in $REGION (account $ACCOUNT_ID)..."
echo ""

# CloudFormation stacks
echo ">> CloudFormation stacks..."
for STACK in "VpnDemoStack-$REGION" "VpnDemoMcpServer-$REGION"; do
  STATUS=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query 'Stacks[0].StackStatus' --output text --no-cli-pager 2>/dev/null || true)
  if [[ -n "$STATUS" && "$STATUS" != "None" && "$STATUS" != "DELETE_COMPLETE" ]]; then
    echo "   FOUND: $STACK ($STATUS)"
    FOUND=1
  fi
done
echo "   CDKToolkit:"
STATUS=$(aws cloudformation describe-stacks --stack-name CDKToolkit --region "$REGION" \
  --query 'Stacks[0].StackStatus' --output text --no-cli-pager 2>/dev/null || true)
if [[ -n "$STATUS" && "$STATUS" != "None" && "$STATUS" != "DELETE_COMPLETE" ]]; then
  echo "   FOUND: CDKToolkit ($STATUS)"
  FOUND=1
else
  echo "   Clean"
fi

# CloudWatch alarms
echo ">> CloudWatch alarms..."
ALARMS=$(aws cloudwatch describe-alarms --region "$REGION" \
  --alarm-name-prefix vpn-demo \
  --query 'MetricAlarms[].AlarmName' --output text --no-cli-pager 2>/dev/null || true)
if [[ -n "$ALARMS" ]]; then
  echo "   FOUND: $ALARMS"
  FOUND=1
else
  echo "   Clean"
fi

# Metric filter
echo ">> CloudWatch metric filter..."
FILTER=$(aws logs describe-metric-filters --region "$REGION" \
  --log-group-name /vpn-demo/tunnel-logs \
  --query 'metricFilters[].filterName' --output text --no-cli-pager 2>/dev/null || true)
if [[ -n "$FILTER" ]]; then
  echo "   FOUND: $FILTER"
  FOUND=1
else
  echo "   Clean"
fi

# CDK bootstrap bucket
echo ">> CDK bootstrap bucket..."
CDK_BUCKET="cdk-hnb659fds-assets-${ACCOUNT_ID}-${REGION}"
if aws s3api head-bucket --bucket "$CDK_BUCKET" --region "$REGION" --no-cli-pager 2>/dev/null; then
  echo "   FOUND: $CDK_BUCKET"
  FOUND=1
else
  echo "   Clean"
fi

# Agent Spaces
echo ">> DevOps Agent spaces..."
SPACES=$(aws devops-agent list-agent-spaces --region "$REGION" \
  --query "agentSpaces[?contains(name,'vpn-demo')].{Name:name,Id:agentSpaceId}" \
  --output text --no-cli-pager 2>/dev/null || true)
if [[ -n "$SPACES" ]]; then
  echo "   FOUND: $SPACES"
  FOUND=1
else
  echo "   Clean"
fi

# IAM roles (global)
echo ">> IAM roles..."
ROLES_FOUND=0
for ROLE in DevOpsAgentRole-AgentSpace DevOpsAgentRole-WebappAdmin; do
  if aws iam get-role --role-name "$ROLE" --query 'Role.RoleName' --output text --no-cli-pager 2>/dev/null | grep -q "$ROLE"; then
    echo "   FOUND: $ROLE"
    FOUND=1
    ROLES_FOUND=1
  fi
done
[[ $ROLES_FOUND -eq 0 ]] && echo "   Clean"

# EC2 key pairs
echo ">> EC2 key pairs..."
KEYS=$(aws ec2 describe-key-pairs --region "$REGION" \
  --query "KeyPairs[?contains(KeyName,'vpn-demo')].KeyName" \
  --output text --no-cli-pager 2>/dev/null || true)
if [[ -n "$KEYS" ]]; then
  echo "   FOUND: $KEYS"
  FOUND=1
else
  echo "   Clean"
fi

echo ""
if [[ $FOUND -eq 0 ]]; then
  echo "✅ No leftover VPN demo resources found in $REGION."
else
  echo "⚠  Some VPN demo resources remain. See above for details."
fi
