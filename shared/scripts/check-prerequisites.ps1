# GenAI Ops Demo Library - Shared Prerequisites Check
# This script validates common requirements across all demos

param(
    [string]$RequiredService = "",
    [string]$MinAwsCliVersion = "2.31.13",
    [string]$MinPythonVersion = "",
    [string]$MinNodeVersion = "",
    [switch]$SkipServiceCheck = $false,
    [switch]$RequireCDK = $false
)

Write-Host "=== GenAI Ops Demo Prerequisites Check (Shared Script) ===" -ForegroundColor Cyan

# Check Python version (if required)
if (-not [string]::IsNullOrEmpty($MinPythonVersion)) {
    Write-Host "`nChecking Python version..." -ForegroundColor Yellow
    $pythonVersion = python --version 2>&1
    if ($pythonVersion -match "Python (\d+)\.(\d+)") {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        $minParts = $MinPythonVersion.Split('.')
        $minMajor = [int]$minParts[0]
        $minMinor = [int]$minParts[1]
        if ($major -gt $minMajor -or ($major -eq $minMajor -and $minor -ge $minMinor)) {
            Write-Host "      OK: Python $major.$minor (required: $MinPythonVersion+)" -ForegroundColor Green
        } else {
            Write-Host "      ERROR: Python $MinPythonVersion+ required (found $major.$minor)" -ForegroundColor Red
            Write-Host "      Install from: https://python.org" -ForegroundColor Cyan
            exit 1
        }
    } else {
        Write-Host "      ERROR: Python not found. Install from https://python.org" -ForegroundColor Red
        exit 1
    }
}

# Check Node.js version (if required for CDK)
if ($RequireCDK -or -not [string]::IsNullOrEmpty($MinNodeVersion)) {
    $nodeMinVersion = if ([string]::IsNullOrEmpty($MinNodeVersion)) { "20" } else { $MinNodeVersion }
    Write-Host "`nChecking Node.js version..." -ForegroundColor Yellow
    $nodeVersion = node --version 2>&1
    if ($nodeVersion -match "v(\d+)") {
        $major = [int]$Matches[1]
        if ($major -ge [int]$nodeMinVersion) {
            Write-Host "      OK: Node.js v$major (required: v$nodeMinVersion+)" -ForegroundColor Green
        } else {
            Write-Host "      ERROR: Node.js v$nodeMinVersion+ required (found v$major)" -ForegroundColor Red
            Write-Host "      Install from: https://nodejs.org" -ForegroundColor Cyan
            exit 1
        }
    } else {
        Write-Host "      ERROR: Node.js not found. Install from https://nodejs.org" -ForegroundColor Red
        exit 1
    }
}

# Verify AWS credentials
Write-Host "`nVerifying AWS credentials..." -ForegroundColor Yellow
Write-Host "      (Checking AWS CLI configuration and validating access)" -ForegroundColor Gray

$callerIdentity = aws sts get-caller-identity 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "AWS credentials are not configured or have expired" -ForegroundColor Red
    Write-Host "`nPlease configure AWS credentials using one of these methods:" -ForegroundColor Yellow
    Write-Host "  1. Run: aws configure" -ForegroundColor Cyan
    Write-Host "  2. Set environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY" -ForegroundColor Cyan
    Write-Host "  3. Use AWS SSO: aws sso login" -ForegroundColor Cyan
    exit 1
}

$accountId = ($callerIdentity | ConvertFrom-Json).Account
$arn = ($callerIdentity | ConvertFrom-Json).Arn
Write-Host "      Authenticated as: $arn" -ForegroundColor Green
Write-Host "      AWS Account: $accountId" -ForegroundColor Green

# Check AWS CLI version
Write-Host "`nChecking AWS CLI version..." -ForegroundColor Yellow
$awsVersion = aws --version 2>&1
$versionMatch = $awsVersion -match 'aws-cli/(\d+)\.(\d+)\.(\d+)'
if ($versionMatch) {
    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    $patch = [int]$Matches[3]
    Write-Host "      Current version: aws-cli/$major.$minor.$patch" -ForegroundColor Gray
    $minVersionParts = $MinAwsCliVersion.Split('.')
    $minMajor = [int]$minVersionParts[0]
    $minMinor = [int]$minVersionParts[1]
    $minPatch = [int]$minVersionParts[2]
    $isVersionValid = ($major -gt $minMajor) -or ($major -eq $minMajor -and $minor -gt $minMinor) -or ($major -eq $minMajor -and $minor -eq $minMinor -and $patch -ge $minPatch)
    if (-not $isVersionValid) {
        Write-Host "      ERROR: AWS CLI version $MinAwsCliVersion or later is required" -ForegroundColor Red
        Write-Host "      Your current version: aws-cli/$major.$minor.$patch" -ForegroundColor Yellow
        Write-Host "      Required version: aws-cli/$MinAwsCliVersion or later" -ForegroundColor Yellow
        Write-Host "      Please upgrade: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html" -ForegroundColor Cyan
        exit 1
    }
    Write-Host "      OK: AWS CLI version is compatible" -ForegroundColor Green
} else {
    Write-Host "      WARN: Could not parse AWS CLI version, continuing anyway..." -ForegroundColor Yellow
}

# Check AWS region configuration
Write-Host "`nChecking AWS region configuration..." -ForegroundColor Yellow

$currentRegion = $env:AWS_DEFAULT_REGION
if ([string]::IsNullOrEmpty($currentRegion)) {
    $currentRegion = aws configure get region 2>$null
}

if ([string]::IsNullOrEmpty($currentRegion)) {
    Write-Host "      ERROR: No AWS region configured" -ForegroundColor Red
    Write-Host "      Please configure your AWS region:" -ForegroundColor Yellow
    Write-Host "        aws configure set region YOUR-REGION" -ForegroundColor Cyan
    exit 1
}

Write-Host "      OK: Region configured: $currentRegion" -ForegroundColor Green

# Check specific AWS service availability (if specified)
if (-not $SkipServiceCheck -and -not [string]::IsNullOrEmpty($RequiredService)) {
    Write-Host "`nChecking $RequiredService availability in $currentRegion..." -ForegroundColor Yellow
    switch ($RequiredService.ToLower()) {
        "bedrock" {
            $null = aws bedrock list-foundation-models --region $currentRegion --max-results 1 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-Host "      ERROR: Amazon Bedrock is not available in region: $currentRegion" -ForegroundColor Red
                Write-Host "      https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-regions.html" -ForegroundColor Gray
                exit 1
            }
            Write-Host "      OK: Amazon Bedrock is available in $currentRegion" -ForegroundColor Green
        }
        "agentcore" {
            $null = aws bedrock-agentcore-control list-agent-runtimes --region $currentRegion --max-results 1 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-Host "      ERROR: Amazon Bedrock AgentCore is not available in region: $currentRegion" -ForegroundColor Red
                Write-Host "      https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-regions.html" -ForegroundColor Gray
                exit 1
            }
            Write-Host "      OK: Amazon Bedrock AgentCore is available in $currentRegion" -ForegroundColor Green
        }
        "agentcore-browser" {
            $null = aws bedrock-agentcore-control list-browsers --region $currentRegion 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-Host "      ERROR: AgentCore Browser Tool is not available in region: $currentRegion" -ForegroundColor Red
                Write-Host "      https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-building-agents.html" -ForegroundColor Gray
                exit 1
            }
            Write-Host "      OK: AgentCore Browser Tool is available in $currentRegion" -ForegroundColor Green
        }
        "nova-act" {
            $null = aws nova-act list-workflow-definitions --region $currentRegion 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-Host "      ERROR: Amazon Nova Act is not available in region: $currentRegion" -ForegroundColor Red
                Write-Host "      https://aws.amazon.com/nova/act/" -ForegroundColor Gray
                exit 1
            }
            Write-Host "      OK: Amazon Nova Act is available in $currentRegion" -ForegroundColor Green
        }
        "transform" {
            Write-Host "      INFO: AWS Transform service check (informational)" -ForegroundColor Cyan
            Write-Host "      AWS Transform must be available in $currentRegion" -ForegroundColor Gray
            Write-Host "      https://docs.aws.amazon.com/transform/latest/userguide/regions.html" -ForegroundColor Gray
        }
        default {
            Write-Host "      WARN: Unknown service '$RequiredService', skipping service check..." -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "`nSkipping service availability check..." -ForegroundColor Yellow
}

Write-Host "`nAll prerequisites validated successfully." -ForegroundColor Green
Write-Host "Ready to proceed with demo deployment." -ForegroundColor Cyan

# Export variables for use by calling script
$global:AWS_ACCOUNT_ID = $accountId
$global:AWS_REGION = $currentRegion
$global:AWS_ARN = $arn
