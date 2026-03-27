#!/bin/bash
# AI Lambda Runtime Migration Assistant - Deployment Script
#
# Usage: ./deploy-all.sh

set -e

echo "=== AI Lambda Runtime Migration Assistant ==="

# Check prerequisites using shared script
source ../../shared/scripts/check-prerequisites.sh --required-service agentcore --require-cdk

region=$AWS_REGION

# Set stack names with region suffix
data_stack="LambdaRuntimeMigrationData-$region"
auth_stack="LambdaRuntimeMigrationAuth-$region"
runtime_stack="LambdaRuntimeMigrationRuntime-$region"
frontend_stack="LambdaRuntimeMigrationFrontend-$region"

# Step 1: Install CDK dependencies
echo ""
echo "[1/10] Installing CDK dependencies..."
if [ ! -d "cdk/node_modules" ]; then
    cd cdk
    npm install
    cd ..
else
    echo "       CDK dependencies already installed"
fi

# Step 2: Install frontend dependencies
echo ""
echo "[2/10] Installing frontend dependencies..."
cd frontend
npm install
cd ..

# Step 3: Create placeholder frontend build
echo ""
echo "[3/10] Creating placeholder frontend build..."
if [ ! -d "frontend/dist" ]; then
    mkdir -p frontend/dist
    echo '<!DOCTYPE html><html><body><h1>Building...</h1></body></html>' > frontend/dist/index.html
fi

# Step 4: Package 3 agent zips
echo ""
echo "[4/10] Packaging agent code (3 agents)..."
bash ./scripts/package-agent.sh

# Step 5: Bootstrap CDK (skip if already bootstrapped)
# Step 5: Bootstrap CDK
echo ""
echo "[5/10] Bootstrapping CDK environment..."
timestamp=$(date +%Y%m%d%H%M%S)
cd cdk
npx cdk bootstrap --output "cdk.out.$timestamp" --no-cli-pager
cd ..

# Step 6: Deploy Data stack (DynamoDB + S3)
echo ""
echo "[6/10] Deploying Data stack (DynamoDB + S3)..."
timestamp=$(date +%Y%m%d%H%M%S)
cd cdk
npx cdk deploy "$data_stack" --output "cdk.out.$timestamp" --no-cli-pager --require-approval never
cd ..

# Upload 3 agent zips to S3
echo ""
echo "Uploading agent zips to S3..."
bucket_name=$(aws cloudformation describe-stacks --stack-name "$data_stack" --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text --no-cli-pager)
agents=("discover" "analyze" "transform")
for agent in "${agents[@]}"; do
    aws s3 cp "agent/$agent/deployment_package.zip" "s3://$bucket_name/agent/$agent/deployment_package.zip" --no-cli-pager
    echo "       Uploaded $agent agent zip"
done

# Step 7: Deploy Auth stack (Cognito)
echo ""
echo "[7/10] Deploying Auth stack (Cognito)..."
timestamp=$(date +%Y%m%d%H%M%S)
cd cdk
npx cdk deploy "$auth_stack" --output "cdk.out.$timestamp" --no-cli-pager --require-approval never
cd ..

# Step 8: Deploy Runtime stack (3 AgentCore Runtimes)
echo ""
echo "[8/10] Deploying Runtime stack (3 AgentCore Runtimes)..."
timestamp=$(date +%Y%m%d%H%M%S)
cd cdk
npx cdk deploy "$runtime_stack" --output "cdk.out.$timestamp" --no-cli-pager --require-approval never
cd ..

# Step 9: Build frontend with stack outputs
echo ""
echo "[9/10] Building frontend with stack outputs..."
user_pool_id=$(aws cloudformation describe-stacks --stack-name "$auth_stack" --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text --no-cli-pager)
user_pool_client_id=$(aws cloudformation describe-stacks --stack-name "$auth_stack" --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" --output text --no-cli-pager)
identity_pool_id=$(aws cloudformation describe-stacks --stack-name "$auth_stack" --query "Stacks[0].Outputs[?OutputKey=='IdentityPoolId'].OutputValue" --output text --no-cli-pager)
discover_arn=$(aws cloudformation describe-stacks --stack-name "$runtime_stack" --query "Stacks[0].Outputs[?OutputKey=='DiscoverRuntimeArn'].OutputValue" --output text --no-cli-pager)
analyze_arn=$(aws cloudformation describe-stacks --stack-name "$runtime_stack" --query "Stacks[0].Outputs[?OutputKey=='AnalyzeRuntimeArn'].OutputValue" --output text --no-cli-pager)
transform_arn=$(aws cloudformation describe-stacks --stack-name "$runtime_stack" --query "Stacks[0].Outputs[?OutputKey=='TransformRuntimeArn'].OutputValue" --output text --no-cli-pager)

bash ./scripts/build-frontend.sh "$region" "$user_pool_id" "$user_pool_client_id" "$identity_pool_id" "$discover_arn" "$analyze_arn" "$transform_arn"

# Step 10: Deploy Frontend stack (CloudFront)
echo ""
echo "[10/10] Deploying Frontend stack (CloudFront)..."
timestamp=$(date +%Y%m%d%H%M%S)
cd cdk
npx cdk deploy "$frontend_stack" --output "cdk.out.$timestamp" --no-cli-pager --require-approval never
cd ..

# Retrieve stack outputs
website_url=$(aws cloudformation describe-stacks --stack-name "$frontend_stack" --query "Stacks[0].Outputs[?OutputKey=='WebsiteUrl'].OutputValue" --output text --no-cli-pager)
bucket_name=$(aws cloudformation describe-stacks --stack-name "$data_stack" --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text --no-cli-pager)
table_name=$(aws cloudformation describe-stacks --stack-name "$data_stack" --query "Stacks[0].Outputs[?OutputKey=='TableName'].OutputValue" --output text --no-cli-pager)

echo ""
echo "========================================"
echo "  Deployment Complete!"
echo "========================================"
echo ""
echo "  Dashboard URL:   $website_url"
echo "  S3 Bucket:       $bucket_name"
echo "  DynamoDB Table:  $table_name"
echo "  Region:          $region"
echo ""
echo "  To create a dashboard user:"
echo "  aws cognito-idp admin-create-user --user-pool-id $user_pool_id --username your.email@example.com --user-attributes Name=email,Value=your.email@example.com --message-action SUPPRESS --no-cli-pager"
echo ""
echo "  Then set a permanent password:"
echo "  aws cognito-idp admin-set-user-password --user-pool-id $user_pool_id --username your.email@example.com --password YourPass123! --permanent --no-cli-pager"
echo ""
