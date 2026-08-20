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

# Step 1: Install CDK dependencies
echo "[1/5] Installing CDK dependencies..."
pushd "$SCRIPT_DIR/infrastructure/cdk" > /dev/null
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt --quiet
popd > /dev/null
echo " ✓ CDK dependencies installed."

# Step 2: Build frontend
echo "[2/5] Building React frontend..."
pushd "$SCRIPT_DIR/frontend" > /dev/null
if [ ! -d "node_modules" ]; then
    npm install
fi
npm run build
popd > /dev/null
echo " ✓ Frontend built."

# Step 3: Deploy CDK stack
echo "[3/5] Deploying CDK infrastructure..."
pushd "$SCRIPT_DIR/infrastructure/cdk" > /dev/null
source .venv/bin/activate
export VIRTUAL_ENV="$SCRIPT_DIR/infrastructure/cdk/.venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"
export AWS_REGION="$REGION"
export CDK_DEFAULT_ACCOUNT="$ACCOUNT_ID"
npx cdk deploy "IamAnalyzerAssistantStack-$REGION" --require-approval never --outputs-file outputs.json
popd > /dev/null
echo " ✓ Infrastructure deployed."

# Step 4: Get stack outputs and configure frontend
echo "[4/5] Configuring frontend with stack outputs..."
OUTPUTS_FILE="$SCRIPT_DIR/infrastructure/cdk/outputs.json"

API_ENDPOINT=$(jq -r ".\"IamAnalyzerAssistantStack-$REGION\".ApiEndpoint" "$OUTPUTS_FILE")
USER_POOL_ID=$(jq -r ".\"IamAnalyzerAssistantStack-$REGION\".UserPoolId" "$OUTPUTS_FILE")
USER_POOL_CLIENT_ID=$(jq -r ".\"IamAnalyzerAssistantStack-$REGION\".UserPoolClientId" "$OUTPUTS_FILE")
IDENTITY_POOL_ID=$(jq -r ".\"IamAnalyzerAssistantStack-$REGION\".IdentityPoolId" "$OUTPUTS_FILE")
WEBSITE_URL=$(jq -r ".\"IamAnalyzerAssistantStack-$REGION\".WebsiteUrl" "$OUTPUTS_FILE")

# Generate frontend environment config
cat > "$SCRIPT_DIR/frontend/.env.production.local" <<EOF
VITE_API_ENDPOINT=$API_ENDPOINT
VITE_USER_POOL_ID=$USER_POOL_ID
VITE_USER_POOL_CLIENT_ID=$USER_POOL_CLIENT_ID
VITE_IDENTITY_POOL_ID=$IDENTITY_POOL_ID
VITE_REGION=$REGION
EOF
echo " ✓ Frontend configured."

# Step 5: Deploy frontend to S3 + invalidate CloudFront
echo "[5/5] Uploading frontend to S3..."
FRONTEND_BUCKET=$(jq -r ".\"IamAnalyzerAssistantStack-$REGION\".FrontendBucketName" "$OUTPUTS_FILE")

# Rebuild with production env vars
pushd "$SCRIPT_DIR/frontend" > /dev/null
npm run build
aws s3 sync dist/ "s3://$FRONTEND_BUCKET" --delete --region "$REGION"
popd > /dev/null

# Invalidate CloudFront cache
DISTRIBUTION_ID=$(jq -r ".\"IamAnalyzerAssistantStack-$REGION\".DistributionId" "$OUTPUTS_FILE")

if [ -n "$DISTRIBUTION_ID" ] && [ "$DISTRIBUTION_ID" != "None" ]; then
    aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" --paths "/*" --region "$REGION" > /dev/null
fi
echo " ✓ Frontend deployed."

# Create a default demo user (avoids Amplify force-change-password UI bug)
echo ""
echo "Creating demo user..."
DEMO_EMAIL="admin@example.com"
DEMO_PASSWORD="IamAnalyzer2024!"

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
