#!/bin/bash
# GenAI Ops Demo Library - Shared CDK Deployment Script
# This script handles CDK bootstrap, dependency installation, and deployment

set -e

# Parse arguments
CDK_DIRECTORY=""
STACK_NAME=""
DESTROY_STACK=false
SKIP_BOOTSTRAP=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --cdk-directory)
            CDK_DIRECTORY="$2"
            shift 2
            ;;
        --stack-name)
            STACK_NAME="$2"
            shift 2
            ;;
        --destroy)
            DESTROY_STACK=true
            shift
            ;;
        --skip-bootstrap)
            SKIP_BOOTSTRAP=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 --cdk-directory <path> [--stack-name <name>] [--destroy] [--skip-bootstrap]"
            exit 1
            ;;
    esac
done

if [ -z "$CDK_DIRECTORY" ]; then
    echo "❌ --cdk-directory is required"
    exit 1
fi

# Get AWS account and region
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --no-cli-pager)
CURRENT_REGION=$(aws configure get region)

if [ -z "$CURRENT_REGION" ]; then
    echo -e "\033[0;31m❌ No AWS region configured\033[0m"
    exit 1
fi

echo ""
echo -e "\033[0;36m=== CDK Deployment (Shared Script) ===\033[0m"
echo -e "\033[0;90m      Directory: $CDK_DIRECTORY\033[0m"
echo -e "\033[0;90m      Region: $CURRENT_REGION\033[0m"
echo -e "\033[0;90m      Account: $ACCOUNT_ID\033[0m"

# Verify CDK directory exists
if [ ! -d "$CDK_DIRECTORY" ]; then
    echo -e "\033[0;31m❌ CDK directory not found: $CDK_DIRECTORY\033[0m"
    exit 1
fi

pushd "$CDK_DIRECTORY" > /dev/null

# Install dependencies
echo ""
echo -e "\033[0;33mInstalling CDK dependencies...\033[0m"

if [ -f "requirements.txt" ]; then
    # Python CDK project
    pip3 install -r requirements.txt -q
    echo -e "\033[0;32m      ✓ Python CDK dependencies installed\033[0m"
elif [ -f "package.json" ]; then
    # TypeScript/JavaScript CDK project
    if [ ! -d "node_modules" ]; then
        npm install
    fi
    echo -e "\033[0;32m      ✓ Node.js CDK dependencies installed\033[0m"
else
    echo -e "\033[0;33m      ⚠ No requirements.txt or package.json found\033[0m"
fi

# Bootstrap CDK (always run to ensure latest version)
if [ "$SKIP_BOOTSTRAP" = false ]; then
    echo ""
    echo -e "\033[0;33mEnsuring CDK bootstrap is up to date...\033[0m"
    npx -y cdk bootstrap "aws://$ACCOUNT_ID/$CURRENT_REGION" --no-cli-pager > /dev/null 2>&1
    if [ $? -ne 0 ]; then
        echo -e "\033[0;31m      ❌ CDK bootstrap failed\033[0m"
        exit 1
    fi
    echo -e "\033[0;32m      ✓ CDK bootstrap is up to date\033[0m"
fi

# Deploy or destroy stack
if [ "$DESTROY_STACK" = true ]; then
    echo ""
    echo -e "\033[0;33mDestroying CDK stack...\033[0m"
    if [ -z "$STACK_NAME" ]; then
        npx -y cdk destroy --force --no-cli-pager
    else
        npx -y cdk destroy "$STACK_NAME" --force --no-cli-pager
    fi
    echo -e "\033[0;32m      ✓ Stack destroyed\033[0m"
else
    echo ""
    echo -e "\033[0;33mDeploying CDK stack...\033[0m"
    if [ -z "$STACK_NAME" ]; then
        npx -y cdk deploy --require-approval never --no-cli-pager
    else
        npx -y cdk deploy "$STACK_NAME" --require-approval never --no-cli-pager
    fi
    echo -e "\033[0;32m      ✓ Stack deployed successfully\033[0m"
fi

popd > /dev/null

# Export variables for use by calling script
export CDK_ACCOUNT_ID="$ACCOUNT_ID"
export CDK_REGION="$CURRENT_REGION"
