#!/bin/bash
# setup-devops-agent.sh — Create DevOps Agent Space, IAM roles, and webhook
#
# Usage:
#   ./setup-devops-agent.sh --region <region>
#
# This script automates the DevOps Agent onboarding:
#   1. Creates IAM roles (AgentSpace + Operator)
#   2. Creates an Agent Space
#   3. Associates the AWS account
#   4. Enables the Operator App
#   5. Creates a generic webhook (for alarm → agent integration)
#   6. Prints webhook URL + secret for use with deploy-all.sh
#
# Reference: https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-cli-onboarding-guide.html
set -euo pipefail

REGION=""
AGENT_SPACE_NAME="vpn-demo-agent-space"

usage() {
  echo "Usage: $0 --region <region> [--name <agent-space-name>]"
  echo ""
  echo "Supported regions: us-east-1, us-west-2, ap-southeast-2, ap-northeast-1, eu-central-1, eu-west-1"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --region) REGION="$2"; shift 2;;
    --name)   AGENT_SPACE_NAME="$2"; shift 2;;
    -h|--help) usage;;
    *) echo "Unknown option: $1"; usage;;
  esac
done

[[ -z "$REGION" ]] && REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-$(aws configure get region 2>/dev/null)}}"
[[ -z "$REGION" ]] && { echo "ERROR: --region is required (or set via 'aws configure' or AWS_DEFAULT_REGION)"; usage; }

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --no-cli-pager)
echo "Account: $ACCOUNT_ID  Region: $REGION"
echo ""

TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

# ============================================================
echo "=== Step 1: Create IAM roles ==="
# ============================================================

# 1a. Agent Space role
echo "Creating DevOpsAgentRole-AgentSpace..."
cat > "$TMPDIR/agentspace-trust.json" << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "aidevops.amazonaws.com" },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": { "aws:SourceAccount": "$ACCOUNT_ID" },
        "ArnLike": { "aws:SourceArn": "arn:aws:aidevops:$REGION:$ACCOUNT_ID:agentspace/*" }
      }
    }
  ]
}
EOF

aws iam create-role \
  --role-name DevOpsAgentRole-AgentSpace \
  --assume-role-policy-document "file://$TMPDIR/agentspace-trust.json" \
  --query 'Role.Arn' --output text --no-cli-pager 2>/dev/null || \
  aws iam get-role --role-name DevOpsAgentRole-AgentSpace --query 'Role.Arn' --output text --no-cli-pager

# Always update trust policy to match current region (role may exist from a different region)
aws iam update-assume-role-policy \
  --role-name DevOpsAgentRole-AgentSpace \
  --policy-document "file://$TMPDIR/agentspace-trust.json" --no-cli-pager

aws iam attach-role-policy \
  --role-name DevOpsAgentRole-AgentSpace \
  --policy-arn arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy --no-cli-pager 2>/dev/null || true

cat > "$TMPDIR/agentspace-inline.json" << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowCreateServiceLinkedRoles",
      "Effect": "Allow",
      "Action": ["iam:CreateServiceLinkedRole"],
      "Resource": ["arn:aws:iam::$ACCOUNT_ID:role/aws-service-role/resource-explorer-2.amazonaws.com/AWSServiceRoleForResourceExplorer"]
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name DevOpsAgentRole-AgentSpace \
  --policy-name AllowCreateServiceLinkedRoles \
  --policy-document "file://$TMPDIR/agentspace-inline.json" --no-cli-pager 2>/dev/null || true

AGENTSPACE_ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/DevOpsAgentRole-AgentSpace"
echo "  ✅ $AGENTSPACE_ROLE_ARN"

# 1b. Operator App role
echo "Creating DevOpsAgentRole-WebappAdmin..."
cat > "$TMPDIR/operator-trust.json" << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "aidevops.amazonaws.com" },
      "Action": ["sts:AssumeRole", "sts:TagSession"],
      "Condition": {
        "StringEquals": { "aws:SourceAccount": "$ACCOUNT_ID" },
        "ArnLike": { "aws:SourceArn": "arn:aws:aidevops:$REGION:$ACCOUNT_ID:agentspace/*" }
      }
    }
  ]
}
EOF

aws iam create-role \
  --role-name DevOpsAgentRole-WebappAdmin \
  --assume-role-policy-document "file://$TMPDIR/operator-trust.json" \
  --query 'Role.Arn' --output text --no-cli-pager 2>/dev/null || \
  aws iam get-role --role-name DevOpsAgentRole-WebappAdmin --query 'Role.Arn' --output text --no-cli-pager

# Always update trust policy to match current region (role may exist from a different region)
aws iam update-assume-role-policy \
  --role-name DevOpsAgentRole-WebappAdmin \
  --policy-document "file://$TMPDIR/operator-trust.json" --no-cli-pager

aws iam attach-role-policy \
  --role-name DevOpsAgentRole-WebappAdmin \
  --policy-arn arn:aws:iam::aws:policy/AIDevOpsOperatorAppAccessPolicy --no-cli-pager 2>/dev/null || true

OPERATOR_ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/DevOpsAgentRole-WebappAdmin"
echo "  ✅ $OPERATOR_ROLE_ARN"

echo "  Waiting 10s for IAM propagation..."
sleep 10

# ============================================================
echo ""
echo "=== Step 2: Create Agent Space ==="
# ============================================================

AGENT_SPACE_ID=$(aws devops-agent create-agent-space \
  --name "$AGENT_SPACE_NAME" \
  --description "Agent Space for VPN tunnel investigation demo" \
  --region "$REGION" \
  --query 'agentSpace.agentSpaceId' --output text --no-cli-pager)

echo "  ✅ Agent Space ID: $AGENT_SPACE_ID"

# ============================================================
echo ""
echo "=== Step 3: Associate AWS account ==="
# ============================================================

aws devops-agent associate-service \
  --agent-space-id "$AGENT_SPACE_ID" \
  --service-id aws \
  --configuration "{
    \"aws\": {
      \"assumableRoleArn\": \"$AGENTSPACE_ROLE_ARN\",
      \"accountId\": \"$ACCOUNT_ID\",
      \"accountType\": \"monitor\"
    }
  }" \
  --region "$REGION" --no-cli-pager > /dev/null

echo "  ✅ Account $ACCOUNT_ID associated"

# ============================================================
echo ""
echo "=== Step 4: Enable Operator App ==="
# ============================================================

aws devops-agent enable-operator-app \
  --agent-space-id "$AGENT_SPACE_ID" \
  --auth-flow iam \
  --operator-app-role-arn "$OPERATOR_ROLE_ARN" \
  --region "$REGION" --no-cli-pager > /dev/null

OPERATOR_URL="https://${AGENT_SPACE_ID}.aidevops.global.app.aws/home"
echo "  ✅ Operator App enabled"
echo "  URL: $OPERATOR_URL"

# ============================================================
echo ""
echo "=== Step 5: Create webhook ==="
echo "  ⚠️  You need to create the webhook in the AWS DevOps Agent console."
echo ""
echo "  1. Open the AWS DevOps Agent console: https://console.aws.amazon.com/aidevops/"
echo "  2. Select your Agent Space: $AGENT_SPACE_NAME"
echo "  3. Go to the Capabilities tab"
echo "  4. In the Webhooks section, click Add webhook"
echo "  5. Click Next through the schema and HMAC steps"
echo "  6. Click 'Generate URL and secret key'"
echo "  7. Copy the Webhook URL and Secret key, then click Add"
echo ""
read -p "  Paste your Webhook URL: " WEBHOOK_URL
read -p "  Paste your Webhook Secret: " WEBHOOK_SECRET
# ============================================================

echo ""
echo "============================================"
echo "  DEVOPS AGENT SETUP COMPLETE"
echo "============================================"
echo "  Agent Space ID : $AGENT_SPACE_ID"
echo "  Agent Space Name: $AGENT_SPACE_NAME"
echo "  Region         : $REGION"
echo "  Account        : $ACCOUNT_ID"
echo "  Operator App   : $OPERATOR_URL"
echo ""
echo "  Use these with deploy-all.sh:"
echo "  --webhook-url '$WEBHOOK_URL'"
echo "  --webhook-secret '$WEBHOOK_SECRET'"
echo ""
echo "  Full deploy command:"
echo "  bash deploy-all.sh \\"
echo "    --key-file <your-key-file> \\"
echo "    --key-pair <your-key-pair> \\"
echo "    --webhook-url '$WEBHOOK_URL' \\"
echo "    --webhook-secret '$WEBHOOK_SECRET'"
echo "============================================"
