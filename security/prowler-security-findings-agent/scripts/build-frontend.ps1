param(
    [Parameter(Mandatory=$true)][string]$Region,
    [Parameter(Mandatory=$true)][string]$UserPoolId,
    [Parameter(Mandatory=$true)][string]$UserPoolClientId,
    [Parameter(Mandatory=$true)][string]$IdentityPoolId,
    [Parameter(Mandatory=$true)][string]$ApiFunctionUrl
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$FrontendDir = (Resolve-Path "$ScriptDir\..\frontend").Path

@"
VITE_REGION=$Region
VITE_USER_POOL_ID=$UserPoolId
VITE_USER_POOL_CLIENT_ID=$UserPoolClientId
VITE_IDENTITY_POOL_ID=$IdentityPoolId
VITE_API_FUNCTION_URL=$ApiFunctionUrl
"@ | Out-File -Encoding ascii "$FrontendDir\.env.production.local"

Push-Location $FrontendDir
npm install --silent
npm run build
Pop-Location
