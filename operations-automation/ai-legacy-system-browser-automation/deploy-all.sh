#!/bin/bash
# Deploy script for Legacy System Browser Automation with AgentCore
# Deploys: AgentCore Browser Tool, S3 recordings bucket, IAM roles

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CDK_DIR="$SCRIPT_DIR/ai-browser-automation/infrastructure/cdk"
SHARED_SCRIPTS_DIR="$SCRIPT_DIR/../../shared/scripts"

# Parse arguments
DESTROY=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --destroy)
            DESTROY=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: ./deploy-all.sh [--destroy]"
            exit 1
            ;;
    esac
done

echo "============================================================"
echo "  Legacy System Browser Automation - Deployment"
echo "============================================================"

# Run shared prerequisites check
echo ""
echo "Checking prerequisites..."
"$SHARED_SCRIPTS_DIR/check-prerequisites.sh" --require-cdk

# Get region using shared utility
source "$SHARED_SCRIPTS_DIR/../utils/aws-utils.sh"
REGION=$(get_aws_region)

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --no-cli-pager)
echo "  Account: $ACCOUNT_ID"
echo "  Region:  $REGION"

# Create virtual environment if it doesn't exist
VENV_DIR="$CDK_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo ""
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Activate virtual environment
source "$VENV_DIR/bin/activate"

# Destroy mode
if [ "$DESTROY" = true ]; then
    echo ""
    echo "Destroying infrastructure..."
    cd "$CDK_DIR"
    pip install -r requirements.txt -q
    npx cdk destroy --force
    deactivate
    echo ""
    echo "Infrastructure destroyed."
    exit 0
fi

# Deploy
echo ""
echo "Installing CDK dependencies..."
cd "$CDK_DIR"
pip install -r requirements.txt -q

echo ""
echo "Deploying AgentCore Browser Tool stack..."
# Set PYTHONPATH to include shared utilities
export PYTHONPATH="$SCRIPT_DIR/../..:$PYTHONPATH"
npx cdk deploy --require-approval never

# Extract outputs
echo ""
echo "============================================================"
echo "  Deployment Complete"
echo "============================================================"

STACK_NAME="LegacySystemAutomationAgentCore-$REGION"
OUTPUTS=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs" --output json --no-cli-pager 2>/dev/null || echo "[]")

BROWSER_ID=$(echo "$OUTPUTS" | python3 -c "import sys,json; o=json.load(sys.stdin); print(next((x['OutputValue'] for x in o if x['OutputKey']=='BrowserId'),''))" 2>/dev/null || echo "")
BUCKET=$(echo "$OUTPUTS" | python3 -c "import sys,json; o=json.load(sys.stdin); print(next((x['OutputValue'] for x in o if x['OutputKey']=='RecordingsBucketName'),''))" 2>/dev/null || echo "")

echo ""
echo "  Browser ID:        $BROWSER_ID"
echo "  Recordings Bucket: $BUCKET"
echo ""
echo "  Set environment variables:"
echo "    export BROWSER_ID=\"$BROWSER_ID\""
echo "    export AWS_REGION=\"$REGION\""
echo ""
echo "  Live view: https://$REGION.console.aws.amazon.com/bedrock-agentcore/builtInTools"
echo ""

# Deactivate virtual environment
deactivate
