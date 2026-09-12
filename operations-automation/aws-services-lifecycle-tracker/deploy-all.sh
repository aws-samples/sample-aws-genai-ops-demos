#!/bin/bash
# AWS Services Lifecycle Tracker - Complete Deployment Script
# macOS/Linux version - mirrors deploy-all.ps1
#
# Stacks (in order): Data -> Auth -> Pipeline -> [Org] -> Api -> Frontend
# No Docker needed: the Lambda bundle is built locally with pip (pure-Python
# dependencies resolved as Linux/arm64 wheels).
#
# Default: scans the account you deploy into (single-account).
# --multi-account: hub-and-spoke scan of an AWS Organization from this account
#   (the "hub"). Runs shared/scripts/check-org-access.sh first; when the hub may
#   run service-managed StackSets, the Org stack rolls the read-only spoke role
#   out to every member account. Otherwise the hub stacks still deploy and the
#   one command a management-account admin has to run is printed.
# --org-targets <ids>: comma-separated organization root (r-xxxx) and/or OU ids
#   to scan and roll out to. Default: the whole organization (its root).

set -e  # Exit on error

MULTI_ACCOUNT=false
ORG_TARGETS=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --multi-account) MULTI_ACCOUNT=true; shift ;;
        --org-targets) ORG_TARGETS="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; echo "Usage: $0 [--multi-account] [--org-targets r-xxxx,ou-xxxx-yyyyyyyy]"; exit 1 ;;
    esac
done

echo -e "\033[0;36m=== AWS Services Lifecycle Tracker Deployment ===\033[0m"

# Run shared prerequisites check
echo -e "\n\033[0;33mRunning prerequisites check...\033[0m"
../../shared/scripts/check-prerequisites.sh --required-service "bedrock" --min-aws-cli-version "2.33.22" --min-python-version "3.11" --require-cdk --min-cdk-version "2.1030.0"

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

# Get region for stack names. Same precedence as shared/scripts/deploy-cdk.sh
# (AWS_REGION -> AWS_DEFAULT_REGION -> aws configure get region) so the region
# encoded in stack names always matches the region stacks are deployed into.
region="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region)}}"

# Multi-account preflight (read-only; exit codes documented in check-org-access.sh)
ORG_ROLLOUT=false
if [ "$MULTI_ACCOUNT" = true ]; then
    echo -e "\n\033[0;33mChecking multi-account (organization) access...\033[0m"
    set +e
    ../../shared/scripts/check-org-access.sh
    org_check=$?
    set -e
    if [ $org_check -eq 2 ]; then
        echo -e "\033[0;31mThis account cannot list the organization's accounts, so --multi-account is not possible from here.\033[0m"
        echo -e "\033[0;33mApply the fix printed above, or deploy without --multi-account and add accounts manually (see README, 'Manual account list').\033[0m"
        exit 1
    elif [ $org_check -ne 0 ] && [ $org_check -ne 3 ]; then
        echo -e "\033[0;31mMulti-account preflight failed\033[0m"
        exit 1
    fi
    [ $org_check -eq 0 ] && ORG_ROLLOUT=true
    if [ -z "$ORG_TARGETS" ]; then
        ORG_TARGETS=$(aws organizations list-roots --query "Roots[0].Id" --output text --no-cli-pager)
    fi
    if [ "$ORG_ROLLOUT" = true ]; then
        echo -e "\033[0;90m      Scan targets: $ORG_TARGETS (spoke role rolled out from this account)\033[0m"
    else
        echo -e "\033[0;90m      Scan targets: $ORG_TARGETS (spoke role rollout must be run by the management account)\033[0m"
    fi
fi

# Deploy data stack
echo -e "\n\033[0;33mDeploying data stack...\033[0m"
echo -e "\033[0;90m      (Creating DynamoDB tables and populating service configurations)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "AWSServicesLifecycleTrackerData-$region"

# Deploy auth stack
echo -e "\n\033[0;33mDeploying authentication stack...\033[0m"
echo -e "\033[0;90m      (Creating Cognito User Pool with email verification and password policies)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "AWSServicesLifecycleTrackerAuth-$region" --skip-bootstrap

# Deploy pipeline stack (durable refresh pipeline + API function + schedules)
echo -e "\n\033[0;33mDeploying pipeline stack...\033[0m"
echo -e "\033[0;90m      (Bundling Python code with pip, creating the Lambda durable function, API function, SNS topic and schedules)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "AWSServicesLifecycleTrackerPipeline-$region" --skip-bootstrap

# Multi-account: roll the spoke role out (StackSet) and tell the pipeline what to scan
if [ "$MULTI_ACCOUNT" = true ]; then
    if [ "$ORG_ROLLOUT" = true ]; then
        echo -e "\n\033[0;33mDeploying org stack (spoke role StackSet)...\033[0m"
        echo -e "\033[0;90m      (Service-managed StackSet placing the read-only LifecycleTrackerScanRole in every account under $ORG_TARGETS)\033[0m"
        ../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "AWSServicesLifecycleTrackerOrg-$region" --skip-bootstrap --cdk-context "orgTargets=$ORG_TARGETS"
    fi

    echo -e "\n\033[0;33mConfiguring scan targets...\033[0m"
    regions_json="[{\"S\": \"$region\"}]"
    if [[ ",$ORG_TARGETS," == *",ou-"* ]]; then
        ou_json=""
        IFS=',' read -ra target_ids <<< "$ORG_TARGETS"
        for t in "${target_ids[@]}"; do
            t="${t// /}"; [ -z "$t" ] && continue
            ou_json="$ou_json${ou_json:+,}{\"S\": \"$t\"}"
        done
        item="{\"service_name\": {\"S\": \"_scan_targets\"}, \"source\": {\"S\": \"ou\"}, \"ou_ids\": {\"L\": [$ou_json]}, \"regions\": {\"L\": $regions_json}}"
    else
        item="{\"service_name\": {\"S\": \"_scan_targets\"}, \"source\": {\"S\": \"organization\"}, \"regions\": {\"L\": $regions_json}}"
    fi
    aws dynamodb put-item --table-name "service-extraction-state" --item "$item" --no-cli-pager
    echo -e "\033[0;90m      Scan targets set: $ORG_TARGETS in $region (editable later in the UI, Sources & coverage)\033[0m"
fi

# Deploy API stack (HTTP API + Cognito JWT authorizer)
echo -e "\n\033[0;33mDeploying API stack...\033[0m"
echo -e "\033[0;90m      (Creating the HTTP API with Cognito JWT authorization in front of the API function)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "AWSServicesLifecycleTrackerApi-$region" --skip-bootstrap

# Build and deploy frontend (after backend is complete)
echo -e "\n\033[0;33mBuilding and deploying frontend...\033[0m"
echo -e "\033[0;90m      (Retrieving API URL and Cognito config, building React app, deploying to S3 + CloudFront)\033[0m"
api_url=$(aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerApi-$region" --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text --no-cli-pager)
user_pool_id=$(aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerAuth-$region" --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text --no-cli-pager)
user_pool_client_id=$(aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerAuth-$region" --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" --output text --no-cli-pager)

if [ -z "$api_url" ] || [ "$api_url" == "None" ]; then
    echo -e "\033[0;31mFailed to get API URL from stack outputs\033[0m"
    exit 1
fi

if [ -z "$user_pool_id" ] || [ -z "$user_pool_client_id" ]; then
    echo -e "\033[0;31mFailed to get Cognito config from stack outputs\033[0m"
    exit 1
fi

echo -e "\033[0;32mAPI URL: $api_url\033[0m"
echo -e "\033[0;32mUser Pool ID: $user_pool_id\033[0m"
echo -e "\033[0;32mUser Pool Client ID: $user_pool_client_id\033[0m"

# Build frontend with API URL and Cognito config
./scripts/build-frontend.sh "$user_pool_id" "$user_pool_client_id" "$api_url" "$region"

# Deploy frontend stack
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "AWSServicesLifecycleTrackerFrontend-$region" --skip-bootstrap

# Gather outputs
website_url=$(aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerFrontend-$region" --query "Stacks[0].Outputs[?OutputKey=='WebsiteUrl'].OutputValue" --output text --no-cli-pager)
pipeline_arn=$(aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerPipeline-$region" --query "Stacks[0].Outputs[?OutputKey=='PipelineFunctionAliasArn'].OutputValue" --output text --no-cli-pager)
topic_arn=$(aws cloudformation describe-stacks --stack-name "AWSServicesLifecycleTrackerPipeline-$region" --query "Stacks[0].Outputs[?OutputKey=='NotificationTopicArn'].OutputValue" --output text --no-cli-pager)

echo -e "\n\033[0;32m========================================\033[0m"
echo -e "\033[0;32m  Deployment Complete!\033[0m"
echo -e "\033[0;32m========================================\033[0m"
echo -e "\033[0;36m  Open the demo:      $website_url\033[0m"
echo -e "\033[0;36m  API URL:            $api_url\033[0m"
echo -e "\033[0;36m  Pipeline (durable): $pipeline_arn\033[0m"
echo -e "\033[0;36m  Notifications:      $topic_arn\033[0m"
echo -e "\033[0;36m  Region:             $region\033[0m"
echo -e "\033[0;36m  User Pool ID:       $user_pool_id\033[0m"
echo -e "\n\033[0;33mNext Steps:\033[0m"
echo -e "\033[0;90m  1. Create an admin user (copy-paste these two commands):\033[0m"
echo ""
echo -e "     aws cognito-idp admin-create-user --user-pool-id $user_pool_id --username admin --user-attributes Name=email,Value=admin@company.com Name=email_verified,Value=true --message-action SUPPRESS"
echo ""
echo -e "     aws cognito-idp admin-set-user-password --user-pool-id $user_pool_id --username admin --password \"YourSecurePassword123!\" --permanent"
echo ""
echo -e "\033[0;90m     (Replace the email and password with your own values)\033[0m"
echo -e "\033[0;90m  2. Sign in at the Website URL above and click Refresh to run the first end-to-end pipeline\033[0m"
echo -e "\033[0;90m  3. Optional: subscribe an email to the notifications topic to receive run summaries:\033[0m"
echo -e "     aws sns subscribe --topic-arn $topic_arn --protocol email --notification-endpoint you@example.com"
echo -e "\033[0;90m  4. The weekly schedule runs the same pipeline automatically; Health events are polled hourly\033[0m"
if [ "$MULTI_ACCOUNT" = true ]; then
    hub_account=$(aws sts get-caller-identity --query Account --output text --no-cli-pager)
    echo -e "\n\033[0;33mMulti-account:\033[0m"
    echo -e "\033[0;90m  Hub account $hub_account scans every active account under $ORG_TARGETS in $region\033[0m"
    if [ "$ORG_ROLLOUT" = true ]; then
        echo -e "\033[0;90m  Spoke role: StackSet aws-services-lifecycle-tracker-spoke (auto-deploys to accounts joining later)\033[0m"
        echo -e "     aws cloudformation list-stack-instances --stack-set-name aws-services-lifecycle-tracker-spoke --region $region"
    else
        echo -e "\033[0;33m  ACTION REQUIRED: this account cannot run service-managed StackSets. From the MANAGEMENT account, run once:\033[0m"
        echo -e "     cd cdk && npx cdk deploy AWSServicesLifecycleTrackerOrg-$region --context orgTargets=$ORG_TARGETS --context hubAccountId=$hub_account --require-approval never"
        echo -e "\033[0;90m     (or register this account as a StackSets delegated administrator, see the preflight output above, and re-run with --multi-account)\033[0m"
        echo -e "\033[0;90m  Until then, spoke accounts are reported as failed in each scan; the hub itself is scanned normally.\033[0m"
    fi
fi
