# AI Dataset Training Audit (DART) - CDK deployment (Windows)
# Mirrors deploy-all.sh.
#
# One stack: DatasetTrainingAudit-<region>. The Fargate image is a CDK
# DockerImageAsset that `cdk deploy` builds locally, so Docker must be running
# (-RequireDocker enforces this up front).
#
# This is the demo's only stack, so deploy-cdk.ps1 is called WITHOUT
# -SkipBootstrap: it runs `cdk bootstrap` once before the single `cdk deploy`.
#
# For the Terraform path instead, use .\deploy-all-terraform.ps1. Only one IaC
# path may be deployed per account+region.

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""

Write-Host "=== AI Dataset Training Audit (DART) Deployment (CDK) ===" -ForegroundColor Cyan

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot  = (Resolve-Path "$ScriptDir/../..").Path
Set-Location $ScriptDir

# 1. Shared prerequisites FIRST (AWS CLI/creds/region, Bedrock, Node/CDK, Docker).
#    -RequireDocker: the Fargate image is a CDK DockerImageAsset built locally by
#    `cdk deploy`; without a running Docker engine the deploy fails deep with an
#    opaque error, so fail fast here instead.
Write-Host "`nRunning prerequisites check..." -ForegroundColor Yellow
& "$repoRoot/shared/scripts/check-prerequisites.ps1" `
    -RequiredService "bedrock" `
    -MinAwsCliVersion "2.31.13" `
    -RequireCDK `
    -RequireDocker
if ($LASTEXITCODE -ne 0) {
    Write-Host "Prerequisites check failed" -ForegroundColor Red
    exit 1
}

$region    = $global:AWS_REGION
$account   = $global:AWS_ACCOUNT_ID
$stackName = "DatasetTrainingAudit-$region"
Write-Host "`nDeploying $stackName (account: $account)" -ForegroundColor Yellow

# 2. Deploy through the shared CDK script (handles bootstrap + deploy). No direct
#    cdk deploy/bootstrap/destroy. Single stack => NO -SkipBootstrap.
& "$repoRoot/shared/scripts/deploy-cdk.ps1" `
    -CdkDirectory "cdk" `
    -StackName $stackName
if ($LASTEXITCODE -ne 0) {
    Write-Host "CDK deployment failed" -ForegroundColor Red
    exit 1
}

# 3. Read back CfnOutputs (no --outputs-file, no jq).
function Get-StackOutput($key) {
    aws cloudformation describe-stacks `
        --stack-name $stackName `
        --region $region `
        --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" `
        --output text
}
$outputBucket  = Get-StackOutput "OutputBucketName"
$auditTable    = Get-StackOutput "AuditTableName"
$clusterName   = Get-StackOutput "ClusterName"
$taskSg        = Get-StackOutput "TaskSecurityGroupId"
$publicSubnets = Get-StackOutput "PublicSubnetIds"
$taskDef       = Get-StackOutput "TaskDefinitionArn"
# Log group name is fixed by the stack (lib/dataset-training-audit-stack.ts): /dataset-training-audit/<region>.
$logGroup      = "/dataset-training-audit/$region"

# 4. User-friendly summary + the exact run-task command. The network config MUST
#    pass subnets, securityGroups AND assignPublicIp=ENABLED, or the public-subnet
#    task cannot reach Bedrock/Comprehend.
Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Output bucket: $outputBucket" -ForegroundColor Cyan
Write-Host "  Audit table:   $auditTable" -ForegroundColor Cyan
Write-Host "  ECS cluster:   $clusterName" -ForegroundColor Cyan
Write-Host "  Region:        $region" -ForegroundColor Cyan
Write-Host "`nRun a dataset audit (replace the message with your own request):" -ForegroundColor Yellow
Write-Host ""
Write-Host "  aws ecs run-task ``"
Write-Host "    --cluster $clusterName ``"
Write-Host "    --launch-type FARGATE ``"
Write-Host "    --task-definition $taskDef ``"
Write-Host "    --region $region ``"
Write-Host "    --network-configuration 'awsvpcConfiguration={subnets=[$publicSubnets],securityGroups=[$taskSg],assignPublicIp=ENABLED}' ``"
Write-Host "    --overrides '{\""containerOverrides\"":[{\""name\"":\""DartContainer\"",\""command\"":[\""Check s3://$outputBucket/train.jsonl for llama3-70b fine-tuning\""]}]}'"
Write-Host ""
Write-Host "  First upload a dataset under s3://$outputBucket/ (the task reads its own" -ForegroundColor Gray
Write-Host "  output bucket by default). To scan a dataset in another bucket, see the" -ForegroundColor Gray
Write-Host "  README section 'Scanning a dataset in your own S3 bucket'." -ForegroundColor Gray
Write-Host "`nWatch the run (logs stream here):" -ForegroundColor Yellow
Write-Host "  aws logs tail $logGroup --region $region --follow"
Write-Host "`nRead the report when the task stops:" -ForegroundColor Yellow
Write-Host "  aws s3 ls s3://$outputBucket/reports/ --region $region"
Write-Host ""
Write-Host "  Cleanup: cd cdk; npx -y cdk destroy $stackName --force -c imageTag=destroy" -ForegroundColor Gray
