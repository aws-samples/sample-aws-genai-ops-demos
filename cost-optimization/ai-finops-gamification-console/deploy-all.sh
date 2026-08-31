#!/bin/bash
# Bash deployment script for FinOps Gamification Console Demo
set -e

DESTROY_INFRA=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --destroy-infra) DESTROY_INFRA=true; shift ;;
        *) echo "Unknown option $1"; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED_SCRIPTS_DIR="$SCRIPT_DIR/../../shared/scripts"

echo "=== FinOps Gamification Console Deployment ==="
echo ""

if [ "$DESTROY_INFRA" = true ]; then
    echo "Destroying infrastructure..."
    "$SHARED_SCRIPTS_DIR/deploy-cdk.sh" --cdk-directory infrastructure/cdk --destroy
    echo "Infrastructure destruction completed"
    exit 0
fi

# Use shared prerequisites check (requires CDK)
echo "Checking prerequisites..."
"$SHARED_SCRIPTS_DIR/check-prerequisites.sh" --require-cdk

# Get region using shared utility
source "$SHARED_SCRIPTS_DIR/../utils/aws-utils.sh"
REGION=$(get_aws_region)
ACCOUNT_ID=$(get_aws_account_id)

echo ""
echo -e "\033[0;36mDeployment Configuration:\033[0m"
echo -e "  Region:  $REGION"
echo -e "  Account: $ACCOUNT_ID"
echo ""

# Deploy CDK infrastructure using shared script
echo "Deploying AWS infrastructure (CDK)..."
"$SHARED_SCRIPTS_DIR/deploy-cdk.sh" --cdk-directory infrastructure/cdk

if [ $? -ne 0 ]; then
    echo "Error: CDK deployment failed"
    exit 1
fi

# Get CDK outputs
echo ""
echo "Getting CDK stack outputs..."
STACK_NAME="FinOpsGamificationConsole-$REGION"
OUTPUTS=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs" --output json --no-cli-pager 2>&1)

if [ $? -ne 0 ]; then
    echo "Warning: Could not retrieve stack outputs"
    echo "$OUTPUTS"
    exit 1
fi

# Parse outputs
USER_POOL_ID=$(echo "$OUTPUTS" | python3 -c "import sys, json; outputs = json.load(sys.stdin); print(next((o['OutputValue'] for o in outputs if o['OutputKey'] == 'UserPoolId'), ''))")
USER_POOL_CLIENT_ID=$(echo "$OUTPUTS" | python3 -c "import sys, json; outputs = json.load(sys.stdin); print(next((o['OutputValue'] for o in outputs if o['OutputKey'] == 'UserPoolClientId'), ''))")
API_ENDPOINT=$(echo "$OUTPUTS" | python3 -c "import sys, json; outputs = json.load(sys.stdin); print(next((o['OutputValue'] for o in outputs if o['OutputKey'] == 'ApiEndpoint'), ''))")
WEBSITE_URL=$(echo "$OUTPUTS" | python3 -c "import sys, json; outputs = json.load(sys.stdin); print(next((o['OutputValue'] for o in outputs if o['OutputKey'] == 'WebsiteUrl'), ''))")
S3_BUCKET=$(echo "$OUTPUTS" | python3 -c "import sys, json; outputs = json.load(sys.stdin); print(next((o['OutputValue'] for o in outputs if o['OutputKey'] == 'WebsiteBucketName'), ''))")
CLOUDFRONT_ID=$(echo "$OUTPUTS" | python3 -c "import sys, json; outputs = json.load(sys.stdin); print(next((o['OutputValue'] for o in outputs if o['OutputKey'] == 'CloudFrontDistributionId'), ''))")
SLACK_SECRET_ARN=$(echo "$OUTPUTS" | python3 -c "import sys, json; outputs = json.load(sys.stdin); print(next((o['OutputValue'] for o in outputs if o['OutputKey'] == 'SlackSecretArn'), ''))")

# Build and deploy frontend
echo ""
echo "Building React frontend..."

pushd frontend > /dev/null

# Install dependencies if needed
if [ ! -d "node_modules" ]; then
    echo "  Installing npm dependencies..."
    npm install
fi

# Create .env.production.local with deployment values
cat > .env.production.local << EOF
VITE_USER_POOL_ID=$USER_POOL_ID
VITE_USER_POOL_CLIENT_ID=$USER_POOL_CLIENT_ID
VITE_API_ENDPOINT=$API_ENDPOINT
EOF
echo -e "\033[0;32m  Generated .env.production.local with Cognito and API config\033[0m"

# Build the frontend
echo "  Running Vite build..."
npm run build

if [ $? -ne 0 ]; then
    popd > /dev/null
    echo "Error: Frontend build failed"
    exit 1
fi

popd > /dev/null

# Upload frontend to S3
if [ -n "$S3_BUCKET" ]; then
    echo ""
    echo "Uploading frontend to S3..."
    aws s3 sync frontend/dist/ "s3://$S3_BUCKET" --delete --no-cli-pager
    
    if [ $? -ne 0 ]; then
        echo "Error: S3 upload failed"
        exit 1
    fi
    echo -e "\033[0;32m  Frontend uploaded to S3 bucket: $S3_BUCKET\033[0m"
    
    # Invalidate CloudFront cache
    if [ -n "$CLOUDFRONT_ID" ]; then
        echo "  Invalidating CloudFront cache..."
        aws cloudfront create-invalidation --distribution-id "$CLOUDFRONT_ID" --paths "/*" --no-cli-pager > /dev/null
        echo -e "\033[0;32m  CloudFront cache invalidated\033[0m"
    fi
fi

# Deployment summary
echo ""
echo "========================================"
echo -e "\033[0;32m  Deployment Complete!\033[0m"
echo "========================================"
echo ""
echo -e "\033[0;36m  Console URL:        $WEBSITE_URL\033[0m"
echo -e "\033[0;36m  API Endpoint:       $API_ENDPOINT\033[0m"
echo -e "\033[0;36m  Region:             $REGION\033[0m"
echo ""
echo -e "\033[0;90m  Cognito User Pool:  $USER_POOL_ID\033[0m"
echo -e "\033[0;90m  User Pool Client:   $USER_POOL_CLIENT_ID\033[0m"
echo -e "\033[0;90m  Slack Secret:       $SLACK_SECRET_ARN\033[0m"
echo ""
echo "========================================"
echo -e "\033[0;33m  Post-Deployment Steps:\033[0m"
echo "========================================"
echo ""
echo "1. Create Cognito Users:"
echo -e "\033[0;90m   aws cognito-idp admin-create-user \\"
echo "     --user-pool-id $USER_POOL_ID \\"
echo "     --username admin@example.com \\"
echo "     --user-attributes Name=email,Value=admin@example.com \\"
echo "                        Name=given_name,Value=Admin \\"
echo -e "                        Name=family_name,Value=User\033[0m"
echo ""
echo "2. Add Users to Groups:"
echo -e "\033[0;90m   aws cognito-idp admin-add-user-to-group \\"
echo "     --user-pool-id $USER_POOL_ID \\"
echo "     --username admin@example.com \\"
echo -e "     --group-name finops-admin\033[0m"
echo ""
echo "3. Configure Slack Integration (Optional):"
echo "   Update the Slack secret with your bot token:"
echo -e "\033[0;90m   aws secretsmanager put-secret-value \\"
echo "     --secret-id \"$SLACK_SECRET_ARN\" \\"
echo -e "     --secret-string '{\"token\":\"xoxb-your-slack-bot-token\"}'\033[0m"
echo ""
echo "========================================"
echo ""
echo -e "\033[0;33mTo destroy the infrastructure later, run:\033[0m"
echo "  ./deploy-all.sh --destroy-infra"
echo ""
