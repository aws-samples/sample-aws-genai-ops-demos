#!/bin/bash
# GenAI Ops Demo Library - Multi-account (hub-and-spoke) prerequisites check
#
# Read-only preflight for demos that scan several accounts of an AWS Organization
# from one "hub" account: the hub lists the organization's accounts and assumes a
# read-only role that a CloudFormation StackSet has placed in every member
# account ("spoke"). Three things must be true for that to work, and none of
# them can be fixed by a deploy script because they are management-account
# actions:
#
#   1. The hub can list the organization's accounts: it IS the management
#      account, or the management account delegated the Organizations read APIs
#      to it through the organization's resource-based policy.
#   2. Trusted access is enabled between AWS Organizations and CloudFormation
#      StackSets (service-managed permissions).
#   3. The hub may run service-managed StackSets: it is the management account
#      or a registered delegated administrator for StackSets.
#
# This script only reports; every FAIL comes with the exact command a
# management-account administrator has to run. Nothing is created or changed.
#
# Exit codes (so deploy scripts can branch):
#   0  multi-account scanning and StackSet rollout both possible
#   2  the hub cannot list accounts (multi-account mode impossible; use a manual
#      account list or fix item 1)
#   3  accounts can be listed but the hub cannot roll the spoke role out itself
#      (deploy anyway, have the management account run the StackSet once)
#   1  no AWS credentials / not in an organization / unexpected error
#
# Usage:
#   source ../../shared/scripts/check-org-access.sh            # report + exported vars
#   ../../shared/scripts/check-org-access.sh --json            # machine-readable only
#
# Exports for the calling script (when sourced): ORG_ID, ORG_MANAGEMENT_ACCOUNT_ID,
# ORG_IS_MANAGEMENT, ORG_CAN_LIST_ACCOUNTS, ORG_CAN_ROLLOUT_STACKSETS

JSON_OUTPUT=false
[[ "$1" == "--json" || "$1" == "-j" ]] && JSON_OUTPUT=true

STACKSETS_PRINCIPAL="member.org.stacksets.cloudformation.amazonaws.com"

# Organizations read APIs a hub needs (used in the fix command). The last two
# let the hub verify items 2 and 3 itself.
ORG_READ_ACTIONS='"organizations:DescribeOrganization","organizations:ListAccounts","organizations:DescribeAccount","organizations:ListRoots","organizations:ListOrganizationalUnitsForParent","organizations:ListParents","organizations:DescribeOrganizationalUnit","organizations:ListTagsForResource","organizations:ListAWSServiceAccessForOrganization","organizations:ListDelegatedAdministrators"'

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; GRAY='\033[0;90m'; NC='\033[0m'

say() { [[ "$JSON_OUTPUT" == true ]] || echo -e "$1"; }

# Run an AWS CLI command; sets AWS_OUT (stdout) and AWS_ERR_CODE (exception name or "")
aws_try() {
    AWS_OUT=$(aws "$@" --output json --no-cli-pager 2>&1)
    if [[ $? -eq 0 ]]; then
        AWS_ERR_CODE=""
        return 0
    fi
    AWS_ERR_CODE=$(echo "$AWS_OUT" | grep -oE '\([A-Za-z]+Exception\)' | head -1 | tr -d '()')
    [[ -z "$AWS_ERR_CODE" ]] && AWS_ERR_CODE="Error"
    AWS_OUT=""
    return 1
}

# JSON field extraction without jq (single-level keys, string values)
json_get() { echo "$1" | grep -oE "\"$2\": *\"[^\"]*\"" | head -1 | sed -E 's/.*: *"([^"]*)"/\1/'; }

ACCOUNT_ID=""; CALLER_ARN=""; ORG_ID=""; ORG_MANAGEMENT_ACCOUNT_ID=""
ORG_IS_MANAGEMENT=false; ORG_CAN_LIST_ACCOUNTS=false; STACKSETS_TRUSTED_ACCESS="unknown"; ORG_CAN_ROLLOUT_STACKSETS=false
CHECKS_JSON=""
EXIT_CODE=1

add_check() {  # name status detail fix
    local color="$GRAY"
    case "$2" in OK) color="$GREEN";; FAIL) color="$RED";; WARN) color="$YELLOW";; esac
    say "      ${color}$2: $1 - $3${NC}"
    [[ -n "$4" ]] && say "      ${CYAN}Fix (management account): $4${NC}"
    local fix_json; fix_json=$(printf '%s' "$4" | sed 's/\\/\\\\/g; s/"/\\"/g')
    [[ -n "$CHECKS_JSON" ]] && CHECKS_JSON+=","
    CHECKS_JSON+="{\"name\":\"$1\",\"status\":\"$2\",\"detail\":\"$3\",\"fix\":\"$fix_json\"}"
}

emit_json() {
    [[ "$JSON_OUTPUT" == true ]] || return
    cat <<EOF
{"account_id":"$ACCOUNT_ID","caller_arn":"$CALLER_ARN","organization_id":"$ORG_ID","management_account_id":"$ORG_MANAGEMENT_ACCOUNT_ID","is_management_account":$ORG_IS_MANAGEMENT,"can_list_accounts":$ORG_CAN_LIST_ACCOUNTS,"stacksets_trusted_access":"$STACKSETS_TRUSTED_ACCESS","can_rollout_stacksets":$ORG_CAN_ROLLOUT_STACKSETS,"checks":[$CHECKS_JSON],"exit_code":$EXIT_CODE}
EOF
}

finish() {
    emit_json
    export ORG_ID ORG_MANAGEMENT_ACCOUNT_ID ORG_IS_MANAGEMENT ORG_CAN_LIST_ACCOUNTS ORG_CAN_ROLLOUT_STACKSETS
    # `return` when sourced, `exit` when executed
    if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then return $EXIT_CODE; else exit $EXIT_CODE; fi
}

say "${CYAN}=== Multi-account (hub-and-spoke) prerequisites check (Shared Script) ===${NC}"

# --- Who am I ---------------------------------------------------------------
say "\n${YELLOW}Identifying the hub account...${NC}"
if ! aws_try sts get-caller-identity; then
    say "      ${RED}ERROR: AWS credentials are not configured or have expired${NC}"
    finish; return 2>/dev/null || exit 1
fi
ACCOUNT_ID=$(json_get "$AWS_OUT" Account)
CALLER_ARN=$(json_get "$AWS_OUT" Arn)
say "      ${GREEN}Hub account: $ACCOUNT_ID ($CALLER_ARN)${NC}"

# --- In an organization? Management account? --------------------------------
say "\n${YELLOW}Checking AWS Organizations membership...${NC}"
if aws_try organizations describe-organization; then
    ORG_ID=$(json_get "$AWS_OUT" Id)
    ORG_MANAGEMENT_ACCOUNT_ID=$(json_get "$AWS_OUT" MasterAccountId)
    if [[ "$ORG_MANAGEMENT_ACCOUNT_ID" == "$ACCOUNT_ID" ]]; then
        ORG_IS_MANAGEMENT=true
        add_check "Organization membership" "OK" "organization $ORG_ID; this account is the MANAGEMENT account" ""
        say "      ${GRAY}INFO: AWS recommends keeping workloads out of the management account; a member account as hub is the safer choice${NC}"
    else
        add_check "Organization membership" "OK" "organization $ORG_ID, management account $ORG_MANAGEMENT_ACCOUNT_ID" ""
    fi
elif [[ "$AWS_ERR_CODE" == "AWSOrganizationsNotInUseException" ]]; then
    add_check "Organization membership" "FAIL" "this account is not part of an AWS Organization" "Multi-account scanning needs an organization; single-account mode still works"
    say "\n${YELLOW}Result: single-account only.${NC}"
    EXIT_CODE=1; finish; return 2>/dev/null || exit 1
else
    add_check "Organization membership" "WARN" "cannot read the organization ($AWS_ERR_CODE); assuming member account" ""
fi

FIX_LIST_ACCOUNTS="aws organizations put-resource-policy --content '{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"GenAiOpsDemoHubReadsOrganization\",\"Effect\":\"Allow\",\"Principal\":{\"AWS\":\"arn:aws:iam::${ACCOUNT_ID}:root\"},\"Action\":[${ORG_READ_ACTIONS}],\"Resource\":\"*\"}]}'"

# --- 1. Can the hub list accounts? -----------------------------------------
say "\n${YELLOW}Checking account listing (organizations:ListAccounts)...${NC}"
if aws_try organizations list-accounts --max-items 1; then
    ORG_CAN_LIST_ACCOUNTS=true
    how="delegated through the organization resource policy"; [[ "$ORG_IS_MANAGEMENT" == true ]] && how="management account"
    add_check "Account listing" "OK" "ListAccounts allowed ($how)" ""
else
    add_check "Account listing" "FAIL" "ListAccounts denied ($AWS_ERR_CODE)" "$FIX_LIST_ACCOUNTS"
    say "      ${GRAY}Docs: https://docs.aws.amazon.com/organizations/latest/userguide/orgs_delegate_policies.html${NC}"
fi

# --- 2. Trusted access for StackSets ---------------------------------------
say "\n${YELLOW}Checking CloudFormation StackSets trusted access...${NC}"
if aws_try organizations list-aws-service-access-for-organization; then
    if echo "$AWS_OUT" | grep -q "\"$STACKSETS_PRINCIPAL\""; then
        STACKSETS_TRUSTED_ACCESS=true
        add_check "StackSets trusted access" "OK" "service-managed StackSets enabled for the organization" ""
    else
        STACKSETS_TRUSTED_ACCESS=false
        add_check "StackSets trusted access" "FAIL" "trusted access not enabled for $STACKSETS_PRINCIPAL" "aws organizations enable-aws-service-access --service-principal $STACKSETS_PRINCIPAL"
    fi
else
    add_check "StackSets trusted access" "WARN" "cannot verify ($AWS_ERR_CODE); needs organizations:ListAWSServiceAccessForOrganization" ""
fi

# --- 3. Can the hub run service-managed StackSets? --------------------------
say "\n${YELLOW}Checking StackSets delegated administrator...${NC}"
if [[ "$ORG_IS_MANAGEMENT" == true ]]; then
    if [[ "$STACKSETS_TRUSTED_ACCESS" == true ]]; then
        ORG_CAN_ROLLOUT_STACKSETS=true
        add_check "StackSets rollout" "OK" "management account can create service-managed StackSets" ""
    else
        add_check "StackSets rollout" "FAIL" "blocked until trusted access is enabled (see above)" ""
    fi
elif aws_try organizations list-delegated-administrators --service-principal "$STACKSETS_PRINCIPAL"; then
    if echo "$AWS_OUT" | grep -q "\"Id\": *\"$ACCOUNT_ID\""; then
        [[ "$STACKSETS_TRUSTED_ACCESS" != false ]] && ORG_CAN_ROLLOUT_STACKSETS=true
        add_check "StackSets rollout" "OK" "this account is a delegated administrator for StackSets" ""
    else
        add_check "StackSets rollout" "FAIL" "this account is not a StackSets delegated administrator" "aws organizations register-delegated-administrator --account-id $ACCOUNT_ID --service-principal $STACKSETS_PRINCIPAL"
        say "      ${GRAY}Docs: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/stacksets-orgs-delegated-admin.html${NC}"
    fi
else
    add_check "StackSets rollout" "WARN" "cannot verify ($AWS_ERR_CODE); needs organizations:ListDelegatedAdministrators" ""
fi

# --- Verdict ----------------------------------------------------------------
if [[ "$ORG_CAN_LIST_ACCOUNTS" != true ]]; then
    EXIT_CODE=2
    say "\n${RED}Result: multi-account scanning NOT possible from this account (cannot list accounts).${NC}"
    say "${YELLOW}        Either run the fix above in the management account, or use a manual account list.${NC}"
elif [[ "$ORG_CAN_ROLLOUT_STACKSETS" != true ]]; then
    EXIT_CODE=3
    say "\n${YELLOW}Result: accounts can be listed, but this account cannot roll the spoke role out itself.${NC}"
    say "${YELLOW}        Deploy the demo; then have the management account deploy the spoke role StackSet once (see the demo README).${NC}"
else
    EXIT_CODE=0
    say "\n${GREEN}Result: multi-account scanning and StackSet rollout are both possible from this account.${NC}"
fi

finish
