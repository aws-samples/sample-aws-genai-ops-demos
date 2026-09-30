# Removes everything deploy-all.ps1 created, Agent Space included (a demo that cannot be
# destroyed fails validation). CDK orders the stacks: Lab, then the file system (FSx takes
# about 10 minutes to delete), then the directory, and the Agent Space in its own region.

param(
    [string]$ProjectName = "fsx-sla-review"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

& "..\..\shared\scripts\check-prerequisites.ps1" -RequiredService "devops-agent" -MinAwsCliVersion "2.34.20" -RequireCDK
if ($LASTEXITCODE -ne 0) { exit 1 }
$region = $global:AWS_REGION
$agentRegion = $global:DEVOPS_AGENT_REGION

Write-Host ""
Write-Host "Destroying the FSx for Windows SLA review demo in $region (Agent Space in $agentRegion)..." -ForegroundColor Yellow
Write-Host "  The file system and its automatic backups are deleted; this takes about 15 minutes."

Push-Location cdk
try {
    npx -y cdk destroy --all --force -c "projectName=$ProjectName" -c "devOpsAgentRegion=$agentRegion"
    if ($LASTEXITCODE -ne 0) { throw "cdk destroy failed" }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Teardown Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Not removed: the CDK bootstrap stack, and any skill you uploaded in the Agent Space console" -ForegroundColor Cyan
Write-Host "  (the Agent Space itself is gone, so the skill is too)." -ForegroundColor Cyan
