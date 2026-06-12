#!/bin/bash
# Tear down everything this demo deployed.

set -euo pipefail

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

# If GuardDuty Runtime Monitoring is on for ECS Fargate, it auto-creates an
# interface endpoint (com.amazonaws.<region>.guardduty-data) plus a security
# group inside the Scanner VPC, both tagged GuardDutyManaged=true. CDK does
# not own them, so destroying the Scanner stack fails with "subnet has
# dependencies". Sweep them first, then wait for the ENIs to detach.
SCANNER_STACK="ProwlerSecurityScanner-$REGION"
SCANNER_VPC=$(aws cloudformation describe-stack-resources \
    --stack-name "$SCANNER_STACK" \
    --region "$REGION" \
    --query "StackResources[?ResourceType=='AWS::EC2::VPC'].PhysicalResourceId | [0]" \
    --output text 2>/dev/null || echo "")
if [ -n "$SCANNER_VPC" ] && [ "$SCANNER_VPC" != "None" ]; then
  GD_ENDPOINTS=$(aws ec2 describe-vpc-endpoints \
      --region "$REGION" \
      --filters "Name=vpc-id,Values=$SCANNER_VPC" "Name=tag:GuardDutyManaged,Values=true" \
      --query 'VpcEndpoints[].VpcEndpointId' --output text 2>/dev/null || echo "")
  if [ -n "$GD_ENDPOINTS" ]; then
    echo "[cleanup] removing GuardDuty-managed endpoints in $SCANNER_VPC: $GD_ENDPOINTS"
    # shellcheck disable=SC2086
    aws ec2 delete-vpc-endpoints --region "$REGION" --vpc-endpoint-ids $GD_ENDPOINTS >/dev/null || true
    # ENIs linger briefly after endpoint deletion; wait up to ~2min.
    for _ in $(seq 1 24); do
      COUNT=$(aws ec2 describe-network-interfaces \
          --region "$REGION" \
          --filters "Name=vpc-id,Values=$SCANNER_VPC" "Name=interface-type,Values=vpc_endpoint" \
          --query 'length(NetworkInterfaces)' --output text 2>/dev/null || echo "0")
      [ "$COUNT" = "0" ] && break
      sleep 5
    done
  fi
  GD_SGS=$(aws ec2 describe-security-groups \
      --region "$REGION" \
      --filters "Name=vpc-id,Values=$SCANNER_VPC" "Name=group-name,Values=GuardDutyManagedSecurityGroup-*" \
      --query 'SecurityGroups[].GroupId' --output text 2>/dev/null || echo "")
  for SG in $GD_SGS; do
    echo "[cleanup] deleting orphan GuardDuty SG $SG"
    aws ec2 delete-security-group --region "$REGION" --group-id "$SG" >/dev/null || true
  done
fi

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
