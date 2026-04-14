#!/usr/bin/env pwsh
# =============================================================================
# DevOps Agent EKS Demo Platform - Zero-Touch Deployment Script (PowerShell)
# =============================================================================
# Deploys the entire DevOps Agent EKS Demo stack with no manual intervention:
#   1. Install CDK dependencies and upload CodeBuild sources to S3
#   2. Deploy CDK stacks (includes RDS credentials secret)
#   3. Configure kubectl for EKS
#   4. Build and push container images via CodeBuild
#   5. Apply Kubernetes manifests
#   6. Create Cognito user & run DB migrations (before pod wait)
#   7. Wait for pods and NLB
#   8. Update CloudFront with NLB API origin via CDK
#   9. Build and deploy frontend
# =============================================================================

param(
    [string]$Environment = "dev",
    [string]$ProjectName = "devops-agent-eks"
)

$ErrorActionPreference = "Stop"

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " DevOps Agent EKS Demo - Automated Deployment" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
Write-Host "[prereqs] Running prerequisites check..." -ForegroundColor Yellow
& "$PSScriptRoot\..\..\shared\scripts\check-prerequisites.ps1" -RequireCDK -RequireKubectl -SkipServiceCheck -MinAwsCliVersion "2.34.21"
if ($LASTEXITCODE -ne 0) { exit 1 }
Write-Host ""

$AWS_ACCOUNT_ID = aws sts get-caller-identity --query Account --output text
$AWS_REGION = $global:AWS_REGION

Write-Host "Account:     $AWS_ACCOUNT_ID"
Write-Host "Region:      $AWS_REGION"
Write-Host "Environment: $Environment"
Write-Host ""

# ---------------------------------------------------------------------------
# DevOps Agent setup (Agent Space + webhook)
# ---------------------------------------------------------------------------
# Agent Space is created via CLI in the DevOps Agent region (e.g. us-east-1)
# because AWS::DevOpsAgent CloudFormation resources are not available in all regions.
if (-not $env:DEVOPS_AGENT_WEBHOOK_URL -or -not $env:DEVOPS_AGENT_WEBHOOK_SECRET) {
    & "$PSScriptRoot\scripts\setup-devops-agent.ps1" -AgentSpaceName $ProjectName
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: DevOps Agent setup failed." -ForegroundColor Red
        exit 1
    }
}

if (-not $env:DEVOPS_AGENT_WEBHOOK_URL -or -not $env:DEVOPS_AGENT_WEBHOOK_SECRET) {
    Write-Host ""
    Write-Host "ERROR: DevOps Agent webhook URL and secret are required." -ForegroundColor Red
    Write-Host ""
    Write-Host "Either provide them as environment variables before running this script:"
    Write-Host '  $env:DEVOPS_AGENT_WEBHOOK_URL = "https://..."'
    Write-Host '  $env:DEVOPS_AGENT_WEBHOOK_SECRET = "..."'
    Write-Host "  .\deploy-all.ps1"
    Write-Host ""
    exit 1
}

$DevOpsWebhookUrl = $env:DEVOPS_AGENT_WEBHOOK_URL
$DevOpsWebhookSecret = $env:DEVOPS_AGENT_WEBHOOK_SECRET
$DevOpsAgentSpaceId = $env:DEVOPS_AGENT_SPACE_ID ?? ''
$DevOpsAgentRegion = $env:DEVOPS_AGENT_REGION ?? 'us-east-1'
Write-Host "DevOps Agent webhook: CONFIGURED" -ForegroundColor Green
Write-Host ""

# EKS node architecture — override via EKS_ARCHITECTURE env var, default arm64
# (matches the CloudFormation EksNodeArchitecture parameter default)
# ---------------------------------------------------------------------------
$EksArchitecture = if ($env:EKS_ARCHITECTURE) { $env:EKS_ARCHITECTURE } else { "arm64" }
switch ($EksArchitecture) {
    "arm64" { $EksInstanceType = "t4g.medium" }
    "amd64" { $EksInstanceType = "t3.medium" }
    default {
        Write-Host "ERROR: Invalid EKS_ARCHITECTURE '$EksArchitecture'. Must be arm64 or amd64." -ForegroundColor Red
        exit 1
    }
}
Write-Host "EKS architecture: $EksArchitecture"
Write-Host "EKS instance:     $EksInstanceType"
Write-Host ""

$ECR_REGISTRY = "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

# =============================================================================
# STEP 1: Install CDK dependencies and create S3 bucket for CodeBuild sources
# =============================================================================
Write-Host "[1/9] Installing CDK dependencies and preparing S3 bucket..." -ForegroundColor Cyan
$BUCKET_NAME = "$ProjectName-cfn-templates-$AWS_ACCOUNT_ID"
aws s3 mb "s3://$BUCKET_NAME" --region $AWS_REGION 2>$null

Push-Location cdk; npm install; Pop-Location
Write-Host "  done."
Write-Host ""

# =============================================================================
# STEP 2: Deploy CDK stacks (initial, no API endpoint yet)
# =============================================================================
Write-Host "[2/9] Deploying CDK stacks (this takes ~15 minutes)..." -ForegroundColor Cyan

Push-Location cdk
$cdkArgs = @(
    "npx", "cdk", "deploy", "--all",
    "-c", "environment=$Environment",
    "-c", "projectName=$ProjectName",
    "-c", "eksNodeArchitecture=$EksArchitecture",
    "-c", "eksNodeInstanceType=$EksInstanceType",
    "-c", "eksNodeDesiredCapacity=2",
    "-c", "devOpsAgentWebhookUrl=$DevOpsWebhookUrl",
    "-c", "devOpsAgentWebhookSecret=$DevOpsWebhookSecret",
    "-c", "devOpsAgentRegion=$DevOpsAgentRegion",
    "-c", "devOpsAgentSpaceId=$DevOpsAgentSpaceId",
    "--require-approval", "never",
    "--no-cli-pager"
)
& $cdkArgs[0] $cdkArgs[1..($cdkArgs.Length-1)]
Pop-Location
Write-Host "  Infrastructure deployed."
Write-Host ""

# =============================================================================
# STEP 3: Configure kubectl for EKS
# =============================================================================
Write-Host "[3/9] Configuring kubectl..." -ForegroundColor Cyan
aws eks update-kubeconfig `
    --name "$ProjectName-$Environment-cluster" `
    --region $AWS_REGION
Write-Host "  kubectl configured."

# Grant the deploying IAM principal cluster-admin access via EKS access entries
$CallerArn = aws sts get-caller-identity --query Arn --output text
# Convert assumed-role ARN to IAM role ARN (EKS access entries need the role ARN)
# arn:aws:sts::123:assumed-role/RoleName/session → arn:aws:iam::123:role/RoleName
if ($CallerArn -match 'assumed-role/([^/]+)/') {
    $RoleName = $Matches[1]
    $CallerArn = "arn:aws:iam::${AWS_ACCOUNT_ID}:role/$RoleName"
}
Write-Host "  Granting EKS access to $CallerArn..."
aws eks create-access-entry `
    --cluster-name "$ProjectName-$Environment-cluster" `
    --principal-arn $CallerArn `
    --type STANDARD `
    --region $AWS_REGION 2>$null | Out-Null
aws eks associate-access-policy `
    --cluster-name "$ProjectName-$Environment-cluster" `
    --principal-arn $CallerArn `
    --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy `
    --access-scope type=cluster `
    --region $AWS_REGION 2>$null | Out-Null
Write-Host "  EKS access granted."

# Grant Failure Simulator Lambda access to EKS cluster
$FailureSimLambdaRoleArn = aws cloudformation describe-stacks `
    --stack-name "DevOpsAgentEksFailureSimulatorApi-$AWS_REGION" `
    --query "Stacks[0].Outputs[?OutputKey=='FailureSimulatorLambdaRoleArn'].OutputValue" `
    --output text --region $AWS_REGION 2>$null
if ($FailureSimLambdaRoleArn -and $FailureSimLambdaRoleArn -ne "None") {
    Write-Host "  Granting EKS access to Failure Simulator Lambda ($FailureSimLambdaRoleArn)..."
    aws eks create-access-entry `
        --cluster-name "$ProjectName-$Environment-cluster" `
        --principal-arn $FailureSimLambdaRoleArn `
        --type STANDARD `
        --region $AWS_REGION 2>$null | Out-Null
    aws eks associate-access-policy `
        --cluster-name "$ProjectName-$Environment-cluster" `
        --principal-arn $FailureSimLambdaRoleArn `
        --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSEditPolicy `
        --access-scope type=namespace,namespaces=payment-demo,kube-system `
        --region $AWS_REGION 2>$null | Out-Null
    Write-Host "  Failure Simulator Lambda EKS access granted (namespaces: payment-demo, kube-system)."
}
Write-Host ""

# Wait for EKS API authentication to propagate (access entries are eventually consistent)
Write-Host "  Waiting for EKS API access to propagate..."
for ($i = 1; $i -le 30; $i++) {
    $null = kubectl get ns default 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  EKS API access confirmed."
        break
    }
    if ($i -eq 30) {
        Write-Host "  WARNING: EKS API access not confirmed after 150 seconds. Continuing anyway..." -ForegroundColor Yellow
    }
    Start-Sleep -Seconds 5
}
Write-Host ""

# Wait for nodes to be ready
Write-Host "  Waiting for EKS nodes to be Ready..."
for ($i = 1; $i -le 60; $i++) {
    $readyNodes = (kubectl get nodes --no-headers 2>$null | Select-String " Ready" | Measure-Object).Count
    if ($readyNodes -ge 1) {
        Write-Host "  $readyNodes node(s) ready."
        break
    }
    if ($i -eq 60) {
        Write-Host "  WARNING: Nodes not ready after 5 minutes. Continuing anyway..." -ForegroundColor Yellow
    }
    Start-Sleep -Seconds 5
}
Write-Host ""

# =============================================================================
# STEP 4: Build and push container images via CodeBuild
# =============================================================================
Write-Host "[4/9] Building and pushing container images via CodeBuild..." -ForegroundColor Cyan

$IMAGE_TAG = $Environment
$Services = @("merchant-gateway", "payment-processor", "webhook-service")

# --- 5a: Zip each service source directory and upload to S3 ---
Write-Host "  Packaging service source bundles..."
$ExcludeDirs = @("node_modules", "dist", "target", ".git")
$ExcludeFilePatterns = @("*.test.*", "jest.config.*", ".jqwik-database")

foreach ($Service in $Services) {
    Write-Host "    Zipping $Service..."
    $ServicePath = "services/$Service"
    $TmpZip = Join-Path $env:TEMP "$Service.zip"
    if (Test-Path $TmpZip) { Remove-Item $TmpZip -Force }

    # Build list of items to include (exclude unwanted dirs and file patterns)
    $StagingDir = Join-Path $env:TEMP "codebuild-staging-$Service"
    if (Test-Path $StagingDir) { Remove-Item $StagingDir -Recurse -Force }
    New-Item -ItemType Directory -Path $StagingDir -Force | Out-Null

    # Use robocopy to mirror the service dir while excluding unwanted directories
    $RobocopyExcludes = $ExcludeDirs | ForEach-Object { "/XD"; $_ }
    $RobocopyFileExcludes = $ExcludeFilePatterns | ForEach-Object { "/XF"; $_ }
    $robocopyArgs = @($ServicePath, $StagingDir, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP") + $RobocopyExcludes + $RobocopyFileExcludes
    & robocopy @robocopyArgs | Out-Null
    # robocopy returns exit codes 0-7 for success; 8+ for errors
    if ($LASTEXITCODE -ge 8) {
        Write-Host "  ERROR: Failed to stage $Service source files." -ForegroundColor Red
        exit 1
    }
    $LASTEXITCODE = 0  # Reset since robocopy uses non-zero for success

    Compress-Archive -Path "$StagingDir\*" -DestinationPath $TmpZip -Force
    Remove-Item $StagingDir -Recurse -Force

    Write-Host "    Uploading $Service.zip to S3..."
    aws s3 cp $TmpZip "s3://$BUCKET_NAME/codebuild-sources/$Service.zip" `
        --region $AWS_REGION | Out-Null
    Remove-Item $TmpZip -Force
}
Write-Host "  Source bundles uploaded."

# --- 5b: Get CodeBuild project names from CloudFormation outputs ---
Write-Host "  Retrieving CodeBuild project names..."
$CbProjects = @{}
$OutputKeyMap = @{
    "merchant-gateway"   = "MerchantGatewayBuildProject"
    "payment-processor"  = "PaymentProcessorBuildProject"
    "webhook-service"    = "WebhookServiceBuildProject"
}

foreach ($Service in $Services) {
    $OutputKey = $OutputKeyMap[$Service]
    $ProjectVal = aws cloudformation describe-stacks `
        --stack-name "DevOpsAgentEksPipeline-$AWS_REGION" `
        --query "Stacks[0].Outputs[?OutputKey=='$OutputKey'].OutputValue" `
        --output text --region $AWS_REGION
    if (-not $ProjectVal -or $ProjectVal -eq "None") {
        Write-Host "  ERROR: Could not find CodeBuild project name for $Service." -ForegroundColor Red
        Write-Host "  Make sure the CloudFormation stack deployed successfully."
        exit 1
    }
    $CbProjects[$Service] = $ProjectVal
    Write-Host "    $Service -> $ProjectVal"
}

# --- 5c: Start CodeBuild builds ---
Write-Host "  Starting CodeBuild builds..."
$BuildIds = @{}
foreach ($Service in $Services) {
    # payment-processor is a Java/Maven project — its own buildspec.yml runs
    # 'mvn clean package' before 'docker build'. Override the inline buildspec
    # so CodeBuild uses the buildspec.yml from the S3 source zip.
    $buildspecArgs = @()
    if ($Service -eq "payment-processor") {
        $buildspecArgs = @("--buildspec-override", "buildspec.yml")
    }

    $BuildId = aws codebuild start-build `
        --project-name $CbProjects[$Service] `
        --source-type-override S3 `
        --source-location-override "$BUCKET_NAME/codebuild-sources/$Service.zip" `
        @buildspecArgs `
        --environment-variables-override "name=IMAGE_TAG,value=$IMAGE_TAG,type=PLAINTEXT" `
        --query 'build.id' --output text `
        --region $AWS_REGION
    $BuildIds[$Service] = $BuildId
    Write-Host "    Started ${Service}: $BuildId"
}

# --- 5d: Poll builds until all complete ---
Write-Host ""
Write-Host "  Waiting for builds to complete..."
$BuildStatus = @{}
$BuildPhase = @{}
$BuildDone = @{}
$PollStart = Get-Date

foreach ($Service in $Services) {
    $BuildDone[$Service] = $false
    $BuildStatus[$Service] = "IN_PROGRESS"
    $BuildPhase[$Service] = "SUBMITTED"
}

while ($true) {
    # Collect pending build IDs
    $PendingIds = @()
    foreach ($Service in $Services) {
        if (-not $BuildDone[$Service]) {
            $PendingIds += $BuildIds[$Service]
        }
    }
    if ($PendingIds.Count -eq 0) { break }

    # Batch query — use Invoke-Expression to handle single/multiple IDs correctly
    $IdsString = ($PendingIds | ForEach-Object { "`"$_`"" }) -join " "
    $BatchRaw = Invoke-Expression "aws codebuild batch-get-builds --ids $IdsString --query 'builds[].[id,buildStatus,currentPhase]' --output json --region $AWS_REGION"
    $BatchJson = $BatchRaw | ConvertFrom-Json

    # Handle single result (not wrapped in outer array)
    if ($BatchJson -and $BatchJson.Count -gt 0 -and $BatchJson[0] -is [string]) {
        $BatchJson = @(,$BatchJson)
    }

    foreach ($Row in $BatchJson) {
        $BId = $Row[0]
        $BStatus = $Row[1]
        $BPhase = $Row[2]
        foreach ($Service in $Services) {
            if ($BuildIds[$Service] -eq $BId) {
                $BuildPhase[$Service] = $BPhase
                if ($BStatus -in @("SUCCEEDED", "FAILED", "FAULT", "TIMED_OUT", "STOPPED", "COMPLETED")) {
                    $BuildDone[$Service] = $true
                    $BuildStatus[$Service] = $BStatus
                }
                break
            }
        }
    }

    # Print status line
    $Elapsed = (Get-Date) - $PollStart
    $ElapsedStr = "{0}m{1}s" -f [int]$Elapsed.TotalMinutes, $Elapsed.Seconds
    $StatusLine = "    [$ElapsedStr]"
    foreach ($Service in $Services) {
        if ($BuildDone[$Service]) {
            $StatusLine += "  ${Service}:$($BuildStatus[$Service])"
        } else {
            $StatusLine += "  ${Service}:$($BuildPhase[$Service])"
        }
    }
    Write-Host $StatusLine

    # Check if all done
    $AllDone = $true
    foreach ($Service in $Services) {
        if (-not $BuildDone[$Service]) { $AllDone = $false; break }
    }
    if ($AllDone) { break }

    Start-Sleep -Seconds 15
}

# --- 5e: Report results ---
Write-Host ""
$Failed = $false
foreach ($Service in $Services) {
    $Status = $BuildStatus[$Service]
    $BId = $BuildIds[$Service]
    $CbProject = $CbProjects[$Service]

    if ($Status -eq "SUCCEEDED") {
        $EcrUri = "$ECR_REGISTRY/$ProjectName/$Service"
        Write-Host "  [OK] ${Service}: SUCCEEDED - image ${EcrUri}:$IMAGE_TAG" -ForegroundColor Green
    } else {
        $Failed = $true
        $LogGroup = "/aws/codebuild/$CbProject"
        $EncodedLogGroup = $LogGroup -replace '/', '$252F'
        $LogsUrl = "https://${AWS_REGION}.console.aws.amazon.com/cloudwatch/home?region=${AWS_REGION}#logsV2:log-groups/log-group/${EncodedLogGroup}"
        Write-Host "  [FAIL] ${Service}: $Status" -ForegroundColor Red
        Write-Host "     Build ID: $BId"
        Write-Host "     Logs: $LogsUrl"
    }
}

if ($Failed) {
    Write-Host ""
    Write-Host "  ERROR: One or more CodeBuild builds failed. See logs above." -ForegroundColor Red
    exit 1
}

Write-Host "  All container images built and pushed successfully."
Write-Host ""

# =============================================================================
# STEP 5: Apply Kubernetes manifests (with dynamic substitution)
# =============================================================================
Write-Host "[5/9] Applying Kubernetes manifests..." -ForegroundColor Cyan

# Create namespace
kubectl create namespace payment-demo --dry-run=client -o yaml | kubectl apply -f -

# Get RDS endpoint
$RDS_ENDPOINT = aws rds describe-db-instances `
    --db-instance-identifier "$ProjectName-$Environment-postgres" `
    --query 'DBInstances[0].Endpoint.Address' `
    --output text `
    --region $AWS_REGION
Write-Host "  RDS endpoint: $RDS_ENDPOINT"

# Get DB password from Secrets Manager (native PowerShell JSON parsing)
$SECRET_JSON = aws secretsmanager get-secret-value `
    --secret-id "$ProjectName-$Environment-rds-credentials" `
    --query SecretString `
    --output text `
    --region $AWS_REGION
$DB_PASSWORD = ($SECRET_JSON | ConvertFrom-Json).password

# Create K8s secret for DB credentials
kubectl create secret generic db-credentials `
    --from-literal=DB_HOST=$RDS_ENDPOINT `
    --from-literal=DB_USERNAME=paymentadmin `
    --from-literal=DB_PASSWORD=$DB_PASSWORD `
    --from-literal=DB_NAME=paymentdb `
    -n payment-demo --dry-run=client -o yaml | kubectl apply -f -

# Substitute placeholders and apply, then restore in a finally block
$configmapFile = "k8s/base/configmap.yaml"
$kustomizationFile = "k8s/overlays/$Environment/kustomization.yaml"

$configmapOriginal = Get-Content $configmapFile -Raw
$kustomizationOriginal = Get-Content $kustomizationFile -Raw

try {
    # Fetch Cognito values from CloudFormation outputs
    $COGNITO_USER_POOL_ID = aws cloudformation describe-stacks `
        --stack-name "DevOpsAgentEksAuth-$AWS_REGION" `
        --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" `
        --output text --region $AWS_REGION
    $COGNITO_CLIENT_ID = aws cloudformation describe-stacks `
        --stack-name "DevOpsAgentEksAuth-$AWS_REGION" `
        --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" `
        --output text --region $AWS_REGION

    # Substitute placeholders in configmap
    Write-Host "  Patching configmap placeholders..."
    $configmapPatched = $configmapOriginal `
        -replace '__AWS_REGION__', $AWS_REGION `
        -replace '__RDS_ENDPOINT__', $RDS_ENDPOINT `
        -replace '__COGNITO_USER_POOL_ID__', $COGNITO_USER_POOL_ID `
        -replace '__COGNITO_CLIENT_ID__', $COGNITO_CLIENT_ID
    Set-Content -Path $configmapFile -Value $configmapPatched -NoNewline

    # Substitute placeholders in kustomization overlay
    Write-Host "  Patching kustomization overlay placeholders..."
    $kustomizationPatched = $kustomizationOriginal -replace '__ACCOUNT_ID__', $AWS_ACCOUNT_ID -replace '__AWS_REGION__', $AWS_REGION
    Set-Content -Path $kustomizationFile -Value $kustomizationPatched -NoNewline

    # Apply kustomize
    kubectl apply -k "k8s/overlays/$Environment"
} finally {
    # Restore placeholders so git stays clean
    Set-Content -Path $configmapFile -Value $configmapOriginal -NoNewline
    Set-Content -Path $kustomizationFile -Value $kustomizationOriginal -NoNewline
}

Write-Host "  Kubernetes manifests applied."
Write-Host ""

# Deploy Fluent Bit (log shipping to CloudWatch)
Write-Host "  Deploying Fluent Bit DaemonSet..."

$fluentBitSAFile = "k8s/base/fluent-bit/service-account.yaml"
$fluentBitCMFile = "k8s/base/fluent-bit/configmap.yaml"
$fluentBitSAOriginal = Get-Content -Path $fluentBitSAFile -Raw
$fluentBitCMOriginal = Get-Content -Path $fluentBitCMFile -Raw

try {
    $fluentBitSAContent = $fluentBitSAOriginal `
        -replace '__ACCOUNT_ID__', $AWS_ACCOUNT_ID `
        -replace '__AWS_REGION__', $AWS_REGION `
        -replace '__ENVIRONMENT__', $ENVIRONMENT
    Set-Content -Path $fluentBitSAFile -Value $fluentBitSAContent -NoNewline

    $fluentBitCMContent = $fluentBitCMOriginal `
        -replace '__AWS_REGION__', $AWS_REGION `
        -replace '__ENVIRONMENT__', $ENVIRONMENT
    Set-Content -Path $fluentBitCMFile -Value $fluentBitCMContent -NoNewline

    kubectl apply -f k8s/base/fluent-bit/
} finally {
    # Restore placeholders so git stays clean
    Set-Content -Path $fluentBitSAFile -Value $fluentBitSAOriginal -NoNewline
    Set-Content -Path $fluentBitCMFile -Value $fluentBitCMOriginal -NoNewline
}

Write-Host "  Fluent Bit deployed."
Write-Host ""

# Restart deployments to pick up latest images
Write-Host "  Restarting deployments to pull latest images..."
kubectl rollout restart deployment/merchant-gateway -n payment-demo
kubectl rollout restart deployment/payment-processor -n payment-demo
kubectl rollout restart deployment/webhook-service -n payment-demo
Write-Host ""

# =============================================================================
# STEP 6: Create Cognito user & run DB migrations (BEFORE pod wait)
# =============================================================================
Write-Host "[6/9] Creating Cognito user and running database migrations..." -ForegroundColor Cyan

# --- 7a: Create Cognito demo user FIRST so we can capture its sub UUID ---
$USER_POOL_ID = aws cloudformation describe-stacks `
    --stack-name "DevOpsAgentEksAuth-$AWS_REGION" `
    --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" `
    --output text `
    --region $AWS_REGION

$COGNITO_SUB = ""
if ($USER_POOL_ID -and $USER_POOL_ID -ne "None") {
    Write-Host "  User Pool ID: $USER_POOL_ID"
    $DEMO_USERNAME = "demo-merchant-1"
    $DEMO_EMAIL = "demo@helios-electronics.com"
    $DEMO_PASSWORD = "DemoPass2026!"
    # MERCHANT_ID will be set to COGNITO_SUB after we retrieve it.
    # The payment-processor uses the JWT sub directly as merchant_id FK,
    # so the merchants.id column MUST equal the Cognito sub UUID.

    # Check if user already exists
    # NOTE: $LASTEXITCODE is unreliable with 2>$null in PowerShell.
    # Instead, capture stderr via 2>&1 and check for error text.
    $userExists = $false
    $userCheck = aws cognito-idp admin-get-user `
        --user-pool-id $USER_POOL_ID `
        --username $DEMO_USERNAME `
        --no-cli-pager 2>&1
    if ($userCheck -notmatch "UserNotFoundException") { $userExists = $true }

    if (-not $userExists) {
        Write-Host "  Creating Cognito user '$DEMO_USERNAME'..."
        aws cognito-idp admin-create-user `
            --user-pool-id $USER_POOL_ID `
            --username $DEMO_USERNAME `
            --user-attributes Name=email,Value=$DEMO_EMAIL Name=email_verified,Value=true `
            --temporary-password $DEMO_PASSWORD `
            --message-action SUPPRESS --no-cli-pager | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  ERROR: Failed to create Cognito user '$DEMO_USERNAME'." -ForegroundColor Red
            exit 1
        }

        aws cognito-idp admin-set-user-password `
            --user-pool-id $USER_POOL_ID `
            --username $DEMO_USERNAME `
            --password $DEMO_PASSWORD `
            --permanent --no-cli-pager | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  ERROR: Failed to set permanent password for '$DEMO_USERNAME'." -ForegroundColor Red
            exit 1
        }
        Write-Host "  Cognito user created."
    } else {
        Write-Host "  Cognito user '$DEMO_USERNAME' already exists."
        # Ensure password is set correctly (handles redeployments)
        aws cognito-idp admin-set-user-password `
            --user-pool-id $USER_POOL_ID `
            --username $DEMO_USERNAME `
            --password $DEMO_PASSWORD `
            --permanent 2>$null | Out-Null
    }

    # Capture the Cognito sub UUID (this is what appears in JWT access tokens)
    $COGNITO_SUB = aws cognito-idp admin-get-user `
        --user-pool-id $USER_POOL_ID `
        --username $DEMO_USERNAME `
        --query "UserAttributes[?Name=='sub'].Value" `
        --output text `
        --region $AWS_REGION
    Write-Host "  Cognito sub: $COGNITO_SUB"
} else {
    Write-Host "  WARNING: Could not find Cognito User Pool ID." -ForegroundColor Yellow
}

# Fallback if we couldn't get the sub
if (-not $COGNITO_SUB) {
    $COGNITO_SUB = "demo-merchant-1"
    Write-Host "  WARNING: Using username as cognito_sub fallback." -ForegroundColor Yellow
}

# The payment-processor uses the JWT sub directly as merchant_id,
# so merchants.id MUST equal the Cognito sub UUID.
$MERCHANT_ID = $COGNITO_SUB

# Update the custom:merchant_id attribute now that we have the real sub
if ($USER_POOL_ID -and $USER_POOL_ID -ne "None") {
    aws cognito-idp admin-update-user-attributes `
        --user-pool-id $USER_POOL_ID `
        --username $DEMO_USERNAME `
        --user-attributes Name=custom:merchant_id,Value=$MERCHANT_ID `
        --region $AWS_REGION 2>$null | Out-Null
    Write-Host "  Updated custom:merchant_id to $MERCHANT_ID"
}

# --- 7b: Run database migrations and seed with the real Cognito sub ---

# Clean up any previous seed job
kubectl delete job db-seed-job -n payment-demo --ignore-not-found 2>$null

Write-Host "  Creating DB seed job..."
$seedJobYaml = @"
apiVersion: batch/v1
kind: Job
metadata:
  name: db-seed-job
  namespace: payment-demo
spec:
  backoffLimit: 1
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: db-seed
          image: postgres:16-alpine
          env:
            - name: PGHOST
              valueFrom:
                secretKeyRef:
                  name: db-credentials
                  key: DB_HOST
            - name: PGUSER
              valueFrom:
                secretKeyRef:
                  name: db-credentials
                  key: DB_USERNAME
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: db-credentials
                  key: DB_PASSWORD
            - name: PGDATABASE
              valueFrom:
                secretKeyRef:
                  name: db-credentials
                  key: DB_NAME
          command: ["/bin/sh", "-c"]
          args:
            - |
              psql <<'EOSQL'
              -- ============================================================
              -- 001: Create merchants table
              -- ============================================================
              CREATE TABLE IF NOT EXISTS merchants (
                  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                  cognito_sub VARCHAR(255) UNIQUE NOT NULL,
                  name VARCHAR(255) NOT NULL,
                  email VARCHAR(255) NOT NULL,
                  webhook_url VARCHAR(500),
                  webhook_secret VARCHAR(255),
                  rate_limit INTEGER DEFAULT 100,
                  status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
                  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                  CONSTRAINT valid_merchant_status CHECK (status IN ('ACTIVE', 'SUSPENDED', 'CLOSED'))
              );

              CREATE UNIQUE INDEX IF NOT EXISTS idx_merchants_email ON merchants(email);
              CREATE INDEX IF NOT EXISTS idx_merchants_cognito_sub ON merchants(cognito_sub);
              CREATE INDEX IF NOT EXISTS idx_merchants_status ON merchants(status);
              CREATE INDEX IF NOT EXISTS idx_merchants_created_at ON merchants(created_at);

              CREATE OR REPLACE FUNCTION update_updated_at_column()
              RETURNS TRIGGER AS `$func`$
              BEGIN
                  NEW.updated_at = NOW();
                  RETURN NEW;
              END;
              `$func`$ language 'plpgsql';

              DROP TRIGGER IF EXISTS update_merchants_updated_at ON merchants;
              CREATE TRIGGER update_merchants_updated_at
                  BEFORE UPDATE ON merchants
                  FOR EACH ROW
                  EXECUTE FUNCTION update_updated_at_column();

              -- ============================================================
              -- 002: Create transactions table
              -- ============================================================
              CREATE TABLE IF NOT EXISTS transactions (
                  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                  merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
                  amount DECIMAL(12,2) NOT NULL,
                  currency VARCHAR(3) NOT NULL DEFAULT 'EUR',
                  status VARCHAR(20) NOT NULL,
                  payment_method VARCHAR(50),
                  payment_method_token VARCHAR(255),
                  card_brand VARCHAR(50),
                  card_last_four VARCHAR(4),
                  description TEXT,
                  idempotency_key VARCHAR(255),
                  correlation_id VARCHAR(255),
                  authorization_code VARCHAR(50),
                  capture_id VARCHAR(50),
                  refund_id VARCHAR(50),
                  error_code VARCHAR(50),
                  error_message TEXT,
                  metadata JSONB,
                  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                  CONSTRAINT valid_status CHECK (status IN ('CREATED', 'PENDING', 'AUTHORIZED', 'CAPTURED', 'REFUNDED', 'CANCELED', 'CANCELLED', 'FAILED')),
                  CONSTRAINT positive_amount CHECK (amount > 0),
                  CONSTRAINT valid_currency CHECK (currency ~ '^[A-Z]{3}')
              );

              CREATE INDEX IF NOT EXISTS idx_transactions_merchant_id ON transactions(merchant_id);
              CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status);
              CREATE INDEX IF NOT EXISTS idx_transactions_created_at ON transactions(created_at);
              CREATE INDEX IF NOT EXISTS idx_transactions_merchant_status ON transactions(merchant_id, status);
              CREATE INDEX IF NOT EXISTS idx_transactions_merchant_created ON transactions(merchant_id, created_at DESC);
              CREATE INDEX IF NOT EXISTS idx_transactions_amount ON transactions(amount);
              CREATE INDEX IF NOT EXISTS idx_transactions_currency ON transactions(currency);

              CREATE INDEX IF NOT EXISTS idx_transactions_active ON transactions(merchant_id, created_at DESC)
                  WHERE status IN ('CREATED', 'AUTHORIZED');

              CREATE INDEX IF NOT EXISTS idx_transactions_metadata ON transactions USING GIN (metadata);

              DROP TRIGGER IF EXISTS update_transactions_updated_at ON transactions;
              CREATE TRIGGER update_transactions_updated_at
                  BEFORE UPDATE ON transactions
                  FOR EACH ROW
                  EXECUTE FUNCTION update_updated_at_column();

              -- ============================================================
              -- 003: Create webhook_deliveries table
              -- ============================================================
              CREATE TABLE IF NOT EXISTS webhook_deliveries (
                  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                  transaction_id UUID NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
                  merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
                  event_type VARCHAR(50) NOT NULL,
                  payload JSONB NOT NULL,
                  attempt_count INTEGER DEFAULT 0,
                  last_attempt_at TIMESTAMP WITH TIME ZONE,
                  next_attempt_at TIMESTAMP WITH TIME ZONE,
                  status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                  response_code INTEGER,
                  response_body TEXT,
                  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                  CONSTRAINT valid_delivery_status CHECK (status IN ('PENDING', 'DELIVERED', 'FAILED')),
                  CONSTRAINT valid_attempt_count CHECK (attempt_count >= 0 AND attempt_count <= 5),
                  CONSTRAINT valid_event_type CHECK (event_type IN (
                      'payment.created',
                      'payment.authorized',
                      'payment.captured',
                      'payment.refunded',
                      'payment.canceled',
                      'payment.failed'
                  ))
              );

              CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_status ON webhook_deliveries(status);
              CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_merchant_id ON webhook_deliveries(merchant_id);
              CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_transaction_id ON webhook_deliveries(transaction_id);
              CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_created_at ON webhook_deliveries(created_at);
              CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_event_type ON webhook_deliveries(event_type);

              CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_next_attempt
                  ON webhook_deliveries(next_attempt_at)
                  WHERE status = 'PENDING';

              CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_merchant_created
                  ON webhook_deliveries(merchant_id, created_at DESC);

              CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_payload ON webhook_deliveries USING GIN (payload);
              EOSQL

              # Phase 2: Seed demo data
              # Use MERCHANT_ID (always a valid UUID) for the id column,
              # and COGNITO_SUB (UUID or fallback string) for the cognito_sub column.
              psql -c "
              INSERT INTO merchants (id, cognito_sub, name, email, webhook_url, webhook_secret, rate_limit, status, created_at, updated_at)
              VALUES
              ('$MERCHANT_ID', '$COGNITO_SUB', 'Helios Electronics', 'demo@helios-electronics.com', 'https://webhook.helios-electronics.com/payments', 'demo_webhook_secret_helios_2026', 100, 'ACTIVE', NOW() - INTERVAL '90 days', NOW() - INTERVAL '90 days'),
              ('22222222-2222-2222-2222-222222222222', 'demo-merchant-2', 'TechStore Global', 'payments@techstore-global.com', 'https://api.techstore-global.com/webhooks/payment', 'demo_webhook_secret_techstore_2026', 200, 'ACTIVE', NOW() - INTERVAL '60 days', NOW() - INTERVAL '60 days'),
              ('33333333-3333-3333-3333-333333333333', 'demo-merchant-3', 'Fashion Boutique', 'admin@fashion-boutique.com', NULL, 'demo_webhook_secret_fashion_2026', 50, 'SUSPENDED', NOW() - INTERVAL '30 days', NOW() - INTERVAL '5 days')
              ON CONFLICT (id) DO UPDATE SET cognito_sub = EXCLUDED.cognito_sub;

              INSERT INTO transactions (id, merchant_id, amount, currency, status, payment_method_token, authorization_code, capture_id, metadata, created_at, updated_at)
              VALUES
              ('a1111111-1111-1111-1111-111111111111', '$MERCHANT_ID', 1299.99, 'EUR', 'CAPTURED', 'pm_tok_demo_visa_4242', 'AUTH_20260112_001', 'CAP_20260112_001', '{\"product\": \"Laptop Pro 15\", \"customer_id\": \"CUST_001\", \"order_id\": \"ORD_2026_001\"}'::jsonb, NOW() - INTERVAL '2 hours', NOW() - INTERVAL '2 hours'),
              ('a2222222-2222-2222-2222-222222222222', '$MERCHANT_ID', 899.00, 'EUR', 'CAPTURED', 'pm_tok_demo_mastercard_5555', 'AUTH_20260112_002', 'CAP_20260112_002', '{\"product\": \"Smartphone X12\", \"customer_id\": \"CUST_002\", \"order_id\": \"ORD_2026_002\"}'::jsonb, NOW() - INTERVAL '5 hours', NOW() - INTERVAL '5 hours'),
              ('a3333333-3333-3333-3333-333333333333', '$MERCHANT_ID', 549.99, 'EUR', 'CAPTURED', 'pm_tok_demo_amex_3782', 'AUTH_20260111_003', 'CAP_20260111_003', '{\"product\": \"Tablet Pro 11\", \"customer_id\": \"CUST_003\", \"order_id\": \"ORD_2026_003\"}'::jsonb, NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day'),
              ('a4444444-4444-4444-4444-444444444444', '$MERCHANT_ID', 199.99, 'EUR', 'AUTHORIZED', 'pm_tok_demo_visa_4111', 'AUTH_20260112_004', NULL, '{\"product\": \"Wireless Headphones Pro\", \"customer_id\": \"CUST_004\", \"order_id\": \"ORD_2026_004\"}'::jsonb, NOW() - INTERVAL '30 minutes', NOW() - INTERVAL '30 minutes'),
              ('a5555555-5555-5555-5555-555555555555', '$MERCHANT_ID', 399.00, 'EUR', 'REFUNDED', 'pm_tok_demo_visa_4242', 'AUTH_20260110_005', 'CAP_20260110_005', '{\"product\": \"Smartwatch Elite\", \"customer_id\": \"CUST_005\", \"order_id\": \"ORD_2026_005\", \"refund_reason\": \"Customer requested\"}'::jsonb, NOW() - INTERVAL '2 days', NOW() - INTERVAL '1 day'),
              ('a6666666-6666-6666-6666-666666666666', '$MERCHANT_ID', 2499.99, 'EUR', 'FAILED', 'pm_tok_demo_visa_4000', NULL, NULL, '{\"product\": \"Gaming Laptop Ultra\", \"customer_id\": \"CUST_006\", \"order_id\": \"ORD_2026_006\"}'::jsonb, NOW() - INTERVAL '3 hours', NOW() - INTERVAL '3 hours')
              ON CONFLICT (id) DO NOTHING;

              INSERT INTO transactions (id, merchant_id, amount, currency, status, payment_method_token, authorization_code, capture_id, metadata, created_at, updated_at)
              VALUES
              ('b1111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222', 449.99, 'EUR', 'CAPTURED', 'pm_tok_demo_visa_4242', 'AUTH_20260111_007', 'CAP_20260111_007', '{\"product\": \"4K Monitor 32inch\", \"customer_id\": \"TECH_CUST_001\", \"order_id\": \"TECH_ORD_001\"}'::jsonb, NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day'),
              ('b2222222-2222-2222-2222-222222222222', '22222222-2222-2222-2222-222222222222', 129.99, 'EUR', 'CAPTURED', 'pm_tok_demo_mastercard_5555', 'AUTH_20260112_008', 'CAP_20260112_008', '{\"product\": \"Mechanical Keyboard + Mouse\", \"customer_id\": \"TECH_CUST_002\", \"order_id\": \"TECH_ORD_002\"}'::jsonb, NOW() - INTERVAL '6 hours', NOW() - INTERVAL '6 hours'),
              ('b3333333-3333-3333-3333-333333333333', '22222222-2222-2222-2222-222222222222', 179.99, 'EUR', 'CAPTURED', 'pm_tok_demo_visa_4111', 'AUTH_20260110_009', 'CAP_20260110_009', '{\"product\": \"External SSD 2TB\", \"customer_id\": \"TECH_CUST_003\", \"order_id\": \"TECH_ORD_003\"}'::jsonb, NOW() - INTERVAL '2 days', NOW() - INTERVAL '2 days')
              ON CONFLICT (id) DO NOTHING;

              INSERT INTO webhook_deliveries (id, transaction_id, merchant_id, event_type, payload, status, attempt_count, last_attempt_at, next_attempt_at, created_at)
              VALUES
              ('d1111111-1111-1111-1111-111111111111', 'a1111111-1111-1111-1111-111111111111', '$MERCHANT_ID', 'payment.captured', '{\"event\": \"payment.captured\", \"transaction_id\": \"a1111111-1111-1111-1111-111111111111\", \"amount\": 1299.99, \"currency\": \"EUR\"}'::jsonb, 'DELIVERED', 1, NOW() - INTERVAL '2 hours', NULL, NOW() - INTERVAL '2 hours'),
              ('d2222222-2222-2222-2222-222222222222', 'a4444444-4444-4444-4444-444444444444', '$MERCHANT_ID', 'payment.authorized', '{\"event\": \"payment.authorized\", \"transaction_id\": \"a4444444-4444-4444-4444-444444444444\", \"amount\": 199.99, \"currency\": \"EUR\"}'::jsonb, 'PENDING', 0, NULL, NOW() + INTERVAL '5 minutes', NOW() - INTERVAL '30 minutes')
              ON CONFLICT (id) DO NOTHING;
              "
"@

$seedJobYaml | kubectl apply -f -

Write-Host "  Waiting for DB seed job to complete (up to 120s)..."
kubectl wait --for=condition=complete job/db-seed-job -n payment-demo --timeout=120s 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: DB seed job failed. Pod logs:" -ForegroundColor Red
    kubectl logs job/db-seed-job -n payment-demo 2>$null
    exit 1
}
Write-Host "  Database migrations and seed data applied successfully."


# =============================================================================
# STEP 7: Wait for pods and NLB
# =============================================================================
Write-Host "[7/9] Waiting for pods to be ready..." -ForegroundColor Cyan
foreach ($deploy in @("merchant-gateway", "payment-processor", "webhook-service")) {
    Write-Host "  Waiting for $deploy..."
    kubectl rollout status deployment/$deploy -n payment-demo --timeout=300s 2>$null
}
Write-Host ""

Write-Host "  Waiting for NLB to get external hostname..."
$NLB_HOSTNAME = $null
for ($i = 1; $i -le 60; $i++) {
    $NLB_HOSTNAME = kubectl get svc merchant-gateway-nlb -n payment-demo `
        -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>$null
    if ($NLB_HOSTNAME) {
        Write-Host "  NLB hostname: $NLB_HOSTNAME"
        break
    }
    if ($i -eq 60) {
        Write-Host "  WARNING: NLB hostname not available after 5 minutes." -ForegroundColor Yellow
    }
    Start-Sleep -Seconds 5
}
Write-Host ""

# =============================================================================
# STEP 8: Update CloudFront with NLB API origin via CDK
# =============================================================================
Write-Host "[8/9] Updating CloudFront with API origin..." -ForegroundColor Cyan
if ($NLB_HOSTNAME) {
    # Failure Simulator API is wired to CloudFront via cross-stack reference (no extra context needed)
    Push-Location cdk
    $cdkArgs = @(
        "npx", "cdk", "deploy", "DevOpsAgentEksFrontend-$AWS_REGION",
        "-c", "environment=$Environment",
        "-c", "projectName=$ProjectName",
        "-c", "eksNodeArchitecture=$EksArchitecture",
        "-c", "eksNodeInstanceType=$EksInstanceType",
        "-c", "eksNodeDesiredCapacity=2",
        "-c", "apiGatewayEndpoint=$NLB_HOSTNAME",
        "-c", "devOpsAgentWebhookUrl=$DevOpsWebhookUrl",
        "-c", "devOpsAgentWebhookSecret=$DevOpsWebhookSecret",
        "-c", "devOpsAgentRegion=$DevOpsAgentRegion",
        "-c", "devOpsAgentSpaceId=$DevOpsAgentSpaceId",
        "--require-approval", "never",
        "--no-cli-pager"
    )
    & $cdkArgs[0] $cdkArgs[1..($cdkArgs.Length-1)]
    Pop-Location
    Write-Host "  CloudFront updated with API origin."
} else {
    Write-Host "  WARNING: Skipping CloudFront API origin (NLB hostname not available)." -ForegroundColor Yellow
    Write-Host "  You can re-run this script later to add the API origin."
}
Write-Host ""

# =============================================================================
# STEP 9: Build and deploy frontend
# =============================================================================
Write-Host "[9/9] Building and deploying frontend..." -ForegroundColor Cyan

$CLIENT_ID = aws cloudformation describe-stacks `
    --stack-name "DevOpsAgentEksAuth-$AWS_REGION" `
    --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" `
    --output text `
    --region $AWS_REGION

$CLOUDFRONT_DOMAIN = aws cloudformation describe-stacks `
    --stack-name "DevOpsAgentEksFrontend-$AWS_REGION" `
    --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDomainName'].OutputValue" `
    --output text `
    --region $AWS_REGION

$DISTRIBUTION_ID = aws cloudformation describe-stacks `
    --stack-name "DevOpsAgentEksFrontend-$AWS_REGION" `
    --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDistributionId'].OutputValue" `
    --output text `
    --region $AWS_REGION

# Generate frontend environment config
$envContent = @"
VITE_COGNITO_USER_POOL_ID=$USER_POOL_ID
VITE_COGNITO_CLIENT_ID=$CLIENT_ID
VITE_COGNITO_REGION=$AWS_REGION
VITE_API_BASE_URL=/api/v1
"@
Set-Content -Path "services/merchant-portal/.env.production.local" -Value $envContent

Write-Host "  Installing frontend dependencies..."
Push-Location services/merchant-portal
npm install --silent
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Host "  ERROR: Frontend npm install failed." -ForegroundColor Red
    exit 1
}
Write-Host "  Building frontend..."
npm run build
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Host "  ERROR: Frontend build failed." -ForegroundColor Red
    exit 1
}
Pop-Location

# Upload to S3
$S3_BUCKET = "$ProjectName-$Environment-merchant-portal-$AWS_ACCOUNT_ID"

Write-Host "  Uploading to S3..."
aws s3 sync services/merchant-portal/dist/ "s3://$S3_BUCKET/" --delete --region $AWS_REGION

Write-Host "  Invalidating CloudFront cache..."
aws cloudfront create-invalidation `
    --distribution-id $DISTRIBUTION_ID `
    --paths "/*" | Out-Null
Write-Host "  Frontend deployed."
Write-Host ""

# =============================================================================
# DEPLOYMENT SUMMARY
# =============================================================================
Write-Host "==============================================" -ForegroundColor Green
Write-Host " Deployment Complete" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Portal URL:     https://$CLOUDFRONT_DOMAIN"
Write-Host "API Endpoint:   https://$CLOUDFRONT_DOMAIN/api/v1"
Write-Host ""
Write-Host "Demo Login:"
Write-Host "  Username: demo-merchant-1"
Write-Host "  Password: DemoPass2026!"
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  kubectl get pods -n payment-demo"
Write-Host "  kubectl logs -f deployment/merchant-gateway -n payment-demo"
Write-Host "  kubectl logs -f deployment/payment-processor -n payment-demo"
Write-Host ""
Write-Host "----------------------------------------------" -ForegroundColor Cyan
Write-Host " Next Steps" -ForegroundColor Cyan
Write-Host "----------------------------------------------" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. Open the Portal URL above in your browser and log in with the demo credentials."
Write-Host ""
Write-Host "2. Test the payment flow:"
Write-Host "   - Browse the product catalog"
Write-Host "   - Add items to cart and complete a checkout"
Write-Host "   - View transaction history on the dashboard"
Write-Host ""
Write-Host "3. Test the DevOps Agent incident investigation:"
Write-Host "   a. Open the DevOps Agent Lab (🧪 icon in the portal)"
Write-Host "   b. Click 'Inject' on a scenario to trigger a real infrastructure failure"
Write-Host "   c. Wait ~2 minutes for the CloudWatch alarm to trigger"
Write-Host "   d. Open the DevOps Agent console to see the automated investigation"
Write-Host "   e. Click 'Rollback' in the Simulator when done (or wait for auto-revert)"
Write-Host ""
Write-Host "4. Clean up all resources when finished:"
Write-Host "     .\scripts\cleanup.ps1"
Write-Host ""
