#!/bin/bash
# G.O.A.T. Demo Scenario B - CloudWatch Apr 1 Incident Correlation
#
# Creates AWS resources that correlate with the real CloudWatch health event
# from April 1, 2026, enabling cross-domain incident correlation:
# - 1x DynamoDB table (goat-demo-app-data) with on-demand billing
# - 1x resolved Support case referencing CloudWatch monitoring gaps
#
# All resources are tagged with goat-demo=true for cleanup.
# Script is idempotent - safe to re-run after partial failures.
#
# Usage: ./setup-scenario-b.sh

set -o pipefail

# ---------------------------------------------------------------------------
# Color helpers (matching deploy-all.sh patterns)
# ---------------------------------------------------------------------------
print_cyan()    { echo -e "\033[0;36m$1\033[0m"; }
print_green()   { echo -e "\033[0;32m$1\033[0m"; }
print_yellow()  { echo -e "\033[0;33m$1\033[0m"; }
print_red()     { echo -e "\033[0;31m$1\033[0m"; }
print_gray()    { echo -e "\033[0;90m$1\033[0m"; }
print_magenta() { echo -e "\033[0;35m$1\033[0m"; }

# ---------------------------------------------------------------------------
# Track created/existing resources for summary
# ---------------------------------------------------------------------------
DYNAMODB_TABLE=""
SUPPORT_CASE_ID=""
WARNINGS=()

# ---------------------------------------------------------------------------
# 1. Verify AWS credentials
# ---------------------------------------------------------------------------
print_cyan "=== G.O.A.T. Demo Scenario B - CloudWatch Apr 1 Incident Correlation ==="
echo ""
print_yellow "Verifying AWS credentials..."

ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text 2>/dev/null)
if [ $? -ne 0 ] || [ -z "$ACCOUNT_ID" ]; then
    print_red "ERROR: AWS credentials not configured."
    print_red "Run 'aws configure' or set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY."
    exit 1
fi
print_green "  Authenticated to account: $ACCOUNT_ID"

# ---------------------------------------------------------------------------
# 2. Detect region
# ---------------------------------------------------------------------------
print_yellow "Detecting AWS region..."

REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-}}"
if [ -z "$REGION" ]; then
    REGION=$(aws configure get region 2>/dev/null)
fi
if [ -z "$REGION" ]; then
    REGION="us-east-1"
    print_yellow "  No region configured, falling back to us-east-1"
fi
print_green "  Region: $REGION"
echo ""

# ---------------------------------------------------------------------------
# 3. Create DynamoDB table (idempotent)
# ---------------------------------------------------------------------------
print_magenta "--- DynamoDB Table ---"

print_yellow "Checking for existing goat-demo-app-data table..."
DYNAMODB_TABLE=$(aws dynamodb describe-table \
    --table-name goat-demo-app-data \
    --query "Table.TableName" --output text --region "$REGION" 2>/dev/null)

if [ -n "$DYNAMODB_TABLE" ] && [ "$DYNAMODB_TABLE" != "None" ]; then
    print_green "  DynamoDB table already exists: $DYNAMODB_TABLE"
else
    print_yellow "Creating DynamoDB table goat-demo-app-data (on-demand billing)..."
    DYNAMODB_TABLE=$(aws dynamodb create-table \
        --table-name goat-demo-app-data \
        --attribute-definitions AttributeName=pk,AttributeType=S \
        --key-schema AttributeName=pk,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST \
        --tags Key=goat-demo,Value=true Key=goat-scenario,Value=b Key=Name,Value=goat-demo-app-data Key=auto-delete,Value=no \
        --query "TableDescription.TableName" --output text --region "$REGION" 2>&1)
    if [ $? -ne 0 ]; then
        print_red "  WARNING: Failed to create DynamoDB table: $DYNAMODB_TABLE"
        WARNINGS+=("DynamoDB table creation failed")
        DYNAMODB_TABLE=""
    else
        print_green "  Created DynamoDB table: $DYNAMODB_TABLE"
    fi
fi

echo ""

# ---------------------------------------------------------------------------
# 4. Create Support case (if Support plan is active)
# ---------------------------------------------------------------------------
print_magenta "--- Support Case ---"

print_yellow "Detecting Support plan..."
SUPPORT_CHECK=$(aws support describe-services --query "services[0].code" --output text --region us-east-1 2>&1)

if echo "$SUPPORT_CHECK" | grep -qi "SubscriptionRequiredException"; then
    print_yellow "  WARNING: No Business or Enterprise Support plan detected."
    print_yellow "  Skipping Support case creation. To enable this feature, upgrade your Support plan."
    WARNINGS+=("Support case skipped - no Support plan")
    SUPPORT_CASE_ID="skipped (no Support plan)"
else
    print_yellow "Creating Support case..."
    SUPPORT_CASE_ID=$(aws support create-case \
        --subject "CloudWatch monitoring gaps and missing alarms on Apr 1 - G.O.A.T. demo" \
        --communication-body "Our monitoring infrastructure experienced gaps on April 1, 2026 due to the CloudWatch planned lifecycle event (AWS_CLOUDWATCH_PLANNED_LIFECYCLE_EVENT). Several CloudWatch alarms and dashboards were affected. The DynamoDB table 'goat-demo-app-data' metrics stopped reporting during this period. This case was created for demo purposes by the G.O.A.T. provisioning scripts." \
        --service-code "amazon-cloudwatch" \
        --category-code "other" \
        --severity-code "low" \
        --language "en" \
        --query "caseId" --output text --region us-east-1 2>&1)

    if [ $? -ne 0 ]; then
        print_red "  WARNING: Failed to create Support case: $SUPPORT_CASE_ID"
        WARNINGS+=("Support case creation failed")
        SUPPORT_CASE_ID=""
    else
        print_green "  Created Support case: $SUPPORT_CASE_ID"

        # Add demo-purpose communication
        aws support add-communication-to-case \
            --case-id "$SUPPORT_CASE_ID" \
            --communication-body "This Support case was created automatically by the G.O.A.T. demo provisioning scripts for demonstration purposes only. It is being resolved immediately. No action is needed from AWS Support." \
            --region us-east-1 2>/dev/null

        # Immediately resolve the case
        print_yellow "  Resolving Support case..."
        RESOLVE_OUTPUT=$(aws support resolve-case --case-id "$SUPPORT_CASE_ID" --region us-east-1 2>&1)
        if [ $? -ne 0 ]; then
            print_red "  WARNING: Failed to resolve Support case $SUPPORT_CASE_ID"
            print_red "  Please close it manually via the AWS Console: https://console.aws.amazon.com/support/home"
            WARNINGS+=("Support case resolve failed - close manually: $SUPPORT_CASE_ID")
        else
            print_green "  Support case resolved: $SUPPORT_CASE_ID"
        fi
    fi
fi

echo ""

# ---------------------------------------------------------------------------
# 5. Summary
# ---------------------------------------------------------------------------
print_green "========================================"
print_green "  G.O.A.T. Scenario B Setup Complete!"
print_green "========================================"
echo ""
print_cyan "  Region:              $REGION"

if [ -n "$DYNAMODB_TABLE" ] && [ "$DYNAMODB_TABLE" != "None" ]; then
    print_cyan "  DynamoDB Table:      $DYNAMODB_TABLE"
fi
if [ -n "$SUPPORT_CASE_ID" ]; then
    print_cyan "  Support Case:        $SUPPORT_CASE_ID"
fi

if [ ${#WARNINGS[@]} -gt 0 ]; then
    echo ""
    print_yellow "  Warnings:"
    for w in "${WARNINGS[@]}"; do
        print_yellow "    - $w"
    done
fi

echo ""
print_cyan "  Suggested Demo Query:"
print_green "    \"We had monitoring gaps on April 1st - was there an AWS issue?\""
echo ""
print_gray "  To clean up all demo resources:"
print_gray "    ./cleanup-scenarios.sh     (Bash)"
print_gray "    .\\cleanup-scenarios.ps1    (PowerShell)"
echo ""
