#!/bin/bash
set -e

# Default values
REPOSITORY_URL=""
SKIP_SETUP=false
DEFAULT_REPO="https://github.com/aws-samples/sample-serverless-digital-asset-payments"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -r|--repository-url)
            REPOSITORY_URL="$2"
            shift 2
            ;;
        -s|--skip-setup)
            SKIP_SETUP=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Deploy Graviton Migration Assessment using CDK and CodeBuild"
            echo ""
            echo "Options:"
            echo "  -r, --repository-url URL    Git repository URL to analyze"
            echo "  -s, --skip-setup           Skip infrastructure deployment, only start assessment"
            echo "  -h, --help                 Show this help message"
            echo ""
            echo "Notes:"
            echo "  This script uses the AWS region configured in your AWS CLI profile."
            echo "  To set your region: aws configure set region <your-region>"
            echo ""
            echo "Examples:"
            echo "  $0"
            echo "  $0 -r \"https://github.com/owner/repo\""
            echo "  $0 -s"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo "=== AI-Powered Graviton Migration Assessment ==="
echo "Analyzes codebases for Graviton migration opportunities and cost optimization"
echo ""
echo "Uses AWS Transform with specialized Graviton migration context:"
echo "  - Architecture compatibility assessment for ARM64"
echo "  - Cost savings analysis (typically 10-20% reduction)"
echo "  - Migration complexity scoring and phased approach"
echo "  - Expected time: ~60 minutes"
echo ""

TRANSFORMATION_TYPE="graviton"
BUILDSPEC_FILE="buildspec.yml"
EXPECTED_TIME="~60 minutes"

echo ""

# Check if repository URL was provided
if [[ -z "$REPOSITORY_URL" ]]; then
    echo "No repository URL specified. Assessment will be generated based on the default sample repository:"
    echo "  $DEFAULT_REPO"
    echo ""
    echo "To analyze your own repository, run:"
    echo "  $0 -r \"https://github.com/owner/repo\""
    echo ""
    REPOSITORY_URL="$DEFAULT_REPO"
fi

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CDK_DIR="$SCRIPT_DIR/infrastructure/cdk"
SHARED_SCRIPTS_DIR="$SCRIPT_DIR/../../shared/scripts"

if [ "$SKIP_SETUP" = false ]; then
    # Run prerequisites check
    echo "Running prerequisites check..."
    "$SHARED_SCRIPTS_DIR/check-prerequisites.sh" \
        --required-service "transform" \
        --require-cdk

    if [[ $? -ne 0 ]]; then
        echo "Prerequisites check failed"
        exit 1
    fi

    # Get AWS account and region info
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --no-cli-pager)

    # Get region using shared utility
    source "$SHARED_SCRIPTS_DIR/../utils/get-aws-region.sh"
    CURRENT_REGION=$(get_aws_region)

    echo ""
    echo "Deploying infrastructure via CDK..."
    echo "      Region: $CURRENT_REGION"

    # Deploy CDK stack using shared script
    "$SHARED_SCRIPTS_DIR/deploy-cdk.sh" --cdk-directory "$CDK_DIR"

    if [[ $? -ne 0 ]]; then
        echo "CDK deployment failed"
        exit 1
    fi

    # Upload buildspec to S3
    echo ""
    echo "Uploading buildspec to S3..."
    BUILDSPEC_PATH="$SCRIPT_DIR/$BUILDSPEC_FILE"
    aws s3 cp "$BUILDSPEC_PATH" "s3://$OUTPUT_BUCKET/config/buildspec.yml" --region "$CURRENT_REGION" --no-cli-pager > /dev/null
    if [[ $? -eq 0 ]]; then
        echo "      ✓ Buildspec uploaded successfully ($BUILDSPEC_FILE)"
    else
        echo "      ❌ Failed to upload buildspec"
        exit 1
    fi

    # Upload custom transformation definition
    echo ""
    echo "Uploading custom Graviton transformation definition..."
    GRAVITON_TRANSFORM_DIR="$SCRIPT_DIR/graviton-transformation-definition"
    aws s3 cp "$GRAVITON_TRANSFORM_DIR" "s3://$OUTPUT_BUCKET/graviton-transformation-definition/" --recursive --region "$CURRENT_REGION" --no-cli-pager > /dev/null
    if [[ $? -eq 0 ]]; then
        echo "      ✓ Custom transformation definition uploaded successfully"
    else
        echo "      ❌ Failed to upload custom transformation definition"
        exit 1
    fi

    # Upload knowledge items
    echo ""
    echo "Uploading Graviton knowledge items..."
    KNOWLEDGE_ITEMS_DIR="$SCRIPT_DIR/knowledge-items"
    aws s3 cp "$KNOWLEDGE_ITEMS_DIR" "s3://$OUTPUT_BUCKET/knowledge-items/" --recursive --region "$CURRENT_REGION" --no-cli-pager > /dev/null
    if [[ $? -eq 0 ]]; then
        echo "      ✓ Knowledge items uploaded successfully"
    else
        echo "      ❌ Failed to upload knowledge items"
        exit 1
    fi
else
    # Skip setup - just get region and retrieve stack outputs
    echo "Skipping infrastructure deployment..."
    
    # Get region using shared utility (prerequisites check was skipped)
    source "$SHARED_SCRIPTS_DIR/../utils/get-aws-region.sh"
    CURRENT_REGION=$(get_aws_region)
    
    echo "      Region: $CURRENT_REGION"
fi

# Get bucket name and project name from CloudFormation outputs
echo ""
echo "Getting stack outputs..."
STACK_NAME="GravitonAssessmentStack-$CURRENT_REGION"
OUTPUT_BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$CURRENT_REGION" --no-cli-pager --query "Stacks[0].Outputs[?OutputKey=='OutputBucketName'].OutputValue" --output text)
PROJECT_NAME=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$CURRENT_REGION" --no-cli-pager --query "Stacks[0].Outputs[?OutputKey=='CodeBuildProjectName'].OutputValue" --output text)
if [[ -z "$OUTPUT_BUCKET" || -z "$PROJECT_NAME" ]]; then
    echo "      ❌ Failed to get stack outputs"
    if [ "$SKIP_SETUP" = true ]; then
        echo "      Stack may not exist. Run without -s to deploy infrastructure first."
    fi
    exit 1
fi
echo "      Output Bucket: $OUTPUT_BUCKET"
echo "      CodeBuild Project: $PROJECT_NAME"

# Start Graviton assessment build
echo ""
echo "Starting Graviton migration assessment..."
echo "      Repository: $REPOSITORY_URL"
echo "      Expected time: $EXPECTED_TIME"

# Generate unique job ID
JOB_ID="graviton-assessment-$(date +%Y%m%d-%H%M%S)"

# Start build with environment variable overrides
BUILD_RESULT=$(aws codebuild start-build \
    --project-name "$PROJECT_NAME" \
    --region "$CURRENT_REGION" \
    --no-cli-pager \
    --environment-variables-override "name=REPOSITORY_URL,value=$REPOSITORY_URL,type=PLAINTEXT" "name=OUTPUT_BUCKET,value=$OUTPUT_BUCKET,type=PLAINTEXT" "name=JOB_ID,value=$JOB_ID,type=PLAINTEXT" \
    --query 'build.id' --output text)

if [[ $? -eq 0 ]]; then
    echo "      ✓ Assessment started: $BUILD_RESULT"
else
    echo "      ❌ Failed to start assessment"
    exit 1
fi

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Resources deployed via CDK:"
echo "  - S3 Bucket: $OUTPUT_BUCKET"
echo "  - CodeBuild Project: $PROJECT_NAME"
echo ""
echo "Monitor assessment progress:"
echo "  aws codebuild batch-get-builds --ids $BUILD_RESULT --region $CURRENT_REGION --no-cli-pager"
echo ""
echo "Stream build logs:"
echo "  aws logs tail /aws/codebuild/$PROJECT_NAME --follow --region $CURRENT_REGION"
echo ""
echo "Check generated assessment:"
echo "  aws s3 ls s3://$OUTPUT_BUCKET/assessments/$JOB_ID/ --recursive"
echo ""
echo "Currently analyzing:"
echo "  $REPOSITORY_URL"
echo ""
echo "Generate assessment for a different repository:"
echo "  aws codebuild start-build --project-name $PROJECT_NAME --region $CURRENT_REGION --no-cli-pager \\"
echo "    --environment-variables-override name=REPOSITORY_URL,value=https://github.com/owner/repo"

# Offer to wait and download assessment
echo ""
echo "=== Download Assessment ==="
echo "The assessment is expected to take $EXPECTED_TIME to complete depending on repository size."
echo ""
read -p "Would you like to wait for the assessment to complete and download the results? (y/n): " WAIT_CHOICE

if [[ "$WAIT_CHOICE" == "y" || "$WAIT_CHOICE" == "Y" ]]; then
    echo ""
    echo "Waiting for assessment to complete..."
    echo "You can press Ctrl+C to cancel and download later using:"
    echo "  aws s3 cp s3://$OUTPUT_BUCKET/assessments/$JOB_ID/ ./graviton-assessment --recursive"
    echo ""
    
    # Poll for build completion
    BUILD_COMPLETE=false
    BUILD_STATUS=""
    START_TIME=$(date +%s)
    while [[ "$BUILD_COMPLETE" == "false" ]]; do
        sleep 30
        BUILD_INFO=$(aws codebuild batch-get-builds --ids "$BUILD_RESULT" --region "$CURRENT_REGION" --no-cli-pager --query 'builds[0].buildStatus' --output text 2>/dev/null || echo "")
        
        if [[ "$BUILD_INFO" == "SUCCEEDED" ]]; then
            BUILD_COMPLETE=true
            BUILD_STATUS="SUCCEEDED"
            echo "Assessment completed successfully!"
        elif [[ "$BUILD_INFO" == "FAILED" || "$BUILD_INFO" == "FAULT" || "$BUILD_INFO" == "STOPPED" || "$BUILD_INFO" == "TIMED_OUT" ]]; then
            BUILD_COMPLETE=true
            BUILD_STATUS="$BUILD_INFO"
            echo "Assessment ended with status: $BUILD_INFO"
        else
            CURRENT_TIME=$(date +%s)
            ELAPSED=$(echo "scale=1; ($CURRENT_TIME - $START_TIME) / 60" | bc -l 2>/dev/null || echo "0")
            echo "  Assessment in progress... (Status: $BUILD_INFO, Elapsed: ${ELAPSED} min)"
        fi
    done
    
    if [[ "$BUILD_STATUS" == "SUCCEEDED" ]]; then
        # Create local directory for assessment
        LOCAL_ASSESSMENT_DIR="./graviton-assessment-$JOB_ID"
        echo ""
        echo "Downloading assessment to: $LOCAL_ASSESSMENT_DIR"
        
        mkdir -p "$LOCAL_ASSESSMENT_DIR"
        aws s3 cp "s3://$OUTPUT_BUCKET/assessments/$JOB_ID/" "$LOCAL_ASSESSMENT_DIR" --recursive --region "$CURRENT_REGION" --no-cli-pager
        
        if [[ $? -eq 0 ]]; then
            echo ""
            echo "Graviton migration assessment downloaded successfully!"
            echo "Location: $LOCAL_ASSESSMENT_DIR"
            
            # List downloaded files
            echo ""
            echo "Assessment files:"
            find "$LOCAL_ASSESSMENT_DIR" -type f | while read -r file; do
                echo "  $file"
            done
            
            echo ""
            echo "=== Next Steps ==="
            echo "1. Review the executive summary in README.md"
            echo "2. Examine cost savings projections in cost-analysis/"
            echo "3. Check migration complexity in migration-plan/"
            echo "4. Identify pilot candidates for initial testing"
        else
            echo "Failed to download assessment"
        fi
    fi
else
    echo ""
    echo "To download assessment later (after build completes):"
    echo "  aws s3 cp s3://$OUTPUT_BUCKET/assessments/$JOB_ID/ ./graviton-assessment --recursive"
fi