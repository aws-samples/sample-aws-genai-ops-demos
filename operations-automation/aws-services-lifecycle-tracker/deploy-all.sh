#!/bin/bash
# AWS Services Lifecycle Tracker - Complete Deployment Script
# macOS/Linux version

set -e  # Exit on error

echo -e "\033[0;36m=== AWS Services Lifecycle Tracker Deployment ===\033[0m"

# Run shared prerequisites check
echo -e "\n\033[0;33mRunning prerequisites check...\033[0m"
../../shared/scripts/check-prerequisites.sh --required-service "agentcore" --min-aws-cli-version "2.31.13" --require-cdk

# Install frontend dependencies
echo -e "\n\033[0;33mInstalling frontend dependencies...\033[0m"
echo -e "\033[0;90m      (Installing React, Vite, Cognito SDK, and UI component libraries)\033[0m"
pushd frontend > /dev/null
npm install
popd > /dev/null

# Create placeholder dist BEFORE any CDK commands
echo -e "\n\033[0;33mCreating placeholder frontend build...\033[0m"
echo -e "\033[0;90m      (Generating temporary HTML file - required for CDK synthesis)\033[0m"
if [ ! -d "frontend/dist" ]; then
    mkdir -p frontend/dist
    echo "<!DOCTYPE html><html><body><h1>Building...</h1></body></html>" > frontend/dist/index.html
else
    echo -e "\033[0;90m      Placeholder already exists, skipping...\033[0m"
fi

# Deploy infrastructure stack
echo -e "\n\033[0;33mDeploying infrastructure stack...\033[0m"
echo -e "\033[0;90m      (Creating ECR repository, CodeBuild project, S3 bucket for agent builds, and IAM roles)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "AWSServicesLifecycleTrackerInfra"

# Deploy data stack
echo -e "\n\033[0;33mDeploying data stack...\033[0m"
echo -e "\033[0;90m      (Creating DynamoDB tables and populating service configurations for 7 AWS services)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "AWSServicesLifecycleTrackerData" --skip-bootstrap

# Deploy auth stack
echo -e "\n\033[0;33mDeploying authentication stack...\033[0m"
echo -e "\033[0;90m      (Creating Cognito User Pool with email verification and password policies)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "AWSServicesLifecycleTrackerAuth" --skip-bootstrap

# Deploy runtime stack
echo -e "\n\033[0;33mDeploying AgentCore runtime stack...\033[0m"
echo -e "\033[0;90m      (Uploading agent code, building ARM64 Docker image via CodeBuild, creating AgentCore runtime)\033[0m"
echo -e "\033[0;90m      Note: CodeBuild will compile the container image - this takes 5-10 minutes\033[0m"
echo -e "\033[0;90m      The deployment will pause while waiting for the build to complete...\033[0m"

if ! ../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "AWSServicesLifecycleTrackerRuntime" --skip-bootstrap 2>&1 | tee /tmp/agentcore-deploy.log; then
    if grep -q "Unrecognized resource types.*BedrockAgentCore" /tmp/agentcore-deploy.log; then
        CURRENT_REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-unknown}}"
        echo -e "\n\033[0;31m❌ DEPLOYMENT FAILED: AgentCore is not available in region '$CURRENT_REGION'\033[0m"
        echo -e ""
        echo -e "\033[0;33mPlease verify AgentCore availability in your target region:\033[0m"
        echo -e "\033[0;36mhttps://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html\033[0m"
        echo -e ""
        echo -e "\033[0;33mTo deploy to a supported region, set the AWS_DEFAULT_REGION environment variable:\033[0m"
        echo -e "\033[0;90m  export AWS_DEFAULT_REGION=\"your-supported-region\"\033[0m"
        echo -e "\033[0;90m  export AWS_REGION=\"your-supported-region\"\033[0m"
        echo -e "\033[0;90m  ./deploy-all.sh\033[0m"
        exit 1
    fi
    echo -e "\033[0;31mBackend deployment failed\033[0m"
    exit 1
fi

# Build and deploy frontend (after backend is complete)
echo -e "\n\033[0;33mBuilding and deploying frontend...\033[0m"
echo -e "\033[0;90m      (Retrieving AgentCore Runtime ID and Cognito config, building React app, deploying to S3 + CloudFront)\033[0m"
AGENT_RUNTIME_ARN=$(aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerRuntime --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager)
REGION=$(aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerRuntime --query "Stacks[0].Outputs[?OutputKey=='Region'].OutputValue" --output text --no-cli-pager)
USER_POOL_ID=$(aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerAuth --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text --no-cli-pager)
USER_POOL_CLIENT_ID=$(aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerAuth --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" --output text --no-cli-pager)
IDENTITY_POOL_ID=$(aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerAuth --query "Stacks[0].Outputs[?OutputKey=='IdentityPoolId'].OutputValue" --output text --no-cli-pager)

if [ -z "$AGENT_RUNTIME_ARN" ]; then
    echo -e "\033[0;31mFailed to get Agent Runtime ARN from stack outputs\033[0m"
    exit 1
fi

if [ -z "$REGION" ]; then
    echo -e "\033[0;31mFailed to get Region from stack outputs\033[0m"
    exit 1
fi

if [ -z "$USER_POOL_ID" ] || [ -z "$USER_POOL_CLIENT_ID" ] || [ -z "$IDENTITY_POOL_ID" ]; then
    echo -e "\033[0;31mFailed to get Cognito config from stack outputs\033[0m"
    exit 1
fi

echo -e "\033[0;32mAgent Runtime ARN: $AGENT_RUNTIME_ARN\033[0m"
echo -e "\033[0;32mRegion: $REGION\033[0m"
echo -e "\033[0;32mUser Pool ID: $USER_POOL_ID\033[0m"
echo -e "\033[0;32mUser Pool Client ID: $USER_POOL_CLIENT_ID\033[0m"
echo -e "\033[0;32mIdentity Pool ID: $IDENTITY_POOL_ID\033[0m"

# Build frontend with AgentCore Runtime ARN and Cognito config
./scripts/build-frontend.sh "$USER_POOL_ID" "$USER_POOL_CLIENT_ID" "$IDENTITY_POOL_ID" "$AGENT_RUNTIME_ARN" "$REGION"

# Deploy scheduler stack (optional but recommended for automated extractions)
echo -e "\nDeploying scheduler stack...\033[0m"
echo -e "\033[0;90m      (Creating EventBridge rules for automated weekly/monthly extractions)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "AWSServicesLifecycleTrackerScheduler" --skip-bootstrap

# Deploy frontend stack
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "AWSServicesLifecycleTrackerFrontend" --skip-bootstrap

# Get CloudFront URL
WEBSITE_URL=$(aws cloudformation describe-stacks --stack-name AWSServicesLifecycleTrackerFrontend --query "Stacks[0].Outputs[?OutputKey=='WebsiteUrl'].OutputValue" --output text --no-cli-pager)

echo -e "\n\033[0;32m=== Deployment Complete ===\033[0m"
echo -e "\033[0;36mWebsite URL: $WEBSITE_URL\033[0m"
echo -e "\033[0;36mAgent Runtime ARN: $AGENT_RUNTIME_ARN\033[0m"
echo -e "\033[0;36mRegion: $REGION\033[0m"
echo -e "\033[0;36mUser Pool ID: $USER_POOL_ID\033[0m"
echo -e "\033[0;36mUser Pool Client ID: $USER_POOL_CLIENT_ID\033[0m"
echo -e "\n\033[0;33mNext Steps:\033[0m"
echo -e "\033[0;90m  1. Sign up at the Website URL above (email verification required)\033[0m"
echo -e "\033[0;90m  2. Initial extraction is running in background - results will appear in DynamoDB\033[0m"
echo -e "\033[0;90m  3. View extracted data via admin UI or query DynamoDB table aws-services-lifecycle\033[0m"
