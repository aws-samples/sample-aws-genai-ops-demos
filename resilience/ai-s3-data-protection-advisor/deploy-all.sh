#!/bin/bash
# S3 data protection advisor - complete deployment script
# macOS/Linux version - mirrors deploy-all.ps1
#
# Stacks (in order): Data -> Auth -> Pipeline -> Api -> Frontend
# The scan is read-only. Options:
#   --admin-email <email>  create the first dashboard user; Cognito emails a temporary password
#   --mock-data            create the sandbox-only mock estate (tagged buckets and a role) to scan.
#                          Sandbox accounts only: remove it with ./destroy-all.sh --mock-data

set -e

MOCK_DATA=false
ADMIN_EMAIL=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --mock-data) MOCK_DATA=true; shift ;;
        --admin-email) ADMIN_EMAIL="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; echo "Usage: $0 [--mock-data] [--admin-email <email>]"; exit 1 ;;
    esac
done

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "\033[0;36m=== S3 data protection advisor deployment ===\033[0m"

echo -e "\n\033[0;33mRunning prerequisites check...\033[0m"
source ../../shared/scripts/check-prerequisites.sh --required-service "bedrock" --min-python-version "3.12" --require-cdk --min-cdk-version "2.1030.0"

region="$AWS_REGION"
data_stack="S3DataProtectionData-$region"
auth_stack="S3DataProtectionAuth-$region"
pipeline_stack="S3DataProtectionPipeline-$region"
api_stack="S3DataProtectionApi-$region"
frontend_stack="S3DataProtectionFrontend-$region"

stack_output() {
    aws cloudformation describe-stacks --stack-name "$1" --region "$region" \
        --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text --no-cli-pager
}

echo -e "\n\033[0;33mInstalling CDK dependencies...\033[0m"
(cd cdk && npm install)

# CDK synthesizes every stack, so frontend/dist must exist before the first deploy.
echo -e "\n\033[0;33mPreparing the dashboard build...\033[0m"
if [ -f frontend/package.json ]; then
    (cd frontend && npm install && npm run build)
else
    echo -e "\033[0;90m      (No dashboard sources yet, writing a placeholder page)\033[0m"
    mkdir -p frontend/dist
    echo "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>S3 data protection advisor</title></head><body><p>S3 data protection advisor: the dashboard is not built yet.</p></body></html>" > frontend/dist/index.html
fi

echo -e "\n\033[0;33mDeploying data stack...\033[0m"
echo -e "\033[0;90m      (Reports bucket and runs table)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "$data_stack"

echo -e "\n\033[0;33mDeploying authentication stack...\033[0m"
echo -e "\033[0;90m      (Cognito user pool and app client)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "$auth_stack" --skip-bootstrap

echo -e "\n\033[0;33mDeploying pipeline stack...\033[0m"
echo -e "\033[0;90m      (Durable scan function with a read-only role)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "$pipeline_stack" --skip-bootstrap

echo -e "\n\033[0;33mDeploying API stack...\033[0m"
echo -e "\033[0;90m      (HTTP API with a Cognito JWT authorizer)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "$api_stack" --skip-bootstrap

echo -e "\n\033[0;33mDeploying frontend stack...\033[0m"
echo -e "\033[0;90m      (S3 and CloudFront, with /api forwarded to the HTTP API)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "$frontend_stack" --skip-bootstrap

website_url=$(stack_output "$frontend_stack" WebsiteUrl)
api_url=$(stack_output "$api_stack" ApiUrl)
user_pool_id=$(stack_output "$auth_stack" UserPoolId)
if [ -z "$user_pool_id" ] || [ "$user_pool_id" == "None" ]; then
    echo -e "\033[0;31mFailed to get the user pool id from stack outputs\033[0m"
    exit 1
fi

if [ -n "$ADMIN_EMAIL" ]; then
    echo -e "\n\033[0;33mCreating the dashboard user $ADMIN_EMAIL...\033[0m"
    if aws cognito-idp admin-create-user --user-pool-id "$user_pool_id" --username "$ADMIN_EMAIL" \
        --user-attributes Name=email,Value="$ADMIN_EMAIL" Name=email_verified,Value=true \
        --desired-delivery-mediums EMAIL --region "$region" --no-cli-pager > /dev/null 2>&1; then
        echo -e "\033[0;90m      Cognito emailed a temporary password; the first sign-in asks for a new one.\033[0m"
    else
        echo -e "\033[0;33m      Note: the user was not created (it may already exist). Continuing.\033[0m"
    fi
fi

if [ "$MOCK_DATA" = true ]; then
    echo -e "\n\033[0;33mCreating the mock estate...\033[0m"
    echo -e "\033[0;90m      (Sandbox accounts only: tagged buckets and a role, removed by destroy-all.sh --mock-data)\033[0m"
    if [ ! -d backend/.venv ]; then
        python3 -m venv backend/.venv
    fi
    backend/.venv/bin/python -m pip install --quiet -r backend/requirements.txt
    (cd backend && ../backend/.venv/bin/python -m s3dp.cli seed --yes)
fi

echo -e "\n\033[0;32m========================================\033[0m"
echo -e "\033[0;32m  Deployment complete\033[0m"
echo -e "\033[0;32m========================================\033[0m"
echo -e "\033[0;36m  Dashboard URL:   $website_url\033[0m"
echo -e "\033[0;36m  API URL:         $api_url\033[0m"
echo -e "\033[0;36m  Region:          $region\033[0m"
echo -e "\033[0;36m  User pool ID:    $user_pool_id\033[0m"
echo -e "\n\033[0;33mNext steps:\033[0m"
if [ -z "$ADMIN_EMAIL" ]; then
    echo -e "\033[0;90m  1. Create a dashboard user. Cognito emails a temporary password:\033[0m"
    echo ""
    echo "     aws cognito-idp admin-create-user --user-pool-id $user_pool_id --username <email> --user-attributes Name=email,Value=<email> Name=email_verified,Value=true --desired-delivery-mediums EMAIL --region $region --no-cli-pager"
    echo ""
else
    echo -e "\033[0;90m  1. Sign in as $ADMIN_EMAIL with the temporary password from the email, then choose a new password.\033[0m"
fi
echo -e "\033[0;90m  2. Open the dashboard URL above and start a scan.\033[0m"
echo -e "\033[0;90m  3. Verify the deployment end to end: python3 scripts/smoke_test.py --region $region\033[0m"
