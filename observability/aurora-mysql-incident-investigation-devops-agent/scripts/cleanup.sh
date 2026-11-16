#!/usr/bin/env bash
# cleanup.sh — Tear down the Aurora incident-investigation demo.
# Usage: ./cleanup.sh [region]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REGION="${1:-${AWS_DEFAULT_REGION:-${AWS_REGION:-$(aws configure get region 2>/dev/null)}}}"
[[ -z "$REGION" ]] && { echo "ERROR: region required (arg 1 or AWS_DEFAULT_REGION)"; exit 1; }

CDK_DIR="$(cd "$SCRIPT_DIR/../infrastructure/cdk" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

echo "==> Destroying CDK stack in $REGION..."
[[ -d "$CDK_DIR/.venv" ]] || python3 -m venv "$CDK_DIR/.venv"
source "$CDK_DIR/.venv/bin/activate"
export AWS_REGION="$REGION"
bash "$REPO_ROOT/shared/scripts/deploy-cdk.sh" --cdk-directory "$CDK_DIR" \
  --stack-name "AuroraDemoStack-$REGION" --destroy --skip-bootstrap \
  --cdk-context "keyPairName=placeholder" || true

echo ""
echo "==> Removing DevOps Agent space + IAM roles (if created by setup-devops-agent.sh)..."
PY="$(command -v python3 || command -v python)"
if [[ -n "$PY" ]]; then
  "$PY" -c 'import boto3' 2>/dev/null || "$PY" -m pip install --quiet --disable-pip-version-check boto3 || true
  "$PY" "$SCRIPT_DIR/devops_agent_setup.py" --region "$REGION" --teardown || true
fi

echo ""
rm -f "$SCRIPT_DIR/../aurora-incident-business-context.zip"
echo "==> Cleanup complete."
echo "   Manually remove if desired:"
echo "     - Secrets Manager secret 'aurora-demo/credentials' (retained ~7 days by default)"
