#!/usr/bin/env bash
# Well-Architected Agent Review - deployment script (Bash)
#
# Deploys the demo's three building blocks. Uses the GenAI Ops Demo Library
# shared scripts, so it must run from <pillar>/<demo>/ inside the library checkout.
#
#   profile           the shared Well-Architected Agent profile (roles, profile,
#                     goals): stack WellArchitectedAgentReviewProfile-<agent-region>.
#                     One per account and agent Region. Deployed (or updated)
#                     automatically before every application.
#   single (default)  deliberately non-optimized single-region application, and its
#                     context stack that registers it in the profile
#   multi             active/active application in two Regions with documented gaps
#                     (primary, secondary, dashboard), and its context stack. The
#                     secondary Region comes from --secondary-region; otherwise the
#                     script picks one from the account's enabled Regions in the
#                     primary's geography (alphabetical), and later runs reuse the
#                     deployed one.
#
# Regions: applications deploy in the Region resolved by the shared prerequisites
# check (AWS_REGION / AWS_DEFAULT_REGION). The profile and every context stack live
# in WA_AGENT_REGION, which defaults to the same Region. A profile can only be
# hosted in a few Regions; when yours is not one of them the check stops and tells
# you to set WA_AGENT_REGION. The profile scans every enabled Region of the account.
#
# Destroy removes one application (its context stack, then its workload). The
# profile stays for the other applications; remove it with --variant profile
# --destroy once no application is left (the script and CloudFormation refuse earlier).
#
# Usage:
#   ./deploy-all.sh                                  # single, admin CIDR = your public IP /32
#   ./deploy-all.sh --admin-cidr "<YOUR_IP>/32"
#   ./deploy-all.sh --variant multi
#   ./deploy-all.sh --variant multi --secondary-region us-west-2
#   ./deploy-all.sh --variant multi --domain app.example.com --zone example.com   # optional HTTPS
#   ./deploy-all.sh [--variant multi] --destroy      # remove one application
#   ./deploy-all.sh --variant profile [--destroy]    # deploy or remove the profile alone
#   WA_AGENT_REGION=us-east-1 ./deploy-all.sh        # host the profile outside the workload Region
set -eo pipefail

VARIANT="single"
ADMIN_CIDR=""
DOMAIN=""
ZONE=""
SECONDARY_ARG=""
DESTROY="false"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --variant)          VARIANT="${2:-}"; shift 2 ;;
        --admin-cidr)       ADMIN_CIDR="${2:-}"; shift 2 ;;
        --domain)           DOMAIN="${2:-}"; shift 2 ;;
        --zone)             ZONE="${2:-}"; shift 2 ;;
        --secondary-region) SECONDARY_ARG="${2:-}"; shift 2 ;;
        --destroy)          DESTROY="true"; shift ;;
        *)
            echo "Usage: $0 [--variant single|multi|profile] [--admin-cidr <YOUR_IP>/32] [--secondary-region <region>] [--domain <app.example.com> --zone <example.com>] [--destroy]"
            exit 1
            ;;
    esac
done

if [ "$VARIANT" != "single" ] && [ "$VARIANT" != "multi" ] && [ "$VARIANT" != "profile" ]; then
    echo -e "\033[0;31mERROR: --variant must be 'single', 'multi' or 'profile'\033[0m"
    exit 1
fi
if [ "$VARIANT" != "multi" ] && { [ -n "$DOMAIN" ] || [ -n "$ZONE" ]; }; then
    echo -e "\033[0;31mERROR: --domain / --zone apply to the multi variant only\033[0m"
    exit 1
fi
if [ "$VARIANT" != "multi" ] && [ -n "$SECONDARY_ARG" ]; then
    echo -e "\033[0;31mERROR: --secondary-region applies to the multi variant only\033[0m"
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
# region, Node.js 20+ and CDK, and the Region that hosts the agent profile
# (WA_AGENT_REGION, default: the deploy Region). The shared check holds the list of
# hosting Regions and stops when the profile Region is not one. Exports AWS_REGION,
# AWS_ACCOUNT_ID and WA_AGENT_REGION.
source "$SHARED_SCRIPTS/check-prerequisites.sh" --required-service wellarchitected-agent --require-cdk --min-aws-cli-version 2.31.13
REGION="$AWS_REGION"
AGENT_REGION="$WA_AGENT_REGION"
PROFILE_STACK="WellArchitectedAgentReviewProfile-$AGENT_REGION"
PROFILE_EXPORT="WellArchitectedAgentReviewProfileArn"   # PROFILE_ARN_EXPORT in lib/wa-agent.ts

get_output() {
    aws cloudformation describe-stacks --stack-name "$1" --region "$2" \
        --query "Stacks[0].Outputs[?OutputKey=='$3'].OutputValue" --output text --no-cli-pager
}

# Same, but a missing stack or output is a normal case: prints "".
get_output_or_empty() {
    local value
    value="$(aws cloudformation describe-stacks --stack-name "$1" --region "$2" \
        --query "Stacks[0].Outputs[?OutputKey=='$3'].OutputValue" --output text --no-cli-pager 2>/dev/null)" || value=""
    [ "$value" = "None" ] && value=""
    printf '%s' "$value"
}

# Regions the shared profile scans: every enabled Region of the account
# (ec2 describe-regions, read-only), sorted so the template does not change
# between runs. Each application context narrows the scope to its own Regions.
scan_regions() {
    local names
    names="$(aws ec2 describe-regions --region "$AGENT_REGION" --query "Regions[].RegionName" \
        --output text --no-cli-pager)" || return 0
    printf '%s\n' $names | LC_ALL=C sort | paste -sd, -
}

# Stacks that still import the profile ARN: the registered applications.
profile_importers() {
    local value
    value="$(aws cloudformation list-imports --export-name "$PROFILE_EXPORT" --region "$AGENT_REGION" \
        --query "Imports" --output text --no-cli-pager 2>/dev/null)" || value=""
    [ "$value" = "None" ] && value=""
    printf '%s' "$value"
}

# Geography of a Region code: the code without its last two segments
# (us-east-1 -> us, ap-southeast-1 -> ap, us-gov-west-1 -> us-gov). The multi
# variant keeps both Regions in one geography (data residency, latency,
# replication cost). Same rule as Get-RegionGeography in the PowerShell twin.
region_geography() {
    printf '%s' "${1%-*-*}"
}

# Default secondary Region of the multi variant, with no Region list in the demo:
# this account's enabled Regions (ec2 describe-regions, read-only) in the
# primary's geography, primary excluded, sorted alphabetically (LC_ALL=C, as the
# ordinal sort in the PowerShell twin), Regions without opt-in first. "" when the
# geography has no other enabled Region. Deterministic for a given set of enabled
# Regions; redeploy and destroy reuse the deployed value instead.
select_secondary_region() {
    local primary="$1" geo rows pick
    geo="$(region_geography "$primary")"
    rows="$(aws ec2 describe-regions --region "$primary" \
        --query "Regions[].[RegionName,OptInStatus]" --output text --no-cli-pager)" || return 0
    # Keep "name status" rows of the same geography (name minus its last two segments).
    rows="$(printf '%s\n' "$rows" | awk -v p="$primary" -v g="$geo" \
        'NF && $1 != p { n = $1; sub(/-[a-z]+-[0-9]+$/, "", n); if (n == g) print }' | LC_ALL=C sort -k1,1)"
    pick="$(printf '%s\n' "$rows" | awk '$2 == "opt-in-not-required" { print $1; exit }')"
    [ -z "$pick" ] && pick="$(printf '%s\n' "$rows" | awk 'NF { print $1; exit }')"
    printf '%s' "$pick"
}

# Run the shared deploy-cdk script with AWS_REGION pinned to one Region. It
# bootstraps that Region (unless --skip-bootstrap) and deploys/destroys one stack.
deploy_in() {
    local region="$1"; shift
    AWS_REGION="$region" AWS_DEFAULT_REGION="$region" \
        "$SHARED_SCRIPTS/deploy-cdk.sh" --cdk-directory "infrastructure/cdk" "$@"
}

# Deploy (or update) the shared profile in the agent Region; bootstraps it.
deploy_profile() {
    deploy_in "$AGENT_REGION" --stack-name "$PROFILE_STACK"
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

# The CDK app reads these from the environment: deploy-cdk passes no --context on
# destroy, and AWS_REGION moves between Regions below. They keep every deploy-cdk
# call synthesizing the same stacks.
SCAN_REGIONS="$(scan_regions)"
if [ -z "$SCAN_REGIONS" ]; then
    echo -e "\033[0;31mERROR: could not list this account's enabled Regions (ec2:DescribeRegions).\033[0m"
    exit 1
fi
export WA_DEMO_VARIANT="$VARIANT"
export WA_DEMO_PRIMARY_REGION="$REGION"
export WA_AGENT_REGION="$AGENT_REGION"
export WA_AGENT_SCAN_REGIONS="$SCAN_REGIONS"

# ===========================================================================
# Variant: profile (the shared profile alone)
# ===========================================================================
if [ "$VARIANT" = "profile" ]; then
    if [ "$DESTROY" = "true" ]; then
        IMPORTERS="$(profile_importers)"
        if [ -n "$IMPORTERS" ]; then
            echo -e "\033[0;31mERROR: applications are still registered in $PROFILE_STACK ($AGENT_REGION):\033[0m"
            for s in $IMPORTERS; do echo -e "\033[0;31m         $s\033[0m"; done
            echo "       Destroy them first (./deploy-all.sh [--variant multi] --destroy), then retry."
            exit 1
        fi
        deploy_in "$AGENT_REGION" --stack-name "$PROFILE_STACK" --skip-bootstrap --destroy
        echo ""
        echo -e "\033[0;32m========================================\033[0m"
        echo -e "\033[0;32m  Cleanup Complete! (variant: profile)\033[0m"
        echo -e "\033[0;32m========================================\033[0m"
        echo -e "\033[0;36m  Destroyed:  $PROFILE_STACK ($AGENT_REGION)\033[0m"
        exit 0
    fi

    deploy_profile
    PROFILE_ARN="$(get_output "$PROFILE_STACK" "$AGENT_REGION" AgentProfileArn)"
    echo ""
    echo -e "\033[0;32m========================================\033[0m"
    echo -e "\033[0;32m  Deployment Complete! (variant: profile)\033[0m"
    echo -e "\033[0;32m========================================\033[0m"
    echo -e "\033[0;36m  Stack:          $PROFILE_STACK\033[0m"
    echo -e "\033[0;36m  Agent region:   $AGENT_REGION\033[0m"
    echo -e "\033[0;36m  Scans:          $SCAN_REGIONS\033[0m"
    echo -e "\033[0;36m  Agent profile:  $PROFILE_ARN\033[0m"
    echo ""
    echo -e "\033[0;33m  Next: deploy an application (./deploy-all.sh or --variant multi). A profile with no\033[0m"
    echo -e "\033[0;33m  application context runs no scheduled recommendations.\033[0m"
    exit 0
fi

# ===========================================================================
# Variant: single
# ===========================================================================
if [ "$VARIANT" = "single" ]; then
    STACK_NAME="WellArchitectedAgentReviewStack-$REGION"
    CONTEXT_STACK="WellArchitectedAgentReviewSingleContext-$REGION"

    if [ "$DESTROY" = "true" ]; then
        # Context first (releases the profile), then the workload.
        deploy_in "$AGENT_REGION" --stack-name "$CONTEXT_STACK" --skip-bootstrap --destroy
        deploy_in "$REGION" --stack-name "$STACK_NAME" --skip-bootstrap --destroy
        echo ""
        echo -e "\033[0;32m========================================\033[0m"
        echo -e "\033[0;32m  Cleanup Complete! (variant: single)\033[0m"
        echo -e "\033[0;32m========================================\033[0m"
        echo -e "\033[0;36m  Destroyed:  $CONTEXT_STACK ($AGENT_REGION)\033[0m"
        echo -e "\033[0;36m              $STACK_NAME ($REGION)\033[0m"
        echo -e "\033[0;36m  Kept:       $PROFILE_STACK ($AGENT_REGION), shared by the applications\033[0m"
        echo -e "\033[0;33m  Remove it when no application is left: ./deploy-all.sh --variant profile --destroy\033[0m"
        exit 0
    fi

    resolve_admin_cidr
    # 1. Shared profile (created or updated), 2. workload, 3. its context.
    deploy_profile
    deploy_in "$REGION" --stack-name "$STACK_NAME" --cdk-context "adminCidr=$ADMIN_CIDR"
    deploy_in "$AGENT_REGION" --stack-name "$CONTEXT_STACK" --skip-bootstrap

    INSTANCE_IP="$(get_output "$STACK_NAME" "$REGION" InstancePublicIp)"
    DB_ENDPOINT="$(get_output "$STACK_NAME" "$REGION" DatabaseEndpoint)"
    BUCKET_NAME="$(get_output "$STACK_NAME" "$REGION" AssetBucketName)"
    PROFILE_ARN="$(get_output "$PROFILE_STACK" "$AGENT_REGION" AgentProfileArn)"

    echo ""
    echo -e "\033[0;32m========================================\033[0m"
    echo -e "\033[0;32m  Deployment Complete! (variant: single)\033[0m"
    echo -e "\033[0;32m========================================\033[0m"
    echo -e "\033[0;36m  Workload:       $STACK_NAME ($REGION)\033[0m"
    echo -e "\033[0;36m  Context:        $CONTEXT_STACK ($AGENT_REGION)\033[0m"
    echo -e "\033[0;36m  Profile:        $PROFILE_STACK ($AGENT_REGION)\033[0m"
    echo -e "\033[0;36m  Admin CIDR:     $ADMIN_CIDR\033[0m"
    echo -e "\033[0;36m  Instance IP:    $INSTANCE_IP\033[0m"
    echo -e "\033[0;36m  DB endpoint:    $DB_ENDPOINT\033[0m"
    echo -e "\033[0;36m  Asset bucket:   $BUCKET_NAME\033[0m"
    echo -e "\033[0;36m  Agent profile:  $PROFILE_ARN\033[0m"
    echo ""
    echo -e "\033[0;33m  Next: the first scheduled recommendations usually arrive within a few hours (at most 48 hours).\033[0m"
    echo -e "\033[0;33m  Check progress: aws wellarchitected list-agent-recommendation-generations --profile-arn $PROFILE_ARN --region $AGENT_REGION\033[0m"
    echo -e "\033[0;33m  Check eligibility: aws wellarchitected get-agent-profile --profile-arn $PROFILE_ARN --region $AGENT_REGION\033[0m"
    echo -e "\033[0;33m  Cleanup: ./deploy-all.sh --destroy, then ./deploy-all.sh --variant profile --destroy (same AWS_REGION and WA_AGENT_REGION)\033[0m"
    exit 0
fi

# ===========================================================================
# Variant: multi
# ===========================================================================
PRIMARY_REGION="$REGION"
PRIMARY_STACK="WellArchitectedAgentReviewMultiPrimary-$PRIMARY_REGION"

# Secondary Region, in this order:
#   1. --secondary-region;
#   2. the one an existing deployment uses (SecondaryRegion output of the primary
#      stack), so a redeploy or a destroy never moves it;
#   3. deploy only: chosen from this account's enabled Regions (select_secondary_region).
DEPLOYED_SECONDARY="$(get_output_or_empty "$PRIMARY_STACK" "$PRIMARY_REGION" SecondaryRegion)"
if [ -n "$SECONDARY_ARG" ]; then
    if [ -n "$DEPLOYED_SECONDARY" ] && [ "$DEPLOYED_SECONDARY" != "$SECONDARY_ARG" ]; then
        echo -e "\033[0;31mERROR: $PRIMARY_STACK is already deployed with secondary Region $DEPLOYED_SECONDARY, not $SECONDARY_ARG.\033[0m"
        echo "       Omit --secondary-region to keep it, or destroy this deployment first."
        exit 1
    fi
    SECONDARY_REGION="$SECONDARY_ARG"
elif [ -n "$DEPLOYED_SECONDARY" ]; then
    SECONDARY_REGION="$DEPLOYED_SECONDARY"
    echo "Secondary Region: $SECONDARY_REGION (from the deployed $PRIMARY_STACK)"
elif [ "$DESTROY" = "true" ]; then
    echo -e "\033[0;31mERROR: $PRIMARY_STACK was not found in $PRIMARY_REGION, so its secondary Region is unknown.\033[0m"
    echo "       To remove what is left, pass it explicitly: --secondary-region <region>"
    exit 1
else
    SECONDARY_REGION="$(select_secondary_region "$PRIMARY_REGION")"
    if [ -z "$SECONDARY_REGION" ]; then
        echo -e "\033[0;31mERROR: this account has no other enabled Region in the $(region_geography "$PRIMARY_REGION") geography of $PRIMARY_REGION.\033[0m"
        echo "       The multi variant needs both Regions in one geography: enable another one, or use the single variant."
        exit 1
    fi
    echo "Secondary Region: $SECONDARY_REGION (chosen from this account's enabled Regions; pass --secondary-region for another)"
fi
if [[ ! "$SECONDARY_REGION" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]$ ]] || [ "$SECONDARY_REGION" = "$PRIMARY_REGION" ]; then
    echo -e "\033[0;31mERROR: --secondary-region must be a Region code different from $PRIMARY_REGION (got: $SECONDARY_REGION)\033[0m"
    exit 1
fi
# Same geography as the primary. Checked on deploy only: a destroy must still be
# able to remove whatever is deployed.
if [ "$DESTROY" != "true" ] && [ "$(region_geography "$SECONDARY_REGION")" != "$(region_geography "$PRIMARY_REGION")" ]; then
    echo -e "\033[0;31mERROR: the secondary Region must be in the same geography as $PRIMARY_REGION ($(region_geography "$PRIMARY_REGION")-*); got $SECONDARY_REGION.\033[0m"
    exit 1
fi
export WA_DEMO_SECONDARY_REGION="$SECONDARY_REGION"

SECONDARY_STACK="WellArchitectedAgentReviewMultiSecondary-$SECONDARY_REGION"
DASHBOARD_STACK="WellArchitectedAgentReviewMultiDashboard-$PRIMARY_REGION"
CONTEXT_STACK="WellArchitectedAgentReviewMultiContext-$PRIMARY_REGION"

if [ "$DESTROY" = "true" ]; then
    # Reverse order: dashboard, context (releases the profile), secondary, primary
    # (the primary deletes the global table and its replica).
    NAMES="$DASHBOARD_STACK, $CONTEXT_STACK, $SECONDARY_STACK, $PRIMARY_STACK"
    echo -e "\033[0;33mDestroying $NAMES...\033[0m"
    deploy_in "$PRIMARY_REGION" --stack-name "$DASHBOARD_STACK" --skip-bootstrap --destroy
    deploy_in "$AGENT_REGION" --stack-name "$CONTEXT_STACK" --skip-bootstrap --destroy
    deploy_in "$SECONDARY_REGION" --stack-name "$SECONDARY_STACK" --skip-bootstrap --destroy
    deploy_in "$PRIMARY_REGION" --stack-name "$PRIMARY_STACK" --skip-bootstrap --destroy
    echo ""
    echo -e "\033[0;32m========================================\033[0m"
    echo -e "\033[0;32m  Cleanup Complete! (variant: multi)\033[0m"
    echo -e "\033[0;32m========================================\033[0m"
    echo -e "\033[0;36m  Destroyed:  $NAMES\033[0m"
    echo -e "\033[0;36m  Kept:       $PROFILE_STACK ($AGENT_REGION), shared by the applications\033[0m"
    echo -e "\033[0;33m  Remove it when no application is left: ./deploy-all.sh --variant profile --destroy\033[0m"
    exit 0
fi

resolve_admin_cidr
CONTEXT=(--cdk-context "adminCidr=$ADMIN_CIDR")
if [ -n "$DOMAIN" ]; then
    CONTEXT+=(--cdk-context "appDomainName=$DOMAIN" --cdk-context "route53ZoneName=$ZONE")
fi

# 1. Shared profile (created or updated), in the agent Region (bootstraps it).
deploy_profile

# 2. Primary: bootstraps the primary Region, deploys the region stack and the global table.
deploy_in "$PRIMARY_REGION" --stack-name "$PRIMARY_STACK" "${CONTEXT[@]}"

# 3. Secondary: bootstraps the secondary Region and deploys its region stack.
deploy_in "$SECONDARY_REGION" --stack-name "$SECONDARY_STACK" "${CONTEXT[@]}"

# 4. Context: registers the application in the profile (agent Region, bootstrapped in 1).
deploy_in "$AGENT_REGION" --stack-name "$CONTEXT_STACK" --skip-bootstrap

# 5. Dashboard: takes the ALB and target group dimensions of both Regions as context.
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
PROFILE_ARN="$(get_output "$PROFILE_STACK" "$AGENT_REGION" AgentProfileArn)"
DASHBOARD_URL="$(get_output "$DASHBOARD_STACK" "$PRIMARY_REGION" DashboardUrl)"

echo ""
echo -e "\033[0;32m========================================\033[0m"
echo -e "\033[0;32m  Deployment Complete! (variant: multi)\033[0m"
echo -e "\033[0;32m========================================\033[0m"
echo -e "\033[0;36m  Primary:        $PRIMARY_STACK ($PRIMARY_REGION)\033[0m"
echo -e "\033[0;36m  Secondary:      $SECONDARY_STACK ($SECONDARY_REGION)\033[0m"
echo -e "\033[0;36m  Context:        $CONTEXT_STACK ($AGENT_REGION)\033[0m"
echo -e "\033[0;36m  Profile:        $PROFILE_STACK ($AGENT_REGION)\033[0m"
echo -e "\033[0;36m  Admin CIDR:     $ADMIN_CIDR\033[0m"
echo -e "\033[0;36m  Primary ALB:    $PRIMARY_ALB\033[0m"
echo -e "\033[0;36m  Secondary ALB:  $SECONDARY_ALB\033[0m"
echo -e "\033[0;36m  DynamoDB table: $TABLE_NAME (global, both regions)\033[0m"
echo -e "\033[0;36m  Agent profile:  $PROFILE_ARN\033[0m"
echo -e "\033[0;36m  Dashboard:      $DASHBOARD_URL\033[0m"
echo ""
echo -e "\033[0;33m  Next: the first scheduled recommendations usually arrive within a few hours (at most 48 hours).\033[0m"
echo -e "\033[0;33m  Check progress: aws wellarchitected list-agent-recommendation-generations --profile-arn $PROFILE_ARN --region $AGENT_REGION\033[0m"
echo -e "\033[0;33m  Check eligibility: aws wellarchitected get-agent-profile --profile-arn $PROFILE_ARN --region $AGENT_REGION\033[0m"
echo -e "\033[0;33m  Cleanup: ./deploy-all.sh --variant multi --destroy, then ./deploy-all.sh --variant profile --destroy (same AWS_REGION and WA_AGENT_REGION)\033[0m"
