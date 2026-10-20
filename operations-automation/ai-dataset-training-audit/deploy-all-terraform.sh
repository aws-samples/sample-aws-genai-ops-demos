#!/bin/bash
# AI Dataset Training Audit (DART) - Terraform deployment (macOS/Linux)
# Mirrors deploy-all-terraform.ps1. Same result as the CDK path (deploy-all.sh).
#
# Terraform cannot use the CDK DockerImageAsset or `cdk bootstrap`, so this
# script builds and pushes the container image itself, in a deterministic
# two-pass apply:
#   pass 1: terraform apply -target=aws_ecr_repository.dart   (ECR repo only)
#   build + push image to that repo
#   pass 2: terraform apply -var image_tag=<tag>              (everything else)
# `var.image_tag` has no default, so a bare `terraform apply` fails at plan time
# and is unsupported.
#
# Only one IaC path may be deployed per account+region. This script does NOT
# call deploy-cdk and does NOT depend on cdk bootstrap.

set -e  # Exit on error
export AWS_PAGER=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TERRAFORM_DIR="${SCRIPT_DIR}/terraform"
cd "${SCRIPT_DIR}"

echo -e "\033[0;36m=== AI Dataset Training Audit (DART) Deployment (Terraform) ===\033[0m"

# 1. Shared prerequisites FIRST (AWS CLI/creds/region, Bedrock, Docker). The
#    Terraform path builds the image with `docker build`/`docker push`, so the
#    same Docker fail-fast applies as on the CDK path.
echo -e "\n\033[0;33mRunning prerequisites check...\033[0m"
source "${REPO_ROOT}/shared/scripts/check-prerequisites.sh" \
    --required-service "bedrock" \
    --min-aws-cli-version "2.31.13" \
    --require-docker

# 2. Terraform CLI check (the shared script has NO terraform case). Confirm it
#    is on PATH and meets the documented floor (>= 1.5.0).
if ! command -v terraform >/dev/null 2>&1; then
    echo -e "\033[0;31mTerraform is not installed or not on PATH.\033[0m"
    echo -e "\033[0;36mInstall Terraform >= 1.5.0: https://developer.hashicorp.com/terraform/install\033[0m"
    exit 1
fi
TF_VERSION="$(terraform version -json 2>/dev/null | grep -o '"terraform_version": *"[^"]*"' | cut -d'"' -f4)"
if [ -z "$TF_VERSION" ]; then
    TF_VERSION="$(terraform version | head -n 1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n 1)"
fi
TF_MAJOR="$(echo "$TF_VERSION" | cut -d. -f1)"
TF_MINOR="$(echo "$TF_VERSION" | cut -d. -f2)"
if [ "$TF_MAJOR" -lt 1 ] || { [ "$TF_MAJOR" -eq 1 ] && [ "$TF_MINOR" -lt 5 ]; }; then
    echo -e "\033[0;31mTerraform >= 1.5.0 is required (found ${TF_VERSION}).\033[0m"
    echo -e "\033[0;36mUpgrade: https://developer.hashicorp.com/terraform/install\033[0m"
    exit 1
fi
echo -e "\033[0;90m      Terraform: ${TF_VERSION}\033[0m"

region="${AWS_REGION}"
account="${AWS_ACCOUNT_ID}"
echo -e "\n\033[0;33mDeploying to region: ${region} (account: ${account})\033[0m"

# Image tag: short git SHA, fallback to a UTC timestamp.
IMAGE_TAG="$(git -C "${SCRIPT_DIR}" rev-parse --short HEAD 2>/dev/null || true)"
if [ -z "$IMAGE_TAG" ]; then
    IMAGE_TAG="$(date -u +%Y%m%d%H%M%S)"
fi

pushd "${TERRAFORM_DIR}" > /dev/null

# 3. terraform init.
echo -e "\n\033[0;33m[1/4] terraform init...\033[0m"
terraform init -input=false

# 4. Pass 1 - ECR repository only (nothing references a not-yet-pushed image).
echo -e "\n\033[0;33m[2/4] terraform apply (ECR repository only)...\033[0m"
terraform apply -target=aws_ecr_repository.dart -var "region=${region}" -auto-approve -input=false
REPO_URL="$(terraform output -raw ecr_repository_url)"
REGISTRY="${REPO_URL%%/*}"

# 5. Build + push the image.
echo -e "\n\033[0;33m[3/4] Building and pushing the container image (${IMAGE_TAG})...\033[0m"
aws ecr get-login-password --region "${region}" | docker login --username AWS --password-stdin "${REGISTRY}"
docker build --platform linux/amd64 -t "${REPO_URL}:${IMAGE_TAG}" "${SCRIPT_DIR}/agent"
docker push "${REPO_URL}:${IMAGE_TAG}"

# 6. Pass 2 - everything else (the task def now references a pushed image).
echo -e "\n\033[0;33m[4/4] terraform apply (full stack)...\033[0m"
terraform apply -var "image_tag=${IMAGE_TAG}" -var "region=${region}" -auto-approve -input=false

output_bucket="$(terraform output -raw output_bucket_name)"
audit_table="$(terraform output -raw audit_table_name)"
cluster_name="$(terraform output -raw cluster_name)"
task_sg="$(terraform output -raw task_security_group_id)"
public_subnets="$(terraform output -raw public_subnet_ids)"
task_def="$(terraform output -raw task_definition_arn)"
popd > /dev/null

# 7. User-friendly summary + the exact run-task command (same three-part network
#    config as the CDK summary: subnets, securityGroups, assignPublicIp=ENABLED).
echo -e "\n\033[0;32m========================================\033[0m"
echo -e "\033[0;32m  Deployment Complete!\033[0m"
echo -e "\033[0;32m========================================\033[0m"
echo -e "\033[0;36m  Output bucket: ${output_bucket}\033[0m"
echo -e "\033[0;36m  Audit table:   ${audit_table}\033[0m"
echo -e "\033[0;36m  ECS cluster:   ${cluster_name}\033[0m"
echo -e "\033[0;36m  Region:        ${region}\033[0m"
echo -e "\n\033[0;33mRun a dataset audit (replace the message with your own request):\033[0m"
echo ""
echo "  aws ecs run-task \\"
echo "    --cluster ${cluster_name} \\"
echo "    --launch-type FARGATE \\"
echo "    --task-definition ${task_def} \\"
echo "    --region ${region} \\"
echo "    --network-configuration 'awsvpcConfiguration={subnets=[${public_subnets}],securityGroups=[${task_sg}],assignPublicIp=ENABLED}' \\"
echo "    --overrides '{\"containerOverrides\":[{\"name\":\"DartContainer\",\"command\":[\"python\",\"agent.py\",\"Check s3://'\"${output_bucket}\"'/train.jsonl for llama3-70b fine-tuning\"]}]}'"
echo ""
echo -e "\033[0;90m  First upload a dataset under s3://${output_bucket}/ (the task reads its own\033[0m"
echo -e "\033[0;90m  output bucket by default). To scan a dataset in another bucket, see the\033[0m"
echo -e "\033[0;90m  README section 'Scanning a dataset in your own S3 bucket'.\033[0m"
echo -e "\033[0;90m  Cleanup: cd terraform && terraform destroy -var image_tag=${IMAGE_TAG} -var region=${region} -auto-approve\033[0m"
