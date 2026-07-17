# refresh-providers.ps1 — Update the live provider registry with NO redeploy.
#
# The running MCP server reads providers.json from S3 via a conditional GET,
# so pushing a new version of the file is all it takes. The server picks up
# the change within one poll interval (default 60s). No zip, no CDK, no restart.
#
# Usage: edit agent/providers.json, then run:  .\refresh-providers.ps1

param()

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path "$ScriptDir/../../..").Path

& "$repoRoot/shared/scripts/check-prerequisites.ps1" | Out-Null
$region = $global:AWS_REGION
$account = (aws sts get-caller-identity --query "Account" --output text)

$bucketName = "saas-status-mcp-$account-$region"
$key = "config/providers.json"

Write-Host "Uploading agent/providers.json to s3://$bucketName/$key ..." -ForegroundColor Yellow
aws s3 cp "$ScriptDir/../agent/providers.json" "s3://$bucketName/$key" --quiet

$count = (Get-Content "$ScriptDir/../agent/providers.json" | ConvertFrom-Json).providers.Count
Write-Host ""
Write-Host "Done. $count providers published to the live registry." -ForegroundColor Green
Write-Host "The running MCP server will pick up the change within its poll interval (~60s)." -ForegroundColor Cyan
Write-Host "No redeploy required." -ForegroundColor Cyan
