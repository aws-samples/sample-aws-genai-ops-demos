#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Generate AI-powered incident response playbooks from your AWS architecture

.DESCRIPTION
    Deploys an S3 bucket via CDK, discovers your AWS account's architecture,
    uses Amazon Bedrock to generate tailored incident response playbooks, and
    uploads results to S3 (plus saves locally).

.PARAMETER OutputFormat
    Output format: ssm, markdown, or both (default: both)

.PARAMETER ModelId
    Bedrock model ID (default: anthropic.claude-3-5-sonnet-20241022-v2:0)

.PARAMETER Region
    AWS region to scan (default: current configured region)

.PARAMETER OrgContext
    Path to JSON file with organization-specific context

.PARAMETER OutputDir
    Local directory for generated playbooks (default: ./output)

.PARAMETER SkipSetup
    Skip CDK deployment (use existing S3 bucket)

.EXAMPLE
    .\build-playbooks.ps1
    .\build-playbooks.ps1 -OutputFormat markdown
    .\build-playbooks.ps1 -OrgContext org-context.json
    .\build-playbooks.ps1 -SkipSetup

.NOTES
    This script uses the AWS region configured in your AWS CLI profile.
    To set your region: aws configure set region <region>
#>

param(
    [ValidateSet("ssm", "markdown", "both")]
    [string]$OutputFormat = "both",
    [string]$ModelId = "anthropic.claude-3-5-sonnet-20241022-v2:0",
    [string]$Region = "",
    [string]$OrgContext = "",
    [string]$OutputDir = "./output",
    [switch]$SkipSetup = $false
)

$ErrorActionPreference = "Stop"

Write-Host "=== AI Incident Response Playbook Builder ===" -ForegroundColor Cyan
Write-Host "Generates tailored IR playbooks from your AWS architecture" -ForegroundColor Green
Write-Host ""
Write-Host "Pipeline:" -ForegroundColor Yellow
Write-Host "  1. Deploy infrastructure (S3 output bucket via CDK)" -ForegroundColor Gray
Write-Host "  2. Discover architecture (read-only API calls)" -ForegroundColor Gray
Write-Host "  3. Analyze threats via Amazon Bedrock" -ForegroundColor Gray
Write-Host "  4. Generate playbooks with MITRE ATT&CK mapping" -ForegroundColor Gray
Write-Host "  5. Upload to S3 + save locally" -ForegroundColor Gray
Write-Host ""

# Get script and shared directories
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$srcDir = Join-Path $scriptDir "src"
$cdkDir = Join-Path $scriptDir "infrastructure/cdk"
$sharedScriptsDir = Join-Path $scriptDir "..\..\shared\scripts"

# Silence JSII warnings for untested Node.js versions
$env:JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION = "1"

# ── Prerequisites ──────────────────────────────────────────────────────────────
if (-not $SkipSetup) {
    Write-Host "Running prerequisites check..." -ForegroundColor Yellow
    & "$sharedScriptsDir\check-prerequisites.ps1" `
        -RequiredService "bedrock" `
        -MinPythonVersion "3.10" `
        -RequireCDK

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Prerequisites check failed" -ForegroundColor Red
        exit 1
    }
}

# Resolve region
if ([string]::IsNullOrEmpty($Region)) {
    $Region = $global:AWS_REGION
}
if ([string]::IsNullOrEmpty($Region)) {
    $Region = $env:AWS_DEFAULT_REGION
}
if ([string]::IsNullOrEmpty($Region)) {
    $Region = aws configure get region 2>$null
}
if ([string]::IsNullOrEmpty($Region)) {
    Write-Host "ERROR: No AWS region configured" -ForegroundColor Red
    Write-Host "  aws configure set region <region>" -ForegroundColor Cyan
    exit 1
}

$accountId = $global:AWS_ACCOUNT_ID
if ([string]::IsNullOrEmpty($accountId)) {
    $accountId = (aws sts get-caller-identity --query Account --output text --no-cli-pager)
}

# ── Deploy Infrastructure ─────────────────────────────────────────────────────
if (-not $SkipSetup) {
    Write-Host ""
    Write-Host "Deploying infrastructure via CDK..." -ForegroundColor Yellow
    Write-Host "  Region: $Region" -ForegroundColor Gray

    & "$sharedScriptsDir\deploy-cdk.ps1" -CdkDirectory $cdkDir

    if ($LASTEXITCODE -ne 0) {
        Write-Host "CDK deployment failed" -ForegroundColor Red
        exit 1
    }
}

# Get S3 bucket from stack outputs
$stackName = "PlaybookBuilderStack-$Region"
$outputBucket = aws cloudformation describe-stacks `
    --stack-name $stackName `
    --region $Region `
    --no-cli-pager `
    --query "Stacks[0].Outputs[?OutputKey=='OutputBucketName'].OutputValue" `
    --output text 2>$null

if ([string]::IsNullOrEmpty($outputBucket)) {
    Write-Host "❌ Failed to get S3 bucket from stack outputs" -ForegroundColor Red
    if ($SkipSetup) {
        Write-Host "  Stack may not exist. Run without -SkipSetup to deploy infrastructure first." -ForegroundColor Gray
    }
    exit 1
}

# Generate unique job ID
$jobId = "ir-playbooks-$(Get-Date -Format 'yyyyMMdd-HHmmss')"

Write-Host ""
Write-Host "Configuration:" -ForegroundColor Yellow
Write-Host "  Account:       $accountId" -ForegroundColor Gray
Write-Host "  Region:        $Region" -ForegroundColor Gray
Write-Host "  Model:         $ModelId" -ForegroundColor Gray
Write-Host "  Output format: $OutputFormat" -ForegroundColor Gray
Write-Host "  S3 Bucket:     $outputBucket" -ForegroundColor Gray
Write-Host "  Job ID:        $jobId" -ForegroundColor Gray
if (-not [string]::IsNullOrEmpty($OrgContext)) {
    Write-Host "  Org context:   $OrgContext" -ForegroundColor Gray
}
Write-Host ""

# ── Install Python dependencies ────────────────────────────────────────────────
Write-Host "Installing Python dependencies..." -ForegroundColor Yellow
pip install -q -r "$srcDir\requirements.txt" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ❌ Failed to install Python dependencies" -ForegroundColor Red
    exit 1
}
Write-Host "  ✓ Dependencies installed" -ForegroundColor Green

# Ensure output directories exist
New-Item -ItemType Directory -Path (Join-Path $OutputDir "reports") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $OutputDir "playbooks") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $OutputDir "ssm-documents") -Force | Out-Null

# ── Phase 1: Discovery ────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Phase 1: Discovering AWS architecture..." -ForegroundColor Yellow
Write-Host "  (Read-only API calls — nothing is modified)" -ForegroundColor Gray

$discoveryStart = Get-Date
$profilePath = Join-Path $OutputDir "reports" "architecture-profile.json"

python "$srcDir\discovery.py" `
    --region $Region `
    --output "$profilePath"

if ($LASTEXITCODE -ne 0) {
    Write-Host "  ❌ Discovery failed" -ForegroundColor Red
    exit 1
}

$discoveryElapsed = [math]::Round(((Get-Date) - $discoveryStart).TotalSeconds, 1)
Write-Host "  ✓ Architecture discovered ($discoveryElapsed s)" -ForegroundColor Green

# ── Phase 2: Threat Assessment & Playbook Generation ──────────────────────────
Write-Host ""
Write-Host "Phase 2: Generating playbooks via Amazon Bedrock..." -ForegroundColor Yellow
Write-Host "  This may take 1-5 minutes depending on account complexity" -ForegroundColor Gray

$generateStart = Get-Date

$generateArgs = @(
    "$srcDir\generator.py",
    "--profile", $profilePath,
    "--model-id", $ModelId,
    "--region", $Region,
    "--output-dir", $OutputDir,
    "--output-format", $OutputFormat
)

if (-not [string]::IsNullOrEmpty($OrgContext)) {
    if (-not (Test-Path $OrgContext)) {
        Write-Host "  ❌ Org context file not found: $OrgContext" -ForegroundColor Red
        exit 1
    }
    $generateArgs += "--org-context"
    $generateArgs += $OrgContext
}

python @generateArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host "  ❌ Playbook generation failed" -ForegroundColor Red
    exit 1
}

$generateElapsed = [math]::Round(((Get-Date) - $generateStart).TotalSeconds, 1)
Write-Host "  ✓ Playbooks generated ($generateElapsed s)" -ForegroundColor Green

# ── Phase 3: Output Assembly ──────────────────────────────────────────────────
Write-Host ""
Write-Host "Phase 3: Assembling output..." -ForegroundColor Yellow

python "$srcDir\output.py" `
    --output-dir $OutputDir `
    --output-format $OutputFormat

if ($LASTEXITCODE -ne 0) {
    Write-Host "  ❌ Output assembly failed" -ForegroundColor Red
    exit 1
}
Write-Host "  ✓ Output assembled" -ForegroundColor Green

# ── Phase 4: Upload to S3 ────────────────────────────────────────────────────
Write-Host ""
Write-Host "Phase 4: Uploading to S3..." -ForegroundColor Yellow

aws s3 cp $OutputDir "s3://$outputBucket/$jobId/" `
    --recursive `
    --region $Region `
    --no-cli-pager | Out-Null

if ($LASTEXITCODE -eq 0) {
    Write-Host "  ✓ Uploaded to s3://$outputBucket/$jobId/" -ForegroundColor Green
} else {
    Write-Host "  ❌ S3 upload failed" -ForegroundColor Red
    exit 1
}

# ── Summary ───────────────────────────────────────────────────────────────────
$totalElapsed = [math]::Round(((Get-Date) - $discoveryStart).TotalSeconds, 1)

Write-Host ""
Write-Host "=== Playbook Generation Complete ===" -ForegroundColor Cyan
Write-Host "  Total time: $totalElapsed s" -ForegroundColor White
Write-Host ""

# List generated files
Write-Host "Generated files:" -ForegroundColor Yellow
if (Test-Path (Join-Path $OutputDir "reports")) {
    Write-Host "  Reports:" -ForegroundColor White
    Get-ChildItem -Path (Join-Path $OutputDir "reports") -File | ForEach-Object {
        Write-Host "    $($_.Name)" -ForegroundColor Gray
    }
}
if (Test-Path (Join-Path $OutputDir "playbooks")) {
    $playbookFiles = Get-ChildItem -Path (Join-Path $OutputDir "playbooks") -File
    if ($playbookFiles.Count -gt 0) {
        Write-Host "  Playbooks ($($playbookFiles.Count)):" -ForegroundColor White
        $playbookFiles | ForEach-Object {
            Write-Host "    $($_.Name)" -ForegroundColor Gray
        }
    }
}
if (Test-Path (Join-Path $OutputDir "ssm-documents")) {
    $ssmFiles = Get-ChildItem -Path (Join-Path $OutputDir "ssm-documents") -File
    if ($ssmFiles.Count -gt 0) {
        Write-Host "  SSM Documents ($($ssmFiles.Count)):" -ForegroundColor White
        $ssmFiles | ForEach-Object {
            Write-Host "    $($_.Name)" -ForegroundColor Gray
        }
    }
}

Write-Host ""
Write-Host "=== Output Locations ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "S3 (persistent):" -ForegroundColor Yellow
Write-Host "  s3://$outputBucket/$jobId/" -ForegroundColor White
Write-Host ""
Write-Host "Browse S3 output:" -ForegroundColor Yellow
Write-Host "  aws s3 ls s3://$outputBucket/$jobId/ --recursive" -ForegroundColor Gray
Write-Host ""
Write-Host "Download from S3:" -ForegroundColor Yellow
Write-Host "  aws s3 cp s3://$outputBucket/$jobId/ ./ir-playbooks --recursive" -ForegroundColor Gray
Write-Host ""
Write-Host "Local copy:" -ForegroundColor Yellow
Write-Host "  $OutputDir/" -ForegroundColor White
Write-Host ""
Write-Host "=== Next Steps ===" -ForegroundColor Cyan
Write-Host "1. Review playbooks in $OutputDir/playbooks/" -ForegroundColor Yellow
Write-Host "2. Check MITRE ATT&CK coverage in $OutputDir/reports/attack-coverage-matrix.md" -ForegroundColor Yellow
Write-Host "3. Import SSM documents:" -ForegroundColor Yellow
Write-Host "   aws ssm create-document --content file://$OutputDir/ssm-documents/<doc>.json --name <name> --document-type Automation" -ForegroundColor Gray
Write-Host "4. Run tabletop exercises with your team using the generated playbooks" -ForegroundColor Yellow
Write-Host ""
Write-Host "Cleanup:" -ForegroundColor Yellow
Write-Host "  cd infrastructure\cdk; npx cdk destroy --no-cli-pager" -ForegroundColor Gray
