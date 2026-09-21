#!/bin/bash
# AI IAM Access Analyzer Assistant — Deployment Script
# Deploys CDK infrastructure and React frontend

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Keep in sync with BEDROCK_MODEL_ID in infrastructure/cdk/stacks/api_construct.py
BEDROCK_MODEL_ID="us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# Verify the configured Bedrock model is actually enabled BEFORE the long deploy,
# so operators don't deploy successfully and then hit an opaque runtime 500 whose
# real cause (model access not enabled / still propagating) is masked by a
# misleading "AWS Marketplace subscription" error. Warns, does not hard-fail —
# propagation timing means a legitimate deploy shouldn't be blocked.
check_bedrock_model_access() {
    local region="$1"
    local model_id="$BEDROCK_MODEL_ID"
    # Cross-region inference profile IDs are prefixed (us./eu./apac.); the
    # availability API expects the underlying base model id.
    local base_model_id="${model_id#us.}"
    base_model_id="${base_model_id#eu.}"
    base_model_id="${base_model_id#apac.}"

    echo "Checking Bedrock model access for $model_id in $region ..."
    local avail=""
    if avail=$(aws bedrock get-foundation-model-availability \
        --model-id "$base_model_id" --region "$region" 2>/dev/null); then
        if echo "$avail" | grep -q '"entitlementAvailability": *"AVAILABLE"'; then
            echo " ✓ Bedrock model access is enabled."
            return 0
        fi
        echo ""
        echo " ⚠ Bedrock model access is NOT enabled for $base_model_id in $region."
        echo "   Enable it: Bedrock console → Model access → enable the model."
        echo "   If you just enabled it, wait ~2 minutes for propagation."
        echo ""
        return 1
    fi

    # Fallback for CLIs without get-foundation-model-availability: a tiny converse.
    if aws bedrock-runtime converse --model-id "$model_id" \
        --messages '[{"role":"user","content":[{"text":"ping"}]}]' \
        --region "$region" >/dev/null 2>&1; then
        echo " ✓ Bedrock model is reachable (test invocation succeeded)."
        return 0
    fi
    echo ""
    echo " ⚠ Could not invoke $model_id in $region — model access may not be enabled"
    echo "   or is still propagating. Enable it in the Bedrock console → Model access."
    echo "   NOTE: a raw AccessDenied may mention 'AWS Marketplace subscriptions' — that"
    echo "   wording is misleading; this is Bedrock model access, not an SCP/Marketplace issue."
    echo ""
    return 1
}

# Check prerequisites
if [ -f "$SCRIPT_DIR/../../shared/scripts/check-prerequisites.sh" ]; then
    source "$SCRIPT_DIR/../../shared/scripts/check-prerequisites.sh" --required-service bedrock --min-aws-cli-version 2.31.13
else
    # Standalone mode — inline checks
    echo "Checking prerequisites..."
    command -v aws >/dev/null 2>&1 || { echo "ERROR: AWS CLI not found."; exit 1; }
    command -v python3 >/dev/null 2>&1 || { echo "ERROR: Python 3 not found."; exit 1; }
    command -v node >/dev/null 2>&1 || { echo "ERROR: Node.js not found."; exit 1; }
    command -v npm >/dev/null 2>&1 || { echo "ERROR: npm not found."; exit 1; }
    aws sts get-caller-identity >/dev/null 2>&1 || { echo "ERROR: AWS credentials not configured."; exit 1; }
fi

echo ""
echo "========================================"
echo " AI IAM Access Analyzer Assistant"
echo " Deployment Script"
echo "========================================"
echo ""

# Get region (shared prereqs may set AWS_REGION, otherwise detect)
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo "us-east-1")}}"
ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text)

echo " Region: $REGION"
echo " Account: $ACCOUNT_ID"
echo ""

# Verify Bedrock model access up front (warns but continues)
check_bedrock_model_access "$REGION" || \
    echo " ⚠ Continuing deploy despite the model-access warning above — the assistant will"$'\n'"   return an access error at runtime until model access is enabled and propagated."
echo ""

STACK_NAME="IamAnalyzerAssistantStack-$REGION"

# Step 1: Build frontend
echo "[1/4] Building React frontend..."
pushd "$SCRIPT_DIR/frontend" > /dev/null
if [ ! -d "node_modules" ]; then
    npm install
fi
npm run build
popd > /dev/null
echo " ✓ Frontend built."

# Step 2: Deploy CDK stack via the shared script (installs CDK deps, bootstraps, deploys)
echo "[2/4] Deploying CDK infrastructure..."
export AWS_REGION="$REGION"
export CDK_DEFAULT_ACCOUNT="$ACCOUNT_ID"
"$SCRIPT_DIR/../../shared/scripts/deploy-cdk.sh" --cdk-directory "$SCRIPT_DIR/infrastructure/cdk" --stack-name "$STACK_NAME"
echo " ✓ Infrastructure deployed."

# Step 3: Get stack outputs and configure frontend
echo "[3/4] Configuring frontend with stack outputs..."
get_stack_output() {
    aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" --no-cli-pager \
        --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

API_ENDPOINT=$(get_stack_output ApiEndpoint)
USER_POOL_ID=$(get_stack_output UserPoolId)
USER_POOL_CLIENT_ID=$(get_stack_output UserPoolClientId)
IDENTITY_POOL_ID=$(get_stack_output IdentityPoolId)
WEBSITE_URL=$(get_stack_output WebsiteUrl)
FRONTEND_BUCKET=$(get_stack_output FrontendBucketName)
DISTRIBUTION_ID=$(get_stack_output DistributionId)

if [ -z "$USER_POOL_ID" ] || [ -z "$FRONTEND_BUCKET" ]; then
    echo "ERROR: Failed to read outputs from stack $STACK_NAME"
    exit 1
fi

# Generate frontend environment config
cat > "$SCRIPT_DIR/frontend/.env.production.local" <<EOF
VITE_API_ENDPOINT=$API_ENDPOINT
VITE_USER_POOL_ID=$USER_POOL_ID
VITE_USER_POOL_CLIENT_ID=$USER_POOL_CLIENT_ID
VITE_IDENTITY_POOL_ID=$IDENTITY_POOL_ID
VITE_REGION=$REGION
EOF
echo " ✓ Frontend configured."

# Step 4: Deploy frontend to S3 + invalidate CloudFront
echo "[4/4] Uploading frontend to S3..."

# Rebuild with production env vars
pushd "$SCRIPT_DIR/frontend" > /dev/null
npm run build
aws s3 sync dist/ "s3://$FRONTEND_BUCKET" --delete --region "$REGION"
popd > /dev/null

# Invalidate CloudFront cache
if [ -n "$DISTRIBUTION_ID" ] && [ "$DISTRIBUTION_ID" != "None" ]; then
    aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" --paths "/*" --region "$REGION" > /dev/null
fi
echo " ✓ Frontend deployed."

# Create a default demo user (avoids Amplify force-change-password UI bug)
echo ""
echo "Creating demo user..."
DEMO_EMAIL="admin@example.com"
# Generate a unique, strong password per deployment instead of shipping a hardcoded
# credential in the repo. Cognito's default policy requires >=8 chars with upper,
# lower, digit, and symbol, so we guarantee one of each (the "Demo" prefix and "!9"
# suffix) and add random alphanumeric entropy. Uses /dev/urandom + tr (POSIX, no
# extra dependency) rather than openssl.
#
# Read a FINITE chunk of /dev/urandom, THEN filter. Piping endless /dev/urandom
# straight into "tr ... | head -c 12" lets head close the pipe after 12 bytes while
# tr is still writing, so tr dies with SIGPIPE (exit 141); under "set -euo pipefail"
# that 141 aborts the whole deploy right here -- before the aws cognito calls below,
# so their "|| true" guards never get a chance to mask it. Bounding the source with
# "head -c 256" lets tr reach EOF cleanly; only ~62/256 bytes survive the
# alphanumeric filter, so 256 raw bytes yield ~62 chars on average -- far more than
# the 12 that cut takes, so the segment is reliably a full 12 characters.
DEMO_PASSWORD="Demo$(head -c 256 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9' | cut -c1-12)!9"

# Create user (ignore error if already exists)
aws cognito-idp admin-create-user \
  --user-pool-id "$USER_POOL_ID" \
  --username "$DEMO_EMAIL" \
  --user-attributes Name=email_verified,Value=true \
  --message-action SUPPRESS \
  --region "$REGION" 2>/dev/null || true

# Set permanent password (bypasses force-change-password flow)
aws cognito-idp admin-set-user-password \
  --user-pool-id "$USER_POOL_ID" \
  --username "$DEMO_EMAIL" \
  --password "$DEMO_PASSWORD" \
  --permanent \
  --region "$REGION" 2>/dev/null || true

echo " ✓ Demo user created."

# Done
echo ""
echo "========================================"
echo " Deployment Complete!"
echo "========================================"
echo " Open the demo: $WEBSITE_URL"
echo " Region: $REGION"
echo ""
echo " Sign in with:"
echo "   Email:    $DEMO_EMAIL"
echo "   Password: $DEMO_PASSWORD"
echo ""
echo " (You can create additional users via the Cognito console or CLI)"
echo ""
