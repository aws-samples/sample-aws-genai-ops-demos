<#
.SYNOPSIS
  Deploy the AI Load Test Generator Agent to AgentCore Runtime via AWS CDK (Windows/PowerShell).
.DESCRIPTION
  Conformity path for sample-aws-genai-ops-demos: CDK IaC, shared prerequisites,
  region auto-detect, user-friendly summary. Coexists with the CloudFormation
  path (infrastructure/cloudformation/deploy.sh). DLT is OPTIONAL. The ARM64 image is built locally by
  the detected container engine (docker/finch/nerdctl) via CDK DockerImageAsset.
.EXAMPLE
  ./deploy-all.ps1 -BedrockModel us.anthropic.claude-opus-4-8 `
     -DltStack LaunchWizard-dlt-poc -DltRegion us-west-2
#>
[CmdletBinding()]
param(
  [string]$Region = "",
  [string]$BedrockRegion = "",
  [Parameter(Mandatory = $true)][string]$BedrockModel,
  [string]$BedrockFallback = "",
  [string]$DltStack = "",
  [string]$DltRegion = "",
  [ValidateSet("public","vpc")][string]$NetworkMode = "public",
  [string]$EnableXray = "false"
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$cdkDir = Join-Path $here "infrastructure/cdk"

# 1) Prerequisites — shared script when present, else local check.
$sharedPrereq = Join-Path $here "../../shared/scripts/check-prerequisites.ps1"
if (Test-Path $sharedPrereq) {
  & $sharedPrereq -RequiredService "agentcore" -MinAwsCliVersion "2.31.13"
  # Explicit -Region wins; otherwise adopt the region the shared check detected.
  if (-not $Region -and $global:AWS_REGION) { $Region = $global:AWS_REGION }
} else {
  foreach ($c in @("aws", "npx")) {
    if (-not (Get-Command $c -ErrorAction SilentlyContinue)) { throw "$c is required" }
  }
  aws sts get-caller-identity | Out-Null
}

# Region: explicit -Region wins; else env -> CLI config -> us-east-1
# (mirrors shared/utils/aws-utils.sh get_aws_region so standalone use matches the monorepo).
if (-not $Region) { $Region = $env:AWS_DEFAULT_REGION }
if (-not $Region) { $Region = $env:AWS_REGION }
if (-not $Region) { $Region = (aws configure get region 2>$null) }
if (-not $Region) { $Region = "us-east-1" }

# Container engine auto-detect.
$engine = $env:CONTAINER_ENGINE
if (-not $engine) {
  foreach ($c in @("docker", "finch", "nerdctl")) {
    if (Get-Command $c -ErrorAction SilentlyContinue) { $engine = $c; break }
  }
}
if (-not $engine) { throw "no container engine (docker/finch/nerdctl) found" }

if (-not $BedrockRegion) { $BedrockRegion = $Region }
$accountId = (aws sts get-caller-identity --query Account --output text)

# 2) DLT optional — derive ARNs from stack name + region.
$dltApiArn = ""; $dltBucketArn = ""; $dltStackArn = ""
if ($DltStack) {
  if (-not $DltRegion) { throw "-DltRegion required when -DltStack is given" }
  Write-Host "Resolving DLT stack '$DltStack' in $DltRegion ..."
  $dltStackArn = (aws cloudformation describe-stacks --stack-name $DltStack --region $DltRegion --query "Stacks[0].StackId" --output text)
  $apiUrl = (aws cloudformation describe-stacks --stack-name $DltStack --region $DltRegion --query "Stacks[0].Outputs[?starts_with(OutputKey, 'DLTApiEndpoint')].OutputValue | [0]" --output text)
  $hostPath = $apiUrl -replace "^https://", ""
  $apiId = $hostPath.Split(".")[0]
  $stage = ($apiUrl -replace "^.*amazonaws\.com/", "").Split("/")[0]
  if (-not $stage) { $stage = "*" }
  $dltApiArn = "arn:aws:execute-api:${DltRegion}:${accountId}:$apiId/$stage/*"
  $scenBucket = (aws cloudformation describe-stacks --stack-name $DltStack --region $DltRegion --query "Stacks[0].Outputs[?OutputKey=='ScenariosBucket'].OutputValue | [0]" --output text)
  $dltBucketArn = "arn:aws:s3:::$scenBucket"
} else {
  Write-Host "DLT not connected (script-only agent). Re-run with -DltStack to wire it later."
}

# 3) Bedrock invoke scope — profile ARN + routed foundation-model ARNs (CRIS).
if (-not $BedrockFallback) { $BedrockFallback = $BedrockModel }
$arns = @()
foreach ($id in @($BedrockModel, $BedrockFallback)) {
  if (-not $id) { continue }
  $p = "arn:aws:bedrock:${BedrockRegion}:${accountId}:inference-profile/$id"
  if ($arns -notcontains $p) { $arns += $p }
  $fms = (aws bedrock get-inference-profile --region $BedrockRegion --inference-profile-identifier $id --query "models[].modelArn" --output text)
  foreach ($m in ($fms -split "\s+")) { if ($m -and ($arns -notcontains $m)) { $arns += $m } }
}
$profileArns = ($arns -join ",")

# 4) Python deps for CDK (Windows uses `python`, not `python3`).
python -m venv "$cdkDir/.venv"
& "$cdkDir/.venv/Scripts/Activate.ps1"
pip install -q -r "$cdkDir/requirements.txt"

# 5) Deploy. CDK builds the image with the detected engine; bootstrap once.
Push-Location $cdkDir
$env:CDK_DOCKER = $engine
npx --yes aws-cdk@latest bootstrap "aws://$accountId/$Region" 2>$null
npx --yes aws-cdk@latest deploy --require-approval never `
  -c region=$Region `
  -c bedrockRegion=$BedrockRegion `
  -c bedrockModelPrimary=$BedrockModel `
  -c bedrockModelFallback=$BedrockFallback `
  -c bedrockProfileArns=$profileArns `
  -c dltStackName=$DltStack `
  -c dltRegion=$DltRegion `
  -c dltApiGatewayArn=$dltApiArn `
  -c dltScenariosBucketArn=$dltBucketArn `
  -c dltStackArn=$dltStackArn `
  -c networkMode=$NetworkMode `
  -c enableXray=$EnableXray
Pop-Location

# 6) User-friendly summary.
$stack = "AILoadTestGen-$Region"
$rtArn = (aws cloudformation describe-stacks --stack-name $stack --region $Region --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text)
$specBucket = (aws cloudformation describe-stacks --stack-name $stack --region $Region --query "Stacks[0].Outputs[?OutputKey=='SpecInputBucketName'].OutputValue" --output text)
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Runtime ARN : $rtArn" -ForegroundColor Cyan
Write-Host "  Spec bucket : $specBucket" -ForegroundColor Cyan
Write-Host "  Region      : $Region" -ForegroundColor Cyan
Write-Host "  DLT wired   : $(if ($DltStack) { 'yes' } else { 'no' })" -ForegroundColor Cyan
