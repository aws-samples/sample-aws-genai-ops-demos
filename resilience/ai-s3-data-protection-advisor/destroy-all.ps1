# S3 data protection advisor - complete teardown script
# Windows version - mirrors destroy-all.sh
# -MockData: delete the mock estate first (tagged buckets and role only)
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

Write-Host "`nDestroying the stacks in $($global:AWS_REGION)..." -ForegroundColor Yellow
Push-Location cdk
npm install
npx cdk destroy --all --force --no-cli-pager
$destroyExit = $LASTEXITCODE
Pop-Location
if ($destroyExit -ne 0) { Write-Host "Stack destruction failed" -ForegroundColor Red; exit 1 }

Write-Host "`nTeardown complete" -ForegroundColor Green
