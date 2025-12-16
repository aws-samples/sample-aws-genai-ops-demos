param(
    [Parameter(Mandatory=$true)]
    [string]$AgentRuntimeArn,
    
    [Parameter(Mandatory=$true)]
    [string]$Region,
    
    [Parameter(Mandatory=$true)]
    [string]$IdentityPoolId,
    
    [Parameter(Mandatory=$true)]
    [string]$UnauthRoleArn
)

Write-Host "Building frontend with:"
Write-Host "  Agent Runtime ARN: $AgentRuntimeArn"
Write-Host "  Region: $Region"
Write-Host "  Identity Pool ID: $IdentityPoolId"
Write-Host "  Unauth Role ARN: $UnauthRoleArn"

Set-Location frontend

# Remove local development environment file if it exists
if (Test-Path ".env.local") {
    Write-Host "Removing local development environment file..."
    Remove-Item ".env.local"
}

# Create production environment file for basic auth flow
@"
VITE_AGENT_RUNTIME_ARN=$AgentRuntimeArn
VITE_REGION=$Region
VITE_IDENTITY_POOL_ID=$IdentityPoolId
VITE_UNAUTH_ROLE_ARN=$UnauthRoleArn
VITE_LOCAL_DEV=false
"@ | Out-File -FilePath ".env.production.local" -Encoding UTF8

Write-Host "Created production environment configuration"

# Build frontend
npm run build

Set-Location ..
Write-Host "Frontend build complete"
