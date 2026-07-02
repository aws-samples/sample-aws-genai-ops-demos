#!/bin/bash
# G.O.A.T. Demo Scenarios - CDK Deployment Script (Bash)
#
# Deploys demo scenario CDK stacks and creates Support cases for G.O.A.T. demos.
# Uses the separate demo-scenarios-app.ts CDK entry point.
#
# Usage:
#   ./deploy-demo-scenarios.sh --scenario <all|account-health|cloudwatch-incident|connectivity|network-troubleshooting>
#
# NOTE: Make this script executable with: chmod +x deploy-demo-scenarios.sh

set -euo pipefail

# ---------------------------------------------------------------------------
# Parameter Parsing
# ---------------------------------------------------------------------------
SCENARIO=""

# Track case IDs for summary
CASE_IDS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --scenario)
            SCENARIO="$2"
            shift 2
            ;;
        *)
            echo -e "\033[0;31mUnknown option: $1\033[0m"
            echo "Usage: $0 --scenario <all|account-health|cloudwatch-incident|connectivity|network-troubleshooting>"
            exit 1
            ;;
    esac
done

# Validate scenario parameter
if [ -z "$SCENARIO" ]; then
    echo -e "\033[0;31mERROR: --scenario parameter is required\033[0m"
    echo ""
    echo "Usage: $0 --scenario <value>"
    echo ""
    echo "Valid values: all, account-health, cloudwatch-incident, connectivity, network-troubleshooting"
    exit 1
fi

case "$SCENARIO" in
    all|account-health|cloudwatch-incident|connectivity|network-troubleshooting)
        ;;
    *)
        echo -e "\033[0;31mERROR: Invalid scenario '$SCENARIO'\033[0m"
        echo ""
        echo "Valid values: all, account-health, cloudwatch-incident, connectivity, network-troubleshooting"
        exit 1
        ;;
esac

echo -e "\033[0;36m=== G.O.A.T. Demo Scenarios Deployment ===\033[0m"
echo -e "\033[0;90m      Scenario: $SCENARIO\033[0m"

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
echo -e "\n\033[0;33mRunning prerequisites check...\033[0m"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../../../shared/scripts/check-prerequisites.sh" --require-cdk

# AWS_REGION is now exported by the prerequisites script
region="$AWS_REGION"
CDK_DIR="$SCRIPT_DIR/../infrastructure/cdk"
CDK_APP="npx ts-node --prefer-ts-exts bin/demo-scenarios-app.ts"

# ---------------------------------------------------------------------------
# Helper: Deploy a CDK stack
# ---------------------------------------------------------------------------
invoke_cdk_deploy() {
    local stack_name="$1"
    echo -e "\n\033[0;33mDeploying $stack_name...\033[0m"

    pushd "$CDK_DIR" > /dev/null
    if ! npx cdk deploy "$stack_name" --app "$CDK_APP" --require-approval never --no-cli-pager; then
        echo -e "\033[0;31mERROR: Deployment of $stack_name failed\033[0m"
        popd > /dev/null
        exit 1
    fi
    popd > /dev/null
}

# ---------------------------------------------------------------------------
# Helper: Create and resolve a Support case
# ---------------------------------------------------------------------------
new_demo_support_case() {
    local subject="$1"
    local body="$2"
    local service_code="${3:-general-info}"
    local category_code="${4:-other}"

    # Check for existing case with the same subject (avoid duplicates)
    local existing=""
    existing=$(aws support describe-cases \
        --include-resolved-cases \
        --region us-east-1 \
        --query "cases[?contains(subject,'$subject')].displayId | [0]" \
        --output text --no-cli-pager 2>/dev/null) || true

    if [ -n "$existing" ] && [ "$existing" != "None" ] && [ "$existing" != "null" ]; then
        echo -e "  \033[0;90mSupport case already exists: $existing -- skipping creation\033[0m"
        CASE_IDS+=("$existing")
        return 0
    fi

    local case_id=""
    local output=""

    output=$(aws support create-case \
        --subject "$subject" \
        --communication-body "$body" \
        --service-code "$service_code" \
        --category-code "$category_code" \
        --severity-code "low" \
        --language "en" \
        --region us-east-1 \
        --query "caseId" --output text --no-cli-pager 2>&1) || true

    if echo "$output" | grep -q "SubscriptionRequiredException"; then
        echo -e "  \033[0;33mNo Business or Enterprise Support plan detected — skipping\033[0m"
        return 0
    fi

    if [ -z "$output" ] || echo "$output" | grep -qi "error\|exception"; then
        echo -e "  \033[0;33mWARNING: CreateCase failed: $output\033[0m"
        return 0
    fi

    case_id="$output"
    sleep 5
    aws support resolve-case --case-id "$case_id" --region us-east-1 --no-cli-pager > /dev/null 2>&1 || true
    # Get the display ID for user-friendly output
    local display_id=""
    display_id=$(aws support describe-cases \
        --case-id-list "$case_id" \
        --include-resolved-cases \
        --region us-east-1 \
        --query "cases[0].displayId" \
        --output text --no-cli-pager 2>/dev/null) || display_id="$case_id"
    if [ -z "$display_id" ] || [ "$display_id" == "None" ]; then display_id="$case_id"; fi
    echo -e "  \033[0;32mCreated and resolved Support case: $display_id\033[0m"
    CASE_IDS+=("$display_id")
}

# ---------------------------------------------------------------------------
# Deployment Logic
# ---------------------------------------------------------------------------
stack_a="GOATDemoScenarioA-$region"
stack_c="GOATDemoScenarioC-$region"

case "$SCENARIO" in
    all)
        invoke_cdk_deploy "$stack_a"
        new_demo_support_case "General account review - G.O.A.T. demo" "This case was created for demo purposes by the G.O.A.T. provisioning scripts."
        new_demo_support_case "CloudWatch monitoring gaps and missing alarms on Apr 1 - G.O.A.T. demo" "Our team noticed a CloudWatch lifecycle event on April 1 resulting in monitoring gaps. Several alarms were missing or misconfigured."
        invoke_cdk_deploy "$stack_c"
        new_demo_support_case \
            "EC2 instance failing HTTPS to ECR - connection reset by peer in $region" \
            "Our instance in goat-demo-vpc is failing to establish HTTPS connections to ECR (endpoint: ecr.$region.amazonaws.com on port 443). The connection is routed through the TGW and the Network Firewall in goat-demo-security-vpc but it is dropped. This case was created by the G.O.A.T. demo provisioning scripts for demonstration purposes." \
            "service-network-firewall" \
            "general-guidance"
        ;;
    account-health)
        invoke_cdk_deploy "$stack_a"
        new_demo_support_case "General account review - G.O.A.T. demo" "This case was created for demo purposes by the G.O.A.T. provisioning scripts."
        ;;
    cloudwatch-incident)
        new_demo_support_case "CloudWatch monitoring gaps and missing alarms on Apr 1 - G.O.A.T. demo" "Our team noticed a CloudWatch lifecycle event on April 1 resulting in monitoring gaps. Several alarms were missing or misconfigured."
        ;;
    connectivity)
        invoke_cdk_deploy "$stack_c"
        new_demo_support_case \
            "EC2 instance failing HTTPS to ECR - connection reset by peer in $region" \
            "Our instance in goat-demo-vpc is failing to establish HTTPS connections to ECR (endpoint: ecr.$region.amazonaws.com on port 443). The connection is routed through the TGW and the Network Firewall in goat-demo-security-vpc but it is dropped. This case was created by the G.O.A.T. demo provisioning scripts for demonstration purposes." \
            "service-network-firewall" \
            "general-guidance"
        ;;
esac

# ---------------------------------------------------------------------------
# Deployment Summary
# ---------------------------------------------------------------------------
echo ""
echo -e "\033[0;32m========================================\033[0m"
echo -e "\033[0;32m  G.O.A.T. Demo Scenario Deployment Complete!\033[0m"
echo -e "\033[0;32m========================================\033[0m"
echo ""
echo -e "  \033[0;36mRegion: $region\033[0m"

if [ "$SCENARIO" = "all" ] || [ "$SCENARIO" = "account-health" ]; then
    vpc_id=$(aws cloudformation describe-stacks --stack-name "$stack_a" --query "Stacks[0].Outputs[?OutputKey=='VpcId'].OutputValue" --output text --no-cli-pager 2>/dev/null || echo "N/A")
    inst1=$(aws cloudformation describe-stacks --stack-name "$stack_a" --query "Stacks[0].Outputs[?OutputKey=='Instance1Id'].OutputValue" --output text --no-cli-pager 2>/dev/null || echo "N/A")
    echo -e "  \033[0;36mVPC:          $vpc_id\033[0m"
    echo -e "  \033[0;36mEC2 Instance: $inst1\033[0m"
fi

if [ "$SCENARIO" = "all" ] || [ "$SCENARIO" = "connectivity" ]; then
    ec2_id=$(aws cloudformation describe-stacks --stack-name "$stack_c" --query "Stacks[0].Outputs[?OutputKey=='AppInstanceId'].OutputValue" --output text --no-cli-pager 2>/dev/null || echo "N/A")
    eni_id=$(aws cloudformation describe-stacks --stack-name "$stack_c" --query "Stacks[0].Outputs[?OutputKey=='AppInstanceEniId'].OutputValue" --output text --no-cli-pager 2>/dev/null || echo "N/A")
    echo -e "  \033[0;36mApp EC2:      $ec2_id\033[0m"
    echo -e "  \033[0;36mApp ENI:      $eni_id (for Network Agent capture)\033[0m"
fi

if [ ${#CASE_IDS[@]} -gt 0 ]; then
    echo ""
    echo -e "  \033[0;33mSupport Cases:\033[0m"
    for id in "${CASE_IDS[@]}"; do
        echo -e "    \033[0;36m$id\033[0m"
    done
    echo ""
    echo -e "  \033[0;33mTry in G.O.A.T.:\033[0m"
    echo -e "  \033[0;90m  \"Help me troubleshoot case ${CASE_IDS[-1]}\"\033[0m"
fi

echo ""
echo -e "  \033[0;33mSuggested Demo Queries:\033[0m"
echo -e "  \033[0;90m  \"Give me a complete health check of my AWS account\"\033[0m"
echo -e "  \033[0;90m  \"We had application errors on April 1 - was there an AWS issue?\"\033[0m"
echo -e "  \033[0;90m  \"My EC2 instance cannot connect to ECR over HTTPS\"\033[0m"
echo ""
echo -e "  \033[0;33mCleanup:\033[0m"
echo -e "  \033[0;90m  ./cleanup-scenarios.ps1    (PowerShell)\033[0m"
echo -e "  \033[0;90m  ./cleanup-scenarios.sh     (Bash)\033[0m"
echo ""
