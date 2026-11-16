#!/usr/bin/env bash
# setup-devops-agent.sh — Hands-free Amazon DevOps Agent setup for the Aurora demo.
#
# Creates (or reuses) the Agent Space, IAM roles, Operator App, AWS-account
# association, a generic webhook, and the aurora-incident-business-context
# skill from aws/tools-for-devops-agent — via the AWS SDK. The webhook URL
# and secret are written to .devops-agent.env so `deploy-all.sh` picks them up
# automatically (you do not need to copy/paste them).
#
# If your AWS SDK build does not include the DevOps Agent API, the script prints
# the equivalent one-time console steps instead.
#
# Usage:
#   ./setup-devops-agent.sh [region] [--space-name NAME]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

REGION=""
SPACE_NAME="aurora-demo"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --space-name) SPACE_NAME="$2"; shift 2;;
    -h|--help) echo "Usage: $0 [region] [--space-name NAME]"; exit 0;;
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


echo ""
echo "Done. Now deploy the demo (it auto-loads the webhook):"
echo "    ./deploy-all.sh --key-file <path-to-your-key.pem>"
