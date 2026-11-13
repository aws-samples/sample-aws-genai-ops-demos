# Well-Architected Agent Review - deployment script (PowerShell)
#
# Deploys one of the two target workloads that the Well-Architected Agent
# analyzes, plus the agent profile that reviews it. Uses the GenAI Ops Demo
# Library shared scripts, so it must run from <pillar>/<demo>/ inside the library
# checkout.
#
# Variants:
#   single (default)  deliberately non-optimized single-region workload, one stack
#   multi             active/active workload in two Regions with documented gaps,
#                     three stacks (primary, secondary, dashboard). The secondary
#                     Region is derived from the primary one (us-east-1 -> us-west-2,
#                     us-east-2 -> us-west-2, us-west-2 -> us-east-1).
# The deployment Region (primary Region for multi) is the one resolved by the
# shared prerequisites check and must be us-east-1, us-east-2 or us-west-2.
#
# Usage:
#   .\deploy-all.ps1                                   # single, admin CIDR = your public IP /32
#   .\deploy-all.ps1 -AdminCidr "<YOUR_IP>/32"
#   .\deploy-all.ps1 -Variant multi
#   .\deploy-all.ps1 -Variant multi -Domain app.example.com -Zone example.com   # optional HTTPS
#   .\deploy-all.ps1 [-Variant multi] -Destroy         # tear everything down
param(
    [ValidateSet("single", "multi")]
    [string]$Variant = "single",
    # Trusted admin CIDR allowed to reach the workload. When omitted, the caller's
    # public IPv4 is detected and used as a /32.
    [string]$AdminCidr = "",
    [string]$Domain = "",
    [string]$Zone = "",
    [switch]$Destroy
)

$ErrorActionPreference = "Stop"

if ($Variant -eq "single" -and (-not [string]::IsNullOrEmpty($Domain) -or -not [string]::IsNullOrEmpty($Zone))) {
    Write-Host "ERROR: -Domain / -Zone apply to the multi variant only" -ForegroundColor Red
    exit 1
}
if ([string]::IsNullOrEmpty($Domain) -ne [string]::IsNullOrEmpty($Zone)) {
    Write-Host "ERROR: -Domain and -Zone must be passed together." -ForegroundColor Red
    exit 1
}

$sharedScripts = Join-Path $PSScriptRoot "..\..\shared\scripts"
if (-not (Test-Path (Join-Path $sharedScripts "check-prerequisites.ps1"))) {
    Write-Host "ERROR: shared scripts not found at $sharedScripts" -ForegroundColor Red
    Write-Host "       Run this script from <pillar>/<demo>/ inside the GenAI Ops Demo Library." -ForegroundColor Yellow
    exit 1
}

Push-Location $PSScriptRoot
try {
    # Prerequisites: AWS CLI (2.31.13+ for the Well-Architected Agent API),
    # credentials, region, Node.js 20+ and CDK.
    & (Join-Path $sharedScripts "check-prerequisites.ps1") -RequireCDK -MinAwsCliVersion "2.31.13"
    if ($LASTEXITCODE -ne 0) { exit 1 }
    $region = $global:AWS_REGION

    $secondaryByPrimary = @{ "us-east-1" = "us-west-2"; "us-east-2" = "us-west-2"; "us-west-2" = "us-east-1" }
    if (-not $secondaryByPrimary.ContainsKey($region)) {
        Write-Host "ERROR: the Well-Architected Agent profile can only be hosted in us-east-1, us-east-2 or us-west-2 (resolved: $region)." -ForegroundColor Red
        Write-Host "       Set AWS_REGION and AWS_DEFAULT_REGION to one of them and retry." -ForegroundColor Yellow
        exit 1
    }

    # The CDK app reads the variant from here; it is also the only way to reach
    # `cdk destroy`, which gets no --context from the shared deploy-cdk script.
    $env:WA_DEMO_VARIANT = $Variant

    function Get-StackOutput([string]$stack, [string]$stackRegion, [string]$key) {
        aws cloudformation describe-stacks --stack-name $stack --region $stackRegion `
            --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" --output text --no-cli-pager
    }

    # Run the shared deploy-cdk script with AWS_REGION pinned to one Region. It
    # bootstraps that Region (unless -SkipBootstrap) and deploys/destroys one stack.
    function Invoke-DeployIn([string]$deployRegion, [hashtable]$cdkArgs) {
        $env:AWS_REGION = $deployRegion
        $env:AWS_DEFAULT_REGION = $deployRegion
        & (Join-Path $sharedScripts "deploy-cdk.ps1") -CdkDirectory "infrastructure/cdk" @cdkArgs
        if ($LASTEXITCODE -ne 0) { exit 1 }
    }

    # Admin CIDR is only needed to deploy (destroy synthesizes with the no-access fallback).
    function Resolve-AdminCidr {
        if ([string]::IsNullOrEmpty($script:AdminCidr)) {
            Write-Host "Detecting your public IP for the admin CIDR..." -ForegroundColor Yellow
            try {
                $detectedIp = (Invoke-RestMethod -Uri "https://checkip.amazonaws.com" -TimeoutSec 10).Trim()
            } catch {
                $detectedIp = ""
            }
            # Fail closed: never deploy with an unexpected value as the admin CIDR.
            if ($detectedIp -notmatch '^(\d{1,3}\.){3}\d{1,3}$') {
                Write-Host "ERROR: could not detect your public IPv4. Pass it explicitly: -AdminCidr `"<YOUR_IP>/32`"" -ForegroundColor Red
                exit 1
            }
            $script:AdminCidr = "$detectedIp/32"
            Write-Host "      Admin CIDR: $script:AdminCidr" -ForegroundColor Gray
        }
        # Basic format check on the admin CIDR before it reaches CDK context.
        if ($script:AdminCidr -notmatch '^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$') {
            Write-Host "ERROR: -AdminCidr must be an IPv4 CIDR, e.g. <YOUR_IP>/32" -ForegroundColor Red
            exit 1
        }
    }

    # =======================================================================
    # Variant: single
    # =======================================================================
    if ($Variant -eq "single") {
        # Pin both variables so the CDK app (getRegion) and deploy-cdk resolve the
        # same region, and the region-suffixed stack name matches what gets deployed.
        $env:AWS_REGION = $region
        $env:AWS_DEFAULT_REGION = $region
        $stackName = "WellArchitectedAgentReviewStack-$region"

        if ($Destroy) {
            Invoke-DeployIn $region @{ StackName = $stackName; SkipBootstrap = $true; DestroyStack = $true }
            Write-Host ""
            Write-Host "  Cleanup Complete: $stackName ($region)" -ForegroundColor Green
            exit 0
        }

        Resolve-AdminCidr
        Invoke-DeployIn $region @{ StackName = $stackName; CdkContext = @("adminCidr=$AdminCidr") }

        $instanceIp = Get-StackOutput $stackName $region "InstancePublicIp"
        $dbEndpoint = Get-StackOutput $stackName $region "DatabaseEndpoint"
        $bucketName = Get-StackOutput $stackName $region "AssetBucketName"
        $profileArn = Get-StackOutput $stackName $region "AgentProfileArn"

        Write-Host ""
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  Deployment Complete! (variant: single)" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  Stack:          $stackName" -ForegroundColor Cyan
        Write-Host "  Region:         $region" -ForegroundColor Cyan
        Write-Host "  Admin CIDR:     $AdminCidr" -ForegroundColor Cyan
        Write-Host "  Instance IP:    $instanceIp" -ForegroundColor Cyan
        Write-Host "  DB endpoint:    $dbEndpoint" -ForegroundColor Cyan
        Write-Host "  Asset bucket:   $bucketName" -ForegroundColor Cyan
        Write-Host "  Agent profile:  $profileArn" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  Next: recommendations appear in the AWS WA Agent console within 24-48 hours." -ForegroundColor Yellow
        Write-Host "  Check eligibility: aws wellarchitected get-agent-profile --profile-arn $profileArn --region $region" -ForegroundColor Yellow
        Write-Host "  Cleanup: .\deploy-all.ps1 -Destroy" -ForegroundColor Yellow
        exit 0
    }

    # =======================================================================
    # Variant: multi
    # =======================================================================
    $primaryRegion   = $region
    $secondaryRegion = $secondaryByPrimary[$primaryRegion]
    $primaryStack    = "WellArchitectedAgentReviewMultiPrimary-$primaryRegion"
    $secondaryStack  = "WellArchitectedAgentReviewMultiSecondary-$secondaryRegion"
    $dashboardStack  = "WellArchitectedAgentReviewMultiDashboard-$primaryRegion"

    # The CDK app reads the primary Region from here, so every deploy-cdk call below
    # synthesizes the same stacks even while AWS_REGION points at the secondary Region.
    $env:WA_DEMO_PRIMARY_REGION = $primaryRegion

    if ($Destroy) {
        # Reverse order: dashboard, secondary, primary (the primary deletes the
        # global table and its replica, and the agent profile).
        Write-Host "Destroying $dashboardStack, $secondaryStack and $primaryStack..." -ForegroundColor Yellow
        Invoke-DeployIn $primaryRegion   @{ StackName = $dashboardStack; SkipBootstrap = $true; DestroyStack = $true }
        Invoke-DeployIn $secondaryRegion @{ StackName = $secondaryStack; SkipBootstrap = $true; DestroyStack = $true }
        Invoke-DeployIn $primaryRegion   @{ StackName = $primaryStack;   SkipBootstrap = $true; DestroyStack = $true }
        Write-Host ""
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  Cleanup Complete! (variant: multi)" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  Destroyed:  $dashboardStack, $secondaryStack, $primaryStack" -ForegroundColor Cyan
        Write-Host "  Regions:    $primaryRegion, $secondaryRegion" -ForegroundColor Cyan
        exit 0
    }

    Resolve-AdminCidr
    $context = @("adminCidr=$AdminCidr")
    if (-not [string]::IsNullOrEmpty($Domain)) {
        $context += @("appDomainName=$Domain", "route53ZoneName=$Zone")
    }

    # 1. Primary: bootstraps the primary Region, deploys the region stack, the
    #    global table and the agent profile.
    Invoke-DeployIn $primaryRegion @{ StackName = $primaryStack; CdkContext = $context }

    # 2. Secondary: bootstraps the secondary Region and deploys its region stack.
    Invoke-DeployIn $secondaryRegion @{ StackName = $secondaryStack; CdkContext = $context }

    # 3. Dashboard: takes the ALB and target group dimensions of both Regions as context.
    $dashboardContext = $context + @(
        "primaryAlbFullName=$(Get-StackOutput $primaryStack $primaryRegion 'AlbFullName')",
        "primaryTgFullName=$(Get-StackOutput $primaryStack $primaryRegion 'TargetGroupFullName')",
        "secondaryAlbFullName=$(Get-StackOutput $secondaryStack $secondaryRegion 'AlbFullName')",
        "secondaryTgFullName=$(Get-StackOutput $secondaryStack $secondaryRegion 'TargetGroupFullName')"
    )
    Invoke-DeployIn $primaryRegion @{ StackName = $dashboardStack; SkipBootstrap = $true; CdkContext = $dashboardContext }

    $primaryAlb   = Get-StackOutput $primaryStack $primaryRegion "AlbDnsName"
    $secondaryAlb = Get-StackOutput $secondaryStack $secondaryRegion "AlbDnsName"
    $tableName    = Get-StackOutput $primaryStack $primaryRegion "DynamoDbTableName"
    $profileArn   = Get-StackOutput $primaryStack $primaryRegion "AgentProfileArn"
    $dashboardUrl = Get-StackOutput $dashboardStack $primaryRegion "DashboardUrl"

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Deployment Complete! (variant: multi)" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Primary:        $primaryStack ($primaryRegion)" -ForegroundColor Cyan
    Write-Host "  Secondary:      $secondaryStack ($secondaryRegion)" -ForegroundColor Cyan
    Write-Host "  Admin CIDR:     $AdminCidr" -ForegroundColor Cyan
    Write-Host "  Primary ALB:    $primaryAlb" -ForegroundColor Cyan
    Write-Host "  Secondary ALB:  $secondaryAlb" -ForegroundColor Cyan
    Write-Host "  DynamoDB table: $tableName (global, both regions)" -ForegroundColor Cyan
    Write-Host "  Agent profile:  $profileArn" -ForegroundColor Cyan
    Write-Host "  Dashboard:      $dashboardUrl" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Next: recommendations appear in the AWS WA Agent console within 24-48 hours." -ForegroundColor Yellow
    Write-Host "  Check eligibility: aws wellarchitected get-agent-profile --profile-arn $profileArn --region $primaryRegion" -ForegroundColor Yellow
    Write-Host "  Cleanup: .\deploy-all.ps1 -Variant multi -Destroy" -ForegroundColor Yellow
}
finally {
    Pop-Location
}
