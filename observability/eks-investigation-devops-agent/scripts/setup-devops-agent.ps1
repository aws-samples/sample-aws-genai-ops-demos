# =============================================================================
# DevOps Agent Setup Script (PowerShell) — GA Version
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
#   .\scripts\setup-devops-agent.ps1 [-AgentSpaceName "my-space"]
# =============================================================================

param(
    [string]$AgentSpaceName = "devops-agent-eks"
)

$ErrorActionPreference = "Stop"

$DevOpsAgentRegion = if ($env:DEVOPS_AGENT_REGION) { $env:DEVOPS_AGENT_REGION } else { "us-east-1" }

Write-Host "=============================================="
Write-Host " DevOps Agent Setup (GA)"
Write-Host "=============================================="
Write-Host ""

$AwsAccountId = aws sts get-caller-identity --query Account --output text
Write-Host "Account:          $AwsAccountId"
Write-Host "Agent Space Name: $AgentSpaceName"
Write-Host "Region:           $DevOpsAgentRegion"
Write-Host ""

# ---------------------------------------------------------------------------
# Step 1: Create Agent Space (idempotent)
# ---------------------------------------------------------------------------
Write-Host "[1/6] Creating Agent Space..."

$existingSpaceId = aws devops-agent list-agent-spaces `
    --region $DevOpsAgentRegion `
    --query "agentSpaces[?name=='$AgentSpaceName'].agentSpaceId | [0]" `
    --output text --no-cli-pager 2>$null

if ($existingSpaceId -and $existingSpaceId -ne "None") {
    $AgentSpaceId = $existingSpaceId
    Write-Host "  Agent Space already exists: $AgentSpaceId"
} else {
    $AgentSpaceId = aws devops-agent create-agent-space `
        --name $AgentSpaceName `
        --region $DevOpsAgentRegion `
        --query 'agentSpace.agentSpaceId' `
        --output text --no-cli-pager
    if ($LASTEXITCODE -ne 0 -or -not $AgentSpaceId) {
        Write-Host "  ERROR: Failed to create Agent Space." -ForegroundColor Red
        exit 1
    }
    Write-Host "  Agent Space created: $AgentSpaceId"
    Write-Host "  Waiting for Agent Space to become active..."
    Start-Sleep -Seconds 10
}
$env:DEVOPS_AGENT_SPACE_ID = $AgentSpaceId
Write-Host ""

# ---------------------------------------------------------------------------
# Step 2: Create IAM roles (idempotent)
# ---------------------------------------------------------------------------
Write-Host "[2/6] Creating IAM roles..."

$trustPolicy = @"
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "aidevops.amazonaws.com" },
    "Action": ["sts:AssumeRole", "sts:TagSession"],
    "Condition": {
      "StringEquals": { "aws:SourceAccount": "$AwsAccountId" }
    }
  }]
}
"@
$trustPolicyFile = Join-Path $env:TEMP "devops-agent-trust.json"
$trustPolicy | Out-File -FilePath $trustPolicyFile -Encoding UTF8

# --- AgentSpaceRole: monitoring role (access to AWS resources) ---
$AgentSpaceRoleName = "$AgentSpaceName-AgentSpaceRole"
$agentRoleCheck = aws iam get-role --role-name $AgentSpaceRoleName --no-cli-pager 2>&1
if ($agentRoleCheck -match "NoSuchEntity") {
    Write-Host "  Creating '$AgentSpaceRoleName' (monitoring role)..."
    aws iam create-role `
        --role-name $AgentSpaceRoleName `
        --assume-role-policy-document "file://$trustPolicyFile" `
        --no-cli-pager | Out-Null
    aws iam attach-role-policy `
        --role-name $AgentSpaceRoleName `
        --policy-arn "arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy" `
        --no-cli-pager 2>$null
    Write-Host "  Created with AIDevOpsAgentAccessPolicy."
} else {
    Write-Host "  '$AgentSpaceRoleName' already exists."
}
$AgentSpaceRoleArn = aws iam get-role --role-name $AgentSpaceRoleName --query 'Role.Arn' --output text --no-cli-pager

# --- OperatorRole: web console role (Operator Access) ---
$OperatorRoleName = "$AgentSpaceName-OperatorRole"
$operatorRoleCheck = aws iam get-role --role-name $OperatorRoleName --no-cli-pager 2>&1
if ($operatorRoleCheck -match "NoSuchEntity") {
    Write-Host "  Creating '$OperatorRoleName' (web console role)..."
    aws iam create-role `
        --role-name $OperatorRoleName `
        --assume-role-policy-document "file://$trustPolicyFile" `
        --no-cli-pager | Out-Null
    aws iam attach-role-policy `
        --role-name $OperatorRoleName `
        --policy-arn "arn:aws:iam::aws:policy/AIDevOpsOperatorAppAccessPolicy" `
        --no-cli-pager 2>$null
    Write-Host "  Created with AIDevOpsOperatorAppAccessPolicy."
} else {
    Write-Host "  '$OperatorRoleName' already exists."
}
$OperatorRoleArn = aws iam get-role --role-name $OperatorRoleName --query 'Role.Arn' --output text --no-cli-pager

Write-Host "  Waiting for IAM propagation..."
Start-Sleep -Seconds 10
Write-Host ""

# ---------------------------------------------------------------------------
# Step 3: Enable Operator App — web console access (idempotent)
# ---------------------------------------------------------------------------
Write-Host "[3/6] Enabling Operator App (web console)..."

aws devops-agent enable-operator-app `
    --agent-space-id $AgentSpaceId `
    --auth-flow iam `
    --operator-app-role-arn $OperatorRoleArn `
    --region $DevOpsAgentRegion `
    --no-cli-pager 2>$null | Out-Null
Write-Host "  Operator App enabled (IAM auth)."
Write-Host ""

# ---------------------------------------------------------------------------
# Step 4: Associate AWS account as cloud source (idempotent)
# ---------------------------------------------------------------------------
Write-Host "[4/6] Associating AWS account (cloud source)..."

$existingAssoc = aws devops-agent list-associations `
    --agent-space-id $AgentSpaceId `
    --region $DevOpsAgentRegion `
    --query "associations[?serviceId=='aws'].associationId | [0]" `
    --output text --no-cli-pager 2>$null

if ($existingAssoc -and $existingAssoc -ne "None") {
    Write-Host "  AWS account already associated."
} else {
    $assocConfig = @"
{"aws":{"accountId":"$AwsAccountId","accountType":"monitor","assumableRoleArn":"$AgentSpaceRoleArn"}}
"@
    $assocResult = aws devops-agent associate-service `
        --agent-space-id $AgentSpaceId `
        --service-id aws `
        --configuration $assocConfig `
        --region $DevOpsAgentRegion `
        --no-cli-pager 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  WARNING: Failed to associate AWS account." -ForegroundColor Yellow
        Write-Host "  $assocResult" -ForegroundColor Yellow
        Write-Host "  You may need to add the cloud source manually in the DevOps Agent console."
    } else {
        Write-Host "  AWS account $AwsAccountId associated (monitor)."
    }
}
Write-Host ""

# ---------------------------------------------------------------------------
# Step 5: Webhook prompt
# ---------------------------------------------------------------------------
Write-Host "[5/6] Webhook Configuration"
Write-Host ""
Write-Host "  Generate a generic webhook in the DevOps Agent console (~30 seconds):"
Write-Host ""
Write-Host "  1. Open: https://$DevOpsAgentRegion.console.aws.amazon.com/aidevops/home?region=$DevOpsAgentRegion#/agent-spaces/$AgentSpaceId" -ForegroundColor Cyan
Write-Host "  2. Go to Capabilities > Webhook > Add"
Write-Host "  3. Click Next, then 'Generate URL and secret key'"
Write-Host "  4. Copy the webhook URL and secret key"
Write-Host ""

$WebhookUrl = Read-Host "  Paste the webhook URL (or press Enter to skip)"
if ($WebhookUrl) {
    $WebhookSecret = Read-Host "  Paste the webhook secret key"
    if ($WebhookSecret) {
        $env:DEVOPS_AGENT_WEBHOOK_URL = $WebhookUrl
        $env:DEVOPS_AGENT_WEBHOOK_SECRET = $WebhookSecret
        Write-Host ""
        Write-Host "  Webhook configured." -ForegroundColor Green
        Write-Host "    DEVOPS_AGENT_WEBHOOK_URL=$env:DEVOPS_AGENT_WEBHOOK_URL"
        Write-Host "    DEVOPS_AGENT_WEBHOOK_SECRET=****"
    } else {
        Write-Host "  No secret provided - skipping."
    }
} else {
    Write-Host "  Skipped. Set env vars before deploying:"
    Write-Host '    $env:DEVOPS_AGENT_WEBHOOK_URL = "<url>"'
    Write-Host '    $env:DEVOPS_AGENT_WEBHOOK_SECRET = "<secret>"'
}
Write-Host ""

# ---------------------------------------------------------------------------
# Step 6: Live-update deployed Lambdas (if demo is already deployed)
# ---------------------------------------------------------------------------
Write-Host "[6/6] Updating deployed resources (if demo is already deployed)..."
# Convention-based names: {projectName}-{env}-devops-trigger, {projectName}-{env}-failure-simulator
$Environment = "dev"
$TriggerLambda = "$AgentSpaceName-$Environment-devops-trigger"
$SimulatorLambda = "$AgentSpaceName-$Environment-failure-simulator"
$SecretName = "$AgentSpaceName-$Environment/devops-agent-webhook-secret"

# Check if the demo is deployed by testing if the trigger Lambda exists
$lambdaCheck = aws lambda get-function --function-name $TriggerLambda --no-cli-pager 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "Updating deployed resources with new Agent Space..." -ForegroundColor Yellow

    # Update webhook secret in Secrets Manager
    if ($env:DEVOPS_AGENT_WEBHOOK_SECRET) {
        aws secretsmanager update-secret `
            --secret-id $SecretName `
            --secret-string $env:DEVOPS_AGENT_WEBHOOK_SECRET `
            --no-cli-pager 2>$null | Out-Null
        Write-Host "  Updated Secrets Manager secret."
    }

    # Update trigger Lambda env vars (webhook URL)
    if ($env:DEVOPS_AGENT_WEBHOOK_URL) {
        $triggerEnv = aws lambda get-function-configuration `
            --function-name $TriggerLambda `
            --query 'Environment.Variables' `
            --output json --no-cli-pager 2>$null | ConvertFrom-Json
        $triggerEnv.WEBHOOK_URL = $env:DEVOPS_AGENT_WEBHOOK_URL
        $envJson = $triggerEnv | ConvertTo-Json -Compress
        aws lambda update-function-configuration `
            --function-name $TriggerLambda `
            --environment "Variables=$envJson" `
            --no-cli-pager 2>$null | Out-Null
        Write-Host "  Updated trigger Lambda (webhook URL)."
    }

    # Update simulator Lambda env vars (space ID)
    $simEnv = aws lambda get-function-configuration `
        --function-name $SimulatorLambda `
        --query 'Environment.Variables' `
        --output json --no-cli-pager 2>$null | ConvertFrom-Json
    $simEnv.DEVOPS_AGENT_SPACE_ID = $AgentSpaceId
    $simEnvJson = $simEnv | ConvertTo-Json -Compress
    aws lambda update-function-configuration `
        --function-name $SimulatorLambda `
        --environment "Variables=$simEnvJson" `
        --no-cli-pager 2>$null | Out-Null
    Write-Host "  Updated simulator Lambda (space ID)."

    Write-Host "  All resources updated. No CDK redeploy needed." -ForegroundColor Green
} else {
    Write-Host "Demo not yet deployed — run deploy-all.ps1 to deploy." -ForegroundColor Yellow
}
Write-Host ""
exit 0
