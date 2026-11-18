# Aurora MySQL incident investigation with business context (AWS DevOps Agent): one-command deployment.
#
#   1. Agent Space (AWS DevOps Agent), in the Agent Space region
#   2. Database: VPC (no NAT), Aurora MySQL writer with its business tags, the connection load
#      function, the DatabaseConnections alarm and the alarm -> webhook trigger chain
#   3. Lab: engine + API + CloudFront site (HTTP Basic authentication)
#   4. Lab site upload
#   5. Skill aurora-incident-business-context, fetched from the Agent Tools repository (main) and registered
#      in the Agent Space for Chat and Incident RCA
#
# Region: AWS_REGION / AWS_DEFAULT_REGION / aws configure (this demo was built for us-east-1).
# Agent Space region: DEVOPS_AGENT_REGION when the deploy region cannot host one (defaults to the deploy region).

param(
    [string]$ProjectName = "aurora-bizctx",
    [string]$LabUser = "presenter",
    # Generated (and printed at the end) when empty.
    [string]$LabPassword = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$startedAt = Get-Date

$skillName = "aurora-incident-business-context"
# The skill is fetched from main of the Agent Tools repository, so every deploy gets its latest version.
$skillRef = "main"
$skillAgentTypes = @("CHAT", "INCIDENT_RCA")   # deploy-skill.ps1 takes -AgentTypes as string[]

Write-Host ""
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host "  Aurora MySQL incident investigation with business context (AWS DevOps Agent)" -ForegroundColor Cyan
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host "  What this deploys:  an Agent Space, a VPC with no internet path, one Aurora MySQL"
Write-Host "                      writer (db.t4g.medium) carrying business tags, a connection load"
Write-Host "                      function, one alarm, and the Demo Lab (control room) behind CloudFront."
Write-Host "  Time to deploy:     14 minutes measured with the Agent Space already deployed (Aurora stack 468 s);"
Write-Host "                      a full fresh deploy was not timed."
Write-Host "  Running cost:       about `$2.35 per day while deployed (Aurora db.t4g.medium ~`$1.75," -ForegroundColor Yellow
Write-Host "                      two interface endpoints ~`$0.48, CloudFront, Lambda, CloudWatch," -ForegroundColor Yellow
Write-Host "                      Secrets Manager ~`$0.10) plus AWS DevOps Agent time at list price" -ForegroundColor Yellow
Write-Host "                      (about `$2.50-3 per investigation: 4.5-6 minutes observed; GDPR right after" -ForegroundColor Yellow
Write-Host "                      the storm can add a second agent run). Tear down with .\destroy-all.ps1." -ForegroundColor Yellow
Write-Host ""

# -----------------------------------------------------------------------------
# Prerequisites (shared): tooling, credentials, region, DevOps Agent availability
# -----------------------------------------------------------------------------
& "..\..\shared\scripts\check-prerequisites.ps1" -RequiredService "devops-agent" -MinAwsCliVersion "2.34.64" -RequireCDK -MinNodeVersion "20"
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
$agentSpaceStack = "AuroraBizCtxAgentSpace-$agentRegion"
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
# 2. Database: VPC, Aurora MySQL, load function, alarm, trigger chain
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "[2/5] Aurora MySQL cluster in $region (the long step: 468 s measured, little output until done)..." -ForegroundColor Cyan
$databaseStack = "AuroraBizCtxDatabase-$region"
$env:AWS_REGION = $region
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $databaseStack -CdkContext $context
if ($LASTEXITCODE -ne 0) { exit 1 }
$clusterId = Get-StackOutput $databaseStack "ClusterIdentifier"
Write-Host "  Cluster $clusterId is available, business tags set." -ForegroundColor Green

# -----------------------------------------------------------------------------
# 3. Lab: engine, API, site
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "[3/5] Demo Lab..." -ForegroundColor Cyan
if (-not $LabPassword) {
    $alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # pragma: allowlist secret
    $bytes = New-Object byte[] 16
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $LabPassword = -join ($bytes | ForEach-Object { $alphabet[$_ % $alphabet.Length] })
}
$labStack = "AuroraBizCtxLab-$region"
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "cdk" -StackName $labStack -SkipBootstrap `
    -CdkContext ($context + @("labUser=$LabUser", "labPassword=$LabPassword"))
if ($LASTEXITCODE -ne 0) { exit 1 }
$labUrl = Get-StackOutput $labStack "LabUrl"
$siteBucket = Get-StackOutput $labStack "SiteBucketName"
$distributionId = Get-StackOutput $labStack "DistributionId"
# Shown now, not only in the summary: if a later step fails, a re-run needs this password
# (pass it with -LabPassword, or a new one is generated and the Lab stack is redeployed).
Write-Host "  Lab: $labUrl   user $LabUser   password $LabPassword" -ForegroundColor Green
Write-Host "  If a later step fails, re-run with -LabPassword '$LabPassword' to keep these credentials." -ForegroundColor Yellow

# -----------------------------------------------------------------------------
# 4. Site upload
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "[4/5] Lab site..." -ForegroundColor Cyan
# Native commands write warnings to stderr; under "Stop", Windows PowerShell 5.1 turns a redirected
# stderr line into a terminating error. Run them under "Continue" and judge them by $LASTEXITCODE
# (the pattern shared/scripts/deploy-cdk.ps1 uses).
$frontendLog = Join-Path $PSScriptRoot "frontend-build.log"
$ErrorActionPreference = "Continue"
Push-Location frontend
try {
    if (Test-Path "package-lock.json") { $installVerb = "ci" } else { $installVerb = "install" }
    npm $installVerb --no-audit --no-fund *> $frontendLog
    $buildExit = $LASTEXITCODE
    if ($buildExit -eq 0) {
        npm run build *>> $frontendLog
        $buildExit = $LASTEXITCODE
    }
} finally { Pop-Location }
$ErrorActionPreference = "Stop"
if ($buildExit -ne 0) {
    Write-Host "  ERROR: the Lab site build failed (npm $installVerb or npm run build). Last lines of ${frontendLog}:" -ForegroundColor Red
    Get-Content $frontendLog -Tail 25
    exit 1
}
aws s3 sync frontend/dist "s3://$siteBucket" --delete --region $region --no-cli-pager | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "  ERROR: uploading the Lab site to s3://$siteBucket failed." -ForegroundColor Red; exit 1 }
aws cloudfront create-invalidation --distribution-id $distributionId --paths "/*" --no-cli-pager | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "  ERROR: the CloudFront invalidation for $distributionId failed." -ForegroundColor Red; exit 1 }
Write-Host "  Site published." -ForegroundColor Green

# -----------------------------------------------------------------------------
# 5. Skill: stays in the Agent Tools repository; fetched from main, packaged and registered in
#    the Agent Space through the Asset API for the two motions it declares (Chat, Incident RCA).
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "[5/5] Skill $skillName from $skillRef..." -ForegroundColor Cyan
& "..\..\shared\devops-agent\agent-tools\deploy-skill.ps1" -Skill $skillName -Ref $skillRef `
    -AgentSpaceId $agentSpaceId -AgentSpaceRegion $agentRegion -AgentTypes $skillAgentTypes
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
Write-Host "  Cluster:       $clusterId ($region)" -ForegroundColor Cyan
Write-Host "  Region:        $region" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Skill:         $skillName registered in the Agent Space (agent types $($skillAgentTypes -join ','))" -ForegroundColor Cyan
Write-Host "                 latest version, fetched from $skillRef" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Next: open the Lab and inject Connection storm on a business-critical cluster;" -ForegroundColor Yellow
Write-Host "        the alarm starts an investigation within about 3 minutes." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Running cost about `$2.35/day. Tear down: .\destroy-all.ps1" -ForegroundColor Yellow
