# S3 data protection advisor - complete teardown script
# Windows version - mirrors destroy-all.sh
# -MockData: delete the mock estate first (tagged buckets and role only)
# The Org stack (multi-account rollout) is removed first when it exists: deleting its StackSet removes the spoke
# role from every member account. Spoke roles deployed by hand are not visible from here (see the final note).
param(
    [switch]$MockData
)

Set-Location $PSScriptRoot

Write-Host "=== S3 data protection advisor teardown ===" -ForegroundColor Cyan

& "..\..\shared\scripts\check-prerequisites.ps1" -SkipServiceCheck -MinPythonVersion "3.12" -RequireCDK -MinCdkVersion "2.1030.0"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Prerequisites check failed" -ForegroundColor Red
    exit 1
}

if ($MockData) {
    Write-Host "`nDeleting the mock estate..." -ForegroundColor Yellow
    if (-not (Test-Path "backend/.venv")) { python -m venv backend/.venv }
    $venvPython = if (Test-Path "backend/.venv/Scripts/python.exe") { "backend/.venv/Scripts/python.exe" } else { "backend/.venv/bin/python" }
    $venvPython = (Resolve-Path $venvPython).Path
    & $venvPython -m pip install --quiet -r backend/requirements.txt
    Push-Location backend
    & $venvPython -m s3dp.cli seed-cleanup --yes
    $cleanExit = $LASTEXITCODE
    Pop-Location
    if ($cleanExit -ne 0) { Write-Host "Deleting the mock estate failed" -ForegroundColor Red; exit 1 }
}

$region = $global:AWS_REGION
$orgStackName = "S3DataProtectionOrg-$region"
# The Org stack only exists in the app with its context, so `cdk destroy --all` would not see it.
$orgTargets = aws cloudformation describe-stacks --stack-name $orgStackName --region $region --query "Stacks[0].Outputs[?OutputKey=='OrgTargets'].OutputValue" --output text --no-cli-pager 2>$null
if ($LASTEXITCODE -eq 0 -and $orgTargets -and $orgTargets -ne "None") {
    $hubAccount = aws cloudformation describe-stacks --stack-name $orgStackName --region $region --query "Stacks[0].Outputs[?OutputKey=='HubAccountId'].OutputValue" --output text --no-cli-pager
    $callAs = aws cloudformation describe-stacks --stack-name $orgStackName --region $region --query "Stacks[0].Outputs[?OutputKey=='CallAs'].OutputValue" --output text --no-cli-pager
    if ($callAs -ne "DELEGATED_ADMIN") { $callAs = "SELF" }
    Write-Host "`nDestroying the organization stack..." -ForegroundColor Yellow
    Write-Host "      (Deletes the StackSet and the spoke role in every account under $orgTargets)" -ForegroundColor Gray
    & "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $orgStackName -SkipBootstrap -DestroyStack -CdkContext @("orgOnly=true", "orgTargets=$orgTargets", "hubAccountId=$hubAccount", "stackSetsCallAs=$callAs")
    if ($LASTEXITCODE -ne 0) { Write-Host "Organization stack destruction failed" -ForegroundColor Red; exit 1 }
}

Write-Host "`nDestroying the stacks in $region..." -ForegroundColor Yellow
Push-Location cdk
npm install
npx cdk destroy --all --force --no-cli-pager
$destroyExit = $LASTEXITCODE
Pop-Location
if ($destroyExit -ne 0) { Write-Host "Stack destruction failed" -ForegroundColor Red; exit 1 }

Write-Host "`nTeardown complete" -ForegroundColor Green
Write-Host "  Spoke roles deployed by hand (-Accounts) stay in their member accounts. Remove each, with that account's credentials:" -ForegroundColor Gray
Write-Host "     `$env:AWS_REGION = '$region'; & ..\..\shared\scripts\deploy-cdk.ps1 -CdkDirectory cdk -StackName S3DataProtectionSpoke -SkipBootstrap -DestroyStack -CdkContext @('spoke=true', 'hubAccountId=<HUB_ACCOUNT_ID>')"

