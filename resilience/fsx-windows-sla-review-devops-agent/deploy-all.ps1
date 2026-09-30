# FSx for Windows SLA review with AWS DevOps Agent: one-command deployment.
#
#   1. Agent Space (AWS DevOps Agent), in the Agent Space region
#   2. Directory: VPC + self-managed Active Directory domain controller (EC2)
#      ... wait until the domain controller reports the forest and the service account ready
#   3. File system (FSx for Windows, Single-AZ, 32 GiB, 8 MB/s), alarms, lifecycle canary, trigger chain
#   4. Lab: engine + API + CloudFront site (HTTP Basic authentication)
#   5. Lab site upload, skill package for the console
#
# Region: AWS_REGION / AWS_DEFAULT_REGION / aws configure. Agent Space region: DEVOPS_AGENT_REGION
# when the deploy region cannot host one (defaults to the deploy region).

param(
    [string]$ProjectName = "fsx-sla-review",
    [string]$LabUser = "presenter",
    # Generated (and printed at the end) when empty.
    [string]$LabPassword = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$startedAt = Get-Date

Write-Host ""
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host "  FSx for Windows SLA review with AWS DevOps Agent" -ForegroundColor Cyan
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host "  What this deploys:  an Agent Space, a VPC with one Active Directory domain"
Write-Host "                      controller, one FSx for Windows file system with its alarms,"
Write-Host "                      and the Demo Lab (control room) behind CloudFront."
Write-Host "  Time to deploy:     about 1 hour. The domain controller promotes itself (~10 min),"
Write-Host "                      then FSx joins the domain and builds the file system (25-35 min)."
Write-Host "  Running cost:       about `$5 per day while deployed (EC2 Windows t3.medium ~`$1.50," -ForegroundColor Yellow
Write-Host "                      FSx 32 GiB / 8 MB/s ~`$0.75, NAT gateway ~`$1.10, CloudFront, Lambda," -ForegroundColor Yellow
Write-Host "                      CloudWatch < `$0.50) plus AWS DevOps Agent time at list price" -ForegroundColor Yellow
Write-Host "                      (about `$0.50 per investigation). Tear down with .\destroy-all.ps1." -ForegroundColor Yellow
Write-Host ""

# -----------------------------------------------------------------------------
# Prerequisites (shared): tooling, credentials, region, DevOps Agent availability
# -----------------------------------------------------------------------------
& "..\..\shared\scripts\check-prerequisites.ps1" -RequiredService "devops-agent" -MinAwsCliVersion "2.34.20" -RequireCDK -MinNodeVersion "20"
if ($LASTEXITCODE -ne 0) { exit 1 }
$region = $global:AWS_REGION
$agentRegion = $global:DEVOPS_AGENT_REGION
$accountId = $global:AWS_ACCOUNT_ID
Write-Host "  Deploy region: $region   Agent Space region: $agentRegion   Account: $accountId"

function Get-StackOutput([string]$StackName, [string]$Key, [string]$StackRegion = $region) {
    $value = aws cloudformation describe-stacks --stack-name $StackName --region $StackRegion `
        --query "Stacks[0].Outputs[?OutputKey=='$Key'].OutputValue" --output text --no-cli-pager
    if (-not $value -or $value -eq "None") { throw "Stack $StackName has no output $Key" }
    return $value
}

# -----------------------------------------------------------------------------
# 1. Agent Space
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "[1/5] Agent Space in $agentRegion..." -ForegroundColor Cyan
$agentSpaceStack = "FsxSlaReviewAgentSpace-$agentRegion"
$savedRegion = $env:AWS_REGION
$env:AWS_REGION = $agentRegion      # bootstrap and deploy where the Agent Space lives
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $agentSpaceStack `
    -CdkContext "projectName=$ProjectName", "devOpsAgentRegion=$agentRegion"
$exit = $LASTEXITCODE
$env:AWS_REGION = $savedRegion
if ($exit -ne 0) { exit 1 }
$webhookUrl = Get-StackOutput $agentSpaceStack "WebhookUrl" $agentRegion
$webhookSecretArn = Get-StackOutput $agentSpaceStack "WebhookSecretArn" $agentRegion
$agentSpaceId = Get-StackOutput $agentSpaceStack "AgentSpaceId" $agentRegion
Write-Host "  Agent Space: $agentSpaceId (webhook configured; its secret stays in Secrets Manager)" -ForegroundColor Green

# Context every remaining stack receives. The secret VALUE never enters this script.
$context = @(
    "projectName=$ProjectName",
    "devOpsAgentRegion=$agentRegion",
    "devOpsAgentSpaceId=$agentSpaceId",
    "devOpsAgentWebhookUrl=$webhookUrl",
    "devOpsAgentWebhookSecretArn=$webhookSecretArn"
)

# -----------------------------------------------------------------------------
# 2. Directory: VPC + domain controller, then wait for the domain
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "[2/5] VPC and Active Directory domain controller in $region..." -ForegroundColor Cyan
$directoryStack = "FsxSlaReviewDirectory-$region"
$env:AWS_REGION = $region
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $directoryStack -CdkContext $context
if ($LASTEXITCODE -ne 0) { exit 1 }
$readyParameter = Get-StackOutput $directoryStack "ReadyParameterName"

Write-Host "  Waiting for the domain controller to promote the forest and create the service account (~10 min)..."
$deadline = (Get-Date).AddMinutes(30)
$ready = $false
while ((Get-Date) -lt $deadline) {
    $value = aws ssm get-parameter --name $readyParameter --region $region --query "Parameter.Value" --output text --no-cli-pager 2>$null
    if ($value -eq "ready") { $ready = $true; break }
    Write-Host "    $(Get-Date -Format HH:mm:ss)  not yet"
    Start-Sleep -Seconds 30
}
if (-not $ready) {
    Write-Host "ERROR: the domain controller did not report ready within 30 minutes." -ForegroundColor Red
    Write-Host "       Open a Session Manager shell on it (output DomainControllerInstanceId of $directoryStack)" -ForegroundColor Red
    Write-Host "       and read C:\dc-setup.log, then re-run this script: it resumes from here." -ForegroundColor Red
    exit 1
}
Write-Host "  Domain ready." -ForegroundColor Green

# -----------------------------------------------------------------------------
# 3. File system, alarms, canary, trigger chain
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "[3/5] FSx for Windows file system (this is the long step: 25-35 minutes, no output until done)..." -ForegroundColor Cyan
$fileSystemStack = "FsxSlaReviewFileSystem-$region"
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $fileSystemStack -SkipBootstrap -CdkContext $context
if ($LASTEXITCODE -ne 0) {
    Write-Host "  If the file system failed to join the domain, run the AWSSupport-ValidateFSxWindowsADConfig" -ForegroundColor Yellow
    Write-Host "  Systems Manager automation with the ServiceAccountSecretArn output of $directoryStack." -ForegroundColor Yellow
    exit 1
}
$fileSystemId = Get-StackOutput $fileSystemStack "FileSystemId"
Write-Host "  File system $fileSystemId is AVAILABLE." -ForegroundColor Green

# -----------------------------------------------------------------------------
# 4. Lab: engine, API, site
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "[4/5] Demo Lab..." -ForegroundColor Cyan
if (-not $LabPassword) {
    $alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    $bytes = New-Object byte[] 16
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $LabPassword = -join ($bytes | ForEach-Object { $alphabet[$_ % $alphabet.Length] })
}
$labStack = "FsxSlaReviewLab-$region"
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $labStack -SkipBootstrap `
    -CdkContext ($context + @("labUser=$LabUser", "labPassword=$LabPassword"))
if ($LASTEXITCODE -ne 0) { exit 1 }
$labUrl = Get-StackOutput $labStack "LabUrl"
$siteBucket = Get-StackOutput $labStack "SiteBucketName"
$distributionId = Get-StackOutput $labStack "DistributionId"

# -----------------------------------------------------------------------------
# 5. Site upload and skill package
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "[5/5] Lab site and skill package..." -ForegroundColor Cyan
Push-Location frontend
try {
    if (Test-Path "package-lock.json") { npm ci --no-audit --no-fund 2>$null | Out-Null } else { npm install --no-audit --no-fund 2>$null | Out-Null }
    if ($LASTEXITCODE -ne 0) { throw "npm install (frontend) failed" }
    npm run build 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
} finally { Pop-Location }
aws s3 sync frontend/dist "s3://$siteBucket" --delete --region $region --no-cli-pager | Out-Null
aws cloudfront create-invalidation --distribution-id $distributionId --paths "/*" --no-cli-pager | Out-Null
Write-Host "  Site published." -ForegroundColor Green

# The capability stays in the Agent Tools repository: fetched at its ref, packaged, and
# registered in the Agent Space through the Asset API (GENERIC = every agent type).
& "..\..\shared\devops-agent\agent-tools\deploy-skill.ps1" -Skill "storage-fsx-windows-sla-optimizer" -Ref "main" `
    -AgentSpaceId $agentSpaceId -AgentSpaceRegion $agentRegion -AgentTypes "GENERIC"
if ($LASTEXITCODE -ne 0) { exit 1 }

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
$elapsed = [int]((Get-Date) - $startedAt).TotalMinutes
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Deployment Complete! ($elapsed min)" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Demo Lab:      $labUrl" -ForegroundColor Cyan
Write-Host "  Sign in:       user $LabUser   password $LabPassword" -ForegroundColor Cyan
Write-Host "  Agent Space:   https://$agentSpaceId.aidevops.global.app.aws/  (id $agentSpaceId, $agentRegion)" -ForegroundColor Cyan
Write-Host "  File system:   $fileSystemId ($region)" -ForegroundColor Cyan
Write-Host "  Region:        $region" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Skill:         storage-fsx-windows-sla-optimizer registered in the Agent Space (all agent types)" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Next: open the Lab, inject a scenario, and ask in the Agent Space chat:" -ForegroundColor Yellow
Write-Host "        Review all my FSx for Windows file systems in $region for SLA readiness." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Running cost about `$5/day. Tear down: .\destroy-all.ps1" -ForegroundColor Yellow
