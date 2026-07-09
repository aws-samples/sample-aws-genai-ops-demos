#!/usr/bin/env bash
# deploy-all.sh — One-command deployment for SaaS Status MCP Server
# Usage: ./deploy-all.sh

set -euo pipefail

echo "========================================"
echo "  SaaS Status MCP Server - Deployment"
echo "========================================"

# Resolve paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Set PYTHONPATH so CDK app can import shared/utils
export PYTHONPATH="${REPO_ROOT}"

# Check prerequisites
source "${REPO_ROOT}/shared/scripts/check-prerequisites.sh"
REGION="${AWS_REGION}"
ACCOUNT=$(aws sts get-caller-identity --query "Account" --output text)

echo ""
echo "Deploying to region: ${REGION} (account: ${ACCOUNT})"

# ─── Step 1: Package the MCP server code ───
echo ""
echo "[1/4] Packaging MCP server code..."

PACKAGE_DIR="${SCRIPT_DIR}/build"
ZIP_PATH="${PACKAGE_DIR}/deployment_package.zip"
STAGE_DIR="${PACKAGE_DIR}/stage"

rm -rf "${PACKAGE_DIR}"
mkdir -p "${STAGE_DIR}"

# Copy agent code flat into the staging dir (main.py must be at the zip root).
# NOTE: providers.json is deliberately NOT bundled — it lives in S3 as the single
# runtime source of truth (uploaded separately below). This avoids shipping the
# registry in two places.
cp "${SCRIPT_DIR}"/agent/*.py "${STAGE_DIR}/"

# Install dependencies for Linux target (AgentCore runs on Linux)
uv pip install -r "${SCRIPT_DIR}/agent/requirements.txt" \
    --python-platform aarch64-unknown-linux-gnu \
    --python-version 3.13 \
    --target "${STAGE_DIR}" > /dev/null 2>&1

# Remove Python cache files incompatible with the target runtime
find "${STAGE_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${STAGE_DIR}" -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete 2>/dev/null || true

# Create zip
( cd "${STAGE_DIR}" && zip -r -q "${ZIP_PATH}" . )
ZIP_SIZE=$(du -m "${ZIP_PATH}" | cut -f1)
echo "      Package created: ${ZIP_SIZE} MB"

# ─── Step 2: Create S3 bucket and upload deployment package ───
echo "[2/4] Uploading deployment package to S3..."

BUCKET_NAME="saas-status-mcp-${ACCOUNT}-${REGION}"

if ! aws s3api head-bucket --bucket "${BUCKET_NAME}" 2>/dev/null; then
    echo "      Creating S3 bucket: ${BUCKET_NAME}"
    if [ "${REGION}" = "us-east-1" ]; then
        aws s3api create-bucket --bucket "${BUCKET_NAME}" > /dev/null
    else
        aws s3api create-bucket --bucket "${BUCKET_NAME}" \
            --create-bucket-configuration LocationConstraint="${REGION}" > /dev/null
    fi
fi

aws s3 cp "${ZIP_PATH}" "s3://${BUCKET_NAME}/agent/deployment_package.zip" --quiet
echo "      Uploaded to s3://${BUCKET_NAME}/agent/deployment_package.zip"

# Upload the provider registry as a standalone config object.
# The running server reads this via conditional GET, so updating providers
# later is just a re-upload (see scripts/refresh-providers.sh) — no redeploy needed.
aws s3 cp "${SCRIPT_DIR}/agent/providers.json" "s3://${BUCKET_NAME}/config/providers.json" --quiet
echo "      Uploaded to s3://${BUCKET_NAME}/config/providers.json"

# ─── Step 3: Deploy CDK stack (creates IAM role + AgentCore Runtime) ───
echo "[3/4] Deploying CDK stack (IAM + AgentCore Runtime)..."

pushd "${SCRIPT_DIR}/infrastructure/cdk" > /dev/null
python3 -m pip install -r requirements.txt --quiet
npx cdk deploy --require-approval never
popd > /dev/null

# ─── Step 4: Retrieve outputs and display config ───
echo "[4/4] Retrieving deployment outputs..."

STACK_NAME="SaasStatusMcpStack-${REGION}"
RUNTIME_ARN=$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --query "Stacks[0].Outputs[?OutputKey=='RuntimeArn'].OutputValue" --output text 2>/dev/null || echo "N/A")
RUNTIME_ENDPOINT=$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --query "Stacks[0].Outputs[?OutputKey=='RuntimeEndpoint'].OutputValue" --output text 2>/dev/null || echo "N/A")
RUNTIME_ROLE_ARN=$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --query "Stacks[0].Outputs[?OutputKey=='RuntimeRoleArn'].OutputValue" --output text 2>/dev/null || echo "N/A")
LOG_GROUP_NAME=$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --query "Stacks[0].Outputs[?OutputKey=='LogGroupName'].OutputValue" --output text 2>/dev/null || echo "N/A")

echo ""
echo "========================================"
echo "  Deployment Complete!"
echo "========================================"
echo ""
echo "  Stack:          ${STACK_NAME}"
echo "  Region:         ${REGION}"
echo "  Runtime ARN:    ${RUNTIME_ARN}"
echo "  MCP Endpoint:   ${RUNTIME_ENDPOINT}"
echo "  Runtime Role:   ${RUNTIME_ROLE_ARN}"
echo "  Log Group:      ${LOG_GROUP_NAME}"
echo ""
echo "  The runtime is IAM-protected (SigV4) - callers sign requests via the"
echo "  InvokeAgentRuntime API. See README 'Consuming the server' for details."

# Clean up build artifacts
rm -rf "${PACKAGE_DIR}"

# ─── Register with AWS DevOps Agent (optional, interactive) ───
echo ""
echo "========================================"
echo "  Register with AWS DevOps Agent"
echo "========================================"
echo ""
echo "  DevOps Agent is the intended consumer. It invokes the runtime"
echo "  natively (no proxy) once registered against your Agent Space."
echo ""
REGISTER=""
read -r -p "  Register this MCP server with a DevOps Agent Space now? (y/N) " REGISTER || true
if [[ "${REGISTER}" =~ ^[Yy] ]]; then
    RUNTIME_REGION="${REGION}" "${SCRIPT_DIR}/scripts/setup-devops-agent.sh"
else
    echo ""
    echo "  Skipped. Run it anytime with:"
    echo "    RUNTIME_REGION=${REGION} ./scripts/setup-devops-agent.sh"
fi

# ─── Kiro / local MCP clients ───
# Generate local-proxy/mcp.json with the real runtime ARN and region baked in.
# Users just point Kiro at this file — no manual copy-paste or URL construction.
MCP_CONFIG_PATH="${SCRIPT_DIR}/local-proxy/mcp.json"

cat > "${MCP_CONFIG_PATH}" <<EOF
{
  "mcpServers": {
    "saas-status-mcp": {
      "command": "python",
      "args": ["observability/saas-status-mcp/local-proxy/proxy.py"],
      "env": {
        "SAAS_MCP_RUNTIME_ARN": "${RUNTIME_ARN}",
        "AWS_REGION": "${REGION}"
      },
      "disabled": false,
      "autoApprove": [
        "list_providers",
        "get_service_status",
        "get_active_events",
        "check_all_dependencies"
      ]
    }
  }
}
EOF

echo ""
echo "========================================"
echo "  Test locally from Kiro (optional)"
echo "========================================"
echo ""
echo "  mcp.json written to: local-proxy/mcp.json"
echo ""
echo "  To connect Kiro to the deployed MCP Server on Bedrock AgentCore Runtime:"
echo "    1) pip install -r local-proxy/requirements.txt"
echo "    2) Merge local-proxy/mcp.json into your Kiro mcp.json"
echo ""
