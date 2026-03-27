# Package all 3 agents, upload to S3, and redeploy Runtime stack if needed
# Usage: .\scripts\deploy-agents.ps1

$ErrorActionPreference = "Stop"
$demoRoot = Join-Path $PSScriptRoot ".."
$demoRoot = (Resolve-Path $demoRoot).Path

# Get region from shared prerequisites
& "$demoRoot\..\..\shared\scripts\check-prerequisites.ps1" -RequiredService "agentcore"
$region = $global:AWS_REGION
$dataStack = "LambdaRuntimeMigrationData-$region"
$runtimeStack = "LambdaRuntimeMigrationRuntime-$region"

# Get S3 bucket
$bucket = aws cloudformation describe-stacks --stack-name $dataStack --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text --no-cli-pager
if ([string]::IsNullOrEmpty($bucket)) { Write-Host "ERROR: Cannot find S3 bucket" -ForegroundColor Red; exit 1 }
Write-Host "S3 Bucket: $bucket" -ForegroundColor Cyan

$agents = @("discover", "analyze", "transform")

foreach ($agent in $agents) {
    Write-Host "`n=== Packaging $agent agent ===" -ForegroundColor Yellow
    $agentDir = Join-Path $demoRoot "agent" $agent
    $deployDir = Join-Path $agentDir "deployment_package"
    $zipPath = Join-Path $agentDir "deployment_package.zip"

    # Clean
    if (Test-Path $deployDir) { Remove-Item -Recurse -Force $deployDir }
    if (Test-Path $zipPath) { Remove-Item -Force $zipPath }

    # Install deps
    Write-Host "  Installing dependencies..." -ForegroundColor Gray
    uv pip install -r "$agentDir/requirements.txt" --python-platform aarch64-unknown-linux-gnu --python-version 3.13 --target $deployDir 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: uv pip install failed for $agent" -ForegroundColor Red; exit 1 }

    # Copy main.py + shared constants
    Copy-Item "$agentDir/main.py" "$deployDir/main.py"
    $sharedDir = Join-Path $demoRoot "agent" "_shared"
    if (Test-Path $sharedDir) {
        New-Item -ItemType Directory -Path "$deployDir/_shared" -Force | Out-Null
        Copy-Item "$sharedDir/*" "$deployDir/_shared/" -Recurse
    }

    # Zip
    Write-Host "  Creating zip..." -ForegroundColor Gray
    Compress-Archive -Path "$deployDir/*" -DestinationPath $zipPath -Force
    Remove-Item -Recurse -Force $deployDir

    $sizeMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Host "  Packaged: $sizeMB MB" -ForegroundColor Green

    # Upload
    Write-Host "  Uploading to S3..." -ForegroundColor Gray
    aws s3 cp $zipPath "s3://$bucket/agent/$agent/deployment_package.zip" --no-cli-pager 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: S3 upload failed for $agent" -ForegroundColor Red; exit 1 }
    Write-Host "  Uploaded to s3://$bucket/agent/$agent/deployment_package.zip" -ForegroundColor Green
}

# Deploy Runtime stack (picks up IAM changes)
Write-Host "`n=== Deploying Runtime stack ===" -ForegroundColor Yellow
Push-Location (Join-Path $demoRoot "cdk")
$ts = Get-Date -Format "yyyyMMddHHmmss"
npx cdk deploy $runtimeStack --output "cdk.out.$ts" --no-cli-pager --require-approval never
Pop-Location

# Force-update each runtime so AgentCore picks up the new S3 zip
# (CDK deploy alone won't restart runtimes if the S3 key hasn't changed)
Write-Host "`n=== Force-updating AgentCore runtimes ===" -ForegroundColor Yellow
$roleArn = aws cloudformation describe-stacks --stack-name $runtimeStack --query "Stacks[0].Outputs[?OutputKey=='AgentRoleArn'].OutputValue" --output text --no-cli-pager

$runtimeIds = @{
    discover  = (aws cloudformation describe-stacks --stack-name $runtimeStack --query "Stacks[0].Outputs[?OutputKey=='DiscoverRuntimeArn'].OutputValue" --output text --no-cli-pager) -replace ".*runtime/", ""
    analyze   = (aws cloudformation describe-stacks --stack-name $runtimeStack --query "Stacks[0].Outputs[?OutputKey=='AnalyzeRuntimeArn'].OutputValue" --output text --no-cli-pager) -replace ".*runtime/", ""
    transform = (aws cloudformation describe-stacks --stack-name $runtimeStack --query "Stacks[0].Outputs[?OutputKey=='TransformRuntimeArn'].OutputValue" --output text --no-cli-pager) -replace ".*runtime/", ""
}

foreach ($agent in $agents) {
    $rtId = $runtimeIds[$agent]
    Write-Host "  Updating $agent ($rtId)..." -ForegroundColor Gray
    $artifact = "{""codeConfiguration"":{""code"":{""s3"":{""bucket"":""$bucket"",""prefix"":""agent/$agent/deployment_package.zip""}},""runtime"":""PYTHON_3_13"",""entryPoint"":[""main.py""]}}"
    $envVars = "TABLE_NAME=lambda-runtime-migration,BUCKET_NAME=$bucket,AWS_DEFAULT_REGION=$region"
    aws bedrock-agentcore-control update-agent-runtime --agent-runtime-id $rtId --role-arn $roleArn --network-configuration networkMode=PUBLIC --agent-runtime-artifact $artifact --environment-variables $envVars --region $region --no-cli-pager 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "  WARNING: update-agent-runtime failed for $agent" -ForegroundColor Yellow }
    else { Write-Host "  Updated $agent runtime" -ForegroundColor Green }
}

# Wait for runtimes to become READY
Write-Host "`n=== Waiting for runtimes to be READY ===" -ForegroundColor Yellow
foreach ($agent in $agents) {
    $rtId = $runtimeIds[$agent]
    $maxWait = 120; $waited = 0
    while ($waited -lt $maxWait) {
        $status = aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id $rtId --region $region --query "status" --output text --no-cli-pager 2>$null
        if ($status -eq "READY") { Write-Host "  $agent is READY" -ForegroundColor Green; break }
        Start-Sleep -Seconds 5; $waited += 5
    }
    if ($waited -ge $maxWait) { Write-Host "  WARNING: $agent still not READY after ${maxWait}s (status: $status)" -ForegroundColor Yellow }
}

Write-Host "`n=== All agents deployed and updated ===" -ForegroundColor Green
Write-Host "Try 'Trigger Scan' in the dashboard now." -ForegroundColor Cyan
