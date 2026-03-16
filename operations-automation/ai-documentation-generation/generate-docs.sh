#!/bin/bash
#
# Deploy AWS Transform Documentation Generator using CDK and CodeBuild
#
# This script deploys a CodeBuild-based solution that runs AWS Transform to generate
# comprehensive documentation from any Git repository. Infrastructure is deployed via CDK.
#
# Usage:
#   ./generate-docs.sh
#   ./generate-docs.sh -r "https://github.com/owner/repo"
#   ./generate-docs.sh -s
#
# Notes:
#   This script uses the AWS region configured in your AWS CLI profile.
#   To set your region: aws configure set region <your-region>
#

set -e

# Default values
DEFAULT_REPO="https://github.com/aws-samples/sample-serverless-digital-asset-payments"
REPOSITORY_URL=""
SKIP_SETUP=false

# Parse arguments
while getopts "r:sh" opt; do
  case $opt in
    r) REPOSITORY_URL="$OPTARG" ;;
    s) SKIP_SETUP=true ;;
    h)
      echo "Usage: $0 [-r repository_url] [-s]"
      echo "  -r  Git repository URL to analyze"
      echo "  -s  Skip infrastructure deployment, only start a build"
      echo ""
      echo "This script uses the AWS region configured in your AWS CLI profile."
      echo "To set your region: aws configure set region <your-region>"
      exit 0
      ;;
    \?) echo "Invalid option -$OPTARG" >&2; exit 1 ;;
  esac
done

echo -e "\033[0;36m=== AWS Transform Documentation Generator ===\033[0m"
echo -e "\033[0;32mGenerates comprehensive documentation from any Git repository using AWS Transform\033[0m"
echo ""
echo -e "\033[0;33mUses Enhanced AWS-managed transformation with custom organizational context:\033[0m"
echo -e "\033[0;90m  - AWS-maintained transformation logic for deep technical analysis\033[0m"
echo -e "\033[0;90m  - Custom context for structured, operational documentation\033[0m"
echo -e "\033[0;90m  - Role-based navigation and multi-audience content\033[0m"
echo -e "\033[0;90m  - Expected time: ~53 minutes\033[0m"
echo ""

TRANSFORMATION_TYPE="enhanced"
BUILDSPEC_FILE="buildspec.yml"
EXPECTED_TIME="~53 minutes"

echo ""

# Check if repository URL was provided
if [ -z "$REPOSITORY_URL" ]; then
  echo -e "\033[0;33mNo repository URL specified. Documentation will be generated based on the default sample repository:\033[0m"
  echo "  $DEFAULT_REPO"
  echo ""
  echo -e "\033[0;90mTo analyze your own repository, run:\033[0m"
  echo "  ./generate-docs.sh -r \"https://github.com/owner/repo\""
  echo ""
  REPOSITORY_URL="$DEFAULT_REPO"
fi

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CDK_DIR="$SCRIPT_DIR/infrastructure/cdk"
SHARED_SCRIPTS_DIR="$SCRIPT_DIR/../../shared/scripts"

if [ "$SKIP_SETUP" = false ]; then
    # Run prerequisites check
    echo -e "\033[0;33mRunning prerequisites check...\033[0m"
    "$SHARED_SCRIPTS_DIR/check-prerequisites.sh" \
        --required-service "transform" \
        --require-cdk

    # Get AWS account and region info
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --no-cli-pager)

    # Get region using shared utility
    source "$SHARED_SCRIPTS_DIR/../utils/get-aws-region.sh"
    CURRENT_REGION=$(get_aws_region)

    echo ""
    echo -e "\033[0;33mDeploying infrastructure via CDK...\033[0m"
    echo -e "\033[0;90m      Region: $CURRENT_REGION\033[0m"

    # Deploy CDK stack using shared script
    "$SHARED_SCRIPTS_DIR/deploy-cdk.sh" --cdk-directory "$CDK_DIR"

    if [ $? -ne 0 ]; then
        echo -e "\033[0;31mCDK deployment failed\033[0m"
        exit 1
    fi

    # Get bucket name and project name from CloudFormation outputs
    echo ""
    echo -e "\033[0;33mGetting stack outputs...\033[0m"
    STACK_NAME="DocumentationGeneratorStack-$CURRENT_REGION"
    OUTPUT_BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$CURRENT_REGION" --no-cli-pager --query "Stacks[0].Outputs[?OutputKey=='OutputBucketName'].OutputValue" --output text)
    PROJECT_NAME=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$CURRENT_REGION" --no-cli-pager --query "Stacks[0].Outputs[?OutputKey=='CodeBuildProjectName'].OutputValue" --output text)
    if [ -z "$OUTPUT_BUCKET" ] || [ -z "$PROJECT_NAME" ]; then
        echo -e "\033[0;31m      ❌ Failed to get stack outputs\033[0m"
        exit 1
    fi
    echo -e "\033[0;90m      Output Bucket: $OUTPUT_BUCKET\033[0m"
    echo -e "\033[0;90m      CodeBuild Project: $PROJECT_NAME\033[0m"

    # Upload appropriate buildspec to S3
    echo ""
    echo -e "\033[0;33mUploading buildspec to S3...\033[0m"
    if aws s3 cp "$SCRIPT_DIR/$BUILDSPEC_FILE" "s3://$OUTPUT_BUCKET/config/buildspec.yml" --region "$CURRENT_REGION" --no-cli-pager > /dev/null; then
        echo -e "\033[0;32m      ✓ Buildspec uploaded successfully ($BUILDSPEC_FILE)\033[0m"
    else
        echo -e "\033[0;31m      ❌ Failed to upload buildspec\033[0m"
        exit 1
    fi

    # Upload enhanced transformation context
    echo ""
    echo -e "\033[0;33mUploading enhanced transformation context...\033[0m"
    if aws s3 cp "$SCRIPT_DIR/enhanced-transformation/" "s3://$OUTPUT_BUCKET/enhanced-transformation/" --recursive --region "$CURRENT_REGION" --no-cli-pager > /dev/null; then
        echo -e "\033[0;32m      ✓ Enhanced transformation context uploaded successfully\033[0m"
    else
        echo -e "\033[0;31m      ❌ Failed to upload enhanced transformation context\033[0m"
        exit 1
    fi
else
    echo -e "\033[0;33m[SETUP] Skipping infrastructure deployment...\033[0m"
    
    # Get region using shared utility (prerequisites check was skipped)
    source "$SHARED_SCRIPTS_DIR/../utils/get-aws-region.sh"
    CURRENT_REGION=$(get_aws_region)
    
    # Get stack outputs
    echo ""
    echo -e "\033[0;33mGetting stack outputs...\033[0m"
    STACK_NAME="DocumentationGeneratorStack-$CURRENT_REGION"
    OUTPUT_BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$CURRENT_REGION" --no-cli-pager --query "Stacks[0].Outputs[?OutputKey=='OutputBucketName'].OutputValue" --output text 2>/dev/null)
    PROJECT_NAME=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$CURRENT_REGION" --no-cli-pager --query "Stacks[0].Outputs[?OutputKey=='CodeBuildProjectName'].OutputValue" --output text 2>/dev/null)
    
    if [ -z "$OUTPUT_BUCKET" ] || [ -z "$PROJECT_NAME" ]; then
        echo -e "\033[0;31m      ❌ Stack not found. Please deploy infrastructure first:\033[0m"
        echo -e "\033[0;90m      ./generate-docs.sh\033[0m"
        exit 1
    fi
    echo -e "\033[0;90m      Output Bucket: $OUTPUT_BUCKET\033[0m"
    echo -e "\033[0;90m      CodeBuild Project: $PROJECT_NAME\033[0m"
fi

# Start documentation generation build
echo ""
echo -e "\033[0;33mStarting documentation generation build...\033[0m"
echo -e "\033[0;90m      Repository: $REPOSITORY_URL\033[0m"
echo -e "\033[0;90m      Transformation: $TRANSFORMATION_TYPE\033[0m"
echo -e "\033[0;90m      Expected time: $EXPECTED_TIME\033[0m"

# Generate unique job ID
JOB_ID="doc-gen-$(date +%Y%m%d-%H%M%S)"

# Start build with environment variable overrides
BUILD_ID=$(aws codebuild start-build \
  --project-name "$PROJECT_NAME" \
  --region "$CURRENT_REGION" \
  --no-cli-pager \
  --environment-variables-override \
    "name=REPOSITORY_URL,value=$REPOSITORY_URL,type=PLAINTEXT" \
    "name=OUTPUT_BUCKET,value=$OUTPUT_BUCKET,type=PLAINTEXT" \
    "name=JOB_ID,value=$JOB_ID,type=PLAINTEXT" \
  --query 'build.id' --output text)

if [ $? -eq 0 ] && [ -n "$BUILD_ID" ]; then
    echo -e "\033[0;32m      ✓ Build started: $BUILD_ID\033[0m"
else
    echo -e "\033[0;31m      ❌ Failed to start build\033[0m"
    exit 1
fi

echo ""
echo -e "\033[0;36m=== Deployment Complete ===\033[0m"
echo ""
echo -e "\033[0;33mResources deployed via CDK:\033[0m"
echo "  - S3 Bucket: $OUTPUT_BUCKET"
echo "  - CodeBuild Project: $PROJECT_NAME"
echo ""
echo -e "\033[0;33mMonitor build progress:\033[0m"
echo "  aws codebuild batch-get-builds --ids $BUILD_ID --region $CURRENT_REGION --no-cli-pager"
echo ""
echo -e "\033[0;33mStream build logs:\033[0m"
echo "  aws logs tail /aws/codebuild/$PROJECT_NAME --follow --region $CURRENT_REGION"
echo ""
echo -e "\033[0;33mCheck generated documentation:\033[0m"
echo "  aws s3 ls s3://$OUTPUT_BUCKET/documentation/$JOB_ID/ --recursive"
echo ""
echo -e "\033[0;33mCurrently analyzing:\033[0m"
echo "  $REPOSITORY_URL"
echo ""
echo -e "\033[0;33mGenerate docs for a different repository:\033[0m"
echo "  aws codebuild start-build --project-name $PROJECT_NAME --region $CURRENT_REGION --no-cli-pager \\"
echo "    --environment-variables-override name=REPOSITORY_URL,value=https://github.com/owner/repo"

# Offer to wait and download documentation
echo ""
echo -e "\033[0;36m=== Download Documentation ===\033[0m"
echo -e "\033[0;33mThe build is expected to take $EXPECTED_TIME to complete depending on repository size.\033[0m"
echo ""
read -p "Would you like to wait for the build to complete and download the documentation? (y/n) " WAIT_CHOICE

if [ "$WAIT_CHOICE" = "y" ] || [ "$WAIT_CHOICE" = "Y" ]; then
  echo ""
  echo -e "\033[0;33mWaiting for build to complete...\033[0m"
  echo -e "\033[0;90mYou can press Ctrl+C to cancel and download later using:\033[0m"
  echo "  aws s3 cp s3://$OUTPUT_BUCKET/documentation/$JOB_ID/ ./generated-docs --recursive"
  echo ""
  
  # Poll for build completion
  BUILD_COMPLETE=false
  BUILD_STATUS=""
  START_TIME=$(date +%s)
  while [ "$BUILD_COMPLETE" = "false" ]; do
    sleep 30
    BUILD_INFO=$(aws codebuild batch-get-builds --ids "$BUILD_ID" --region "$CURRENT_REGION" --no-cli-pager \
      --query 'builds[0].buildStatus' --output text 2>/dev/null || echo "UNKNOWN")
    
    if [ "$BUILD_INFO" = "SUCCEEDED" ]; then
      BUILD_COMPLETE=true
      BUILD_STATUS="SUCCEEDED"
      echo -e "\033[0;32mBuild completed successfully!\033[0m"
    elif [ "$BUILD_INFO" = "FAILED" ] || [ "$BUILD_INFO" = "FAULT" ] || [ "$BUILD_INFO" = "STOPPED" ] || [ "$BUILD_INFO" = "TIMED_OUT" ]; then
      BUILD_COMPLETE=true
      BUILD_STATUS="$BUILD_INFO"
      echo -e "\033[0;31mBuild ended with status: $BUILD_INFO\033[0m"
    else
      CURRENT_TIME=$(date +%s)
      ELAPSED=$(( (CURRENT_TIME - START_TIME) / 60 ))
      echo -e "\033[0;90m  Build in progress... (Status: $BUILD_INFO, Elapsed: ${ELAPSED} min)\033[0m"
    fi
  done
  
  if [ "$BUILD_STATUS" = "SUCCEEDED" ]; then
    # Create local directory for documentation
    LOCAL_DOCS_DIR="./generated-docs-$JOB_ID"
    echo ""
    echo -e "\033[0;33mDownloading documentation to: $LOCAL_DOCS_DIR\033[0m"
    
    mkdir -p "$LOCAL_DOCS_DIR"
    aws s3 cp "s3://$OUTPUT_BUCKET/documentation/$JOB_ID/" "$LOCAL_DOCS_DIR" --recursive --region "$CURRENT_REGION" --no-cli-pager
    
    if [ $? -eq 0 ]; then
      echo ""
      echo -e "\033[0;32mDocumentation downloaded successfully!\033[0m"
      echo "Location: $LOCAL_DOCS_DIR"
      
      # List downloaded files
      echo ""
      echo -e "\033[0;33mDownloaded files:\033[0m"
      find "$LOCAL_DOCS_DIR" -type f | while read -r file; do
        echo "  $file"
      done
    else
      echo -e "\033[0;31mFailed to download documentation\033[0m"
    fi
  fi
else
  echo ""
  echo -e "\033[0;33mTo download documentation later (after build completes):\033[0m"
  echo "  aws s3 cp s3://$OUTPUT_BUCKET/documentation/$JOB_ID/ ./generated-docs --recursive"
fi