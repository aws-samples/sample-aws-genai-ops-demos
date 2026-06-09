param(
    [Parameter(Mandatory=$true)]
    [string]$UserPoolId,

    [Parameter(Mandatory=$true)]
    [string]$UserPoolClientId,

    [Parameter(Mandatory=$true)]
    [string]$IdentityPoolId,

    [Parameter(Mandatory=$true)]
    [string]$AgentRuntimeArn,

    [Parameter(Mandatory=$true)]
    [string]$Region
)

Write-Host "Building frontend with:"
Write-Host "  User Pool ID:        $UserPoolId"
Write-Host "  User Pool Client ID: $UserPoolClientId"
Write-Host "  Identity Pool ID:    $IdentityPoolId"
Write-Host "  Agent Runtime ARN:   $AgentRuntimeArn"
Write-Host "  Region:              $Region"

Set-Location frontend

# Remove local development environment file if it exists
if (Test-Path ".env.local") {
    Write-Host "Removing local development environment file..."
    Remove-Item ".env.local"
}

# Generate production environment file with VITE_* variables
@"
VITE_USER_POOL_ID=$UserPoolId
VITE_USER_POOL_CLIENT_ID=$UserPoolClientId
VITE_IDENTITY_POOL_ID=$IdentityPoolId
VITE_AGENT_RUNTIME_ARN=$AgentRuntimeArn
VITE_REGION=$Region
"@ | Out-File -FilePath ".env.production.local" -Encoding UTF8

Write-Host "Created .env.production.local"

# Build frontend
npm run build

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Frontend build failed (npm run build exited with code $LASTEXITCODE)" -ForegroundColor Red
    Set-Location ..
    exit 1
}

# Verify build output exists
if (-not (Test-Path "dist")) {
    Write-Host "ERROR: Frontend build output (dist/) not found" -ForegroundColor Red
    Set-Location ..
    exit 1
}

Set-Location ..
Write-Host "Frontend build complete"
