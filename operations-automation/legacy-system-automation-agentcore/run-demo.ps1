# AI-Powered Legacy System Automation with AgentCore Browser Tool
# One-click setup and demo execution using CDK
#
# This demo uses AgentCore Browser Tool (cloud browser) instead of local browser.
# Benefits: scalable cloud execution, session recording, live view via AWS Console.
#
# Authentication: AWS IAM (no API key needed)
# Infrastructure: Deployed via AWS CDK

param(
    [switch]$SkipSetup = $false,
    [string]$Region = "us-east-1",
    [switch]$DestroyInfra = $false
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$CdkDir = Join-Path $ScriptDir "infrastructure\cdk"
$SharedScriptsDir = Join-Path $ScriptDir "..\..\shared\scripts"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  AI-Powered Legacy System Automation" -ForegroundColor Cyan
Write-Host "  with AgentCore Browser Tool (Cloud)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# ============================================================
# Handle Infrastructure Destroy
# ============================================================
if ($DestroyInfra) {
    Write-Host "[CLEANUP] Destroying CDK infrastructure..." -ForegroundColor Yellow
    & "$SharedScriptsDir\deploy-cdk.ps1" -CdkDirectory $CdkDir -DestroyStack
    Write-Host "[CLEANUP] Infrastructure destroyed!" -ForegroundColor Green
    exit 0
}

# ============================================================
# STEP 1: Check Prerequisites using shared script
# ============================================================
if (-not $SkipSetup) {
    Write-Host "[SETUP] Running shared prerequisites check..." -ForegroundColor Yellow
    Write-Host ""
    
    # Set region first
    aws configure set region $Region
    
    # Call shared prerequisites script with required checks
    & "$SharedScriptsDir\check-prerequisites.ps1" `
        -RequiredService "agentcore-browser" `
        -MinPythonVersion "3.10" `
        -RequireCDK
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Prerequisites check failed!" -ForegroundColor Red
        exit 1
    }
    
    # Install Python dependencies
    Write-Host ""
    Write-Host "Installing Python dependencies..." -ForegroundColor Yellow
    pip install bedrock-agentcore nova-act rich nest-asyncio --quiet 2>$null
    Write-Host "      ✓ Python dependencies installed" -ForegroundColor Green
    
    Write-Host ""
    Write-Host "[SETUP] Complete!" -ForegroundColor Green
    Write-Host ""
}

# ============================================================
# STEP 2: Deploy Infrastructure with CDK (using shared script)
# ============================================================
Write-Host "------------------------------------------------------------" -ForegroundColor Gray
Write-Host "[INFRA] Deploying AgentCore Browser infrastructure via CDK..." -ForegroundColor Yellow
Write-Host "------------------------------------------------------------" -ForegroundColor Gray

& "$SharedScriptsDir\deploy-cdk.ps1" -CdkDirectory $CdkDir

if ($LASTEXITCODE -ne 0) {
    Write-Host "CDK deployment failed!" -ForegroundColor Red
    exit 1
}

# Get outputs from CloudFormation
Write-Host ""
Write-Host "Getting stack outputs..." -ForegroundColor Yellow
$BrowserId = aws cloudformation describe-stacks --stack-name LegacySystemAutomationAgentCore --region $Region --no-cli-pager --query "Stacks[0].Outputs[?OutputKey=='BrowserId'].OutputValue" --output text
$RecordingsBucket = aws cloudformation describe-stacks --stack-name LegacySystemAutomationAgentCore --region $Region --no-cli-pager --query "Stacks[0].Outputs[?OutputKey=='RecordingsBucketName'].OutputValue" --output text

if ([string]::IsNullOrEmpty($BrowserId) -or [string]::IsNullOrEmpty($RecordingsBucket)) {
    Write-Host "      ❌ Failed to get stack outputs" -ForegroundColor Red
    exit 1
}

Write-Host "      Browser ID: $BrowserId" -ForegroundColor Cyan
Write-Host "      Recordings: s3://$RecordingsBucket/browser-recordings/" -ForegroundColor Cyan
Write-Host ""

# ============================================================
# STEP 3: Create Nova Act Workflow Definition (with S3 for step data)
# ============================================================
Write-Host "------------------------------------------------------------" -ForegroundColor Gray
Write-Host "[WORKFLOW] Creating Nova Act workflow definition..." -ForegroundColor Yellow
Write-Host "------------------------------------------------------------" -ForegroundColor Gray
Write-Host ""

$workflowName = "legacy-system-automation-agentcore"
$definitions = aws nova-act list-workflow-definitions --region $Region --no-cli-pager 2>$null | ConvertFrom-Json
$exists = $definitions.workflowDefinitionSummaries | Where-Object { $_.workflowDefinitionName -eq $workflowName }

if ($exists) {
    Write-Host "      ✓ Workflow '$workflowName' exists" -ForegroundColor Green
    Write-Host "      Note: To update S3 config, delete and recreate the workflow" -ForegroundColor Gray
} else {
    # Create workflow with S3 export config for step data/recordings
    aws nova-act create-workflow-definition `
        --name $workflowName `
        --export-config "s3BucketName=$RecordingsBucket,s3KeyPrefix=nova-act-workflows" `
        --region $Region `
        --no-cli-pager 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "      ✓ Workflow '$workflowName' created with S3 export" -ForegroundColor Green
        Write-Host "      S3: s3://$RecordingsBucket/nova-act-workflows/" -ForegroundColor Cyan
    } else {
        Write-Host "      ⚠ Could not create workflow (may already exist)" -ForegroundColor Yellow
    }
}

Write-Host ""

# ============================================================
# STEP 4: Display Live View Instructions
# ============================================================
Write-Host "------------------------------------------------------------" -ForegroundColor Gray
Write-Host "[LIVE VIEW] Watch the browser session in AWS Console" -ForegroundColor Cyan
Write-Host "------------------------------------------------------------" -ForegroundColor Gray
Write-Host ""
Write-Host "  Console URL:" -ForegroundColor White
Write-Host "  https://$Region.console.aws.amazon.com/bedrock-agentcore/builtInTools" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Instructions:" -ForegroundColor White
Write-Host "  1. Open the URL above in your browser" -ForegroundColor Gray
Write-Host "  2. Navigate to 'Built-in tools' > Select your browser" -ForegroundColor Gray
Write-Host "  3. Find your active session (status: Ready)" -ForegroundColor Gray
Write-Host "  4. Click 'View live session' to watch in real-time" -ForegroundColor Gray
Write-Host ""

# ============================================================
# STEP 5: Run Demo
# ============================================================
Write-Host "------------------------------------------------------------" -ForegroundColor Gray
Write-Host "[DEMO] Running automation on Nova Act Gym (Cloud Browser)..." -ForegroundColor Yellow
Write-Host "------------------------------------------------------------" -ForegroundColor Gray
Write-Host ""

# Build command arguments
$demoArgs = @("--region", $Region, "--browser-id", $BrowserId)

Push-Location $ScriptDir
try {
    python create_ticket_agentcore.py @demoArgs
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Demo Complete!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Session recordings stored at:" -ForegroundColor Gray
Write-Host "  s3://$RecordingsBucket/browser-recordings/" -ForegroundColor Cyan
Write-Host ""
Write-Host "  To destroy infrastructure:" -ForegroundColor Gray
Write-Host "  .\run-demo.ps1 -DestroyInfra" -ForegroundColor Yellow
Write-Host ""
