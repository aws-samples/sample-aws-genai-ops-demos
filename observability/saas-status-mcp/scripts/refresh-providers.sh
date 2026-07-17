#!/usr/bin/env bash
# refresh-providers.sh — Update the live provider registry with NO redeploy.
#
# The running MCP server reads providers.json from S3 via a conditional GET,
# so pushing a new version of the file is all it takes. The server picks up
# the change within one poll interval (default 60s). No zip, no CDK, no restart.
#
# Usage: edit agent/providers.json, then run:  ./refresh-providers.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

source "${REPO_ROOT}/shared/scripts/check-prerequisites.sh" > /dev/null
REGION="${AWS_REGION}"
ACCOUNT=$(aws sts get-caller-identity --query "Account" --output text)

BUCKET_NAME="saas-status-mcp-${ACCOUNT}-${REGION}"
KEY="config/providers.json"

echo "Uploading agent/providers.json to s3://${BUCKET_NAME}/${KEY} ..."
aws s3 cp "${SCRIPT_DIR}/../agent/providers.json" "s3://${BUCKET_NAME}/${KEY}" --quiet

echo ""
echo "Done. Provider registry published to the live server."
echo "The running MCP server will pick up the change within its poll interval (~60s)."
echo "No redeploy required."
