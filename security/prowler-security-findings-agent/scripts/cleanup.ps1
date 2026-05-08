$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot = (Resolve-Path "$ScriptDir\..\..\..").Path
. "$RepoRoot\shared\scripts\check-prerequisites.ps1" -SkipServiceCheck
$Region = $global:AWS_REGION
$DevOpsAgentRegion = if ($env:DEVOPS_AGENT_REGION) { $env:DEVOPS_AGENT_REGION } else { "us-east-1" }

# CDK synth needs to resolve every stack's assets before it can destroy
# anything. The FrontendStack references frontend/dist as a BucketDeployment
# source; if the user has never built the frontend (or cleaned the build
# output), synth fails with CannotFindAsset and every destroy call aborts.
# Stub a placeholder so synth resolves and destroy proceeds — the files will
# be deleted along with the bucket anyway.
$DistDir = "$ScriptDir\..\frontend\dist"
if (-not (Test-Path "$DistDir\index.html")) {
    New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
    "<!doctype html><html><body>cleanup placeholder</body></html>" | Out-File -Encoding utf8 "$DistDir\index.html"
}

Push-Location "$ScriptDir\..\cdk"
$Stacks = @(
  "ProwlerSecurityFrontend-$Region",
  "ProwlerSecurityApi-$Region",
  "ProwlerSecurityIngest-$Region",
  "ProwlerSecurityScanner-$Region",
  "ProwlerSecurityDevOpsAgent-$Region",
  "ProwlerSecurityAuth-$Region",
  "ProwlerSecurityData-$Region"
)
foreach ($s in $Stacks) {
  Write-Host "[cleanup] destroying $s..."
  npx cdk destroy $s --force --no-cli-pager
}
Pop-Location

$AgentSpace = "prowler-security"
$SpaceId = aws devops-agent list-agent-spaces --region $DevOpsAgentRegion --query "agentSpaces[?name=='$AgentSpace'].agentSpaceId | [0]" --output text --no-cli-pager 2>$null
if ($SpaceId -and $SpaceId -ne "None") {
  $confirm = Read-Host "Delete DevOps Agent Space $SpaceId? (y/N)"
  if ($confirm -eq "y") {
    aws devops-agent delete-agent-space --agent-space-id $SpaceId --region $DevOpsAgentRegion | Out-Null
    aws iam detach-role-policy --role-name "$AgentSpace-AgentSpaceRole" --policy-arn arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy 2>$null | Out-Null
    aws iam delete-role --role-name "$AgentSpace-AgentSpaceRole" 2>$null | Out-Null
    aws iam detach-role-policy --role-name "$AgentSpace-OperatorRole" --policy-arn arn:aws:iam::aws:policy/AIDevOpsOperatorAppAccessPolicy 2>$null | Out-Null
    aws iam delete-role --role-name "$AgentSpace-OperatorRole" 2>$null | Out-Null
  }
}
Write-Host "[cleanup] done."
