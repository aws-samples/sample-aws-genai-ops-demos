[CmdletBinding()]
param(
    [string]$RoleName,
    [string]$UserName,
    [int]$Days = 30,
    [string]$ModelId = "us.anthropic.claude-sonnet-4-20250514-v1:0",
    [string]$OutputDir = "./output",
    [switch]$SkipSetup,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

if ($Help) {
    Write-Host "Usage: ./generate-boundaries.ps1 [OPTIONS]"
    Write-Host ""
    Write-Host "Generates least-privilege permission boundaries by analyzing CloudTrail activity."
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  -RoleName NAME     IAM role name to analyze"
    Write-Host "  -UserName NAME     IAM user name to analyze"
    Write-Host "  -Days NUM          Number of days of CloudTrail history (default: 30)"
    Write-Host "  -ModelId ID        Bedrock model ID (default: us.anthropic.claude-sonnet-4-20250514-v1:0)"
    Write-Host "  -OutputDir DIR     Output directory for generated policies (default: ./output)"
    Write-Host "  -SkipSetup         Skip prerequisite checks and CDK deployment"
    Write-Host "  -Help              Show this help message"
    Write-Host ""
    Write-Host "Either -RoleName or -UserName must be provided."
    exit 0
}

Write-Host "=== AI Permission Boundary Generator ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Analyzes CloudTrail logs and generates least-privilege permission"
Write-Host "boundaries using Amazon Bedrock."
Write-Host ""

# Validate inputs
if (-not $RoleName -and -not $UserName) {
    Write-Host "Error: Either -RoleName or -UserName must be provided." -ForegroundColor Red
    Write-Host "Run with -Help for usage information."
    exit 1
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SharedScriptsDir = Join-Path $ScriptDir "../../shared/scripts"
$CdkDir = Join-Path $ScriptDir "infrastructure/cdk"
$BucketName = ""

# Setup phase
if (-not $SkipSetup) {
    Write-Host "--- Checking prerequisites ---" -ForegroundColor Yellow
    & "$SharedScriptsDir/check-prerequisites.ps1" -RequiredService bedrock -RequireCdk

    Write-Host ""
    Write-Host "--- Deploying CDK infrastructure ---" -ForegroundColor Yellow
    & "$SharedScriptsDir/deploy-cdk.ps1" -CdkDirectory $CdkDir

    Write-Host ""
    Write-Host "--- Retrieving stack outputs ---" -ForegroundColor Yellow
    try {
        $CurrentRegion = (aws configure get region 2>$null)
        if (-not $CurrentRegion) { $CurrentRegion = $env:AWS_DEFAULT_REGION }
        if (-not $CurrentRegion) { $CurrentRegion = $env:AWS_REGION }
        if (-not $CurrentRegion) { $CurrentRegion = "us-east-1" }
        $StackName = "PermissionBoundaryStack-$CurrentRegion"
        $BucketName = (aws cloudformation describe-stacks `
            --stack-name $StackName --region $CurrentRegion `
            --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" `
            --output text --no-cli-pager)
    } catch {
        $BucketName = ""
    }
}

# Install Python dependencies
Write-Host "--- Installing Python dependencies ---" -ForegroundColor Yellow
pip install -r "$ScriptDir/src/requirements.txt" --quiet

# Set PYTHONPATH to include repo root for shared.utils
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "../..")
$env:PYTHONPATH = "$RepoRoot;$env:PYTHONPATH"

# Build python arguments
$PythonArgs = @("$ScriptDir/src/main.py", "--days", $Days, "--model-id", $ModelId, "--output-dir", $OutputDir)
if ($RoleName) { $PythonArgs += @("--role-name", $RoleName) }
if ($UserName) { $PythonArgs += @("--user-name", $UserName) }
if ($BucketName) { $PythonArgs += @("--bucket-name", $BucketName) }

# Run analysis
Write-Host ""
Write-Host "--- Running permission boundary analysis ---" -ForegroundColor Yellow
Push-Location "$ScriptDir/src"
& python @PythonArgs
Pop-Location

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "=== Permission Boundary Generation Complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Results saved to: $OutputDir" -ForegroundColor Green
Write-Host "Review the generated policies before applying them."
