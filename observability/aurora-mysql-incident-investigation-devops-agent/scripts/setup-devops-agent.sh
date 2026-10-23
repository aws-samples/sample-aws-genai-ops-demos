#!/usr/bin/env bash
# setup-devops-agent.sh — Hands-free Amazon DevOps Agent setup for the Aurora demo.
#
# Creates (or reuses) the Agent Space, IAM roles, Operator App, AWS-account
# association, and a generic webhook — entirely via the AWS SDK. The webhook URL
# and secret are written to .devops-agent.env so `deploy-all.sh` picks them up
# automatically (you do not need to copy/paste them).
#
# If your AWS SDK build does not include the DevOps Agent API, the script prints
# the equivalent one-time console steps instead.
#
# Usage:
#   ./setup-devops-agent.sh [region] [--space-name NAME] [--with-mcp]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEMO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

REGION=""
SPACE_NAME="aurora-demo"
WITH_MCP=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --space-name) SPACE_NAME="$2"; shift 2;;
    --with-mcp)   WITH_MCP="yes"; shift;;
    -h|--help) echo "Usage: $0 [region] [--space-name NAME] [--with-mcp]"; exit 0;;
    *) REGION="$1"; shift;;
  esac
done
REGION="${REGION:-${AWS_DEFAULT_REGION:-${AWS_REGION:-$(aws configure get region 2>/dev/null)}}}"
[[ -z "$REGION" ]] && { echo "ERROR: region required (arg 1 or AWS_DEFAULT_REGION)"; exit 1; }

PY="$(command -v python3 || command -v python)"
[[ -z "$PY" ]] && { echo "ERROR: python3 is required"; exit 1; }
# Ensure the AWS SDK is available for the DevOps Agent API calls.
"$PY" -c 'import boto3' 2>/dev/null || {
  echo "  Installing boto3 for $PY ..."
  "$PY" -m pip install --quiet --disable-pip-version-check boto3
}

# ---------------------------------------------------------------------------
echo "==> Configuring Amazon DevOps Agent (region: $REGION)..."
# ---------------------------------------------------------------------------
"$PY" "$SCRIPT_DIR/devops_agent_setup.py" --region "$REGION" --space-name "$SPACE_NAME"

# ---------------------------------------------------------------------------
# Optional: deploy the MCP business-context server and print how to register it.
# ---------------------------------------------------------------------------
if [[ "$WITH_MCP" == "yes" ]]; then
  echo ""
  echo "==> Deploying MCP business-context server (AuroraDemoMcpServer-$REGION)..."
  CDK_DIR="$DEMO_DIR/infrastructure/cdk"
  export PYTHONPATH="$REPO_ROOT"
  [[ -d "$CDK_DIR/.venv" ]] || python3 -m venv "$CDK_DIR/.venv"
  # shellcheck disable=SC1091
  source "$CDK_DIR/.venv/bin/activate"
  pip install -q -r "$CDK_DIR/requirements.txt"
  ( cd "$CDK_DIR" && npx -y cdk deploy "AuroraDemoMcpServer-$REGION" --require-approval never --no-cli-pager )

  MCP_ENDPOINT=$(aws cloudformation describe-stacks --stack-name "AuroraDemoMcpServer-$REGION" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='McpEndpoint'].OutputValue" --output text --no-cli-pager 2>/dev/null || true)
  API_KEY_ID=$(aws cloudformation describe-stacks --stack-name "AuroraDemoMcpServer-$REGION" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ApiKeyId'].OutputValue" --output text --no-cli-pager 2>/dev/null || true)
  if [[ -n "$MCP_ENDPOINT" && "$MCP_ENDPOINT" != "None" ]]; then
    echo ""
    echo "  Register this MCP server in the Agent Space (Capabilities > MCP servers):"
    echo "    Endpoint : $MCP_ENDPOINT"
    if [[ -n "$API_KEY_ID" && "$API_KEY_ID" != "None" ]]; then
      API_KEY_VALUE=$(aws apigateway get-api-key --api-key "$API_KEY_ID" --include-value \
        --region "$REGION" --query 'value' --output text --no-cli-pager 2>/dev/null || true)
      echo "    Header   : x-api-key: $API_KEY_VALUE"
    fi
  fi
fi

echo ""
echo "Done. Now deploy the demo (it auto-loads the webhook):"
echo "    ./deploy-all.sh --key-file <path-to-your-key.pem>"
