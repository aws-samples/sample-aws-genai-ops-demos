#!/bin/bash
#
# Deploy AWS Transform Documentation Generator using CodeBuild
#
# This script deploys a CodeBuild-based solution that runs AWS Transform to generate
# comprehensive documentation from any Git repository. Simple and cost-effective.
#
# Usage:
#   ./generate-docs.sh
#   ./generate-docs.sh -r "https://github.com/owner/repo"
#   ./generate-docs.sh -g us-west-2
#

set -e

# Default values
DEFAULT_REPO="https://github.com/aws-samples/sample-serverless-digital-asset-payments"
REPOSITORY_URL=""
OUTPUT_BUCKET="${OUTPUT_BUCKET:-}"
REGION="${AWS_REGION:-us-east-1}"

# Parse arguments
while getopts "r:b:g:h" opt; do
  case $opt in
    r) REPOSITORY_URL="$OPTARG" ;;
    b) OUTPUT_BUCKET="$OPTARG" ;;
    g) REGION="$OPTARG" ;;
    h)
      echo "Usage: $0 [-r repository_url] [-b output_bucket] [-g region]"
      echo "  -r  Git repository URL to analyze"
      echo "  -b  S3 bucket name for output (optional - will be created)"
      echo "  -g  AWS region (default: us-east-1)"
      exit 0
      ;;
    \?) echo "Invalid option -$OPTARG" >&2; exit 1 ;;
  esac
done

echo "=== AWS Transform Documentation Generator (CodeBuild Version) ==="
echo "Generates comprehensive documentation from any Git repository"
echo ""

# Check if repository URL was provided
if [ -z "$REPOSITORY_URL" ]; then
  echo "No repository URL specified. Documentation will be generated based on the default sample repository:"
  echo "  $DEFAULT_REPO"
  echo ""
  echo "To analyze your own repository, run:"
  echo "  ./generate-docs.sh -r \"https://github.com/owner/repo\""
  echo ""
  REPOSITORY_URL="$DEFAULT_REPO"
fi

# Run prerequisites check
echo "Running prerequisites check..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/../../shared/scripts/check-prerequisites.sh" ]; then
  source "$SCRIPT_DIR/../../shared/scripts/check-prerequisites.sh" --required-service transform
fi

# Get account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
if [ -z "$ACCOUNT_ID" ]; then
  echo "Failed to get AWS account ID"
  exit 1
fi

# Set default output bucket if not provided
if [ -z "$OUTPUT_BUCKET" ]; then
  OUTPUT_BUCKET="doc-gen-output-${ACCOUNT_ID}-${REGION}"
fi

echo ""
echo "[1/5] Creating S3 bucket for documentation output..."
echo "      Bucket: $OUTPUT_BUCKET"

# Create S3 bucket if it doesn't exist
if ! aws s3api head-bucket --bucket "$OUTPUT_BUCKET" --region "$REGION" 2>/dev/null; then
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$OUTPUT_BUCKET" --region "$REGION" > /dev/null
  else
    aws s3api create-bucket --bucket "$OUTPUT_BUCKET" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION" > /dev/null
  fi
  echo "      S3 bucket created successfully"
else
  echo "      S3 bucket already exists"
fi

echo "[2/5] Uploading buildspec to S3..."
aws s3 cp "$SCRIPT_DIR/buildspec.yml" "s3://$OUTPUT_BUCKET/config/buildspec.yml" --region "$REGION" > /dev/null
echo "      Buildspec uploaded successfully"

echo "[3/5] Creating IAM role for CodeBuild..."

ROLE_NAME="CodeBuildDocGenRole"
ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text 2>/dev/null || echo "")

if [ -z "$ROLE_ARN" ]; then
  echo "      Creating new IAM role..."
  
  # Create trust policy
  TRUST_POLICY='{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Principal": {
          "Service": "codebuild.amazonaws.com"
        },
        "Action": "sts:AssumeRole"
      }
    ]
  }'
  
  aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document "$TRUST_POLICY" > /dev/null
  
  # Attach managed policies
  echo "      Attaching CloudWatch Logs policy..."
  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess"
  
  echo "      Attaching S3 policy..."
  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/AmazonS3FullAccess"
  
  # Create and attach Transform custom policy
  echo "      Creating Transform custom policy..."
  TRANSFORM_POLICY='{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": "transform-custom:*",
        "Resource": "*"
      }
    ]
  }'
  aws iam put-role-policy --role-name "$ROLE_NAME" \
    --policy-name "TransformCustomPolicy" \
    --policy-document "$TRANSFORM_POLICY"
  
  # Wait for role to propagate
  echo "      Waiting for IAM role to propagate..."
  sleep 10
  
  ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)
  echo "      IAM role created successfully"
else
  echo "      IAM role already exists"
  
  # Ensure Transform policy exists
  if ! aws iam get-role-policy --role-name "$ROLE_NAME" --policy-name "TransformCustomPolicy" 2>/dev/null; then
    echo "      Adding Transform custom policy..."
    TRANSFORM_POLICY='{
      "Version": "2012-10-17",
      "Statement": [
        {
          "Effect": "Allow",
          "Action": "transform-custom:*",
          "Resource": "*"
        }
      ]
    }'
    aws iam put-role-policy --role-name "$ROLE_NAME" \
      --policy-name "TransformCustomPolicy" \
      --policy-document "$TRANSFORM_POLICY"
  fi
fi

echo "[4/5] Creating CodeBuild project..."

PROJECT_NAME="aws-transform-doc-generator"

# Check if project exists
PROJECT_EXISTS=$(aws codebuild batch-get-projects --names "$PROJECT_NAME" --region "$REGION" \
  --query 'projects[0].name' --output text 2>/dev/null || echo "")

if [ "$PROJECT_EXISTS" != "$PROJECT_NAME" ]; then
  echo "      Creating new CodeBuild project..."
  
  aws codebuild create-project \
    --name "$PROJECT_NAME" \
    --description "AWS Transform Documentation Generator - generates comprehensive docs from any Git repo" \
    --service-role "$ROLE_ARN" \
    --region "$REGION" \
    --artifacts "type=NO_ARTIFACTS" \
    --environment "type=LINUX_CONTAINER,image=aws/codebuild/amazonlinux2-x86_64-standard:5.0,computeType=BUILD_GENERAL1_MEDIUM,environmentVariables=[{name=REPOSITORY_URL,value=$REPOSITORY_URL,type=PLAINTEXT},{name=OUTPUT_BUCKET,value=$OUTPUT_BUCKET,type=PLAINTEXT},{name=JOB_ID,value=doc-gen-default,type=PLAINTEXT}]" \
    --source "type=S3,location=$OUTPUT_BUCKET/config/buildspec.yml" \
    --timeout-in-minutes 120 > /dev/null
  
  echo "      CodeBuild project created successfully"
else
  echo "      CodeBuild project already exists"
fi

echo "[5/5] Starting test build..."
echo "      Repository: $REPOSITORY_URL"

# Generate unique job ID
JOB_ID="doc-gen-$(date +%Y%m%d-%H%M%S)"

# Start build with environment variable overrides
BUILD_ID=$(aws codebuild start-build \
  --project-name "$PROJECT_NAME" \
  --region "$REGION" \
  --environment-variables-override \
    "name=REPOSITORY_URL,value=$REPOSITORY_URL,type=PLAINTEXT" \
    "name=OUTPUT_BUCKET,value=$OUTPUT_BUCKET,type=PLAINTEXT" \
    "name=JOB_ID,value=$JOB_ID,type=PLAINTEXT" \
  --query 'build.id' --output text)

echo "      Build started: $BUILD_ID"

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Resources created:"
echo "  - S3 Bucket: $OUTPUT_BUCKET"
echo "  - IAM Role: $ROLE_NAME"
echo "  - CodeBuild Project: $PROJECT_NAME"
echo ""
echo "Monitor build progress:"
echo "  aws codebuild batch-get-builds --ids $BUILD_ID --region $REGION"
echo ""
echo "Stream build logs:"
echo "  aws logs tail /aws/codebuild/$PROJECT_NAME --follow --region $REGION"
echo ""
echo "Check generated documentation:"
echo "  aws s3 ls s3://$OUTPUT_BUCKET/documentation/$JOB_ID/ --recursive"
echo ""
echo "Currently analyzing:"
echo "  $REPOSITORY_URL"
echo ""
echo "Generate docs for a different repository:"
echo "  aws codebuild start-build --project-name $PROJECT_NAME --region $REGION \\"
echo "    --environment-variables-override name=REPOSITORY_URL,value=https://github.com/owner/repo"

# Offer to wait and download documentation
echo ""
echo "=== Download Documentation ==="
echo "The build typically takes 45-90 minutes to complete depending on repository size."
echo ""
read -p "Would you like to wait for the build to complete and download the documentation? (y/n) " WAIT_CHOICE

if [ "$WAIT_CHOICE" = "y" ] || [ "$WAIT_CHOICE" = "Y" ]; then
  echo ""
  echo "Waiting for build to complete..."
  echo "You can press Ctrl+C to cancel and download later using:"
  echo "  aws s3 cp s3://$OUTPUT_BUCKET/documentation/$JOB_ID/ ./generated-docs --recursive"
  echo ""
  
  # Poll for build completion
  BUILD_COMPLETE=false
  BUILD_STATUS=""
  while [ "$BUILD_COMPLETE" = "false" ]; do
    sleep 30
    BUILD_INFO=$(aws codebuild batch-get-builds --ids "$BUILD_ID" --region "$REGION" \
      --query 'builds[0].buildStatus' --output text 2>/dev/null || echo "UNKNOWN")
    
    if [ "$BUILD_INFO" = "SUCCEEDED" ]; then
      BUILD_COMPLETE=true
      BUILD_STATUS="SUCCEEDED"
      echo "Build completed successfully!"
    elif [ "$BUILD_INFO" = "FAILED" ] || [ "$BUILD_INFO" = "FAULT" ] || [ "$BUILD_INFO" = "STOPPED" ] || [ "$BUILD_INFO" = "TIMED_OUT" ]; then
      BUILD_COMPLETE=true
      BUILD_STATUS="$BUILD_INFO"
      echo "Build ended with status: $BUILD_INFO"
    else
      echo "  Build in progress... (Status: $BUILD_INFO)"
    fi
  done
  
  if [ "$BUILD_STATUS" = "SUCCEEDED" ]; then
    # Create local directory for documentation
    LOCAL_DOCS_DIR="./generated-docs-$JOB_ID"
    echo ""
    echo "Downloading documentation to: $LOCAL_DOCS_DIR"
    
    mkdir -p "$LOCAL_DOCS_DIR"
    aws s3 cp "s3://$OUTPUT_BUCKET/documentation/$JOB_ID/" "$LOCAL_DOCS_DIR" --recursive --region "$REGION"
    
    if [ $? -eq 0 ]; then
      echo ""
      echo "Documentation downloaded successfully!"
      echo "Location: $LOCAL_DOCS_DIR"
      
      # List downloaded files
      echo ""
      echo "Downloaded files:"
      find "$LOCAL_DOCS_DIR" -type f | while read -r file; do
        echo "  $file"
      done
    else
      echo "Failed to download documentation"
    fi
  fi
else
  echo ""
  echo "To download documentation later (after build completes):"
  echo "  aws s3 cp s3://$OUTPUT_BUCKET/documentation/$JOB_ID/ ./generated-docs --recursive"
fi
