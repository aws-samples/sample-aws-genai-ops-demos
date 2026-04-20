# G.O.A.T. Demo Scenario B - CloudWatch Apr 1 Incident Correlation
#
# Creates a resolved Support case that correlates with the real CloudWatch
# health event from April 1, 2026, enabling cross-domain incident correlation.
# No AWS resources are created — only a Support case (zero cost).
#
# Script is idempotent - safe to re-run (creates a new case each time).
#
# Usage: .\setup-scenario-b.ps1

$ErrorActionPreference = "Continue"

# ---------------------------------------------------------------------------
# Track created resources for summary
# ---------------------------------------------------------------------------
$supportCaseId = ""
$warnings = @()

# ---------------------------------------------------------------------------
# 1. Verify AWS credentials
# ---------------------------------------------------------------------------
Write-Host "=== G.O.A.T. Demo Scenario B - CloudWatch Apr 1 Incident Correlation ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Verifying AWS credentials..." -ForegroundColor Yellow

try {
    $accountId = aws sts get-caller-identity --query "Account" --output text 2>$null
    if ([string]::IsNullOrEmpty($accountId)) { throw "Empty account ID" }
    Write-Host "  Authenticated to account: $accountId" -ForegroundColor Green
} catch {
    Write-Host "ERROR: AWS credentials not configured." -ForegroundColor Red
    Write-Host "Run 'aws configure' or set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# 2. Detect region
# ---------------------------------------------------------------------------
Write-Host "Detecting AWS region..." -ForegroundColor Yellow

$region = $env:AWS_DEFAULT_REGION
if ([string]::IsNullOrEmpty($region)) { $region = $env:AWS_REGION }
if ([string]::IsNullOrEmpty($region)) {
    $region = aws configure get region 2>$null
}
if ([string]::IsNullOrEmpty($region)) {
    $region = "us-east-1"
    Write-Host "  No region configured, falling back to us-east-1" -ForegroundColor Yellow
}
Write-Host "  Region: $region" -ForegroundColor Green
Write-Host ""

# ---------------------------------------------------------------------------
# 3. Create Support case (if Support plan is active)
# ---------------------------------------------------------------------------
Write-Host "--- Support Case ---" -ForegroundColor Magenta

Write-Host "Detecting Support plan..." -ForegroundColor Yellow
$supportCheck = aws support describe-services --query "services[0].code" --output text --region us-east-1 2>&1

if ($supportCheck -match "SubscriptionRequiredException") {
    Write-Host "  WARNING: No Business or Enterprise Support plan detected." -ForegroundColor Yellow
    Write-Host "  Skipping Support case creation. To enable this feature, upgrade your Support plan." -ForegroundColor Yellow
    $warnings += "Support case skipped - no Support plan"
    $supportCaseId = "skipped (no Support plan)"
} else {
    Write-Host "Creating Support case..." -ForegroundColor Yellow
    try {
        $supportCaseId = aws support create-case `
            --subject "CloudWatch monitoring gaps and missing alarms on Apr 1 - G.O.A.T. demo" `
            --communication-body "Our monitoring infrastructure experienced gaps on April 1, 2026 due to the CloudWatch planned lifecycle event (AWS_CLOUDWATCH_PLANNED_LIFECYCLE_EVENT). Several CloudWatch alarms and dashboards were affected, causing blind spots in our observability stack. This case was created for demo purposes by the G.O.A.T. provisioning scripts." `
            --service-code "amazon-cloudwatch" `
            --category-code "other" `
            --severity-code "low" `
            --language "en" `
            --query "caseId" --output text --region us-east-1 2>&1
        if ($LASTEXITCODE -ne 0) { throw $supportCaseId }
        Write-Host "  Created Support case: $supportCaseId" -ForegroundColor Green

        # Add demo-purpose communication
        aws support add-communication-to-case `
            --case-id $supportCaseId `
            --communication-body "This Support case was created automatically by the G.O.A.T. demo provisioning scripts for demonstration purposes only. It is being resolved immediately. No action is needed from AWS Support." `
            --region us-east-1 2>$null | Out-Null

        # Immediately resolve the case
        Write-Host "  Resolving Support case..." -ForegroundColor Yellow
        try {
            aws support resolve-case --case-id $supportCaseId --region us-east-1 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "resolve failed" }
            Write-Host "  Support case resolved: $supportCaseId" -ForegroundColor Green
        } catch {
            Write-Host "  WARNING: Failed to resolve Support case $supportCaseId" -ForegroundColor Red
            Write-Host "  Please close it manually via the AWS Console: https://console.aws.amazon.com/support/home" -ForegroundColor Red
            $warnings += "Support case resolve failed - close manually: $supportCaseId"
        }
    } catch {
        Write-Host "  WARNING: Failed to create Support case: $_" -ForegroundColor Red
        $warnings += "Support case creation failed"
        $supportCaseId = ""
    }
}

Write-Host ""

# ---------------------------------------------------------------------------
# 4. Summary
# ---------------------------------------------------------------------------
Write-Host "========================================" -ForegroundColor Green
Write-Host "  G.O.A.T. Scenario B Setup Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Region:              $region" -ForegroundColor Cyan

if (-not [string]::IsNullOrEmpty($supportCaseId)) {
    Write-Host "  Support Case:        $supportCaseId" -ForegroundColor Cyan
}

if ($warnings.Count -gt 0) {
    Write-Host ""
    Write-Host "  Warnings:" -ForegroundColor Yellow
    foreach ($w in $warnings) {
        Write-Host "    - $w" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "  Suggested Demo Query:" -ForegroundColor Cyan
Write-Host "    `"We had monitoring gaps on April 1st - was there an AWS issue?`"" -ForegroundColor Green
Write-Host ""
Write-Host "  To clean up all demo resources:" -ForegroundColor Gray
Write-Host "    .\cleanup-scenarios.ps1    (PowerShell)" -ForegroundColor Gray
Write-Host "    ./cleanup-scenarios.sh     (Bash)" -ForegroundColor Gray
Write-Host ""
