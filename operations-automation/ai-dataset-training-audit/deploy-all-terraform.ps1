# AI Dataset Training Audit (DART) - Terraform deployment (Windows)
# Mirrors deploy-all-terraform.sh. Same result as the CDK path (deploy-all.ps1).
#
# Terraform cannot use the CDK DockerImageAsset or `cdk bootstrap`, so this
# script builds and pushes the container image itself, in a deterministic
# two-pass apply:
#   pass 1: terraform apply -target=aws_ecr_repository.dart   (ECR repo only)
#   build + push image to that repo
#   pass 2: terraform apply -var image_tag=<tag>              (everything else)
# `var.image_tag` has no default, so a bare `terraform apply` fails at plan time
# and is unsupported.
#
# Only one IaC path may be deployed per account+region. This script does NOT
# call deploy-cdk and does NOT depend on cdk bootstrap.

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""

Write-Host "=== AI Dataset Training Audit (DART) Deployment (Terraform) ===" -ForegroundColor Cyan

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot     = (Resolve-Path "$ScriptDir/../..").Path
$TerraformDir = "$ScriptDir/terraform"
Set-Location $ScriptDir

# 1. Shared prerequisites FIRST (AWS CLI/creds/region, Bedrock, Docker). The
#    Terraform path builds the image with `docker build`/`docker push`, so the
#    same Docker fail-fast applies as on the CDK path.
Write-Host "`nRunning prerequisites check..." -ForegroundColor Yellow
& "$repoRoot/shared/scripts/check-prerequisites.ps1" `
    -RequiredService "bedrock" `
    -MinAwsCliVersion "2.31.13" `
    -RequireDocker
if ($LASTEXITCODE -ne 0) {
    Write-Host "Prerequisites check failed" -ForegroundColor Red
    exit 1
}

# 2. Terraform CLI check (the shared script has NO terraform case). Confirm it
#    is on PATH and meets the documented floor (>= 1.5.0).
if (-not (Get-Command terraform -ErrorAction SilentlyContinue)) {
    Write-Host "Terraform is not installed or not on PATH." -ForegroundColor Red
    Write-Host "Install Terraform >= 1.5.0: https://developer.hashicorp.com/terraform/install" -ForegroundColor Cyan
    exit 1
}
$tfVersion = (terraform version -json | ConvertFrom-Json).terraform_version
$tfParts = $tfVersion.Split('.')
if ([int]$tfParts[0] -lt 1 -or ([int]$tfParts[0] -eq 1 -and [int]$tfParts[1] -lt 5)) {
    Write-Host "Terraform >= 1.5.0 is required (found $tfVersion)." -ForegroundColor Red
    Write-Host "Upgrade: https://developer.hashicorp.com/terraform/install" -ForegroundColor Cyan
    exit 1
}
Write-Host "      Terraform: $tfVersion" -ForegroundColor Gray

$region  = $global:AWS_REGION
$account = $global:AWS_ACCOUNT_ID
Write-Host "`nDeploying to region: $region (account: $account)" -ForegroundColor Yellow

# Image tag: short git SHA, fallback to a UTC timestamp.
$imageTag = (git -C $ScriptDir rev-parse --short HEAD 2>$null)
if (-not $imageTag) {
    $imageTag = (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")
}

Push-Location $TerraformDir

# 3. terraform init.
Write-Host "`n[1/4] terraform init..." -ForegroundColor Yellow
terraform init -input=false

# 4. Pass 1 - ECR repository only (nothing references a not-yet-pushed image).
Write-Host "`n[2/4] terraform apply (ECR repository only)..." -ForegroundColor Yellow
# image_tag is required with no default; Terraform validates all root variables
# even for a -target apply, so pass 1 must supply a placeholder. The task def that
# consumes the image is not created until pass 2, so this value is never used here.
terraform apply -target=aws_ecr_repository.dart -var "region=$region" -var "image_tag=bootstrap" -auto-approve -input=false
$repoUrl  = terraform output -raw ecr_repository_url
$registry = $repoUrl.Split('/')[0]

# 5. Build + push the image.
Write-Host "`n[3/4] Building and pushing the container image ($imageTag)..." -ForegroundColor Yellow
aws ecr get-login-password --region $region | docker login --username AWS --password-stdin $registry
docker build --platform linux/amd64 -t "${repoUrl}:${imageTag}" "$ScriptDir/agent"
docker push "${repoUrl}:${imageTag}"

# 6. Pass 2 - everything else (the task def now references a pushed image).
Write-Host "`n[4/4] terraform apply (full stack)..." -ForegroundColor Yellow
terraform apply -var "image_tag=$imageTag" -var "region=$region" -auto-approve -input=false

$outputBucket  = terraform output -raw output_bucket_name
$auditTable    = terraform output -raw audit_table_name
$clusterName   = terraform output -raw cluster_name
$taskSg        = terraform output -raw task_security_group_id
$publicSubnets = terraform output -raw public_subnet_ids
$taskDef       = terraform output -raw task_definition_arn
Pop-Location

# 7. User-friendly summary + the exact run-task command (same three-part network
#    config as the CDK summary: subnets, securityGroups, assignPublicIp=ENABLED).
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
Write-Host "  aws logs tail /dataset-training-audit/$region --region $region --follow"
Write-Host "`nRead the report when the task stops:" -ForegroundColor Yellow
Write-Host "  aws s3 ls s3://$outputBucket/reports/ --region $region"
Write-Host ""
Write-Host "  Cleanup: cd terraform; terraform destroy -var image_tag=$imageTag -var region=$region -auto-approve" -ForegroundColor Gray
