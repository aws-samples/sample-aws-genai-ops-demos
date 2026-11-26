#!/bin/bash
# S3 data protection advisor - complete teardown script
# macOS/Linux version - mirrors destroy-all.ps1
#   --mock-data   delete the mock estate first (tagged buckets and role only)
# The Org stack (multi-account rollout) is removed first when it exists: deleting its StackSet removes the spoke
# role from every member account. Spoke roles deployed by hand are not visible from here (see the final note).

set -e

MOCK_DATA=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --mock-data) MOCK_DATA=true; shift ;;
        *) echo "Unknown option: $1"; echo "Usage: $0 [--mock-data]"; exit 1 ;;
    esac
done

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "\033[0;36m=== S3 data protection advisor teardown ===\033[0m"

source ../../shared/scripts/check-prerequisites.sh --skip-service-check --min-python-version "3.12" --require-cdk --min-cdk-version "2.1030.0"

region="$AWS_REGION"
org_stack="S3DataProtectionOrg-$region"

if [ "$MOCK_DATA" = true ]; then
    echo -e "\n\033[0;33mDeleting the mock estate...\033[0m"
    if [ ! -d backend/.venv ]; then
        python3 -m venv backend/.venv
    fi
    backend/.venv/bin/python -m pip install --quiet -r backend/requirements.txt
    (cd backend && ../backend/.venv/bin/python -m s3dp.cli seed-cleanup --yes)
fi

# The Org stack only exists in the app with its context, so `cdk destroy --all` would not see it.
org_targets=$(aws cloudformation describe-stacks --stack-name "$org_stack" --region "$region" \
    --query "Stacks[0].Outputs[?OutputKey=='OrgTargets'].OutputValue" --output text --no-cli-pager 2>/dev/null || true)
if [ -n "$org_targets" ] && [ "$org_targets" != "None" ]; then
    hub_account=$(aws cloudformation describe-stacks --stack-name "$org_stack" --region "$region" \
        --query "Stacks[0].Outputs[?OutputKey=='HubAccountId'].OutputValue" --output text --no-cli-pager)
    echo -e "\n\033[0;33mDestroying the organization stack...\033[0m"
    echo -e "\033[0;90m      (Deletes the StackSet and the spoke role in every account under $org_targets)\033[0m"
    ../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "$org_stack" --skip-bootstrap --destroy \
        --cdk-context "orgOnly=true" --cdk-context "orgTargets=$org_targets" --cdk-context "hubAccountId=$hub_account"
fi

echo -e "\n\033[0;33mDestroying the stacks in $AWS_REGION...\033[0m"
(cd cdk && npm install && npx cdk destroy --all --force --no-cli-pager)

echo -e "\n\033[0;32mTeardown complete\033[0m"
echo -e "\033[0;90m  Spoke roles deployed by hand (--accounts) stay in their member accounts. Remove each, with that account's credentials:\033[0m"
echo "     AWS_REGION=$region AWS_DEFAULT_REGION=$region ../../shared/scripts/deploy-cdk.sh --cdk-directory cdk --stack-name S3DataProtectionSpoke --skip-bootstrap --destroy --cdk-context spoke=true --cdk-context hubAccountId=<HUB_ACCOUNT_ID>"
