#!/bin/bash
# G.O.A.T. Demo Scenarios - CDK Deployment Script (Bash)
#
# Deploys demo scenario CDK stacks and creates Support cases for G.O.A.T. demos.
# Uses the separate demo-scenarios-app.ts CDK entry point.
#
# Usage:
#   ./deploy-demo-scenarios.sh --scenario <all|account-health|cloudwatch-incident|tls-fragmentation>
#
# NOTE: Make this script executable with: chmod +x deploy-demo-scenarios.sh

set -euo pipefail

# ---------------------------------------------------------------------------
# Parameter Parsing
# ---------------------------------------------------------------------------
SCENARIO=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --scenario)
            SCENARIO="$2"
            shift 2
            ;;
        *)
            echo -e "\033[0;31mUnknown option: $1\033[0m"
            echo "Usage: $0 --scenario <all|account-health|cloudwatch-incident|tls-fragmentation>"
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
    echo "Valid values: all, account-health, cloudwatch-incident, tls-fragmentation"
    exit 1
fi

case "$SCENARIO" in
    all|account-health|cloudwatch-incident|tls-fragmentation)
        ;;
    *)
        echo -e "\033[0;31mERROR: Invalid scenario '$SCENARIO'\033[0m"
        echo ""
        echo "Valid values: all, account-health, cloudwatch-incident, tls-fragmentation"
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
source "$SCRIPT_DIR/../../../../shared/scripts/check-prerequisites.sh" --require-cdk

# AWS_REGION is now exported by the prerequisites script
region="$AWS_REGION"
CDK_DIR="$SCRIPT_DIR/../../infrastructure/cdk"
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

    local case_id=""
    local output=""

    output=$(aws support create-case \
        --subject "$subject" \
        --communication-body "$body" \
        --service-code "general-info" \
        --category-code "other" \
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
    echo -e "  \033[0;32mCreated and resolved Support case: $case_id\033[0m"
}

# ---------------------------------------------------------------------------
# Deployment Logic
# ---------------------------------------------------------------------------
stack_a="GOATDemoScenarioA-$region"
stack_c="GOATDemoScenarioTLS-$region"

case "$SCENARIO" in
    all)
        invoke_cdk_deploy "$stack_a"
        new_demo_support_case "General account review - G.O.A.T. demo" "This case was created for demo purposes by the G.O.A.T. provisioning scripts."
        new_demo_support_case "CloudWatch monitoring gaps and missing alarms on Apr 1 - G.O.A.T. demo" "Our team noticed a CloudWatch lifecycle event on April 1 resulting in monitoring gaps. Several alarms were missing or misconfigured."
        invoke_cdk_deploy "$stack_c"
        new_demo_support_case "EC2 instance failing HTTPS to ECR - connection reset by peer" "An EC2 instance is failing HTTPS connections to ECR. The TLS Client Hello appears to suffer from fragmentation when using ML-KEM key exchange. Network Firewall is dropping the connection."
        ;;
    account-health)
        invoke_cdk_deploy "$stack_a"
        new_demo_support_case "General account review - G.O.A.T. demo" "This case was created for demo purposes by the G.O.A.T. provisioning scripts."
        ;;
    cloudwatch-incident)
        new_demo_support_case "CloudWatch monitoring gaps and missing alarms on Apr 1 - G.O.A.T. demo" "Our team noticed a CloudWatch lifecycle event on April 1 resulting in monitoring gaps. Several alarms were missing or misconfigured."
        ;;
    tls-fragmentation)
        invoke_cdk_deploy "$stack_c"
        new_demo_support_case "EC2 instance failing HTTPS to ECR - connection reset by peer" "An EC2 instance is failing HTTPS connections to ECR. The TLS Client Hello appears to suffer from fragmentation when using ML-KEM key exchange. Network Firewall is dropping the connection."
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

if [ "$SCENARIO" = "all" ] || [ "$SCENARIO" = "tls-fragmentation" ]; then
    ec2_id=$(aws cloudformation describe-stacks --stack-name "$stack_c" --query "Stacks[0].Outputs[?OutputKey=='TlsInstanceId'].OutputValue" --output text --no-cli-pager 2>/dev/null || echo "N/A")
    eni_id=$(aws cloudformation describe-stacks --stack-name "$stack_c" --query "Stacks[0].Outputs[?OutputKey=='TlsInstanceEniId'].OutputValue" --output text --no-cli-pager 2>/dev/null || echo "N/A")
    echo -e "  \033[0;36mTLS EC2:      $ec2_id\033[0m"
    echo -e "  \033[0;36mTLS ENI:      $eni_id (for Network Agent capture)\033[0m"
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
