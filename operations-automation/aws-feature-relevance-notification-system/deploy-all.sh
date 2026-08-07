#!/bin/bash
# Bash deployment script for AWS Feature Relevance Notification System
set -e

DESTROY_INFRA=false
KNOWLEDGE_BASE_ID=""
SLACK_WEBHOOK_URL=""

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --destroy-infra) DESTROY_INFRA=true; shift ;;
        --knowledge-base-id) KNOWLEDGE_BASE_ID="$2"; shift 2 ;;
        --slack-webhook-url) SLACK_WEBHOOK_URL="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CDK_DIR="$SCRIPT_DIR/infrastructure/cdk"

echo "=== AWS Feature Relevance Notification System Deployment ==="

# Use shared prerequisites check (validates AWS CLI, region, service availability)
# Region is available as $AWS_REGION after this call
source "$SCRIPT_DIR/../../shared/scripts/check-prerequisites.sh" --required-service bedrock

REGION="$AWS_REGION"
STACK_NAME="FeatureRelevanceNotification-$REGION"

echo "Using region: $REGION"

# Destroy mode
if [ "$DESTROY_INFRA" = true ]; then
    echo "Destroying infrastructure..."
    cd "$CDK_DIR"
    cdk destroy "$STACK_NAME" --force
    echo "Infrastructure destruction completed"
    exit 0
fi

# Install CDK dependencies
echo ""
echo "Installing CDK dependencies..."
cd "$CDK_DIR"
pip3 install -r requirements.txt -q 2>/dev/null || pip3 install -r requirements.txt -q --break-system-packages 2>/dev/null

# Install Lambda dependencies (no Docker required)
echo ""
echo "Installing Lambda dependencies..."
pip3 install -r "$SCRIPT_DIR/lambdas/rss-ingestion/requirements.txt" -t "$SCRIPT_DIR/lambdas/rss-ingestion/" -q --no-compile 2>/dev/null || \
pip3 install -r "$SCRIPT_DIR/lambdas/rss-ingestion/requirements.txt" -t "$SCRIPT_DIR/lambdas/rss-ingestion/" -q --no-compile --break-system-packages 2>/dev/null

# Set PYTHONPATH for shared utilities
export PYTHONPATH="$SCRIPT_DIR/../..:$PYTHONPATH"

# Build context args
CONTEXT_ARGS=""
if [ -n "$KNOWLEDGE_BASE_ID" ]; then
    CONTEXT_ARGS="$CONTEXT_ARGS --context knowledge_base_id=$KNOWLEDGE_BASE_ID"
fi
if [ -n "$SLACK_WEBHOOK_URL" ]; then
    CONTEXT_ARGS="$CONTEXT_ARGS --context slack_webhook_url=$SLACK_WEBHOOK_URL"
fi

# Deploy CDK stack
echo ""
echo "Deploying CDK stack..."
cdk deploy "$STACK_NAME" --require-approval never $CONTEXT_ARGS

if [ $? -ne 0 ]; then
    echo "ERROR: CDK deployment failed"
    exit 1
fi

# Get stack outputs
echo ""
echo "Getting stack outputs..."
OUTPUTS=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs" \
    --output json \
    --region "$REGION" \
    --no-cli-pager 2>/dev/null)

if [ $? -eq 0 ] && [ -n "$OUTPUTS" ]; then
    RSS_FUNCTION=$(echo "$OUTPUTS" | python3 -c "import sys, json; outputs = json.load(sys.stdin); print(next((o['OutputValue'] for o in outputs if o['OutputKey'] == 'RSSIngestionFunctionName'), 'N/A'))" 2>/dev/null)
    WORKLOAD_MANAGER=$(echo "$OUTPUTS" | python3 -c "import sys, json; outputs = json.load(sys.stdin); print(next((o['OutputValue'] for o in outputs if o['OutputKey'] == 'WorkloadManagerFunctionName'), 'N/A'))" 2>/dev/null)
fi

# Print deployment summary
echo ""
echo "========================================"
echo "  Deployment Complete!"
echo "========================================"
echo ""
echo "  Region:              $REGION"
echo "  Stack Name:          $STACK_NAME"
echo "  RSS Ingestion:       ${RSS_FUNCTION:-N/A}"
echo "  Workload Manager:    ${WORKLOAD_MANAGER:-N/A}"
echo ""
echo "Next Steps:"
echo "  1. Configure Slack webhook in Secrets Manager"
echo "  2. Create workload profiles (see README.md)"
echo "  3. Test with: aws lambda invoke --function-name ${RSS_FUNCTION:-RSSIngestion} \\"
echo "       --cli-binary-format raw-in-base64-out \\"
echo "       --payload '{\"test_mode\":true}' /tmp/test.json"
echo ""
echo "To destroy the infrastructure later, run:"
echo "  ./deploy-all.sh --destroy-infra"
