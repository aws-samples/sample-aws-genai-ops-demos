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
  [string]$BedrockModel = "",
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

# The engine must actually be running — CDK builds the ARM64 image locally. A
# stopped daemon otherwise surfaces as a cryptic mid-build docker API error.
& $engine info *> $null
if ($LASTEXITCODE -ne 0) {
  $hint = if ($engine -eq 'finch') { 'start it with:  finch vm start' } else { "start the $engine daemon (e.g. open Docker Desktop) and re-run." }
  throw "'$engine' is installed but not running. $hint"
}

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

# 3) Bedrock model — OPTIONAL. Default to the agent's built-in models, then
# resolve each to a cross-region inference profile that ACTUALLY EXISTS in
# $BedrockRegion. We never fabricate a prefixed id (the Asia-Pacific prefix is
# `apac`, not `ap`, and the newest models ship global-only in some regions):
# instead we list the system profiles for the base model in $BedrockRegion and
# pick deterministically — the region's own geography (us/eu/apac) first, else
# the global profile, else the sole match; none or ambiguous fails fast.
# https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html
if (-not $BedrockModel) { $BedrockModel = "anthropic.claude-opus-4-8" }
if (-not $BedrockFallback) {
  # a caller-supplied primary doubles as its own fallback; otherwise keep the
  # agent's built-in opus->sonnet pairing.
  $BedrockFallback = if ($PSBoundParameters.ContainsKey('BedrockModel')) { $BedrockModel } else { "anthropic.claude-sonnet-5" }
}

function Resolve-Profile([string]$want) {
  $base = $want -replace '^(us|eu|apac|ap|global)\.', ''
  # CRIS geo prefix used ONLY to prefer among profiles that really exist, never
  # to construct an id. Asia-Pacific is `apac` (not `ap`); regions without a
  # dedicated geography (ca/me/af/sa/il/mx) use the global profile.
  $geo = switch (($BedrockRegion -split '-')[0]) { 'us' { 'us' } 'eu' { 'eu' } 'ap' { 'apac' } default { 'global' } }
  # every system-defined profile in $BedrockRegion whose id ends with the base
  $raw = (aws bedrock list-inference-profiles --region $BedrockRegion --type-equals SYSTEM_DEFINED `
            --query "inferenceProfileSummaries[?ends_with(inferenceProfileId, '$base')].inferenceProfileId" `
            --output text 2>$null)
  $ids = @($raw -split "\s+" | Where-Object { $_ })
  # 1) the region's own geography wins (deterministic where geo+global co-exist)
  if ($ids -contains "$geo.$base") { return "$geo.$base" }
  # 2) else the global profile (its destination list is all commercial regions)
  if ($ids -contains "global.$base") {
    Write-Host "note: no '$geo.' profile for '$base' in $BedrockRegion; using 'global.$base'."
    return "global.$base"
  }
  # 3) else a single unambiguous match
  if ($ids.Count -eq 1) {
    Write-Host "note: '$want' resolved to '$($ids[0])' in $BedrockRegion."
    return $ids[0]
  }
  # 4) none or ambiguous -> fail fast with guidance
  if ($ids.Count -eq 0) {
    throw "No inference profile for '$base' in ${BedrockRegion}: none offered here (or bedrock:ListInferenceProfiles denied). Deploy in a region that offers this model, or pass -BedrockModel."
  }
  throw "Multiple inference profiles for '$base' in ${BedrockRegion}; re-run with -BedrockModel set to one of: $($ids -join ', ')"
}

$primaryId = Resolve-Profile $BedrockModel
$fallbackId = Resolve-Profile $BedrockFallback
Write-Host "Bedrock: primary=$primaryId fallback=$fallbackId (region $BedrockRegion)"

$arns = @()
foreach ($id in @($primaryId, $fallbackId)) {
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
  -c bedrockModelPrimary=$primaryId `
  -c bedrockModelFallback=$fallbackId `
  -c bedrockProfileArns=$profileArns `
  -c dltStackName=$DltStack `
  -c dltRegion=$DltRegion `
  -c dltApiGatewayArn=$dltApiArn `
  -c dltScenariosBucketArn=$dltBucketArn `
  -c dltStackArn=$dltStackArn `
  -c networkMode=$NetworkMode `
  -c enableXray=$EnableXray
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "cdk deploy failed (exit $LASTEXITCODE)" }
Pop-Location

# 6) User-friendly summary.
$stack = "AILoadTestGen-$Region"
$rtArn = (aws cloudformation describe-stacks --stack-name $stack --region $Region --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text)
$specBucket = (aws cloudformation describe-stacks --stack-name $stack --region $Region --query "Stacks[0].Outputs[?OutputKey=='SpecInputBucketName'].OutputValue" --output text)
if (-not $rtArn -or $rtArn -eq 'None') { throw "stack $stack has no AgentRuntimeArn output — the deploy did not complete." }
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Runtime ARN : $rtArn" -ForegroundColor Cyan
Write-Host "  Spec bucket : $specBucket" -ForegroundColor Cyan
Write-Host "  Region      : $Region" -ForegroundColor Cyan
Write-Host "  DLT wired   : $(if ($DltStack) { 'yes' } else { 'no' })" -ForegroundColor Cyan
