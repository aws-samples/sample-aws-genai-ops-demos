# Package three separate agent zips for S3 upload
# Each agent (discover, analyze, transform) gets its own deployment_package.zip

$ErrorActionPreference = "Stop"

$AgentDir = Join-Path $PSScriptRoot ".." "agent"
$AgentDir = (Resolve-Path $AgentDir).Path

$agents = @("discover", "analyze", "transform")

foreach ($agent in $agents) {
    $AgentSubDir = Join-Path $AgentDir $agent
    $DeploymentDir = Join-Path $AgentSubDir "deployment_package"
    $OutputZip = Join-Path $AgentSubDir "deployment_package.zip"

    Write-Host "`nPackaging $agent agent from: $AgentSubDir" -ForegroundColor Cyan

    # Clean up
    if (Test-Path $DeploymentDir) { Remove-Item -Recurse -Force $DeploymentDir }
    if (Test-Path $OutputZip) { Remove-Item -Force $OutputZip }

    # Install dependencies
    Write-Host "  Installing dependencies..." -ForegroundColor Yellow
    $reqFile = Join-Path $AgentSubDir "requirements.txt"
    uv pip install -r $reqFile --python-platform aarch64-unknown-linux-gnu --python-version 3.13 --target $DeploymentDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Failed to install dependencies for $agent" -ForegroundColor Red
        exit 1
    }

    # Copy main.py
    Copy-Item (Join-Path $AgentSubDir "main.py") (Join-Path $DeploymentDir "main.py")

    # Create zip
    Write-Host "  Creating zip archive..." -ForegroundColor Yellow
    Compress-Archive -Path "$DeploymentDir/*" -DestinationPath $OutputZip -Force

    # Clean up deployment_package directory
    Remove-Item -Recurse -Force $DeploymentDir

    $ZipSize = (Get-Item $OutputZip).Length
    $ZipSizeMB = [math]::Round($ZipSize / 1MB, 2)
    Write-Host "  $agent agent packaged: $ZipSizeMB MB" -ForegroundColor Green
}

Write-Host "`nAll agents packaged successfully!" -ForegroundColor Green
