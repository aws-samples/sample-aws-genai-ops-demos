<#
.SYNOPSIS
  Tear down the DLT-agent AgentCore deployment created by deploy.ps1/deploy.sh
  (Windows/PowerShell). Mirror of teardown.sh.
.DESCRIPTION
  Deletes, in order:
    1. Empties the spec-input S3 bucket (a stack resource, versioned) so the
       stack can delete it.
    2. Deletes the CloudFormation stack (runtime, VPC, NAT, endpoints, ECR,
       roles). ECR is EmptyOnDelete; NAT/EIP/endpoints are removed by CFN.
    3. Empties + deletes the source bucket (script-managed, NOT a stack resource).

  DESTRUCTIVE. Requires explicit confirmation (type "delete") or -Yes.
  Only touches the AGENT stack and its own buckets — never the DLT stack/buckets.
.EXAMPLE
  ./teardown.ps1 -StackName ai-load-test-gen -Region us-east-1
#>
[CmdletBinding()]
param(
  [string]$Region = "",             # explicit -Region override; empty = auto-detect (env -> CLI config -> us-east-1)
  [string]$StackName = "ai-load-test-gen",
  [string]$SourceBucket = "",
  [switch]$Yes
)
$ErrorActionPreference = "Stop"

# Region: explicit -Region wins; else env -> CLI config -> us-east-1. Must match
# the region deploy used, or the DescribeStacks lookup below finds nothing.
if (-not $Region) { $Region = $env:AWS_DEFAULT_REGION }
if (-not $Region) { $Region = $env:AWS_REGION }
if (-not $Region) { $Region = (aws configure get region 2>$null) }
if (-not $Region) { $Region = "us-east-1" }

$accountId = (aws sts get-caller-identity --query Account --output text)
if (-not $SourceBucket) { $SourceBucket = "${StackName}-src-${accountId}-${Region}" }

# Resolve the spec-input bucket from the stack BEFORE deleting the stack.
$specBucket = (aws cloudformation describe-stacks --region $Region --stack-name $StackName `
  --query "Stacks[0].Outputs[?OutputKey=='SpecInputBucketName'].OutputValue" --output text 2>$null)
if ($specBucket -eq "None") { $specBucket = "" }

# Empty-Bucket <name> — remove all objects AND versions/delete-markers.
function Empty-Bucket([string]$b) {
  aws s3api head-bucket --bucket $b --region $Region 2>$null
  if ($LASTEXITCODE -ne 0) { Write-Host "  (bucket $b not found, skip)"; return }
  Write-Host "  emptying s3://$b ..."
  # current objects
  aws s3 rm "s3://$b" --recursive --region $Region *> $null
  # versions + delete markers (for versioned buckets) — delete per object id so
  # there is no fragile JSON assembly across PowerShell versions.
  while ($true) {
    $json = (aws s3api list-object-versions --bucket $b --region $Region --max-items 500 --output json 2>$null)
    if (-not $json) { break }
    $parsed = $null
    try { $parsed = $json | ConvertFrom-Json } catch { break }
    $items = @()
    if ($parsed.Versions) { $items += $parsed.Versions }
    if ($parsed.DeleteMarkers) { $items += $parsed.DeleteMarkers }
    if (-not $items -or $items.Count -eq 0) { break }
    foreach ($it in $items) {
      aws s3api delete-object --bucket $b --region $Region --key $it.Key --version-id $it.VersionId *> $null
    }
  }
}

Write-Host "About to DELETE (region=$Region):"
Write-Host "  - CloudFormation stack : $StackName"
Write-Host "  - spec-input bucket     : $(if ($specBucket) { $specBucket } else { '<none>' }) (emptied, then removed by the stack)"
Write-Host "  - source bucket         : $SourceBucket (emptied + deleted)"
Write-Host "  NOTE: the DLT stack and DLT buckets are NOT touched."
if (-not $Yes) {
  $confirm = Read-Host 'Type "delete" to proceed'
  if ($confirm -ne "delete") { Write-Host "aborted."; exit 1 }
}

# 1) empty spec-input bucket so the stack delete does not fail on a non-empty bucket
if ($specBucket) { Empty-Bucket $specBucket }

# 2) delete the stack and wait
Write-Host "deleting stack $StackName ..."
aws cloudformation delete-stack --region $Region --stack-name $StackName
Write-Host "waiting for stack deletion (this can take several minutes; NAT/ENIs are slow)..."
aws cloudformation wait stack-delete-complete --region $Region --stack-name $StackName
if ($LASTEXITCODE -eq 0) {
  Write-Host "stack deleted."
} else {
  Write-Host "WARN: wait ended without confirmation — check the stack status in the console."
}

# 3) delete the script-managed source bucket (not part of the stack)
Empty-Bucket $SourceBucket
aws s3api head-bucket --bucket $SourceBucket --region $Region 2>$null
if ($LASTEXITCODE -eq 0) {
  aws s3 rb "s3://$SourceBucket" --region $Region 2>$null
  if ($LASTEXITCODE -eq 0) { Write-Host "source bucket deleted." } else { Write-Host "  (source bucket already gone)" }
} else {
  Write-Host "  (source bucket already gone)"
}

Write-Host "Teardown complete."
