#!/usr/bin/env bash
# cleanup.sh — Tear down the Aurora incident-investigation demo.
# Usage: ./cleanup.sh [region]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REGION="${1:-${AWS_DEFAULT_REGION:-${AWS_REGION:-$(aws configure get region 2>/dev/null)}}}"
[[ -z "$REGION" ]] && { echo "ERROR: region required (arg 1 or AWS_DEFAULT_REGION)"; exit 1; }

CDK_DIR="$SCRIPT_DIR/../infrastructure/cdk"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
export PYTHONPATH="$REPO_ROOT"

echo "==> Destroying CDK stacks in $REGION..."
if [[ -d "$CDK_DIR/.venv" ]]; then
  source "$CDK_DIR/.venv/bin/activate"
fi
pushd "$CDK_DIR" > /dev/null
npx -y cdk destroy "AuroraDemoStack-$REGION" "AuroraDemoMcpServer-$REGION" \
  --force --no-cli-pager --context "keyPairName=placeholder" || true
popd > /dev/null

echo ""
echo "==> Removing DevOps Agent space + IAM roles (if created by setup-devops-agent.sh)..."
PY="$(command -v python3 || command -v python)"
if [[ -n "$PY" ]]; then
  "$PY" -c 'import boto3' 2>/dev/null || "$PY" -m pip install --quiet --disable-pip-version-check boto3 || true
  "$PY" "$SCRIPT_DIR/devops_agent_setup.py" --region "$REGION" --teardown || true
fi

echo ""
echo "==> Cleanup complete."
echo "   Manually remove if desired:"
echo "     - MCP server registration in the DevOps Agent console"
echo "     - Secrets Manager secret 'aurora-demo/credentials' (retained ~7 days by default)"
