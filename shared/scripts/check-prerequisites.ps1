# GenAI Ops Demo Library - Shared Prerequisites Check
# This script validates common requirements across all demos

param(
    [string]$RequiredService = "",
    [string]$MinAwsCliVersion = "2.31.13",
    [string]$MinPythonVersion = "",
    [string]$MinNodeVersion = "",
    [switch]$SkipServiceCheck = $false,
    [switch]$RequireCDK = $false,
    [switch]$RequireKubectl = $false
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

# Check kubectl (if required for EKS demos)
if ($RequireKubectl) {
    Write-Host "`nChecking kubectl..." -ForegroundColor Yellow
    try {
        $null = kubectl version --client 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "      OK: kubectl installed" -ForegroundColor Green
        } else {
            throw "kubectl not working"
        }
    } catch {
        Write-Host "      ERROR: kubectl not found. Install from https://kubernetes.io/docs/tasks/tools/" -ForegroundColor Red
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

# Precedence matches shared/utils/aws-utils.* and the repo convention:
#   AWS_DEFAULT_REGION -> AWS_REGION -> aws configure get region
# (No us-east-1 fallback here: an unset region is a hard error so the user never
# deploys somewhere they did not choose.)
$currentRegion = $env:AWS_DEFAULT_REGION
if ([string]::IsNullOrEmpty($currentRegion)) {
    $currentRegion = $env:AWS_REGION
}
if ([string]::IsNullOrEmpty($currentRegion)) {
    $currentRegion = aws configure get region 2>$null
}

if ([string]::IsNullOrEmpty($currentRegion)) {
    Write-Host "      ERROR: No AWS region configured" -ForegroundColor Red
    Write-Host "      Please configure your AWS region using one of:" -ForegroundColor Yellow
    Write-Host '        $env:AWS_REGION = "<your-region>"' -ForegroundColor Cyan
    Write-Host '        $env:AWS_DEFAULT_REGION = "<your-region>"' -ForegroundColor Cyan
    Write-Host "        aws configure set region <your-region>" -ForegroundColor Cyan
    exit 1
}

Write-Host "      OK: Region configured: $currentRegion" -ForegroundColor Green

# ─── AWS DevOps Agent region resolution ──────────────────────────────────────
# An Agent Space is a regional resource and AWS DevOps Agent is available in a
# subset of AWS Regions. Resolution is opt-in and deliberately simple:
#
#   1. DEVOPS_AGENT_REGION — explicit override, wins outright
#   2. the resolved deploy region — same-region default
#
# Same-region is the default because an Agent Space discovers and monitors
# resources across ALL Regions of an associated account. Splitting the two is
# therefore only needed when the deploy Region cannot host an Agent Space, or
# for data-residency / console-availability reasons.
#
# No hardcoded Region list lives here: the "devops-agent" service check below
# probes the live API instead, so a Region stays usable the moment the service
# reaches it. See shared/README.md ("AWS DevOps Agent Region") for the full
# convention and its caveats.
$devOpsAgentRegion = $env:DEVOPS_AGENT_REGION
if ([string]::IsNullOrEmpty($devOpsAgentRegion)) {
    $devOpsAgentRegion = $currentRegion
} elseif ($devOpsAgentRegion -ne $currentRegion) {
    Write-Host "      INFO: DevOps Agent region overridden: $devOpsAgentRegion (deploy region: $currentRegion)" -ForegroundColor Cyan
}

# Check specific AWS service availability (if specified)
if (-not $SkipServiceCheck -and -not [string]::IsNullOrEmpty($RequiredService)) {
    # DevOps Agent is probed in the Agent Space region (which may differ from the
    # deploy region); every other service is probed in the deploy region.
    $serviceCheckRegion = if ($RequiredService.ToLower() -eq "devops-agent") { $devOpsAgentRegion } else { $currentRegion }
    Write-Host "`nChecking $RequiredService availability in $serviceCheckRegion..." -ForegroundColor Yellow
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
        "devops-agent" {
            # Probe the actual AWS DevOps Agent service (aidevops) in the Agent
            # Space region. A region with no reachable aidevops endpoint fails at
            # endpoint resolution and we stop here. Probing the live service avoids
            # a hardcoded region list, which rots and would reject regions where the
            # service is already reachable ahead of the docs. (Different service
            # from Bedrock AgentCore, which the "agentcore" case probes.)
            $null = aws devops-agent list-agent-spaces --region $devOpsAgentRegion --no-cli-pager 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-Host "      ERROR: AWS DevOps Agent is not available in region: $devOpsAgentRegion" -ForegroundColor Red
                Write-Host "" -ForegroundColor Red
                Write-Host "      Pin the Agent Space to a supported Region and re-run:" -ForegroundColor Yellow
                Write-Host '        $env:DEVOPS_AGENT_REGION = "<supported-region>"' -ForegroundColor Cyan
                Write-Host "" -ForegroundColor Yellow
                Write-Host "      An Agent Space monitors resources in ALL Regions of an associated" -ForegroundColor Gray
                Write-Host "      account, so it does not need to live in your deploy Region." -ForegroundColor Gray
                Write-Host "      https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-supported-regions.html" -ForegroundColor Gray
                exit 1
            }
            if ($devOpsAgentRegion -ne $currentRegion) {
                Write-Host "      OK: AWS DevOps Agent is available in $devOpsAgentRegion (Agent Space region)" -ForegroundColor Green
            } else {
                Write-Host "      OK: AWS DevOps Agent is available in $devOpsAgentRegion" -ForegroundColor Green
            }
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
# Agent Space region for AWS DevOps Agent demos — equals $global:AWS_REGION unless
# DEVOPS_AGENT_REGION was set by the caller.
#
# Published ONLY when the caller requested the devops-agent check, mirroring the
# Bash version. Demos that manage $env:DEVOPS_AGENT_REGION themselves (applying their
# own default) must not have it decided for them here.
if ($RequiredService.ToLower() -eq "devops-agent") {
    $global:DEVOPS_AGENT_REGION = $devOpsAgentRegion
}
