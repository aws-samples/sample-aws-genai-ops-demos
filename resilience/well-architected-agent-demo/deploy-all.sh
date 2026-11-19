#!/usr/bin/env bash
# Well-Architected Agent demo - deployment script (Bash)
#
# Deploys ONE variant: the workload under test, then the agent stack that reviews
# it. Uses the GenAI Ops Demo Library shared scripts, so it must run from
# <pillar>/<demo>/ inside the library checkout.
#
#   single (default)  deliberately non-optimized single-region workload:
#                     WAAgentDemoSingleWorkload-<region>
#   multi             active/active workload in two Regions with documented gaps:
#                     WAAgentDemoMultiWorkloadPrimary-<primary> and
#                     WAAgentDemoMultiWorkloadSecondary-<secondary>. The secondary
#                     Region comes from --secondary-region; otherwise, in us-* and
#                     eu-* Regions, the script picks one from the account's enabled
#                     Regions with the same Region-code prefix (alphabetical). Later
#                     runs reuse the deployed one.
#   agent stack       WAAgentDemoProfile-<agent-region>: roles, profile, goals and one
#                     application context, configured for the variant. Main stack.
#
# One variant at a time per account and agent Region: the agent stack records the
# deployed variant and Regions in its outputs. A deploy that does not match them
# stops and prints the destroy command; --destroy reads them, so it needs no option.
#
# Regions: the workload deploys in the Region resolved by the shared prerequisites
# check (AWS_REGION / AWS_DEFAULT_REGION). The agent stack lives in WA_AGENT_REGION,
# which defaults to the same Region. A profile can only be hosted in a few Regions;
# when yours is not one of them the check stops and tells you to set WA_AGENT_REGION.
#
# Usage:
#   ./deploy-all.sh                                  # single, admin CIDR = your public IP /32
#   ./deploy-all.sh --admin-cidr "<YOUR_IP>/32"
#   ./deploy-all.sh --variant multi
#   ./deploy-all.sh --variant multi --secondary-region us-west-2
#   ./deploy-all.sh --variant multi --domain app.example.com --zone example.com   # optional HTTPS
#   ./deploy-all.sh --destroy                        # remove the deployed variant
#   WA_AGENT_REGION=us-east-1 ./deploy-all.sh        # host the agent stack outside the workload Region
set -eo pipefail

VARIANT="single"
VARIANT_GIVEN="false"
ADMIN_CIDR=""
DOMAIN=""
ZONE=""
SECONDARY_ARG=""
DESTROY="false"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --variant)          VARIANT="${2:-}"; VARIANT_GIVEN="true"; shift 2 ;;
        --admin-cidr)       ADMIN_CIDR="${2:-}"; shift 2 ;;
        --domain)           DOMAIN="${2:-}"; shift 2 ;;
        --zone)             ZONE="${2:-}"; shift 2 ;;
        --secondary-region) SECONDARY_ARG="${2:-}"; shift 2 ;;
        --destroy)          DESTROY="true"; shift ;;
        *)
            echo "Usage: $0 [--variant single|multi] [--admin-cidr <YOUR_IP>/32] [--secondary-region <region>] [--domain <app.example.com> --zone <example.com>] [--destroy]"
            exit 1
            ;;
    esac
done

if [ "$VARIANT" != "single" ] && [ "$VARIANT" != "multi" ]; then
    echo -e "\033[0;31mERROR: --variant must be 'single' or 'multi'\033[0m"
    exit 1
fi
if [ "$DESTROY" != "true" ] && [ "$VARIANT" != "multi" ] && { [ -n "$DOMAIN" ] || [ -n "$ZONE" ]; }; then
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
# hosting Regions and stops when the agent Region is not one. Exports AWS_REGION,
# AWS_ACCOUNT_ID and WA_AGENT_REGION.
source "$SHARED_SCRIPTS/check-prerequisites.sh" --required-service wellarchitected-agent --require-cdk --min-aws-cli-version 2.31.13
REGION="$AWS_REGION"
AGENT_REGION="$WA_AGENT_REGION"
AGENT_STACK="WAAgentDemoProfile-$AGENT_REGION"

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

# Region-code prefix: the code without its last two segments (us-east-1 -> us,
# ap-southeast-1 -> ap, us-gov-west-1 -> us-gov). Both Regions of multi share it.
# It is not a data residency boundary (ap groups Tokyo, Mumbai and Sydney), so the
# script only picks a secondary Region by itself for us and eu. Same rule as
# Get-RegionPrefix in the PowerShell twin.
region_prefix() {
    printf '%s' "${1%-*-*}"
}

# Default secondary Region of multi, with no Region list in the demo: this
# account's enabled Regions (ec2 describe-regions, read-only) with the primary's
# prefix, primary excluded, sorted alphabetically (LC_ALL=C, as the ordinal sort in
# the PowerShell twin), Regions without opt-in first. "" when there is none.
select_secondary_region() {
    local primary="$1" prefix rows pick
    prefix="$(region_prefix "$primary")"
    rows="$(aws ec2 describe-regions --region "$primary" \
        --query "Regions[].[RegionName,OptInStatus]" --output text --no-cli-pager)" || return 0
    # Keep "name status" rows with the same prefix (name minus its last two segments).
    rows="$(printf '%s\n' "$rows" | awk -v p="$primary" -v g="$prefix" \
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

# What is deployed in this account and agent Region, read from the agent stack
# (read-only). Its Region is known, while a workload stack can be in any Region.
DEPLOYED_VARIANT="$(get_output_or_empty "$AGENT_STACK" "$AGENT_REGION" Variant)"
DEPLOYED_WORKLOAD="$(get_output_or_empty "$AGENT_STACK" "$AGENT_REGION" WorkloadRegion)"
DEPLOYED_SECONDARY="$(get_output_or_empty "$AGENT_STACK" "$AGENT_REGION" SecondaryRegion)"

# ===========================================================================
# Destroy: agent stack first, then the workload stacks
# ===========================================================================
if [ "$DESTROY" = "true" ]; then
    if [ -n "$DEPLOYED_VARIANT" ]; then
        if [ "$VARIANT_GIVEN" = "true" ] && [ "$VARIANT" != "$DEPLOYED_VARIANT" ]; then
            echo -e "\033[0;31mERROR: $AGENT_STACK ($AGENT_REGION) deploys variant $DEPLOYED_VARIANT, not $VARIANT. Run ./deploy-all.sh --destroy without --variant.\033[0m"
            exit 1
        fi
        VARIANT="$DEPLOYED_VARIANT"
        WORKLOAD_REGION="$DEPLOYED_WORKLOAD"
        SECONDARY_REGION="$DEPLOYED_SECONDARY"
        echo "Found $AGENT_STACK ($AGENT_REGION): variant $VARIANT, workload Region $WORKLOAD_REGION${SECONDARY_REGION:+, secondary $SECONDARY_REGION}"
    else
        # No agent stack: remove what is left of a partial deployment, from the
        # options (variant, AWS_REGION, and the secondary Region for multi).
        WORKLOAD_REGION="$REGION"
        echo -e "\033[0;33mNo $AGENT_STACK in $AGENT_REGION. Removing what is left of variant $VARIANT in $WORKLOAD_REGION.\033[0m"
        if [ "$VARIANT" = "multi" ]; then
            SECONDARY_REGION="$SECONDARY_ARG"
            if [ -z "$SECONDARY_REGION" ]; then
                SECONDARY_REGION="$(get_output_or_empty "WAAgentDemoMultiWorkloadPrimary-$WORKLOAD_REGION" "$WORKLOAD_REGION" SecondaryRegion)"
            fi
            if [ -z "$SECONDARY_REGION" ]; then
                echo -e "\033[0;31mERROR: the secondary Region is unknown. Pass it explicitly: --secondary-region <region>\033[0m"
                exit 1
            fi
        fi
    fi

    export WA_DEMO_VARIANT="$VARIANT"
    export WA_DEMO_PRIMARY_REGION="$WORKLOAD_REGION"
    export WA_AGENT_REGION="$AGENT_REGION"
    [ "$VARIANT" = "multi" ] && export WA_DEMO_SECONDARY_REGION="$SECONDARY_REGION"

    deploy_in "$AGENT_REGION" --stack-name "$AGENT_STACK" --skip-bootstrap --destroy
    if [ "$VARIANT" = "single" ]; then
        NAMES="WAAgentDemoSingleWorkload-$WORKLOAD_REGION"
        deploy_in "$WORKLOAD_REGION" --stack-name "$NAMES" --skip-bootstrap --destroy
    else
        # Secondary, then primary (the primary deletes the global table and its replica).
        S_STACK="WAAgentDemoMultiWorkloadSecondary-$SECONDARY_REGION"
        P_STACK="WAAgentDemoMultiWorkloadPrimary-$WORKLOAD_REGION"
        NAMES="$S_STACK $P_STACK"
        deploy_in "$SECONDARY_REGION" --stack-name "$S_STACK" --skip-bootstrap --destroy
        deploy_in "$WORKLOAD_REGION" --stack-name "$P_STACK" --skip-bootstrap --destroy
    fi
    echo ""
    echo -e "\033[0;32m========================================\033[0m"
    echo -e "\033[0;32m  Cleanup Complete! (variant: $VARIANT)\033[0m"
    echo -e "\033[0;32m========================================\033[0m"
    echo -e "\033[0;36m  Destroyed:  $AGENT_STACK ($AGENT_REGION)\033[0m"
    for n in $NAMES; do echo -e "\033[0;36m              $n\033[0m"; done
    exit 0
fi

# ===========================================================================
# Deploy: one variant per account and agent Region
# ===========================================================================
if [ -n "$DEPLOYED_VARIANT" ] && { [ "$DEPLOYED_VARIANT" != "$VARIANT" ] || [ "$DEPLOYED_WORKLOAD" != "$REGION" ]; }; then
    echo -e "\033[0;31mERROR: $AGENT_STACK ($AGENT_REGION) already deploys variant $DEPLOYED_VARIANT for workload Region $DEPLOYED_WORKLOAD.\033[0m"
    echo "       One variant at a time. Destroy it first, with the same WA_AGENT_REGION ($AGENT_REGION):"
    echo "         ./deploy-all.sh --destroy"
    exit 1
fi

resolve_admin_cidr
CONTEXT=(--cdk-context "adminCidr=$ADMIN_CIDR")
export WA_DEMO_VARIANT="$VARIANT"
export WA_DEMO_PRIMARY_REGION="$REGION"
export WA_AGENT_REGION="$AGENT_REGION"

# ===========================================================================
# Variant: single
# ===========================================================================
if [ "$VARIANT" = "single" ]; then
    WORKLOAD_STACK="WAAgentDemoSingleWorkload-$REGION"

    # 1. Workload (bootstraps its Region), 2. agent stack (bootstraps the agent
    #    Region when it differs).
    deploy_in "$REGION" --stack-name "$WORKLOAD_STACK" "${CONTEXT[@]}"
    AGENT_ARGS=(--stack-name "$AGENT_STACK" "${CONTEXT[@]}")
    [ "$AGENT_REGION" = "$REGION" ] && AGENT_ARGS+=(--skip-bootstrap)
    deploy_in "$AGENT_REGION" "${AGENT_ARGS[@]}"

    INSTANCE_IP="$(get_output "$WORKLOAD_STACK" "$REGION" InstancePublicIp)"
    DB_ENDPOINT="$(get_output "$WORKLOAD_STACK" "$REGION" DatabaseEndpoint)"
    BUCKET_NAME="$(get_output "$WORKLOAD_STACK" "$REGION" AssetBucketName)"
    PROFILE_ARN="$(get_output "$AGENT_STACK" "$AGENT_REGION" AgentProfileArn)"

    echo ""
    echo -e "\033[0;32m========================================\033[0m"
    echo -e "\033[0;32m  Deployment Complete! (variant: single)\033[0m"
    echo -e "\033[0;32m========================================\033[0m"
    echo -e "\033[0;36m  Workload:       $WORKLOAD_STACK ($REGION)\033[0m"
    echo -e "\033[0;36m  Agent:          $AGENT_STACK ($AGENT_REGION)\033[0m"
    echo -e "\033[0;36m  Admin CIDR:     $ADMIN_CIDR\033[0m"
    echo -e "\033[0;36m  Instance IP:    $INSTANCE_IP\033[0m"
    echo -e "\033[0;36m  DB endpoint:    $DB_ENDPOINT\033[0m"
    echo -e "\033[0;36m  Asset bucket:   $BUCKET_NAME\033[0m"
    echo -e "\033[0;36m  Agent profile:  $PROFILE_ARN\033[0m"
    echo ""
    echo -e "\033[0;33m  Next: the first scheduled recommendations usually arrive within a few hours (at most 48 hours).\033[0m"
    echo -e "\033[0;33m  Check progress: aws wellarchitected list-agent-recommendation-generations --profile-arn $PROFILE_ARN --region $AGENT_REGION\033[0m"
    echo -e "\033[0;33m  Check eligibility: aws wellarchitected get-agent-profile --profile-arn $PROFILE_ARN --region $AGENT_REGION\033[0m"
    echo -e "\033[0;33m  Cleanup: ./deploy-all.sh --destroy (same WA_AGENT_REGION)\033[0m"
    exit 0
fi

# ===========================================================================
# Variant: multi
# ===========================================================================
PRIMARY_REGION="$REGION"
PRIMARY_STACK="WAAgentDemoMultiWorkloadPrimary-$PRIMARY_REGION"
PREFIX="$(region_prefix "$PRIMARY_REGION")"

# Secondary Region, in this order:
#   1. --secondary-region;
#   2. the deployed one (agent stack, else the primary stack of a partial
#      deployment), so a redeploy never moves it;
#   3. in us-* and eu-* only: chosen from this account's enabled Regions.
if [ -z "$DEPLOYED_SECONDARY" ]; then
    DEPLOYED_SECONDARY="$(get_output_or_empty "$PRIMARY_STACK" "$PRIMARY_REGION" SecondaryRegion)"
fi
if [ -n "$SECONDARY_ARG" ]; then
    if [ -n "$DEPLOYED_SECONDARY" ] && [ "$DEPLOYED_SECONDARY" != "$SECONDARY_ARG" ]; then
        echo -e "\033[0;31mERROR: this deployment already uses secondary Region $DEPLOYED_SECONDARY, not $SECONDARY_ARG.\033[0m"
        echo "       Omit --secondary-region to keep it, or destroy it first: ./deploy-all.sh --destroy"
        exit 1
    fi
    SECONDARY_REGION="$SECONDARY_ARG"
elif [ -n "$DEPLOYED_SECONDARY" ]; then
    SECONDARY_REGION="$DEPLOYED_SECONDARY"
    echo "Secondary Region: $SECONDARY_REGION (from the deployed stacks)"
elif [ "$PREFIX" != "us" ] && [ "$PREFIX" != "eu" ]; then
    echo -e "\033[0;31mERROR: pass the secondary Region for $PRIMARY_REGION explicitly: --secondary-region <region>\033[0m"
    echo "       Outside us-* and eu-*, Regions with the same code prefix can be far apart (ap-* spans Tokyo to Sydney),"
    echo "       so the script does not choose one for you."
    exit 1
else
    SECONDARY_REGION="$(select_secondary_region "$PRIMARY_REGION")"
    if [ -z "$SECONDARY_REGION" ]; then
        echo -e "\033[0;31mERROR: this account has no other enabled $PREFIX-* Region besides $PRIMARY_REGION.\033[0m"
        echo "       Enable one, pass --secondary-region, or use the single variant."
        exit 1
    fi
    echo "Secondary Region: $SECONDARY_REGION (chosen from this account's enabled Regions; pass --secondary-region for another)"
fi
if [[ ! "$SECONDARY_REGION" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]$ ]] || [ "$SECONDARY_REGION" = "$PRIMARY_REGION" ]; then
    echo -e "\033[0;31mERROR: --secondary-region must be a Region code different from $PRIMARY_REGION (got: $SECONDARY_REGION)\033[0m"
    exit 1
fi
if [ "$(region_prefix "$SECONDARY_REGION")" != "$PREFIX" ]; then
    echo -e "\033[0;31mERROR: the secondary Region must have the same Region-code prefix as $PRIMARY_REGION ($PREFIX-*); got $SECONDARY_REGION.\033[0m"
    exit 1
fi
export WA_DEMO_SECONDARY_REGION="$SECONDARY_REGION"
SECONDARY_STACK="WAAgentDemoMultiWorkloadSecondary-$SECONDARY_REGION"

if [ -n "$DOMAIN" ]; then
    CONTEXT+=(--cdk-context "appDomainName=$DOMAIN" --cdk-context "route53ZoneName=$ZONE")
fi

# 1. Primary: bootstraps the primary Region, deploys its stack and the global table.
deploy_in "$PRIMARY_REGION" --stack-name "$PRIMARY_STACK" "${CONTEXT[@]}"
# 2. Secondary: bootstraps the secondary Region and deploys its stack.
deploy_in "$SECONDARY_REGION" --stack-name "$SECONDARY_STACK" "${CONTEXT[@]}"
# 3. Agent stack (bootstraps the agent Region when it is neither workload Region).
AGENT_ARGS=(--stack-name "$AGENT_STACK" "${CONTEXT[@]}")
if [ "$AGENT_REGION" = "$PRIMARY_REGION" ] || [ "$AGENT_REGION" = "$SECONDARY_REGION" ]; then
    AGENT_ARGS+=(--skip-bootstrap)
fi
deploy_in "$AGENT_REGION" "${AGENT_ARGS[@]}"

PRIMARY_ALB="$(get_output "$PRIMARY_STACK" "$PRIMARY_REGION" AlbDnsName)"
SECONDARY_ALB="$(get_output "$SECONDARY_STACK" "$SECONDARY_REGION" AlbDnsName)"
TABLE_NAME="$(get_output "$PRIMARY_STACK" "$PRIMARY_REGION" DynamoDbTableName)"
PROFILE_ARN="$(get_output "$AGENT_STACK" "$AGENT_REGION" AgentProfileArn)"

echo ""
echo -e "\033[0;32m========================================\033[0m"
echo -e "\033[0;32m  Deployment Complete! (variant: multi)\033[0m"
echo -e "\033[0;32m========================================\033[0m"
echo -e "\033[0;36m  Primary:        $PRIMARY_STACK ($PRIMARY_REGION)\033[0m"
echo -e "\033[0;36m  Secondary:      $SECONDARY_STACK ($SECONDARY_REGION)\033[0m"
echo -e "\033[0;36m  Agent:          $AGENT_STACK ($AGENT_REGION)\033[0m"
echo -e "\033[0;36m  Admin CIDR:     $ADMIN_CIDR\033[0m"
echo -e "\033[0;36m  Primary ALB:    $PRIMARY_ALB\033[0m"
echo -e "\033[0;36m  Secondary ALB:  $SECONDARY_ALB\033[0m"
echo -e "\033[0;36m  DynamoDB table: $TABLE_NAME (global, both regions)\033[0m"
echo -e "\033[0;36m  Agent profile:  $PROFILE_ARN\033[0m"
echo ""
echo -e "\033[0;33m  Next: the first scheduled recommendations usually arrive within a few hours (at most 48 hours).\033[0m"
echo -e "\033[0;33m  Check progress: aws wellarchitected list-agent-recommendation-generations --profile-arn $PROFILE_ARN --region $AGENT_REGION\033[0m"
echo -e "\033[0;33m  Check eligibility: aws wellarchitected get-agent-profile --profile-arn $PROFILE_ARN --region $AGENT_REGION\033[0m"
echo -e "\033[0;33m  Cleanup: ./deploy-all.sh --destroy (same WA_AGENT_REGION)\033[0m"
