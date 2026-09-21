#Requires -Version 7.0
<#
.SYNOPSIS
    Deploys the AI IAM Access Analyzer Assistant infrastructure and frontend.

.DESCRIPTION
    This script deploys the complete solution: CDK infrastructure (Lambda, API Gateway,
    Cognito, CloudFront, S3) and builds/deploys the React frontend.

.EXAMPLE
    .\deploy-all.ps1
#>

param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Check prerequisites
$sharedScript = Join-Path $PSScriptRoot "..\..\shared\scripts\check-prerequisites.ps1"
if (Test-Path $sharedScript) {
    & $sharedScript -RequiredService "bedrock" -MinAwsCliVersion "2.31.13"
} else {
    # Standalone mode — inline checks
    Write-Host "Checking prerequisites..." -ForegroundColor Cyan
    if (-not (Get-Command aws -ErrorAction SilentlyContinue)) { throw "AWS CLI not found." }
    if (-not (Get-Command python3 -ErrorAction SilentlyContinue)) { throw "Python 3 not found." }
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw "Node.js not found." }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "npm not found." }
    aws sts get-caller-identity | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "AWS credentials not configured." }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " AI IAM Access Analyzer Assistant" -ForegroundColor Cyan
Write-Host " Deployment Script" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Get region from shared prerequisites (or detect)
$region = if ($global:AWS_REGION) { $global:AWS_REGION } `
    elseif ($env:AWS_REGION) { $env:AWS_REGION } `
    elseif ($env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION } `
    else {
        $awsRegion = aws configure get region 2>$null
        if ($awsRegion) { $awsRegion } else { "us-east-1" }
    }

$accountId = aws sts get-caller-identity --query "Account" --output text
Write-Host " Region: $region" -ForegroundColor Yellow
Write-Host " Account: $accountId" -ForegroundColor Yellow
Write-Host ""

# Report what the assistant will be able to see in this region. The stack deploys a
# read-only role and creates none of these: it reads whatever Security Hub CSPM, IAM
# Access Analyzer and CloudTrail already hold here. So the deployment cannot fail on
# them, but the demo is empty without them, and an analyzer of the wrong kind looks
# exactly like a clean account. Every line is one of three states: what is there,
# what is missing, or what could not be checked (the operator's credentials, not the
# Lambda role, run these calls). Never blocks; mirrors src/tools/list_findings.py.
function Show-DataSourceStatus {
    param([string]$Region)

    # One block per AWS service: the service name as a header, then one line per check.
    function Write-Service([string]$Name) { Write-Host "   $Name" -ForegroundColor White }
    function Write-Line([string]$State, [string]$Label, [string]$Text) {
        $mark, $color = switch ($State) {
            "ok"      { "+", "Green" }
            "missing" { "!", "Yellow" }
            default   { "?", "DarkGray" }
        }
        Write-Host ("     [{0}] {1,-30} {2}" -f $mark, $Label, $Text) -ForegroundColor $color
    }
    function Write-Hint([string]$Text) { Write-Host "         $Text" -ForegroundColor Gray }
    # Run a read-only AWS CLI call; return stdout on success, $null on failure, with
    # the first error line in the script-scoped $lastAwsError for the "?" state.
    function Invoke-AwsRead([string[]]$CliArgs) {
        $script:lastAwsError = ""
        $prev = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $out = & aws @CliArgs --region $Region --no-cli-pager 2>&1
            if ($LASTEXITCODE -ne 0) {
                $script:lastAwsError = (($out | ForEach-Object { "$_" }) -join " ").Trim()
                return $null
            }
            return (($out | ForEach-Object { "$_" }) -join "`n").Trim()
        } finally {
            $ErrorActionPreference = $prev
        }
    }

    Write-Host " Data sources in ${Region}" -ForegroundColor Cyan
    Write-Host "   The assistant only reads what these AWS services already hold in this region." -ForegroundColor Gray
    Write-Host "   The deployment does not depend on them; this is what the assistant can see today." -ForegroundColor Gray
    Write-Host ""

    # --- AWS Security Hub CSPM: the only place the assistant reads findings from ---
    Write-Service "AWS Security Hub CSPM"
    $hubEnabled = $false
    $hub = Invoke-AwsRead @("securityhub", "describe-hub", "--query", "SubscribedAt", "--output", "text")
    if ($null -ne $hub) {
        $hubEnabled = $true
        Write-Line "ok" "Service" "enabled in $Region since $($hub.Substring(0, 10))"
    } elseif ($script:lastAwsError -match "InvalidAccessException|not subscribed") {
        Write-Line "missing" "Service" "not enabled in ${Region}: the assistant cannot read any finding"
        Write-Hint "Enable Security Hub CSPM in this region: https://console.aws.amazon.com/securityhub/"
    } else {
        Write-Line "unknown" "Service" "could not check ($($script:lastAwsError))"
    }

    # Access Analyzer -> Security Hub integration (auto-enabled, can be switched off)
    $integrationOn = $false
    if ($hubEnabled) {
        $sub = Invoke-AwsRead @("securityhub", "list-enabled-products-for-import",
            "--query", "length(ProductSubscriptions[?contains(@, 'product-subscription/aws/access-analyzer')])",
            "--output", "text")
        if ($null -eq $sub) {
            Write-Line "unknown" "IAM Access Analyzer feed" "could not check ($($script:lastAwsError))"
        } elseif ($sub -eq "0") {
            Write-Line "missing" "IAM Access Analyzer feed" "switched off: IAM Access Analyzer findings do not reach Security Hub"
            Write-Hint "Security Hub CSPM console > Integrations > IAM Access Analyzer > Accept findings"
        } else {
            $integrationOn = $true
            Write-Line "ok" "IAM Access Analyzer feed" "on: IAM Access Analyzer findings are forwarded to Security Hub"
        }
    }

    # Findings the assistant can see right now: same filter as src/tools/list_findings.py.
    if ($hubEnabled -and $integrationOn) {
        $filterFile = Join-Path ([IO.Path]::GetTempPath()) "iam-assistant-findings-filter-$PID.json"
        try {
            Set-Content -Path $filterFile -NoNewline -Value ('{"ProductName":[{"Value":"IAM Access Analyzer","Comparison":"EQUALS"}],' +
                '"RecordState":[{"Value":"ACTIVE","Comparison":"EQUALS"}],' +
                '"WorkflowStatus":[{"Value":"NEW","Comparison":"EQUALS"}]}')
            $count = Invoke-AwsRead @("securityhub", "get-findings", "--filters", "file://$filterFile",
                "--max-results", "100", "--query", "length(Findings)", "--output", "text")
        } finally {
            Remove-Item $filterFile -Force -ErrorAction SilentlyContinue
        }
        if ($null -eq $count) {
            Write-Line "unknown" "Findings from Access Analyzer" "could not check ($($script:lastAwsError))"
        } elseif ($count -eq "0") {
            Write-Line "missing" "Findings from Access Analyzer" "0 active in Security Hub: nothing to report, or the analyzer is new"
            Write-Hint "New findings reach Security Hub within about 30 minutes of creating an analyzer."
        } else {
            $shown = if ([int]$count -ge 100) { "100 or more" } else { $count }
            Write-Line "ok" "Findings from Access Analyzer" "$shown active in Security Hub"
        }
    }

    # --- AWS IAM Access Analyzer: produces the findings. Two analyzer kinds, two finding families ---
    Write-Service "AWS IAM Access Analyzer"
    $analyzers = Invoke-AwsRead @("accessanalyzer", "list-analyzers",
        "--query", "analyzers[?status=='ACTIVE'].[type,name]", "--output", "text")
    if ($null -eq $analyzers) {
        Write-Line "unknown" "Analyzers" "could not check ($($script:lastAwsError))"
    } else {
        $rows = @($analyzers -split "`n" | Where-Object { $_ } | ForEach-Object {
            $t, $n = $_ -split "`t", 2; [pscustomobject]@{ Type = $t; Name = $n } })
        $external = @($rows | Where-Object { $_.Type -notlike "*UNUSED_ACCESS" })
        $unused   = @($rows | Where-Object { $_.Type -like "*UNUSED_ACCESS" })

        # External access (ACCOUNT / ORGANIZATION): public and cross-account access findings
        if ($external.Count -gt 0) {
            Write-Line "ok" "External access analyzer" "$($external[0].Name) ($($external[0].Type))"
            Write-Hint "Reports public and cross-account access on S3, KMS, Lambda, SQS, Secrets Manager and IAM role trust."
        } else {
            Write-Line "missing" "External access analyzer" "none in ${Region}: public and cross-account access findings cannot appear"
            Write-Hint "aws accessanalyzer create-analyzer --analyzer-name external-access --type ACCOUNT --region $Region   (free)"
        }
        # Unused access (*_UNUSED_ACCESS): unused roles, permissions, access keys, passwords
        if ($unused.Count -gt 0) {
            Write-Line "ok" "Unused access analyzer" "$($unused[0].Name) ($($unused[0].Type))"
            Write-Hint "Reports unused IAM roles, permissions, access keys and passwords."
        } else {
            Write-Line "missing" "Unused access analyzer" "none in ${Region}: unused roles and permissions cannot appear (most suggested prompts need this)"
            Write-Hint "aws accessanalyzer create-analyzer --analyzer-name unused-access --type ACCOUNT_UNUSED_ACCESS --configuration ""unusedAccess={unusedAccessAge=90}"" --region $Region"
            Write-Hint "(billed per IAM role and user analyzed)"
        }
    }

    # --- AWS CloudTrail: usage data for least-privilege policy generation ---
    Write-Service "AWS CloudTrail"
    Write-Line "ok" "Event history" "90-day management event history of $Region (always on, no trail required)"
    Write-Hint "Used by policy generation to see which API calls a role actually made."
    Write-Host ""
}

$stackName = "IamAnalyzerAssistantStack-$region"

# Step 1: Build frontend
Write-Host "[1/4] Building React frontend..." -ForegroundColor Cyan
Push-Location "$PSScriptRoot\frontend"
try {
    if (-not (Test-Path "node_modules")) {
        npm install
    }
    npm run build
} finally {
    Pop-Location
}
Write-Host " Frontend built." -ForegroundColor Green

# Step 2: Deploy CDK stack via the shared script (installs CDK deps, bootstraps, deploys)
Write-Host "[2/4] Deploying CDK infrastructure..." -ForegroundColor Cyan
$env:AWS_REGION = $region
$env:CDK_DEFAULT_ACCOUNT = $accountId
& "$PSScriptRoot\..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "$PSScriptRoot\infrastructure\cdk" -StackName $stackName
if ($LASTEXITCODE -ne 0) {
    Write-Host "CDK deployment failed" -ForegroundColor Red
    exit 1
}
Write-Host " Infrastructure deployed." -ForegroundColor Green

# Step 3: Get stack outputs and configure frontend
Write-Host "[3/4] Configuring frontend with stack outputs..." -ForegroundColor Cyan
function Get-StackOutput($key) {
    aws cloudformation describe-stacks --stack-name $stackName --region $region --no-cli-pager `
        --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" --output text
}

$apiEndpoint = Get-StackOutput "ApiEndpoint"
$userPoolId = Get-StackOutput "UserPoolId"
$userPoolClientId = Get-StackOutput "UserPoolClientId"
$identityPoolId = Get-StackOutput "IdentityPoolId"
$websiteUrl = Get-StackOutput "WebsiteUrl"
$frontendBucket = Get-StackOutput "FrontendBucketName"
$distributionId = Get-StackOutput "DistributionId"

if ([string]::IsNullOrEmpty($userPoolId) -or [string]::IsNullOrEmpty($frontendBucket)) {
    Write-Host "Failed to read outputs from stack $stackName" -ForegroundColor Red
    exit 1
}

# Generate frontend environment config
$envContent = @"
VITE_API_ENDPOINT=$apiEndpoint
VITE_USER_POOL_ID=$userPoolId
VITE_USER_POOL_CLIENT_ID=$userPoolClientId
VITE_IDENTITY_POOL_ID=$identityPoolId
VITE_REGION=$region
"@
$envContent | Out-File -FilePath "$PSScriptRoot\frontend\.env.production.local" -Encoding UTF8
Write-Host " Frontend configured." -ForegroundColor Green

# Step 4: Deploy frontend to S3 + invalidate CloudFront
Write-Host "[4/4] Uploading frontend to S3..." -ForegroundColor Cyan

# Rebuild with production env vars
Push-Location "$PSScriptRoot\frontend"
try {
    npm run build
    aws s3 sync dist/ "s3://$frontendBucket" --delete --region $region
} finally {
    Pop-Location
}

# Invalidate CloudFront cache
if (-not [string]::IsNullOrEmpty($distributionId) -and $distributionId -ne "None") {
    aws cloudfront create-invalidation --distribution-id $distributionId --paths "/*" --region $region | Out-Null
}
Write-Host " Frontend deployed." -ForegroundColor Green

# Create a default demo user (avoids Amplify force-change-password UI bug)
Write-Host ""
Write-Host "Creating demo user..." -ForegroundColor Cyan
$demoEmail = "admin@example.com"
# Generate a unique, strong password per deployment instead of shipping a hardcoded
# credential in the repo. Cognito's default policy requires >=8 chars with upper,
# lower, digit, and symbol, so we guarantee one of each and add random entropy.
$demoRandom = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 12 | ForEach-Object { [char]$_ })
$demoPassword = "Demo$demoRandom!9"

# admin-create-user: allow re-runs on an existing user (UsernameExistsException),
# but surface any other error rather than silently continuing to a
# "Deployment Complete!" summary whose credentials the pool doesn't actually
# accept. Native commands do not respect $ErrorActionPreference, so check
# $LASTEXITCODE explicitly and inspect the merged stdout/stderr text.
$createOutput = aws cognito-idp admin-create-user `
    --user-pool-id $userPoolId `
    --username $demoEmail `
    --user-attributes Name=email_verified,Value=true `
    --message-action SUPPRESS `
    --region $region 2>&1
$createStatus = $LASTEXITCODE

if ($createStatus -ne 0) {
    if ($createOutput -match 'UsernameExistsException') {
        Write-Host "   (demo user already exists — password will be rotated below)" -ForegroundColor Yellow
    } else {
        Write-Host ""
        Write-Host " ✗ Failed to create demo user:" -ForegroundColor Red
        Write-Host $createOutput -ForegroundColor Red
        Write-Host ""
        Write-Host "   The deployment finished but sign-in with the demo credentials" -ForegroundColor Red
        Write-Host "   below will not work. Fix the error above and re-run this script," -ForegroundColor Red
        Write-Host "   or create a user manually via the Cognito console for User Pool" -ForegroundColor Red
        Write-Host "   $userPoolId." -ForegroundColor Red
        exit 1
    }
}

# admin-set-user-password: this is the step that guarantees the demo user can
# sign in (bypasses the force-change-password flow the Amplify hosted UI
# mishandles). Do NOT swallow failures — a silent failure here produces the
# exact InvalidPasswordException symptom this script exists to avoid.
$passwordOutput = aws cognito-idp admin-set-user-password `
    --user-pool-id $userPoolId `
    --username $demoEmail `
    --password $demoPassword `
    --permanent `
    --region $region 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host " ✗ Failed to set demo user password. Sign-in will not work." -ForegroundColor Red
    Write-Host $passwordOutput -ForegroundColor Red
    Write-Host "   The user exists in the pool but has no usable permanent password." -ForegroundColor Red
    exit 1
}

Write-Host " Demo user created." -ForegroundColor Green

# Done
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host " Open the demo: $websiteUrl" -ForegroundColor Cyan
Write-Host " Region: $region" -ForegroundColor Cyan
Write-Host ""
Show-DataSourceStatus -Region $region
Write-Host " Sign in with:" -ForegroundColor Yellow
Write-Host "   Email:    $demoEmail" -ForegroundColor Yellow
Write-Host "   Password: $demoPassword" -ForegroundColor Yellow
Write-Host ""
Write-Host " (You can create additional users via the Cognito console or CLI)" -ForegroundColor Yellow
Write-Host ""
