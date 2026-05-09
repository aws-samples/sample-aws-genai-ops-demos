# Prowler Security Findings + DevOps Agent + Bedrock Nova — Deploy (PowerShell)
$ErrorActionPreference = "Stop"

$ProjectName = "prowler-security"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot = (Resolve-Path "$ScriptDir\..\..").Path

Write-Host "=============================================="
Write-Host " Prowler Security Demo — Automated Deployment"
Write-Host "=============================================="
Write-Host ""

& "$RepoRoot\shared\scripts\check-prerequisites.ps1" -RequireCDK -SkipServiceCheck -MinAwsCliVersion "2.34.21"
$AwsRegion = $global:AWS_REGION
$AccountId = $global:AWS_ACCOUNT_ID

$DevOpsAgentRegion = if ($env:DEVOPS_AGENT_REGION) { $env:DEVOPS_AGENT_REGION } else { "us-east-1" }
# BedrockModelId is resolved by cdk/bin/app.ts from the deploy region when
# not set — the inference-profile prefix (eu./us./apac.) is region-gated, so
# leaving it empty here lets CDK pick the right profile for any region.
$BedrockModelId = if ($env:BEDROCK_MODEL_ID) { $env:BEDROCK_MODEL_ID } else { "" }
$ScanSchedule = if ($env:SCAN_SCHEDULE) { $env:SCAN_SCHEDULE } else { "cron(0 6 * * ? *)" }

if (-not $env:DEVOPS_AGENT_WEBHOOK_URL -or -not $env:DEVOPS_AGENT_WEBHOOK_SECRET) {
    . "$ScriptDir\scripts\setup-devops-agent.ps1"
    Invoke-SetupDevOpsAgent -AgentSpaceName $ProjectName
}

$DevOpsAgentWebhookUrl = if ($env:DEVOPS_AGENT_WEBHOOK_URL) { $env:DEVOPS_AGENT_WEBHOOK_URL } else { "" }
$DevOpsAgentWebhookSecret = if ($env:DEVOPS_AGENT_WEBHOOK_SECRET) { $env:DEVOPS_AGENT_WEBHOOK_SECRET } else { "" }
$DevOpsAgentSpaceId = if ($env:DEVOPS_AGENT_SPACE_ID) { $env:DEVOPS_AGENT_SPACE_ID } else { "" }

$CdkContext = @(
    "-c", "devOpsAgentWebhookUrl=$DevOpsAgentWebhookUrl",
    "-c", "devOpsAgentWebhookSecret=$DevOpsAgentWebhookSecret",
    "-c", "devOpsAgentRegion=$DevOpsAgentRegion",
    "-c", "devOpsAgentSpaceId=$DevOpsAgentSpaceId",
    "-c", "bedrockModelId=$BedrockModelId",
    "-c", "scanSchedule=$ScanSchedule"
)

# ── Timing helpers ────────────────────────────────────────────────
# Mirrors deploy-all.sh: each step prints its label, runs, then
# announces elapsed time. Users see ~3-8 minute blocks instead of
# a single long silence.
$DeployStart = Get-Date
$script:StepStart = $DeployStart
function Start-Step([string]$label) {
    Write-Host $label
    $script:StepStart = Get-Date
}
function End-Step {
    $elapsed = (Get-Date) - $script:StepStart
    $label = ("{0:mm\:ss}" -f $elapsed)
    Write-Host "  done in $label."
    Write-Host ""
}

Start-Step "[1/7] Installing CDK dependencies..."
Push-Location "$ScriptDir\cdk"
npm install --silent
Pop-Location
End-Step

if (-not (Test-Path "$ScriptDir\frontend\dist\index.html")) {
    New-Item -ItemType Directory -Force -Path "$ScriptDir\frontend\dist" | Out-Null
    "<!doctype html><html><body>Prowler Security Dashboard — building...</body></html>" | Out-File -Encoding utf8 "$ScriptDir\frontend\dist\index.html"
}

Start-Step "[2/7] Deploying CDK stacks (all except Frontend)..."
Push-Location "$ScriptDir\cdk"
npx cdk deploy `
    "ProwlerSecurityData-$AwsRegion" `
    "ProwlerSecurityAuth-$AwsRegion" `
    "ProwlerSecurityDevOpsAgent-$AwsRegion" `
    "ProwlerSecurityScanner-$AwsRegion" `
    "ProwlerSecurityIngest-$AwsRegion" `
    "ProwlerSecurityApi-$AwsRegion" `
    @CdkContext `
    --require-approval never `
    --no-cli-pager
Pop-Location
End-Step

Start-Step "[3/7] Building Prowler scanner image..."
$RawBucket = aws cloudformation describe-stacks --stack-name "ProwlerSecurityData-$AwsRegion" --query "Stacks[0].Outputs[?OutputKey=='RawReportsBucketName'].OutputValue" --output text
$BuildProject = aws cloudformation describe-stacks --stack-name "ProwlerSecurityScanner-$AwsRegion" --query "Stacks[0].Outputs[?OutputKey=='BuildProjectName'].OutputValue" --output text
& "$ScriptDir\scripts\build-scanner-image.ps1" -RawBucket $RawBucket -BuildProject $BuildProject
End-Step

Start-Step "[4/7] Fetching CDK outputs for frontend build..."
$UserPoolId = aws cloudformation describe-stacks --stack-name "ProwlerSecurityAuth-$AwsRegion" --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text
$UserPoolClientId = aws cloudformation describe-stacks --stack-name "ProwlerSecurityAuth-$AwsRegion" --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" --output text
$IdentityPoolId = aws cloudformation describe-stacks --stack-name "ProwlerSecurityAuth-$AwsRegion" --query "Stacks[0].Outputs[?OutputKey=='IdentityPoolId'].OutputValue" --output text
$ApiFunctionUrl = aws cloudformation describe-stacks --stack-name "ProwlerSecurityApi-$AwsRegion" --query "Stacks[0].Outputs[?OutputKey=='FunctionUrl'].OutputValue" --output text
End-Step

Start-Step "[5/7] Building frontend..."
& "$ScriptDir\scripts\build-frontend.ps1" `
    -Region $AwsRegion `
    -UserPoolId $UserPoolId `
    -UserPoolClientId $UserPoolClientId `
    -IdentityPoolId $IdentityPoolId `
    -ApiFunctionUrl $ApiFunctionUrl
End-Step

Start-Step "[6/7] Deploying Frontend stack..."
Push-Location "$ScriptDir\cdk"
npx cdk deploy "ProwlerSecurityFrontend-$AwsRegion" @CdkContext --require-approval never --no-cli-pager
Pop-Location
End-Step

$WebsiteUrl = aws cloudformation describe-stacks --stack-name "ProwlerSecurityFrontend-$AwsRegion" --query "Stacks[0].Outputs[?OutputKey=='WebsiteUrl'].OutputValue" --output text

# Step 7: create a default demo user so the dashboard is usable out of the box
$DemoUsername = if ($env:DEMO_USERNAME) { $env:DEMO_USERNAME } else { "demo@prowler-security.local" }
$DemoPassword = if ($env:DEMO_PASSWORD) { $env:DEMO_PASSWORD } else { "ProwlerDemo2026!" }
Start-Step "[7/7] Creating default Cognito user $DemoUsername..."
$userExists = $false
try {
    aws cognito-idp admin-get-user --user-pool-id $UserPoolId --username $DemoUsername --no-cli-pager *> $null
    if ($LASTEXITCODE -eq 0) { $userExists = $true }
} catch {
    $userExists = $false
}
if ($userExists) {
    Write-Host "  User already exists — refreshing password."
} else {
    aws cognito-idp admin-create-user `
        --user-pool-id $UserPoolId `
        --username $DemoUsername `
        --user-attributes "Name=email,Value=$DemoUsername" "Name=email_verified,Value=true" `
        --temporary-password $DemoPassword `
        --message-action SUPPRESS `
        --no-cli-pager *> $null
}
aws cognito-idp admin-set-user-password `
    --user-pool-id $UserPoolId `
    --username $DemoUsername `
    --password $DemoPassword `
    --permanent `
    --no-cli-pager *> $null
End-Step

$TotalElapsed = (Get-Date) - $DeployStart
Write-Host ("Total deploy time: {0:mm\:ss}" -f $TotalElapsed)
Write-Host ""

Write-Host "=============================================="
Write-Host " Deployment complete"
Write-Host "=============================================="
Write-Host ""
Write-Host "Dashboard: $WebsiteUrl"
Write-Host "API URL:   $ApiFunctionUrl"
Write-Host ""
Write-Host "Demo login:"
Write-Host "  Username: $DemoUsername"
Write-Host "  Password: $DemoPassword"
Write-Host "  (Override with `$env:DEMO_USERNAME / `$env:DEMO_PASSWORD before deploy.)"
Write-Host ""
Write-Host "Trigger your first scan:"
Write-Host "  1. Log in to $WebsiteUrl"
Write-Host "  2. Click 'Run scan now' on the Dashboard"
Write-Host "  3. Wait ~3-10 min for findings to appear"
Write-Host "  4. Open any finding and click 'Generate Bedrock Insights' for the"
Write-Host "     Nova Pro remediation playbook, or 'Investigate with DevOps Agent'"
Write-Host "     to dispatch an autonomous investigation (both are on-demand)."
Write-Host ""
if (-not $env:DEVOPS_AGENT_WEBHOOK_URL -or $env:DEVOPS_AGENT_WEBHOOK_URL -eq "") {
    Write-Host "NOTE: DevOps Agent webhook was NOT configured (non-interactive deploy)."
    Write-Host "      To wire it up, run from a terminal:"
    Write-Host "        .\scripts\setup-devops-agent.ps1"
    Write-Host ""
}
