# =============================================================================
# DevOps Agent Setup Script (PowerShell)
# =============================================================================
# Creates the AWS DevOps Agent Space, IAM roles, and account association.
# After running this script, generate a generic webhook in the DevOps Agent
# console (Capabilities > Webhook > Add), then set the env vars:
#   $env:DEVOPS_AGENT_WEBHOOK_URL = "https://event-ai.us-east-1.api.aws/webhook/generic/<id>"
#   $env:DEVOPS_AGENT_WEBHOOK_SECRET = "<secret>"
# and re-run deploy-all.ps1 to enable the alarm > webhook integration.
#
# Usage:
#   .\scripts\setup-devops-agent.ps1 [-AgentSpaceName "my-space"]
# =============================================================================

param(
    [string]$AgentSpaceName = "devops-agent-eks"
)

$ErrorActionPreference = "Stop"

$DevOpsAgentEndpoint = "https://api.prod.cp.aidevops.us-east-1.api.aws"
$DevOpsAgentRegion = "us-east-1"

Write-Host "=============================================="
Write-Host " DevOps Agent Setup"
Write-Host "=============================================="
Write-Host ""

# ---------------------------------------------------------------------------
# Validate prerequisites
# ---------------------------------------------------------------------------
$AwsAccountId = aws sts get-caller-identity --query Account --output text
Write-Host "Account:          $AwsAccountId"
Write-Host "Agent Space Name: $AgentSpaceName"
Write-Host "Region:           $DevOpsAgentRegion (DevOps Agent is us-east-1 only)"
Write-Host ""

# Auto-patch AWS CLI with DevOps Agent service model if not already available
aws devopsagent help 2>$null | Out-Null
$devopsAgentAvailable = ($LASTEXITCODE -eq 0)

if (-not $devopsAgentAvailable) {
    Write-Host "  DevOps Agent CLI not found - patching AWS CLI..."
    try {
        Invoke-WebRequest -Uri "https://d1co8nkiwcta1g.cloudfront.net/devopsagent.json" -OutFile "$env:TEMP\devopsagent.json" -ErrorAction Stop
        aws configure add-model --service-model "file://$env:TEMP\devopsagent.json" --service-name devopsagent
        Write-Host "  AWS CLI patched with DevOps Agent service model."
    } catch {
        Write-Host "ERROR: Failed to download DevOps Agent service model." -ForegroundColor Red
        Write-Host "  Manual fix: Invoke-WebRequest -Uri 'https://d1co8nkiwcta1g.cloudfront.net/devopsagent.json' -OutFile devopsagent.json"
        Write-Host "              aws configure add-model --service-model file://devopsagent.json --service-name devopsagent"
        exit 1
    }

    # Verify the patch actually works
    aws devopsagent list-agent-spaces --endpoint-url $DevOpsAgentEndpoint --region $DevOpsAgentRegion --query 'agentSpaces' --output text 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: DevOps Agent CLI patch failed. The 'devopsagent' command is not recognized." -ForegroundColor Red
        Write-Host "  This may mean the service model is incompatible with your AWS CLI version." -ForegroundColor Yellow
        Write-Host "  Try updating AWS CLI: winget upgrade AWSCLIV2" -ForegroundColor Gray
        exit 1
    }
} else {
    Write-Host "  DevOps Agent CLI already available."
}

# ---------------------------------------------------------------------------
# Step 1: Create IAM roles
# ---------------------------------------------------------------------------
Write-Host "[1/4] Creating IAM roles..."

# --- Agent Space Role ---
$AgentSpaceRole = "$AgentSpaceName-AgentSpaceRole"
aws iam get-role --role-name $AgentSpaceRole 2>$null | Out-Null
$roleExists = ($LASTEXITCODE -eq 0)

if ($roleExists) {
    Write-Host "  IAM role '$AgentSpaceRole' already exists."
} else {
    Write-Host "  Creating IAM role '$AgentSpaceRole'..."
    $trustPolicy = @"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "aidevops.amazonaws.com" },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": { "aws:SourceAccount": "$AwsAccountId" },
        "ArnLike": { "aws:SourceArn": "arn:aws:aidevops:${DevOpsAgentRegion}:${AwsAccountId}:agentspace/*" }
      }
    }
  ]
}
"@
    $trustPolicy | Out-File -FilePath "$env:TEMP\devops-agentspace-trust-policy.json" -Encoding UTF8
    aws iam create-role `
        --role-name $AgentSpaceRole `
        --assume-role-policy-document "file://$env:TEMP\devops-agentspace-trust-policy.json" `
        --region $DevOpsAgentRegion | Out-Null

    aws iam attach-role-policy `
        --role-name $AgentSpaceRole `
        --policy-arn "arn:aws:iam::aws:policy/AIOpsAssistantPolicy"

    $inlinePolicy = @'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowExpandedAIOpsAssistantPolicy",
      "Effect": "Allow",
      "Action": [
        "aidevops:GetKnowledgeItem", "aidevops:ListKnowledgeItems",
        "eks:AccessKubernetesApi", "synthetics:GetCanaryRuns",
        "route53:GetHealthCheckStatus", "resource-explorer-2:Search",
        "support:CreateCase", "support:DescribeCases"
      ],
      "Resource": ["*"]
    }
  ]
}
'@
    $inlinePolicy | Out-File -FilePath "$env:TEMP\devops-agentspace-inline-policy.json" -Encoding UTF8
    aws iam put-role-policy `
        --role-name $AgentSpaceRole `
        --policy-name AllowExpandedAIOpsAssistantPolicy `
        --policy-document "file://$env:TEMP\devops-agentspace-inline-policy.json"

    Write-Host "  Role '$AgentSpaceRole' created with AIOpsAssistantPolicy + EKS access."
}

$AgentSpaceRoleArn = aws iam get-role --role-name $AgentSpaceRole --query 'Role.Arn' --output text
Write-Host "  Role ARN: $AgentSpaceRoleArn"

# --- Operator App Role ---
$OperatorRole = "$AgentSpaceName-OperatorRole"
aws iam get-role --role-name $OperatorRole 2>$null | Out-Null
$operatorExists = ($LASTEXITCODE -eq 0)

if ($operatorExists) {
    Write-Host "  IAM role '$OperatorRole' already exists."
} else {
    Write-Host "  Creating IAM role '$OperatorRole'..."
    $operatorTrust = @"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "aidevops.amazonaws.com" },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": { "aws:SourceAccount": "$AwsAccountId" },
        "ArnLike": { "aws:SourceArn": "arn:aws:aidevops:${DevOpsAgentRegion}:${AwsAccountId}:agentspace/*" }
      }
    }
  ]
}
"@
    $operatorTrust | Out-File -FilePath "$env:TEMP\devops-operator-trust-policy.json" -Encoding UTF8
    aws iam create-role `
        --role-name $OperatorRole `
        --assume-role-policy-document "file://$env:TEMP\devops-operator-trust-policy.json" `
        --region $DevOpsAgentRegion | Out-Null

    $operatorPolicy = @"
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
      "Resource": "arn:aws:aidevops:${DevOpsAgentRegion}:${AwsAccountId}:agentspace/*"
    },
    {
      "Sid": "AllowSupportOperatorActions",
      "Effect": "Allow",
      "Action": ["support:DescribeCases", "support:InitiateChatForCase", "support:DescribeSupportLevel"],
      "Resource": "*"
    }
  ]
}
"@
    $operatorPolicy | Out-File -FilePath "$env:TEMP\devops-operator-inline-policy.json" -Encoding UTF8
    aws iam put-role-policy `
        --role-name $OperatorRole `
        --policy-name AIDevOpsBasicOperatorActionsPolicy `
        --policy-document "file://$env:TEMP\devops-operator-inline-policy.json"

    Write-Host "  Role '$OperatorRole' created."
}

$OperatorRoleArn = aws iam get-role --role-name $OperatorRole --query 'Role.Arn' --output text
Write-Host "  Role ARN: $OperatorRoleArn"
Write-Host ""

Write-Host "  Waiting 10 seconds for IAM role propagation..."
Start-Sleep -Seconds 10

# ---------------------------------------------------------------------------
# Step 2: Create Agent Space
# ---------------------------------------------------------------------------
Write-Host "[2/4] Creating Agent Space..."

$existingSpaces = aws devopsagent list-agent-spaces `
    --endpoint-url $DevOpsAgentEndpoint `
    --region $DevOpsAgentRegion `
    --query 'agentSpaces[0].agentSpaceId' `
    --output text 2>$null

if ($existingSpaces -and $existingSpaces -ne "None") {
    $AgentSpaceId = $existingSpaces
    Write-Host "  Agent Space already exists: $AgentSpaceId"
} else {
    $AgentSpaceId = aws devopsagent create-agent-space `
        --name $AgentSpaceName `
        --description "Agent Space for EKS incident investigation demo" `
        --endpoint-url $DevOpsAgentEndpoint `
        --region $DevOpsAgentRegion `
        --query 'agentSpace.agentSpaceId' `
        --output text
    Write-Host "  Agent Space created: $AgentSpaceId"
}
Write-Host ""

# ---------------------------------------------------------------------------
# Step 3: Associate AWS account
# ---------------------------------------------------------------------------
Write-Host "[3/4] Associating AWS account..."

$existingAssoc = aws devopsagent list-associations `
    --agent-space-id $AgentSpaceId `
    --endpoint-url $DevOpsAgentEndpoint `
    --region $DevOpsAgentRegion `
    --query "associations[?serviceId=='aws'].associationId" `
    --output text 2>$null

if ($existingAssoc -and $existingAssoc -ne "None") {
    Write-Host "  AWS account already associated."
} else {
    $config = @"
{"aws":{"assumableRoleArn":"$AgentSpaceRoleArn","accountId":"$AwsAccountId","accountType":"monitor","resources":[]}}
"@
    aws devopsagent associate-service `
        --agent-space-id $AgentSpaceId `
        --service-id aws `
        --configuration $config `
        --endpoint-url $DevOpsAgentEndpoint `
        --region $DevOpsAgentRegion | Out-Null
    Write-Host "  AWS account $AwsAccountId associated."
}
Write-Host ""

# ---------------------------------------------------------------------------
# Step 4: Enable Operator App
# ---------------------------------------------------------------------------
Write-Host "[4/4] Enabling Operator App..."

aws devopsagent enable-operator-app `
    --agent-space-id $AgentSpaceId `
    --auth-flow iam `
    --operator-app-role-arn $OperatorRoleArn `
    --endpoint-url $DevOpsAgentEndpoint `
    --region $DevOpsAgentRegion 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  (already enabled)"
}
Write-Host "  Operator App enabled."
Write-Host ""

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host "=============================================="
Write-Host " DevOps Agent Setup Complete"
Write-Host "=============================================="
Write-Host ""
Write-Host "Agent Space ID:   $AgentSpaceId"
Write-Host "Agent Space Role: $AgentSpaceRoleArn"
Write-Host "Operator Role:    $OperatorRoleArn"
Write-Host ""

# ---------------------------------------------------------------------------
# Interactive webhook configuration
# ---------------------------------------------------------------------------
Write-Host "=============================================="
Write-Host " Webhook Configuration"
Write-Host "=============================================="
Write-Host ""
Write-Host "Generate a generic webhook in the DevOps Agent console (~30 seconds):"
Write-Host ""
Write-Host "  1. Open: https://$DevOpsAgentRegion.console.aws.amazon.com/aidevops/home#/agent-spaces"
Write-Host "  2. Select '$AgentSpaceName' > Capabilities > Webhook > Add"
Write-Host "  3. Click Next on the wizard, then click 'Generate URL and secret key'"
Write-Host "  4. Copy the webhook URL and secret key"
Write-Host ""

$WebhookUrl = Read-Host "Paste the webhook URL (or press Enter to skip)"
if ($WebhookUrl) {
    $WebhookSecret = Read-Host "Paste the webhook secret key"
    if ($WebhookSecret) {
        $env:DEVOPS_AGENT_WEBHOOK_URL = $WebhookUrl
        $env:DEVOPS_AGENT_WEBHOOK_SECRET = $WebhookSecret
        Write-Host ""
        Write-Host "  Webhook configured. Environment variables set:" -ForegroundColor Green
        Write-Host "    DEVOPS_AGENT_WEBHOOK_URL=$env:DEVOPS_AGENT_WEBHOOK_URL"
        Write-Host "    DEVOPS_AGENT_WEBHOOK_SECRET=****"
        Write-Host ""
        Write-Host "  You can now deploy with DevOps Agent integration:"
        Write-Host "    .\deploy-all.ps1 -Environment dev"
    } else {
        Write-Host ""
        Write-Host "  No secret provided - skipping webhook configuration."
        Write-Host '  You can set them later before deploying:'
        Write-Host '    $env:DEVOPS_AGENT_WEBHOOK_URL = "<url>"'
        Write-Host '    $env:DEVOPS_AGENT_WEBHOOK_SECRET = "<secret>"'
    }
} else {
    Write-Host "  Skipped. You can configure the webhook later before deploying:"
    Write-Host '    $env:DEVOPS_AGENT_WEBHOOK_URL = "<url>"'
    Write-Host '    $env:DEVOPS_AGENT_WEBHOOK_SECRET = "<secret>"'
}
Write-Host ""
