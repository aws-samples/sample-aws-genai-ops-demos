# =============================================================================
# Setup DevOps Agent MCP Registration (PowerShell)
# =============================================================================
# Deploys SaasStatusMcpRegistrationStack to your Agent Space's region using CDK.
# The stack creates the SigV4 signing IAM role, registers the MCP server
# (AWS::DevOpsAgent::Service), and attaches it to your Agent Space with the
# four tools (AWS::DevOpsAgent::Association).
#
# Requires aws-cdk-lib >= 2.251.0 (see infrastructure/cdk/requirements.txt).
#
# Usage:
#   .\scripts\setup-devops-agent.ps1
#   .\scripts\setup-devops-agent.ps1 -AgentSpaceArn arn:aws:aidevops:eu-west-1:<acct>:agentspace/<id>
#   .\scripts\setup-devops-agent.ps1 -AgentSpaceArn <arn> -RuntimeRegion eu-west-3
# =============================================================================

param(
    [string]$AgentSpaceArn = "",
    [string]$RuntimeRegion = ""
)

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = (Resolve-Path "$ScriptDir/../../..").Path
$CdkDir    = "$ScriptDir/../infrastructure/cdk"

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " DevOps Agent MCP Registration (CDK)" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------------
# Resolve caller identity
# ---------------------------------------------------------------------------
$AwsAccountId = (aws sts get-caller-identity --query Account --output text)
if (-not $AwsAccountId) {
    Write-Host "ERROR: could not resolve AWS account. Configure credentials first." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Runtime region — where SaasStatusMcpStack was deployed
# ---------------------------------------------------------------------------
if (-not $RuntimeRegion) {
    $RuntimeRegion = if ($env:AWS_REGION) { $env:AWS_REGION } `
                     elseif ($env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION } `
                     else { (aws configure get region 2>$null) }
}
if (-not $RuntimeRegion) {
    $RuntimeRegion = Read-Host "Enter the region where the MCP runtime is deployed (e.g. eu-west-3)"
}

# ---------------------------------------------------------------------------
# Fetch the runtime ARN from the main stack outputs (one CLI call, no manual input)
# ---------------------------------------------------------------------------
Write-Host "[1/3] Reading runtime ARN from CloudFormation stack..." -ForegroundColor Yellow

$MainStackName = "SaasStatusMcpStack-$RuntimeRegion"
$RuntimeArn = aws cloudformation describe-stacks `
    --stack-name $MainStackName `
    --region $RuntimeRegion `
    --query "Stacks[0].Outputs[?OutputKey=='RuntimeArn'].OutputValue" `
    --output text 2>$null

if (-not $RuntimeArn -or $RuntimeArn -eq "None") {
    Write-Host "  ERROR: stack '$MainStackName' not found in $RuntimeRegion." -ForegroundColor Red
    Write-Host "  Deploy the MCP server first: .\deploy-all.ps1" -ForegroundColor Red
    exit 1
}
Write-Host "  Runtime ARN: $RuntimeArn"
Write-Host ""

# ---------------------------------------------------------------------------
# Agent Space ARN — carries the region so the user doesn't specify it separately
# ---------------------------------------------------------------------------
if (-not $AgentSpaceArn) {
    $AgentSpaceArn = if ($env:AGENT_SPACE_ARN) { $env:AGENT_SPACE_ARN } else { "" }
}
if (-not $AgentSpaceArn) {
    Write-Host "Provide your Agent Space ARN (open the DevOps Agent console and from your space click Actions \ Copy ARN)." -ForegroundColor Gray
    Write-Host "  Example: arn:aws:aidevops:eu-west-1:${AwsAccountId}:agentspace/xxxxxxxx-xxxx-..." -ForegroundColor Gray
    $AgentSpaceArn = Read-Host "Enter your Agent Space ARN"
}

# Parse region + ID from the ARN — avoids asking for them separately
if ($AgentSpaceArn -match '^arn:aws[\w-]*:aidevops:([^:]+):(\d+):agentspace/(.+)$') {
    $AgentSpaceRegion = $Matches[1]
    $AgentSpaceAccount = $Matches[2]
    $AgentSpaceId = $Matches[3]
} else {
    Write-Host "ERROR: not a valid Agent Space ARN." -ForegroundColor Red
    Write-Host "  Expected: arn:aws:aidevops:<region>:<account>:agentspace/<id>" -ForegroundColor Red
    exit 1
}
if ($AgentSpaceAccount -ne $AwsAccountId) {
    Write-Host "WARNING: Agent Space account ($AgentSpaceAccount) differs from your credentials ($AwsAccountId)." -ForegroundColor Yellow
}

Write-Host "  Account:            $AwsAccountId"
Write-Host "  Runtime region:     $RuntimeRegion  (SigV4 signing region)"
Write-Host "  Agent Space region: $AgentSpaceRegion"
Write-Host "  Agent Space ID:     $AgentSpaceId"
Write-Host ""

# ---------------------------------------------------------------------------
# CDK deploy the registration stack
# ---------------------------------------------------------------------------
Write-Host "[2/3] Installing CDK dependencies..." -ForegroundColor Yellow
Push-Location $CdkDir
python -m pip install -r requirements.txt --quiet 2>$null
Pop-Location

Write-Host "[3/3] Deploying SaasStatusMcpRegistrationStack via CDK..." -ForegroundColor Yellow
Write-Host "  (creates IAM role, DevOps Agent Service, Association)" -ForegroundColor Gray
Write-Host ""

$StackId = "SaasStatusMcpRegistrationStack-$AgentSpaceRegion"
$env:PYTHONPATH = $RepoRoot

Push-Location $CdkDir
npx cdk deploy $StackId `
    --require-approval never `
    --context "agent_space_id=$AgentSpaceId" `
    --context "agent_space_region=$AgentSpaceRegion" `
    --context "runtime_arn=$RuntimeArn" `
    --context "runtime_region=$RuntimeRegion"
$exitCode = $LASTEXITCODE
Pop-Location

if ($exitCode -ne 0) {
    Write-Host ""
    Write-Host "ERROR: CDK deployment failed. See errors above." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Read outputs from the deployed registration stack
# ---------------------------------------------------------------------------
$regOutputs = aws cloudformation describe-stacks `
    --stack-name $StackId `
    --region $AgentSpaceRegion `
    --query "Stacks[0].Outputs" `
    --output json 2>$null | ConvertFrom-Json

$serviceId    = ($regOutputs | Where-Object { $_.OutputKey -eq "ServiceId" }).OutputValue
$signingRole  = ($regOutputs | Where-Object { $_.OutputKey -eq "SigningRoleArn" }).OutputValue

Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  Registration Complete" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  CDK stack:    $StackId" -ForegroundColor Cyan
Write-Host "  Service ID:   $serviceId" -ForegroundColor Cyan
Write-Host "  Agent Space:  $AgentSpaceId ($AgentSpaceRegion)" -ForegroundColor Cyan
Write-Host "  Signing role: $signingRole" -ForegroundColor Cyan
Write-Host "  MCP name:     saas-status-mcp" -ForegroundColor Cyan
Write-Host "  Tools:        4 enabled" -ForegroundColor Cyan
Write-Host ""
exit 0
