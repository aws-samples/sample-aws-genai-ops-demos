#!/bin/bash
# =============================================================================
# DevOps Agent setup — Prowler Security demo
# =============================================================================
# Creates the Agent Space, IAM roles, Operator App, AWS account association,
# and prompts for the webhook URL and secret. Mirrors the EKS demo's setup
# script (observability/eks-investigation-devops-agent/scripts/setup-devops-agent.sh)
# adapted to this demo's naming (prowler-security-*).
# =============================================================================

setup_devops_agent() {
    AGENT_SPACE_NAME="${1:-prowler-security}"
    # Default to the same region where the rest of the stack lives. Fall back to
    # us-east-1 only if AWS_REGION isn't set (e.g. when the script is sourced
    # standalone without running deploy-all.sh first).
    DEVOPS_AGENT_REGION="${DEVOPS_AGENT_REGION:-${AWS_REGION:-us-east-1}}"

    echo "=============================================="
    echo " DevOps Agent Setup — Prowler Security"
    echo "=============================================="
    echo ""

    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    echo "Account:          $AWS_ACCOUNT_ID"
    echo "Agent Space Name: $AGENT_SPACE_NAME"
    echo "Region:           $DEVOPS_AGENT_REGION"
    echo ""

    echo "[1/6] Creating Agent Space..."
    EXISTING_SPACE_ID=$(aws devops-agent list-agent-spaces \
        --region "$DEVOPS_AGENT_REGION" \
        --query "agentSpaces[?name=='$AGENT_SPACE_NAME'].agentSpaceId | [0]" \
        --output text --no-cli-pager 2>/dev/null || echo "")

    if [ -n "$EXISTING_SPACE_ID" ] && [ "$EXISTING_SPACE_ID" != "None" ]; then
        AGENT_SPACE_ID="$EXISTING_SPACE_ID"
        echo "  Agent Space already exists: $AGENT_SPACE_ID"
    else
        AGENT_SPACE_ID=$(aws devops-agent create-agent-space \
            --name "$AGENT_SPACE_NAME" \
            --region "$DEVOPS_AGENT_REGION" \
            --query 'agentSpace.agentSpaceId' \
            --output text --no-cli-pager)
        if [ -z "$AGENT_SPACE_ID" ]; then
            echo "  ERROR: Failed to create Agent Space."
            return 1
        fi
        echo "  Agent Space created: $AGENT_SPACE_ID"
        sleep 10
    fi
    export DEVOPS_AGENT_SPACE_ID="$AGENT_SPACE_ID"
    echo ""

    echo "[2/6] Creating IAM roles..."
    cat > /tmp/devops-agent-trust.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "aidevops.amazonaws.com" },
    "Action": ["sts:AssumeRole", "sts:TagSession"],
    "Condition": { "StringEquals": { "aws:SourceAccount": "$AWS_ACCOUNT_ID" } }
  }]
}
EOF

    AGENTSPACE_ROLE_NAME="${AGENT_SPACE_NAME}-AgentSpaceRole"
    if ! aws iam get-role --role-name "$AGENTSPACE_ROLE_NAME" --no-cli-pager &>/dev/null; then
        aws iam create-role \
            --role-name "$AGENTSPACE_ROLE_NAME" \
            --assume-role-policy-document file:///tmp/devops-agent-trust.json \
            --no-cli-pager >/dev/null
        aws iam attach-role-policy \
            --role-name "$AGENTSPACE_ROLE_NAME" \
            --policy-arn "arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy" \
            --no-cli-pager 2>/dev/null
        echo "  Created $AGENTSPACE_ROLE_NAME."
    else
        echo "  $AGENTSPACE_ROLE_NAME already exists."
    fi
    AGENTSPACE_ROLE_ARN=$(aws iam get-role --role-name "$AGENTSPACE_ROLE_NAME" --query 'Role.Arn' --output text --no-cli-pager)

    OPERATOR_ROLE_NAME="${AGENT_SPACE_NAME}-OperatorRole"
    if ! aws iam get-role --role-name "$OPERATOR_ROLE_NAME" --no-cli-pager &>/dev/null; then
        aws iam create-role \
            --role-name "$OPERATOR_ROLE_NAME" \
            --assume-role-policy-document file:///tmp/devops-agent-trust.json \
            --no-cli-pager >/dev/null
        aws iam attach-role-policy \
            --role-name "$OPERATOR_ROLE_NAME" \
            --policy-arn "arn:aws:iam::aws:policy/AIDevOpsOperatorAppAccessPolicy" \
            --no-cli-pager 2>/dev/null
        echo "  Created $OPERATOR_ROLE_NAME."
    else
        echo "  $OPERATOR_ROLE_NAME already exists."
    fi
    OPERATOR_ROLE_ARN=$(aws iam get-role --role-name "$OPERATOR_ROLE_NAME" --query 'Role.Arn' --output text --no-cli-pager)

    sleep 10
    echo ""

    echo "[3/6] Enabling Operator App (web console)..."
    aws devops-agent enable-operator-app \
        --agent-space-id "$AGENT_SPACE_ID" \
        --auth-flow iam \
        --operator-app-role-arn "$OPERATOR_ROLE_ARN" \
        --region "$DEVOPS_AGENT_REGION" \
        --no-cli-pager >/dev/null 2>&1 || true
    echo "  Operator App enabled."
    echo ""

    echo "[4/6] Associating AWS account (cloud source)..."
    EXISTING_ASSOC=$(aws devops-agent list-associations \
        --agent-space-id "$AGENT_SPACE_ID" \
        --region "$DEVOPS_AGENT_REGION" \
        --query "associations[?serviceId=='aws'].associationId | [0]" \
        --output text --no-cli-pager 2>/dev/null || echo "")

    if [ -n "$EXISTING_ASSOC" ] && [ "$EXISTING_ASSOC" != "None" ]; then
        echo "  AWS account already associated."
    else
        ASSOC_CONFIG="{\"aws\":{\"accountId\":\"$AWS_ACCOUNT_ID\",\"accountType\":\"monitor\",\"assumableRoleArn\":\"$AGENTSPACE_ROLE_ARN\"}}"
        aws devops-agent associate-service \
            --agent-space-id "$AGENT_SPACE_ID" \
            --service-id aws \
            --configuration "$ASSOC_CONFIG" \
            --region "$DEVOPS_AGENT_REGION" \
            --no-cli-pager >/dev/null 2>&1 || echo "  (association may need to be done in the console)"
        echo "  Associated account $AWS_ACCOUNT_ID."
    fi
    echo ""

    echo "[5/6] Webhook configuration"
    echo ""
    echo "  1. Open: https://$DEVOPS_AGENT_REGION.console.aws.amazon.com/aidevops/home?region=$DEVOPS_AGENT_REGION#/agent-spaces/$AGENT_SPACE_ID"
    echo "  2. Capabilities > Webhook > Add > Next > Generate URL and secret key"
    echo "  3. Copy both values."
    echo ""
    # Interactive prompt — only if stdin is a TTY. Otherwise (CI, automation,
    # nohup, etc.) skip silently and leave a placeholder. The operator can
    # wire the webhook later by re-running this script in a real terminal.
    if [ -t 0 ]; then
        read -rp "  Paste the webhook URL (or press Enter to skip): " WEBHOOK_URL
        if [ -n "$WEBHOOK_URL" ]; then
            read -rp "  Paste the webhook secret key: " WEBHOOK_SECRET
            if [ -n "$WEBHOOK_SECRET" ]; then
                export DEVOPS_AGENT_WEBHOOK_URL="$WEBHOOK_URL"
                export DEVOPS_AGENT_WEBHOOK_SECRET="$WEBHOOK_SECRET"
                echo "  Webhook configured."
            fi
        else
            echo "  Skipped. Re-run this script or set DEVOPS_AGENT_WEBHOOK_URL / DEVOPS_AGENT_WEBHOOK_SECRET manually."
        fi
    else
        echo "  [non-interactive] stdin is not a TTY — skipping webhook prompt."
        echo "  To wire the webhook later, run from a terminal:"
        echo "    bash scripts/setup-devops-agent.sh"
        echo "  It will live-update the Lambda env + Secrets Manager (no CDK redeploy)."
    fi
    echo ""

    echo "[6/6] Live-updating deployed resources if any..."
    TRIGGER_LAMBDA="prowler-security-devops-trigger"
    SECRET_NAME="prowler-security/devops-agent-webhook-secret"  # pragma: allowlist secret - Secrets Manager resource name

    # The dashboard API Lambda is exported by ProwlerSecurityApi-<region>. Look it
    # up instead of hard-coding the physical id — CDK generates a random suffix.
    DASHBOARD_LAMBDA=""
    API_STACK="ProwlerSecurityApi-${AWS_REGION:-$DEVOPS_AGENT_REGION}"
    DASHBOARD_LAMBDA=$(aws cloudformation describe-stack-resources \
        --stack-name "$API_STACK" \
        --query "StackResources[?ResourceType=='AWS::Lambda::Function'].PhysicalResourceId" \
        --output text --no-cli-pager 2>/dev/null | tr '\t' '\n' | head -1)

    if aws lambda get-function --function-name "$TRIGGER_LAMBDA" --no-cli-pager >/dev/null 2>&1; then
        if [ -n "$DEVOPS_AGENT_WEBHOOK_SECRET" ]; then
            aws secretsmanager update-secret \
                --secret-id "$SECRET_NAME" \
                --secret-string "$DEVOPS_AGENT_WEBHOOK_SECRET" \
                --no-cli-pager >/dev/null 2>&1 && echo "  Updated Secrets Manager secret."
        fi
        # Patch the trigger Lambda with webhook URL + Agent Space ID so newly
        # dispatched findings carry the right target.
        if [ -n "$DEVOPS_AGENT_WEBHOOK_URL" ] || [ -n "$DEVOPS_AGENT_SPACE_ID" ]; then
            TRIGGER_ENV=$(aws lambda get-function-configuration \
                --function-name "$TRIGGER_LAMBDA" \
                --query 'Environment.Variables' \
                --output json --no-cli-pager 2>/dev/null)
            if [ -n "$DEVOPS_AGENT_WEBHOOK_URL" ]; then
                TRIGGER_ENV=$(echo "$TRIGGER_ENV" | jq --arg url "$DEVOPS_AGENT_WEBHOOK_URL" '.WEBHOOK_URL = $url')
            fi
            if [ -n "$DEVOPS_AGENT_SPACE_ID" ]; then
                TRIGGER_ENV=$(echo "$TRIGGER_ENV" | jq --arg id "$DEVOPS_AGENT_SPACE_ID" '.DEVOPS_AGENT_SPACE_ID = $id')
            fi
            aws lambda update-function-configuration \
                --function-name "$TRIGGER_LAMBDA" \
                --environment "Variables=$TRIGGER_ENV" \
                --no-cli-pager >/dev/null 2>&1 && echo "  Updated trigger Lambda."
        fi
    else
        echo "  Demo not yet deployed — run deploy-all.sh."
    fi

    # Patch the dashboard-api Lambda with the Agent Space ID so /investigations
    # can list tasks from the real backlog.
    if [ -n "$DASHBOARD_LAMBDA" ] && [ -n "$DEVOPS_AGENT_SPACE_ID" ]; then
        DASH_ENV=$(aws lambda get-function-configuration \
            --function-name "$DASHBOARD_LAMBDA" \
            --query 'Environment.Variables' \
            --output json --no-cli-pager 2>/dev/null)
        DASH_ENV=$(echo "$DASH_ENV" | jq --arg id "$DEVOPS_AGENT_SPACE_ID" '.DEVOPS_AGENT_SPACE_ID = $id')
        aws lambda update-function-configuration \
            --function-name "$DASHBOARD_LAMBDA" \
            --environment "Variables=$DASH_ENV" \
            --no-cli-pager >/dev/null 2>&1 && echo "  Updated dashboard-api Lambda (Agent Space)."
    fi
    echo ""
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    setup_devops_agent "$@"
fi
