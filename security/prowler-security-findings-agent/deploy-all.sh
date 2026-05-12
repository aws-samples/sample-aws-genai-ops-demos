#!/bin/bash
set -e

# =============================================================================
# Prowler Security Findings + DevOps Agent + Bedrock Nova Lite — Deploy
# =============================================================================
# Steps:
#   1. Prerequisites (AWS CLI, Node, CDK, zip)
#   2. DevOps Agent setup (Agent Space + webhook)
#   3. CDK deploy of non-frontend stacks (placeholder webhook first if needed)
#   4. Build and push Prowler scanner image via CodeBuild
#   5. Build React dashboard with env from CDK outputs
#   6. CDK deploy the frontend stack (uploads dist/ to S3 behind CloudFront)
#   7. Optional: create a default Cognito user so the dashboard is immediately usable
# =============================================================================

PROJECT_NAME="prowler-security"

echo "=============================================="
echo " Prowler Security Demo — Automated Deployment"
echo "=============================================="
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$REPO_ROOT/shared/scripts/check-prerequisites.sh" --require-cdk --skip-service-check --min-aws-cli-version 2.34.21

if ! command -v zip &>/dev/null; then
    echo "ERROR: 'zip' is required (used to package the scanner source for CodeBuild)."
    exit 1
fi

AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Account:  $AWS_ACCOUNT_ID"
echo "Region:   $AWS_REGION"
echo ""

# DevOps Agent setup — runs if EITHER the Agent Space ID or the webhook
# creds are missing. The script creates the Agent Space (if needed) and
# prompts for the webhook; both outputs are used below to build CDK context.
if [ -z "${DEVOPS_AGENT_SPACE_ID:-}" ] \
    || [ -z "${DEVOPS_AGENT_WEBHOOK_URL:-}" ] \
    || [ -z "${DEVOPS_AGENT_WEBHOOK_SECRET:-}" ]; then
    source "$SCRIPT_DIR/scripts/setup-devops-agent.sh"
    setup_devops_agent "$PROJECT_NAME"
fi

if [ -z "${DEVOPS_AGENT_WEBHOOK_URL:-}" ] || [ -z "${DEVOPS_AGENT_WEBHOOK_SECRET:-}" ]; then
    echo ""
    echo "WARNING: DEVOPS_AGENT_WEBHOOK_URL/SECRET not set — deploying with placeholder."
    echo "The DevOps Agent webhook will not fire until you re-run setup-devops-agent.sh."
    DEVOPS_AGENT_WEBHOOK_URL=""
    DEVOPS_AGENT_WEBHOOK_SECRET=""
fi

# Default the Agent Space region to the stack region — both resources live in
# the same account and the API expects the agent space to be reachable from
# there. Override by exporting DEVOPS_AGENT_REGION before running this script.
DEVOPS_AGENT_REGION="${DEVOPS_AGENT_REGION:-$AWS_REGION}"
DEVOPS_AGENT_SPACE_ID="${DEVOPS_AGENT_SPACE_ID:-}"
# BEDROCK_MODEL_ID defaults to the Nova Lite 2 global inference profile
# (global.amazon.nova-2-lite-v1:0) in cdk/bin/app.ts when not set. The global
# profile routes to the closest supported region automatically, so this works
# out of the box anywhere.
BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-}"
SCAN_SCHEDULE="${SCAN_SCHEDULE:-cron(0 6 * * ? *)}"

# Pass CDK context as an array so values with spaces or shell metacharacters
# survive expansion. SCAN_SCHEDULE in particular contains `*` and `?` which
# bash would otherwise word-split and glob-expand at the deploy call site,
# breaking the argv CDK receives.
CDK_CONTEXT=(
    -c "devOpsAgentWebhookUrl=$DEVOPS_AGENT_WEBHOOK_URL"
    -c "devOpsAgentWebhookSecret=$DEVOPS_AGENT_WEBHOOK_SECRET"
    -c "devOpsAgentRegion=$DEVOPS_AGENT_REGION"
    -c "devOpsAgentSpaceId=$DEVOPS_AGENT_SPACE_ID"
    -c "bedrockModelId=$BEDROCK_MODEL_ID"
    -c "scanSchedule=$SCAN_SCHEDULE"
)

# ── Timing helpers ────────────────────────────────────────────────
# `timed_step` prints "[N/TOTAL] Label..." at the start and
# "  done in Xm Ys." at the end, so users watching a ~20 min deploy
# get visible progress per phase instead of silence.
DEPLOY_START=$(date +%s)
_STEP_T=0
timed_step_start() {
    printf "%s\n" "$1"
    _STEP_T=$(date +%s)
}
timed_step_done() {
    local s=$(( $(date +%s) - _STEP_T ))
    printf "  done in %dm %02ds.\n\n" $((s/60)) $((s%60))
}

# Step 1: install CDK deps
timed_step_start "[1/7] Installing CDK dependencies..."
cd "$SCRIPT_DIR/cdk"
npm install --silent
cd "$SCRIPT_DIR"
timed_step_done

# Step 2: deploy all non-frontend stacks
timed_step_start "[2/7] Deploying CDK stacks (all except Frontend)..."
cd "$SCRIPT_DIR/cdk"
# Build a dummy frontend/dist so FrontendStack's BucketDeployment doesn't fail if we
# accidentally include it; we'll deploy the real one in step 6.
mkdir -p "$SCRIPT_DIR/frontend/dist"
if [ ! -f "$SCRIPT_DIR/frontend/dist/index.html" ]; then
  echo "<!doctype html><html><body>Prowler Security Dashboard — building…</body></html>" > "$SCRIPT_DIR/frontend/dist/index.html"
fi

npx cdk deploy \
    "ProwlerSecurityData-$AWS_REGION" \
    "ProwlerSecurityAuth-$AWS_REGION" \
    "ProwlerSecurityDevOpsAgent-$AWS_REGION" \
    "ProwlerSecurityScanner-$AWS_REGION" \
    "ProwlerSecurityIngest-$AWS_REGION" \
    "ProwlerSecurityApi-$AWS_REGION" \
    "ProwlerSecurityObservability-$AWS_REGION" \
    "${CDK_CONTEXT[@]}" \
    --require-approval never \
    --no-cli-pager
cd "$SCRIPT_DIR"
timed_step_done

# Step 3: build scanner image
timed_step_start "[3/7] Building Prowler scanner image via CodeBuild..."
RAW_BUCKET=$(aws cloudformation describe-stacks \
    --stack-name "ProwlerSecurityData-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='RawReportsBucketName'].OutputValue" \
    --output text)
BUILD_PROJECT=$(aws cloudformation describe-stacks \
    --stack-name "ProwlerSecurityScanner-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='BuildProjectName'].OutputValue" \
    --output text)
bash "$SCRIPT_DIR/scripts/build-scanner-image.sh" "$RAW_BUCKET" "$BUILD_PROJECT"
timed_step_done

# Step 4: pull CDK outputs for frontend config
timed_step_start "[4/7] Fetching CDK outputs for frontend build..."
USER_POOL_ID=$(aws cloudformation describe-stacks \
    --stack-name "ProwlerSecurityAuth-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text)
USER_POOL_CLIENT_ID=$(aws cloudformation describe-stacks \
    --stack-name "ProwlerSecurityAuth-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" --output text)
IDENTITY_POOL_ID=$(aws cloudformation describe-stacks \
    --stack-name "ProwlerSecurityAuth-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='IdentityPoolId'].OutputValue" --output text)
API_FUNCTION_URL=$(aws cloudformation describe-stacks \
    --stack-name "ProwlerSecurityApi-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='FunctionUrl'].OutputValue" --output text)
echo "  UserPoolId:       $USER_POOL_ID"
echo "  UserPoolClientId: $USER_POOL_CLIENT_ID"
echo "  IdentityPoolId:   $IDENTITY_POOL_ID"
echo "  API URL:          $API_FUNCTION_URL"
timed_step_done

# Step 5: build frontend
timed_step_start "[5/7] Building frontend..."
bash "$SCRIPT_DIR/scripts/build-frontend.sh" \
    "$AWS_REGION" "$USER_POOL_ID" "$USER_POOL_CLIENT_ID" "$IDENTITY_POOL_ID" "$API_FUNCTION_URL"
timed_step_done

# Step 6: deploy frontend stack
timed_step_start "[6/7] Deploying Frontend stack..."
cd "$SCRIPT_DIR/cdk"
npx cdk deploy "ProwlerSecurityFrontend-$AWS_REGION" \
    "${CDK_CONTEXT[@]}" \
    --require-approval never \
    --no-cli-pager
cd "$SCRIPT_DIR"
timed_step_done

WEBSITE_URL=$(aws cloudformation describe-stacks \
    --stack-name "ProwlerSecurityFrontend-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='WebsiteUrl'].OutputValue" --output text)

# Write the DevOps Agent webhook bundle (URL, HMAC secret, agent space id)
# into the Secrets Manager secret created by CDK. This is run AFTER the CDK
# deploy so the secret resource exists. CDK only sets a placeholder on create;
# the real values live here and survive every subsequent partial `cdk deploy`.
if [ -n "${DEVOPS_AGENT_WEBHOOK_URL:-}" ] && [ -n "${DEVOPS_AGENT_WEBHOOK_SECRET:-}" ]; then
    SECRET_NAME="prowler-security/devops-agent-webhook-secret"  # pragma: allowlist secret
    BUNDLE=$(jq -n \
        --arg url "$DEVOPS_AGENT_WEBHOOK_URL" \
        --arg secret "$DEVOPS_AGENT_WEBHOOK_SECRET" \
        --arg space "${DEVOPS_AGENT_SPACE_ID:-}" \
        '{webhookUrl: $url, webhookSecret: $secret, agentSpaceId: $space}')
    if aws secretsmanager put-secret-value \
        --secret-id "$SECRET_NAME" \
        --secret-string "$BUNDLE" \
        --no-cli-pager >/dev/null 2>&1; then
        echo "  DevOps Agent bundle written to Secrets Manager."
    else
        echo "  WARN: failed to write DevOps Agent bundle. Run scripts/setup-devops-agent.sh manually to retry."
    fi
fi

# Step 7: create a default demo user so the dashboard is usable out of the box
DEMO_USERNAME="${DEMO_USERNAME:-demo@prowler-security.local}"
DEMO_PASSWORD="${DEMO_PASSWORD:-ProwlerDemo2026!}"
timed_step_start "[7/7] Creating default Cognito user $DEMO_USERNAME..."
if aws cognito-idp admin-get-user --user-pool-id "$USER_POOL_ID" --username "$DEMO_USERNAME" --no-cli-pager >/dev/null 2>&1; then
    echo "  User already exists — refreshing password."
else
    aws cognito-idp admin-create-user \
        --user-pool-id "$USER_POOL_ID" \
        --username "$DEMO_USERNAME" \
        --user-attributes Name=email,Value="$DEMO_USERNAME" Name=email_verified,Value=true \
        --temporary-password "$DEMO_PASSWORD" \
        --message-action SUPPRESS \
        --no-cli-pager >/dev/null
fi
aws cognito-idp admin-set-user-password \
    --user-pool-id "$USER_POOL_ID" \
    --username "$DEMO_USERNAME" \
    --password "$DEMO_PASSWORD" \
    --permanent \
    --no-cli-pager >/dev/null
timed_step_done

DEPLOY_TOTAL=$(( $(date +%s) - DEPLOY_START ))
printf "Total deploy time: %dm %02ds\n\n" $((DEPLOY_TOTAL/60)) $((DEPLOY_TOTAL%60))

echo "=============================================="
echo " Deployment complete"
echo "=============================================="
echo ""
echo "Dashboard:   $WEBSITE_URL"
echo "API URL:     $API_FUNCTION_URL"
echo ""
echo "Demo login:"
echo "  Username: $DEMO_USERNAME"
echo "  Password: $DEMO_PASSWORD"
echo "  (Override with DEMO_USERNAME / DEMO_PASSWORD env vars before deploy.)"
echo ""
echo "Trigger your first scan:"
echo "  1. Log in to $WEBSITE_URL"
echo "  2. Click 'Run scan now' on the Dashboard"
echo "  3. Wait ~3-10 min for findings to appear"
echo "  4. Open any finding and click 'Generate Bedrock Insights' for the"
echo "     Nova Lite 2 remediation playbook, or 'Investigate with DevOps Agent'"
echo "     to dispatch an autonomous investigation (both are on-demand)."
echo ""
if [ -z "${DEVOPS_AGENT_WEBHOOK_URL:-}" ] || [ "${DEVOPS_AGENT_WEBHOOK_URL:-}" = "" ]; then
    echo "NOTE: DevOps Agent webhook was NOT configured (non-interactive deploy)."
    echo "      To wire it up, run from a terminal:"
    echo "        bash scripts/setup-devops-agent.sh"
    echo ""
fi
