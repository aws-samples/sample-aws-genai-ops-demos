#!/usr/bin/env bash
# deploy-all.sh — Deploy the Aurora MySQL incident-investigation demo via CDK,
# then configure the bastion load-generator and CloudWatch alarms.
#
# Usage:
#   ./deploy-all.sh --key-file <path> [--key-pair <name>] \
#                   [--webhook-url <url>] [--webhook-secret <secret>] \
#                   [--ssh-cidr <cidr>] [--ssh-open]
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KEY_PAIR=""
KEY_FILE=""
WEBHOOK_URL=""
WEBHOOK_SECRET=""
SSH_CIDR=""
SSH_OPEN=""

usage() {
  echo "Usage: $0 --key-file <path> [--key-pair <name>] [--webhook-url <url>] [--webhook-secret <secret>] [--ssh-cidr <cidr>] [--ssh-open]"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --key-pair) KEY_PAIR="$2"; shift 2;;
    --key-file) KEY_FILE="$2"; shift 2;;
    --webhook-url) WEBHOOK_URL="$2"; shift 2;;
    --webhook-secret) WEBHOOK_SECRET="$2"; shift 2;;
    --ssh-cidr) SSH_CIDR="$2"; shift 2;;
    --ssh-open) SSH_OPEN="yes"; shift;;
    -h|--help) usage;;
    *) echo "Unknown option: $1"; usage;;
  esac
done

[[ -z "$KEY_FILE" ]] && echo "ERROR: --key-file is required" && usage
[[ ! -f "$KEY_FILE" ]] && echo "ERROR: key file not found: $KEY_FILE" && exit 1

# Auto-load the DevOps Agent webhook written by setup-devops-agent.sh, unless the
# caller passed one explicitly. Lets you run:  ./setup-devops-agent.sh  then  ./deploy-all.sh
if [[ -z "$WEBHOOK_URL" && -f "$SCRIPT_DIR/.devops-agent.env" ]]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.devops-agent.env"
  [[ -n "${WEBHOOK_URL:-}" ]] && echo "  Loaded DevOps Agent webhook from .devops-agent.env"
fi

# =============================================================================
echo "==> Step 1: Check prerequisites..."
# =============================================================================
source "$SCRIPT_DIR/../../shared/scripts/check-prerequisites.sh" --require-cdk --skip-service-check
REGION="$AWS_REGION"

if [[ -z "$KEY_PAIR" ]]; then
  echo "Available key pairs in $REGION:"
  aws ec2 describe-key-pairs --region "$REGION" --query 'KeyPairs[].KeyName' --output table --no-cli-pager
  read -rp "Enter key pair name: " KEY_PAIR
  [[ -z "$KEY_PAIR" ]] && echo "ERROR: key pair required" && exit 1
fi

# Resolve SSH CIDR
if [[ "$SSH_OPEN" == "yes" ]]; then
  SSH_CIDR="0.0.0.0/0"
  echo "  ⚠ SSH open to 0.0.0.0/0 (not recommended for production)"
elif [[ -z "$SSH_CIDR" ]]; then
  MY_IP=$(curl -s --max-time 5 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]')
  if [[ -n "$MY_IP" ]]; then
    SSH_CIDR="${MY_IP}/32"
    echo "  SSH restricted to your IP: $SSH_CIDR"
  else
    SSH_CIDR="0.0.0.0/0"
    echo "  ⚠ Could not detect your IP — SSH open to 0.0.0.0/0"
  fi
fi

# =============================================================================
echo ""
echo "==> Step 2: Deploy Aurora infrastructure via CDK (cluster creation ~10-15 min)..."
# =============================================================================
command -v node >/dev/null 2>&1 || { echo "ERROR: Node.js is required for CDK. Install from https://nodejs.org"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "ERROR: Python 3 is required for CDK. Install from https://python.org"; exit 1; }

CDK_DIR="$SCRIPT_DIR/infrastructure/cdk"
STACK_NAME="AuroraDemoStack-$REGION"

REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT"

python3 -m venv "$CDK_DIR/.venv"
source "$CDK_DIR/.venv/bin/activate"
pip install -r "$CDK_DIR/requirements.txt"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --no-cli-pager)
pushd "$CDK_DIR" > /dev/null
npx -y cdk bootstrap "aws://$ACCOUNT_ID/$REGION" --no-cli-pager

CDK_ARGS=(npx -y cdk deploy "$STACK_NAME" --require-approval never --no-cli-pager)
CDK_ARGS+=(--context "keyPairName=$KEY_PAIR" --context "sshCidr=$SSH_CIDR")
[[ -n "$WEBHOOK_URL" ]] && CDK_ARGS+=(--context "webhookUrl=$WEBHOOK_URL")
[[ -n "$WEBHOOK_SECRET" ]] && CDK_ARGS+=(--context "webhookSecret=$WEBHOOK_SECRET")
"${CDK_ARGS[@]}"
popd > /dev/null

# =============================================================================
echo ""
echo "==> Step 3: Fetch stack outputs..."
# =============================================================================
get_output() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text --no-cli-pager
}

CLUSTER_ID=$(get_output ClusterIdentifier)
WRITER_ENDPOINT=$(get_output WriterEndpoint)
READER_ENDPOINT=$(get_output ReaderEndpoint)
DB_PORT=$(get_output DbPort)
DB_NAME=$(get_output DefaultDatabaseName)
SECRET_ARN=$(get_output SecretArn)
BASTION_IP=$(get_output BastionPublicIp)

echo "  Cluster: $CLUSTER_ID"
echo "  Writer : $WRITER_ENDPOINT | Reader: $READER_ENDPOINT"
echo "  Bastion: $BASTION_IP"

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i $KEY_FILE"
SSH_USER="ec2-user"
run_ssh() { ssh $SSH_OPTS "${SSH_USER}@${BASTION_IP}" "$1"; }

# =============================================================================
echo ""
echo "==> Step 4: Wait for SSH on $BASTION_IP..."
# =============================================================================
for i in {1..30}; do
  ssh $SSH_OPTS -o ConnectTimeout=5 "${SSH_USER}@${BASTION_IP}" "true" 2>/dev/null && break
  sleep 10; echo "  Waiting... $((i*10))s"
done

# =============================================================================
echo "==> Step 5: Wait for UserData (mysql client install) to complete..."
# =============================================================================
for i in {1..30}; do
  run_ssh "grep -q USERDATA_COMPLETE /var/log/aurora-userdata.log 2>/dev/null" && echo "  Done." && break
  sleep 10; echo "  Installing packages... ($i/30)"
done

# =============================================================================
echo "==> Step 6: Push bastion scripts + write environment file..."
# =============================================================================
scp $SSH_OPTS "$SCRIPT_DIR/bastion-scripts/"* "${SSH_USER}@${BASTION_IP}:/tmp/"
run_ssh "sudo mkdir -p /opt/aurora-demo && sudo cp /tmp/lib.sh /tmp/inject /tmp/rollback /tmp/status /tmp/list /tmp/seed-data /opt/aurora-demo/ && sudo chmod +x /opt/aurora-demo/inject /opt/aurora-demo/rollback /opt/aurora-demo/status /opt/aurora-demo/list /opt/aurora-demo/seed-data"

run_ssh "sudo tee /opt/aurora-demo/env >/dev/null <<EOF
REGION=$REGION
CLUSTER_ID=$CLUSTER_ID
WRITER_ENDPOINT=$WRITER_ENDPOINT
READER_ENDPOINT=$READER_ENDPOINT
DB_PORT=$DB_PORT
DB_NAME=$DB_NAME
SECRET_ARN=$SECRET_ARN
EOF"

# =============================================================================
echo "==> Step 7: Seed demo schema/data..."
# =============================================================================
run_ssh "sudo /opt/aurora-demo/seed-data" || echo "  ⚠ Seeding reported an issue — re-run 'sudo /opt/aurora-demo/seed-data' on the bastion if scenarios fail."

# =============================================================================
echo "==> Step 8: Disable actions on dedicated alarms (enabled per-scenario)..."
# =============================================================================
aws cloudwatch disable-alarm-actions --region "$REGION" \
  --alarm-names aurora-demo-memory-pressure aurora-demo-replica-lag --no-cli-pager
echo "  aurora-demo-memory-pressure and aurora-demo-replica-lag start with actions disabled."

# =============================================================================
echo ""
echo "========================================"
echo "  Aurora Incident Demo Ready"
echo "========================================"
echo ""
echo "  Region        : $REGION"
echo "  Cluster       : $CLUSTER_ID"
echo "  Writer        : $WRITER_ENDPOINT"
echo "  Reader        : $READER_ENDPOINT"
echo "  Bastion (SSH) : ssh -i $KEY_FILE ${SSH_USER}@$BASTION_IP"
echo ""
echo "  Inject a scenario:"
echo "    bash scripts/inject-failure.sh connection-storm --key-file $KEY_FILE"
echo "    bash scripts/inject-failure.sh cpu-spike        --key-file $KEY_FILE"
echo "    bash scripts/inject-failure.sh deadlock         --key-file $KEY_FILE"
echo "    bash scripts/inject-failure.sh failover         --key-file $KEY_FILE"
echo "    bash scripts/inject-failure.sh memory-pressure  --key-file $KEY_FILE   # dedicated alarm"
echo "    bash scripts/inject-failure.sh replica-lag      --key-file $KEY_FILE   # dedicated alarm"
echo ""
echo "  Roll back:"
echo "    bash scripts/inject-failure.sh <scenario> --key-file $KEY_FILE --rollback"
echo ""
echo "  Status:"
echo "    bash scripts/inject-failure.sh status --key-file $KEY_FILE"
echo "========================================"
