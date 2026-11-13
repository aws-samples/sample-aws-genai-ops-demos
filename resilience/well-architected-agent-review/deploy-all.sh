#!/usr/bin/env bash
# Well-Architected Agent Review - deployment script (Bash)
#
# Deploys one of the two target workloads that the Well-Architected Agent
# analyzes, plus the agent profile that reviews it. Uses the GenAI Ops Demo
# Library shared scripts, so it must run from <pillar>/<demo>/ inside the library
# checkout.
#
# Variants:
#   single (default)  deliberately non-optimized single-region workload, one stack
#   multi             active/active workload in two Regions with documented gaps,
#                     three stacks (primary, secondary, dashboard). The secondary
#                     Region is derived from the primary one (us-east-1 -> us-west-2,
#                     us-east-2 -> us-west-2, us-west-2 -> us-east-1).
# The deployment Region (primary Region for multi) is the one resolved by the
# shared prerequisites check and must be us-east-1, us-east-2 or us-west-2.
#
# Usage:
#   ./deploy-all.sh                                  # single, admin CIDR = your public IP /32
#   ./deploy-all.sh --admin-cidr "<YOUR_IP>/32"
#   ./deploy-all.sh --variant multi
#   ./deploy-all.sh --variant multi --domain app.example.com --zone example.com   # optional HTTPS
#   ./deploy-all.sh [--variant multi] --destroy      # tear everything down
set -eo pipefail

VARIANT="single"
ADMIN_CIDR=""
DOMAIN=""
ZONE=""
DESTROY="false"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --variant)    VARIANT="${2:-}"; shift 2 ;;
        --admin-cidr) ADMIN_CIDR="${2:-}"; shift 2 ;;
        --domain)     DOMAIN="${2:-}"; shift 2 ;;
        --zone)       ZONE="${2:-}"; shift 2 ;;
        --destroy)    DESTROY="true"; shift ;;
        *)
            echo "Usage: $0 [--variant single|multi] [--admin-cidr <YOUR_IP>/32] [--domain <app.example.com> --zone <example.com>] [--destroy]"
            exit 1
            ;;
    esac
done

if [ "$VARIANT" != "single" ] && [ "$VARIANT" != "multi" ]; then
    echo -e "\033[0;31mERROR: --variant must be 'single' or 'multi'\033[0m"
    exit 1
fi
if [ "$VARIANT" = "single" ] && { [ -n "$DOMAIN" ] || [ -n "$ZONE" ]; }; then
    echo -e "\033[0;31mERROR: --domain / --zone apply to the multi variant only\033[0m"
    exit 1
fi
if { [ -n "$DOMAIN" ] && [ -z "$ZONE" ]; } || { [ -z "$DOMAIN" ] && [ -n "$ZONE" ]; }; then
    echo -e "\033[0;31mERROR: --domain and --zone must be passed together.\033[0m"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED_SCRIPTS="$SCRIPT_DIR/../../shared/scripts"
if [ ! -f "$SHARED_SCRIPTS/check-prerequisites.sh" ]; then
    echo -e "\033[0;31mERROR: shared scripts not found at $SHARED_SCRIPTS\033[0m"
    echo "       Run this script from <pillar>/<demo>/ inside the GenAI Ops Demo Library."
    exit 1
fi

cd "$SCRIPT_DIR"

# Prerequisites: AWS CLI (2.31.13+ for the Well-Architected Agent API), credentials,
# region, Node.js 20+ and CDK. Exports AWS_REGION and AWS_ACCOUNT_ID.
source "$SHARED_SCRIPTS/check-prerequisites.sh" --require-cdk --min-aws-cli-version 2.31.13
REGION="$AWS_REGION"

case "$REGION" in
    us-east-1|us-east-2|us-west-2) ;;
    *)
        echo -e "\033[0;31mERROR: the Well-Architected Agent profile can only be hosted in us-east-1, us-east-2 or us-west-2 (resolved: $REGION).\033[0m"
        echo "       Set AWS_REGION and AWS_DEFAULT_REGION to one of them and retry."
        exit 1
        ;;
esac

# The CDK app reads the variant from here; it is also the only way to reach
# `cdk destroy`, which gets no --context from the shared deploy-cdk script.
export WA_DEMO_VARIANT="$VARIANT"

get_output() {
    aws cloudformation describe-stacks --stack-name "$1" --region "$2" \
        --query "Stacks[0].Outputs[?OutputKey=='$3'].OutputValue" --output text --no-cli-pager
}

# Admin CIDR is only needed to deploy (destroy synthesizes with the no-access fallback).
resolve_admin_cidr() {
    # Trusted admin CIDR allowed to reach the workload. When omitted, the caller's
    # public IPv4 is detected and used as a /32.
    if [ -z "$ADMIN_CIDR" ]; then
        echo -e "\033[0;33mDetecting your public IP for the admin CIDR...\033[0m"
        DETECTED_IP="$(curl -fsS --max-time 10 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
        # Fail closed: never deploy with an unexpected value as the admin CIDR.
        if [[ ! "$DETECTED_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
            echo -e "\033[0;31mERROR: could not detect your public IPv4. Pass it explicitly: --admin-cidr <YOUR_IP>/32\033[0m"
            exit 1
        fi
        ADMIN_CIDR="$DETECTED_IP/32"
        echo "      Admin CIDR: $ADMIN_CIDR"
    fi
    # Basic format check on the admin CIDR before it reaches CDK context.
    if [[ ! "$ADMIN_CIDR" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$ ]]; then
        echo -e "\033[0;31mERROR: --admin-cidr must be an IPv4 CIDR, e.g. <YOUR_IP>/32\033[0m"
        exit 1
    fi
}

# ===========================================================================
# Variant: single
# ===========================================================================
if [ "$VARIANT" = "single" ]; then
    # Pin both variables so the CDK app (getRegion) and deploy-cdk resolve the same
    # region, and the region-suffixed stack name below matches what gets deployed.
    export AWS_REGION="$REGION"
    export AWS_DEFAULT_REGION="$REGION"
    STACK_NAME="WellArchitectedAgentReviewStack-$REGION"

    if [ "$DESTROY" = "true" ]; then
        "$SHARED_SCRIPTS/deploy-cdk.sh" --cdk-directory "infrastructure/cdk" \
            --stack-name "$STACK_NAME" --skip-bootstrap --destroy
        echo ""
        echo -e "\033[0;32m  Cleanup Complete: $STACK_NAME ($REGION)\033[0m"
        exit 0
    fi

    resolve_admin_cidr
    "$SHARED_SCRIPTS/deploy-cdk.sh" \
        --cdk-directory "infrastructure/cdk" \
        --stack-name "$STACK_NAME" \
        --cdk-context "adminCidr=$ADMIN_CIDR"

    INSTANCE_IP="$(get_output "$STACK_NAME" "$REGION" InstancePublicIp)"
    DB_ENDPOINT="$(get_output "$STACK_NAME" "$REGION" DatabaseEndpoint)"
    BUCKET_NAME="$(get_output "$STACK_NAME" "$REGION" AssetBucketName)"
    PROFILE_ARN="$(get_output "$STACK_NAME" "$REGION" AgentProfileArn)"

    echo ""
    echo -e "\033[0;32m========================================\033[0m"
    echo -e "\033[0;32m  Deployment Complete! (variant: single)\033[0m"
    echo -e "\033[0;32m========================================\033[0m"
    echo -e "\033[0;36m  Stack:          $STACK_NAME\033[0m"
    echo -e "\033[0;36m  Region:         $REGION\033[0m"
    echo -e "\033[0;36m  Admin CIDR:     $ADMIN_CIDR\033[0m"
    echo -e "\033[0;36m  Instance IP:    $INSTANCE_IP\033[0m"
    echo -e "\033[0;36m  DB endpoint:    $DB_ENDPOINT\033[0m"
    echo -e "\033[0;36m  Asset bucket:   $BUCKET_NAME\033[0m"
    echo -e "\033[0;36m  Agent profile:  $PROFILE_ARN\033[0m"
    echo ""
    echo -e "\033[0;33m  Next: recommendations appear in the AWS WA Agent console within 24-48 hours.\033[0m"
    echo -e "\033[0;33m  Check eligibility: aws wellarchitected get-agent-profile --profile-arn $PROFILE_ARN --region $REGION\033[0m"
    echo -e "\033[0;33m  Cleanup: ./deploy-all.sh --destroy\033[0m"
    exit 0
fi

# ===========================================================================
# Variant: multi
# ===========================================================================
PRIMARY_REGION="$REGION"
case "$PRIMARY_REGION" in
    us-east-1|us-east-2) SECONDARY_REGION="us-west-2" ;;
    us-west-2)           SECONDARY_REGION="us-east-1" ;;
esac
PRIMARY_STACK="WellArchitectedAgentReviewMultiPrimary-$PRIMARY_REGION"
SECONDARY_STACK="WellArchitectedAgentReviewMultiSecondary-$SECONDARY_REGION"
DASHBOARD_STACK="WellArchitectedAgentReviewMultiDashboard-$PRIMARY_REGION"

# The CDK app reads the primary Region from here, so every deploy-cdk call below
# synthesizes the same stacks even while AWS_REGION points at the secondary Region.
export WA_DEMO_PRIMARY_REGION="$PRIMARY_REGION"

# Run the shared deploy-cdk script with AWS_REGION pinned to one Region. It
# bootstraps that Region (unless --skip-bootstrap) and deploys/destroys one stack.
deploy_in() {
    local region="$1"; shift
    AWS_REGION="$region" AWS_DEFAULT_REGION="$region" \
        "$SHARED_SCRIPTS/deploy-cdk.sh" --cdk-directory "infrastructure/cdk" "$@"
}

if [ "$DESTROY" = "true" ]; then
    # Reverse order: dashboard, secondary, primary (the primary deletes the global
    # table and its replica, and the agent profile).
    echo -e "\033[0;33mDestroying $DASHBOARD_STACK, $SECONDARY_STACK and $PRIMARY_STACK...\033[0m"
    deploy_in "$PRIMARY_REGION" --stack-name "$DASHBOARD_STACK" --skip-bootstrap --destroy
    deploy_in "$SECONDARY_REGION" --stack-name "$SECONDARY_STACK" --skip-bootstrap --destroy
    deploy_in "$PRIMARY_REGION" --stack-name "$PRIMARY_STACK" --skip-bootstrap --destroy
    echo ""
    echo -e "\033[0;32m========================================\033[0m"
    echo -e "\033[0;32m  Cleanup Complete! (variant: multi)\033[0m"
    echo -e "\033[0;32m========================================\033[0m"
    echo -e "\033[0;36m  Destroyed:  $DASHBOARD_STACK, $SECONDARY_STACK, $PRIMARY_STACK\033[0m"
    echo -e "\033[0;36m  Regions:    $PRIMARY_REGION, $SECONDARY_REGION\033[0m"
    exit 0
fi

resolve_admin_cidr
CONTEXT=(--cdk-context "adminCidr=$ADMIN_CIDR")
if [ -n "$DOMAIN" ]; then
    CONTEXT+=(--cdk-context "appDomainName=$DOMAIN" --cdk-context "route53ZoneName=$ZONE")
fi

# 1. Primary: bootstraps the primary Region, deploys the region stack, the global
#    table and the agent profile.
deploy_in "$PRIMARY_REGION" --stack-name "$PRIMARY_STACK" "${CONTEXT[@]}"

# 2. Secondary: bootstraps the secondary Region and deploys its region stack.
deploy_in "$SECONDARY_REGION" --stack-name "$SECONDARY_STACK" "${CONTEXT[@]}"

# 3. Dashboard: takes the ALB and target group dimensions of both Regions as context.
P_ALB="$(get_output "$PRIMARY_STACK" "$PRIMARY_REGION" AlbFullName)"
P_TG="$(get_output "$PRIMARY_STACK" "$PRIMARY_REGION" TargetGroupFullName)"
S_ALB="$(get_output "$SECONDARY_STACK" "$SECONDARY_REGION" AlbFullName)"
S_TG="$(get_output "$SECONDARY_STACK" "$SECONDARY_REGION" TargetGroupFullName)"
deploy_in "$PRIMARY_REGION" --stack-name "$DASHBOARD_STACK" --skip-bootstrap "${CONTEXT[@]}" \
    --cdk-context "primaryAlbFullName=$P_ALB" --cdk-context "primaryTgFullName=$P_TG" \
    --cdk-context "secondaryAlbFullName=$S_ALB" --cdk-context "secondaryTgFullName=$S_TG"

PRIMARY_ALB="$(get_output "$PRIMARY_STACK" "$PRIMARY_REGION" AlbDnsName)"
SECONDARY_ALB="$(get_output "$SECONDARY_STACK" "$SECONDARY_REGION" AlbDnsName)"
TABLE_NAME="$(get_output "$PRIMARY_STACK" "$PRIMARY_REGION" DynamoDbTableName)"
PROFILE_ARN="$(get_output "$PRIMARY_STACK" "$PRIMARY_REGION" AgentProfileArn)"
DASHBOARD_URL="$(get_output "$DASHBOARD_STACK" "$PRIMARY_REGION" DashboardUrl)"

echo ""
echo -e "\033[0;32m========================================\033[0m"
echo -e "\033[0;32m  Deployment Complete! (variant: multi)\033[0m"
echo -e "\033[0;32m========================================\033[0m"
echo -e "\033[0;36m  Primary:        $PRIMARY_STACK ($PRIMARY_REGION)\033[0m"
echo -e "\033[0;36m  Secondary:      $SECONDARY_STACK ($SECONDARY_REGION)\033[0m"
echo -e "\033[0;36m  Admin CIDR:     $ADMIN_CIDR\033[0m"
echo -e "\033[0;36m  Primary ALB:    $PRIMARY_ALB\033[0m"
echo -e "\033[0;36m  Secondary ALB:  $SECONDARY_ALB\033[0m"
echo -e "\033[0;36m  DynamoDB table: $TABLE_NAME (global, both regions)\033[0m"
echo -e "\033[0;36m  Agent profile:  $PROFILE_ARN\033[0m"
echo -e "\033[0;36m  Dashboard:      $DASHBOARD_URL\033[0m"
echo ""
echo -e "\033[0;33m  Next: recommendations appear in the AWS WA Agent console within 24-48 hours.\033[0m"
echo -e "\033[0;33m  Check eligibility: aws wellarchitected get-agent-profile --profile-arn $PROFILE_ARN --region $PRIMARY_REGION\033[0m"
echo -e "\033[0;33m  Cleanup: ./deploy-all.sh --variant multi --destroy\033[0m"
