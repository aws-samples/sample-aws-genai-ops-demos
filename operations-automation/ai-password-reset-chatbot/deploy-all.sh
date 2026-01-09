#!/bin/bash
set -e

echo "=== Password Reset Chatbot Deployment ==="

# Step 1: Verify AWS credentials
echo -e "\n[1/10] Verifying AWS credentials..."
AWS_CHECK=$(aws sts get-caller-identity 2>/dev/null || echo "FAILED")
if [[ "$AWS_CHECK" == "FAILED" ]]; then
    echo "AWS credentials are not configured. Run: aws configure"
    exit 1
fi
echo "      Authenticated successfully"

# Step 2: Check AWS CLI version
echo -e "\n[2/10] Checking AWS CLI version..."
AWS_VERSION=$(aws --version 2>&1 | grep -oP 'aws-cli/\K[0-9]+\.[0-9]+\.[0-9]+' || echo "0.0.0")
echo "      AWS CLI version: $AWS_VERSION"

# Step 3: Check AgentCore availability
echo -e "\n[3/10] Checking AgentCore availability..."
REGION=$(aws configure get region)
if [ -z "$REGION" ]; then
    echo "      No AWS region configured. Run: aws configure set region <region>"
    exit 1
fi
echo "      Target region: $REGION"
AGENTCORE_CHECK=$(aws bedrock-agentcore-control list-agent-runtimes --region $REGION --max-results 1 2>/dev/null || echo "FAILED")
if [[ "$AGENTCORE_CHECK" == "FAILED" ]]; then
    echo "      AgentCore is not available in region: $REGION"
    exit 1
fi
echo "      AgentCore is available"

# Step 4: Install CDK dependencies
echo -e "\n[4/10] Installing CDK dependencies..."
if [ ! -d "cdk/node_modules" ]; then
    cd cdk && npm install && cd ..
else
    echo "      CDK dependencies already installed"
fi

# Step 5: Install frontend dependencies
echo -e "\n[5/10] Installing frontend dependencies..."
cd frontend && npm install && cd ..

# Step 6: Create placeholder frontend build
echo -e "\n[6/10] Creating placeholder frontend build..."
mkdir -p frontend/dist
echo '<!DOCTYPE html><html><body><h1>Building...</h1></body></html>' > frontend/dist/index.html


# Step 7: Bootstrap CDK
echo -e "\n[7/10] Bootstrapping CDK environment..."
cd cdk
TIMESTAMP=$(date +%Y%m%d%H%M%S)
npx cdk bootstrap --output "cdk.out.$TIMESTAMP" --no-cli-pager
cd ..

# Step 8: Deploy infrastructure stack
echo -e "\n[8/10] Deploying infrastructure stack..."
cd cdk
TIMESTAMP=$(date +%Y%m%d%H%M%S)
npx cdk deploy PasswordResetInfra --output "cdk.out.$TIMESTAMP" --no-cli-pager --require-approval never
cd ..

# Step 9: Deploy auth stack
echo -e "\n[9/10] Deploying authentication stack (Cognito User Pool)..."
cd cdk
TIMESTAMP=$(date +%Y%m%d%H%M%S)
npx cdk deploy PasswordResetAuth --output "cdk.out.$TIMESTAMP" --no-cli-pager --require-approval never
cd ..

# Step 10: Deploy runtime stack
echo -e "\n[10/10] Deploying AgentCore runtime (anonymous access)..."
echo "      Note: CodeBuild will compile the container - this takes 5-10 minutes"
cd cdk
TIMESTAMP=$(date +%Y%m%d%H%M%S)
npx cdk deploy PasswordResetRuntime --output "cdk.out.$TIMESTAMP" --no-cli-pager --require-approval never
cd ..

# Build and deploy frontend
echo -e "\nBuilding and deploying frontend..."
AGENT_RUNTIME_ARN=$(aws cloudformation describe-stacks --stack-name PasswordResetRuntime --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager)
REGION=$(aws cloudformation describe-stacks --stack-name PasswordResetRuntime --query "Stacks[0].Outputs[?OutputKey=='Region'].OutputValue" --output text --no-cli-pager)
IDENTITY_POOL_ID=$(aws cloudformation describe-stacks --stack-name PasswordResetAuth --query "Stacks[0].Outputs[?OutputKey=='IdentityPoolId'].OutputValue" --output text --no-cli-pager)
UNAUTH_ROLE_ARN=$(aws cloudformation describe-stacks --stack-name PasswordResetAuth --query "Stacks[0].Outputs[?OutputKey=='UnauthenticatedRoleArn'].OutputValue" --output text --no-cli-pager)

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
npx cdk deploy PasswordResetFrontend --output "cdk.out.$TIMESTAMP" --no-cli-pager --require-approval never
cd ..

# Get outputs
WEBSITE_URL=$(aws cloudformation describe-stacks --stack-name PasswordResetFrontend --query "Stacks[0].Outputs[?OutputKey=='WebsiteUrl'].OutputValue" --output text --no-cli-pager)
USER_POOL_ID=$(aws cloudformation describe-stacks --stack-name PasswordResetAuth --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text --no-cli-pager)

echo -e "\n=== Deployment Complete ==="
echo "Website URL: $WEBSITE_URL"
echo "Agent Runtime ARN: $AGENT_RUNTIME_ARN"
echo "User Pool ID: $USER_POOL_ID"
echo ""
echo "NOTE: This chatbot allows ANONYMOUS access (no login required)"
echo "Users can reset passwords for accounts in the Cognito User Pool"
echo ""
echo "To test, create a user in Cognito first:"
echo "  aws cognito-idp admin-create-user --user-pool-id $USER_POOL_ID --username test@example.com --temporary-password TempPass1!"
