# Build frontend with injected Vite environment variables
# Parameters are passed from the main deploy script after retrieving CDK stack outputs.

param(
    [Parameter(Mandatory = $true)][string]$Region,
    [Parameter(Mandatory = $true)][string]$UserPoolId,
    [Parameter(Mandatory = $true)][string]$UserPoolClientId,
    [Parameter(Mandatory = $true)][string]$IdentityPoolId,
    [Parameter(Mandatory = $true)][string]$DiscoverRuntimeArn,
    [Parameter(Mandatory = $true)][string]$AnalyzeRuntimeArn,
    [Parameter(Mandatory = $true)][string]$TransformRuntimeArn
)

$ErrorActionPreference = "Stop"

$FrontendDir = Join-Path $PSScriptRoot ".." "frontend"
$FrontendDir = (Resolve-Path $FrontendDir).Path
$EnvFile = Join-Path $FrontendDir ".env.production.local"

Write-Host "Building frontend from: $FrontendDir" -ForegroundColor Cyan

# Generate .env.production.local with Vite environment variables
Write-Host "Generating .env.production.local..." -ForegroundColor Cyan
$envContent = @"
VITE_REGION=$Region
VITE_USER_POOL_ID=$UserPoolId
VITE_USER_POOL_CLIENT_ID=$UserPoolClientId
VITE_IDENTITY_POOL_ID=$IdentityPoolId
VITE_DISCOVER_RUNTIME_ARN=$DiscoverRuntimeArn
VITE_ANALYZE_RUNTIME_ARN=$AnalyzeRuntimeArn
VITE_TRANSFORM_RUNTIME_ARN=$TransformRuntimeArn
"@
$envContent | Out-File -FilePath $EnvFile -Encoding UTF8

Write-Host "  Region:              $Region" -ForegroundColor Gray
Write-Host "  User Pool ID:        $UserPoolId" -ForegroundColor Gray
Write-Host "  User Pool Client:    $UserPoolClientId" -ForegroundColor Gray
Write-Host "  Identity Pool ID:    $IdentityPoolId" -ForegroundColor Gray
Write-Host "  Discover Runtime:    $DiscoverRuntimeArn" -ForegroundColor Gray
Write-Host "  Analyze Runtime:     $AnalyzeRuntimeArn" -ForegroundColor Gray
Write-Host "  Transform Runtime:   $TransformRuntimeArn" -ForegroundColor Gray

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
