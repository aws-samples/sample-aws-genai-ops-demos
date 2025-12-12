#!/bin/bash
# AI-Powered Legacy System Automation with AgentCore Browser Tool
# One-click setup and demo execution using CDK
#
# This demo uses AgentCore Browser Tool (cloud browser) instead of local browser.
# Benefits: scalable cloud execution, session recording, live view via AWS Console.
#
# Authentication: AWS IAM (no API key needed)
# Infrastructure: Deployed via AWS CDK

set -e

# Default values
SKIP_SETUP=false
REGION="us-east-1"
DESTROY_INFRA=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-setup)
            SKIP_SETUP=true
            shift
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --destroy-infra)
            DESTROY_INFRA=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --skip-setup          Skip prerequisite checks"
            echo "  --region REGION       AWS region (default: us-east-1)"
            echo "  --destroy-infra       Destroy CDK infrastructure and exit"
            echo ""
            echo "Authentication: Uses AWS IAM credentials (no API key needed)"
            echo "Infrastructure: Deployed via AWS CDK"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CDK_DIR="$SCRIPT_DIR/infrastructure/cdk"
SHARED_SCRIPTS_DIR="$SCRIPT_DIR/../../shared/scripts"

echo ""
echo "============================================================"
echo "  AI-Powered Legacy System Automation"
echo "  with AgentCore Browser Tool (Cloud)"
echo "============================================================"
echo ""

# ============================================================
# Handle Infrastructure Destroy
# ============================================================
if [[ "$DESTROY_INFRA" == "true" ]]; then
    echo "[CLEANUP] Destroying CDK infrastructure..."
    "$SHARED_SCRIPTS_DIR/deploy-cdk.sh" --cdk-directory "$CDK_DIR" --destroy
    echo "[CLEANUP] Infrastructure destroyed!"
    exit 0
fi

# ============================================================
# STEP 1: Check Prerequisites using shared script
# ============================================================
if [[ "$SKIP_SETUP" != "true" ]]; then
    echo "[SETUP] Running shared prerequisites check..."
    echo ""
    
    # Set region first
    aws configure set region "$REGION"
    
    # Call shared prerequisites script with required checks
    "$SHARED_SCRIPTS_DIR/check-prerequisites.sh" \
        --service "agentcore-browser" \
        --min-python-version "3.10" \
        --require-cdk
    
    if [[ $? -ne 0 ]]; then
        echo "Prerequisites check failed!"
        exit 1
    fi
    
    # Install Python dependencies
    echo ""
    echo "Installing Python dependencies..."
    pip3 install bedrock-agentcore nova-act rich nest-asyncio --quiet 2>/dev/null || \
    pip install bedrock-agentcore nova-act rich nest-asyncio --quiet 2>/dev/null
    echo "      ✓ Python dependencies installed"
    
    echo ""
    echo "[SETUP] Complete!"
    echo ""
fi

# ============================================================
# STEP 2: Deploy Infrastructure with CDK (using shared script)
# ============================================================
echo "------------------------------------------------------------"
echo "[INFRA] Deploying AgentCore Browser infrastructure via CDK..."
echo "------------------------------------------------------------"

"$SHARED_SCRIPTS_DIR/deploy-cdk.sh" --cdk-directory "$CDK_DIR"

if [[ $? -ne 0 ]]; then
    echo "CDK deployment failed!"
    exit 1
fi

# Get outputs from CloudFormation
echo ""
echo "Getting stack outputs..."
BROWSER_ID=$(aws cloudformation describe-stacks --stack-name LegacySystemAutomationAgentCore --region "$REGION" --no-cli-pager --query "Stacks[0].Outputs[?OutputKey=='BrowserId'].OutputValue" --output text)
RECORDINGS_BUCKET=$(aws cloudformation describe-stacks --stack-name LegacySystemAutomationAgentCore --region "$REGION" --no-cli-pager --query "Stacks[0].Outputs[?OutputKey=='RecordingsBucketName'].OutputValue" --output text)

if [[ -z "$BROWSER_ID" ]] || [[ -z "$RECORDINGS_BUCKET" ]]; then
    echo "      ❌ Failed to get stack outputs"
    exit 1
fi

echo "      Browser ID: $BROWSER_ID"
echo "      Recordings: s3://$RECORDINGS_BUCKET/browser-recordings/"
echo ""

# ============================================================
# STEP 3: Create Nova Act Workflow Definition (with S3 for step data)
# ============================================================
echo "------------------------------------------------------------"
echo "[WORKFLOW] Creating Nova Act workflow definition..."
echo "------------------------------------------------------------"
echo ""

WORKFLOW_NAME="legacy-system-automation-agentcore"
EXISTING=$(aws nova-act list-workflow-definitions --region "$REGION" --no-cli-pager 2>/dev/null | grep -c "$WORKFLOW_NAME" || echo "0")

if [[ "$EXISTING" -gt 0 ]]; then
    echo "      ✓ Workflow '$WORKFLOW_NAME' exists"
    echo "      Note: To update S3 config, delete and recreate the workflow"
else
    # Create workflow with S3 export config for step data/recordings
    aws nova-act create-workflow-definition \
        --name "$WORKFLOW_NAME" \
        --export-config "s3BucketName=$RECORDINGS_BUCKET,s3KeyPrefix=nova-act-workflows" \
        --region "$REGION" \
        --no-cli-pager 2>/dev/null || true
    echo "      ✓ Workflow '$WORKFLOW_NAME' created with S3 export"
    echo "      S3: s3://$RECORDINGS_BUCKET/nova-act-workflows/"
fi

echo ""

# ============================================================
# STEP 4: Display Live View Instructions
# ============================================================
echo "------------------------------------------------------------"
echo "[LIVE VIEW] Watch the browser session in AWS Console"
echo "------------------------------------------------------------"
echo ""
echo "  Console URL:"
echo "  https://$REGION.console.aws.amazon.com/bedrock-agentcore/builtInTools"
echo ""
echo "  Instructions:"
echo "  1. Open the URL above in your browser"
echo "  2. Navigate to 'Built-in tools' > Select your browser"
echo "  3. Find your active session (status: Ready)"
echo "  4. Click 'View live session' to watch in real-time"
echo ""

# ============================================================
# STEP 5: Run Demo
# ============================================================
echo "------------------------------------------------------------"
echo "[DEMO] Running automation on Nova Act Gym (Cloud Browser)..."
echo "------------------------------------------------------------"
echo ""

# Build command arguments
DEMO_ARGS="--region $REGION --browser-id $BROWSER_ID"

cd "$SCRIPT_DIR"
python3 create_ticket_agentcore.py $DEMO_ARGS || python create_ticket_agentcore.py $DEMO_ARGS

echo ""
echo "============================================================"
echo "  Demo Complete!"
echo "============================================================"
echo ""
echo "  Session recordings stored at:"
echo "  s3://$RECORDINGS_BUCKET/browser-recordings/"
echo ""
echo "  To destroy infrastructure:"
echo "  ./run-demo.sh --destroy-infra"
echo ""
