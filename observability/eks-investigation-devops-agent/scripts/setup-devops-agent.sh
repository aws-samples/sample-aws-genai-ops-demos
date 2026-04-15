#!/bin/bash

# =============================================================================
# DevOps Agent Setup Script — GA Version
# =============================================================================
# Creates the Agent Space, IAM roles, Operator App, AWS account association,
# and prompts for the generic webhook.
# Uses native GA AWS CLI >= 2.34.21 (command: aws devops-agent).
#
# Two IAM roles are created:
#   1. AgentSpaceRole — monitoring role assumed by DevOps Agent to access AWS
#      resources (CloudWatch, EKS, RDS, CloudTrail, etc.)
#   2. OperatorRole — web console role assumed by users for Operator Access
#
# Usage:
#   source scripts/setup-devops-agent.sh  # makes setup_devops_agent available
#   ./scripts/setup-devops-agent.sh       # run directly
# =============================================================================

setup_devops_agent() {
    AGENT_SPACE_NAME="${1:-devops-agent-eks}"
    DEVOPS_AGENT_REGION="${DEVOPS_AGENT_REGION:-us-east-1}"

    echo "=============================================="
    echo " DevOps Agent Setup (GA)"
    echo "=============================================="
    echo ""

    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    echo "Account:          $AWS_ACCOUNT_ID"
    echo "Agent Space Name: $AGENT_SPACE_NAME"
    echo "Region:           $DEVOPS_AGENT_REGION"
    echo ""

    # -----------------------------------------------------------------------
    # Step 1: Create Agent Space (idempotent)
    # -----------------------------------------------------------------------
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
        echo "  Waiting for Agent Space to become active..."
        sleep 10
    fi
    export DEVOPS_AGENT_SPACE_ID="$AGENT_SPACE_ID"
    echo ""

    # -----------------------------------------------------------------------
    # Step 2: Create IAM roles (idempotent)
    # -----------------------------------------------------------------------
    echo "[2/6] Creating IAM roles..."

    cat > /tmp/devops-agent-trust.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "aidevops.amazonaws.com" },
    "Action": ["sts:AssumeRole", "sts:TagSession"],
    "Condition": {
      "StringEquals": { "aws:SourceAccount": "$AWS_ACCOUNT_ID" }
    }
  }]
}
EOF

    # --- AgentSpaceRole: monitoring role (access to AWS resources) ---
    AGENTSPACE_ROLE_NAME="${AGENT_SPACE_NAME}-AgentSpaceRole"
    if ! aws iam get-role --role-name "$AGENTSPACE_ROLE_NAME" --no-cli-pager &>/dev/null; then
        echo "  Creating '$AGENTSPACE_ROLE_NAME' (monitoring role)..."
        aws iam create-role \
            --role-name "$AGENTSPACE_ROLE_NAME" \
            --assume-role-policy-document file:///tmp/devops-agent-trust.json \
            --no-cli-pager >/dev/null
        aws iam attach-role-policy \
            --role-name "$AGENTSPACE_ROLE_NAME" \
            --policy-arn "arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy" \
            --no-cli-pager 2>/dev/null
        echo "  Created with AIDevOpsAgentAccessPolicy."
    else
        echo "  '$AGENTSPACE_ROLE_NAME' already exists."
    fi
    AGENTSPACE_ROLE_ARN=$(aws iam get-role --role-name "$AGENTSPACE_ROLE_NAME" --query 'Role.Arn' --output text --no-cli-pager)

    # --- OperatorRole: web console role (Operator Access) ---
    OPERATOR_ROLE_NAME="${AGENT_SPACE_NAME}-OperatorRole"
    if ! aws iam get-role --role-name "$OPERATOR_ROLE_NAME" --no-cli-pager &>/dev/null; then
        echo "  Creating '$OPERATOR_ROLE_NAME' (web console role)..."
        aws iam create-role \
            --role-name "$OPERATOR_ROLE_NAME" \
            --assume-role-policy-document file:///tmp/devops-agent-trust.json \
            --no-cli-pager >/dev/null
        aws iam attach-role-policy \
            --role-name "$OPERATOR_ROLE_NAME" \
            --policy-arn "arn:aws:iam::aws:policy/AIDevOpsOperatorAppAccessPolicy" \
            --no-cli-pager 2>/dev/null
        echo "  Created with AIDevOpsOperatorAppAccessPolicy."
    else
        echo "  '$OPERATOR_ROLE_NAME' already exists."
    fi
    OPERATOR_ROLE_ARN=$(aws iam get-role --role-name "$OPERATOR_ROLE_NAME" --query 'Role.Arn' --output text --no-cli-pager)

    echo "  Waiting for IAM propagation..."
    sleep 10
    echo ""

    # -----------------------------------------------------------------------
    # Step 3: Enable Operator App — web console access (idempotent)
    # -----------------------------------------------------------------------
    echo "[3/6] Enabling Operator App (web console)..."

    aws devops-agent enable-operator-app \
        --agent-space-id "$AGENT_SPACE_ID" \
        --auth-flow iam \
        --operator-app-role-arn "$OPERATOR_ROLE_ARN" \
        --region "$DEVOPS_AGENT_REGION" \
        --no-cli-pager >/dev/null 2>&1 || true
    echo "  Operator App enabled (IAM auth)."
    echo ""

    # -----------------------------------------------------------------------
    # Step 4: Associate AWS account as cloud source (idempotent)
    # -----------------------------------------------------------------------
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
        ASSOC_RESULT=$(aws devops-agent associate-service \
            --agent-space-id "$AGENT_SPACE_ID" \
            --service-id aws \
            --configuration "$ASSOC_CONFIG" \
            --region "$DEVOPS_AGENT_REGION" \
            --no-cli-pager 2>&1)
        if [ $? -ne 0 ]; then
            echo "  WARNING: Failed to associate AWS account."
            echo "  $ASSOC_RESULT"
            echo "  You may need to add the cloud source manually in the DevOps Agent console."
        else
            echo "  AWS account $AWS_ACCOUNT_ID associated (monitor)."
        fi
    fi
    echo ""

    # -----------------------------------------------------------------------
    # Step 5: Webhook prompt
    # -----------------------------------------------------------------------
    echo "[5/6] Webhook Configuration"
    echo ""
    echo "  Generate a generic webhook in the DevOps Agent console (~30 seconds):"
    echo ""
    echo "  1. Open: https://$DEVOPS_AGENT_REGION.console.aws.amazon.com/aidevops/home?region=$DEVOPS_AGENT_REGION#/agent-spaces/$AGENT_SPACE_ID"
    echo "  2. Go to Capabilities > Webhook > Add"
    echo "  3. Click Next, then 'Generate URL and secret key'"
    echo "  4. Copy the webhook URL and secret key"
    echo ""

    read -rp "  Paste the webhook URL (or press Enter to skip): " WEBHOOK_URL
    if [ -n "$WEBHOOK_URL" ]; then
        read -rp "  Paste the webhook secret key: " WEBHOOK_SECRET
        if [ -n "$WEBHOOK_SECRET" ]; then
            export DEVOPS_AGENT_WEBHOOK_URL="$WEBHOOK_URL"
            export DEVOPS_AGENT_WEBHOOK_SECRET="$WEBHOOK_SECRET"
            echo ""
            echo "  Webhook configured."
            echo "    DEVOPS_AGENT_WEBHOOK_URL=$DEVOPS_AGENT_WEBHOOK_URL"
            echo "    DEVOPS_AGENT_WEBHOOK_SECRET=****"
        else
            echo "  No secret provided — skipping."
        fi
    else
        echo "  Skipped. Set env vars before deploying:"
        echo "    export DEVOPS_AGENT_WEBHOOK_URL=\"<url>\""
        echo "    export DEVOPS_AGENT_WEBHOOK_SECRET=\"<secret>\""
    fi
    echo ""

    # -----------------------------------------------------------------------
    # Step 6: Live-update deployed Lambdas (if demo is already deployed)
    # -----------------------------------------------------------------------
    echo "[6/6] Updating deployed resources (if demo is already deployed)..."

    # Convention-based names: {projectName}-{env}-devops-trigger, {projectName}-{env}-failure-simulator
    ENVIRONMENT="dev"
    TRIGGER_LAMBDA="${AGENT_SPACE_NAME}-${ENVIRONMENT}-devops-trigger"
    SIMULATOR_LAMBDA="${AGENT_SPACE_NAME}-${ENVIRONMENT}-failure-simulator"
    SECRET_NAME="${AGENT_SPACE_NAME}-${ENVIRONMENT}/devops-agent-webhook-secret"

    # Check if the demo is deployed by testing if the trigger Lambda exists
    if aws lambda get-function --function-name "$TRIGGER_LAMBDA" --no-cli-pager >/dev/null 2>&1; then
        echo "  Updating deployed resources with new Agent Space..."

        # Update webhook secret in Secrets Manager
        if [ -n "$DEVOPS_AGENT_WEBHOOK_SECRET" ]; then
            aws secretsmanager update-secret \
                --secret-id "$SECRET_NAME" \
                --secret-string "$DEVOPS_AGENT_WEBHOOK_SECRET" \
                --no-cli-pager >/dev/null 2>&1
            echo "  Updated Secrets Manager secret."
        fi

        # Update trigger Lambda env vars (webhook URL)
        if [ -n "$DEVOPS_AGENT_WEBHOOK_URL" ]; then
            TRIGGER_ENV=$(aws lambda get-function-configuration \
                --function-name "$TRIGGER_LAMBDA" \
                --query 'Environment.Variables' \
                --output json --no-cli-pager 2>/dev/null)
            TRIGGER_ENV=$(echo "$TRIGGER_ENV" | jq --arg url "$DEVOPS_AGENT_WEBHOOK_URL" '.WEBHOOK_URL = $url')
            aws lambda update-function-configuration \
                --function-name "$TRIGGER_LAMBDA" \
                --environment "Variables=$TRIGGER_ENV" \
                --no-cli-pager >/dev/null 2>&1
            echo "  Updated trigger Lambda (webhook URL)."
        fi

        # Update simulator Lambda env vars (space ID)
        SIM_ENV=$(aws lambda get-function-configuration \
            --function-name "$SIMULATOR_LAMBDA" \
            --query 'Environment.Variables' \
            --output json --no-cli-pager 2>/dev/null)
        SIM_ENV=$(echo "$SIM_ENV" | jq --arg sid "$AGENT_SPACE_ID" '.DEVOPS_AGENT_SPACE_ID = $sid')
        aws lambda update-function-configuration \
            --function-name "$SIMULATOR_LAMBDA" \
            --environment "Variables=$SIM_ENV" \
            --no-cli-pager >/dev/null 2>&1
        echo "  Updated simulator Lambda (space ID)."

        echo "  All resources updated. No CDK redeploy needed."
    else
        echo "  Demo not yet deployed — run deploy-all.sh to deploy."
    fi
    echo ""
}

# Run if executed directly, not when sourced
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    setup_devops_agent "$@"
fi
