# G.O.A.T. (GenAI Operations Analytics Tool) - Complete Deployment Script
#
# Deploys the multi-agent orchestration solution with modular deployment modes.
# Uses shared/scripts/deploy-cdk.ps1 for each stack in dependency order.

param(
    [ValidateSet("full", "cost", "health", "support", "trusted-advisor", "cur", "network", "network-mcp")]
    [string]$DeploymentMode = "full",

    [string]$OrchModelId = "",

    # --- "Bring Your Own VPC" parameters (Network Agent) ---
    # When set, the collector deploys into an existing VPC/subnet.
    # Leave unset for the default demo VPC.
    [string]$VpcId = "",
    [string]$SubnetIds = "",       # Comma-separated subnet IDs
    [string]$VpcCidr = "",         # e.g. "10.0.0.0/16"
    [switch]$SkipVpcEndpoints,
    [string]$CollectorInstanceType = "",
    [int]$CollectorVolumeGib = 0
)

# ---------------------------------------------------------------------------
# Validate --orch-model-id parameter
# ---------------------------------------------------------------------------
if ($PSBoundParameters.ContainsKey('OrchModelId') -and [string]::IsNullOrEmpty($OrchModelId)) {
    Write-Host "Error: --OrchModelId requires a non-empty Bedrock model identifier" -ForegroundColor Red
    Write-Host "Example: -OrchModelId 'global.amazon.nova-pro-v1:0'" -ForegroundColor Gray
    exit 1
}

Write-Host "=== G.O.A.T. - GenAI Operations Analytics Tool Deployment ===" -ForegroundColor Cyan
Write-Host "      Deployment Mode: $DeploymentMode" -ForegroundColor Gray

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
Write-Host "`nRunning prerequisites check..." -ForegroundColor Yellow
& "..\..\shared\scripts\check-prerequisites.ps1" -RequiredService "agentcore" -MinAwsCliVersion "2.31.13" -RequireCDK

if ($LASTEXITCODE -ne 0) {
    Write-Host "Prerequisites check failed" -ForegroundColor Red
    exit 1
}

$region = $global:AWS_REGION
$cdkDir = "infrastructure/cdk"

# ---------------------------------------------------------------------------
# Install frontend dependencies and create placeholder dist
# CDK synthesizes all stacks even when deploying one, so frontend/dist must exist
# ---------------------------------------------------------------------------
if ($DeploymentMode -ne "network-mcp") {
    Write-Host "`nInstalling frontend dependencies..." -ForegroundColor Yellow
    Write-Host "      (Installing React, Vite, Cognito SDK, and Cloudscape components)" -ForegroundColor Gray
    Push-Location frontend
    # Remove stale environment config and build artifacts from prior installs.
    # The correct values are regenerated in section 7 after all stacks are deployed.
    # Without this, a partial redeploy can leave old Cognito/ARN values in the
    # build, causing "User pool client does not exist" errors at sign-in.
    if (Test-Path ".env.production.local") {
        Remove-Item ".env.production.local" -Force
        Write-Host "      Removed stale .env.production.local (will regenerate after deploy)" -ForegroundColor DarkGray
    }
    if (Test-Path "dist") {
        Remove-Item "dist" -Recurse -Force
        Write-Host "      Removed stale dist/ (will rebuild after deploy)" -ForegroundColor DarkGray
    }
    npm install
    Pop-Location
} else {
    Write-Host "`nSkipping frontend dependency install (network-mcp mode has no Frontend stack)..." -ForegroundColor DarkGray
}

# Placeholder frontend/dist/index.html must always be created, regardless of
# deployment mode. CDK synthesizes the entire app -- including FrontendStack's
# BucketDeployment asset resolution -- for every `cdk deploy` invocation
# targeting this app, so frontend/dist must exist on disk even in network-mcp
# mode where the Frontend stack is never actually deployed.
Write-Host "`nCreating placeholder frontend build..." -ForegroundColor Yellow
Write-Host "      (Generating temporary HTML file - required for CDK synthesis)" -ForegroundColor Gray
if (-not (Test-Path "frontend/dist")) {
    New-Item -ItemType Directory -Path "frontend/dist" -Force | Out-Null
    "<!DOCTYPE html><html><body><h1>Building...</h1></body></html>" | Out-File -FilePath "frontend/dist/index.html" -Encoding UTF8
} else {
    Write-Host "      Placeholder already exists, skipping..." -ForegroundColor Gray
}

# ---------------------------------------------------------------------------
# Determine which modules to deploy based on mode
# ---------------------------------------------------------------------------
$deployModules = @()

switch ($DeploymentMode) {
    "full"             { $deployModules = @("Cost", "Health", "Support", "TA", "CUR") }
    "cost"             { $deployModules = @("Cost") }
    "health"           { $deployModules = @("Health") }
    "support"          { $deployModules = @("Support") }
    "trusted-advisor"  { $deployModules = @("TA") }
    "cur"              { $deployModules = @("CUR") }
    "network"          { $deployModules = @() }
    "network-mcp"      { $deployModules = @() }
}

Write-Host "`nModules to deploy: $($deployModules -join ', ')" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# Helper: deploy a single stack with error handling
# ---------------------------------------------------------------------------
function Deploy-Stack {
    param(
        [string]$StackName,
        [string]$Description,
        [switch]$SkipBootstrap
    )

    Write-Host "`nDeploying $StackName..." -ForegroundColor Yellow
    Write-Host "      ($Description)" -ForegroundColor Gray

    # Pre-check: if the stack is stuck in DELETE_FAILED from a prior run,
    # force-delete it first. This is common with AgentCore runtimes that
    # timeout during deletion - not a real error, just a CFN timeout.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $stackStatus = aws cloudformation describe-stacks --stack-name $StackName --query "Stacks[0].StackStatus" --output text --no-cli-pager 2>$null
    $ErrorActionPreference = $prevEAP
    if ($stackStatus -eq "DELETE_FAILED") {
        Write-Host "      Stack is in DELETE_FAILED state (normal - AgentCore runtime deletion timeout)." -ForegroundColor DarkYellow
        Write-Host "      Force-deleting before redeploy..." -ForegroundColor DarkYellow
        $failedResources = aws cloudformation describe-stack-resources --stack-name $StackName `
            --query "StackResources[?ResourceStatus=='DELETE_FAILED'].LogicalResourceId" `
            --output text --no-cli-pager 2>$null
        $retainList = @(($failedResources -split '\s+') | Where-Object { $_ -and $_ -ne "None" })
        if ($retainList.Count -gt 0) {
            aws cloudformation delete-stack --stack-name $StackName --retain-resources $retainList --no-cli-pager 2>$null
        } else {
            aws cloudformation delete-stack --stack-name $StackName --no-cli-pager 2>$null
        }
        aws cloudformation wait stack-delete-complete --stack-name $StackName --no-cli-pager 2>$null
        Write-Host "      Done - proceeding with fresh deploy." -ForegroundColor Green
    }

    # Build CDK context args for "Bring Your Own VPC" if provided
    $extraArgs = ""
    if ($StackName -match "NetworkInfra" -or $StackName -match "NetworkData") {
        $contextParts = @()
        if (-not [string]::IsNullOrEmpty($VpcId)) { $contextParts += "-c goatExistingVpcId=$VpcId" }
        if (-not [string]::IsNullOrEmpty($SubnetIds)) { $contextParts += "-c goatCollectorSubnetIds=$SubnetIds" }
        if (-not [string]::IsNullOrEmpty($VpcCidr)) { $contextParts += "-c goatVpcCidr=$VpcCidr" }
        if ($SkipVpcEndpoints) { $contextParts += "-c goatSkipVpcEndpoints=true" }
        if (-not [string]::IsNullOrEmpty($CollectorInstanceType)) { $contextParts += "-c goatCollectorInstanceType=$CollectorInstanceType" }
        if ($CollectorVolumeGib -gt 0) { $contextParts += "-c goatCollectorVolumeGib=$CollectorVolumeGib" }
        if ($contextParts.Count -gt 0) { $extraArgs = $contextParts -join " " }
    }

    if ($SkipBootstrap) {
        & "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory $cdkDir -StackName $StackName -SkipBootstrap -ExtraArgs $extraArgs
    } else {
        & "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory $cdkDir -StackName $StackName -ExtraArgs $extraArgs
    }

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Deployment of $StackName failed" -ForegroundColor Red
        exit 1
    }
}

# ---------------------------------------------------------------------------
# 1. Core Stacks (skipped entirely under network-mcp mode: no Auth/Data
# stack is needed since there is no Frontend and no Cognito sign-in step)
# ---------------------------------------------------------------------------
Write-Host "`n--- Core Stacks ---" -ForegroundColor Magenta

if ($DeploymentMode -ne "network-mcp") {
    Deploy-Stack -StackName "GOATAuth-$region" `
        -Description "Creating Cognito User Pool, Identity Pool, and app client"

    Deploy-Stack -StackName "GOATData-$region" `
        -Description "Creating DynamoDB tables for conversations, knowledge articles, and user preferences" `
        -SkipBootstrap
} else {
    Write-Host "      Skipping GOATAuth/GOATData (network-mcp mode has no Auth/Frontend dependency)..." -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# 2. Infrastructure Stacks per module (ECR, CodeBuild, S3, IAM)
# ---------------------------------------------------------------------------
Write-Host "`n--- Infrastructure Stacks ---" -ForegroundColor Magenta

foreach ($module in $deployModules) {
    Deploy-Stack -StackName "GOAT${module}Infra-$region" `
        -Description "Creating ECR repository, CodeBuild project, S3 bucket, and IAM role for $module agent" `
        -SkipBootstrap
}

# ---------------------------------------------------------------------------
# 3. Runtime Stacks per module (upload source, build container, create AgentCore)
# ---------------------------------------------------------------------------
Write-Host "`n--- Runtime Stacks ---" -ForegroundColor Magenta
Write-Host "      Note: Each runtime stack builds an ARM64 Docker image via CodeBuild (5-10 min each)" -ForegroundColor DarkGray

foreach ($module in $deployModules) {
    Deploy-Stack -StackName "GOAT${module}Runtime-$region" `
        -Description "Uploading $module agent code, building container image, creating AgentCore runtime" `
        -SkipBootstrap
}

# ---------------------------------------------------------------------------
# 4. Network Agent Stacks (full mode, network mode, or network-mcp mode)
# ---------------------------------------------------------------------------
if ($DeploymentMode -eq "full" -or $DeploymentMode -eq "network" -or $DeploymentMode -eq "network-mcp") {
    Write-Host "`n--- Network Agent Stacks ---" -ForegroundColor Magenta

    # Check if GOATSharedDataBucketName export exists; deploy NetworkDataStack if absent
    $sharedBucketExport = $null
    try {
        $sharedBucketExport = aws cloudformation list-exports --query "Exports[?Name=='GOATSharedDataBucketName'].Value" --output text --no-cli-pager 2>$null
    } catch {
        $sharedBucketExport = $null
    }

    if ([string]::IsNullOrWhiteSpace($sharedBucketExport)) {
        Deploy-Stack -StackName "GOATNetworkData-$region" `
            -Description "Creating dedicated Network Data S3 bucket (shared bucket not available)" `
            -SkipBootstrap
    } else {
        Write-Host "`n      Shared data bucket found ($sharedBucketExport), skipping NetworkDataStack" -ForegroundColor Gray
    }

    Deploy-Stack -StackName "GOATNetworkInfra-$region" `
        -Description "Creating ECR repository, CodeBuild project, EC2 collector, Traffic Mirror plumbing, DynamoDB tables, Glue catalog, and Step Functions for Network agent" `
        -SkipBootstrap

    Deploy-Stack -StackName "GOATNetworkRuntime-$region" `
        -Description "Uploading Network agent code, building container image, creating AgentCore runtime" `
        -SkipBootstrap
}

# ---------------------------------------------------------------------------
# 5. Orchestration Stacks (full mode only)
# ---------------------------------------------------------------------------
if ($DeploymentMode -eq "full") {
    Write-Host "`n--- Orchestration Stacks ---" -ForegroundColor Magenta

    Deploy-Stack -StackName "GOATOrchInfra-$region" `
        -Description "Creating ECR repository, CodeBuild project, S3 bucket, and IAM role for orchestration agent" `
        -SkipBootstrap

    # Set ORCH_MODEL_ID environment variable for OrchRuntimeStack when --OrchModelId is supplied
    if (-not [string]::IsNullOrEmpty($OrchModelId)) {
        $env:ORCH_MODEL_ID = $OrchModelId
        Write-Host "      Setting ORCH_MODEL_ID=$OrchModelId for orchestration runtime" -ForegroundColor Gray
    }

    & "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory $cdkDir -StackName "GOATOrchRuntime-$region" -SkipBootstrap 2>&1 | Tee-Object -Variable cdkOutput | Out-Null

    if ($LASTEXITCODE -ne 0) {
        if ($cdkOutput -match "Unrecognized resource types.*BedrockAgentCore") {
            Write-Host "`nDEPLOYMENT FAILED: AgentCore is not available in region '$region'" -ForegroundColor Red
            Write-Host ""
            Write-Host "Please verify AgentCore availability in your target region:" -ForegroundColor Yellow
            Write-Host "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html" -ForegroundColor Cyan
            Write-Host ""
            Write-Host "To deploy to a supported region, configure your AWS CLI:" -ForegroundColor Yellow
            Write-Host "  aws configure set region <your-supported-region>" -ForegroundColor Gray
            Write-Host "  .\deploy-all.ps1" -ForegroundColor Gray
            exit 1
        }
        if ($cdkOutput -match "No export named GOATNetworkAgentRuntimeArn found") {
            Write-Host "`nDEPLOYMENT FAILED: Attach-by-import could not find the Network Agent export" -ForegroundColor Red
            Write-Host ""
            Write-Host "No stack in this account/region has exported 'GOATNetworkAgentRuntimeArn'." -ForegroundColor Yellow
            Write-Host "The '-c goatAttachNetworkByImport=true' context flag requires a Network Agent" -ForegroundColor Yellow
            Write-Host "stack to already be deployed and exporting that value." -ForegroundColor Yellow
            Write-Host ""
            Write-Host "Deploy the Network Agent first, then re-run this deployment:" -ForegroundColor Yellow
            Write-Host "  .\deploy-all.ps1 -DeploymentMode network" -ForegroundColor Gray
            Write-Host "  # or, for an Auth-free/Frontend-free standalone deployment:" -ForegroundColor Gray
            Write-Host "  .\deploy-all.ps1 -DeploymentMode network-mcp" -ForegroundColor Gray
            exit 1
        }
        Write-Host "Orchestration runtime deployment failed" -ForegroundColor Red
        exit 1
    }
}

# ---------------------------------------------------------------------------
# 5b. DevOps Agent Integration (MCP Server + esbuild bundle)
# full mode OR network-mcp mode
# ---------------------------------------------------------------------------
if ($DeploymentMode -eq "full" -or $DeploymentMode -eq "network-mcp") {
    Write-Host "`n--- DevOps Agent Integration ---" -ForegroundColor Magenta

    $devopsDir = Join-Path $PSScriptRoot "devops-integration"
    $devopsIntegrationCdkDir = Join-Path $devopsDir "infrastructure\cdk"
    if (Test-Path $devopsIntegrationCdkDir) {
        # Step 1: Build the MCP handler with esbuild
        Write-Host "`nBuilding MCP handler (esbuild)..." -ForegroundColor Yellow
        Push-Location $devopsDir
        $prevEAP3 = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        $esbuildOutput = npx esbuild src/lambda/mcp-handler.ts --bundle --platform=node --target=node20 --outfile=dist/mcp-handler.js "--external:@aws-sdk/client-bedrock-agent-runtime" 2>&1
        $esbuildExit = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP3
        if ($esbuildExit -ne 0) {
            Write-Host "  WARNING: esbuild failed. Skipping DevOps Agent Integration." -ForegroundColor Yellow
            Pop-Location
        } else {
            Write-Host "      OK: MCP handler built (dist/mcp-handler.js)" -ForegroundColor Green
            Pop-Location

            # Step 2: Deploy the CDK stack
            Write-Host "`nDeploying GOATDevOpsIntegration-$region..." -ForegroundColor Yellow
            Write-Host "      (MCP server endpoint, IAM role, and DevOps Agent registration)" -ForegroundColor Gray
            & "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory $devopsIntegrationCdkDir -StackName "GOATDevOpsIntegration-$region" -SkipBootstrap
            if ($LASTEXITCODE -ne 0) {
                Write-Host "  WARNING: DevOps Agent Integration deployment failed (non-fatal)." -ForegroundColor Yellow
                Write-Host "  The core GOAT solution is deployed. DevOps Agent integration can be deployed separately." -ForegroundColor Yellow
            }

            # Step 3: Retrieve MCP endpoint from stack outputs
            $prevEAP2 = $ErrorActionPreference; $ErrorActionPreference = "Continue"
            $devopsStackName = "GOATDevOpsIntegration-$region"
            $mcpEndpointUrl = aws cloudformation describe-stacks --stack-name $devopsStackName --query "Stacks[0].Outputs[?OutputKey=='McpEndpointUrl'].OutputValue" --output text --no-cli-pager 2>$null
            $healthCheckUrl = aws cloudformation describe-stacks --stack-name $devopsStackName --query "Stacks[0].Outputs[?OutputKey=='HealthCheckUrl'].OutputValue" --output text --no-cli-pager 2>$null
            $ErrorActionPreference = $prevEAP2
        }
    } else {
        Write-Host "  DevOps integration directory not found - skipping." -ForegroundColor DarkGray
    }
}

# ---------------------------------------------------------------------------
# 6. Retrieve stack outputs for frontend build
# Skipped entirely under network-mcp mode: no GOATAuth/GOATData/GOATFrontend
# stack is deployed, so there is no Cognito configuration or frontend-facing
# agent runtime ARN to retrieve.
# ---------------------------------------------------------------------------
if ($DeploymentMode -ne "network-mcp") {
    Write-Host "`n--- Retrieving Stack Outputs ---" -ForegroundColor Magenta

    $userPoolId = aws cloudformation describe-stacks --stack-name "GOATAuth-$region" --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text --no-cli-pager
    $userPoolClientId = aws cloudformation describe-stacks --stack-name "GOATAuth-$region" --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" --output text --no-cli-pager
    $identityPoolId = aws cloudformation describe-stacks --stack-name "GOATAuth-$region" --query "Stacks[0].Outputs[?OutputKey=='IdentityPoolId'].OutputValue" --output text --no-cli-pager

    if ([string]::IsNullOrEmpty($userPoolId) -or [string]::IsNullOrEmpty($userPoolClientId) -or [string]::IsNullOrEmpty($identityPoolId)) {
        Write-Host "Failed to retrieve Cognito configuration from GOATAuth-$region stack outputs" -ForegroundColor Red
        exit 1
    }

    # Retrieve orchestration agent ARN (full mode) or first available sub-agent ARN (single module)
    $agentRuntimeArn = ""
    if ($DeploymentMode -eq "full") {
        $agentRuntimeArn = aws cloudformation describe-stacks --stack-name "GOATOrchRuntime-$region" --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager
    } elseif ($DeploymentMode -eq "network") {
        # In network mode, use the Network Agent runtime ARN
        $agentRuntimeArn = aws cloudformation describe-stacks --stack-name "GOATNetworkRuntime-$region" --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager
    } else {
        # In single-module mode, use the deployed module's runtime ARN
        $moduleStackName = "GOAT$($deployModules[0])Runtime-$region"
        $agentRuntimeArn = aws cloudformation describe-stacks --stack-name $moduleStackName --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager
    }

    if ([string]::IsNullOrEmpty($agentRuntimeArn)) {
        Write-Host "Failed to retrieve Agent Runtime ARN from stack outputs" -ForegroundColor Red
        exit 1
    }

    Write-Host "      User Pool ID:        $userPoolId" -ForegroundColor Green
    Write-Host "      User Pool Client ID:  $userPoolClientId" -ForegroundColor Green
    Write-Host "      Identity Pool ID:     $identityPoolId" -ForegroundColor Green
    Write-Host "      Agent Runtime ARN:    $agentRuntimeArn" -ForegroundColor Green
    Write-Host "      Region:               $region" -ForegroundColor Green
} else {
    Write-Host "`nSkipping stack output retrieval for frontend build (network-mcp mode has no Frontend/Auth stack)..." -ForegroundColor DarkGray
}

# Retrieve Network Agent runtime ARN when applicable
$networkAgentArn = ""
if ($DeploymentMode -eq "full" -or $DeploymentMode -eq "network" -or $DeploymentMode -eq "network-mcp") {
    $networkAgentArn = aws cloudformation describe-stacks --stack-name "GOATNetworkRuntime-$region" --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager
    if ([string]::IsNullOrEmpty($networkAgentArn)) {
        Write-Host "Failed to retrieve Network Agent Runtime ARN from GOATNetworkRuntime-$region stack outputs" -ForegroundColor Red
        exit 1
    }
    Write-Host "      Network Agent ARN:    $networkAgentArn" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 7. Build frontend with retrieved outputs
# Skipped entirely under network-mcp mode: there is no Frontend stack to
# build assets for.
# ---------------------------------------------------------------------------
if ($DeploymentMode -ne "network-mcp") {
    Write-Host "`n--- Building Frontend ---" -ForegroundColor Magenta
    Write-Host "      (Injecting Cognito config and Agent Runtime ARN, building React app)" -ForegroundColor Gray

    & .\scripts\build-frontend.ps1 -UserPoolId $userPoolId -UserPoolClientId $userPoolClientId -IdentityPoolId $identityPoolId -AgentRuntimeArn $agentRuntimeArn -Region $region

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Frontend build failed" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "`nSkipping frontend build (network-mcp mode has no Frontend stack)..." -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# 8. Deploy Frontend Stack (always last)
# Skipped entirely under network-mcp mode: no Frontend stack is deployed
# under this mode.
# ---------------------------------------------------------------------------
if ($DeploymentMode -ne "network-mcp") {
    Write-Host "`n--- Frontend Stack ---" -ForegroundColor Magenta

    Deploy-Stack -StackName "GOATFrontend-$region" `
        -Description "Deploying React app to S3 + CloudFront with OAC" `
        -SkipBootstrap
} else {
    Write-Host "`nSkipping Frontend Stack deployment (network-mcp mode has no Frontend stack)..." -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# 9. Deployment Summary
# ---------------------------------------------------------------------------
# Skipped entirely under network-mcp mode: no GOATFrontend stack is deployed
# in this mode, so there is no Website URL to retrieve.
$websiteUrl = ""
if ($DeploymentMode -ne "network-mcp") {
    $websiteUrl = aws cloudformation describe-stacks --stack-name "GOATFrontend-$region" --query "Stacks[0].Outputs[?OutputKey=='WebsiteUrl'].OutputValue" --output text --no-cli-pager

    if ([string]::IsNullOrEmpty($websiteUrl)) {
        Write-Host "Failed to retrieve Website URL from GOATFrontend-$region stack outputs" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  G.O.A.T. Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
if ($DeploymentMode -eq "network-mcp") {
    # network-mcp has no Website URL / Agent Runtime ARN (Frontend/Orchestrator) /
    # User Pool ID (Auth) to show -- print the MCP endpoint and Network Agent
    # runtime ARN as the primary "how to use this" output instead.
    Write-Host "  Deployment Mode:      $DeploymentMode" -ForegroundColor Cyan
    Write-Host "  Region:               $region" -ForegroundColor Cyan
    Write-Host "  Network Agent ARN:    $networkAgentArn" -ForegroundColor Cyan
    if (-not [string]::IsNullOrEmpty($mcpEndpointUrl)) {
        Write-Host "  MCP Endpoint:         $mcpEndpointUrl" -ForegroundColor Cyan
        Write-Host "  Health Check:         $healthCheckUrl" -ForegroundColor Cyan
    }
    Write-Host ""
    Write-Host "  No Cognito sign-in is required for this mode (Auth_Scope_Boundary) -" -ForegroundColor Yellow
    Write-Host "  register the MCP Endpoint above with your DevOps Agent to start using" -ForegroundColor Yellow
    Write-Host "  the six network diagnostic tools." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Next Steps:" -ForegroundColor Yellow
    Write-Host "    1. Register the MCP Endpoint above with the AWS DevOps Agent" -ForegroundColor Gray
    Write-Host "    2. Try a query like: 'Run a TCP traceroute from i-xxxx to example.com'" -ForegroundColor Gray
    Write-Host "    3. To add the chat UI and orchestrator later, re-run with -DeploymentMode full" -ForegroundColor Gray
} else {
    Write-Host "  Website URL:          $websiteUrl" -ForegroundColor Cyan
    Write-Host "  Deployment Mode:      $DeploymentMode" -ForegroundColor Cyan
    Write-Host "  Region:               $region" -ForegroundColor Cyan
    Write-Host "  Agent Runtime ARN:    $agentRuntimeArn" -ForegroundColor Cyan
    Write-Host "  User Pool ID:         $userPoolId" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Deployed Modules: $($deployModules -join ', ')" -ForegroundColor Cyan
    if ($DeploymentMode -eq "full" -or $DeploymentMode -eq "network") {
        Write-Host "  Network Agent:        Deployed (BedrockAgentCoreApp + Nova Lite)" -ForegroundColor Cyan
        Write-Host "  Network Agent ARN:    $networkAgentArn" -ForegroundColor Cyan
    }
    if ($DeploymentMode -eq "full") {
        Write-Host "  Orchestration Agent:  Deployed (Strands Agent SDK + Nova Pro)" -ForegroundColor Cyan
    }
    if ($DeploymentMode -eq "full" -and -not [string]::IsNullOrEmpty($mcpEndpointUrl)) {
        Write-Host "  MCP Endpoint:         $mcpEndpointUrl" -ForegroundColor Cyan
        Write-Host "  Health Check:         $healthCheckUrl" -ForegroundColor Cyan
    }
    if (-not [string]::IsNullOrEmpty($OrchModelId)) {
        Write-Host "  Orchestration Model:  $OrchModelId" -ForegroundColor Cyan
    }
    Write-Host ""
    Write-Host "  Next Steps:" -ForegroundColor Yellow
    Write-Host "    1. Create an admin user with full permissions (copy-paste all commands):" -ForegroundColor Gray
    Write-Host ""
    Write-Host "       aws cognito-idp admin-create-user --user-pool-id $userPoolId --username admin --user-attributes Name=email,Value=admin@company.com Name=email_verified,Value=true --message-action SUPPRESS" -ForegroundColor White
    Write-Host ""
    Write-Host "       aws cognito-idp admin-set-user-password --user-pool-id $userPoolId --username admin --password ""YourSecurePassword123!"" --permanent" -ForegroundColor White
    Write-Host ""
    if ($DeploymentMode -eq "network" -or $DeploymentMode -eq "full") {
        Write-Host "       aws cognito-idp admin-add-user-to-group --user-pool-id $userPoolId --username admin --group-name GOATNetworkCaptureUsers" -ForegroundColor White
        Write-Host ""
    }
    Write-Host "       (Replace the email and password with your own values)" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "    2. Sign in at the Website URL above with your created admin credentials" -ForegroundColor Gray
    Write-Host "    3. Try a query like: 'What are my top cost optimization opportunities?'" -ForegroundColor Gray
    if ($DeploymentMode -eq "network" -or $DeploymentMode -eq "full") {
        Write-Host "    4. For packet captures: 'Start a capture on eni-xxx' (requires GOATNetworkCaptureUsers group)" -ForegroundColor Gray
    }
    if ($DeploymentMode -ne "full") {
        Write-Host "    5. To add more modules later, re-run with -DeploymentMode full" -ForegroundColor Gray
    }
}
Write-Host ""
