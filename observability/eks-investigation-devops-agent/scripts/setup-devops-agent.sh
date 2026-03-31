#!/bin/bash

# =============================================================================
# DevOps Agent Setup Script
# =============================================================================
# Creates the AWS DevOps Agent Space, IAM roles, and account association.
# After running this script, generate a generic webhook in the DevOps Agent
# console (Capabilities → Webhook → Add), then set the env vars:
#   export DEVOPS_AGENT_WEBHOOK_URL="https://event-ai.us-east-1.api.aws/webhook/generic/<id>"
#   export DEVOPS_AGENT_WEBHOOK_SECRET="<secret>"
# and re-run deploy-all.sh to enable the alarm → webhook integration.
#
# Prerequisites:
#   - AWS CLI v2 with DevOps Agent service model patched
#   - IAM permissions: iam:CreateRole, iam:AttachRolePolicy, iam:PutRolePolicy,
#     aidevops:CreateAgentSpace, aidevops:AssociateService, aidevops:EnableOperatorApp
#
# Usage:
#   ./scripts/setup-devops-agent.sh [agent-space-name]
#   source scripts/setup-devops-agent.sh  # makes setup_devops_agent available
# =============================================================================

setup_devops_agent() {
    AGENT_SPACE_NAME="${1:-devops-agent-eks}"
    DEVOPS_AGENT_ENDPOINT="https://api.prod.cp.aidevops.us-east-1.api.aws"
    DEVOPS_AGENT_REGION="us-east-1"

    echo "=============================================="
    echo " DevOps Agent Setup"
    echo "=============================================="
    echo ""

    # ---------------------------------------------------------------------------
    # Validate prerequisites
    # ---------------------------------------------------------------------------
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    echo "Account:          $AWS_ACCOUNT_ID"
    echo "Agent Space Name: $AGENT_SPACE_NAME"
    echo "Region:           $DEVOPS_AGENT_REGION (DevOps Agent is us-east-1 only)"
    echo ""

    # Auto-patch AWS CLI with DevOps Agent service model if not already available
    if ! aws devopsagent help &>/dev/null; then
        echo "  DevOps Agent CLI not found — patching AWS CLI..."
        curl -sf -o /tmp/devopsagent.json https://d1co8nkiwcta1g.cloudfront.net/devopsagent.json
        if [ $? -ne 0 ]; then
            echo "ERROR: Failed to download DevOps Agent service model."
            echo "  Manual fix: curl -o devopsagent.json https://d1co8nkiwcta1g.cloudfront.net/devopsagent.json"
            echo "              aws configure add-model --service-model file://devopsagent.json --service-name devopsagent"
            return 1
        fi
        aws configure add-model --service-model file:///tmp/devopsagent.json --service-name devopsagent
        echo "  AWS CLI patched with DevOps Agent service model."
    else
        echo "  DevOps Agent CLI already available."
    fi

    # ---------------------------------------------------------------------------
    # Step 1: Create IAM roles
    # ---------------------------------------------------------------------------
    echo "[1/4] Creating IAM roles..."

    # --- Agent Space Role ---
    AGENTSPACE_ROLE="${AGENT_SPACE_NAME}-AgentSpaceRole"
    if aws iam get-role --role-name "$AGENTSPACE_ROLE" &>/dev/null; then
        echo "  IAM role '$AGENTSPACE_ROLE' already exists."
    else
        echo "  Creating IAM role '$AGENTSPACE_ROLE'..."
        cat > /tmp/devops-agentspace-trust-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "aidevops.amazonaws.com" },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": { "aws:SourceAccount": "$AWS_ACCOUNT_ID" },
        "ArnLike": { "aws:SourceArn": "arn:aws:aidevops:$DEVOPS_AGENT_REGION:$AWS_ACCOUNT_ID:agentspace/*" }
      }
    }
  ]
}
EOF
        aws iam create-role \
            --role-name "$AGENTSPACE_ROLE" \
            --assume-role-policy-document file:///tmp/devops-agentspace-trust-policy.json \
            --region "$DEVOPS_AGENT_REGION" >/dev/null

        aws iam attach-role-policy \
            --role-name "$AGENTSPACE_ROLE" \
            --policy-arn arn:aws:iam::aws:policy/AIOpsAssistantPolicy

        cat > /tmp/devops-agentspace-inline-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowExpandedAIOpsAssistantPolicy",
      "Effect": "Allow",
      "Action": [
        "aidevops:GetKnowledgeItem",
        "aidevops:ListKnowledgeItems",
        "eks:AccessKubernetesApi",
        "synthetics:GetCanaryRuns",
        "route53:GetHealthCheckStatus",
        "resource-explorer-2:Search",
        "support:CreateCase",
        "support:DescribeCases"
      ],
      "Resource": ["*"]
    }
  ]
}
EOF
        aws iam put-role-policy \
            --role-name "$AGENTSPACE_ROLE" \
            --policy-name AllowExpandedAIOpsAssistantPolicy \
            --policy-document file:///tmp/devops-agentspace-inline-policy.json

        echo "  Role '$AGENTSPACE_ROLE' created with AIOpsAssistantPolicy + EKS access."
    fi

    AGENTSPACE_ROLE_ARN=$(aws iam get-role --role-name "$AGENTSPACE_ROLE" --query 'Role.Arn' --output text)
    echo "  Role ARN: $AGENTSPACE_ROLE_ARN"

    # --- Operator App Role ---
    OPERATOR_ROLE="${AGENT_SPACE_NAME}-OperatorRole"
    if aws iam get-role --role-name "$OPERATOR_ROLE" &>/dev/null; then
        echo "  IAM role '$OPERATOR_ROLE' already exists."
    else
        echo "  Creating IAM role '$OPERATOR_ROLE'..."
        cat > /tmp/devops-operator-trust-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "aidevops.amazonaws.com" },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": { "aws:SourceAccount": "$AWS_ACCOUNT_ID" },
        "ArnLike": { "aws:SourceArn": "arn:aws:aidevops:$DEVOPS_AGENT_REGION:$AWS_ACCOUNT_ID:agentspace/*" }
      }
    }
  ]
}
EOF
        aws iam create-role \
            --role-name "$OPERATOR_ROLE" \
            --assume-role-policy-document file:///tmp/devops-operator-trust-policy.json \
            --region "$DEVOPS_AGENT_REGION" >/dev/null

        cat > /tmp/devops-operator-inline-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowBasicOperatorActions",
      "Effect": "Allow",
      "Action": [
        "aidevops:GetAgentSpace", "aidevops:GetAssociation", "aidevops:ListAssociations",
        "aidevops:CreateBacklogTask", "aidevops:GetBacklogTask", "aidevops:UpdateBacklogTask",
        "aidevops:ListBacklogTasks", "aidevops:ListChildExecutions", "aidevops:ListJournalRecords",
        "aidevops:DiscoverTopology", "aidevops:InvokeAgent", "aidevops:ListGoals",
        "aidevops:ListRecommendations", "aidevops:ListExecutions", "aidevops:GetRecommendation",
        "aidevops:UpdateRecommendation", "aidevops:CreateKnowledgeItem", "aidevops:ListKnowledgeItems",
        "aidevops:GetKnowledgeItem", "aidevops:UpdateKnowledgeItem", "aidevops:ListPendingMessages",
        "aidevops:InitiateChatForCase", "aidevops:EndChatForCase", "aidevops:DescribeSupportLevel",
        "aidevops:ListChats", "aidevops:CreateChat", "aidevops:StreamMessage"
      ],
      "Resource": "arn:aws:aidevops:$DEVOPS_AGENT_REGION:$AWS_ACCOUNT_ID:agentspace/*"
    },
    {
      "Sid": "AllowSupportOperatorActions",
      "Effect": "Allow",
      "Action": ["support:DescribeCases", "support:InitiateChatForCase", "support:DescribeSupportLevel"],
      "Resource": "*"
    }
  ]
}
EOF
        aws iam put-role-policy \
            --role-name "$OPERATOR_ROLE" \
            --policy-name AIDevOpsBasicOperatorActionsPolicy \
            --policy-document file:///tmp/devops-operator-inline-policy.json

        echo "  Role '$OPERATOR_ROLE' created."
    fi

    OPERATOR_ROLE_ARN=$(aws iam get-role --role-name "$OPERATOR_ROLE" --query 'Role.Arn' --output text)
    echo "  Role ARN: $OPERATOR_ROLE_ARN"
    echo ""

    # Wait for IAM propagation
    echo "  Waiting 10 seconds for IAM role propagation..."
    sleep 10

    # ---------------------------------------------------------------------------
    # Step 2: Create Agent Space (idempotent — check if one already exists)
    # ---------------------------------------------------------------------------
    echo "[2/4] Creating Agent Space..."

    EXISTING_SPACES=$(aws devopsagent list-agent-spaces \
        --endpoint-url "$DEVOPS_AGENT_ENDPOINT" \
        --region "$DEVOPS_AGENT_REGION" \
        --query 'agentSpaces[*].agentSpaceId' \
        --output text 2>/dev/null || echo "")

    if [ -n "$EXISTING_SPACES" ] && [ "$EXISTING_SPACES" != "None" ]; then
        AGENT_SPACE_ID=$(echo "$EXISTING_SPACES" | awk '{print $1}')
        echo "  Agent Space already exists: $AGENT_SPACE_ID"
    else
        AGENT_SPACE_ID=$(aws devopsagent create-agent-space \
            --name "$AGENT_SPACE_NAME" \
            --description "Agent Space for EKS incident investigation demo" \
            --endpoint-url "$DEVOPS_AGENT_ENDPOINT" \
            --region "$DEVOPS_AGENT_REGION" \
            --query 'agentSpace.agentSpaceId' \
            --output text)
        echo "  Agent Space created: $AGENT_SPACE_ID"
    fi
    echo ""

    # ---------------------------------------------------------------------------
    # Step 3: Associate AWS account
    # ---------------------------------------------------------------------------
    echo "[3/4] Associating AWS account..."

    # Check if already associated
    EXISTING_ASSOC=$(aws devopsagent list-associations \
        --agent-space-id "$AGENT_SPACE_ID" \
        --endpoint-url "$DEVOPS_AGENT_ENDPOINT" \
        --region "$DEVOPS_AGENT_REGION" \
        --query "associations[?serviceId=='aws'].associationId" \
        --output text 2>/dev/null || echo "")

    if [ -n "$EXISTING_ASSOC" ] && [ "$EXISTING_ASSOC" != "None" ]; then
        echo "  AWS account already associated."
    else
        aws devopsagent associate-service \
            --agent-space-id "$AGENT_SPACE_ID" \
            --service-id aws \
            --configuration "{
                \"aws\": {
                    \"assumableRoleArn\": \"$AGENTSPACE_ROLE_ARN\",
                    \"accountId\": \"$AWS_ACCOUNT_ID\",
                    \"accountType\": \"monitor\",
                    \"resources\": []
                }
            }" \
            --endpoint-url "$DEVOPS_AGENT_ENDPOINT" \
            --region "$DEVOPS_AGENT_REGION" >/dev/null
        echo "  AWS account $AWS_ACCOUNT_ID associated."
    fi
    echo ""

    # ---------------------------------------------------------------------------
    # Step 4: Enable Operator App
    # ---------------------------------------------------------------------------
    echo "[4/4] Enabling Operator App..."

    aws devopsagent enable-operator-app \
        --agent-space-id "$AGENT_SPACE_ID" \
        --auth-flow iam \
        --operator-app-role-arn "$OPERATOR_ROLE_ARN" \
        --endpoint-url "$DEVOPS_AGENT_ENDPOINT" \
        --region "$DEVOPS_AGENT_REGION" 2>/dev/null || echo "  (already enabled)"

    echo "  Operator App enabled."
    echo ""

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------
    echo "=============================================="
    echo " DevOps Agent Setup Complete"
    echo "=============================================="
    echo ""
    echo "Agent Space ID:   $AGENT_SPACE_ID"
    echo "Agent Space Role: $AGENTSPACE_ROLE_ARN"
    echo "Operator Role:    $OPERATOR_ROLE_ARN"
    echo ""

    # ---------------------------------------------------------------------------
    # Interactive webhook configuration
    # ---------------------------------------------------------------------------
    echo "=============================================="
    echo " Webhook Configuration"
    echo "=============================================="
    echo ""
    echo "Generate a generic webhook in the DevOps Agent console (~30 seconds):"
    echo ""
    echo "  1. Open: https://$DEVOPS_AGENT_REGION.console.aws.amazon.com/aidevops/home#/agent-spaces"
    echo "  2. Select '$AGENT_SPACE_NAME' → Capabilities → Webhook → Add"
    echo "  3. Click Next on the wizard, then click 'Generate URL and secret key'"
    echo "  4. Copy the webhook URL and secret key"
    echo ""

    read -rp "Paste the webhook URL (or press Enter to skip): " WEBHOOK_URL
    if [ -n "$WEBHOOK_URL" ]; then
        read -rp "Paste the webhook secret key: " WEBHOOK_SECRET
        if [ -n "$WEBHOOK_SECRET" ]; then
            export DEVOPS_AGENT_WEBHOOK_URL="$WEBHOOK_URL"
            export DEVOPS_AGENT_WEBHOOK_SECRET="$WEBHOOK_SECRET"
            echo ""
            echo "  Webhook configured. Environment variables set:"
            echo "    DEVOPS_AGENT_WEBHOOK_URL=$DEVOPS_AGENT_WEBHOOK_URL"
            echo "    DEVOPS_AGENT_WEBHOOK_SECRET=****"
            echo ""
            echo "  You can now deploy with DevOps Agent integration:"
            echo "    ./deploy-all.sh dev"
        else
            echo ""
            echo "  No secret provided — skipping webhook configuration."
            echo "  You can set them later before deploying:"
            echo "    export DEVOPS_AGENT_WEBHOOK_URL=\"<url>\""
            echo "    export DEVOPS_AGENT_WEBHOOK_SECRET=\"<secret>\""
        fi
    else
        echo "  Skipped. You can configure the webhook later before deploying:"
        echo "    export DEVOPS_AGENT_WEBHOOK_URL=\"<url>\""
        echo "    export DEVOPS_AGENT_WEBHOOK_SECRET=\"<secret>\""
    fi
    echo ""
}

# Run if executed directly, not when sourced
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    setup_devops_agent "$@"
fi
