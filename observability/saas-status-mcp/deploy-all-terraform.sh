#!/usr/bin/env bash
# deploy-all-terraform.sh - One-command Terraform deployment for SaaS Status MCP Server
# Usage: ./deploy-all-terraform.sh
#        AGENT_SPACE_ARN=arn:aws:aidevops:eu-west-1:<acct>:agentspace/<id> ./deploy-all-terraform.sh

set -euo pipefail
export AWS_PAGER=""

AGENT_SPACE_ARN="${AGENT_SPACE_ARN:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TERRAFORM_DIR="${SCRIPT_DIR}/infrastructure/terraform"

echo "========================================"
echo "  SaaS Status MCP Server - Terraform"
echo "========================================"

# Check prerequisites
source "${REPO_ROOT}/shared/scripts/check-prerequisites.sh"
REGION="${AWS_REGION}"
ACCOUNT=$(aws sts get-caller-identity --query "Account" --output text)

echo ""
echo "Deploying to region: ${REGION} (account: ${ACCOUNT})"

# Step 1: Package the MCP server code
echo ""
echo "[1/4] Packaging MCP server code..."

PACKAGE_DIR="${SCRIPT_DIR}/build"
ZIP_PATH="${PACKAGE_DIR}/deployment_package.zip"

rm -rf "${PACKAGE_DIR}"
mkdir -p "${PACKAGE_DIR}/stage"
STAGE_DIR="${PACKAGE_DIR}/stage"

cp "${SCRIPT_DIR}/agent/"*.py "${STAGE_DIR}/"
uv pip install -r "${SCRIPT_DIR}/agent/requirements.txt" \
    --python-platform aarch64-unknown-linux-gnu \
    --python-version 3.13 \
    --target "${STAGE_DIR}" 2>/dev/null

find "${STAGE_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${STAGE_DIR}" -name "*.pyc" -o -name "*.pyo" | xargs rm -f 2>/dev/null || true

cd "${STAGE_DIR}" && zip -qr "${ZIP_PATH}" . && cd "${SCRIPT_DIR}"
ZIP_SIZE=$(du -m "${ZIP_PATH}" | cut -f1)
echo "      Package created: ${ZIP_SIZE} MB"

# Step 2: Upload to S3
echo "[2/4] Uploading deployment package to S3..."

BUCKET_NAME="saas-status-mcp-${ACCOUNT}-${REGION}"
if ! aws s3api head-bucket --bucket "${BUCKET_NAME}" 2>/dev/null; then
    echo "      Creating S3 bucket: ${BUCKET_NAME}"
    if [ "${REGION}" = "us-east-1" ]; then
        aws s3api create-bucket --bucket "${BUCKET_NAME}" >/dev/null
    else
        aws s3api create-bucket --bucket "${BUCKET_NAME}" \
            --create-bucket-configuration LocationConstraint="${REGION}" >/dev/null
    fi
fi

aws s3 cp "${ZIP_PATH}" "s3://${BUCKET_NAME}/agent/deployment_package.zip" --quiet
echo "      Uploaded deployment zip"
aws s3 cp "${SCRIPT_DIR}/agent/providers.json" "s3://${BUCKET_NAME}/config/providers.json" --quiet
echo "      Uploaded provider registry"

# Step 3: Generate terraform.tfvars
echo "[3/4] Configuring Terraform..."

if [ -z "${AGENT_SPACE_ARN}" ]; then
    echo ""
    echo "  To register with DevOps Agent, provide your Agent Space ARN."
    echo "  Open the DevOps Agent console and from your space click Actions > Copy ARN."
    echo "  Leave blank to deploy the runtime only."
    read -r -p "  Agent Space ARN (optional): " AGENT_SPACE_ARN || true
fi

TFVARS_PATH="${TERRAFORM_DIR}/terraform.tfvars"
cat > "${TFVARS_PATH}" <<EOF
runtime_region = "${REGION}"
account_id     = "${ACCOUNT}"
EOF
if [ -n "${AGENT_SPACE_ARN}" ]; then
    echo "agent_space_arn = \"${AGENT_SPACE_ARN}\"" >> "${TFVARS_PATH}"
fi
echo "      terraform.tfvars written"

# Step 4: Terraform init + apply
echo "[4/4] Running terraform apply..."
echo ""

# AWS_SDK_LOAD_CONFIG=0 prevents Terraform from reading ~/.aws/config which
# can cause "source_profile requires role_arn" errors on some configurations.
export AWS_SDK_LOAD_CONFIG=0

pushd "${TERRAFORM_DIR}" > /dev/null
terraform init -upgrade
terraform apply -auto-approve
popd > /dev/null

unset AWS_SDK_LOAD_CONFIG

# Read outputs
pushd "${TERRAFORM_DIR}" > /dev/null
RUNTIME_ARN=$(terraform output -raw runtime_arn 2>/dev/null || echo "")
S3_BUCKET=$(terraform output -raw s3_bucket 2>/dev/null || echo "")
LOG_GROUP=$(terraform output -raw log_group 2>/dev/null || echo "")
popd > /dev/null

# Generate local-proxy/mcp.json
MCP_CONFIG_PATH="${SCRIPT_DIR}/local-proxy/mcp.json"
cat > "${MCP_CONFIG_PATH}" <<EOF
{
  "mcpServers": {
    "saas-status-mcp": {
      "command": "python",
      "args": ["${SCRIPT_DIR}/local-proxy/proxy.py"],
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

# Cleanup
rm -rf "${PACKAGE_DIR}"

# Summary
echo ""
echo "========================================"
echo "  Deployment Complete!"
echo "========================================"
echo ""
echo "  Region:       ${REGION}"
echo "  Runtime ARN:  ${RUNTIME_ARN}"
echo "  S3 bucket:    ${S3_BUCKET}"
echo "  Log group:    ${LOG_GROUP}"
echo ""
echo "  The runtime is IAM-protected (SigV4). See README 'Consuming the server'."
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
