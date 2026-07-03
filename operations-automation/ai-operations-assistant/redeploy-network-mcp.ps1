# =============================================================================
# G.O.A.T. - Full uninstall + redeploy NETWORK-MCP mode only
#
# Destroys every GOAT stack (both CDK apps) in dependency order, then
# redeploys ONLY the Network Agent + DevOps Agent MCP integration (no Auth,
# no Frontend, no Orchestrator) plus the network troubleshooting demo
# scenarios (TLS fragmentation + Scenarios G-L).
#
# Run from this directory:
#   .\redeploy-network-mcp.ps1
#
# Common overrides:
#   .\redeploy-network-mcp.ps1 -Region eu-west-1
#   .\redeploy-network-mcp.ps1 -SkipConfirm   (no interactive prompt)
# =============================================================================
param(
    # AWS CLI profile to use. The account id embedded in the profile name is
    # used as a safety guard against running in the wrong account.
    [string]$Profile = "AdministratorAccess-157643525386",

    # Target region. GOAT stack ids are region-suffixed, so this must match
    # the region the stacks were deployed in.
    [string]$Region = "us-east-1",

    # Skip the interactive "are you sure" prompt before destroying.
    [switch]$SkipConfirm
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$cdkDir = Join-Path $root "infrastructure\cdk"
$demoDir = Join-Path $root "demo-scenarios"
$devopsDir = Join-Path $root "devops-integration"
$demoApp = "npx ts-node --prefer-ts-exts bin/demo-scenarios-app.ts"

$env:AWS_PROFILE = $Profile
$env:AWS_REGION = $Region
$env:AWS_DEFAULT_REGION = $Region

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

# -----------------------------------------------------------------------------
# 0. Safety: confirm we are pointed at the expected account
# -----------------------------------------------------------------------------
Write-Step "Verifying AWS identity"
$expectedAccount = ($Profile -replace '[^0-9]', '')
$actualAccount = (aws sts get-caller-identity --query "Account" --output text --no-cli-pager 2>&1)
if ($LASTEXITCODE -ne 0) {
    Write-Host "Could not call sts get-caller-identity. Is the profile '$Profile' valid / logged in?" -ForegroundColor Red
    Write-Host $actualAccount -ForegroundColor Red
    exit 1
}
Write-Host "Profile : $Profile"
Write-Host "Account : $actualAccount"
Write-Host "Region  : $Region"
Write-Host "Mode    : network-mcp (Network Agent + MCP only, no Auth/Frontend/Orchestrator)" -ForegroundColor Magenta
if ($expectedAccount -and ($actualAccount.Trim() -ne $expectedAccount)) {
    Write-Host "ABORT: resolved account ($actualAccount) does not match the account in the profile name ($expectedAccount)." -ForegroundColor Red
    Write-Host "Pass the correct -Profile, or fix your SSO login." -ForegroundColor Red
    exit 1
}

if (-not $SkipConfirm) {
    Write-Host "`nThis will DESTROY all GOAT stacks in account $actualAccount ($Region) and redeploy in network-mcp mode." -ForegroundColor Yellow
    Write-Host "Stacks deployed: NetworkData, NetworkInfra, NetworkRuntime, DevOpsIntegration + Demo Scenarios (C + G-L)" -ForegroundColor Yellow
    $answer = Read-Host "Type 'destroy' to continue"
    if ($answer -ne "destroy") { Write-Host "Cancelled." -ForegroundColor Yellow; exit 0 }
}

# -----------------------------------------------------------------------------
# 1. DESTROY - DevOps Agent integration (depends on NetworkAgent exports)
# -----------------------------------------------------------------------------
Write-Step "Destroying DevOps Agent integration stack (GOATDevOpsIntegration*)"

# Deregister MCP server
$prevEAP0 = $ErrorActionPreference; $ErrorActionPreference = "Continue"
$serviceList = aws devops-agent list-services --output json --no-cli-pager 2>$null
$ErrorActionPreference = $prevEAP0
if ($serviceList) {
    try {
        $services = $serviceList | ConvertFrom-Json
        $svc = $services.services | Where-Object { $_.serviceType -eq "mcpserversigv4" }
        if ($svc) {
            $serviceId = $svc.serviceId
            Write-Host "  Found MCP service: $serviceId" -ForegroundColor Yellow

            # Disassociate from all AgentSpaces first
            $prevEAP0b = $ErrorActionPreference; $ErrorActionPreference = "Continue"
            $agentSpacesJson = aws devops-agent list-agent-spaces --output json --no-cli-pager 2>$null
            $ErrorActionPreference = $prevEAP0b
            if ($agentSpacesJson) {
                try {
                    $agentSpaces = ($agentSpacesJson | ConvertFrom-Json).agentSpaces
                    foreach ($space in $agentSpaces) {
                        $assocJson = aws devops-agent list-associations --agent-space-id $space.agentSpaceId --filter-service-types "mcpserversigv4" --output json --no-cli-pager 2>$null
                        if ($assocJson) {
                            $assocs = ($assocJson | ConvertFrom-Json).associations
                            foreach ($assoc in $assocs) {
                                if ($assoc.serviceId -eq $serviceId) {
                                    Write-Host "  Disassociating from AgentSpace $($space.agentSpaceId) (association: $($assoc.associationId))..." -ForegroundColor Yellow
                                    aws devops-agent disassociate-service --agent-space-id $space.agentSpaceId --association-id $assoc.associationId --no-cli-pager 2>&1 | Out-Null
                                }
                            }
                        }
                    }
                } catch {
                    Write-Host "  Could not disassociate (continuing): $_" -ForegroundColor DarkYellow
                }
            }

            Write-Host "  Deregistering MCP service: $serviceId..." -ForegroundColor Yellow
            aws devops-agent deregister-service --service-id $serviceId --no-cli-pager 2>&1 | Out-Null
            Write-Host "  Deregistered." -ForegroundColor Green
        } else {
            Write-Host "  No mcpserversigv4 service found (already deregistered)." -ForegroundColor DarkGray
        }
    } catch {
        Write-Host "  Could not parse service list (continuing)." -ForegroundColor DarkYellow
    }
}

$devopsStackName = "GOATDevOpsIntegration-$Region"
$prevEAP = $ErrorActionPreference; $ErrorActionPreference = "Continue"
$devopsStatus = aws cloudformation describe-stacks --stack-name $devopsStackName --query "Stacks[0].StackStatus" --output text --no-cli-pager 2>$null
$ErrorActionPreference = $prevEAP
if ($devopsStatus -and $devopsStatus -notmatch "does not exist" -and $devopsStatus -ne "DELETE_COMPLETE") {
    Write-Host "  Deleting $devopsStackName (status: $devopsStatus)..." -ForegroundColor Yellow
    aws cloudformation delete-stack --stack-name $devopsStackName --no-cli-pager 2>&1 | Out-Null
    Write-Host "  Waiting for stack deletion..." -ForegroundColor DarkGray
    aws cloudformation wait stack-delete-complete --stack-name $devopsStackName --no-cli-pager 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Delete failed - retrying with --retain-resources..." -ForegroundColor DarkYellow
        $failedResources = aws cloudformation describe-stack-resources --stack-name $devopsStackName `
            --query "StackResources[?ResourceStatus=='DELETE_FAILED'].LogicalResourceId" `
            --output text --no-cli-pager 2>&1
        $retainList = @(($failedResources -split '\s+') | Where-Object { $_ -and $_ -ne "None" })
        if ($retainList.Count -gt 0) {
            aws cloudformation delete-stack --stack-name $devopsStackName --retain-resources $retainList --no-cli-pager 2>&1 | Out-Null
        } else {
            aws cloudformation delete-stack --stack-name $devopsStackName --no-cli-pager 2>&1 | Out-Null
        }
        aws cloudformation wait stack-delete-complete --stack-name $devopsStackName --no-cli-pager 2>&1 | Out-Null
    }
    Write-Host "  $devopsStackName deleted." -ForegroundColor Green
} else {
    Write-Host "  $devopsStackName not found (already deleted or never deployed)." -ForegroundColor Green
}

# -----------------------------------------------------------------------------
# 1b. DESTROY - demo scenarios app
# -----------------------------------------------------------------------------
Write-Step "Destroying demo scenario stacks (GOATDemoScenario*)"
Push-Location $cdkDir
try {
    npx cdk destroy --all --app $demoApp --force --no-cli-pager
} catch {
    Write-Host "Demo scenario destroy reported an issue (continuing): $_" -ForegroundColor DarkYellow
}
Pop-Location

# -----------------------------------------------------------------------------
# 2. DESTROY - main app (all GOAT stacks)
# -----------------------------------------------------------------------------
Write-Step "Destroying core GOAT stacks (--all)"
Push-Location $cdkDir
try {
    npx cdk destroy --all --force --no-cli-pager
} catch {
    Write-Host "Core destroy reported an issue (continuing): $_" -ForegroundColor DarkYellow
}
Pop-Location

# -----------------------------------------------------------------------------
# 3. Verify nothing is left + force-delete DELETE_FAILED stacks + destroy remaining
# -----------------------------------------------------------------------------
Write-Step "Checking for leftover GOAT stacks"
$leftover = aws cloudformation list-stacks `
    --query "StackSummaries[?contains(StackName,'GOAT') && StackStatus!='DELETE_COMPLETE'].[StackName,StackStatus]" `
    --output text --no-cli-pager 2>&1
if ([string]::IsNullOrWhiteSpace($leftover)) {
    Write-Host "Clean - no remaining GOAT stacks." -ForegroundColor Green
} else {
    Write-Host "Stacks still present:" -ForegroundColor Yellow
    Write-Host $leftover

    # Force-delete any DELETE_FAILED stacks (AgentCore timeout is the usual cause)
    $failedStacks = aws cloudformation list-stacks `
        --query "StackSummaries[?contains(StackName,'GOAT') && StackStatus=='DELETE_FAILED'].StackName" `
        --output text --no-cli-pager 2>&1
    $failedList = @(($failedStacks -split '\s+') | Where-Object { $_ -and $_ -ne "None" })

    if ($failedList.Count -gt 0) {
        Write-Host "Force-deleting $($failedList.Count) DELETE_FAILED stack(s)..." -ForegroundColor Yellow
        foreach ($stack in $failedList) {
            Write-Host "  Deleting $stack (skipping failed resources)..." -ForegroundColor Yellow
            $failedResources = aws cloudformation describe-stack-resources --stack-name $stack `
                --query "StackResources[?ResourceStatus=='DELETE_FAILED'].LogicalResourceId" `
                --output text --no-cli-pager 2>&1
            $retainList = @(($failedResources -split '\s+') | Where-Object { $_ -and $_ -ne "None" })
            if ($retainList.Count -gt 0) {
                aws cloudformation delete-stack --stack-name $stack --retain-resources $retainList --no-cli-pager 2>&1 | Out-Null
            } else {
                aws cloudformation delete-stack --stack-name $stack --no-cli-pager 2>&1 | Out-Null
            }
            aws cloudformation wait stack-delete-complete --stack-name $stack --no-cli-pager 2>&1 | Out-Null
        }
    }

    # Delete any remaining active stacks (CREATE_COMPLETE, UPDATE_COMPLETE, etc.)
    # that CDK never reached due to the earlier abort. Delete in reverse-dependency
    # order: Runtime stacks first, then Infra, then Data.
    $activeStacks = aws cloudformation list-stacks `
        --query "StackSummaries[?contains(StackName,'GOAT') && (StackStatus=='CREATE_COMPLETE' || StackStatus=='UPDATE_COMPLETE' || StackStatus=='UPDATE_ROLLBACK_COMPLETE')].StackName" `
        --output text --no-cli-pager 2>&1
    $activeList = @(($activeStacks -split '\s+') | Where-Object { $_ -and $_ -ne "None" })

    if ($activeList.Count -gt 0) {
        Write-Host "Destroying $($activeList.Count) remaining active stack(s) individually..." -ForegroundColor Yellow

        # Sort: Runtime stacks first, then Infra, then Data/Auth/Frontend (reverse dependency)
        $runtimeStacks = $activeList | Where-Object { $_ -match "Runtime" }
        $infraStacks = $activeList | Where-Object { $_ -match "Infra" -and $_ -notmatch "Runtime" }
        $otherStacks = $activeList | Where-Object { $_ -notmatch "Runtime" -and $_ -notmatch "Infra" }
        $orderedStacks = @($runtimeStacks) + @($infraStacks) + @($otherStacks)

        foreach ($stack in $orderedStacks) {
            Write-Host "  Deleting $stack..." -ForegroundColor Yellow
            aws cloudformation delete-stack --stack-name $stack --no-cli-pager 2>&1 | Out-Null
        }
        # Wait for all (in parallel - they're independent once ordered correctly)
        foreach ($stack in $orderedStacks) {
            Write-Host "  Waiting for $stack..." -ForegroundColor DarkGray
            aws cloudformation wait stack-delete-complete --stack-name $stack --no-cli-pager 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                # Might be another AgentCore timeout - force-delete
                Write-Host "  $stack delete timed out - force-deleting with --retain-resources..." -ForegroundColor DarkYellow
                $failedRes = aws cloudformation describe-stack-resources --stack-name $stack `
                    --query "StackResources[?ResourceStatus=='DELETE_FAILED'].LogicalResourceId" `
                    --output text --no-cli-pager 2>&1
                $retainRes = @(($failedRes -split '\s+') | Where-Object { $_ -and $_ -ne "None" })
                if ($retainRes.Count -gt 0) {
                    aws cloudformation delete-stack --stack-name $stack --retain-resources $retainRes --no-cli-pager 2>&1 | Out-Null
                } else {
                    aws cloudformation delete-stack --stack-name $stack --no-cli-pager 2>&1 | Out-Null
                }
                aws cloudformation wait stack-delete-complete --stack-name $stack --no-cli-pager 2>&1 | Out-Null
            }
        }

        # Final verification
        $stillLeft = aws cloudformation list-stacks `
            --query "StackSummaries[?contains(StackName,'GOAT') && StackStatus!='DELETE_COMPLETE'].[StackName,StackStatus]" `
            --output text --no-cli-pager 2>&1
        if ([string]::IsNullOrWhiteSpace($stillLeft)) {
            Write-Host "All GOAT stacks destroyed." -ForegroundColor Green
        } else {
            Write-Host "Some stacks remain (may need manual intervention):" -ForegroundColor Red
            Write-Host $stillLeft
        }
    }
}
cmd /c "rd /s /q `"$cdkDir\cdk.out`" 2>nul"

# -----------------------------------------------------------------------------
# 3b. Clean up orphaned Traffic Mirror resources
# -----------------------------------------------------------------------------
Write-Step "Cleaning up orphaned Traffic Mirror resources"

function Remove-AllGoatMirrorSessions {
    $sessions = aws ec2 describe-traffic-mirror-sessions `
        --query "TrafficMirrorSessions[].TrafficMirrorSessionId" --output text --no-cli-pager 2>&1
    $ids = @(($sessions -split '\s+') | Where-Object { $_ -and $_ -ne "None" })
    foreach ($sid in $ids) {
        Write-Host "  Deleting mirror session $sid" -ForegroundColor Yellow
        aws ec2 delete-traffic-mirror-session --traffic-mirror-session-id $sid --no-cli-pager 2>&1 | Out-Null
    }
    return $ids.Count
}
function Remove-OrphanedGoatMirrorTargets {
    $targets = aws ec2 describe-traffic-mirror-targets `
        --query "TrafficMirrorTargets[?contains(Description,'G.O.A.T.') || contains(Description,'goat')].TrafficMirrorTargetId" `
        --output text --no-cli-pager 2>&1
    $ids = @(($targets -split '\s+') | Where-Object { $_ -and $_ -ne "None" })
    foreach ($tid in $ids) {
        Write-Host "  Deleting mirror target $tid" -ForegroundColor Yellow
        aws ec2 delete-traffic-mirror-target --traffic-mirror-target-id $tid --no-cli-pager 2>&1 | Out-Null
    }
    return $ids.Count
}
function Remove-OrphanedGoatMirrorFilters {
    $filters = aws ec2 describe-traffic-mirror-filters `
        --query "TrafficMirrorFilters[?contains(Description,'G.O.A.T.') || contains(Description,'goat')].TrafficMirrorFilterId" `
        --output text --no-cli-pager 2>&1
    $ids = @(($filters -split '\s+') | Where-Object { $_ -and $_ -ne "None" })
    foreach ($fid in $ids) {
        Write-Host "  Deleting mirror filter $fid" -ForegroundColor Yellow
        aws ec2 delete-traffic-mirror-filter --traffic-mirror-filter-id $fid --no-cli-pager 2>&1 | Out-Null
    }
    return $ids.Count
}

$sessCount = Remove-AllGoatMirrorSessions
$targCount = Remove-OrphanedGoatMirrorTargets
$filtCount = Remove-OrphanedGoatMirrorFilters
if (($sessCount + $targCount + $filtCount) -eq 0) {
    Write-Host "Clean - no orphaned Traffic Mirror resources." -ForegroundColor Green
} else {
    Write-Host "Removed: $sessCount sessions, $targCount targets, $filtCount filters." -ForegroundColor Green
}

# -----------------------------------------------------------------------------
# 3c. Verify leftover GOAT EC2 instances and ENIs
# -----------------------------------------------------------------------------
Write-Step "Verifying GOAT EC2 instances and ENIs are gone"

function Get-GoatInstances {
    $out = aws ec2 describe-instances `
        --filters "Name=tag:Name,Values=goat-*" "Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down" `
        --query "Reservations[].Instances[].InstanceId" --output text --no-cli-pager 2>&1
    return @(($out -split '\s+') | Where-Object { $_ -and $_ -ne "None" })
}
function Get-GoatEnis {
    $out = aws ec2 describe-network-interfaces `
        --filters "Name=tag:Name,Values=goat-*" `
        --query "NetworkInterfaces[].NetworkInterfaceId" --output text --no-cli-pager 2>&1
    return @(($out -split '\s+') | Where-Object { $_ -and $_ -ne "None" })
}

$goatInstances = Get-GoatInstances
$goatEnis = Get-GoatEnis

if ($goatInstances.Count -eq 0 -and $goatEnis.Count -eq 0) {
    Write-Host "Clean - no GOAT EC2 instances or ENIs remain." -ForegroundColor Green
} else {
    Write-Host "Leftover resources detected:" -ForegroundColor Yellow
    if ($goatInstances.Count) { Write-Host "  Instances: $($goatInstances -join ', ')" -ForegroundColor Yellow }
    if ($goatEnis.Count)      { Write-Host "  ENIs     : $($goatEnis -join ', ')" -ForegroundColor Yellow }

    # Auto-cleanup in SkipConfirm mode, prompt otherwise
    $doCleanup = $SkipConfirm
    if (-not $SkipConfirm) {
        $ans = Read-Host "Attempt to terminate/delete these leftovers? (yes/no)"
        $doCleanup = ($ans -eq "yes")
    }
    if ($doCleanup) {
        if ($goatInstances.Count) {
            Write-Host "Terminating leftover instances..." -ForegroundColor Yellow
            aws ec2 terminate-instances --instance-ids $goatInstances --no-cli-pager 2>&1 | Out-Null
            aws ec2 wait instance-terminated --instance-ids $goatInstances --no-cli-pager 2>&1 | Out-Null
        }
        Start-Sleep -Seconds 5
        foreach ($eni in (Get-GoatEnis)) {
            $status = aws ec2 describe-network-interfaces --network-interface-ids $eni `
                --query "NetworkInterfaces[0].Status" --output text --no-cli-pager 2>&1
            if ($status -eq "available") {
                aws ec2 delete-network-interface --network-interface-id $eni --no-cli-pager 2>&1 | Out-Null
            }
        }
    } else {
        Write-Host "Aborting before redeploy." -ForegroundColor Yellow
        exit 1
    }
}

# -----------------------------------------------------------------------------
# 4. Bootstrap CDK (idempotent)
# -----------------------------------------------------------------------------
Write-Step "Bootstrapping CDK environment"
Push-Location $cdkDir
npx cdk bootstrap "aws://$actualAccount/$Region" --no-cli-pager
$bootstrapExit = $LASTEXITCODE
Pop-Location
if ($bootstrapExit -ne 0) { Write-Host "cdk bootstrap failed." -ForegroundColor Red; exit 1 }

# -----------------------------------------------------------------------------
# 4b. Clear stale CDK context cache
#     After a full teardown + redeploy, VPCs get new IDs but cdk.context.json
#     may still cache the old VPC ID from Vpc.fromLookup(). Removing it forces
#     CDK to re-resolve from live CloudFormation exports on the next synth.
# -----------------------------------------------------------------------------
Write-Step "Clearing stale CDK context caches"
$contextFiles = @(
    (Join-Path $cdkDir "cdk.context.json"),
    (Join-Path $cdkDir "cdk.out")
)
foreach ($ctxFile in $contextFiles) {
    if (Test-Path $ctxFile) {
        Write-Host "  Removing $ctxFile" -ForegroundColor Yellow
        Remove-Item -Recurse -Force $ctxFile -ErrorAction SilentlyContinue
    }
}
Write-Host "  Context caches cleared — CDK will re-resolve VPC lookups from live exports." -ForegroundColor Green

# -----------------------------------------------------------------------------
# 5. REDEPLOY - Network Agent + MCP integration (network-mcp mode)
# -----------------------------------------------------------------------------
Write-Step "Building DevOps Agent MCP handler (esbuild)"
Push-Location $devopsDir
if (Test-Path "node_modules") {
    $esbuildDirs = Get-ChildItem -Path "node_modules/@esbuild" -Directory -ErrorAction SilentlyContinue
    $hasCorrectPlatform = $esbuildDirs | Where-Object { $_.Name -match "win32" }
    if (-not $hasCorrectPlatform -and $esbuildDirs.Count -gt 0) {
        Write-Host "  Detected cross-platform node_modules - reinstalling..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force "node_modules" -ErrorAction SilentlyContinue
        npm ci --silent
    }
}
if (-not (Test-Path "node_modules")) {
    Write-Host "  Installing devops-integration dependencies..." -ForegroundColor DarkGray
    npm ci --silent
}
npx esbuild src/lambda/mcp-handler.ts --bundle --platform=node --target=node20 --outfile=dist/mcp-handler.js "--external:@aws-sdk/client-bedrock-agent-runtime"
if ($LASTEXITCODE -ne 0) { Write-Host "esbuild failed." -ForegroundColor Red; Pop-Location; exit 1 }
Pop-Location

Write-Step "Deploying in network-mcp mode (Network Agent + DevOps MCP integration only)"
Push-Location $root
& ".\deploy-all.ps1" -DeploymentMode "network-mcp"
$deployExit = $LASTEXITCODE
Pop-Location
if ($deployExit -ne 0) { Write-Host "deploy-all.ps1 -DeploymentMode network-mcp failed." -ForegroundColor Red; exit 1 }

# -----------------------------------------------------------------------------
# 6. DEPLOY - Demo Scenarios: TLS Fragmentation (Scenario C) + Network Troubleshooting (G-L)
# -----------------------------------------------------------------------------
Write-Step "Deploying demo scenarios: connectivity (TLS fragmentation) + network-troubleshooting (G-L)"
Push-Location $demoDir

# Deploy connectivity scenario (Scenario C - TLS fragmentation)
Write-Host "  Deploying Scenario C (TLS fragmentation)..." -ForegroundColor Cyan
& ".\deploy-demo-scenarios.ps1" -Scenario connectivity
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Warning: Scenario C deployment had issues (continuing to G-L)." -ForegroundColor DarkYellow
}

# Deploy network troubleshooting scenarios (G-L)
Write-Host "  Deploying Scenarios G-L (network troubleshooting)..." -ForegroundColor Cyan
& ".\deploy-demo-scenarios.ps1" -Scenario network-troubleshooting
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Warning: Scenario G-L deployment had issues." -ForegroundColor DarkYellow
}

Pop-Location

# -----------------------------------------------------------------------------
# 7. Summary
# -----------------------------------------------------------------------------
Write-Step "Redeploy complete (network-mcp mode)"
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Network-MCP Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Account       : $actualAccount" -ForegroundColor Cyan
Write-Host "  Region        : $Region" -ForegroundColor Cyan
Write-Host "  Mode          : network-mcp (no Auth, no Frontend, no Orchestrator)" -ForegroundColor Cyan
Write-Host ""

# Network Agent ARN
$networkArn = aws cloudformation describe-stacks --stack-name "GOATNetworkRuntime-$Region" `
    --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager 2>&1
if ($networkArn -and $networkArn -ne "None" -and $networkArn -notmatch "error") {
    Write-Host "  Network Agent : $networkArn" -ForegroundColor Cyan
}

# MCP endpoint
$mcpEndpoint = aws cloudformation describe-stacks --stack-name "GOATDevOpsIntegration-$Region" `
    --query "Stacks[0].Outputs[?OutputKey=='McpEndpointUrl'].OutputValue" --output text --no-cli-pager 2>&1
if ($mcpEndpoint -and $mcpEndpoint -ne "None" -and $mcpEndpoint -notmatch "error") {
    Write-Host "  MCP Endpoint  : $mcpEndpoint" -ForegroundColor Cyan
    Write-Host "  Health Check  : ${mcpEndpoint}health" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "  Demo Scenarios Deployed:" -ForegroundColor White
Write-Host "    - Scenario C  : TLS Fragmentation (GOATDemoScenarioC-$Region)" -ForegroundColor White
Write-Host "    - Scenario G  : Connectivity Troubleshooting (agentic_reachability_analyze)" -ForegroundColor White
Write-Host "    - Scenario H  : Routing Troubleshooting (tcp_traceroute)" -ForegroundColor White
Write-Host "    - Scenario I  : TLS Troubleshooting (tls_traceroute)" -ForegroundColor White
Write-Host "    - Scenario J  : DNS Troubleshooting (dns_resolve)" -ForegroundColor White
Write-Host "    - Scenario K  : Database Troubleshooting (db_connectivity_probe)" -ForegroundColor White
Write-Host "    - Scenario L  : SSM Troubleshooting (ssm_health_check)" -ForegroundColor White
Write-Host ""
Write-Host "  No Cognito sign-in required - use via AWS DevOps Agent MCP integration." -ForegroundColor DarkGray
Write-Host "  To add the full chat UI later: .\deploy-all.ps1 -DeploymentMode full" -ForegroundColor DarkGray
Write-Host ""
