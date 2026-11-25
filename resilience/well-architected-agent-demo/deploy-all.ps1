# Well-Architected Agent demo - deployment script (PowerShell)
#
# Deploys ONE variant: the workload under test, then the agent stack that reviews
# it. Uses the GenAI Ops Demo Library shared scripts, so it must run from
# <pillar>/<demo>/ inside the library checkout.
#
#   single (default)  deliberately non-optimized single-region workload:
#                     WAAgentDemoSingleWorkload-<region>
#   multi             active/active workload in two Regions with documented gaps:
#                     WAAgentDemoMultiWorkloadPrimary-<primary> and
#                     WAAgentDemoMultiWorkloadSecondary-<secondary>. The secondary
#                     Region comes from -SecondaryRegion; otherwise, in us-* and eu-*
#                     Regions, the script picks one from the account's enabled Regions
#                     with the same Region-code prefix (alphabetical). Later runs
#                     reuse the deployed one.
#   agent stack       WAAgentDemoProfile-<agent-region>: roles, profile, goals and one
#                     application context, configured for the variant. Main stack.
#
# One variant at a time per account and agent Region: the agent stack records the
# deployed variant and Regions in its outputs. A deploy that does not match them
# stops and prints the destroy command; -Destroy reads them, so it needs no option.
#
# Regions: the workload deploys in the Region resolved by the shared prerequisites
# check (AWS_REGION / AWS_DEFAULT_REGION). The agent stack lives in WA_AGENT_REGION,
# which defaults to the same Region. A profile can only be hosted in a few Regions;
# when yours is not one of them the check stops and tells you to set WA_AGENT_REGION.
#
# Usage:
#   .\deploy-all.ps1                                   # single, admin CIDR = your public IP /32
#   .\deploy-all.ps1 -AdminCidr "<YOUR_IP>/32"
#   .\deploy-all.ps1 -Variant multi
#   .\deploy-all.ps1 -Variant multi -SecondaryRegion us-west-2
#   .\deploy-all.ps1 -Variant multi -Domain app.example.com -Zone example.com   # optional HTTPS
#   .\deploy-all.ps1 -Destroy                          # remove the deployed variant
#   $env:WA_AGENT_REGION = "us-east-1"                 # host the agent stack outside the workload Region
param(
    [ValidateSet("single", "multi")]
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
$variantGiven = $PSBoundParameters.ContainsKey("Variant")

if (-not $Destroy -and $Variant -ne "multi" -and (-not [string]::IsNullOrEmpty($Domain) -or -not [string]::IsNullOrEmpty($Zone))) {
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
$envKeys = @("AWS_REGION", "AWS_DEFAULT_REGION", "WA_AGENT_REGION",
             "WA_DEMO_VARIANT", "WA_DEMO_PRIMARY_REGION", "WA_DEMO_SECONDARY_REGION")
$savedEnv = @{}
foreach ($k in $envKeys) { $savedEnv[$k] = [Environment]::GetEnvironmentVariable($k, "Process") }

Push-Location $PSScriptRoot
try {
    # Prerequisites: AWS CLI (2.31.13+ for the Well-Architected Agent API),
    # credentials, region, Node.js 20+ and CDK, and the Region that hosts the agent
    # profile (WA_AGENT_REGION, default: the deploy Region). The shared check holds
    # the list of hosting Regions and stops when the agent Region is not one.
    & (Join-Path $sharedScripts "check-prerequisites.ps1") -RequiredService "wellarchitected-agent" -RequireCDK -MinAwsCliVersion "2.31.13"
    if ($LASTEXITCODE -ne 0) { exit 1 }
    $region = $global:AWS_REGION
    $agentRegion = $global:WA_AGENT_REGION
    $agentStack = "WAAgentDemoProfile-$agentRegion"

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

    # Region-code prefix: the code without its last two segments (us-east-1 -> us,
    # ap-southeast-1 -> ap, us-gov-west-1 -> us-gov). Both Regions of multi share it.
    # It is not a data residency boundary (ap groups Tokyo, Mumbai and Sydney), so
    # the script only picks a secondary Region by itself for us and eu. Same rule as
    # region_prefix in the Bash twin.
    function Get-RegionPrefix([string]$regionCode) {
        return ($regionCode -replace '-[a-z]+-\d+$', '')
    }

    # Default secondary Region of multi, with no Region list in the demo: this
    # account's enabled Regions (ec2 describe-regions, read-only) with the primary's
    # prefix, primary excluded, sorted alphabetically (ordinal, as `LC_ALL=C sort` in
    # the Bash twin), Regions without opt-in first. "" when there is none.
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
        $prefix = Get-RegionPrefix $primary
        $same = @($names | Where-Object { (Get-RegionPrefix $_) -ceq $prefix })
        $first = @($same | Where-Object { $optIn[$_] -eq "opt-in-not-required" }) + $same | Select-Object -First 1
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

    # What is deployed in this account and agent Region, read from the agent stack
    # (read-only). Its Region is known, while a workload stack can be in any Region.
    $deployedVariant   = Get-StackOutputOrEmpty $agentStack $agentRegion "Variant"
    $deployedWorkload  = Get-StackOutputOrEmpty $agentStack $agentRegion "WorkloadRegion"
    $deployedSecondary = Get-StackOutputOrEmpty $agentStack $agentRegion "SecondaryRegion"

    # =======================================================================
    # Destroy: agent stack first, then the workload stacks
    # =======================================================================
    if ($Destroy) {
        if ($deployedVariant) {
            if ($variantGiven -and $Variant -ne $deployedVariant) {
                Write-Host "ERROR: $agentStack ($agentRegion) deploys variant $deployedVariant, not $Variant. Run .\deploy-all.ps1 -Destroy without -Variant." -ForegroundColor Red
                exit 1
            }
            $Variant = $deployedVariant
            $workloadRegion = $deployedWorkload
            $secondaryRegion = $deployedSecondary
            Write-Host "Found $agentStack ($agentRegion): variant $Variant, workload Region $workloadRegion$(if ($secondaryRegion) { ", secondary $secondaryRegion" })" -ForegroundColor Gray
        } else {
            # No agent stack: remove what is left of a partial deployment, from the
            # options (variant, AWS_REGION, and the secondary Region for multi).
            $workloadRegion = $region
            Write-Host "No $agentStack in $agentRegion. Removing what is left of variant $Variant in $workloadRegion." -ForegroundColor Yellow
            if ($Variant -eq "multi") {
                $secondaryRegion = $SecondaryRegion
                if (-not $secondaryRegion) {
                    $secondaryRegion = Get-StackOutputOrEmpty "WAAgentDemoMultiWorkloadPrimary-$workloadRegion" $workloadRegion "SecondaryRegion"
                }
                if (-not $secondaryRegion) {
                    Write-Host "ERROR: the secondary Region is unknown. Pass it explicitly: -SecondaryRegion <region>" -ForegroundColor Red
                    exit 1
                }
            }
        }

        $env:WA_DEMO_VARIANT = $Variant
        $env:WA_DEMO_PRIMARY_REGION = $workloadRegion
        $env:WA_AGENT_REGION = $agentRegion
        if ($Variant -eq "multi") { $env:WA_DEMO_SECONDARY_REGION = $secondaryRegion }

        Invoke-DeployIn $agentRegion @{ StackName = $agentStack; SkipBootstrap = $true; DestroyStack = $true }
        if ($Variant -eq "single") {
            $names = @("WAAgentDemoSingleWorkload-$workloadRegion")
            Invoke-DeployIn $workloadRegion @{ StackName = $names[0]; SkipBootstrap = $true; DestroyStack = $true }
        } else {
            # Secondary, then primary (the primary deletes the global table and its replica).
            $names = @("WAAgentDemoMultiWorkloadSecondary-$secondaryRegion", "WAAgentDemoMultiWorkloadPrimary-$workloadRegion")
            Invoke-DeployIn $secondaryRegion @{ StackName = $names[0]; SkipBootstrap = $true; DestroyStack = $true }
            Invoke-DeployIn $workloadRegion  @{ StackName = $names[1]; SkipBootstrap = $true; DestroyStack = $true }
        }
        Write-Host ""
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  Cleanup Complete! (variant: $Variant)" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  Destroyed:  $agentStack ($agentRegion)" -ForegroundColor Cyan
        foreach ($n in $names) { Write-Host "              $n" -ForegroundColor Cyan }
        exit 0
    }

    # =======================================================================
    # Deploy: one variant per account and agent Region
    # =======================================================================
    if ($deployedVariant -and ($deployedVariant -ne $Variant -or $deployedWorkload -ne $region)) {
        Write-Host "ERROR: $agentStack ($agentRegion) already deploys variant $deployedVariant for workload Region $deployedWorkload." -ForegroundColor Red
        Write-Host "       One variant at a time. Destroy it first, with the same WA_AGENT_REGION ($agentRegion):" -ForegroundColor Yellow
        Write-Host "         .\deploy-all.ps1 -Destroy" -ForegroundColor Yellow
        exit 1
    }

    Resolve-AdminCidr
    $context = @("adminCidr=$AdminCidr")
    $env:WA_DEMO_VARIANT = $Variant
    $env:WA_DEMO_PRIMARY_REGION = $region
    $env:WA_AGENT_REGION = $agentRegion

    # =======================================================================
    # Variant: single
    # =======================================================================
    if ($Variant -eq "single") {
        $workloadStack = "WAAgentDemoSingleWorkload-$region"

        # 1. Workload (bootstraps its Region), 2. agent stack (bootstraps the agent
        #    Region when it differs).
        Invoke-DeployIn $region @{ StackName = $workloadStack; CdkContext = $context }
        Invoke-DeployIn $agentRegion @{ StackName = $agentStack; CdkContext = $context; SkipBootstrap = ($agentRegion -eq $region) }

        $instanceIp = Get-StackOutput $workloadStack $region "InstancePublicIp"
        $dbEndpoint = Get-StackOutput $workloadStack $region "DatabaseEndpoint"
        $bucketName = Get-StackOutput $workloadStack $region "AssetBucketName"
        $profileArn = Get-StackOutput $agentStack $agentRegion "AgentProfileArn"

        Write-Host ""
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  Deployment Complete! (variant: single)" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  Workload:       $workloadStack ($region)" -ForegroundColor Cyan
        Write-Host "  Agent:          $agentStack ($agentRegion)" -ForegroundColor Cyan
        Write-Host "  Admin CIDR:     $AdminCidr" -ForegroundColor Cyan
        Write-Host "  Instance IP:    $instanceIp" -ForegroundColor Cyan
        Write-Host "  DB endpoint:    $dbEndpoint" -ForegroundColor Cyan
        Write-Host "  Asset bucket:   $bucketName" -ForegroundColor Cyan
        Write-Host "  Agent profile:  $profileArn" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  Next: the first scheduled recommendations usually arrive within a few hours (at most 48 hours)." -ForegroundColor Yellow
        Write-Host "  Check progress: aws wellarchitected list-agent-recommendation-generations --profile-arn $profileArn --region $agentRegion" -ForegroundColor Yellow
        Write-Host "  Check eligibility: aws wellarchitected get-agent-profile --profile-arn $profileArn --region $agentRegion" -ForegroundColor Yellow
        Write-Host "  Cleanup: .\deploy-all.ps1 -Destroy (same WA_AGENT_REGION)" -ForegroundColor Yellow
        exit 0
    }

    # =======================================================================
    # Variant: multi
    # =======================================================================
    $primaryRegion = $region
    $primaryStack  = "WAAgentDemoMultiWorkloadPrimary-$primaryRegion"
    $prefix = Get-RegionPrefix $primaryRegion

    # Secondary Region, in this order:
    #   1. -SecondaryRegion;
    #   2. the deployed one (agent stack, else the primary stack of a partial
    #      deployment), so a redeploy never moves it;
    #   3. in us-* and eu-* only: chosen from this account's enabled Regions.
    if (-not $deployedSecondary) {
        $deployedSecondary = Get-StackOutputOrEmpty $primaryStack $primaryRegion "SecondaryRegion"
    }
    if (-not [string]::IsNullOrEmpty($SecondaryRegion)) {
        if ($deployedSecondary -and $deployedSecondary -ne $SecondaryRegion) {
            Write-Host "ERROR: this deployment already uses secondary Region $deployedSecondary, not $SecondaryRegion." -ForegroundColor Red
            Write-Host "       Omit -SecondaryRegion to keep it, or destroy it first: .\deploy-all.ps1 -Destroy" -ForegroundColor Yellow
            exit 1
        }
        $secondaryRegion = $SecondaryRegion
    } elseif ($deployedSecondary) {
        $secondaryRegion = $deployedSecondary
        Write-Host "Secondary Region: $secondaryRegion (from the deployed stacks)" -ForegroundColor Gray
    } elseif ($prefix -cne "us" -and $prefix -cne "eu") {
        Write-Host "ERROR: pass the secondary Region for $primaryRegion explicitly: -SecondaryRegion <region>" -ForegroundColor Red
        Write-Host "       Outside us-* and eu-*, Regions with the same code prefix can be far apart (ap-* spans Tokyo to Sydney)," -ForegroundColor Yellow
        Write-Host "       so the script does not choose one for you." -ForegroundColor Yellow
        exit 1
    } else {
        $secondaryRegion = Select-SecondaryRegion $primaryRegion
        if ([string]::IsNullOrEmpty($secondaryRegion)) {
            Write-Host "ERROR: this account has no other enabled $prefix-* Region besides $primaryRegion." -ForegroundColor Red
            Write-Host "       Enable one, pass -SecondaryRegion, or use the single variant." -ForegroundColor Yellow
            exit 1
        }
        Write-Host "Secondary Region: $secondaryRegion (chosen from this account's enabled Regions; pass -SecondaryRegion for another)" -ForegroundColor Gray
    }
    if ($secondaryRegion -notmatch '^[a-z]{2}(-[a-z]+)+-\d$' -or $secondaryRegion -eq $primaryRegion) {
        Write-Host "ERROR: -SecondaryRegion must be a Region code different from $primaryRegion (got: $secondaryRegion)" -ForegroundColor Red
        exit 1
    }
    if ((Get-RegionPrefix $secondaryRegion) -cne $prefix) {
        Write-Host "ERROR: the secondary Region must have the same Region-code prefix as $primaryRegion ($prefix-*); got $secondaryRegion." -ForegroundColor Red
        exit 1
    }
    $env:WA_DEMO_SECONDARY_REGION = $secondaryRegion
    $secondaryStack = "WAAgentDemoMultiWorkloadSecondary-$secondaryRegion"

    if (-not [string]::IsNullOrEmpty($Domain)) {
        $context += @("appDomainName=$Domain", "route53ZoneName=$Zone")
    }

    # 1. Primary: bootstraps the primary Region, deploys its stack and the global table.
    Invoke-DeployIn $primaryRegion @{ StackName = $primaryStack; CdkContext = $context }
    # 2. Secondary: bootstraps the secondary Region and deploys its stack.
    Invoke-DeployIn $secondaryRegion @{ StackName = $secondaryStack; CdkContext = $context }
    # 3. Agent stack (bootstraps the agent Region when it is neither workload Region).
    $agentBootstrapped = ($agentRegion -eq $primaryRegion -or $agentRegion -eq $secondaryRegion)
    Invoke-DeployIn $agentRegion @{ StackName = $agentStack; CdkContext = $context; SkipBootstrap = $agentBootstrapped }

    $primaryAlb   = Get-StackOutput $primaryStack $primaryRegion "AlbDnsName"
    $secondaryAlb = Get-StackOutput $secondaryStack $secondaryRegion "AlbDnsName"
    $tableName    = Get-StackOutput $primaryStack $primaryRegion "DynamoDbTableName"
    $profileArn   = Get-StackOutput $agentStack $agentRegion "AgentProfileArn"

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Deployment Complete! (variant: multi)" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Primary:        $primaryStack ($primaryRegion)" -ForegroundColor Cyan
    Write-Host "  Secondary:      $secondaryStack ($secondaryRegion)" -ForegroundColor Cyan
    Write-Host "  Agent:          $agentStack ($agentRegion)" -ForegroundColor Cyan
    Write-Host "  Admin CIDR:     $AdminCidr" -ForegroundColor Cyan
    Write-Host "  Primary ALB:    $primaryAlb" -ForegroundColor Cyan
    Write-Host "  Secondary ALB:  $secondaryAlb" -ForegroundColor Cyan
    Write-Host "  DynamoDB table: $tableName (global, both regions)" -ForegroundColor Cyan
    Write-Host "  Agent profile:  $profileArn" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Next: the first scheduled recommendations usually arrive within a few hours (at most 48 hours)." -ForegroundColor Yellow
    Write-Host "  Check progress: aws wellarchitected list-agent-recommendation-generations --profile-arn $profileArn --region $agentRegion" -ForegroundColor Yellow
    Write-Host "  Check eligibility: aws wellarchitected get-agent-profile --profile-arn $profileArn --region $agentRegion" -ForegroundColor Yellow
    Write-Host "  Cleanup: .\deploy-all.ps1 -Destroy (same WA_AGENT_REGION)" -ForegroundColor Yellow
}
finally {
    foreach ($k in $envKeys) { [Environment]::SetEnvironmentVariable($k, $savedEnv[$k], "Process") }
    Pop-Location
}
