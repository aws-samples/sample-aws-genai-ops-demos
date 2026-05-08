#!/bin/bash
# Tear down everything this demo deployed.

# set -eo pipefail (no -u): shared/utils/aws-utils.sh references
# $AWS_DEFAULT_REGION unguarded, which aborts under `set -u` when the
# caller only has `aws configure set region` and no env var exported.
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$REPO_ROOT/shared/utils/aws-utils.sh"
REGION="$(get_aws_region)"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
DEVOPS_AGENT_REGION="${DEVOPS_AGENT_REGION:-us-east-1}"

echo "=============================================="
echo " Prowler Security Demo — Cleanup"
echo "=============================================="
echo "Account: $ACCOUNT_ID  Region: $REGION  DevOps Agent region: $DEVOPS_AGENT_REGION"
echo ""

# CDK synth needs to resolve every stack's assets before it can destroy
# anything. The FrontendStack references `frontend/dist` as a BucketDeployment
# source; if the user has never built the frontend (or cleaned the build
# output), synth fails with CannotFindAsset and every destroy call aborts.
# Stub a placeholder so synth resolves and destroy proceeds — the files will
# be deleted along with the bucket anyway.
if [ ! -f "$SCRIPT_DIR/../frontend/dist/index.html" ]; then
    mkdir -p "$SCRIPT_DIR/../frontend/dist"
    echo '<!doctype html><html><body>cleanup placeholder</body></html>' > "$SCRIPT_DIR/../frontend/dist/index.html"
fi

cd "$SCRIPT_DIR/../cdk"
STACKS=(
  "ProwlerSecurityFrontend-$REGION"
  "ProwlerSecurityApi-$REGION"
  "ProwlerSecurityIngest-$REGION"
  "ProwlerSecurityScanner-$REGION"
  "ProwlerSecurityDevOpsAgent-$REGION"
  "ProwlerSecurityAuth-$REGION"
  "ProwlerSecurityData-$REGION"
)
for STACK in "${STACKS[@]}"; do
  echo "[cleanup] destroying $STACK..."
  npx cdk destroy "$STACK" --force --no-cli-pager || true
done

# Agent Space (best-effort — only if the user wants it gone)
AGENT_SPACE_NAME="prowler-security"
EXISTING_SPACE_ID=$(aws devops-agent list-agent-spaces \
    --region "$DEVOPS_AGENT_REGION" \
    --query "agentSpaces[?name=='$AGENT_SPACE_NAME'].agentSpaceId | [0]" \
    --output text --no-cli-pager 2>/dev/null || echo "")
if [ -n "$EXISTING_SPACE_ID" ] && [ "$EXISTING_SPACE_ID" != "None" ]; then
  read -rp "Delete DevOps Agent Space $EXISTING_SPACE_ID? (y/N) " ans
  if [[ "$ans" == "y" || "$ans" == "Y" ]]; then
    aws devops-agent delete-agent-space --agent-space-id "$EXISTING_SPACE_ID" --region "$DEVOPS_AGENT_REGION" || true
    aws iam detach-role-policy --role-name "${AGENT_SPACE_NAME}-AgentSpaceRole" --policy-arn arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy 2>/dev/null || true
    aws iam delete-role --role-name "${AGENT_SPACE_NAME}-AgentSpaceRole" 2>/dev/null || true
    aws iam detach-role-policy --role-name "${AGENT_SPACE_NAME}-OperatorRole" --policy-arn arn:aws:iam::aws:policy/AIDevOpsOperatorAppAccessPolicy 2>/dev/null || true
    aws iam delete-role --role-name "${AGENT_SPACE_NAME}-OperatorRole" 2>/dev/null || true
  fi
fi

echo "[cleanup] done."
