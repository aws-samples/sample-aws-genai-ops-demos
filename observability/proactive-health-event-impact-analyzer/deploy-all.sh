#!/bin/bash
# deploy-all.sh — Proactive Health Event Impact Analyzer
# Deploys the full solution using the interactive setup wizard.
#
# Usage:
#   ./deploy-all.sh              # Full deployment (prerequisites + wizard)
#   ./deploy-all.sh -s           # Skip prerequisites, run wizard only
#   ./deploy-all.sh --skip-setup # Skip prerequisites, run wizard only
#
# The setup wizard handles:
#   1. DevOps Agent Space creation and configuration
#   2. IAM roles for topology discovery
#   3. Webhook generation for investigation triggers
#   4. Notification channel configuration (email, Slack, MS Teams)
#   5. CDK stack deployment

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKIP_SETUP=false

# ─── Parse Arguments ──────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case $1 in
        -s|--skip-setup) SKIP_SETUP=true; shift ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  -s, --skip-setup   Skip prerequisites check and dependency install"
            echo "  -h, --help         Show this help message"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ─── Region Detection ─────────────────────────────────────────────────────────
# Priority: Environment variable > AWS CLI config > Fallback

get_aws_region() {
    if [ -n "${AWS_DEFAULT_REGION:-}" ]; then
        echo "$AWS_DEFAULT_REGION"
        return
    fi
    if [ -n "${AWS_REGION:-}" ]; then
        echo "$AWS_REGION"
        return
    fi

    local cli_region
    cli_region=$(aws configure get region 2>/dev/null || true)
    if [ -n "$cli_region" ]; then
        echo "$cli_region"
        return
    fi

    echo "us-east-1"
}

# ─── Inline Prerequisites Check ──────────────────────────────────────────────

check_prerequisites() {
    echo ""
    echo "Checking prerequisites..."
    echo ""

    # Check AWS CLI
    if ! command -v aws &>/dev/null; then
        echo "  [FAIL] AWS CLI not found. Install from https://aws.amazon.com/cli/"
        exit 1
    fi
    local aws_version
    aws_version=$(aws --version 2>&1)
    echo "  [OK] AWS CLI: $aws_version"

    # Check AWS credentials
    local account_id
    account_id=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) || {
        echo "  [FAIL] AWS credentials not configured. Run 'aws configure' or set environment variables."
        exit 1
    }
    echo "  [OK] AWS Account: $account_id"

    # Check Node.js
    if ! command -v node &>/dev/null; then
        echo "  [FAIL] Node.js not found. Install from https://nodejs.org/"
        exit 1
    fi
    local node_version
    node_version=$(node --version)
    echo "  [OK] Node.js: $node_version"

    # Check npm
    if ! command -v npm &>/dev/null; then
        echo "  [FAIL] npm not found."
        exit 1
    fi
    local npm_version
    npm_version=$(npm --version)
    echo "  [OK] npm: $npm_version"

    # Check region
    local region
    region=$(get_aws_region)
    echo "  [OK] Region: $region"
    echo ""
}

# ─── Main ─────────────────────────────────────────────────────────────────────

echo ""
echo "========================================"
echo "  Proactive Health Event Impact Analyzer"
echo "  Deployment Script"
echo "========================================"

if [ "$SKIP_SETUP" = false ]; then
    # Use shared prerequisites if available (monorepo), otherwise inline checks
    SHARED_PREREQS="$SCRIPT_DIR/../../shared/scripts/check-prerequisites.sh"
    if [ -f "$SHARED_PREREQS" ]; then
        source "$SHARED_PREREQS" bedrock 2.34.20
        region=$AWS_REGION
    else
        check_prerequisites
        region=$(get_aws_region)
    fi

    # Install CDK dependencies
    echo "Installing CDK dependencies..."

    cd "$SCRIPT_DIR/infrastructure/cdk"
    npm install --silent 2>/dev/null
    echo "  [OK] CDK dependencies installed"

    cd "$SCRIPT_DIR/scripts"
    npm install --silent 2>/dev/null
    echo "  [OK] Scripts dependencies installed"

    cd "$SCRIPT_DIR"
else
    echo ""
    echo "Skipping prerequisites and dependency installation..."
    region=$(get_aws_region)
fi

# Run the interactive setup wizard (handles CDK deployment)
echo ""
echo "Launching interactive setup wizard..."
echo "The wizard will guide you through DevOps Agent configuration and CDK deployment."
echo ""

cd "$SCRIPT_DIR"
npx ts-node scripts/setup-wizard.ts
wizard_exit_code=$?

if [ $wizard_exit_code -ne 0 ]; then
    echo ""
    echo "  Setup wizard exited with errors."
    echo "  Review the output above for details."
    exit $wizard_exit_code
fi

# ─── Deployment Summary ───────────────────────────────────────────────────────

stack_name="HealthEventAnalyzerStack-$region"

# Retrieve stack outputs
state_machine_arn=$(aws cloudformation describe-stacks --stack-name "$stack_name" \
    --query "Stacks[0].Outputs[?OutputKey=='StateMachineArn'].OutputValue" \
    --output text --no-cli-pager 2>/dev/null || echo "")
teams_table_name=$(aws cloudformation describe-stacks --stack-name "$stack_name" \
    --query "Stacks[0].Outputs[?OutputKey=='TeamsTableName'].OutputValue" \
    --output text --no-cli-pager 2>/dev/null || echo "")
sns_topic_arn=$(aws cloudformation describe-stacks --stack-name "$stack_name" \
    --query "Stacks[0].Outputs[?OutputKey=='NotificationTopicArn'].OutputValue" \
    --output text --no-cli-pager 2>/dev/null || echo "")

echo ""
echo "========================================"
echo "  Deployment Complete!"
echo "========================================"
echo ""
echo "  Region:          $region"
echo "  Stack:           $stack_name"
[ -n "$state_machine_arn" ] && echo "  State Machine:   $state_machine_arn"
[ -n "$teams_table_name" ] && echo "  Teams Table:     $teams_table_name"
[ -n "$sns_topic_arn" ] && echo "  SNS Topic:       $sns_topic_arn"
echo ""
echo "  Next Steps:"
echo "    1. Seed team routing:  ./scripts/seed-teams.sh $teams_table_name"
echo "    2. Upload DevOps Agent skill from devops-agent-skill/SKILL.md"
echo "    3. Test with: aws lambda invoke --function-name <EventRouter> --payload file://events/test-lambda-deprecation-event.json /tmp/out.json"
echo ""
