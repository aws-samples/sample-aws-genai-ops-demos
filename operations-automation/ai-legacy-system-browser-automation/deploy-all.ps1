# Deploy script for Legacy System Browser Automation with AgentCore
# Deploys: AgentCore Browser Tool, S3 recordings bucket, IAM roles

param(
    [switch]$Destroy
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$CdkDir = Join-Path (Join-Path (Join-Path $ScriptDir "ai-browser-automation") "infrastructure") "cdk"
$SharedScriptsDir = Join-Path $ScriptDir "..\..\shared\scripts"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Legacy System Browser Automation - Deployment" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# Run shared prerequisites check
Write-Host ""
Write-Host "Checking prerequisites..." -ForegroundColor Yellow
& "$SharedScriptsDir\check-prerequisites.ps1" -RequireCDK

if ($LASTEXITCODE -ne 0) {
    Write-Host "Prerequisites check failed" -ForegroundColor Red
    exit 1
}

$Region = $global:AWS_REGION

Write-Host "  Account: $(aws sts get-caller-identity --query Account --output text --no-cli-pager)"
Write-Host "  Region:  $Region"

# Destroy mode
if ($Destroy) {
    Write-Host ""
    Write-Host "Destroying infrastructure..." -ForegroundColor Red
    Push-Location $CdkDir
    pip install -r requirements.txt -q
    npx cdk destroy --force
    Pop-Location
    Write-Host ""
    Write-Host "Infrastructure destroyed." -ForegroundColor Green
    exit 0
}

# Deploy
Write-Host ""
Write-Host "Installing CDK dependencies..." -ForegroundColor Yellow
Push-Location $CdkDir
pip install -r requirements.txt -q

Write-Host ""
Write-Host "Deploying AgentCore Browser Tool stack..." -ForegroundColor Yellow
npx cdk deploy --require-approval never
Pop-Location

# Extract outputs
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Deployment Complete" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green

$StackName = "LegacySystemAutomationAgentCore-$Region"
try {
    $Outputs = aws cloudformation describe-stacks --stack-name $StackName --query "Stacks[0].Outputs" --output json --no-cli-pager 2>$null | ConvertFrom-Json

    $BrowserId = ($Outputs | Where-Object { $_.OutputKey -eq "BrowserId" }).OutputValue
    $Bucket = ($Outputs | Where-Object { $_.OutputKey -eq "RecordingsBucketName" }).OutputValue

    Write-Host ""
    Write-Host "  Browser ID:        $BrowserId" -ForegroundColor Cyan
    Write-Host "  Recordings Bucket: $Bucket" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Set environment variables:" -ForegroundColor Yellow
    Write-Host "    `$env:BROWSER_ID=`"$BrowserId`"" -ForegroundColor White
    Write-Host "    `$env:AWS_REGION=`"$Region`"" -ForegroundColor White
    Write-Host ""
    Write-Host "  Live view: https://$Region.console.aws.amazon.com/bedrock-agentcore/builtInTools" -ForegroundColor Cyan
} catch {
    Write-Host "  Could not retrieve stack outputs." -ForegroundColor Yellow
}

Write-Host ""
