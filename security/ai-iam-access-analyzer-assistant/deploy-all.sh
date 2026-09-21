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

# Report what the assistant will be able to see in this region. The stack deploys a
# read-only role and creates none of these: it reads whatever Security Hub CSPM, IAM
# Access Analyzer and CloudTrail already hold here. So the deployment cannot fail on
# them, but the demo is empty without them, and an analyzer of the wrong kind looks
# exactly like a clean account. Every line is one of three states: what is there,
# what is missing, or what could not be checked (the operator's credentials, not the
# Lambda role, run these calls). Never blocks; mirrors src/tools/list_findings.py.
# Mirrors Show-DataSourceStatus in deploy-all.ps1.
show_data_source_status() {
    local region="$1"
    local last_aws_error=""

    line() {  # state label text
        local mark
        case "$1" in
            ok)      mark="+" ;;
            missing) mark="!" ;;
            *)       mark="?" ;;
        esac
        printf '   [%s] %-22s %s\n' "$mark" "$2" "$3"
    }
    hint() { printf '       %s\n' "$1"; }
    # Read-only AWS CLI call: prints stdout and returns 0, or sets last_aws_error and
    # returns 1. Never aborts the script (set -e is disabled around the call).
    aws_read() {
        local out
        set +e
        out=$(aws "$@" --region "$region" --no-cli-pager 2>&1)
        local rc=$?
        set -e
        if [ $rc -ne 0 ]; then
            last_aws_error=$(printf '%s' "$out" | head -n 1)
            return 1
        fi
        printf '%s' "$out"
    }

    echo " Data sources in ${region}:"
    echo "   The assistant reads what these services already hold in this region; the"
    echo "   deployment never depends on them. This is what it will be able to see today."
    echo ""

    # 1. Security Hub CSPM enabled in this region
    local hub_enabled=false hub
    if hub=$(aws_read securityhub describe-hub --query SubscribedAt --output text); then
        hub_enabled=true
        line ok "Security Hub CSPM" "enabled (since ${hub:0:10})"
    elif printf '%s' "$last_aws_error" | grep -qE 'InvalidAccessException|not subscribed'; then
        line missing "Security Hub CSPM" "not enabled: the assistant will see no findings at all"
        hint "Enable it: https://console.aws.amazon.com/securityhub/ (Security Hub CSPM, this region)"
    else
        line unknown "Security Hub CSPM" "could not check ($last_aws_error)"
    fi

    # 2. IAM Access Analyzer -> Security Hub integration (auto-enabled, can be disabled)
    local integration_on=false sub
    if [ "$hub_enabled" = true ]; then
        if sub=$(aws_read securityhub list-enabled-products-for-import \
                --query "length(ProductSubscriptions[?contains(@, 'product-subscription/aws/access-analyzer')])" \
                --output text); then
            if [ "$sub" = "0" ]; then
                line missing "Analyzer integration" "Access Analyzer findings are not flowing into Security Hub"
                hint "Security Hub CSPM console > Integrations > IAM Access Analyzer > Accept findings"
            else
                integration_on=true
                line ok "Analyzer integration" "Access Analyzer findings flow into Security Hub"
            fi
        else
            line unknown "Analyzer integration" "could not check ($last_aws_error)"
        fi
    fi

    # 3. Which analyzer kinds exist. External access (ACCOUNT/ORGANIZATION) yields
    #    public and cross-account findings; unused access (*_UNUSED_ACCESS) yields
    #    unused roles and permissions, which most of the suggested prompts rely on.
    local analyzers external unused
    if analyzers=$(aws_read accessanalyzer list-analyzers \
            --query "analyzers[?status=='ACTIVE'].[type,name]" --output text); then
        external=$(printf '%s\n' "$analyzers" | grep -v 'UNUSED_ACCESS' | head -n 1 || true)
        unused=$(printf '%s\n' "$analyzers" | grep 'UNUSED_ACCESS' | head -n 1 || true)
        if [ -n "$external" ]; then
            line ok "External access" "$(printf '%s' "$external" | cut -f2) ($(printf '%s' "$external" | cut -f1)): public and cross-account findings"
        else
            line missing "External access" "no analyzer: public and cross-account findings will not appear"
            hint "aws accessanalyzer create-analyzer --analyzer-name external-access --type ACCOUNT --region $region"
        fi
        if [ -n "$unused" ]; then
            line ok "Unused access" "$(printf '%s' "$unused" | cut -f2) ($(printf '%s' "$unused" | cut -f1)): unused roles and permissions"
        else
            line missing "Unused access" "no analyzer: unused roles and permissions will not appear"
            hint "aws accessanalyzer create-analyzer --analyzer-name unused-access --type ACCOUNT_UNUSED_ACCESS --configuration \"unusedAccess={unusedAccessAge=90}\" --region $region"
            hint "(billed per IAM role and user analyzed; external access analyzers are free)"
        fi
    else
        line unknown "Access Analyzer" "could not check ($last_aws_error)"
    fi

    # 4. Findings the assistant can see right now: same filter as list_findings.py.
    local count
    if [ "$hub_enabled" = true ] && [ "$integration_on" = true ]; then
        local filters='{"ProductName":[{"Value":"IAM Access Analyzer","Comparison":"EQUALS"}],"RecordState":[{"Value":"ACTIVE","Comparison":"EQUALS"}],"WorkflowStatus":[{"Value":"NEW","Comparison":"EQUALS"}]}'
        if count=$(aws_read securityhub get-findings --filters "$filters" --max-results 100 \
                --query "length(Findings)" --output text); then
            if [ "$count" = "0" ]; then
                line missing "Findings visible now" "0 active. Either nothing to report, or the analyzer is new"
                hint "New findings reach Security Hub within about 30 minutes of analyzer creation."
            elif [ "$count" -ge 100 ] 2>/dev/null; then
                line ok "Findings visible now" "100 or more active"
            else
                line ok "Findings visible now" "$count active"
            fi
        else
            line unknown "Findings visible now" "could not check ($last_aws_error)"
        fi
    fi

    # 5. CloudTrail: nothing to configure. Policy generation reads the always-on
    #    90-day management event history of this region via cloudtrail:LookupEvents.
    line ok "CloudTrail" "90-day event history of this region (no trail required)"
    echo ""
}

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

# admin-create-user: allow re-runs on an existing user (UsernameExistsException),
# but surface any other error rather than silently continuing to a
# "Deployment Complete!" summary whose credentials the pool doesn't actually
# accept. Capture stderr into a variable while dropping stdout, then decide
# based on both the exit status and the specific error class.
CREATE_ERR="$(aws cognito-idp admin-create-user \
    --user-pool-id "$USER_POOL_ID" \
    --username "$DEMO_EMAIL" \
    --user-attributes Name=email_verified,Value=true \
    --message-action SUPPRESS \
    --region "$REGION" 2>&1 1>/dev/null)" && CREATE_STATUS=0 || CREATE_STATUS=$?

if [ "$CREATE_STATUS" -ne 0 ]; then
    if printf '%s' "$CREATE_ERR" | grep -q 'UsernameExistsException'; then
        echo "   (demo user already exists — password will be rotated below)"
    else
        echo ""
        echo " ✗ Failed to create demo user:"
        printf '%s\n' "$CREATE_ERR"
        echo ""
        echo "   The deployment finished but sign-in with the demo credentials"
        echo "   below will not work. Fix the error above and re-run this script,"
        echo "   or create a user manually via the Cognito console for User Pool"
        echo "   $USER_POOL_ID."
        exit 1
    fi
fi

# admin-set-user-password: this is the step that guarantees the demo user can
# sign in (bypasses the force-change-password flow the Amplify hosted UI
# mishandles). Do NOT swallow failures — a silent failure here produces the
# exact InvalidPasswordException symptom this script exists to avoid.
if ! aws cognito-idp admin-set-user-password \
    --user-pool-id "$USER_POOL_ID" \
    --username "$DEMO_EMAIL" \
    --password "$DEMO_PASSWORD" \
    --permanent \
    --region "$REGION"; then
    echo ""
    echo " ✗ Failed to set demo user password. Sign-in will not work."
    echo "   The user exists in the pool but has no usable permanent password."
    exit 1
fi

echo " ✓ Demo user created."

# Done
echo ""
echo "========================================"
echo " Deployment Complete!"
echo "========================================"
echo " Open the demo: $WEBSITE_URL"
echo " Region: $REGION"
echo ""
show_data_source_status "$REGION"
echo " Sign in with:"
echo "   Email:    $DEMO_EMAIL"
echo "   Password: $DEMO_PASSWORD"
echo ""
echo " (You can create additional users via the Cognito console or CLI)"
echo ""
