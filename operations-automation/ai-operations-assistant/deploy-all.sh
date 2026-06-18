#!/bin/bash
# G.O.A.T. (GenAI Operations Analytics Tool) - Complete Deployment Script
#
# Deploys the multi-agent orchestration solution with modular deployment modes.
# Uses shared/scripts/deploy-cdk.sh for each stack in dependency order.

set -e

# ---------------------------------------------------------------------------
# Parse command line arguments
# ---------------------------------------------------------------------------
DEPLOYMENT_MODE="full"
ORCH_MODEL_ID=""
ORCH_MODEL_ID_PROVIDED=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --mode)
            DEPLOYMENT_MODE="$2"
            shift 2
            ;;
        --orch-model-id)
            ORCH_MODEL_ID="$2"
            ORCH_MODEL_ID_PROVIDED=true
            shift 2
            ;;
        --vpc-id)
            VPC_ID="$2"
            shift 2
            ;;
        --subnet-ids)
            SUBNET_IDS="$2"
            shift 2
            ;;
        --vpc-cidr)
            VPC_CIDR="$2"
            shift 2
            ;;
        --skip-vpc-endpoints)
            SKIP_VPC_ENDPOINTS=true
            shift
            ;;
        --collector-instance-type)
            COLLECTOR_INSTANCE_TYPE="$2"
            shift 2
            ;;
        --collector-volume-gib)
            COLLECTOR_VOLUME_GIB="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--mode full|cost|health|support|trusted-advisor|cur|network] [--orch-model-id MODEL_ID] [--vpc-id VPC_ID] [--subnet-ids SUBNET_IDS] [--vpc-cidr CIDR] [--skip-vpc-endpoints] [--collector-instance-type TYPE] [--collector-volume-gib SIZE]"
            exit 1
            ;;
    esac
done

# Validate --orch-model-id parameter
if [ "$ORCH_MODEL_ID_PROVIDED" = true ] && [ -z "$ORCH_MODEL_ID" ]; then
    echo -e "\033[0;31mError: --orch-model-id requires a non-empty Bedrock model identifier\033[0m"
    echo -e "\033[0;90mExample: --orch-model-id 'global.amazon.nova-pro-v1:0'\033[0m"
    exit 1
fi

# Validate deployment mode
case "$DEPLOYMENT_MODE" in
    full|cost|health|support|trusted-advisor|cur|network) ;;
    *)
        echo -e "\033[0;31mInvalid deployment mode: $DEPLOYMENT_MODE\033[0m"
        echo "Valid modes: full, cost, health, support, trusted-advisor, cur, network"
        exit 1
        ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED_SCRIPTS_DIR="$SCRIPT_DIR/../../shared/scripts"
CDK_DIR="infrastructure/cdk"

echo -e "\033[0;36m=== G.O.A.T. - GenAI Operations Analytics Tool Deployment ===\033[0m"
echo -e "\033[0;90m      Deployment Mode: $DEPLOYMENT_MODE\033[0m"

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
echo -e "\n\033[0;33mRunning prerequisites check...\033[0m"
"$SHARED_SCRIPTS_DIR/check-prerequisites.sh" --required-service agentcore --min-aws-cli-version 2.31.13 --require-cdk

if [ $? -ne 0 ]; then
    echo -e "\033[0;31mPrerequisites check failed\033[0m"
    exit 1
fi

region="$AWS_REGION"

# ---------------------------------------------------------------------------
# Install frontend dependencies and create placeholder dist
# CDK synthesizes all stacks even when deploying one, so frontend/dist must exist
# ---------------------------------------------------------------------------
echo -e "\n\033[0;33mInstalling frontend dependencies...\033[0m"
echo -e "\033[0;90m      (Installing React, Vite, Cognito SDK, and Cloudscape components)\033[0m"
pushd frontend > /dev/null
# Remove stale environment config and build artifacts from prior installs.
# The correct values are regenerated in section 7 after all stacks are deployed.
if [ -f ".env.production.local" ]; then
    rm -f ".env.production.local"
    echo -e "\033[0;90m      Removed stale .env.production.local (will regenerate after deploy)\033[0m"
fi
if [ -d "dist" ]; then
    rm -rf "dist"
    echo -e "\033[0;90m      Removed stale dist/ (will rebuild after deploy)\033[0m"
fi
npm install
popd > /dev/null

echo -e "\n\033[0;33mCreating placeholder frontend build...\033[0m"
echo -e "\033[0;90m      (Generating temporary HTML file - required for CDK synthesis)\033[0m"
if [ ! -d "frontend/dist" ]; then
    mkdir -p frontend/dist
    echo '<!DOCTYPE html><html><body><h1>Building...</h1></body></html>' > frontend/dist/index.html
else
    echo -e "\033[0;90m      Placeholder already exists, skipping...\033[0m"
fi

# ---------------------------------------------------------------------------
# Determine which modules to deploy based on mode
# ---------------------------------------------------------------------------
declare -a DEPLOY_MODULES

case "$DEPLOYMENT_MODE" in
    full)             DEPLOY_MODULES=("Cost" "Health" "Support" "TA" "CUR") ;;
    cost)             DEPLOY_MODULES=("Cost") ;;
    health)           DEPLOY_MODULES=("Health") ;;
    support)          DEPLOY_MODULES=("Support") ;;
    trusted-advisor)  DEPLOY_MODULES=("TA") ;;
    cur)              DEPLOY_MODULES=("CUR") ;;
    network)          DEPLOY_MODULES=() ;;
esac

echo -e "\n\033[0;36mModules to deploy: ${DEPLOY_MODULES[*]}\033[0m"

# ---------------------------------------------------------------------------
# Helper: deploy a single stack with error handling
# ---------------------------------------------------------------------------
deploy_stack() {
    local stack_name="$1"
    local description="$2"
    local skip_bootstrap="$3"

    echo -e "\n\033[0;33mDeploying $stack_name...\033[0m"
    echo -e "\033[0;90m      ($description)\033[0m"

    # Pre-check: if the stack is stuck in DELETE_FAILED from a prior run,
    # force-delete it first. This is common with AgentCore runtimes that
    # timeout during deletion -- not a real error, just a CFN timeout.
    local stack_status=""
    stack_status=$(aws cloudformation describe-stacks --stack-name "$stack_name" \
        --query "Stacks[0].StackStatus" --output text --no-cli-pager 2>/dev/null) || stack_status="DOES_NOT_EXIST"
    # Guard: if the result contains unexpected characters (not a valid status), treat as non-existent
    if [[ ! "$stack_status" =~ ^[A-Z_]+$ ]]; then
        stack_status="DOES_NOT_EXIST"
    fi
    if [ "$stack_status" = "DELETE_FAILED" ]; then
        echo -e "\033[0;33m      Stack is in DELETE_FAILED state (normal - AgentCore runtime deletion timeout).\033[0m"
        echo -e "\033[0;33m      Force-deleting before redeploy...\033[0m"
        local failed_resources=""
        failed_resources=$(aws cloudformation describe-stack-resources --stack-name "$stack_name" \
            --query "StackResources[?ResourceStatus=='DELETE_FAILED'].LogicalResourceId" \
            --output text --no-cli-pager 2>/dev/null || echo "")
        if [ -n "$failed_resources" ] && [ "$failed_resources" != "None" ]; then
            aws cloudformation delete-stack --stack-name "$stack_name" \
                --retain-resources $failed_resources --no-cli-pager 2>/dev/null || true
        else
            aws cloudformation delete-stack --stack-name "$stack_name" --no-cli-pager 2>/dev/null || true
        fi
        aws cloudformation wait stack-delete-complete --stack-name "$stack_name" --no-cli-pager 2>/dev/null || true
        echo -e "\033[0;32m      Done - proceeding with fresh deploy.\033[0m"
    fi

    # Build CDK context args for "Bring Your Own VPC" if provided
    local extra_args=""
    if [[ "$stack_name" == *"NetworkInfra"* ]] || [[ "$stack_name" == *"NetworkData"* ]]; then
        local context_parts=""
        [ -n "$VPC_ID" ] && context_parts="$context_parts -c goatExistingVpcId=$VPC_ID"
        [ -n "$SUBNET_IDS" ] && context_parts="$context_parts -c goatCollectorSubnetIds=$SUBNET_IDS"
        [ -n "$VPC_CIDR" ] && context_parts="$context_parts -c goatVpcCidr=$VPC_CIDR"
        [ "$SKIP_VPC_ENDPOINTS" = "true" ] && context_parts="$context_parts -c goatSkipVpcEndpoints=true"
        [ -n "$COLLECTOR_INSTANCE_TYPE" ] && context_parts="$context_parts -c goatCollectorInstanceType=$COLLECTOR_INSTANCE_TYPE"
        [ -n "$COLLECTOR_VOLUME_GIB" ] && context_parts="$context_parts -c goatCollectorVolumeGib=$COLLECTOR_VOLUME_GIB"
        extra_args="$context_parts"
    fi

    local deploy_args=("--cdk-directory" "$CDK_DIR" "--stack-name" "$stack_name")
    if [ "$skip_bootstrap" = "true" ]; then
        deploy_args+=("--skip-bootstrap")
    fi
    if [ -n "$extra_args" ]; then
        deploy_args+=("--extra-args" "$extra_args")
    fi

    "$SHARED_SCRIPTS_DIR/deploy-cdk.sh" "${deploy_args[@]}"

    if [ $? -ne 0 ]; then
        echo -e "\033[0;31mDeployment of $stack_name failed\033[0m"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# 1. Core Stacks (always deployed)
# ---------------------------------------------------------------------------
echo -e "\n\033[0;35m--- Core Stacks ---\033[0m"

deploy_stack "GOATAuth-$region" \
    "Creating Cognito User Pool, Identity Pool, and app client" \
    "false"

deploy_stack "GOATData-$region" \
    "Creating DynamoDB tables for conversations, knowledge articles, and user preferences" \
    "true"

# ---------------------------------------------------------------------------
# 2. Infrastructure Stacks per module (ECR, CodeBuild, S3, IAM)
# ---------------------------------------------------------------------------
echo -e "\n\033[0;35m--- Infrastructure Stacks ---\033[0m"

for module in "${DEPLOY_MODULES[@]}"; do
    deploy_stack "GOAT${module}Infra-$region" \
        "Creating ECR repository, CodeBuild project, S3 bucket, and IAM role for $module agent" \
        "true"
done

# ---------------------------------------------------------------------------
# 3. Runtime Stacks per module (upload source, build container, create AgentCore)
# ---------------------------------------------------------------------------
echo -e "\n\033[0;35m--- Runtime Stacks ---\033[0m"
echo -e "\033[0;90m      Note: Each runtime stack builds an ARM64 Docker image via CodeBuild (5-10 min each)\033[0m"

for module in "${DEPLOY_MODULES[@]}"; do
    deploy_stack "GOAT${module}Runtime-$region" \
        "Uploading $module agent code, building container image, creating AgentCore runtime" \
        "true"
done

# ---------------------------------------------------------------------------
# 4. Network Agent Stacks (full mode or network mode)
# ---------------------------------------------------------------------------
if [ "$DEPLOYMENT_MODE" = "full" ] || [ "$DEPLOYMENT_MODE" = "network" ]; then
    echo -e "\n\033[0;35m--- Network Agent Stacks ---\033[0m"

    # Check if GOATSharedDataBucketName export exists; deploy NetworkDataStack if absent
    shared_bucket_export=$(aws cloudformation list-exports --query "Exports[?Name=='GOATSharedDataBucketName'].Value" --output text --no-cli-pager 2>/dev/null || echo "")

    if [ -z "$shared_bucket_export" ] || [ "$shared_bucket_export" = "None" ]; then
        deploy_stack "GOATNetworkData-$region" \
            "Creating dedicated Network Data S3 bucket (shared bucket not available)" \
            "true"
    else
        echo -e "\n\033[0;90m      Shared data bucket found ($shared_bucket_export), skipping NetworkDataStack\033[0m"
    fi

    deploy_stack "GOATNetworkInfra-$region" \
        "Creating ECR repository, CodeBuild project, EC2 collector, Traffic Mirror plumbing, DynamoDB tables, Glue catalog, and Step Functions for Network agent" \
        "true"

    deploy_stack "GOATNetworkRuntime-$region" \
        "Uploading Network agent code, building container image, creating AgentCore runtime" \
        "true"
fi

# ---------------------------------------------------------------------------
# 5. Orchestration Stacks (full mode only)
# ---------------------------------------------------------------------------
if [ "$DEPLOYMENT_MODE" = "full" ]; then
    echo -e "\n\033[0;35m--- Orchestration Stacks ---\033[0m"

    deploy_stack "GOATOrchInfra-$region" \
        "Creating ECR repository, CodeBuild project, S3 bucket, and IAM role for orchestration agent" \
        "true"

    # Set ORCH_MODEL_ID environment variable for OrchRuntimeStack when --orch-model-id is supplied
    if [ -n "$ORCH_MODEL_ID" ]; then
        export ORCH_MODEL_ID="$ORCH_MODEL_ID"
        echo -e "\033[0;90m      Setting ORCH_MODEL_ID=$ORCH_MODEL_ID for orchestration runtime\033[0m"
    fi

    # Deploy orchestration runtime with special error handling for AgentCore availability
    echo -e "\n\033[0;33mDeploying GOATOrchRuntime-$region...\033[0m"
    echo -e "\033[0;90m      (Uploading orchestration agent code, building container image, creating AgentCore runtime)\033[0m"

    set +e
    cdk_output=$("$SHARED_SCRIPTS_DIR/deploy-cdk.sh" --cdk-directory "$CDK_DIR" --stack-name "GOATOrchRuntime-$region" --skip-bootstrap 2>&1)
    cdk_exit=$?
    set -e

    if [ $cdk_exit -ne 0 ]; then
        if echo "$cdk_output" | grep -q "Unrecognized resource types.*BedrockAgentCore"; then
            echo -e "\n\033[0;31mDEPLOYMENT FAILED: AgentCore is not available in region '$region'\033[0m"
            echo ""
            echo -e "\033[0;33mPlease verify AgentCore availability in your target region:\033[0m"
            echo -e "\033[0;36mhttps://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html\033[0m"
            echo ""
            echo -e "\033[0;33mTo deploy to a supported region, configure your AWS CLI:\033[0m"
            echo -e "\033[0;90m  aws configure set region <your-supported-region>\033[0m"
            echo -e "\033[0;90m  ./deploy-all.sh\033[0m"
            exit 1
        fi
        echo -e "\033[0;31mOrchestration runtime deployment failed\033[0m"
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# 6. Retrieve stack outputs for frontend build
# ---------------------------------------------------------------------------
echo -e "\n\033[0;35m--- Retrieving Stack Outputs ---\033[0m"

user_pool_id=$(aws cloudformation describe-stacks --stack-name "GOATAuth-$region" \
    --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text --no-cli-pager)
user_pool_client_id=$(aws cloudformation describe-stacks --stack-name "GOATAuth-$region" \
    --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" --output text --no-cli-pager)
identity_pool_id=$(aws cloudformation describe-stacks --stack-name "GOATAuth-$region" \
    --query "Stacks[0].Outputs[?OutputKey=='IdentityPoolId'].OutputValue" --output text --no-cli-pager)

if [ -z "$user_pool_id" ] || [ -z "$user_pool_client_id" ] || [ -z "$identity_pool_id" ]; then
    echo -e "\033[0;31mFailed to retrieve Cognito configuration from GOATAuth-$region stack outputs\033[0m"
    exit 1
fi

# Retrieve orchestration agent ARN (full mode) or first available sub-agent ARN (single module)
agent_runtime_arn=""
if [ "$DEPLOYMENT_MODE" = "full" ]; then
    agent_runtime_arn=$(aws cloudformation describe-stacks --stack-name "GOATOrchRuntime-$region" \
        --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager)
elif [ "$DEPLOYMENT_MODE" = "network" ]; then
    # In network mode, use the Network Agent runtime ARN
    agent_runtime_arn=$(aws cloudformation describe-stacks --stack-name "GOATNetworkRuntime-$region" \
        --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager)
else
    # In single-module mode, use the deployed module's runtime ARN
    module_stack_name="GOAT${DEPLOY_MODULES[0]}Runtime-$region"
    agent_runtime_arn=$(aws cloudformation describe-stacks --stack-name "$module_stack_name" \
        --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager)
fi

if [ -z "$agent_runtime_arn" ]; then
    echo -e "\033[0;31mFailed to retrieve Agent Runtime ARN from stack outputs\033[0m"
    exit 1
fi

echo -e "\033[0;32m      User Pool ID:        $user_pool_id\033[0m"
echo -e "\033[0;32m      User Pool Client ID:  $user_pool_client_id\033[0m"
echo -e "\033[0;32m      Identity Pool ID:     $identity_pool_id\033[0m"
echo -e "\033[0;32m      Agent Runtime ARN:    $agent_runtime_arn\033[0m"
echo -e "\033[0;32m      Region:               $region\033[0m"

# Retrieve Network Agent runtime ARN when applicable
network_agent_arn=""
if [ "$DEPLOYMENT_MODE" = "full" ] || [ "$DEPLOYMENT_MODE" = "network" ]; then
    network_agent_arn=$(aws cloudformation describe-stacks --stack-name "GOATNetworkRuntime-$region" \
        --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager)
    if [ -z "$network_agent_arn" ]; then
        echo -e "\033[0;31mFailed to retrieve Network Agent Runtime ARN from GOATNetworkRuntime-$region stack outputs\033[0m"
        exit 1
    fi
    echo -e "\033[0;32m      Network Agent ARN:    $network_agent_arn\033[0m"
fi

# ---------------------------------------------------------------------------
# 7. Build frontend with retrieved outputs
# ---------------------------------------------------------------------------
echo -e "\n\033[0;35m--- Building Frontend ---\033[0m"
echo -e "\033[0;90m      (Injecting Cognito config and Agent Runtime ARN, building React app)\033[0m"

./scripts/build-frontend.sh \
    --user-pool-id "$user_pool_id" \
    --user-pool-client-id "$user_pool_client_id" \
    --identity-pool-id "$identity_pool_id" \
    --agent-runtime-arn "$agent_runtime_arn" \
    --region "$region"

if [ $? -ne 0 ]; then
    echo -e "\033[0;31mFrontend build failed\033[0m"
    exit 1
fi

# ---------------------------------------------------------------------------
# 8. Deploy Frontend Stack (always last)
# ---------------------------------------------------------------------------
echo -e "\n\033[0;35m--- Frontend Stack ---\033[0m"

deploy_stack "GOATFrontend-$region" \
    "Deploying React app to S3 + CloudFront with OAC" \
    "true"

# ---------------------------------------------------------------------------
# 9. Deployment Summary
# ---------------------------------------------------------------------------
website_url=$(aws cloudformation describe-stacks --stack-name "GOATFrontend-$region" \
    --query "Stacks[0].Outputs[?OutputKey=='WebsiteUrl'].OutputValue" --output text --no-cli-pager)

if [ -z "$website_url" ]; then
    echo -e "\033[0;31mFailed to retrieve Website URL from GOATFrontend-$region stack outputs\033[0m"
    exit 1
fi

echo ""
echo -e "\033[0;32m========================================\033[0m"
echo -e "\033[0;32m  G.O.A.T. Deployment Complete!\033[0m"
echo -e "\033[0;32m========================================\033[0m"
echo ""
echo -e "\033[0;36m  Website URL:          $website_url\033[0m"
echo -e "\033[0;36m  Deployment Mode:      $DEPLOYMENT_MODE\033[0m"
echo -e "\033[0;36m  Region:               $region\033[0m"
echo -e "\033[0;36m  Agent Runtime ARN:    $agent_runtime_arn\033[0m"
echo -e "\033[0;36m  User Pool ID:         $user_pool_id\033[0m"
echo ""
echo -e "\033[0;36m  Deployed Modules: ${DEPLOY_MODULES[*]}\033[0m"
if [ "$DEPLOYMENT_MODE" = "full" ] || [ "$DEPLOYMENT_MODE" = "network" ]; then
    echo -e "\033[0;36m  Network Agent:        Deployed (BedrockAgentCoreApp + Nova Lite)\033[0m"
    echo -e "\033[0;36m  Network Agent ARN:    $network_agent_arn\033[0m"
fi
if [ "$DEPLOYMENT_MODE" = "full" ]; then
    echo -e "\033[0;36m  Orchestration Agent:  Deployed (Strands Agent SDK + Nova Pro)\033[0m"
fi
if [ -n "$ORCH_MODEL_ID" ]; then
    echo -e "\033[0;36m  Orchestration Model:  $ORCH_MODEL_ID\033[0m"
fi
echo ""
echo -e "\033[0;33m  Next Steps:\033[0m"
echo -e "\033[0;90m    1. Create an admin user (copy-paste these two commands):\033[0m"
echo ""
echo -e "\033[0;37m       aws cognito-idp admin-create-user --user-pool-id $user_pool_id --username admin --user-attributes Name=email,Value=admin@company.com Name=email_verified,Value=true --message-action SUPPRESS\033[0m"
echo ""
echo -e "\033[0;37m       aws cognito-idp admin-set-user-password --user-pool-id $user_pool_id --username admin --password \"YourSecurePassword123!\" --permanent\033[0m"
echo ""
echo -e "\033[0;90m       (Replace the email and password with your own values)\033[0m"
echo -e "\033[0;90m    2. Sign in at the Website URL above with your created admin credentials\033[0m"
echo -e "\033[0;90m    3. Try a query like: 'What are my top cost optimization opportunities?'\033[0m"
if [ "$DEPLOYMENT_MODE" != "full" ]; then
    echo -e "\033[0;90m    4. To add more modules later, re-run with --mode full\033[0m"
fi
if [ "$DEPLOYMENT_MODE" = "network" ] || [ "$DEPLOYMENT_MODE" = "full" ]; then
    echo -e "\033[0;90m    Note: To use capture actions, add users to the GOATNetworkCaptureUsers Cognito group\033[0m"
fi
echo ""
