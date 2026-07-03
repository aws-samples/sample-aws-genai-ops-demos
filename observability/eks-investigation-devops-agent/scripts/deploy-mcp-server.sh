#!/bin/bash
# =============================================================================
# Deploy Payment Transaction Insights MCP Server (AgentCore Gateway + Lambda)
# =============================================================================
# Called by deploy-all.sh after CDK stacks are deployed.
# Updates the Lambda env var with the RDS endpoint, then registers the
# AgentCore Gateway as an MCP server in DevOps Agent.
#
# DevOps Agent reaches the Gateway over its public HTTPS endpoint, authenticated
# via Cognito OAuth client-credentials (TLS-encrypted). No VPC/private connection
# is required for this managed, authenticated path.
#
# Usage:
#   source scripts/deploy-mcp-server.sh
#   deploy_mcp_server <project_name> <environment>
# =============================================================================

deploy_mcp_server() {
    local PROJECT_NAME="$1"
    local ENVIRONMENT="$2"
    local REGION="${AWS_REGION:-us-east-1}"
    local DA_REGION="${DEVOPS_AGENT_REGION:-us-east-1}"
    local STACK_NAME="DevOpsAgentEksMcpServer-$REGION"

    echo "  Retrieving MCP server stack outputs..."

    local GATEWAY_ID=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='McpGatewayId'].OutputValue" \
        --output text --region "$REGION")

    local LAMBDA_NAME=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='McpLambdaFunctionName'].OutputValue" \
        --output text --region "$REGION")

    if [ -z "$GATEWAY_ID" ] || [ "$GATEWAY_ID" = "None" ]; then
        echo "  ERROR: Could not retrieve MCP server stack outputs."
        return 1
    fi

    echo "  Gateway ID: $GATEWAY_ID"
    echo "  Lambda:     $LAMBDA_NAME"

    # -----------------------------------------------------------------------
    # Update Lambda with RDS endpoint
    # -----------------------------------------------------------------------
    local RDS_ENDPOINT=$(aws cloudformation describe-stacks \
        --stack-name "DevOpsAgentEksDatabase-$REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='RdsEndpoint'].OutputValue" \
        --output text --region "$REGION")

    echo "  Setting DB_HOST on Lambda..."
    local LAMBDA_ENV=$(aws lambda get-function-configuration \
        --function-name "$LAMBDA_NAME" \
        --query 'Environment.Variables' \
        --output json --region "$REGION" --no-cli-pager 2>/dev/null)
    LAMBDA_ENV=$(echo "$LAMBDA_ENV" | jq --arg host "$RDS_ENDPOINT" '.DB_HOST = $host')
    aws lambda update-function-configuration \
        --function-name "$LAMBDA_NAME" \
        --environment "Variables=$LAMBDA_ENV" \
        --region "$REGION" --no-cli-pager >/dev/null 2>&1
    echo "  ✓ Lambda environment updated"

    # -----------------------------------------------------------------------
    # Sync mcp_readonly DB password (in case CDK regenerated the secret)
    # -----------------------------------------------------------------------
    echo "  Syncing mcp_readonly DB password..."
    local MCP_SECRET_ARN=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='McpSecretArn'].OutputValue" \
        --output text --region "$REGION" 2>/dev/null || echo "")
    if [ -n "$MCP_SECRET_ARN" ] && [ "$MCP_SECRET_ARN" != "None" ]; then
        local MCP_DB_PASSWORD=$(aws secretsmanager get-secret-value \
            --secret-id "$MCP_SECRET_ARN" --region "$REGION" \
            --query SecretString --output text --no-cli-pager 2>/dev/null | jq -r '.password // empty')
        local ADMIN_PASSWORD=$(aws secretsmanager get-secret-value \
            --secret-id "devops-agent-eks-dev-rds-credentials" --region "$REGION" \
            --query SecretString --output text --no-cli-pager 2>/dev/null | jq -r '.password // empty')
        if [ -n "$MCP_DB_PASSWORD" ] && [ -n "$ADMIN_PASSWORD" ] && [ -n "$RDS_ENDPOINT" ]; then
            kubectl run db-sync-mcp-pw --rm -i --restart=Never \
                --namespace=payment-demo \
                --image=postgres:15 \
                --env="PGPASSWORD=${ADMIN_PASSWORD}" \
                -- psql -h "$RDS_ENDPOINT" -U paymentadmin -d paymentdb -c \
                "CREATE ROLE mcp_readonly WITH LOGIN PASSWORD '${MCP_DB_PASSWORD}'; GRANT CONNECT ON DATABASE paymentdb TO mcp_readonly; GRANT USAGE ON SCHEMA public TO mcp_readonly; GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_readonly; ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcp_readonly;" \
                >/dev/null 2>&1 \
            || kubectl run db-sync-mcp-pw2 --rm -i --restart=Never \
                --namespace=payment-demo \
                --image=postgres:15 \
                --env="PGPASSWORD=${ADMIN_PASSWORD}" \
                -- psql -h "$RDS_ENDPOINT" -U paymentadmin -d paymentdb -c \
                "ALTER ROLE mcp_readonly WITH PASSWORD '${MCP_DB_PASSWORD}';" \
                >/dev/null 2>&1 \
            && echo "  ✓ mcp_readonly password synced" || echo "  ⚠ Could not sync mcp_readonly password (nodes may not be ready)"
        fi
    fi

    # -----------------------------------------------------------------------
    # Get Gateway endpoint and OAuth credentials
    # Wait for Gateway authorizer to be ready (can take a minute after CDK deploy)
    # -----------------------------------------------------------------------
    local MCP_ENDPOINT=""
    local GATEWAY_AUTH=""
    local COGNITO_CLIENT_ID=""
    local COGNITO_DISCOVERY_URL=""

    echo "  Retrieving Gateway endpoint and OAuth credentials..."
    for attempt in $(seq 1 18); do  # up to 3 minutes
        MCP_ENDPOINT=$(aws bedrock-agentcore-control get-gateway \
            --gateway-identifier "$GATEWAY_ID" --region "$REGION" \
            --query "gatewayUrl" --output text --no-cli-pager 2>/dev/null || echo "")
        GATEWAY_AUTH=$(aws bedrock-agentcore-control get-gateway \
            --gateway-identifier "$GATEWAY_ID" --region "$REGION" \
            --query "authorizerConfiguration.customJWTAuthorizer" \
            --output json --no-cli-pager 2>/dev/null || echo "")
        COGNITO_CLIENT_ID=$(echo "$GATEWAY_AUTH" | jq -r '.allowedClients[0] // empty' 2>/dev/null || echo "")
        COGNITO_DISCOVERY_URL=$(echo "$GATEWAY_AUTH" | jq -r '.discoveryUrl // empty' 2>/dev/null || echo "")

        if [ -n "$COGNITO_CLIENT_ID" ] && [ -n "$COGNITO_DISCOVERY_URL" ] && [ -n "$MCP_ENDPOINT" ]; then
            break
        fi
        if [ "$attempt" -eq 18 ]; then
            echo "  ERROR: Gateway authorizer not ready after 3 minutes."
            echo "  GATEWAY_AUTH: $GATEWAY_AUTH"
            return 1
        fi
        echo "  Gateway authorizer not ready yet ($attempt/18)... waiting 10s"
        sleep 10
    done

    echo "  MCP endpoint: $MCP_ENDPOINT"

    # Extract Cognito pool ID from discovery URL
    # URL format: https://cognito-idp.{region}.amazonaws.com/{poolId}/.well-known/openid-configuration
    local COGNITO_POOL_ID=$(echo "$COGNITO_DISCOVERY_URL" | awk -F'/' '{print $4}')
    local COGNITO_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
        --user-pool-id "$COGNITO_POOL_ID" --client-id "$COGNITO_CLIENT_ID" \
        --region "$REGION" --query "UserPoolClient.ClientSecret" \
        --output text --no-cli-pager 2>/dev/null)
    local COGNITO_DOMAIN=$(aws cognito-idp describe-user-pool \
        --user-pool-id "$COGNITO_POOL_ID" --region "$REGION" \
        --query "UserPool.Domain" --output text --no-cli-pager 2>/dev/null)
    local COGNITO_TOKEN_URL="https://${COGNITO_DOMAIN}.auth.${REGION}.amazoncognito.com/oauth2/token"

    if [ -z "$COGNITO_CLIENT_SECRET" ] || [ "$COGNITO_CLIENT_SECRET" = "None" ]; then
        echo "  ERROR: Could not retrieve Cognito client secret."
        echo "  Pool ID: $COGNITO_POOL_ID, Client ID: $COGNITO_CLIENT_ID"
        return 1
    fi

    # -----------------------------------------------------------------------
    # Register Gateway as MCP server in DevOps Agent
    # -----------------------------------------------------------------------
    # DevOps Agent connects to the AgentCore Gateway over its public HTTPS
    # endpoint, authenticated via Cognito OAuth client-credentials (TLS-encrypted).
    # No VPC/private connection is required for this managed, authenticated path.
    # -----------------------------------------------------------------------
    echo "  Registering MCP server in DevOps Agent..."

    local MCP_SERVICE_ID=$(aws devops-agent list-services \
        --region "$DA_REGION" --no-cli-pager --output json 2>/dev/null \
        | jq -r '.services[] | select(.serviceType=="mcpserver") | .serviceId' 2>/dev/null || echo "")

    if [ -n "$MCP_SERVICE_ID" ] && [ "$MCP_SERVICE_ID" != "None" ] && [ "$MCP_SERVICE_ID" != "" ]; then
        echo "  ✓ MCP server already registered: $MCP_SERVICE_ID"
    else
        local COGNITO_SCOPES=$(aws cognito-idp describe-user-pool-client \
            --user-pool-id "$COGNITO_POOL_ID" --client-id "$COGNITO_CLIENT_ID" \
            --region "$REGION" --query "UserPoolClient.AllowedOAuthScopes" \
            --output json --no-cli-pager 2>/dev/null || echo "[]")

        local REGISTER_RESULT=""
        REGISTER_RESULT=$(aws devops-agent register-service \
            --service mcpserver \
            --name "pay-txn-mcp" \
            --service-details "{\"mcpserver\":{\"name\":\"pay-txn-mcp\",\"endpoint\":\"$MCP_ENDPOINT\",\"description\":\"Read-only payment transaction insights for incident investigation\",\"authorizationConfig\":{\"oAuthClientCredentials\":{\"clientName\":\"mcp-gateway-client\",\"clientId\":\"$COGNITO_CLIENT_ID\",\"clientSecret\":\"$COGNITO_CLIENT_SECRET\",\"exchangeUrl\":\"$COGNITO_TOKEN_URL\",\"scopes\":$COGNITO_SCOPES}}}}" \
            --region "$DA_REGION" \
            --output json --no-cli-pager 2>&1) || true

        if echo "$REGISTER_RESULT" | grep -q "serviceId"; then
            MCP_SERVICE_ID=$(echo "$REGISTER_RESULT" | jq -r '.serviceId // empty' 2>/dev/null || echo "")
            echo "  ✓ MCP server registered: $MCP_SERVICE_ID"
        else
            echo "  WARNING: Could not register MCP server."
            echo "  $REGISTER_RESULT"
            return 0
        fi
    fi

    # -----------------------------------------------------------------------
    # Associate MCP server with Agent Space
    # -----------------------------------------------------------------------
    local AGENT_SPACE_ID="${DEVOPS_AGENT_SPACE_ID:-}"
    if [ -z "$AGENT_SPACE_ID" ]; then
        AGENT_SPACE_ID=$(aws devops-agent list-agent-spaces \
            --region "$DA_REGION" \
            --query "agentSpaces[?name=='$PROJECT_NAME'].agentSpaceId | [0]" \
            --output text --no-cli-pager 2>/dev/null || echo "")
        if [ -z "$AGENT_SPACE_ID" ] || [ "$AGENT_SPACE_ID" = "None" ]; then
            AGENT_SPACE_ID=$(aws devops-agent list-agent-spaces \
                --region "$DA_REGION" \
                --query "agentSpaces[0].agentSpaceId" \
                --output text --no-cli-pager 2>/dev/null || echo "")
        fi
    fi

    if [ -z "$AGENT_SPACE_ID" ] || [ "$AGENT_SPACE_ID" = "None" ]; then
        echo "  WARNING: Agent Space not found."
        return 0
    fi

    echo "  Associating with Agent Space $AGENT_SPACE_ID..."
    aws devops-agent associate-service \
        --agent-space-id "$AGENT_SPACE_ID" \
        --service-id "$MCP_SERVICE_ID" \
        --region "$DA_REGION" \
        --no-cli-pager >/dev/null 2>&1 || true

    echo "  ✓ MCP server registered and associated with Agent Space."
    echo "    NOTE: MCP tools are not enabled by default."
    echo "    Enable them in the DevOps Agent Console → Capabilities → MCP Servers → pay-txn-mcp → select tools."
    echo ""
    return 0
}
