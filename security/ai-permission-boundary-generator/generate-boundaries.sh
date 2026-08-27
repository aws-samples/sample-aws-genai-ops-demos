#!/bin/bash
set -e

# === AI Permission Boundary Generator ===
# Analyzes CloudTrail logs to generate least-privilege permission boundaries
# using Amazon Bedrock AI models.

DAYS=30
MODEL_ID="us.anthropic.claude-sonnet-4-20250514-v1:0"
OUTPUT_DIR="./output"
SKIP_SETUP=false
ROLE_NAME=""
USER_NAME=""

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Generates least-privilege permission boundaries by analyzing CloudTrail activity."
    echo ""
    echo "Options:"
    echo "  -r, --role-name NAME    IAM role name to analyze"
    echo "  -u, --user-name NAME    IAM user name to analyze"
    echo "  -d, --days NUM          Number of days of CloudTrail history (default: 30)"
    echo "  -m, --model-id ID       Bedrock model ID (default: us.anthropic.claude-sonnet-4-20250514-v1:0)"
    echo "  -o, --output-dir DIR    Output directory for generated policies (default: ./output)"
    echo "  -s, --skip-setup        Skip prerequisite checks and CDK deployment"
    echo "  -h, --help              Show this help message"
    echo ""
    echo "Either --role-name or --user-name must be provided."
    exit 0
}

while [[ $# -gt 0 ]]; do
    case $1 in
        -r|--role-name) ROLE_NAME="$2"; shift 2 ;;
        -u|--user-name) USER_NAME="$2"; shift 2 ;;
        -d|--days) DAYS="$2"; shift 2 ;;
        -m|--model-id) MODEL_ID="$2"; shift 2 ;;
        -o|--output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        -s|--skip-setup) SKIP_SETUP=true; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "=== AI Permission Boundary Generator ==="
echo ""
echo "Analyzes CloudTrail logs and generates least-privilege permission"
echo "boundaries using Amazon Bedrock."
echo ""

# Validate inputs
if [[ -z "$ROLE_NAME" && -z "$USER_NAME" ]]; then
    echo "Error: Either --role-name or --user-name must be provided."
    echo "Run with --help for usage information."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED_SCRIPTS_DIR="$SCRIPT_DIR/../../shared/scripts"
CDK_DIR="$SCRIPT_DIR/infrastructure/cdk"

# Setup phase
if [[ "$SKIP_SETUP" == "false" ]]; then
    echo "--- Checking prerequisites ---"
    "$SHARED_SCRIPTS_DIR/check-prerequisites.sh" --required-service bedrock --require-cdk

    echo ""
    echo "--- Deploying CDK infrastructure ---"
    "$SHARED_SCRIPTS_DIR/deploy-cdk.sh" --cdk-directory "$CDK_DIR"

    echo ""
    echo "--- Retrieving stack outputs ---"
    source "$SHARED_SCRIPTS_DIR/../utils/aws-utils.sh"
    CURRENT_REGION=$(get_aws_region)
    STACK_NAME="PermissionBoundaryStack-$CURRENT_REGION"
    BUCKET_NAME=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" --region "$CURRENT_REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" \
        --output text --no-cli-pager 2>/dev/null || echo "")
fi

# Install Python dependencies
echo "--- Installing Python dependencies ---"
pip install -r "$SCRIPT_DIR/src/requirements.txt" --quiet

# Set PYTHONPATH to include repo root for shared.utils
export PYTHONPATH="$SCRIPT_DIR/../..:$PYTHONPATH"

# Build python arguments
PYTHON_ARGS="--days $DAYS --model-id $MODEL_ID --output-dir $OUTPUT_DIR"
if [[ -n "$ROLE_NAME" ]]; then
    PYTHON_ARGS="$PYTHON_ARGS --role-name $ROLE_NAME"
fi
if [[ -n "$USER_NAME" ]]; then
    PYTHON_ARGS="$PYTHON_ARGS --user-name $USER_NAME"
fi
if [[ -n "$BUCKET_NAME" ]]; then
    PYTHON_ARGS="$PYTHON_ARGS --bucket-name $BUCKET_NAME"
fi

# Run analysis
echo ""
echo "--- Running permission boundary analysis ---"
cd "$SCRIPT_DIR/src"
python main.py $PYTHON_ARGS

echo ""
echo "=== Permission Boundary Generation Complete ==="
echo ""
echo "Results saved to: $OUTPUT_DIR"
echo "Review the generated policies before applying them."
