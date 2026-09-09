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
    # `cdk destroy` re-synthesizes app.py, so both aws_cdk AND the repo's `shared` package must
    # be importable by the interpreter cdk uses. app.py does `from shared.utils.aws_utils import
    # get_region`, which needs the repo root on PYTHONPATH -- the deploy path exports it below,
    # but the destroy path exits before that, so export it here too. Pin --app to an explicit
    # python3 and check both imports so destroy fails with a clear message instead of a cryptic
    # ModuleNotFoundError.
    export PYTHONPATH="$SCRIPT_DIR/../..:$PYTHONPATH"
    PY="$(command -v python3 || true)"
    if [ -z "$PY" ] || ! "$PY" -c "import aws_cdk" 2>/dev/null; then
        echo "ERROR: 'aws_cdk' is not importable by python3, which cdk needs to synthesize the app for destroy."
        echo "       Install the CDK deps first (in a venv): python3 -m pip install -r requirements.txt"
        exit 1
    fi
    if ! PYTHONPATH="$SCRIPT_DIR/../..:$PYTHONPATH" "$PY" -c "import shared.utils.aws_utils" 2>/dev/null; then
        echo "ERROR: the repo's 'shared' package is not importable, which app.py needs to synthesize for destroy."
        echo "       Run this script from within the repo so \$PYTHONPATH can reach the repo root."
        exit 1
    fi
    if ! cdk destroy "$STACK_NAME" --force --app "$PY app.py"; then
        echo "ERROR: CDK destroy failed"
        exit 1
    fi
    echo "Infrastructure destruction completed"
    exit 0
fi

# Install CDK dependencies
echo ""
echo "Installing CDK dependencies..."
cd "$CDK_DIR"

# Pin ONE python interpreter for install, verification, and CDK synth. `cdk` synthesizes the
# app via cdk.json (python3 app.py); if we install into a different python than cdk uses, the
# deps are invisible and synth dies with ModuleNotFoundError. Resolve $PY once and reuse it.
PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
    echo "ERROR: python3 not found on PATH; cannot install/synthesize the CDK app"
    exit 1
fi

# Install CDK deps and FAIL LOUDLY. Previously both attempts piped stderr to /dev/null and the
# exit code was never checked, so a failed install still fell through to `cdk deploy` and died
# with ModuleNotFoundError: No module named 'aws_cdk'. We also no longer auto-force
# --break-system-packages (which mutates the user's system Python); that bypass is opt-in only
# via DEPLOY_CDK_ALLOW_BREAK_SYSTEM_PACKAGES=1, for throwaway/ephemeral hosts such as CI runners.
if ! "$PY" -m pip install -r requirements.txt -q; then
    if [ "${DEPLOY_CDK_ALLOW_BREAK_SYSTEM_PACKAGES:-}" = "1" ]; then
        echo "Normal pip install failed; DEPLOY_CDK_ALLOW_BREAK_SYSTEM_PACKAGES=1 set, retrying with --break-system-packages (mutates system Python)..."
        if ! "$PY" -m pip install -r requirements.txt -q --break-system-packages; then
            echo "ERROR: Failed to install CDK dependencies (requirements.txt)"
            exit 1
        fi
    else
        echo "ERROR: Failed to install CDK dependencies (requirements.txt)."
        echo "If this is an 'externally-managed-environment' (PEP 668) error, do NOT force it into"
        echo "your system Python. Create and activate a virtual environment first:"
        echo "    python3 -m venv .venv && source .venv/bin/activate"
        echo "then re-run. On a throwaway/ephemeral host you may instead re-run with:"
        echo "    DEPLOY_CDK_ALLOW_BREAK_SYSTEM_PACKAGES=1"
        exit 1
    fi
fi
# Verify aws_cdk is importable by the exact interpreter that will synth the app.
if ! "$PY" -c "import aws_cdk" 2>/dev/null; then
    echo "ERROR: 'aws_cdk' is not importable by $PY after installing requirements.txt."
    echo "       Use a virtual environment so pip and python3 are the same interpreter."
    exit 1
fi

# Install Lambda dependencies (no Docker required). These are vendored into the function
# directory via -t and zipped into the Lambda, so a SILENT failure here ships a Lambda that is
# missing its deps and only fails at RUNTIME in AWS -- much harder to diagnose. Fail loudly.
# (--break-system-packages is irrelevant for a -t target install, so it is not used here.)
echo ""
echo "Installing Lambda dependencies..."
if ! "$PY" -m pip install -r "$SCRIPT_DIR/lambdas/rss-ingestion/requirements.txt" -t "$SCRIPT_DIR/lambdas/rss-ingestion/" -q --no-compile; then
    echo "ERROR: Failed to install Lambda dependencies for rss-ingestion"
    exit 1
fi

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

# Deploy CDK stack. Pin --app to the SAME interpreter ($PY) we installed into and verified
# above, so cdk does not re-resolve `python3` to a different interpreter that lacks the deps.
echo ""
echo "Deploying CDK stack..."
if ! cdk deploy "$STACK_NAME" --require-approval never --app "$PY app.py" $CONTEXT_ARGS; then
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
