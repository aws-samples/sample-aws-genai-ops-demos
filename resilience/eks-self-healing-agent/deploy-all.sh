#!/bin/bash
# EKS Self-Healing Agent (Agatha) - CDK deployment (macOS/Linux)
# Mirrors deploy-all.ps1.
#
# One stack: EksSelfHealingAgent-<region>. It installs Agatha onto an EXISTING
# EKS cluster: the agent image (CDK DockerImageAsset, built locally, so Docker
# must be running), an IRSA role with Bedrock InvokeModel on one model, and a
# Helm release of helm/agatha installed through the CDK kubectl handler.
#
# Usage:
#   ./deploy-all.sh --cluster-name <name> --kubectl-role-arn <arn> [--oidc-issuer <url>]
#                   [--node-architecture amd64|arm64] [--model-id <base model id>]
#   ./deploy-all.sh --destroy
# Inputs can also come from CLUSTER_NAME, KUBECTL_ROLE_ARN, OIDC_ISSUER,
# NODE_ARCHITECTURE and MODEL_ID environment variables.
set -e  # Exit on error
export AWS_PAGER=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${SCRIPT_DIR}"

CLUSTER_NAME="${CLUSTER_NAME:-}"
KUBECTL_ROLE_ARN="${KUBECTL_ROLE_ARN:-}"
OIDC_ISSUER="${OIDC_ISSUER:-}"
NODE_ARCHITECTURE="${NODE_ARCHITECTURE:-}"
MODEL_ID="${MODEL_ID:-}"
DESTROY=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --cluster-name)      CLUSTER_NAME="$2"; shift 2 ;;
        --kubectl-role-arn)  KUBECTL_ROLE_ARN="$2"; shift 2 ;;
        --oidc-issuer)       OIDC_ISSUER="$2"; shift 2 ;;
        --node-architecture) NODE_ARCHITECTURE="$2"; shift 2 ;;
        --model-id)          MODEL_ID="$2"; shift 2 ;;
        --destroy)           DESTROY=true; shift ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 --cluster-name <name> --kubectl-role-arn <arn> [--oidc-issuer <url>] [--node-architecture amd64|arm64] [--model-id <id>] [--destroy]"
            exit 1
            ;;
    esac
done

if [ -n "${NODE_ARCHITECTURE}" ] && [ "${NODE_ARCHITECTURE}" != "amd64" ] && [ "${NODE_ARCHITECTURE}" != "arm64" ]; then
    echo -e "\033[0;31m--node-architecture must be amd64 or arm64\033[0m"
    exit 1
fi

echo -e "\033[0;36m=== EKS Self-Healing Agent (Agatha) Deployment (CDK) ===\033[0m"

# 1. Shared prerequisites FIRST (AWS CLI/creds/region, Bedrock, Node/CDK, Docker).
#    Destroy uses the imageTag escape hatch, so it does not need Docker.
echo -e "\n\033[0;33mRunning prerequisites check...\033[0m"
if [ "${DESTROY}" = true ]; then
    source "${REPO_ROOT}/shared/scripts/check-prerequisites.sh" \
        --required-service "bedrock" \
        --min-aws-cli-version "2.31.13" \
        --require-cdk
else
    source "${REPO_ROOT}/shared/scripts/check-prerequisites.sh" \
        --required-service "bedrock" \
        --min-aws-cli-version "2.31.13" \
        --require-cdk \
        --require-docker
fi

region="${AWS_REGION}"
account="${AWS_ACCOUNT_ID}"
partition="$(echo "${AWS_ARN}" | cut -d: -f2)"
stack_name="EksSelfHealingAgent-${region}"

get_output() {
    aws cloudformation describe-stacks \
        --stack-name "${stack_name}" \
        --region "${region}" \
        --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" \
        --output text 2>/dev/null || true
}

# 2. Teardown path: read the inputs back from the stack outputs, then destroy
#    through the shared script. Removes the Helm release, IAM role and image
#    reference. No direct cdk invocation.
if [ "${DESTROY}" = true ]; then
    [ -n "${CLUSTER_NAME}" ] || CLUSTER_NAME="$(get_output ClusterName)"
    [ -n "${KUBECTL_ROLE_ARN}" ] || KUBECTL_ROLE_ARN="$(get_output KubectlRoleArn)"
    [ -n "${OIDC_ISSUER}" ] || OIDC_ISSUER="$(get_output OidcIssuer)"
    if [ -z "${CLUSTER_NAME}" ] || [ "${CLUSTER_NAME}" = "None" ] || [ -z "${KUBECTL_ROLE_ARN}" ] || [ -z "${OIDC_ISSUER}" ]; then
        echo -e "\033[0;31mCould not read the stack inputs. Stack ${stack_name} not found in ${region}? Pass --cluster-name, --kubectl-role-arn and --oidc-issuer.\033[0m"
        exit 1
    fi
    echo -e "\n\033[0;33mDestroying ${stack_name} (account: ${account})\033[0m"
    "${REPO_ROOT}/shared/scripts/deploy-cdk.sh" \
        --cdk-directory "infrastructure/cdk" \
        --stack-name "${stack_name}" \
        --destroy \
        --skip-bootstrap \
        --cdk-context "clusterName=${CLUSTER_NAME}" \
        --cdk-context "kubectlRoleArn=${KUBECTL_ROLE_ARN}" \
        --cdk-context "oidcIssuer=${OIDC_ISSUER}" \
        --cdk-context "imageTag=destroy"
    # The CDK kubectl provider Lambdas create their own log groups at runtime, so
    # CloudFormation does not delete them. Remove them so teardown leaves no orphans.
    # Log groups are regional and the stack name is per region, so this prefix only
    # matches this demo's Lambdas in this region.
    log_prefix="/aws/lambda/EksSelfHealingAgent-"
    for lg in $(aws logs describe-log-groups --region "${region}" \
            --log-group-name-prefix "${log_prefix}" \
            --query "logGroups[].logGroupName" --output text 2>/dev/null); do
        echo "  Deleting log group ${lg}"
        aws logs delete-log-group --region "${region}" --log-group-name "${lg}" || true
    done
    echo -e "\n\033[0;32mTeardown complete. The agent image stays in the shared CDK bootstrap asset repository.\033[0m"
    exit 0
fi

# 3. Validate inputs.
if [ -z "${CLUSTER_NAME}" ] || [ -z "${KUBECTL_ROLE_ARN}" ]; then
    echo -e "\033[0;31m--cluster-name and --kubectl-role-arn are required (see README Inputs)\033[0m"
    exit 1
fi
if [ -z "${OIDC_ISSUER}" ]; then
    OIDC_ISSUER="$(aws eks describe-cluster --name "${CLUSTER_NAME}" --region "${region}" \
        --query "cluster.identity.oidc.issuer" --output text 2>/dev/null || true)"
fi
if [ -z "${OIDC_ISSUER}" ] || [ "${OIDC_ISSUER}" = "None" ]; then
    echo -e "\033[0;31mCould not find an OIDC issuer for cluster ${CLUSTER_NAME} in ${region}. Check the name and region, or pass --oidc-issuer.\033[0m"
    exit 1
fi
issuer_host="${OIDC_ISSUER#https://}"
if ! aws iam get-open-id-connect-provider \
    --open-id-connect-provider-arn "arn:${partition}:iam::${account}:oidc-provider/${issuer_host}" >/dev/null 2>&1; then
    echo -e "\033[0;31mNo IAM OIDC provider for ${OIDC_ISSUER}. Create one first:\033[0m"
    echo "  eksctl utils associate-iam-oidc-provider --cluster ${CLUSTER_NAME} --region ${region} --approve"
    exit 1
fi

CONTEXT_ARGS=(
    --cdk-context "clusterName=${CLUSTER_NAME}"
    --cdk-context "kubectlRoleArn=${KUBECTL_ROLE_ARN}"
    --cdk-context "oidcIssuer=${OIDC_ISSUER}"
)
[ -z "${NODE_ARCHITECTURE}" ] || CONTEXT_ARGS+=(--cdk-context "nodeArchitecture=${NODE_ARCHITECTURE}")
[ -z "${MODEL_ID}" ] || CONTEXT_ARGS+=(--cdk-context "bedrockBaseModelId=${MODEL_ID}")

echo -e "\n\033[0;33mDeploying ${stack_name} onto cluster ${CLUSTER_NAME} (account: ${account})\033[0m"

# 4. Deploy through the shared CDK script (it owns bootstrap + deploy). Single
#    stack, so no --skip-bootstrap.
"${REPO_ROOT}/shared/scripts/deploy-cdk.sh" \
    --cdk-directory "infrastructure/cdk" \
    --stack-name "${stack_name}" \
    "${CONTEXT_ARGS[@]}"
if [ $? -ne 0 ]; then
    echo -e "\033[0;31mCDK deployment failed\033[0m"
    exit 1
fi

# 5. Read back CfnOutputs (no --outputs-file, no jq).
agent_role_arn="$(get_output AgentRoleArn)"
model_id_out="$(get_output BedrockModelId)"
image_uri="$(get_output ImageUri)"

echo -e "\n\033[0;32m========================================\033[0m"
echo -e "\033[0;32m  Deployment Complete!\033[0m"
echo -e "\033[0;32m========================================\033[0m"
echo -e "\033[0;36m  Region:        ${region}\033[0m"
echo -e "\033[0;36m  Cluster:       ${CLUSTER_NAME}\033[0m"
echo -e "\033[0;36m  Agent role:    ${agent_role_arn}\033[0m"
echo -e "\033[0;36m  Bedrock model: ${model_id_out}\033[0m"
echo -e "\033[0;36m  Image:         ${image_uri}\033[0m"
echo -e "\n\033[0;33mCheck the agent (kubectl pointed at the cluster):\033[0m"
echo "  kubectl get pods -l app=agatha"
echo "  kubectl logs -f deployment/agatha"
echo ""
echo -e "\033[0;90m  Next: see the README section 'Run the Demo'.\033[0m"
echo -e "\033[0;90m  Teardown: ./deploy-all.sh --destroy\033[0m"
