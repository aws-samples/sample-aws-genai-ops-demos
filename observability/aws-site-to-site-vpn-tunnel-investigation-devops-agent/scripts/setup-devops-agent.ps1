# setup-devops-agent.ps1 — Create DevOps Agent Space, IAM roles, and webhook
#
# Usage:
#   .\setup-devops-agent.ps1 [-Region <region>] [-Name <agent-space-name>]
#
# This script automates the DevOps Agent onboarding:
#   1. Creates IAM roles (AgentSpace + Operator)
#   2. Creates an Agent Space
#   3. Associates the AWS account
#   4. Enables the Operator App
#   5. Creates a generic webhook (for alarm → agent integration)
#   6. Prints webhook URL + secret for use with deploy-all.ps1
#
# Reference: https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-cli-onboarding-guide.html

param(
    [string]$Region = "",
    [string]$Name = "vpn-demo-agent-space"
)

$ErrorActionPreference = "Continue"

if ([string]::IsNullOrEmpty($Region)) {
    $Region = $env:AWS_DEFAULT_REGION
    if ([string]::IsNullOrEmpty($Region)) { $Region = $env:AWS_REGION }
    if ([string]::IsNullOrEmpty($Region)) { $Region = aws configure get region 2>$null }
}
if ([string]::IsNullOrEmpty($Region)) {
    Write-Host "ERROR: -Region is required (or set via 'aws configure' or AWS_DEFAULT_REGION)" -ForegroundColor Red
    Write-Host "Supported regions: us-east-1, us-west-2, ap-southeast-2, ap-northeast-1, eu-central-1, eu-west-1"
    exit 1
}

$accountId = aws sts get-caller-identity --query Account --output text --no-cli-pager
Write-Host "Account: $accountId  Region: $Region"
Write-Host ""

$tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) "vpn-demo-setup-$(Get-Random)"
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
# AWS CLI file:// requires forward slashes on Windows
$tmpDirUri = $tmpDir.Replace('\', '/')

try {
    # ============================================================
    Write-Host "=== Step 1: Create IAM roles ===" -ForegroundColor Cyan
    # ============================================================

    # 1a. Agent Space role
    Write-Host "Creating DevOpsAgentRole-AgentSpace..."
    $agentspaceTrust = @"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "aidevops.amazonaws.com" },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": { "aws:SourceAccount": "$accountId" },
        "ArnLike": { "aws:SourceArn": "arn:aws:aidevops:${Region}:${accountId}:agentspace/*" }
      }
    }
  ]
}
"@
    $agentspaceTrustFile = Join-Path $tmpDir "agentspace-trust.json"
    $agentspaceTrust | Set-Content -Path $agentspaceTrustFile -Encoding ASCII

    aws iam create-role --role-name DevOpsAgentRole-AgentSpace `
        --assume-role-policy-document "file://$($agentspaceTrustFile.Replace('\', '/'))" `
        --query 'Role.Arn' --output text --no-cli-pager 2>$null
    if ($LASTEXITCODE -ne 0) {
        aws iam get-role --role-name DevOpsAgentRole-AgentSpace --query 'Role.Arn' --output text --no-cli-pager
    }

    # Always update trust policy to match current region (role may exist from a different region)
    aws iam update-assume-role-policy --role-name DevOpsAgentRole-AgentSpace `
        --policy-document "file://$($agentspaceTrustFile.Replace('\', '/'))" --no-cli-pager

    aws iam attach-role-policy --role-name DevOpsAgentRole-AgentSpace `
        --policy-arn arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy --no-cli-pager 2>$null

    $agentspaceInline = @"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowCreateServiceLinkedRoles",
      "Effect": "Allow",
      "Action": ["iam:CreateServiceLinkedRole"],
      "Resource": ["arn:aws:iam::${accountId}:role/aws-service-role/resource-explorer-2.amazonaws.com/AWSServiceRoleForResourceExplorer"]
    }
  ]
}
"@
    $agentspaceInlineFile = Join-Path $tmpDir "agentspace-inline.json"
    $agentspaceInline | Set-Content -Path $agentspaceInlineFile -Encoding ASCII

    aws iam put-role-policy --role-name DevOpsAgentRole-AgentSpace `
        --policy-name AllowCreateServiceLinkedRoles `
        --policy-document "file://$($agentspaceInlineFile.Replace('\', '/'))" --no-cli-pager 2>$null

    $agentspaceRoleArn = "arn:aws:iam::${accountId}:role/DevOpsAgentRole-AgentSpace"
    Write-Host "  OK: $agentspaceRoleArn" -ForegroundColor Green

    # 1b. Operator App role
    Write-Host "Creating DevOpsAgentRole-WebappAdmin..."
    $operatorTrust = @"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "aidevops.amazonaws.com" },
      "Action": ["sts:AssumeRole", "sts:TagSession"],
      "Condition": {
        "StringEquals": { "aws:SourceAccount": "$accountId" },
        "ArnLike": { "aws:SourceArn": "arn:aws:aidevops:${Region}:${accountId}:agentspace/*" }
      }
    }
  ]
}
"@
    $operatorTrustFile = Join-Path $tmpDir "operator-trust.json"
    $operatorTrust | Set-Content -Path $operatorTrustFile -Encoding ASCII

    aws iam create-role --role-name DevOpsAgentRole-WebappAdmin `
        --assume-role-policy-document "file://$($operatorTrustFile.Replace('\', '/'))" `
        --query 'Role.Arn' --output text --no-cli-pager 2>$null
    if ($LASTEXITCODE -ne 0) {
        aws iam get-role --role-name DevOpsAgentRole-WebappAdmin --query 'Role.Arn' --output text --no-cli-pager
    }

    # Always update trust policy to match current region (role may exist from a different region)
    aws iam update-assume-role-policy --role-name DevOpsAgentRole-WebappAdmin `
        --policy-document "file://$($operatorTrustFile.Replace('\', '/'))" --no-cli-pager

    aws iam attach-role-policy --role-name DevOpsAgentRole-WebappAdmin `
        --policy-arn arn:aws:iam::aws:policy/AIDevOpsOperatorAppAccessPolicy --no-cli-pager 2>$null

    $operatorRoleArn = "arn:aws:iam::${accountId}:role/DevOpsAgentRole-WebappAdmin"
    Write-Host "  OK: $operatorRoleArn" -ForegroundColor Green

    Write-Host "  Waiting 10s for IAM propagation..."
    Start-Sleep -Seconds 10

    # ============================================================
    Write-Host ""
    Write-Host "=== Step 2: Create Agent Space ===" -ForegroundColor Cyan
    # ============================================================

    $agentSpaceId = aws devops-agent create-agent-space `
        --name $Name `
        --description "Agent Space for VPN tunnel investigation demo" `
        --region $Region `
        --query 'agentSpace.agentSpaceId' --output text --no-cli-pager

    Write-Host "  OK: Agent Space ID: $agentSpaceId" -ForegroundColor Green

    # ============================================================
    Write-Host ""
    Write-Host "=== Step 3: Associate AWS account ===" -ForegroundColor Cyan
    # ============================================================

    $configJson = @"
{"aws":{"assumableRoleArn":"$agentspaceRoleArn","accountId":"$accountId","accountType":"monitor"}}
"@
    $configFile = Join-Path $tmpDir "config.json"
    $configJson | Set-Content -Path $configFile -Encoding ASCII -NoNewline
    aws devops-agent associate-service `
        --agent-space-id $agentSpaceId `
        --service-id aws `
        --configuration "file://$($configFile.Replace('\', '/'))" `
        --region $Region --no-cli-pager | Out-Null

    Write-Host "  OK: Account $accountId associated" -ForegroundColor Green

    # ============================================================
    Write-Host ""
    Write-Host "=== Step 4: Enable Operator App ===" -ForegroundColor Cyan
    # ============================================================

    aws devops-agent enable-operator-app `
        --agent-space-id $agentSpaceId `
        --auth-flow iam `
        --operator-app-role-arn $operatorRoleArn `
        --region $Region --no-cli-pager | Out-Null

    $operatorUrl = "https://${agentSpaceId}.aidevops.global.app.aws/home"
    Write-Host "  OK: Operator App enabled" -ForegroundColor Green
    Write-Host "  URL: $operatorUrl"

    # ============================================================
    Write-Host ""
    Write-Host "=== Step 5: Create webhook ===" -ForegroundColor Cyan
    Write-Host "  WARNING: You need to create the webhook in the AWS DevOps Agent console." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  1. Open the AWS DevOps Agent console: https://console.aws.amazon.com/aidevops/"
    Write-Host "  2. Select your Agent Space: $Name"
    Write-Host "  3. Go to the Capabilities tab"
    Write-Host "  4. In the Webhooks section, click Add webhook"
    Write-Host "  5. Click Next through the schema and HMAC steps"
    Write-Host "  6. Click 'Generate URL and secret key'"
    Write-Host "  7. Copy the Webhook URL and Secret key, then click Add"
    Write-Host ""
    $webhookUrl = Read-Host "  Paste your Webhook URL"
    $webhookSecret = Read-Host "  Paste your Webhook Secret"
    # ============================================================

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  DEVOPS AGENT SETUP COMPLETE" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Agent Space ID : $agentSpaceId" -ForegroundColor Cyan
    Write-Host "  Agent Space Name: $Name" -ForegroundColor Cyan
    Write-Host "  Region         : $Region" -ForegroundColor Cyan
    Write-Host "  Account        : $accountId" -ForegroundColor Cyan
    Write-Host "  Operator App   : $operatorUrl" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Use these with deploy-all.ps1:"
    Write-Host "  -WebhookUrl '$webhookUrl'"
    Write-Host "  -WebhookSecret '$webhookSecret'"
    Write-Host ""
    Write-Host "  Full deploy command:"
    Write-Host "  .\deploy-all.ps1 ``"
    Write-Host "    -KeyFile <your-key-file> ``"
    Write-Host "    -KeyPair <your-key-pair> ``"
    Write-Host "    -WebhookUrl '$webhookUrl' ``"
    Write-Host "    -WebhookSecret '$webhookSecret'"
    Write-Host "========================================" -ForegroundColor Green

} finally {
    Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
}
