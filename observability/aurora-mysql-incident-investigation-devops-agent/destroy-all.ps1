# Removes everything deploy-all.ps1 created, Agent Space included (a demo that cannot be
# destroyed fails validation). Stacks go in dependency order: Lab, the database (Aurora takes
# about 10 minutes to delete; no final snapshot is kept), then the Agent Space in its own
# region. Each step goes through the shared deploy-cdk script.

param(
    [string]$ProjectName = "aurora-bizctx"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

& "..\..\shared\scripts\check-prerequisites.ps1" -RequiredService "devops-agent" -MinAwsCliVersion "2.34.64" -RequireCDK
if ($LASTEXITCODE -ne 0) { exit 1 }
$region = $global:AWS_REGION
$agentRegion = $global:DEVOPS_AGENT_REGION
$context = @("projectName=$ProjectName", "devOpsAgentRegion=$agentRegion")

Write-Host ""
Write-Host "Destroying the Aurora MySQL business-context demo in $region (Agent Space in $agentRegion)..." -ForegroundColor Yellow
Write-Host "  The cluster is deleted without a final snapshot; this takes about 15 minutes."

$env:AWS_REGION = $region
# Best-effort calls below are expected to write to stderr (a function, stack or log group that is
# already gone). Under "Stop", Windows PowerShell 5.1 turns a redirected stderr line into a
# terminating error, so they run under "Continue" and are judged by $LASTEXITCODE (the pattern
# shared/scripts/deploy-cdk.ps1 uses). The shared deploy-cdk calls run under "Stop".
$ErrorActionPreference = "Continue"
# A storm left running would hold connections while the cluster deletes; switch the load off first.
aws lambda put-function-concurrency --function-name "$ProjectName-connection-load" --reserved-concurrent-executions 0 `
    --region $region --no-cli-pager 2>$null | Out-Null

# Log groups Lambda creates at run time (not stack resources): note the trigger function's
# generated name before its stack goes, delete them all once the stacks are gone.
$triggerFunction = aws cloudformation describe-stacks --stack-name "AuroraBizCtxDatabase-$region" --region $region `
    --query "Stacks[0].Outputs[?OutputKey=='TriggerFunctionName'].OutputValue" --output text --no-cli-pager 2>$null
if ($LASTEXITCODE -ne 0) { $triggerFunction = $null }
$ErrorActionPreference = "Stop"

foreach ($stack in @("AuroraBizCtxLab-$region", "AuroraBizCtxDatabase-$region")) {
    Write-Host ""
    Write-Host "Removing $stack..." -ForegroundColor Cyan
    & "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $stack -DestroyStack -SkipBootstrap -CdkContext $context
    if ($LASTEXITCODE -ne 0) { exit 1 }
}

Write-Host ""
Write-Host "Removing run-time log groups..." -ForegroundColor Cyan
$ErrorActionPreference = "Continue"
$logGroups = @("/aws/lambda/$ProjectName-lab-engine", "/aws/lambda/$ProjectName-lab-api")
if ($triggerFunction -and $triggerFunction -ne "None") { $logGroups += "/aws/lambda/$triggerFunction" }
foreach ($prefix in @("/aws/lambda/AuroraBizCtxLab-$region-", "/aws/lambda/AuroraBizCtxDatabase-$region-")) {
    $found = aws logs describe-log-groups --log-group-name-prefix $prefix --region $region `
        --query "logGroups[].logGroupName" --output text --no-cli-pager 2>$null
    if ($LASTEXITCODE -ne 0) { $found = $null }
    if ($found -and $found -ne "None") { $logGroups += ($found -split "\s+" | Where-Object { $_ }) }
}
foreach ($name in $logGroups) {
    aws logs delete-log-group --log-group-name $name --region $region --no-cli-pager 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "  deleted $name" }
}
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "Removing AuroraBizCtxAgentSpace-$agentRegion..." -ForegroundColor Cyan
$env:AWS_REGION = $agentRegion
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName "AuroraBizCtxAgentSpace-$agentRegion" -DestroyStack -SkipBootstrap -CdkContext $context
$exit = $LASTEXITCODE
$env:AWS_REGION = $region
if ($exit -ne 0) { exit 1 }

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Teardown Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Not removed: the CDK bootstrap stack. The Agent Space is gone, and the skill registered in it with it." -ForegroundColor Cyan
