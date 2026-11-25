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
# prompts for the webhook; the Agent Space ID goes into the CDK context and
# the webhook into Secrets Manager after the deploy.
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
# EVENT_SCANS=true: risky CloudTrail write calls start targeted Prowler scans (needs a CloudTrail trail).
EVENT_SCANS="${EVENT_SCANS:-false}"

# VPC Block Public Access in block-ingress mode drops replies to traffic the
# scanner sends straight through the internet gateway, so it can't pull its
# image from ECR. NAT gateway traffic is still allowed, so switch to it.
# Override with SCANNER_NAT_GATEWAY=true|false.
if [ -z "${SCANNER_NAT_GATEWAY:-}" ]; then
    BPA_MODE=$(aws ec2 describe-vpc-block-public-access-options --region "$AWS_REGION" \
        --query 'VpcBlockPublicAccessOptions.InternetGatewayBlockMode' --output text 2>/dev/null || echo "off")
    case "$BPA_MODE" in
        block-ingress)
            echo "VPC Block Public Access is block-ingress: the scanner will use a NAT gateway (~\$33/month)."
            SCANNER_NAT_GATEWAY=true ;;
        block-bidirectional)
            echo "WARNING: VPC Block Public Access is block-bidirectional; scans need a BPA exclusion for the scanner VPC."
            SCANNER_NAT_GATEWAY=false ;;
        *)
            SCANNER_NAT_GATEWAY=false ;;
    esac
fi

# Same context on every deploy-cdk.sh call: each one synthesizes the whole app and deploys the
# stack's dependencies too. The webhook URL and HMAC secret stay out of it (Secrets Manager only).
CDK_CONTEXT=(
    --cdk-context "devOpsAgentRegion=$DEVOPS_AGENT_REGION"
    --cdk-context "devOpsAgentSpaceId=$DEVOPS_AGENT_SPACE_ID"
    --cdk-context "bedrockModelId=$BEDROCK_MODEL_ID"
    --cdk-context "scanSchedule=$SCAN_SCHEDULE"
    --cdk-context "scannerNatGateway=$SCANNER_NAT_GATEWAY"
    --cdk-context "eventScans=$EVENT_SCANS"
)
deploy_stack() {   # stack-name [deploy-cdk.sh flags]
    # bash -f: deploy-cdk.sh expands context values unquoted, so SCAN_SCHEDULE's * and ? must not glob.
    bash -f "$REPO_ROOT/shared/scripts/deploy-cdk.sh" --cdk-directory "$SCRIPT_DIR/cdk" \
        --stack-name "$1" "${@:2}" "${CDK_CONTEXT[@]}"
}

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

# Step 1: deploy all non-frontend stacks, one shared deploy-cdk.sh call each, in
# dependency order (the first call also checks the CDK bootstrap).
timed_step_start "[1/6] Deploying CDK stacks (all except Frontend)..."
# Build a dummy frontend/dist so FrontendStack's BucketDeployment doesn't fail if we
# accidentally include it; we'll deploy the real one in step 5.
mkdir -p "$SCRIPT_DIR/frontend/dist"
if [ ! -f "$SCRIPT_DIR/frontend/dist/index.html" ]; then
  echo "<!doctype html><html><body>Prowler Security Dashboard — building…</body></html>" > "$SCRIPT_DIR/frontend/dist/index.html"
fi

BOOTSTRAP_FLAG=()
for STACK in Data Auth DevOpsAgent Scanner Ingest Api Observability; do
    deploy_stack "ProwlerSecurity$STACK-$AWS_REGION" "${BOOTSTRAP_FLAG[@]}"
    BOOTSTRAP_FLAG=(--skip-bootstrap)
done
timed_step_done

# Step 2: build scanner image
timed_step_start "[2/6] Building Prowler scanner image via CodeBuild..."
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

# Step 3: pull CDK outputs for frontend config
timed_step_start "[3/6] Fetching CDK outputs for frontend build..."
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

# Step 4: build frontend
timed_step_start "[4/6] Building frontend..."
bash "$SCRIPT_DIR/scripts/build-frontend.sh" \
    "$AWS_REGION" "$USER_POOL_ID" "$USER_POOL_CLIENT_ID" "$IDENTITY_POOL_ID" "$API_FUNCTION_URL"
timed_step_done

# Step 5: deploy frontend stack
timed_step_start "[5/6] Deploying Frontend stack..."
deploy_stack "ProwlerSecurityFrontend-$AWS_REGION" --skip-bootstrap
timed_step_done

WEBSITE_URL=$(aws cloudformation describe-stacks \
    --stack-name "ProwlerSecurityFrontend-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='WebsiteUrl'].OutputValue" --output text)

# Write the DevOps Agent webhook bundle (URL, HMAC secret, agent space id)
# into the Secrets Manager secret created by CDK. This is run AFTER the CDK
# deploy so the secret resource exists. CDK only sets a placeholder on create;
# the real values live here and survive every subsequent partial `cdk deploy`.
# A bundle that already holds a webhook is never overwritten (rotate with setup-devops-agent).
if [ -n "${DEVOPS_AGENT_WEBHOOK_URL:-}" ] && [ -n "${DEVOPS_AGENT_WEBHOOK_SECRET:-}" ]; then
    SECRET_NAME="prowler-security/devops-agent-webhook-secret"  # pragma: allowlist secret
    if ! STORED_URL=$(set -o pipefail; aws secretsmanager get-secret-value --secret-id "$SECRET_NAME" \
        --query SecretString --output text --no-cli-pager 2>/dev/null | jq -r '.webhookUrl // ""'); then
        echo "  WARN: could not read the DevOps Agent bundle, so it was not written. Run scripts/setup-devops-agent.sh to retry."
    elif [ -n "$STORED_URL" ] && [ "$STORED_URL" != "NOT_CONFIGURED" ]; then
        echo "  DevOps Agent bundle already holds a webhook; left unchanged."
    else
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
fi

# Step 6: create a default demo user so the dashboard is usable out of the box
DEMO_USERNAME="${DEMO_USERNAME:-demo@prowler-security.local}"
DEMO_PASSWORD="${DEMO_PASSWORD:-ProwlerDemo2026!}"
timed_step_start "[6/6] Creating default Cognito user $DEMO_USERNAME..."
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

# Kick off the first scan now — image is in ECR (step 2 already pushed it),
# so the Fargate task will pull it cleanly. Doing this here instead of from
# a CDK custom resource at stack-create time avoids the race where the
# stack creates faster than CodeBuild publishes :latest.
SCANNER_CLUSTER_ARN=$(aws cloudformation describe-stacks \
    --stack-name "ProwlerSecurityScanner-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ClusterArn'].OutputValue" --output text)
SCANNER_TASK_DEF=$(aws cloudformation describe-stacks \
    --stack-name "ProwlerSecurityScanner-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='TaskDefinitionArn'].OutputValue" --output text)
SCANNER_SUBNETS=$(aws cloudformation describe-stacks \
    --stack-name "ProwlerSecurityScanner-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ScannerSubnetIds'].OutputValue" --output text)
SCANNER_SG=$(aws cloudformation describe-stacks \
    --stack-name "ProwlerSecurityScanner-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ScannerSecurityGroupId'].OutputValue" --output text)
SCANNER_PUBLIC_IP=$(aws cloudformation describe-stacks \
    --stack-name "ProwlerSecurityScanner-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ScannerAssignPublicIp'].OutputValue" --output text)

echo "Starting first Prowler scan..."
NETWORK_CFG=$(printf '{"awsvpcConfiguration":{"subnets":[%s],"securityGroups":["%s"],"assignPublicIp":"%s"}}' \
    "$(echo "$SCANNER_SUBNETS" | awk -F, '{for(i=1;i<=NF;i++) printf (i>1?",":"") "\"" $i "\""}')" \
    "$SCANNER_SG" "${SCANNER_PUBLIC_IP:-ENABLED}")
if aws ecs run-task \
    --cluster "$SCANNER_CLUSTER_ARN" \
    --task-definition "$SCANNER_TASK_DEF" \
    --launch-type FARGATE \
    --platform-version LATEST \
    --network-configuration "$NETWORK_CFG" \
    --count 1 \
    --no-cli-pager --query 'tasks[0].taskArn' --output text >/dev/null 2>&1; then
    echo "  First scan launched."
else
    echo "  WARN: failed to launch first scan — start it from the dashboard with 'Run scan now'."
fi
echo ""

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
echo "First scan is running:"
echo "  1. Log in to $WEBSITE_URL"
echo "  2. Wait ~3-10 min for findings to appear (re-run with 'Run scan now' anytime)"
echo "  3. Open any finding and click 'Generate Bedrock Insights' for the"
echo "     Nova Lite 2 remediation playbook, or 'Investigate with DevOps Agent'"
echo "     to dispatch an autonomous investigation (both are on-demand)."
echo ""
if [ -z "${DEVOPS_AGENT_WEBHOOK_URL:-}" ] || [ "${DEVOPS_AGENT_WEBHOOK_URL:-}" = "" ]; then
    echo "NOTE: DevOps Agent webhook was NOT configured (non-interactive deploy)."
    echo "      To wire it up, run from a terminal:"
    echo "        bash scripts/setup-devops-agent.sh"
    echo ""
fi
