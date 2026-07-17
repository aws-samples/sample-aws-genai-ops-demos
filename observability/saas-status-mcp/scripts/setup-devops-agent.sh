#!/usr/bin/env bash
# =============================================================================
# Setup DevOps Agent MCP Registration (Bash)
# =============================================================================
# Deploys SaasStatusMcpRegistrationStack to your Agent Space's region using CDK.
# The stack creates the SigV4 signing IAM role, registers the MCP server
# (AWS::DevOpsAgent::Service), and attaches it to your Agent Space with the
# four tools (AWS::DevOpsAgent::Association).
#
# Requires aws-cdk-lib >= 2.251.0 (see infrastructure/cdk/requirements.txt).
#
# Usage:
#   ./scripts/setup-devops-agent.sh
#   AGENT_SPACE_ARN=arn:aws:aidevops:eu-west-1:<acct>:agentspace/<id> ./scripts/setup-devops-agent.sh
#   AGENT_SPACE_ARN=<arn> RUNTIME_REGION=eu-west-3 ./scripts/setup-devops-agent.sh
# =============================================================================

set -euo pipefail
export AWS_PAGER=""

AGENT_SPACE_ARN="${AGENT_SPACE_ARN:-}"
RUNTIME_REGION="${RUNTIME_REGION:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
CDK_DIR="${SCRIPT_DIR}/../infrastructure/cdk"

echo "=============================================="
echo " DevOps Agent MCP Registration (CDK)"
echo "=============================================="
echo ""

# ---------------------------------------------------------------------------
# Resolve caller identity
# ---------------------------------------------------------------------------
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
if [ -z "${AWS_ACCOUNT_ID}" ]; then
    echo "ERROR: could not resolve AWS account. Configure credentials first."
    exit 1
fi

# ---------------------------------------------------------------------------
# Runtime region — where SaasStatusMcpStack was deployed
# ---------------------------------------------------------------------------
if [ -z "${RUNTIME_REGION}" ]; then
    RUNTIME_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo '')}}"
fi
if [ -z "${RUNTIME_REGION}" ]; then
    read -r -p "Enter the region where the MCP runtime is deployed (e.g. eu-west-3): " RUNTIME_REGION
fi

# ---------------------------------------------------------------------------
# Fetch the runtime ARN from the main stack outputs (one CLI call, no manual input)
# ---------------------------------------------------------------------------
echo "[1/3] Reading runtime ARN from CloudFormation stack..."

MAIN_STACK_NAME="SaasStatusMcpStack-${RUNTIME_REGION}"
RUNTIME_ARN=$(aws cloudformation describe-stacks \
    --stack-name "${MAIN_STACK_NAME}" \
    --region "${RUNTIME_REGION}" \
    --query "Stacks[0].Outputs[?OutputKey=='RuntimeArn'].OutputValue" \
    --output text 2>/dev/null || echo "")

if [ -z "${RUNTIME_ARN}" ] || [ "${RUNTIME_ARN}" = "None" ]; then
    echo "  ERROR: stack '${MAIN_STACK_NAME}' not found in ${RUNTIME_REGION}."
    echo "  Deploy the MCP server first: ./deploy-all.sh"
    exit 1
fi
echo "  Runtime ARN: ${RUNTIME_ARN}"
echo ""

# ---------------------------------------------------------------------------
# Agent Space ARN — carries the region so the user doesn't specify it separately
# ---------------------------------------------------------------------------
if [ -z "${AGENT_SPACE_ARN}" ]; then
    echo "Provide your Agent Space ARN (open the DevOps Agent console and from your space click Actions > Copy ARN)."
    echo "  Example: arn:aws:aidevops:eu-west-1:${AWS_ACCOUNT_ID}:agentspace/xxxxxxxx-xxxx-..."
    read -r -p "Enter your Agent Space ARN: " AGENT_SPACE_ARN
fi

# Parse region + ID from the ARN — avoids asking for them separately
if [[ "${AGENT_SPACE_ARN}" =~ ^arn:aws[a-z-]*:aidevops:([^:]+):([0-9]+):agentspace/(.+)$ ]]; then
    AGENT_SPACE_REGION="${BASH_REMATCH[1]}"
    AGENT_SPACE_ACCOUNT="${BASH_REMATCH[2]}"
    AGENT_SPACE_ID="${BASH_REMATCH[3]}"
else
    echo "ERROR: not a valid Agent Space ARN."
    echo "  Expected: arn:aws:aidevops:<region>:<account>:agentspace/<id>"
    exit 1
fi
if [ "${AGENT_SPACE_ACCOUNT}" != "${AWS_ACCOUNT_ID}" ]; then
    echo "WARNING: Agent Space account (${AGENT_SPACE_ACCOUNT}) differs from your credentials (${AWS_ACCOUNT_ID})."
fi

echo "  Account:            ${AWS_ACCOUNT_ID}"
echo "  Runtime region:     ${RUNTIME_REGION}  (SigV4 signing region)"
echo "  Agent Space region: ${AGENT_SPACE_REGION}"
echo "  Agent Space ID:     ${AGENT_SPACE_ID}"
echo ""

# ---------------------------------------------------------------------------
# CDK deploy the registration stack
# ---------------------------------------------------------------------------
echo "[2/3] Installing CDK dependencies..."
python3 -m pip install -r "${CDK_DIR}/requirements.txt" --quiet

echo "[3/3] Deploying SaasStatusMcpRegistrationStack via CDK..."
echo "  (creates IAM role, DevOps Agent Service, Association)"
echo ""

STACK_ID="SaasStatusMcpRegistrationStack-${AGENT_SPACE_REGION}"
export PYTHONPATH="${REPO_ROOT}"

pushd "${CDK_DIR}" > /dev/null
npx cdk deploy "${STACK_ID}" \
    --require-approval never \
    --context "agent_space_id=${AGENT_SPACE_ID}" \
    --context "agent_space_region=${AGENT_SPACE_REGION}" \
    --context "runtime_arn=${RUNTIME_ARN}" \
    --context "runtime_region=${RUNTIME_REGION}"
popd > /dev/null

# ---------------------------------------------------------------------------
# Read outputs from the deployed registration stack
# ---------------------------------------------------------------------------
SERVICE_ID=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_ID}" \
    --region "${AGENT_SPACE_REGION}" \
    --query "Stacks[0].Outputs[?OutputKey=='ServiceId'].OutputValue" \
    --output text 2>/dev/null || echo "N/A")

SIGNING_ROLE=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_ID}" \
    --region "${AGENT_SPACE_REGION}" \
    --query "Stacks[0].Outputs[?OutputKey=='SigningRoleArn'].OutputValue" \
    --output text 2>/dev/null || echo "N/A")

echo ""
echo "=============================================="
echo "  Registration Complete"
echo "=============================================="
echo ""
echo "  CDK stack:    ${STACK_ID}"
echo "  Service ID:   ${SERVICE_ID}"
echo "  Agent Space:  ${AGENT_SPACE_ID} (${AGENT_SPACE_REGION})"
echo "  Signing role: ${SIGNING_ROLE}"
echo "  MCP name:     saas-status-mcp"
echo "  Tools:        4 enabled"
echo ""
exit 0
