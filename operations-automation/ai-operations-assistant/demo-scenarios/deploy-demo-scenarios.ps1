# G.O.A.T. Demo Scenarios - CDK Deployment Script
#
# Deploys demo scenario CDK stacks and creates Support cases for G.O.A.T. demos.
# Uses the separate demo-scenarios-app.ts CDK entry point.

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("all", "account-health", "cloudwatch-incident", "connectivity", "network-troubleshooting")]
    [string]$Scenario
)

$ErrorActionPreference = "Stop"

Write-Host "=== G.O.A.T. Demo Scenarios Deployment ===" -ForegroundColor Cyan
Write-Host "      Scenario: $Scenario" -ForegroundColor Gray

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
Write-Host "`nRunning prerequisites check..." -ForegroundColor Yellow
& "$PSScriptRoot\..\..\..\shared\scripts\check-prerequisites.ps1" -RequireCDK

if ($LASTEXITCODE -ne 0) {
    Write-Host "Prerequisites check failed" -ForegroundColor Red
    exit 1
}

$region = $global:AWS_REGION
$cdkDir = "$PSScriptRoot\..\infrastructure\cdk"
$cdkApp = "npx ts-node --prefer-ts-exts bin/demo-scenarios-app.ts"

# Track created/found case IDs for summary
$script:caseIds = @()

# ---------------------------------------------------------------------------
# Helper: Deploy a CDK stack
# ---------------------------------------------------------------------------
function Invoke-CdkDeploy {
    param([string]$StackName)
    Write-Host "`nDeploying $StackName..." -ForegroundColor Yellow
    Push-Location $cdkDir
    npx cdk deploy $StackName --app $cdkApp --require-approval never --no-cli-pager
    $exitCode = $LASTEXITCODE
    Pop-Location
    if ($exitCode -ne 0) {
        Write-Host "ERROR: Deployment of $StackName failed" -ForegroundColor Red
        exit 1
    }
}

# ---------------------------------------------------------------------------
# Helper: Create and resolve a Support case (with duplicate check)
# Returns the case displayId (or existing one if duplicate found)
# ---------------------------------------------------------------------------
function New-DemoSupportCase {
    param(
        [string]$Subject,
        [string]$Body,
        [string]$ServiceCode = "general-info",
        [string]$CategoryCode = "other"
    )
    try {
        # Check for existing case with the same subject (avoid duplicates)
        $existing = aws support describe-cases `
            --include-resolved-cases `
            --region us-east-1 `
            --query "cases[?contains(subject,'$Subject')].displayId" `
            --output text --no-cli-pager 2>&1
        if ($LASTEXITCODE -ne 0) {
            if ($existing -match "SubscriptionRequired" -or $existing -match "not subscribed") {
                Write-Host "  No Business or Enterprise Support plan -- skipping case creation" -ForegroundColor Yellow
                return ""
            }
            Write-Host "  WARNING: Cannot query Support API: $existing" -ForegroundColor Yellow
            return ""
        }
        if ($existing -and $existing.Trim() -ne "" -and $existing.Trim() -ne "None") {
            $displayId = ($existing.Trim() -split "\s+")[0]
            Write-Host "  Support case already exists: $displayId -- skipping creation" -ForegroundColor Gray
            $script:caseIds += $displayId
            return $displayId
        }

        $caseId = aws support create-case `
            --subject $Subject `
            --communication-body $Body `
            --service-code $ServiceCode `
            --category-code $CategoryCode `
            --severity-code "low" `
            --language "en" `
            --region us-east-1 `
            --query "caseId" --output text --no-cli-pager 2>&1
        if ($LASTEXITCODE -ne 0) {
            if ($caseId -match "SubscriptionRequiredException") {
                Write-Host "  No Business or Enterprise Support plan detected -- skipping" -ForegroundColor Yellow
                return ""
            }
            Write-Host "  WARNING: CreateCase failed: $caseId" -ForegroundColor Yellow
            return ""
        }
        Start-Sleep -Seconds 5
        aws support resolve-case --case-id $caseId --region us-east-1 --no-cli-pager 2>$null
        # Get the display ID for user-friendly output
        $displayId = aws support describe-cases `
            --case-id-list $caseId `
            --include-resolved-cases `
            --region us-east-1 `
            --query "cases[0].displayId" `
            --output text --no-cli-pager 2>$null
        if (-not $displayId -or $displayId -eq "None") { $displayId = $caseId }
        Write-Host "  Created and resolved Support case: $displayId" -ForegroundColor Green
        $script:caseIds += $displayId
        return $displayId
    } catch {
        Write-Host "  WARNING: Support API error: $_" -ForegroundColor Yellow
        return ""
    }
}

# ---------------------------------------------------------------------------
# Deployment Logic
# ---------------------------------------------------------------------------
$stackA = "GOATDemoScenarioA-$region"
$stackC = "GOATDemoScenarioC-$region"

switch ($Scenario) {
    "all" {
        Invoke-CdkDeploy $stackA
        New-DemoSupportCase "General account review - G.O.A.T. demo" "This case was created for demo purposes by the G.O.A.T. provisioning scripts."
        New-DemoSupportCase "CloudWatch monitoring gaps and missing alarms on Apr 1 - G.O.A.T. demo" "Our team noticed a CloudWatch lifecycle event on April 1 resulting in monitoring gaps. Several alarms were missing or misconfigured."
        Invoke-CdkDeploy $stackC
        New-DemoSupportCase `
            -Subject "EC2 instance failing HTTPS to ECR - connection reset by peer in $region" `
            -Body "Our instance in goat-demo-vpc is failing to establish HTTPS connections to ECR (endpoint: ecr.$region.amazonaws.com on port 443). The connexion is going through the TGW and the NFW in goat-demo-security-vpc but it is dropped. This case was created by the G.O.A.T. demo provisioning scripts for demonstration purposes." `
            -ServiceCode "service-network-firewall" `
            -CategoryCode "general-guidance"
    }
    "account-health" {
        Invoke-CdkDeploy $stackA
        New-DemoSupportCase "General account review - G.O.A.T. demo" "This case was created for demo purposes by the G.O.A.T. provisioning scripts."
    }
    "cloudwatch-incident" {
        New-DemoSupportCase "CloudWatch monitoring gaps and missing alarms on Apr 1 - G.O.A.T. demo" "Our team noticed a CloudWatch lifecycle event on April 1 resulting in monitoring gaps. Several alarms were missing or misconfigured."
    }
    "connectivity" {
        Invoke-CdkDeploy $stackC
        New-DemoSupportCase `
            -Subject "EC2 instance failing HTTPS to ECR - connection reset by peer in $region" `
            -Body "Our instance in goat-demo-vpc is failing to establish HTTPS connections to ECR (endpoint: ecr.$region.amazonaws.com on port 443). The connexion is going through the TGW and the NFW in goat-demo-security-vpc but it is dropped. This case was created by the G.O.A.T. demo provisioning scripts for demonstration purposes." `
            -ServiceCode "service-network-firewall" `
            -CategoryCode "general-guidance"
    }
}

# ---------------------------------------------------------------------------
# Deployment Summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  G.O.A.T. Demo Scenario Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Region: $region" -ForegroundColor Cyan

if ($Scenario -eq "all" -or $Scenario -eq "account-health") {
    $vpcId = aws cloudformation describe-stacks --stack-name $stackA --query "Stacks[0].Outputs[?OutputKey=='VpcId'].OutputValue" --output text --no-cli-pager
    $inst1 = aws cloudformation describe-stacks --stack-name $stackA --query "Stacks[0].Outputs[?OutputKey=='Instance1Id'].OutputValue" --output text --no-cli-pager
    Write-Host "  VPC:          $vpcId" -ForegroundColor Cyan
    Write-Host "  EC2 Instance: $inst1" -ForegroundColor Cyan
}

if ($Scenario -eq "all" -or $Scenario -eq "connectivity") {
    $ec2Id = aws cloudformation describe-stacks --stack-name $stackC --query "Stacks[0].Outputs[?OutputKey=='AppInstanceId'].OutputValue" --output text --no-cli-pager
    $eniId = aws cloudformation describe-stacks --stack-name $stackC --query "Stacks[0].Outputs[?OutputKey=='AppInstanceEniId'].OutputValue" --output text --no-cli-pager
    Write-Host "  App EC2:      $ec2Id" -ForegroundColor Cyan
    Write-Host "  App ENI:      $eniId (for Network Agent capture)" -ForegroundColor Cyan
}

if ($script:caseIds.Count -gt 0) {
    Write-Host ""
    Write-Host "  Support Cases:" -ForegroundColor Yellow
    foreach ($id in $script:caseIds) {
        Write-Host "    $id" -ForegroundColor Cyan
    }
    Write-Host ""
    Write-Host "  Try in G.O.A.T.:" -ForegroundColor Yellow
    Write-Host "    `"Help me troubleshoot case $($script:caseIds[-1])`"" -ForegroundColor Gray
}

Write-Host ""
Write-Host "  Suggested Demo Queries:" -ForegroundColor Yellow
Write-Host '    "Give me a complete health check of my AWS account"' -ForegroundColor Gray
Write-Host '    "We had application errors on April 1 - was there an AWS issue?"' -ForegroundColor Gray
Write-Host '    "My EC2 instance cannot connect to ECR over HTTPS"' -ForegroundColor Gray
Write-Host ""
Write-Host "  Cleanup:" -ForegroundColor Yellow
Write-Host '    .\cleanup-scenarios.ps1    (PowerShell)' -ForegroundColor Gray
Write-Host '    ./cleanup-scenarios.sh     (Bash)' -ForegroundColor Gray
Write-Host ""
