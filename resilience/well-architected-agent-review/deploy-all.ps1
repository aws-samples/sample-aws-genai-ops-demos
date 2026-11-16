# Well-Architected Agent Review - deployment script (PowerShell)
#
# Deploys the demo's three building blocks. Uses the GenAI Ops Demo Library
# shared scripts, so it must run from <pillar>/<demo>/ inside the library checkout.
#
#   profile           the shared Well-Architected Agent profile (roles, profile,
#                     goals): stack WellArchitectedAgentReviewProfile-<agent-region>.
#                     One per account and agent Region. Deployed (or updated)
#                     automatically before every application.
#   single (default)  deliberately non-optimized single-region application, and its
#                     context stack that registers it in the profile
#   multi             active/active application in two Regions with documented gaps
#                     (primary, secondary, dashboard), and its context stack. The
#                     secondary Region comes from -SecondaryRegion; otherwise the
#                     script picks one from the account's enabled Regions in the
#                     primary's geography (alphabetical), and later runs reuse the
#                     deployed one.
#
# Regions: applications deploy in the Region resolved by the shared prerequisites
# check (AWS_REGION / AWS_DEFAULT_REGION). The profile and every context stack live
# in WA_AGENT_REGION, which defaults to the same Region. A profile can only be
# hosted in a few Regions; when yours is not one of them the check stops and tells
# you to set WA_AGENT_REGION. The profile scans every enabled Region of the account.
#
# Destroy removes one application (its context stack, then its workload). The
# profile stays for the other applications; remove it with -Variant profile -Destroy
# once no application is left (the script and CloudFormation refuse earlier).
#
# Usage:
#   .\deploy-all.ps1                                   # single, admin CIDR = your public IP /32
#   .\deploy-all.ps1 -AdminCidr "<YOUR_IP>/32"
#   .\deploy-all.ps1 -Variant multi
#   .\deploy-all.ps1 -Variant multi -SecondaryRegion us-west-2
#   .\deploy-all.ps1 -Variant multi -Domain app.example.com -Zone example.com   # optional HTTPS
#   .\deploy-all.ps1 [-Variant multi] -Destroy         # remove one application
#   .\deploy-all.ps1 -Variant profile [-Destroy]       # deploy or remove the profile alone
#   $env:WA_AGENT_REGION = "us-east-1"                 # host the profile outside the workload Region
param(
    [ValidateSet("single", "multi", "profile")]
    [string]$Variant = "single",
    # Trusted admin CIDR allowed to reach the workload. When omitted, the caller's
    # public IPv4 is detected and used as a /32.
    [string]$AdminCidr = "",
    [string]$Domain = "",
    [string]$Zone = "",
    # multi only. Default: the deployed one, else chosen from the account's enabled Regions.
    [string]$SecondaryRegion = "",
    [switch]$Destroy
)

$ErrorActionPreference = "Stop"

if ($Variant -ne "multi" -and (-not [string]::IsNullOrEmpty($Domain) -or -not [string]::IsNullOrEmpty($Zone))) {
    Write-Host "ERROR: -Domain / -Zone apply to the multi variant only" -ForegroundColor Red
    exit 1
}
if ($Variant -ne "multi" -and -not [string]::IsNullOrEmpty($SecondaryRegion)) {
    Write-Host "ERROR: -SecondaryRegion applies to the multi variant only" -ForegroundColor Red
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

# This script runs in the caller's session, so every environment variable it sets
# would outlive it. In particular a leftover WA_AGENT_REGION or AWS_REGION would
# silently move the next run. Save them here, restore them in `finally`.
$envKeys = @("AWS_REGION", "AWS_DEFAULT_REGION", "WA_AGENT_REGION", "WA_AGENT_SCAN_REGIONS",
             "WA_DEMO_VARIANT", "WA_DEMO_PRIMARY_REGION", "WA_DEMO_SECONDARY_REGION")
$savedEnv = @{}
foreach ($k in $envKeys) { $savedEnv[$k] = [Environment]::GetEnvironmentVariable($k, "Process") }

Push-Location $PSScriptRoot
try {
    # Prerequisites: AWS CLI (2.31.13+ for the Well-Architected Agent API),
    # credentials, region, Node.js 20+ and CDK, and the Region that hosts the agent
    # profile (WA_AGENT_REGION, default: the deploy Region). The shared check holds
    # the list of hosting Regions and stops when the profile Region is not one.
    & (Join-Path $sharedScripts "check-prerequisites.ps1") -RequiredService "wellarchitected-agent" -RequireCDK -MinAwsCliVersion "2.31.13"
    if ($LASTEXITCODE -ne 0) { exit 1 }
    $region = $global:AWS_REGION
    $agentRegion = $global:WA_AGENT_REGION
    $profileStack = "WellArchitectedAgentReviewProfile-$agentRegion"
    $profileExport = "WellArchitectedAgentReviewProfileArn"   # PROFILE_ARN_EXPORT in lib/wa-agent.ts

    function Get-StackOutput([string]$stack, [string]$stackRegion, [string]$key) {
        aws cloudformation describe-stacks --stack-name $stack --region $stackRegion `
            --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" --output text --no-cli-pager
    }

    # Same, but a missing stack or output is a normal case: returns "". Under "Stop",
    # Windows PowerShell 5.1 turns redirected native stderr into a terminating
    # error, so the call runs with "Continue".
    function Get-StackOutputOrEmpty([string]$stack, [string]$stackRegion, [string]$key) {
        $value = & { $ErrorActionPreference = 'Continue'
            aws cloudformation describe-stacks --stack-name $stack --region $stackRegion `
                --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" --output text --no-cli-pager 2>$null }
        if ($LASTEXITCODE -ne 0 -or -not $value -or $value -eq "None") { return "" }
        return ([string]$value).Trim()
    }

    # Regions the shared profile scans: every enabled Region of the account
    # (ec2 describe-regions, read-only), sorted so the template does not change
    # between runs. Each application context narrows the scope to its own Regions.
    function Get-ScanRegions {
        $names = aws ec2 describe-regions --region $agentRegion --query "Regions[].RegionName" --output text --no-cli-pager
        if ($LASTEXITCODE -ne 0) { return "" }
        $list = [string[]]@((([string]($names -join ' ')).Trim() -split '\s+') | Where-Object { $_ })
        [Array]::Sort($list, [StringComparer]::Ordinal)
        return ($list -join ',')
    }

    # Stacks that still import the profile ARN: the registered applications.
    function Get-ProfileImporters {
        $value = & { $ErrorActionPreference = 'Continue'
            aws cloudformation list-imports --export-name $profileExport --region $agentRegion `
                --query "Imports" --output text --no-cli-pager 2>$null }
        if ($LASTEXITCODE -ne 0 -or -not $value -or $value -eq "None") { return @() }
        return @((([string]($value -join ' ')).Trim() -split '\s+') | Where-Object { $_ })
    }

    # Geography of a Region code: the code without its last two segments
    # (us-east-1 -> us, ap-southeast-1 -> ap, us-gov-west-1 -> us-gov). The multi
    # variant keeps both Regions in one geography (data residency, latency,
    # replication cost). Same rule as region_geography in the Bash twin.
    function Get-RegionGeography([string]$regionCode) {
        return ($regionCode -replace '-[a-z]+-\d+$', '')
    }

    # Default secondary Region of the multi variant, with no Region list in the demo:
    # this account's enabled Regions (ec2 describe-regions, read-only) in the
    # primary's geography, primary excluded, sorted alphabetically (ordinal, as
    # `LC_ALL=C sort` in the Bash twin), Regions without opt-in first. "" when the
    # geography has no other enabled Region. Deterministic for a given set of
    # enabled Regions; redeploy and destroy reuse the deployed value instead.
    function Select-SecondaryRegion([string]$primary) {
        $rows = aws ec2 describe-regions --region $primary `
            --query "Regions[].[RegionName,OptInStatus]" --output text --no-cli-pager
        if ($LASTEXITCODE -ne 0) { return "" }
        $optIn = @{}
        foreach ($row in @($rows)) {
            $parts = ([string]$row).Trim() -split '\s+'
            if ($parts[0] -and $parts[0] -ne $primary) { $optIn[$parts[0]] = $parts[1] }
        }
        $names = [string[]]@($optIn.Keys)
        [Array]::Sort($names, [StringComparer]::Ordinal)
        $geo = Get-RegionGeography $primary
        $sameGeo = @($names | Where-Object { (Get-RegionGeography $_) -ceq $geo })
        $first = @($sameGeo | Where-Object { $optIn[$_] -eq "opt-in-not-required" }) + $sameGeo | Select-Object -First 1
        if ($first) { return [string]$first }
        return ""
    }

    # Run the shared deploy-cdk script with AWS_REGION pinned to one Region. It
    # bootstraps that Region (unless -SkipBootstrap) and deploys/destroys one stack.
    function Invoke-DeployIn([string]$deployRegion, [hashtable]$cdkArgs) {
        $env:AWS_REGION = $deployRegion
        $env:AWS_DEFAULT_REGION = $deployRegion
        & (Join-Path $sharedScripts "deploy-cdk.ps1") -CdkDirectory "infrastructure/cdk" @cdkArgs
        if ($LASTEXITCODE -ne 0) { exit 1 }
    }

    # Deploy (or update) the shared profile in the agent Region; bootstraps it.
    function Deploy-Profile {
        Invoke-DeployIn $agentRegion @{ StackName = $profileStack }
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

    # The CDK app reads these from the environment: deploy-cdk passes no --context
    # on destroy, and AWS_REGION moves between Regions below. They keep every
    # deploy-cdk call synthesizing the same stacks.
    $scanRegions = Get-ScanRegions
    if ([string]::IsNullOrEmpty($scanRegions)) {
        Write-Host "ERROR: could not list this account's enabled Regions (ec2:DescribeRegions)." -ForegroundColor Red
        exit 1
    }
    $env:WA_DEMO_VARIANT = $Variant
    $env:WA_DEMO_PRIMARY_REGION = $region
    $env:WA_AGENT_REGION = $agentRegion
    $env:WA_AGENT_SCAN_REGIONS = $scanRegions

    # =======================================================================
    # Variant: profile (the shared profile alone)
    # =======================================================================
    if ($Variant -eq "profile") {
        if ($Destroy) {
            $importers = Get-ProfileImporters
            if ($importers.Count -gt 0) {
                Write-Host "ERROR: applications are still registered in $profileStack ($agentRegion):" -ForegroundColor Red
                foreach ($s in $importers) { Write-Host "         $s" -ForegroundColor Red }
                Write-Host "       Destroy them first (.\deploy-all.ps1 [-Variant multi] -Destroy), then retry." -ForegroundColor Yellow
                exit 1
            }
            Invoke-DeployIn $agentRegion @{ StackName = $profileStack; SkipBootstrap = $true; DestroyStack = $true }
            Write-Host ""
            Write-Host "========================================" -ForegroundColor Green
            Write-Host "  Cleanup Complete! (variant: profile)" -ForegroundColor Green
            Write-Host "========================================" -ForegroundColor Green
            Write-Host "  Destroyed:  $profileStack ($agentRegion)" -ForegroundColor Cyan
            exit 0
        }

        Deploy-Profile
        $profileArn = Get-StackOutput $profileStack $agentRegion "AgentProfileArn"
        Write-Host ""
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  Deployment Complete! (variant: profile)" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  Stack:          $profileStack" -ForegroundColor Cyan
        Write-Host "  Agent region:   $agentRegion" -ForegroundColor Cyan
        Write-Host "  Scans:          $scanRegions" -ForegroundColor Cyan
        Write-Host "  Agent profile:  $profileArn" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  Next: deploy an application (.\deploy-all.ps1 or -Variant multi). A profile with no" -ForegroundColor Yellow
        Write-Host "  application context runs no scheduled recommendations." -ForegroundColor Yellow
        exit 0
    }

    # =======================================================================
    # Variant: single
    # =======================================================================
    if ($Variant -eq "single") {
        $stackName    = "WellArchitectedAgentReviewStack-$region"
        $contextStack = "WellArchitectedAgentReviewSingleContext-$region"

        if ($Destroy) {
            # Context first (releases the profile), then the workload.
            Invoke-DeployIn $agentRegion @{ StackName = $contextStack; SkipBootstrap = $true; DestroyStack = $true }
            Invoke-DeployIn $region @{ StackName = $stackName; SkipBootstrap = $true; DestroyStack = $true }
            Write-Host ""
            Write-Host "========================================" -ForegroundColor Green
            Write-Host "  Cleanup Complete! (variant: single)" -ForegroundColor Green
            Write-Host "========================================" -ForegroundColor Green
            Write-Host "  Destroyed:  $contextStack ($agentRegion)" -ForegroundColor Cyan
            Write-Host "              $stackName ($region)" -ForegroundColor Cyan
            Write-Host "  Kept:       $profileStack ($agentRegion), shared by the applications" -ForegroundColor Cyan
            Write-Host "  Remove it when no application is left: .\deploy-all.ps1 -Variant profile -Destroy" -ForegroundColor Yellow
            exit 0
        }

        Resolve-AdminCidr
        # 1. Shared profile (created or updated), 2. workload, 3. its context.
        Deploy-Profile
        Invoke-DeployIn $region @{ StackName = $stackName; CdkContext = @("adminCidr=$AdminCidr") }
        Invoke-DeployIn $agentRegion @{ StackName = $contextStack; SkipBootstrap = $true }

        $instanceIp = Get-StackOutput $stackName $region "InstancePublicIp"
        $dbEndpoint = Get-StackOutput $stackName $region "DatabaseEndpoint"
        $bucketName = Get-StackOutput $stackName $region "AssetBucketName"
        $profileArn = Get-StackOutput $profileStack $agentRegion "AgentProfileArn"

        Write-Host ""
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  Deployment Complete! (variant: single)" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  Workload:       $stackName ($region)" -ForegroundColor Cyan
        Write-Host "  Context:        $contextStack ($agentRegion)" -ForegroundColor Cyan
        Write-Host "  Profile:        $profileStack ($agentRegion)" -ForegroundColor Cyan
        Write-Host "  Admin CIDR:     $AdminCidr" -ForegroundColor Cyan
        Write-Host "  Instance IP:    $instanceIp" -ForegroundColor Cyan
        Write-Host "  DB endpoint:    $dbEndpoint" -ForegroundColor Cyan
        Write-Host "  Asset bucket:   $bucketName" -ForegroundColor Cyan
        Write-Host "  Agent profile:  $profileArn" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  Next: the first scheduled recommendations usually arrive within a few hours (at most 48 hours)." -ForegroundColor Yellow
        Write-Host "  Check progress: aws wellarchitected list-agent-recommendation-generations --profile-arn $profileArn --region $agentRegion" -ForegroundColor Yellow
        Write-Host "  Check eligibility: aws wellarchitected get-agent-profile --profile-arn $profileArn --region $agentRegion" -ForegroundColor Yellow
        Write-Host "  Cleanup: .\deploy-all.ps1 -Destroy, then .\deploy-all.ps1 -Variant profile -Destroy (same AWS_REGION and WA_AGENT_REGION)" -ForegroundColor Yellow
        exit 0
    }

    # =======================================================================
    # Variant: multi
    # =======================================================================
    $primaryRegion = $region
    $primaryStack  = "WellArchitectedAgentReviewMultiPrimary-$primaryRegion"

    # Secondary Region, in this order:
    #   1. -SecondaryRegion;
    #   2. the one an existing deployment uses (SecondaryRegion output of the primary
    #      stack), so a redeploy or a destroy never moves it;
    #   3. deploy only: chosen from this account's enabled Regions (Select-SecondaryRegion).
    $deployedSecondary = Get-StackOutputOrEmpty $primaryStack $primaryRegion "SecondaryRegion"
    if (-not [string]::IsNullOrEmpty($SecondaryRegion)) {
        if ($deployedSecondary -and $deployedSecondary -ne $SecondaryRegion) {
            Write-Host "ERROR: $primaryStack is already deployed with secondary Region $deployedSecondary, not $SecondaryRegion." -ForegroundColor Red
            Write-Host "       Omit -SecondaryRegion to keep it, or destroy this deployment first." -ForegroundColor Yellow
            exit 1
        }
        $secondaryRegion = $SecondaryRegion
    } elseif ($deployedSecondary) {
        $secondaryRegion = $deployedSecondary
        Write-Host "Secondary Region: $secondaryRegion (from the deployed $primaryStack)" -ForegroundColor Gray
    } elseif ($Destroy) {
        Write-Host "ERROR: $primaryStack was not found in $primaryRegion, so its secondary Region is unknown." -ForegroundColor Red
        Write-Host "       To remove what is left, pass it explicitly: -SecondaryRegion <region>" -ForegroundColor Yellow
        exit 1
    } else {
        $secondaryRegion = Select-SecondaryRegion $primaryRegion
        if ([string]::IsNullOrEmpty($secondaryRegion)) {
            Write-Host "ERROR: this account has no other enabled Region in the $(Get-RegionGeography $primaryRegion) geography of $primaryRegion." -ForegroundColor Red
            Write-Host "       The multi variant needs both Regions in one geography: enable another one, or use the single variant." -ForegroundColor Yellow
            exit 1
        }
        Write-Host "Secondary Region: $secondaryRegion (chosen from this account's enabled Regions; pass -SecondaryRegion for another)" -ForegroundColor Gray
    }
    if ($secondaryRegion -notmatch '^[a-z]{2}(-[a-z]+)+-\d$' -or $secondaryRegion -eq $primaryRegion) {
        Write-Host "ERROR: -SecondaryRegion must be a Region code different from $primaryRegion (got: $secondaryRegion)" -ForegroundColor Red
        exit 1
    }
    # Same geography as the primary. Checked on deploy only: a destroy must still be
    # able to remove whatever is deployed.
    if (-not $Destroy -and (Get-RegionGeography $secondaryRegion) -cne (Get-RegionGeography $primaryRegion)) {
        Write-Host "ERROR: the secondary Region must be in the same geography as $primaryRegion ($(Get-RegionGeography $primaryRegion)-*); got $secondaryRegion." -ForegroundColor Red
        exit 1
    }
    $env:WA_DEMO_SECONDARY_REGION = $secondaryRegion

    $secondaryStack = "WellArchitectedAgentReviewMultiSecondary-$secondaryRegion"
    $dashboardStack = "WellArchitectedAgentReviewMultiDashboard-$primaryRegion"
    $contextStack   = "WellArchitectedAgentReviewMultiContext-$primaryRegion"

    if ($Destroy) {
        # Reverse order: dashboard, context (releases the profile), secondary,
        # primary (the primary deletes the global table and its replica).
        $names = @($dashboardStack, $contextStack, $secondaryStack, $primaryStack)
        Write-Host "Destroying $($names -join ', ')..." -ForegroundColor Yellow
        Invoke-DeployIn $primaryRegion   @{ StackName = $dashboardStack; SkipBootstrap = $true; DestroyStack = $true }
        Invoke-DeployIn $agentRegion     @{ StackName = $contextStack;   SkipBootstrap = $true; DestroyStack = $true }
        Invoke-DeployIn $secondaryRegion @{ StackName = $secondaryStack; SkipBootstrap = $true; DestroyStack = $true }
        Invoke-DeployIn $primaryRegion   @{ StackName = $primaryStack;   SkipBootstrap = $true; DestroyStack = $true }
        Write-Host ""
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  Cleanup Complete! (variant: multi)" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  Destroyed:  $($names -join ', ')" -ForegroundColor Cyan
        Write-Host "  Kept:       $profileStack ($agentRegion), shared by the applications" -ForegroundColor Cyan
        Write-Host "  Remove it when no application is left: .\deploy-all.ps1 -Variant profile -Destroy" -ForegroundColor Yellow
        exit 0
    }

    Resolve-AdminCidr
    $context = @("adminCidr=$AdminCidr")
    if (-not [string]::IsNullOrEmpty($Domain)) {
        $context += @("appDomainName=$Domain", "route53ZoneName=$Zone")
    }

    # 1. Shared profile (created or updated), in the agent Region (bootstraps it).
    Deploy-Profile

    # 2. Primary: bootstraps the primary Region, deploys the region stack and the global table.
    Invoke-DeployIn $primaryRegion @{ StackName = $primaryStack; CdkContext = $context }

    # 3. Secondary: bootstraps the secondary Region and deploys its region stack.
    Invoke-DeployIn $secondaryRegion @{ StackName = $secondaryStack; CdkContext = $context }

    # 4. Context: registers the application in the profile (agent Region, bootstrapped in 1).
    Invoke-DeployIn $agentRegion @{ StackName = $contextStack; SkipBootstrap = $true }

    # 5. Dashboard: takes the ALB and target group dimensions of both Regions as context.
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
    $profileArn   = Get-StackOutput $profileStack $agentRegion "AgentProfileArn"
    $dashboardUrl = Get-StackOutput $dashboardStack $primaryRegion "DashboardUrl"

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Deployment Complete! (variant: multi)" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Primary:        $primaryStack ($primaryRegion)" -ForegroundColor Cyan
    Write-Host "  Secondary:      $secondaryStack ($secondaryRegion)" -ForegroundColor Cyan
    Write-Host "  Context:        $contextStack ($agentRegion)" -ForegroundColor Cyan
    Write-Host "  Profile:        $profileStack ($agentRegion)" -ForegroundColor Cyan
    Write-Host "  Admin CIDR:     $AdminCidr" -ForegroundColor Cyan
    Write-Host "  Primary ALB:    $primaryAlb" -ForegroundColor Cyan
    Write-Host "  Secondary ALB:  $secondaryAlb" -ForegroundColor Cyan
    Write-Host "  DynamoDB table: $tableName (global, both regions)" -ForegroundColor Cyan
    Write-Host "  Agent profile:  $profileArn" -ForegroundColor Cyan
    Write-Host "  Dashboard:      $dashboardUrl" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Next: the first scheduled recommendations usually arrive within a few hours (at most 48 hours)." -ForegroundColor Yellow
    Write-Host "  Check progress: aws wellarchitected list-agent-recommendation-generations --profile-arn $profileArn --region $agentRegion" -ForegroundColor Yellow
    Write-Host "  Check eligibility: aws wellarchitected get-agent-profile --profile-arn $profileArn --region $agentRegion" -ForegroundColor Yellow
    Write-Host "  Cleanup: .\deploy-all.ps1 -Variant multi -Destroy, then .\deploy-all.ps1 -Variant profile -Destroy (same AWS_REGION and WA_AGENT_REGION)" -ForegroundColor Yellow
}
finally {
    foreach ($k in $envKeys) { [Environment]::SetEnvironmentVariable($k, $savedEnv[$k], "Process") }
    Pop-Location
}
