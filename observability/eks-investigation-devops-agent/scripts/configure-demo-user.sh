#!/bin/bash
# Creates or repairs the demo Cognito user and proves the printed credentials work.
# Outputs only the Cognito sub UUID; progress goes to stderr so callers can capture it.
set -euo pipefail

USER_POOL_ID="${1:?Usage: configure-demo-user.sh <user-pool-id> <client-id> <region>}"
CLIENT_ID="${2:?Usage: configure-demo-user.sh <user-pool-id> <client-id> <region>}"
REGION="${3:?Usage: configure-demo-user.sh <user-pool-id> <client-id> <region>}"
USERNAME="${DEMO_USERNAME:-demo-merchant-1}"
EMAIL="${DEMO_EMAIL:-demo@helios-electronics.com}"
PASSWORD="${DEMO_PASSWORD:-DemoPass2026!}"
FULL_NAME="${DEMO_FULL_NAME:-Helios Electronics Demo Merchant}"
GROUP_NAME="${DEMO_GROUP_NAME:-Merchants}"
ATTRIBUTES_JSON=$(printf '[{"Name":"email","Value":"%s"},{"Name":"email_verified","Value":"true"},{"Name":"name","Value":"%s"}]' "$EMAIL" "$FULL_NAME")

log() { echo "$@" >&2; }

set +e
USER_CHECK=$(aws cognito-idp admin-get-user \
    --user-pool-id "$USER_POOL_ID" \
    --username "$USERNAME" \
    --region "$REGION" \
    --no-cli-pager 2>&1)
USER_CHECK_EXIT=$?
set -e

if [ $USER_CHECK_EXIT -eq 0 ]; then
    log "  Cognito user '$USERNAME' already exists; refreshing credentials."
elif echo "$USER_CHECK" | grep -q 'UserNotFoundException'; then
    log "  Creating Cognito user '$USERNAME'..."
    aws cognito-idp admin-create-user \
        --user-pool-id "$USER_POOL_ID" \
        --username "$USERNAME" \
        --user-attributes "$ATTRIBUTES_JSON" \
        --temporary-password "$PASSWORD" \
        --message-action SUPPRESS \
        --region "$REGION" \
        --no-cli-pager >/dev/null
else
    log "  ERROR: Could not inspect Cognito user '$USERNAME': $USER_CHECK"
    exit 1
fi

aws cognito-idp admin-update-user-attributes \
    --user-pool-id "$USER_POOL_ID" \
    --username "$USERNAME" \
    --user-attributes "$ATTRIBUTES_JSON" \
    --region "$REGION" \
    --no-cli-pager >/dev/null

aws cognito-idp admin-enable-user \
    --user-pool-id "$USER_POOL_ID" \
    --username "$USERNAME" \
    --region "$REGION" \
    --no-cli-pager >/dev/null

aws cognito-idp admin-set-user-password \
    --user-pool-id "$USER_POOL_ID" \
    --username "$USERNAME" \
    --password "$PASSWORD" \
    --permanent \
    --region "$REGION" \
    --no-cli-pager >/dev/null

aws cognito-idp admin-add-user-to-group \
    --user-pool-id "$USER_POOL_ID" \
    --username "$USERNAME" \
    --group-name "$GROUP_NAME" \
    --region "$REGION" \
    --no-cli-pager >/dev/null

USER_STATE=$(aws cognito-idp admin-get-user \
    --user-pool-id "$USER_POOL_ID" \
    --username "$USERNAME" \
    --region "$REGION" \
    --query '[Enabled,UserStatus]' \
    --output text \
    --no-cli-pager)
if [ "$USER_STATE" != $'True\tCONFIRMED' ]; then
    log "  ERROR: Cognito user is not login-ready: $USER_STATE"
    exit 1
fi

AUTH_CHECK=$(aws cognito-idp initiate-auth \
    --auth-flow USER_PASSWORD_AUTH \
    --client-id "$CLIENT_ID" \
    --auth-parameters "USERNAME=$USERNAME,PASSWORD=$PASSWORD" \
    --region "$REGION" \
    --query 'AuthenticationResult.ExpiresIn' \
    --output text \
    --no-cli-pager)
if [ -z "$AUTH_CHECK" ] || [ "$AUTH_CHECK" = "None" ]; then
    log "  ERROR: Cognito credential smoke test failed."
    exit 1
fi

SUB=$(aws cognito-idp admin-get-user \
    --user-pool-id "$USER_POOL_ID" \
    --username "$USERNAME" \
    --query "UserAttributes[?Name=='sub'].Value" \
    --output text \
    --region "$REGION" \
    --no-cli-pager)
if [ -z "$SUB" ] || [ "$SUB" = "None" ]; then
    log "  ERROR: Could not resolve Cognito sub for '$USERNAME'."
    exit 1
fi

log "  Cognito login verified (Enabled=true, Status=CONFIRMED, Group=$GROUP_NAME)."
printf '%s\n' "$SUB"
