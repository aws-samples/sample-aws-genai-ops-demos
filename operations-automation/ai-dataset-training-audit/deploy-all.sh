#!/bin/bash
# AI Dataset Training Audit (DART) - CDK deployment (macOS/Linux)
# Mirrors deploy-all.ps1.
#
# One stack: DatasetTrainingAudit-<region>. The Fargate image is a CDK
# DockerImageAsset that the shared deploy script builds locally, so Docker must
# be running (the shared prerequisites check --require-docker enforces this).
#
# This is the demo's only stack, so deploy-cdk.sh is called WITHOUT
# --skip-bootstrap: it bootstraps once before the single deploy.
#
# For the Terraform path instead, use ./deploy-all-terraform.sh. Only one IaC
# path may be deployed per account+region.

set -e  # Exit on error
export AWS_PAGER=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${SCRIPT_DIR}"

echo -e "\033[0;36m=== AI Dataset Training Audit (DART) Deployment (CDK) ===\033[0m"

# 1. Shared prerequisites FIRST (AWS CLI/creds/region, Bedrock, Node/CDK, Docker).
#    --require-docker: the Fargate image is a CDK DockerImageAsset built locally
#    at deploy time; without a running Docker engine the deploy fails deep with
#    an opaque error, so fail fast here instead.
echo -e "\n\033[0;33mRunning prerequisites check...\033[0m"
source "${REPO_ROOT}/shared/scripts/check-prerequisites.sh" \
    --required-service "bedrock" \
    --min-aws-cli-version "2.31.13" \
    --require-cdk \
    --require-docker

region="${AWS_REGION}"
account="${AWS_ACCOUNT_ID}"
stack_name="DatasetTrainingAudit-${region}"
echo -e "\n\033[0;33mDeploying ${stack_name} (account: ${account})\033[0m"

# 2. Deploy through the shared CDK script (it owns bootstrap + deploy). No direct
#    cdk invocation here. Single stack => NO --skip-bootstrap.
"${REPO_ROOT}/shared/scripts/deploy-cdk.sh" \
    --cdk-directory "cdk" \
    --stack-name "${stack_name}"
if [ $? -ne 0 ]; then
    echo -e "\033[0;31mCDK deployment failed\033[0m"
    exit 1
fi

# 3. Read back CfnOutputs (no --outputs-file, no jq).
get_output() {
    aws cloudformation describe-stacks \
        --stack-name "${stack_name}" \
        --region "${region}" \
        --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" \
        --output text
}
output_bucket="$(get_output OutputBucketName)"
audit_table="$(get_output AuditTableName)"
cluster_name="$(get_output ClusterName)"
task_sg="$(get_output TaskSecurityGroupId)"
public_subnets="$(get_output PublicSubnetIds)"
task_def="$(get_output TaskDefinitionArn)"
# Log group name is fixed by the stack (lib/dataset-training-audit-stack.ts): /dataset-training-audit/<region>.
log_group="/dataset-training-audit/${region}"

# 4. User-friendly summary + the exact run-task command. The network config MUST
#    pass subnets, securityGroups AND assignPublicIp=ENABLED, or the public-subnet
#    task cannot reach Bedrock/Comprehend.
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
echo "    --overrides '{\"containerOverrides\":[{\"name\":\"DartContainer\",\"command\":[\"Check s3://'\"${output_bucket}\"'/train.jsonl for llama3-70b fine-tuning\"]}]}'"
echo ""
echo -e "\033[0;90m  First upload a dataset under s3://${output_bucket}/ (the task reads its own\033[0m"
echo -e "\033[0;90m  output bucket by default). To scan a dataset in another bucket, see the\033[0m"
echo -e "\033[0;90m  README section 'Scanning a dataset in your own S3 bucket'.\033[0m"
echo -e "\n\033[0;33mWatch the run (logs stream here):\033[0m"
echo "  aws logs tail ${log_group} --region ${region} --follow"
echo -e "\n\033[0;33mRead the report when the task stops:\033[0m"
echo "  aws s3 ls s3://${output_bucket}/reports/ --region ${region}"
echo ""
echo -e "\033[0;90m  Cleanup: see the README Cleanup section (destroy uses -c imageTag=destroy, no Docker needed)\033[0m"
