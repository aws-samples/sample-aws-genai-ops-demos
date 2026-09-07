# Creates or repairs the demo Cognito user and proves the printed credentials work.
# Outputs only the Cognito sub UUID; progress is written with Write-Host so callers
# can safely capture the return value.
param(
    [Parameter(Mandatory=$true)][string]$UserPoolId,
    [Parameter(Mandatory=$true)][string]$ClientId,
    [Parameter(Mandatory=$true)][string]$Region,
    [string]$Username = "demo-merchant-1",
    [string]$Email = "demo@helios-electronics.com",
    [string]$Password = "DemoPass2026!",
    [string]$FullName = "Helios Electronics Demo Merchant",
    [string]$GroupName = "Merchants"
)

$ErrorActionPreference = "Stop"
$attributesJson = @(
    @{ Name = "email"; Value = $Email }
    @{ Name = "email_verified"; Value = "true" }
    @{ Name = "name"; Value = $FullName }
) | ConvertTo-Json -Compress

$userCheck = (aws cognito-idp admin-get-user `
    --user-pool-id $UserPoolId `
    --username $Username `
    --region $Region `
    --no-cli-pager 2>&1) -join "`n"
$userCheckExit = $LASTEXITCODE

if ($userCheckExit -eq 0) {
    Write-Host "  Cognito user '$Username' already exists; refreshing credentials."
} elseif ($userCheck -match "UserNotFoundException") {
    Write-Host "  Creating Cognito user '$Username'..."
    aws cognito-idp admin-create-user `
        --user-pool-id $UserPoolId `
        --username $Username `
        --user-attributes $attributesJson `
        --temporary-password $Password `
        --message-action SUPPRESS `
        --region $Region `
        --no-cli-pager | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to create Cognito user '$Username'." }
} else {
    throw "Could not inspect Cognito user '$Username': $userCheck"
}

# Normalize all mutable/login state on every run so redeployments self-heal.
aws cognito-idp admin-update-user-attributes `
    --user-pool-id $UserPoolId `
    --username $Username `
    --user-attributes $attributesJson `
    --region $Region `
    --no-cli-pager | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to update Cognito attributes." }

aws cognito-idp admin-enable-user `
    --user-pool-id $UserPoolId `
    --username $Username `
    --region $Region `
    --no-cli-pager | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to enable Cognito user." }

aws cognito-idp admin-set-user-password `
    --user-pool-id $UserPoolId `
    --username $Username `
    --password $Password `
    --permanent `
    --region $Region `
    --no-cli-pager | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to set permanent Cognito password." }

aws cognito-idp admin-add-user-to-group `
    --user-pool-id $UserPoolId `
    --username $Username `
    --group-name $GroupName `
    --region $Region `
    --no-cli-pager | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to add Cognito user to '$GroupName'." }

$userStateJson = aws cognito-idp admin-get-user `
    --user-pool-id $UserPoolId `
    --username $Username `
    --region $Region `
    --output json `
    --no-cli-pager
if ($LASTEXITCODE -ne 0) { throw "Could not verify Cognito user." }
$userState = $userStateJson | ConvertFrom-Json
if (-not $userState.Enabled -or $userState.UserStatus -ne "CONFIRMED") {
    throw "Cognito user is not login-ready (Enabled=$($userState.Enabled), Status=$($userState.UserStatus))."
}

# Test the exact app client and credentials. Query only expiration metadata so
# access/ID/refresh tokens never enter terminal output or caller variables.
$authCheck = (aws cognito-idp initiate-auth `
    --auth-flow USER_PASSWORD_AUTH `
    --client-id $ClientId `
    --auth-parameters "USERNAME=$Username,PASSWORD=$Password" `
    --region $Region `
    --query "AuthenticationResult.ExpiresIn" `
    --output text `
    --no-cli-pager 2>&1) -join "`n"
if ($LASTEXITCODE -ne 0 -or -not $authCheck -or $authCheck -eq "None") {
    throw "Cognito credential smoke test failed: $authCheck"
}

$sub = aws cognito-idp admin-get-user `
    --user-pool-id $UserPoolId `
    --username $Username `
    --query "UserAttributes[?Name=='sub'].Value" `
    --output text `
    --region $Region `
    --no-cli-pager
if ($LASTEXITCODE -ne 0 -or -not $sub -or $sub -eq "None") {
    throw "Could not resolve Cognito sub for '$Username'."
}

Write-Host "  Cognito login verified (Enabled=true, Status=CONFIRMED, Group=$GroupName)." -ForegroundColor Green
Write-Output $sub
