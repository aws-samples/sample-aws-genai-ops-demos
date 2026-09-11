param(
    [Parameter(Mandatory=$true)]
    [string]$UserPoolId,

    [Parameter(Mandatory=$true)]
    [string]$UserPoolClientId,

    [Parameter(Mandatory=$true)]
    [string]$ApiUrl,

    [Parameter(Mandatory=$true)]
    [string]$Region
)

# The SPA talks to the HTTP API (Api stack) with the Cognito ID token; it
# holds no AWS credentials, so only these values are baked into the build.
Write-Host "Building frontend with:"
Write-Host "  User Pool ID:        $UserPoolId"
Write-Host "  User Pool Client ID: $UserPoolClientId"
Write-Host "  API URL:             $ApiUrl"
Write-Host "  Region:              $Region"

# Set environment variables for build
$env:VITE_USER_POOL_ID = $UserPoolId
$env:VITE_USER_POOL_CLIENT_ID = $UserPoolClientId
$env:VITE_API_URL = $ApiUrl
$env:VITE_REGION = $Region

# Build frontend
Set-Location frontend
npm run build
if ($LASTEXITCODE -ne 0) {
    Set-Location ..
    Write-Host "Frontend build failed" -ForegroundColor Red
    exit 1
}

Set-Location ..
Write-Host "Frontend build complete"
