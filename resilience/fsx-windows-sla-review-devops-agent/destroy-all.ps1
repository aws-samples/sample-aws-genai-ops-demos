# Removes everything deploy-all.ps1 created, Agent Space included (a demo that cannot be
# destroyed fails validation). Stacks go in dependency order: Lab, the file system (FSx takes
# about 10 minutes to delete, its backups with it), the directory, then the Agent Space in
# its own region. Each step goes through the shared deploy-cdk script.

param(
    [string]$ProjectName = "fsx-sla-review"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

& "..\..\shared\scripts\check-prerequisites.ps1" -RequiredService "devops-agent" -MinAwsCliVersion "2.34.20" -RequireCDK
if ($LASTEXITCODE -ne 0) { exit 1 }
$region = $global:AWS_REGION
$agentRegion = $global:DEVOPS_AGENT_REGION
$context = @("projectName=$ProjectName", "devOpsAgentRegion=$agentRegion")

Write-Host ""
Write-Host "Destroying the FSx for Windows SLA review demo in $region (Agent Space in $agentRegion)..." -ForegroundColor Yellow
Write-Host "  The file system and its automatic backups are deleted; this takes about 15 minutes."

$env:AWS_REGION = $region
foreach ($stack in @("FsxSlaReviewLab-$region", "FsxSlaReviewFileSystem-$region", "FsxSlaReviewDirectory-$region")) {
    Write-Host ""
    Write-Host "Removing $stack..." -ForegroundColor Cyan
    & "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $stack -DestroyStack -SkipBootstrap -CdkContext $context
    if ($LASTEXITCODE -ne 0) { exit 1 }
}

Write-Host ""
Write-Host "Removing FsxSlaReviewAgentSpace-$agentRegion..." -ForegroundColor Cyan
$env:AWS_REGION = $agentRegion
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName "FsxSlaReviewAgentSpace-$agentRegion" -DestroyStack -SkipBootstrap -CdkContext $context
$exit = $LASTEXITCODE
$env:AWS_REGION = $region
if ($exit -ne 0) { exit 1 }

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Teardown Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Not removed: the CDK bootstrap stack. The Agent Space is gone, and the skill registered in it with it." -ForegroundColor Cyan
