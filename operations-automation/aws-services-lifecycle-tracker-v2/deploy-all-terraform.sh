#!/bin/bash
# AWS Services Lifecycle Tracker - Terraform deployment (alternative to deploy-all.sh)
# macOS/Linux version - mirrors deploy-all-terraform.ps1
# Usage: ./deploy-all-terraform.sh
#
# Same result as the CDK path, single-account only (multi-account rollout is a
# CloudFormation StackSet and stays CDK-only, see deploy-all.sh --multi-account).
# No Docker needed: the Lambda bundle is built locally with pip (pure-Python
# dependencies resolved as Linux/arm64 wheels), then zipped by Terraform.
#
# Steps: stage backend -> terraform apply -> build frontend with the API URL and
# Cognito IDs -> upload to S3 + invalidate CloudFront.

set -e  # Exit on error
export AWS_PAGER=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TERRAFORM_DIR="${SCRIPT_DIR}/terraform"
STAGE_DIR="${TERRAFORM_DIR}/.backend-stage"
cd "${SCRIPT_DIR}"

echo -e "\033[0;36m=== AWS Services Lifecycle Tracker Deployment (Terraform) ===\033[0m"

# Run shared prerequisites check (AWS CLI, credentials, region, Python, Bedrock)
echo -e "\n\033[0;33mRunning prerequisites check...\033[0m"
source "${REPO_ROOT}/shared/scripts/check-prerequisites.sh" --required-service "bedrock" --min-aws-cli-version "2.33.22" --min-python-version "3.11"

# Terraform + Node.js (frontend build) are specific to this path
if ! command -v terraform >/dev/null 2>&1; then
    echo -e "\033[0;31mTerraform is not installed or not on PATH (https://developer.hashicorp.com/terraform/install)\033[0m"
    exit 1
fi
echo -e "\033[0;90m      Terraform: $(terraform version | head -n 1)\033[0m"
if ! command -v npm >/dev/null 2>&1; then
    echo -e "\033[0;31mNode.js/npm is required to build the frontend (https://nodejs.org)\033[0m"
    exit 1
fi

# python3 or python, whichever has pip
PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -m pip --version >/dev/null 2>&1; then
        PYTHON="$candidate"; break
    fi
done
if [ -z "$PYTHON" ]; then
    echo -e "\033[0;31mpython3 (or python) with pip is required to bundle the Lambda code\033[0m"
    exit 1
fi

REGION="${AWS_REGION}"
ACCOUNT="${AWS_ACCOUNT_ID}"
echo -e "\n\033[0;33mDeploying to region: ${REGION} (account: ${ACCOUNT})\033[0m"

# Step 1: Stage the Lambda code (mirrors cdk/lib/pipeline-stack.ts bundling)
echo -e "\n\033[0;33m[1/5] Staging Lambda code...\033[0m"
echo -e "\033[0;90m      (backend/*.py + shared aws_utils.py + pip wheels for Linux/arm64, Python 3.14)\033[0m"
rm -rf "${STAGE_DIR}"
mkdir -p "${STAGE_DIR}"
cp "${SCRIPT_DIR}/backend/"*.py "${STAGE_DIR}/"
cp "${SCRIPT_DIR}/backend/requirements.txt" "${STAGE_DIR}/"
# Repo-wide region/account helpers: the bundle cannot import from outside its
# own directory, so the shared file is copied in (one source of truth, no local copy).
cp "${REPO_ROOT}/shared/utils/aws_utils.py" "${STAGE_DIR}/"

"$PYTHON" -m pip install --quiet --disable-pip-version-check --no-warn-conflicts \
    --platform manylinux2014_aarch64 --only-binary=:all: \
    --python-version 3.14 --implementation cp \
    --target "${STAGE_DIR}" -r "${STAGE_DIR}/requirements.txt"
find "${STAGE_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
echo -e "\033[0;90m      Staged $(ls "${STAGE_DIR}"/*.py | wc -l | tr -d ' ') Python files + dependencies\033[0m"

# Step 2: Frontend dependencies (the build itself needs the API URL, after apply)
echo -e "\n\033[0;33m[2/5] Installing frontend dependencies...\033[0m"
echo -e "\033[0;90m      (Installing React, Vite, Cognito SDK, and UI component libraries)\033[0m"
pushd frontend > /dev/null
npm install
popd > /dev/null

# Step 3: terraform.tfvars
echo -e "\n\033[0;33m[3/5] Configuring Terraform...\033[0m"
# Only the region is written; any other variable you set in terraform.tfvars
# (e.g. pipeline_role_name) is preserved across runs.
TFVARS_PATH="${TERRAFORM_DIR}/terraform.tfvars"
OTHER_VARS=""
if [ -f "${TFVARS_PATH}" ]; then
    OTHER_VARS=$(grep -v -E '^\s*region\s*=' "${TFVARS_PATH}" || true)
fi
{ echo "region = \"${REGION}\""; [ -n "${OTHER_VARS}" ] && echo "${OTHER_VARS}"; } > "${TFVARS_PATH}"
mkdir -p "${TERRAFORM_DIR}/build"
echo -e "\033[0;90m      terraform.tfvars written (region = ${REGION})\033[0m"

# Step 4: terraform init + apply
echo -e "\n\033[0;33m[4/5] Running terraform apply...\033[0m"
echo -e "\033[0;90m      (DynamoDB tables + config seed, Cognito, durable pipeline + API functions, HTTP API, S3 + CloudFront)\033[0m"
pushd "${TERRAFORM_DIR}" > /dev/null
terraform init -upgrade -input=false
terraform apply -auto-approve -input=false

WEBSITE_URL=$(terraform output -raw website_url)
WEBSITE_BUCKET=$(terraform output -raw website_bucket)
DISTRIBUTION_ID=$(terraform output -raw distribution_id)
API_URL=$(terraform output -raw api_url)
USER_POOL_ID=$(terraform output -raw user_pool_id)
USER_POOL_CLIENT_ID=$(terraform output -raw user_pool_client_id)
PIPELINE_ARN=$(terraform output -raw pipeline_function_alias_arn)
TOPIC_ARN=$(terraform output -raw notification_topic_arn)
popd > /dev/null

if [ -z "$API_URL" ] || [ -z "$USER_POOL_ID" ] || [ -z "$USER_POOL_CLIENT_ID" ]; then
    echo -e "\033[0;31mFailed to read API URL / Cognito config from terraform outputs\033[0m"
    exit 1
fi
echo -e "\033[0;32mAPI URL: ${API_URL}\033[0m"
echo -e "\033[0;32mUser Pool ID: ${USER_POOL_ID}\033[0m"
echo -e "\033[0;32mUser Pool Client ID: ${USER_POOL_CLIENT_ID}\033[0m"

# Step 5: Build the frontend with the API URL and Cognito config, upload, invalidate
echo -e "\n\033[0;33m[5/5] Building and uploading frontend...\033[0m"
./scripts/build-frontend.sh "$USER_POOL_ID" "$USER_POOL_CLIENT_ID" "$API_URL" "$REGION"

aws s3 sync frontend/dist "s3://${WEBSITE_BUCKET}" --delete
aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" --paths "/*" > /dev/null
echo -e "\033[0;90m      Uploaded to s3://${WEBSITE_BUCKET} and invalidated CloudFront\033[0m"

echo -e "\n\033[0;32m========================================\033[0m"
echo -e "\033[0;32m  Deployment Complete!\033[0m"
echo -e "\033[0;32m========================================\033[0m"
echo -e "\033[0;36m  Open the demo:      ${WEBSITE_URL}\033[0m"
echo -e "\033[0;36m  API URL:            ${API_URL}\033[0m"
echo -e "\033[0;36m  Pipeline (durable): ${PIPELINE_ARN}\033[0m"
echo -e "\033[0;36m  Notifications:      ${TOPIC_ARN}\033[0m"
echo -e "\033[0;36m  Region:             ${REGION}\033[0m"
echo -e "\033[0;36m  User Pool ID:       ${USER_POOL_ID}\033[0m"
echo -e "\n\033[0;33mNext Steps:\033[0m"
echo -e "\033[0;90m  1. Create an admin user (copy-paste these two commands):\033[0m"
echo ""
echo -e "     aws cognito-idp admin-create-user --user-pool-id ${USER_POOL_ID} --username admin --user-attributes Name=email,Value=admin@example.com Name=email_verified,Value=true --message-action SUPPRESS"
echo ""
echo -e "     aws cognito-idp admin-set-user-password --user-pool-id ${USER_POOL_ID} --username admin --password \"YourSecurePassword123!\" --permanent"
echo ""
echo -e "\033[0;90m     (Replace the email and password with your own values)\033[0m"
echo -e "\033[0;90m  2. Sign in at the Website URL above and click Refresh to run the first end-to-end pipeline\033[0m"
echo -e "\033[0;90m  3. Optional: subscribe an email to the notifications topic to receive run summaries:\033[0m"
echo -e "     aws sns subscribe --topic-arn ${TOPIC_ARN} --protocol email --notification-endpoint you@example.com"
echo -e "\033[0;90m  4. The weekly schedule runs the same pipeline automatically\033[0m"
echo -e "\n\033[0;90mCleanup: cd terraform && terraform destroy -auto-approve   (removes the DynamoDB tables and their data too)\033[0m"
