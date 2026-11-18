# EKS Self-Healing Agent (Agatha) - CDK deployment (Windows)
# Mirrors deploy-all.sh.
#
# One stack: EksSelfHealingAgent-<region>. It installs Agatha onto an EXISTING
# EKS cluster: the agent image (CDK DockerImageAsset, built locally, so Docker
# must be running), an IRSA role with Bedrock InvokeModel on one model, and a
# Helm release of helm/agatha installed through the CDK kubectl handler.
#
# Usage:
#   .\deploy-all.ps1 -ClusterName <name> -KubectlRoleArn <arn> [-OidcIssuer <url>]
#                    [-NodeArchitecture amd64|arm64] [-ModelId <base model id>]
#   .\deploy-all.ps1 -Destroy
# Inputs can also come from CLUSTER_NAME, KUBECTL_ROLE_ARN, OIDC_ISSUER,
# NODE_ARCHITECTURE and MODEL_ID environment variables.
param(
    [string]$ClusterName = $env:CLUSTER_NAME,
    [string]$KubectlRoleArn = $env:KUBECTL_ROLE_ARN,
    [string]$OidcIssuer = $env:OIDC_ISSUER,
    [ValidateSet('', 'amd64', 'arm64')]
    [string]$NodeArchitecture = $env:NODE_ARCHITECTURE,
    [string]$ModelId = $env:MODEL_ID,
    [switch]$Destroy
)
$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""

Write-Host "=== EKS Self-Healing Agent (Agatha) Deployment (CDK) ===" -ForegroundColor Cyan
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot  = (Resolve-Path "$ScriptDir/../..").Path
Set-Location $ScriptDir

# 1. Shared prerequisites FIRST (AWS CLI/creds/region, Bedrock, Node/CDK, Docker).
#    Destroy uses the imageTag escape hatch, so it does not need Docker.
Write-Host "`nRunning prerequisites check..." -ForegroundColor Yellow
if ($Destroy) {
    & "$repoRoot/shared/scripts/check-prerequisites.ps1" `
        -RequiredService "bedrock" `
        -MinAwsCliVersion "2.31.13" `
        -RequireCDK
} else {
    & "$repoRoot/shared/scripts/check-prerequisites.ps1" `
        -RequiredService "bedrock" `
        -MinAwsCliVersion "2.31.13" `
        -RequireCDK `
        -RequireDocker
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "Prerequisites check failed" -ForegroundColor Red
    exit 1
}

$region    = $global:AWS_REGION
$account   = $global:AWS_ACCOUNT_ID
$partition = ($global:AWS_ARN -split ':')[1]
$stackName = "EksSelfHealingAgent-$region"

function Get-StackOutput($key) {
    $value = aws cloudformation describe-stacks `
        --stack-name $stackName `
        --region $region `
        --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" `
        --output text 2>$null
    if ($LASTEXITCODE -ne 0) { return "" }
    return $value
}

# 2. Teardown path: read the inputs back from the stack outputs, then destroy
#    through the shared script. No direct cdk invocation.
if ($Destroy) {
    if (-not $ClusterName)    { $ClusterName    = Get-StackOutput "ClusterName" }
    if (-not $KubectlRoleArn) { $KubectlRoleArn = Get-StackOutput "KubectlRoleArn" }
    if (-not $OidcIssuer)     { $OidcIssuer     = Get-StackOutput "OidcIssuer" }
    if (-not $ClusterName -or $ClusterName -eq "None" -or -not $KubectlRoleArn -or -not $OidcIssuer) {
        Write-Host "Could not read the stack inputs. Stack $stackName not found in $region? Pass -ClusterName, -KubectlRoleArn and -OidcIssuer." -ForegroundColor Red
        exit 1
    }
    Write-Host "`nDestroying $stackName (account: $account)" -ForegroundColor Yellow
    & "$repoRoot/shared/scripts/deploy-cdk.ps1" `
        -CdkDirectory "infrastructure/cdk" `
        -StackName $stackName `
        -DestroyStack `
        -SkipBootstrap `
        -CdkContext @("clusterName=$ClusterName", "kubectlRoleArn=$KubectlRoleArn", "oidcIssuer=$OidcIssuer", "imageTag=destroy")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "CDK teardown failed" -ForegroundColor Red
        exit 1
    }
    # The CDK kubectl provider Lambdas create their own log groups at runtime, so
    # CloudFormation does not delete them. Remove them so teardown leaves no orphans.
    # Log groups are regional and the stack name is per region, so this prefix only
    # matches this demo's Lambdas in this region.
    $logPrefix = "/aws/lambda/EksSelfHealingAgent-"
    $logGroups = aws logs describe-log-groups --region $region `
        --log-group-name-prefix $logPrefix `
        --query "logGroups[].logGroupName" --output text 2>$null
    if ($LASTEXITCODE -eq 0 -and $logGroups) {
        foreach ($lg in ($logGroups -split "\s+" | Where-Object { $_ })) {
            Write-Host "  Deleting log group $lg"
            aws logs delete-log-group --region $region --log-group-name $lg | Out-Null
        }
    }
    Write-Host "`nTeardown complete. The agent image stays in the shared CDK bootstrap asset repository." -ForegroundColor Green
    exit 0
}

# 3. Validate inputs.
if (-not $ClusterName -or -not $KubectlRoleArn) {
    Write-Host "-ClusterName and -KubectlRoleArn are required (see README Inputs)" -ForegroundColor Red
    exit 1
}
if (-not $OidcIssuer) {
    $OidcIssuer = aws eks describe-cluster --name $ClusterName --region $region `
        --query "cluster.identity.oidc.issuer" --output text 2>$null
}
if (-not $OidcIssuer -or $OidcIssuer -eq "None") {
    Write-Host "Could not find an OIDC issuer for cluster $ClusterName in $region. Check the name and region, or pass -OidcIssuer." -ForegroundColor Red
    exit 1
}
$issuerHost = $OidcIssuer -replace '^https://', ''
aws iam get-open-id-connect-provider `
    --open-id-connect-provider-arn "arn:${partition}:iam::${account}:oidc-provider/$issuerHost" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "No IAM OIDC provider for $OidcIssuer. Create one first:" -ForegroundColor Red
    Write-Host "  eksctl utils associate-iam-oidc-provider --cluster $ClusterName --region $region --approve"
    exit 1
}

$contextArgs = @("clusterName=$ClusterName", "kubectlRoleArn=$KubectlRoleArn", "oidcIssuer=$OidcIssuer")
if ($NodeArchitecture) { $contextArgs += "nodeArchitecture=$NodeArchitecture" }
if ($ModelId)          { $contextArgs += "bedrockBaseModelId=$ModelId" }

Write-Host "`nDeploying $stackName onto cluster $ClusterName (account: $account)" -ForegroundColor Yellow

# 4. Deploy through the shared CDK script (handles bootstrap + deploy). Single
#    stack, so no -SkipBootstrap.
& "$repoRoot/shared/scripts/deploy-cdk.ps1" `
    -CdkDirectory "infrastructure/cdk" `
    -StackName $stackName `
    -CdkContext $contextArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "CDK deployment failed" -ForegroundColor Red
    exit 1
}

# 5. Read back CfnOutputs (no --outputs-file, no jq).
$agentRoleArn = Get-StackOutput "AgentRoleArn"
$modelIdOut   = Get-StackOutput "BedrockModelId"
$imageUri     = Get-StackOutput "ImageUri"

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Region:        $region" -ForegroundColor Cyan
Write-Host "  Cluster:       $ClusterName" -ForegroundColor Cyan
Write-Host "  Agent role:    $agentRoleArn" -ForegroundColor Cyan
Write-Host "  Bedrock model: $modelIdOut" -ForegroundColor Cyan
Write-Host "  Image:         $imageUri" -ForegroundColor Cyan
Write-Host "`nCheck the agent (kubectl pointed at the cluster):" -ForegroundColor Yellow
Write-Host "  kubectl get pods -l app=agatha"
Write-Host "  kubectl logs -f deployment/agatha"
Write-Host ""
Write-Host "  Next: see the README section 'Run the Demo'." -ForegroundColor Gray
Write-Host "  Teardown: .\deploy-all.ps1 -Destroy" -ForegroundColor Gray
