#!/bin/bash
# AI Lambda Runtime Migration Assistant - Complete Deployment Script
# macOS/Linux version - mirrors deploy-all.ps1
#
# Stacks (in order): Data -> Auth -> Pipeline -> Api -> Transform -> Frontend
# The Transform stack is the migration build plane (CodeBuild running AWS
# Transform); its buildspec is uploaded to the artifacts bucket right after it
# deploys, so Migrate works as soon as the script finishes.
# No Docker needed: the Lambda bundle is built locally with pip by the
# Pipeline stack (pure-Python dependencies resolved as Linux/arm64 wheels).
#
# Default (single-account): discovers and assesses Lambda functions in the
# account you deploy into. The hub pipeline ALWAYS scans whatever AWS
# Organization it can read at runtime — no deploy flag is needed for that, and
# no flag can make the scan drift out of sync after a redeploy. A standalone
# account simply scans itself.
# --multi-account: additionally rolls the read-only spoke role out to every
#   member account of an AWS Organization, so the hub can also scan them. Runs
#   shared/scripts/check-org-access.sh first (read-only preflight). When the hub
#   may run service-managed StackSets, the Org stack performs the rollout;
#   otherwise the one command a management-account admin has to run is printed.
#   The pipeline stack itself is deployed identically with or without this flag.
# --org-targets <ids>: comma-separated organization root (r-xxxx) and/or OU ids
#   to roll the spoke role out to. Default: the whole organization (its root).
# --mock: also deploys the optional mock fixture (LambdaRuntimeMigrationMock-<region>):
#   a CodeCommit repository and three deprecated-runtime Lambda functions tagged
#   so Migrate works with the default Settings. Remove it later with
#   scripts/deploy-mock.sh --destroy.

set -e  # Exit on error

MULTI_ACCOUNT=false
ORG_TARGETS=""
MOCK=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --multi-account) MULTI_ACCOUNT=true; shift ;;
        --org-targets) ORG_TARGETS="$2"; shift 2 ;;
        --mock) MOCK=true; shift ;;
        *) echo "Unknown option: $1"; echo "Usage: $0 [--multi-account] [--org-targets r-xxxx,ou-xxxx-yyyyyyyy] [--mock]"; exit 1 ;;
    esac
done

echo -e "\033[0;36m=== AI Lambda Runtime Migration Assistant Deployment ===\033[0m"

# Run shared prerequisites check (transform service: points at the AWS Transform
# region reference; the demo picks the Transform region at run time).
echo -e "\n\033[0;33mRunning prerequisites check...\033[0m"
source ../../shared/scripts/check-prerequisites.sh --required-service "transform" --min-aws-cli-version "2.31.13" --min-python-version "3.12" --require-cdk --min-cdk-version "2.1030.0"

# Install frontend dependencies
echo -e "\n\033[0;33mInstalling frontend dependencies...\033[0m"
echo -e "\033[0;90m      (Installing React, Vite, Cognito SDK, and UI component libraries)\033[0m"
pushd frontend > /dev/null
npm install
popd > /dev/null

# Create placeholder dist BEFORE any CDK commands
echo -e "\n\033[0;33mCreating placeholder frontend build...\033[0m"
echo -e "\033[0;90m      (Generating temporary HTML file - required for CDK synthesis)\033[0m"
if [ ! -d "frontend/dist" ]; then
    mkdir -p frontend/dist
    echo "<!DOCTYPE html><html><body><h1>Building...</h1></body></html>" > frontend/dist/index.html
else
    echo -e "\033[0;90m      Placeholder already exists, skipping...\033[0m"
fi

# Get region for stack names (set by check-prerequisites.sh)
region="$AWS_REGION"

# Set stack names with region suffix
data_stack="LambdaRuntimeMigrationData-$region"
auth_stack="LambdaRuntimeMigrationAuth-$region"
pipeline_stack="LambdaRuntimeMigrationPipeline-$region"
api_stack="LambdaRuntimeMigrationApi-$region"
frontend_stack="LambdaRuntimeMigrationFrontend-$region"
transform_stack="LambdaRuntimeMigrationTransform-$region"
mock_stack="LambdaRuntimeMigrationMock-$region"

# Multi-account preflight (read-only; exit codes documented in check-org-access.sh).
# This only drives the OPTIONAL Org/Spoke StackSet rollout below — the pipeline
# stack is deployed identically whether or not --multi-account is passed, because
# the pipeline decides multi-account vs single-account at runtime.
ORG_ROLLOUT=false
if [ "$MULTI_ACCOUNT" = true ]; then
    echo -e "\n\033[0;33mChecking multi-account (organization) access...\033[0m"
    set +e
    ../../shared/scripts/check-org-access.sh
    org_check=$?
    set -e
    if [ $org_check -eq 2 ]; then
        echo -e "\033[0;31mThis account cannot list the organization's accounts, so --multi-account is not possible from here.\033[0m"
        echo -e "\033[0;33mApply the fix printed above, or deploy without --multi-account to scan just this account.\033[0m"
        exit 1
    elif [ $org_check -ne 0 ] && [ $org_check -ne 3 ]; then
        echo -e "\033[0;31mMulti-account preflight failed\033[0m"
        exit 1
    fi
    [ $org_check -eq 0 ] && ORG_ROLLOUT=true
    if [ -z "$ORG_TARGETS" ]; then
        ORG_TARGETS=$(aws organizations list-roots --query "Roots[0].Id" --output text --no-cli-pager)
    fi
    echo -e "\033[0;90m      Spoke role rollout enabled (member accounts the hub will be able to scan)\033[0m"
    if [ "$ORG_ROLLOUT" = true ]; then
        echo -e "\033[0;90m      Scan targets: $ORG_TARGETS (spoke role rolled out from this account)\033[0m"
    else
        echo -e "\033[0;90m      Scan targets: $ORG_TARGETS (spoke role rollout must be run by the management account)\033[0m"
    fi
fi

# Deploy data stack
echo -e "\n\033[0;33mDeploying data stack...\033[0m"
echo -e "\033[0;90m      (Creating the DynamoDB inventory table and S3 bucket for migration artifacts)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "$data_stack"

# Deploy auth stack
echo -e "\n\033[0;33mDeploying authentication stack...\033[0m"
echo -e "\033[0;90m      (Creating the Cognito User Pool and app client)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "$auth_stack" --skip-bootstrap

# Deploy pipeline stack (durable discover+assess function + API function)
echo -e "\n\033[0;33mDeploying pipeline stack...\033[0m"
echo -e "\033[0;90m      (Bundling Python code with pip, creating the Lambda durable function and API function)\033[0m"
# Deployed identically with or without --multi-account: the pipeline role always
# carries the org-read + spoke-assume grants and decides scope at runtime.
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "$pipeline_stack" --skip-bootstrap

# Multi-account: roll the read-only spoke role out to every member account via a
# service-managed StackSet (the Org stack). Only when this account may run them;
# otherwise the ACTION REQUIRED command is printed in the summary below.
if [ "$MULTI_ACCOUNT" = true ] && [ "$ORG_ROLLOUT" = true ]; then
    hub_account=$(aws sts get-caller-identity --query Account --output text --no-cli-pager)
    org_stack="LambdaRuntimeMigrationOrg-$region"
    echo -e "\n\033[0;33mDeploying org stack (spoke role StackSet)...\033[0m"
    echo -e "\033[0;90m      (Service-managed StackSet placing the read-only LambdaRuntimeMigrationScanRole in every account under $ORG_TARGETS)\033[0m"
    ../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "$org_stack" --skip-bootstrap --cdk-context "orgTargets=$ORG_TARGETS" --cdk-context "hubAccountId=$hub_account"
fi

# Deploy API stack (HTTP API + Cognito JWT authorizer)
echo -e "\n\033[0;33mDeploying API stack...\033[0m"
echo -e "\033[0;90m      (Creating the HTTP API with Cognito JWT authorization in front of the API function)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "$api_stack" --skip-bootstrap

# Deploy Transform stack (migration build plane). It imports Pipeline and Data
# exports, so it goes after them.
echo -e "\n\033[0;33mDeploying Transform stack...\033[0m"
echo -e "\033[0;90m      (Creating the CodeBuild project running AWS Transform, its build role and EventBridge rule)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "$transform_stack" --skip-bootstrap

# Read the artifacts bucket name from the Data stack output, then upload the
# buildspec the CodeBuild project reads from config/.
bucket=$(aws cloudformation describe-stacks --stack-name "$data_stack" --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text --no-cli-pager)
transform_project_name=$(aws cloudformation describe-stacks --stack-name "$transform_stack" --query "Stacks[0].Outputs[?OutputKey=='TransformProjectName'].OutputValue" --output text --no-cli-pager)
if [ -z "$bucket" ] || [ "$bucket" == "None" ]; then
    echo -e "\033[0;31mFailed to get the artifacts bucket name from stack outputs\033[0m"
    exit 1
fi
if [ -z "$transform_project_name" ] || [ "$transform_project_name" == "None" ]; then
    echo -e "\033[0;31mFailed to get the AWS Transform CodeBuild project name from stack outputs\033[0m"
    exit 1
fi

echo -e "\n\033[0;33mUploading the migration buildspec to the artifacts bucket...\033[0m"
# Object MUST be named buildspec.yml: CodeBuild's S3 single-file source looks for
# a file named buildspec.yml in the downloaded source root.
aws s3 cp cdk/buildspec/transform-migrate.buildspec.yml "s3://$bucket/config/buildspec.yml" --no-cli-pager

# Build frontend with API URL and Cognito config (after backend is complete)
echo -e "\n\033[0;33mBuilding frontend with stack outputs...\033[0m"
echo -e "\033[0;90m      (Retrieving API URL and Cognito config, building the React app)\033[0m"
api_url=$(aws cloudformation describe-stacks --stack-name "$api_stack" --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text --no-cli-pager)
user_pool_id=$(aws cloudformation describe-stacks --stack-name "$auth_stack" --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text --no-cli-pager)
user_pool_client_id=$(aws cloudformation describe-stacks --stack-name "$auth_stack" --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" --output text --no-cli-pager)

if [ -z "$api_url" ] || [ "$api_url" == "None" ]; then
    echo -e "\033[0;31mFailed to get API URL from stack outputs\033[0m"
    exit 1
fi

if [ -z "$user_pool_id" ] || [ "$user_pool_id" == "None" ] || [ -z "$user_pool_client_id" ] || [ "$user_pool_client_id" == "None" ]; then
    echo -e "\033[0;31mFailed to get Cognito config from stack outputs\033[0m"
    exit 1
fi

echo -e "\033[0;32mAPI URL: $api_url\033[0m"
echo -e "\033[0;32mUser Pool ID: $user_pool_id\033[0m"
echo -e "\033[0;32mUser Pool Client ID: $user_pool_client_id\033[0m"

./scripts/build-frontend.sh "$api_url" "$user_pool_id" "$user_pool_client_id"

# Deploy frontend stack (CloudFront + S3)
echo -e "\n\033[0;33mDeploying frontend stack...\033[0m"
echo -e "\033[0;90m      (Deploying the built React app to S3 behind CloudFront)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "$frontend_stack" --skip-bootstrap

# Optional mock fixture (--mock): CodeCommit repo + three deprecated-runtime
# Lambda functions, gated behind the mockFixture CDK context.
if [ "$MOCK" = true ]; then
    echo -e "\n\033[0;33mDeploying mock fixture stack...\033[0m"
    echo -e "\033[0;90m      (Creating the CodeCommit repo and the three deprecated-runtime Lambda functions)\033[0m"
    ../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "$mock_stack" --skip-bootstrap --cdk-context "mockFixture=true"

    repo_url=$(aws cloudformation describe-stacks --stack-name "$mock_stack" --query "Stacks[0].Outputs[?OutputKey=='FixtureRepoCloneUrlHttp'].OutputValue" --output text --no-cli-pager)
    python_fn=$(aws cloudformation describe-stacks --stack-name "$mock_stack" --query "Stacks[0].Outputs[?OutputKey=='MockPythonFunctionName'].OutputValue" --output text --no-cli-pager)
    nodejs_fn=$(aws cloudformation describe-stacks --stack-name "$mock_stack" --query "Stacks[0].Outputs[?OutputKey=='MockNodejsFunctionName'].OutputValue" --output text --no-cli-pager)
    dotnet_fn=$(aws cloudformation describe-stacks --stack-name "$mock_stack" --query "Stacks[0].Outputs[?OutputKey=='MockDotnetFunctionName'].OutputValue" --output text --no-cli-pager)
fi

# Gather outputs
website_url=$(aws cloudformation describe-stacks --stack-name "$frontend_stack" --query "Stacks[0].Outputs[?OutputKey=='WebsiteUrl'].OutputValue" --output text --no-cli-pager)

echo -e "\n\033[0;32m========================================\033[0m"
echo -e "\033[0;32m  Deployment Complete!\033[0m"
echo -e "\033[0;32m========================================\033[0m"
echo -e "\033[0;36m  Dashboard URL:   $website_url\033[0m"
echo -e "\033[0;36m  API URL:         $api_url\033[0m"
echo -e "\033[0;36m  Region:          $region\033[0m"
echo -e "\033[0;36m  User Pool ID:    $user_pool_id\033[0m"
echo -e "\033[0;36m  CodeBuild project (AWS Transform): $transform_project_name\033[0m"
if [ "$MOCK" = true ]; then
    echo -e "\n\033[0;33mMock fixture:\033[0m"
    echo -e "\033[0;36m  CodeCommit repo URL: $repo_url\033[0m"
    echo -e "\033[0;36m  Functions:\033[0m"
    echo -e "\033[0;36m    - $python_fn  (python3.9)\033[0m"
    echo -e "\033[0;36m    - $nodejs_fn  (nodejs16.x)\033[0m"
    echo -e "\033[0;36m    - $dotnet_fn  (dotnet6)\033[0m"
    echo -e "\033[0;90m  Trusted Advisor's deprecated-runtime detection has a lag, so these functions\033[0m"
    echo -e "\033[0;90m  may take time (often hours) to appear in the inventory after the next Discover.\033[0m"
    echo -e "\033[0;90m  Remove the fixture with: ./scripts/deploy-mock.sh --destroy\033[0m"
fi
echo -e "\n\033[0;33mNext Steps:\033[0m"
echo -e "\033[0;90m  1. Create a dashboard user (copy-paste these two commands):\033[0m"
echo ""
echo -e "     aws cognito-idp admin-create-user --user-pool-id $user_pool_id --username admin@example.com --user-attributes Name=email,Value=admin@example.com Name=email_verified,Value=true --message-action SUPPRESS --no-cli-pager"
echo ""
echo -e "     aws cognito-idp admin-set-user-password --user-pool-id $user_pool_id --username admin@example.com --password \"YourSecurePassword123!\" --permanent --no-cli-pager"
echo ""
echo -e "\033[0;90m     (The username is the email address; replace it and the password with your own values)\033[0m"
echo -e "\033[0;90m  2. Sign in at the Dashboard URL above and click Discover to run the first discover + assess pipeline\033[0m"
echo -e "\033[0;90m     The hub always scans whatever organization it can read at runtime; a standalone account scans itself.\033[0m"
echo -e "\033[0;90m  3. Migrate is available right away: select functions and click Migrate to run AWS Transform in CodeBuild\033[0m"
echo -e "\033[0;90m     and push an upgrade branch to each function's repository (tens of minutes per function).\033[0m"
if [ "$MULTI_ACCOUNT" = true ]; then
    hub_account=$(aws sts get-caller-identity --query Account --output text --no-cli-pager)
    echo -e "\n\033[0;33mMulti-account:\033[0m"
    echo -e "\033[0;90m  Spoke role rolled out so hub account $hub_account can also scan every active account under $ORG_TARGETS\033[0m"
    if [ "$ORG_ROLLOUT" = true ]; then
        echo -e "\033[0;90m  Spoke role: StackSet ai-lambda-runtime-migration-spoke (auto-deploys to accounts joining later)\033[0m"
        echo -e "     aws cloudformation list-stack-instances --stack-set-name ai-lambda-runtime-migration-spoke --region $region"
    else
        echo -e "\033[0;33m  ACTION REQUIRED: this account cannot run service-managed StackSets. From the MANAGEMENT account, run once:\033[0m"
        echo -e "     cd cdk && npx cdk deploy LambdaRuntimeMigrationOrg-$region --context orgTargets=$ORG_TARGETS --context hubAccountId=$hub_account --require-approval never"
        echo -e "\033[0;90m     (or register this account as a StackSets delegated administrator, see the preflight output above, and re-run with --multi-account)\033[0m"
        echo -e "\033[0;90m  Until then, member accounts are reported as failed in each scan; the hub itself is scanned normally.\033[0m"
    fi
fi
