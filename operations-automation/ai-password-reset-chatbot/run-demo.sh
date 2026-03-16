#!/bin/bash
set -e

echo "=== Password Reset Chatbot Deployment ==="

# Check prerequisites using shared script
source ../../shared/scripts/check-prerequisites.sh agentcore 2.31.13

# Use region from shared prerequisites
REGION=$AWS_REGION

# Set stack names with region suffix for multi-region support
INFRA_STACK="PasswordResetInfra-$REGION"
AUTH_STACK="PasswordResetAuth-$REGION"
RUNTIME_STACK="PasswordResetRuntime-$REGION"
FRONTEND_STACK="PasswordResetFrontend-$REGION"

# Step 1: Install CDK dependencies
echo -e "\n[1/7] Installing CDK dependencies..."
if [ ! -d "cdk/node_modules" ]; then
    cd cdk && npm install && cd ..
else
    echo "      CDK dependencies already installed"
fi

# Step 2: Install frontend dependencies
echo -e "\n[2/7] Installing frontend dependencies..."
cd frontend && npm install && cd ..

# Step 3: Create placeholder frontend build
echo -e "\n[3/7] Creating placeholder frontend build..."
mkdir -p frontend/dist
echo '<!DOCTYPE html><html><body><h1>Building...</h1></body></html>' > frontend/dist/index.html


# Step 4: Bootstrap CDK
echo -e "\n[4/7] Bootstrapping CDK environment..."
cd cdk
TIMESTAMP=$(date +%Y%m%d%H%M%S)
npx cdk bootstrap --output "cdk.out.$TIMESTAMP" --no-cli-pager
cd ..

# Step 5: Deploy infrastructure stack
echo -e "\n[5/7] Deploying infrastructure stack..."
cd cdk
TIMESTAMP=$(date +%Y%m%d%H%M%S)
npx cdk deploy "$INFRA_STACK" --output "cdk.out.$TIMESTAMP" --no-cli-pager --require-approval never
cd ..

# Step 6: Deploy auth stack
echo -e "\n[6/7] Deploying authentication stack (Cognito User Pool)..."
cd cdk
TIMESTAMP=$(date +%Y%m%d%H%M%S)
npx cdk deploy "$AUTH_STACK" --output "cdk.out.$TIMESTAMP" --no-cli-pager --require-approval never
cd ..

# Step 7: Deploy runtime stack
echo -e "\n[7/7] Deploying AgentCore runtime (anonymous access)..."
echo "      Note: CodeBuild will compile the container - this takes 5-10 minutes"
cd cdk
TIMESTAMP=$(date +%Y%m%d%H%M%S)
npx cdk deploy "$RUNTIME_STACK" --output "cdk.out.$TIMESTAMP" --no-cli-pager --require-approval never
cd ..

# Build and deploy frontend
echo -e "\nBuilding and deploying frontend..."
AGENT_RUNTIME_ARN=$(aws cloudformation describe-stacks --stack-name "$RUNTIME_STACK" --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager)
REGION=$(aws cloudformation describe-stacks --stack-name "$RUNTIME_STACK" --query "Stacks[0].Outputs[?OutputKey=='Region'].OutputValue" --output text --no-cli-pager)
IDENTITY_POOL_ID=$(aws cloudformation describe-stacks --stack-name "$AUTH_STACK" --query "Stacks[0].Outputs[?OutputKey=='IdentityPoolId'].OutputValue" --output text --no-cli-pager)
UNAUTH_ROLE_ARN=$(aws cloudformation describe-stacks --stack-name "$AUTH_STACK" --query "Stacks[0].Outputs[?OutputKey=='UnauthenticatedRoleArn'].OutputValue" --output text --no-cli-pager)

echo "Agent Runtime ARN: $AGENT_RUNTIME_ARN"
echo "Region: $REGION"
echo "Identity Pool ID: $IDENTITY_POOL_ID"
echo "Unauth Role ARN: $UNAUTH_ROLE_ARN"

# Build frontend with basic auth flow (bypasses session policy restrictions)
chmod +x scripts/build-frontend.sh
./scripts/build-frontend.sh "$AGENT_RUNTIME_ARN" "$REGION" "$IDENTITY_POOL_ID" "$UNAUTH_ROLE_ARN"

# Deploy frontend stack
cd cdk
TIMESTAMP=$(date +%Y%m%d%H%M%S)
npx cdk deploy "$FRONTEND_STACK" --output "cdk.out.$TIMESTAMP" --no-cli-pager --require-approval never
cd ..

# Get outputs
WEBSITE_URL=$(aws cloudformation describe-stacks --stack-name "$FRONTEND_STACK" --query "Stacks[0].Outputs[?OutputKey=='WebsiteUrl'].OutputValue" --output text --no-cli-pager)
USER_POOL_ID=$(aws cloudformation describe-stacks --stack-name "$AUTH_STACK" --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text --no-cli-pager)

echo -e "\n=== Deployment Complete ==="
echo "Website URL: $WEBSITE_URL"
echo "Agent Runtime ARN: $AGENT_RUNTIME_ARN"
echo "User Pool ID: $USER_POOL_ID"
echo ""
echo "NOTE: This chatbot allows ANONYMOUS access (no login required)"
echo "Users can reset passwords for accounts in the Cognito User Pool"
echo ""
echo "To test the password reset flow:"
echo "1. Create a test user with an email address you can access:"
echo "   aws cognito-idp admin-create-user --user-pool-id $USER_POOL_ID --username your.email@example.com --user-attributes Name=email,Value=your.email@example.com --message-action SUPPRESS"
echo ""
echo "2. Set a permanent password (REQUIRED - users with FORCE_CHANGE_PASSWORD status cannot receive reset emails):"
echo "   aws cognito-idp admin-set-user-password --user-pool-id $USER_POOL_ID --username your.email@example.com --password TempPass123! --permanent"
echo ""
echo "3. Open the website and initiate a password reset"
echo ""
echo "CRITICAL: Step 2 is mandatory. Cognito will not send password reset emails to users in FORCE_CHANGE_PASSWORD status."
