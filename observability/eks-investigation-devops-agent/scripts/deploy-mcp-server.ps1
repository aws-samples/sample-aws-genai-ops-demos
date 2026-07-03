# =============================================================================
# Deploy Payment Transaction Insights MCP Server (AgentCore Gateway + Lambda)
# PowerShell port of deploy-mcp-server.sh
# =============================================================================
# Dot-sourced by deploy-all.ps1 after CDK stacks are deployed.
# Updates the Lambda env var with the RDS endpoint, then registers the
# AgentCore Gateway as an MCP server in DevOps Agent.
#
# Usage:
#   . scripts/deploy-mcp-server.ps1
#   Deploy-McpServer -ProjectName <name> -Environment <env>
# =============================================================================

function Deploy-McpServer {
    param(
        [Parameter(Mandatory = $true)] [string] $ProjectName,
        [Parameter(Mandatory = $true)] [string] $Environment
    )

    $REGION = if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }
    $DA_REGION = if ($env:DEVOPS_AGENT_REGION) { $env:DEVOPS_AGENT_REGION } else { "us-east-1" }
    $STACK_NAME = "DevOpsAgentEksMcpServer-$REGION"

    Write-Host "  Retrieving MCP server stack outputs..."

    $GATEWAY_ID = aws cloudformation describe-stacks `
        --stack-name $STACK_NAME `
        --query "Stacks[0].Outputs[?OutputKey=='McpGatewayId'].OutputValue" `
        --output text --region $REGION 2>$null

    $LAMBDA_NAME = aws cloudformation describe-stacks `
        --stack-name $STACK_NAME `
        --query "Stacks[0].Outputs[?OutputKey=='McpLambdaFunctionName'].OutputValue" `
        --output text --region $REGION 2>$null

    if (-not $GATEWAY_ID -or $GATEWAY_ID -eq "None") {
        Write-Host "  ERROR: Could not retrieve MCP server stack outputs." -ForegroundColor Red
        return 1
    }

    Write-Host "  Gateway ID: $GATEWAY_ID"
    Write-Host "  Lambda:     $LAMBDA_NAME"

    # -----------------------------------------------------------------------
    # Update Lambda with RDS endpoint
    # -----------------------------------------------------------------------
    $RDS_ENDPOINT = aws cloudformation describe-stacks `
        --stack-name "DevOpsAgentEksDatabase-$REGION" `
        --query "Stacks[0].Outputs[?OutputKey=='RdsEndpoint'].OutputValue" `
        --output text --region $REGION 2>$null

    Write-Host "  Setting DB_HOST on Lambda..."
    $lambdaEnvJson = aws lambda get-function-configuration `
        --function-name $LAMBDA_NAME `
        --query "Environment.Variables" `
        --output json --region $REGION --no-cli-pager 2>$null
    if ($lambdaEnvJson) {
        $envObj = $lambdaEnvJson | ConvertFrom-Json
        $envObj.DB_HOST = $RDS_ENDPOINT
        # aws CLI on Windows needs JSON via file to avoid quote mangling
        $envPayload = @{ Variables = $envObj } | ConvertTo-Json -Compress -Depth 10
        $envFile = Join-Path $env:TEMP "mcp-lambda-env.json"
        $envPayload | Out-File -FilePath $envFile -Encoding UTF8
        aws lambda update-function-configuration `
            --function-name $LAMBDA_NAME `
            --environment "file://$envFile" `
            --region $REGION --no-cli-pager 2>$null | Out-Null
        Remove-Item -Force $envFile -ErrorAction SilentlyContinue
        Write-Host "  ✓ Lambda environment updated" -ForegroundColor Green
    }

    # -----------------------------------------------------------------------
    # Sync mcp_readonly DB password (in case CDK regenerated the secret)
    # -----------------------------------------------------------------------
    Write-Host "  Syncing mcp_readonly DB password..."
    $MCP_SECRET_ARN = aws cloudformation describe-stacks `
        --stack-name $STACK_NAME `
        --query "Stacks[0].Outputs[?OutputKey=='McpSecretArn'].OutputValue" `
        --output text --region $REGION 2>$null
    if ($MCP_SECRET_ARN -and $MCP_SECRET_ARN -ne "None") {
        $mcpSecretJson = aws secretsmanager get-secret-value `
            --secret-id $MCP_SECRET_ARN --region $REGION `
            --query SecretString --output text --no-cli-pager 2>$null
        $adminSecretJson = aws secretsmanager get-secret-value `
            --secret-id "$ProjectName-$Environment-rds-credentials" --region $REGION `
            --query SecretString --output text --no-cli-pager 2>$null
        $MCP_DB_PASSWORD = if ($mcpSecretJson) { ($mcpSecretJson | ConvertFrom-Json).password } else { "" }
        $ADMIN_PASSWORD = if ($adminSecretJson) { ($adminSecretJson | ConvertFrom-Json).password } else { "" }
        if ($MCP_DB_PASSWORD -and $ADMIN_PASSWORD -and $RDS_ENDPOINT) {
            $grantSql = "CREATE ROLE mcp_readonly WITH LOGIN PASSWORD '$MCP_DB_PASSWORD'; GRANT CONNECT ON DATABASE paymentdb TO mcp_readonly; GRANT USAGE ON SCHEMA public TO mcp_readonly; GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_readonly; ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcp_readonly;"
            kubectl run db-sync-mcp-pw --rm -i --restart=Never `
                --namespace=payment-demo `
                --image=postgres:15 `
                --env="PGPASSWORD=$ADMIN_PASSWORD" `
                -- psql -h $RDS_ENDPOINT -U paymentadmin -d paymentdb -c $grantSql 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) {
                kubectl run db-sync-mcp-pw2 --rm -i --restart=Never `
                    --namespace=payment-demo `
                    --image=postgres:15 `
                    --env="PGPASSWORD=$ADMIN_PASSWORD" `
                    -- psql -h $RDS_ENDPOINT -U paymentadmin -d paymentdb -c "ALTER ROLE mcp_readonly WITH PASSWORD '$MCP_DB_PASSWORD';" 2>$null | Out-Null
            }
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  ✓ mcp_readonly password synced" -ForegroundColor Green
            } else {
                Write-Host "  ⚠ Could not sync mcp_readonly password (nodes may not be ready)" -ForegroundColor Yellow
            }
        }
    }

    # -----------------------------------------------------------------------
    # Get Gateway endpoint and OAuth credentials
    # Wait for Gateway authorizer to be ready (can take a minute after CDK deploy)
    # -----------------------------------------------------------------------
    $MCP_ENDPOINT = ""
    $COGNITO_CLIENT_ID = ""
    $COGNITO_DISCOVERY_URL = ""

    Write-Host "  Retrieving Gateway endpoint and OAuth credentials..."
    for ($attempt = 1; $attempt -le 18; $attempt++) {  # up to 3 minutes
        $MCP_ENDPOINT = aws bedrock-agentcore-control get-gateway `
            --gateway-identifier $GATEWAY_ID --region $REGION `
            --query "gatewayUrl" --output text --no-cli-pager 2>$null
        $gatewayAuthJson = aws bedrock-agentcore-control get-gateway `
            --gateway-identifier $GATEWAY_ID --region $REGION `
            --query "authorizerConfiguration.customJWTAuthorizer" `
            --output json --no-cli-pager 2>$null
        if ($gatewayAuthJson) {
            $gatewayAuth = $gatewayAuthJson | ConvertFrom-Json
            $COGNITO_CLIENT_ID = if ($gatewayAuth.allowedClients) { $gatewayAuth.allowedClients[0] } else { "" }
            $COGNITO_DISCOVERY_URL = if ($gatewayAuth.discoveryUrl) { $gatewayAuth.discoveryUrl } else { "" }
        }

        if ($COGNITO_CLIENT_ID -and $COGNITO_DISCOVERY_URL -and $MCP_ENDPOINT -and $MCP_ENDPOINT -ne "None") {
            break
        }
        if ($attempt -eq 18) {
            Write-Host "  ERROR: Gateway authorizer not ready after 3 minutes." -ForegroundColor Red
            Write-Host "  GATEWAY_AUTH: $gatewayAuthJson"
            return 1
        }
        Write-Host "  Gateway authorizer not ready yet ($attempt/18)... waiting 10s"
        Start-Sleep -Seconds 10
    }

    Write-Host "  MCP endpoint: $MCP_ENDPOINT"

    # Extract Cognito pool ID from discovery URL
    # URL format: https://cognito-idp.{region}.amazonaws.com/{poolId}/.well-known/openid-configuration
    $COGNITO_POOL_ID = ($COGNITO_DISCOVERY_URL -split '/')[3]
    $COGNITO_CLIENT_SECRET = aws cognito-idp describe-user-pool-client `
        --user-pool-id $COGNITO_POOL_ID --client-id $COGNITO_CLIENT_ID `
        --region $REGION --query "UserPoolClient.ClientSecret" `
        --output text --no-cli-pager 2>$null
    $COGNITO_DOMAIN = aws cognito-idp describe-user-pool `
        --user-pool-id $COGNITO_POOL_ID --region $REGION `
        --query "UserPool.Domain" --output text --no-cli-pager 2>$null
    $COGNITO_TOKEN_URL = "https://$COGNITO_DOMAIN.auth.$REGION.amazoncognito.com/oauth2/token"

    if (-not $COGNITO_CLIENT_SECRET -or $COGNITO_CLIENT_SECRET -eq "None") {
        Write-Host "  ERROR: Could not retrieve Cognito client secret." -ForegroundColor Red
        Write-Host "  Pool ID: $COGNITO_POOL_ID, Client ID: $COGNITO_CLIENT_ID"
        return 1
    }

    # -----------------------------------------------------------------------
    # Register Gateway as MCP server in DevOps Agent
    # -----------------------------------------------------------------------
    # DevOps Agent connects to the AgentCore Gateway over its public HTTPS
    # endpoint, authenticated via Cognito OAuth client-credentials (TLS-encrypted).
    # No VPC/private connection is required for this managed, authenticated path.
    # -----------------------------------------------------------------------
    Write-Host "  Registering MCP server in DevOps Agent..."

    $servicesJson = aws devops-agent list-services `
        --region $DA_REGION --no-cli-pager --output json 2>$null
    $MCP_SERVICE_ID = ""
    if ($servicesJson) {
        $svc = ($servicesJson | ConvertFrom-Json).services | Where-Object { $_.serviceType -eq "mcpserver" } | Select-Object -First 1
        if ($svc) { $MCP_SERVICE_ID = $svc.serviceId }
    }

    if ($MCP_SERVICE_ID -and $MCP_SERVICE_ID -ne "None") {
        Write-Host "  ✓ MCP server already registered: $MCP_SERVICE_ID" -ForegroundColor Green
    } else {
        $scopesJson = aws cognito-idp describe-user-pool-client `
            --user-pool-id $COGNITO_POOL_ID --client-id $COGNITO_CLIENT_ID `
            --region $REGION --query "UserPoolClient.AllowedOAuthScopes" `
            --output json --no-cli-pager 2>$null
        $scopes = if ($scopesJson) { $scopesJson | ConvertFrom-Json } else { @() }

        $serviceDetails = @{
            mcpserver = @{
                name        = "pay-txn-mcp"
                endpoint    = $MCP_ENDPOINT
                description = "Read-only payment transaction insights for incident investigation"
                authorizationConfig = @{
                    oAuthClientCredentials = @{
                        clientName   = "mcp-gateway-client"
                        clientId     = $COGNITO_CLIENT_ID
                        clientSecret = $COGNITO_CLIENT_SECRET
                        exchangeUrl  = $COGNITO_TOKEN_URL
                        scopes       = @($scopes)
                    }
                }
            }
        }
        $detailsPayload = $serviceDetails | ConvertTo-Json -Compress -Depth 10
        $detailsFile = Join-Path $env:TEMP "mcp-service-details.json"
        $detailsPayload | Out-File -FilePath $detailsFile -Encoding UTF8

        $REGISTER_RESULT = aws devops-agent register-service `
            --service mcpserver `
            --name "pay-txn-mcp" `
            --service-details "file://$detailsFile" `
            --region $DA_REGION `
            --output json --no-cli-pager 2>&1
        Remove-Item -Force $detailsFile -ErrorAction SilentlyContinue

        if ($REGISTER_RESULT -match "serviceId") {
            $MCP_SERVICE_ID = (($REGISTER_RESULT | Out-String) | ConvertFrom-Json).serviceId
            Write-Host "  ✓ MCP server registered: $MCP_SERVICE_ID" -ForegroundColor Green
        } else {
            Write-Host "  WARNING: Could not register MCP server." -ForegroundColor Yellow
            Write-Host "  $REGISTER_RESULT"
            return 0
        }
    }

    # -----------------------------------------------------------------------
    # Associate MCP server with Agent Space
    # -----------------------------------------------------------------------
    $AGENT_SPACE_ID = if ($env:DEVOPS_AGENT_SPACE_ID) { $env:DEVOPS_AGENT_SPACE_ID } else { "" }
    if (-not $AGENT_SPACE_ID) {
        $AGENT_SPACE_ID = aws devops-agent list-agent-spaces `
            --region $DA_REGION `
            --query "agentSpaces[?name=='$ProjectName'].agentSpaceId | [0]" `
            --output text --no-cli-pager 2>$null
        if (-not $AGENT_SPACE_ID -or $AGENT_SPACE_ID -eq "None") {
            $AGENT_SPACE_ID = aws devops-agent list-agent-spaces `
                --region $DA_REGION `
                --query "agentSpaces[0].agentSpaceId" `
                --output text --no-cli-pager 2>$null
        }
    }

    if (-not $AGENT_SPACE_ID -or $AGENT_SPACE_ID -eq "None") {
        Write-Host "  WARNING: Agent Space not found." -ForegroundColor Yellow
        return 0
    }

    Write-Host "  Associating with Agent Space $AGENT_SPACE_ID..."
    aws devops-agent associate-service `
        --agent-space-id $AGENT_SPACE_ID `
        --service-id $MCP_SERVICE_ID `
        --region $DA_REGION `
        --no-cli-pager 2>$null | Out-Null

    Write-Host "  ✓ MCP server registered and associated with Agent Space." -ForegroundColor Green
    Write-Host "    NOTE: MCP tools are not enabled by default."
    Write-Host "    Enable them in the DevOps Agent Console -> Capabilities -> MCP Servers -> pay-txn-mcp -> select tools."
    Write-Host ""
    return 0
}
