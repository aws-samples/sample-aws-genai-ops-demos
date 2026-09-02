<#
.SYNOPSIS
  Deploy the AI Load Test Generator Agent to AgentCore Runtime via CloudFormation
  (Windows/PowerShell). Mirror of deploy.sh.
.DESCRIPTION
  Pre-deploy upload model (no local container engine required — the ARM64 image
  is built IN-STACK by CodeBuild):
    1. resolve DLT inputs from just the DLT stack name + region
    2. resolve the Bedrock model(s) to an inference profile that exists in the
       Bedrock region (model is OPTIONAL; defaults to the agent's Claude models)
    3. zip the agent source (git archive => tracked files, stable hash)
    4. ensure an S3 source bucket exists, upload the zip there
    5. deploy the CloudFormation stack, which builds the image in-stack and
       creates the private runtime.

  There is NO `aws cloudformation package` step: the custom-resource Lambda is
  inlined in the template, so the only artifact uploaded is the source zip.
.EXAMPLE
  # model optional (auto-resolved), deploys a generator agent
  ./deploy.ps1
.EXAMPLE
  # with DLT wired (DLT ARNs auto-derived); the model is optional and auto-resolved
  ./deploy.ps1 -DltStack LaunchWizard-dlt-poc -DltRegion us-west-2
#>
[CmdletBinding()]
param(
  [string]$DltStack = "",
  [string]$DltRegion = "",
  [string]$BedrockRegion = "",
  [string]$BedrockModel = "",       # optional: primary model/profile id (else default, auto-resolved)
  [string]$BedrockFallback = "",    # optional: fallback model/profile id (else default, auto-resolved)
  [string]$DltApiArn = "",          # advanced override; else auto-derived
  [string]$DltBucketArn = "",       # advanced override; else auto-derived
  [string]$DltStackArn = "",        # advanced override; else auto-derived
  [string]$BedrockProfiles = "",    # advanced override: IAM invoke scope; else derived from picks
  [string]$Region = "",             # agent stack region [default: auto-detect env/CLI, else us-east-1]
  [string]$StackName = "ai-load-test-gen",
  [ValidateSet("public","vpc")][string]$NetworkMode = "public",
  [string]$CreateVpc = "true",      # new vs existing VPC (vpc mode only)
  [string]$SourceBucket = "",       # [default: <stack>-src-<account>-<region>]
  [string]$EnableXray = "false",    # X-Ray opt-in (needs Transaction Search)
  [switch]$ForceXrayWithoutTs       # allow -EnableXray true even when TS is off
)
$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $here "../..")).Path
$template = Join-Path $here "template.yaml"

# Region: explicit -Region wins; else env -> CLI config -> us-east-1
# (mirrors shared/utils/aws-utils.sh get_aws_region so standalone use matches the monorepo).
if (-not $Region) { $Region = $env:AWS_DEFAULT_REGION }
if (-not $Region) { $Region = $env:AWS_REGION }
if (-not $Region) { $Region = (aws configure get region 2>$null) }
if (-not $Region) { $Region = "us-east-1" }

if (-not $BedrockRegion) { $BedrockRegion = $Region }
$accountId = (aws sts get-caller-identity --query Account --output text)

# ---------------------------------------------------------------------------
# 1) DLT is OPTIONAL. Resolve DLT inputs only when a DLT stack was supplied;
#    otherwise deploy a script-only agent with no DLT-scoped IAM. To connect
#    DLT later, re-run with -DltStack/-DltRegion (a CloudFormation update wires
#    it in and rolls the runtime to a new version).
# ---------------------------------------------------------------------------
if ($DltStack) {
  if (-not $DltRegion) { throw "-DltRegion required when -DltStack is given" }
  Write-Host "Resolving DLT stack '$DltStack' in $DltRegion ..."

  if (-not $DltStackArn) {
    $DltStackArn = (aws cloudformation describe-stacks --stack-name $DltStack --region $DltRegion `
      --query "Stacks[0].StackId" --output text 2>$null)
  }
  if (-not $DltStackArn -or $DltStackArn -eq "None") {
    [Console]::Error.WriteLine("ERROR: cannot describe DLT stack '$DltStack' in $DltRegion.")
    [Console]::Error.WriteLine("       Check the name/region and your credentials.")
    exit 4
  }

  if (-not $DltApiArn) {
    # The API output key carries a logical-ID hash (e.g. DLTApiEndpointD98B09AC),
    # so match by prefix, not an exact key.
    $apiUrl = (aws cloudformation describe-stacks --stack-name $DltStack --region $DltRegion `
      --query "Stacks[0].Outputs[?starts_with(OutputKey, 'DLTApiEndpoint')].OutputValue | [0]" --output text 2>$null)
    if (-not $apiUrl -or $apiUrl -eq "None") {
      [Console]::Error.WriteLine("ERROR: no DLTApiEndpoint* output on stack '$DltStack'; is this a DLT stack?")
      exit 4
    }
    # https://<apiId>.execute-api.<region>.amazonaws.com/<stage>/
    $hostPath = $apiUrl -replace "^https://", ""
    $apiId = $hostPath.Split(".")[0]
    $stage = ($apiUrl -replace "^.*amazonaws\.com/", "").Split("/")[0]
    if (-not $stage) { $stage = "*" }
    $DltApiArn = "arn:aws:execute-api:${DltRegion}:${accountId}:$apiId/$stage/*"
  }

  if (-not $DltBucketArn) {
    $scenBucket = (aws cloudformation describe-stacks --stack-name $DltStack --region $DltRegion `
      --query "Stacks[0].Outputs[?OutputKey=='ScenariosBucket'].OutputValue | [0]" --output text 2>$null)
    if (-not $scenBucket -or $scenBucket -eq "None") {
      [Console]::Error.WriteLine("ERROR: no ScenariosBucket output on stack '$DltStack'; is this a DLT stack?")
      exit 4
    }
    $DltBucketArn = "arn:aws:s3:::$scenBucket"
  }

  Write-Host "  stack ARN : $DltStackArn"
  Write-Host "  api  ARN  : $DltApiArn"
  Write-Host "  bucket ARN: $DltBucketArn"
} else {
  Write-Host "DLT not connected (script-only agent)."
  Write-Host "  -> to wire DLT later, re-run: ./deploy.ps1 -DltStack <name> -DltRegion <region> ..."
}

# ---------------------------------------------------------------------------
# 2) Bedrock model(s) — OPTIONAL, resolved exactly like the CDK path
#    (deploy-all.ps1): default to the agent's built-in models, then resolve each
#    to a cross-region inference profile that ACTUALLY EXISTS in $BedrockRegion.
#    We never fabricate a prefixed id (the Asia-Pacific prefix is `apac`, not
#    `ap`, and the newest models ship global-only in some regions): we list the
#    system profiles for the base model and pick deterministically — the
#    region's own geography (us/eu/apac) first, else the global profile, else
#    the sole match; none or ambiguous fails fast. The chosen id(s) set
#    BEDROCK_MODEL_PRIMARY/FALLBACK (what the agent invokes) AND, unless
#    -BedrockProfiles is given, the IAM invoke scope.
#    https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html
# ---------------------------------------------------------------------------
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

if ($BedrockProfiles -and $BedrockModel) {
  # advanced manual override: caller supplied BOTH the IAM scope and the model
  # id — trust them verbatim (no resolution).
  $selPrimary = $BedrockModel
  $selFallback = if ($BedrockFallback) { $BedrockFallback } else { $BedrockModel }
} else {
  # default path — model is OPTIONAL and resolved to a real profile. A
  # caller-supplied primary doubles as its own fallback; otherwise keep the
  # agent's built-in opus->sonnet pairing.
  $modelGiven = [bool]$BedrockModel
  if (-not $BedrockModel) { $BedrockModel = "anthropic.claude-opus-4-8" }
  if (-not $BedrockFallback) {
    $BedrockFallback = if ($modelGiven) { $BedrockModel } else { "anthropic.claude-sonnet-5" }
  }
  $selPrimary = Resolve-Profile $BedrockModel
  $selFallback = Resolve-Profile $BedrockFallback
  Write-Host "Bedrock: primary=$selPrimary fallback=$selFallback (region $BedrockRegion)"
}

# Keep primary/fallback consistent with the IAM scope: an empty fallback reuses
# the primary so the agent never falls back to a model IAM does not allow.
if (-not $selFallback) { $selFallback = $selPrimary }

# Derive the IAM invoke scope from the chosen ids unless overridden. CRIS
# (cross-region inference) requires bedrock:InvokeModel on BOTH the inference
# profile ARN AND the underlying foundation-model ARNs of every region the
# profile routes to — get-inference-profile lists those model ARNs.
if (-not $BedrockProfiles) {
  $arns = @()
  foreach ($id in @($selPrimary, $selFallback)) {
    if (-not $id) { continue }
    $p = "arn:aws:bedrock:${BedrockRegion}:${accountId}:inference-profile/$id"
    if ($arns -notcontains $p) { $arns += $p }
    $fms = (aws bedrock get-inference-profile --region $BedrockRegion --inference-profile-identifier $id --query "models[].modelArn" --output text 2>$null)
    foreach ($m in ($fms -split "\s+")) { if ($m -and ($arns -notcontains $m)) { $arns += $m } }
  }
  $BedrockProfiles = ($arns -join ",")
}

Write-Host "  primary   : $selPrimary"
Write-Host "  fallback  : $selFallback"
Write-Host "  invoke ARNs: $BedrockProfiles"

if (-not $SourceBucket) { $SourceBucket = "${StackName}-src-${accountId}-${Region}" }

# ---------------------------------------------------------------------------
# 3) Deterministic source zip. Archive the WORKING TREE (tracked files with
#    current, possibly-uncommitted edits) so we can test before committing:
#    `git stash create` snapshots the working state to a throwaway commit
#    without touching the index; falls back to HEAD when the tree is clean.
# ---------------------------------------------------------------------------
$buildDir = Join-Path $here "build"
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
$srcZip = Join-Path $buildDir "agent-source.zip"
$tree = (git -C $repoRoot stash create 2>$null)
if ($tree) { $tree = "$tree".Trim() }
if (-not $tree) { $tree = "HEAD" }
git -C $repoRoot archive --format=zip -o $srcZip $tree
if ($LASTEXITCODE -ne 0) { throw "git archive failed (exit $LASTEXITCODE)" }
$srcHash = ("$(git -C $repoRoot rev-parse --short $tree)").Trim()
$srcKey = "agent-source/$srcHash.zip"
$imageTag = "git-$srcHash"

# 4) Ensure the source bucket exists (private, idempotent), then upload.
aws s3api head-bucket --bucket $SourceBucket --region $Region 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "creating source bucket s3://$SourceBucket"
  if ($Region -eq "us-east-1") {
    aws s3api create-bucket --bucket $SourceBucket --region $Region | Out-Null
  } else {
    aws s3api create-bucket --bucket $SourceBucket --region $Region `
      --create-bucket-configuration "LocationConstraint=$Region" | Out-Null
  }
  aws s3api put-public-access-block --bucket $SourceBucket `
    --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" | Out-Null
  # Pass the encryption config via a temp JSON file — robust across PowerShell
  # versions (native-arg quoting of inline JSON is unreliable pre-7.3).
  $encFile = New-TemporaryFile
  Set-Content -Path $encFile -Value '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms"}}]}' -NoNewline
  aws s3api put-bucket-encryption --bucket $SourceBucket --region $Region `
    --server-side-encryption-configuration "file://$($encFile.FullName)" | Out-Null
  Remove-Item $encFile -ErrorAction SilentlyContinue
}
aws s3 cp $srcZip "s3://$SourceBucket/$srcKey" --region $Region
if ($LASTEXITCODE -ne 0) { throw "source upload failed (exit $LASTEXITCODE)" }

# 5) VPC mode only: look up the region's S3 prefix list id for the runtime SG
#    egress rule. In public mode no VPC/SG exists, so leave it empty.
if ($NetworkMode -eq "vpc") {
  $s3PlId = (aws ec2 describe-prefix-lists --region $Region `
    --filters "Name=prefix-list-name,Values=com.amazonaws.$Region.s3" `
    --query "PrefixLists[0].PrefixListId" --output text 2>$null)
  if ($s3PlId -eq "None") { $s3PlId = "" }
} else {
  $s3PlId = ""
}

# 5b) X-Ray perms are opt-in (-EnableXray true). They are only useful when
#     CloudWatch Transaction Search is enabled; if it is OFF, refuse unless the
#     operator re-confirms with -ForceXrayWithoutTs (perms would be granted but
#     spans won't be searchable and may incur cost with no benefit).
if ($EnableXray -eq "true") {
  $xrayTs = (aws xray get-trace-segment-destination --region $Region --query "Destination" --output text 2>$null)
  if (-not $xrayTs) { $xrayTs = "unknown" }
  if ($xrayTs -ne "CloudWatchLogs" -and -not $ForceXrayWithoutTs) {
    [Console]::Error.WriteLine("REFUSED: -EnableXray true but CloudWatch Transaction Search is not enabled (Destination=$xrayTs).")
    [Console]::Error.WriteLine("X-Ray permissions would be granted but spans would not be searchable and X-Ray/CloudWatch may still bill for exported data.")
    [Console]::Error.WriteLine("Enable Transaction Search first, or re-run with -ForceXrayWithoutTs to proceed anyway.")
    exit 3
  }
  Write-Host "X-Ray tracing ENABLED (EnableXrayTracing=true, TS destination=$xrayTs)"
} else {
  Write-Host "X-Ray tracing disabled (EnableXrayTracing=false)"
}

# 6) Deploy (no `package` — Lambda code is inlined in the template).
aws cloudformation deploy `
  --template-file $template `
  --stack-name $StackName `
  --capabilities CAPABILITY_NAMED_IAM `
  --region $Region `
  --parameter-overrides `
    "ArtifactBucketName=$SourceBucket" `
    "SourceS3Key=$srcKey" `
    "ImageTag=$imageTag" `
    "DltStackName=$DltStack" `
    "DltRegion=$DltRegion" `
    "BedrockRegion=$BedrockRegion" `
    "DltApiGatewayArn=$DltApiArn" `
    "DltScenariosBucketArn=$DltBucketArn" `
    "DltStackArn=$DltStackArn" `
    "BedrockInferenceProfileArns=$BedrockProfiles" `
    "BedrockModelPrimary=$selPrimary" `
    "BedrockModelFallback=$selFallback" `
    "NetworkMode=$NetworkMode" `
    "CreateVpc=$CreateVpc" `
    "S3PrefixListId=$s3PlId" `
    "EnableXrayTracing=$EnableXray"
if ($LASTEXITCODE -ne 0) { throw "cloudformation deploy failed (exit $LASTEXITCODE)" }

Write-Host ""
Write-Host "Deployed. Outputs:"
aws cloudformation describe-stacks --stack-name $StackName --region $Region `
  --query "Stacks[0].Outputs" --output table
