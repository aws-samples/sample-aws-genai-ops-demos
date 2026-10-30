# Build frontend with injected Vite environment variables
# Parameters are passed from the main deploy script after retrieving CDK stack outputs.
#
# The SPA talks to the HTTP API (Api stack) with the Cognito ID token; it holds
# no AWS credentials, so only these three values are baked into the build.

param(
    [Parameter(Mandatory = $true)][string]$ApiUrl,
    [Parameter(Mandatory = $true)][string]$UserPoolId,
    [Parameter(Mandatory = $true)][string]$UserPoolClientId
)

$ErrorActionPreference = "Stop"

$FrontendDir = Join-Path $PSScriptRoot ".." "frontend"
$FrontendDir = (Resolve-Path $FrontendDir).Path
$EnvFile = Join-Path $FrontendDir ".env.production.local"

Write-Host "Building frontend from: $FrontendDir" -ForegroundColor Cyan

# Generate .env.production.local with Vite environment variables
Write-Host "Generating .env.production.local..." -ForegroundColor Cyan
$envContent = @"
VITE_API_URL=$ApiUrl
VITE_USER_POOL_ID=$UserPoolId
VITE_USER_POOL_CLIENT_ID=$UserPoolClientId
"@
$envContent | Out-File -FilePath $EnvFile -Encoding UTF8

Write-Host "  API URL:             $ApiUrl" -ForegroundColor Gray
Write-Host "  User Pool ID:        $UserPoolId" -ForegroundColor Gray
Write-Host "  User Pool Client ID: $UserPoolClientId" -ForegroundColor Gray

# Run npm build
Write-Host "Running npm run build..." -ForegroundColor Cyan
Push-Location $FrontendDir
try {
    npm run build
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Frontend build failed" -ForegroundColor Red
        exit 1
    }
}
finally {
    Pop-Location
}

Write-Host "`nFrontend built successfully!" -ForegroundColor Green
Write-Host "  Output: $FrontendDir/dist" -ForegroundColor Cyan
