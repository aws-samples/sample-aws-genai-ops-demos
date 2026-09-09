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

# Derive the Refresh All state machine ARN from its fixed name (deployed by
# the Scheduler stack). Derived rather than read from stack outputs because
# the frontend builds before the Scheduler stack deploys on a first install.
$accountId = aws sts get-caller-identity --query Account --output text --no-cli-pager
$stateMachineArn = "arn:aws:states:${Region}:${accountId}:stateMachine:aws-services-lifecycle-refresh-all"

Write-Host "Building frontend with:"
Write-Host "  User Pool ID: $UserPoolId"
Write-Host "  User Pool Client ID: $UserPoolClientId"
Write-Host "  Identity Pool ID: $IdentityPoolId"
Write-Host "  Agent Runtime ARN: $AgentRuntimeArn"
Write-Host "  State Machine ARN: $stateMachineArn"
Write-Host "  Region: $Region"

# Set environment variables for build
$env:VITE_USER_POOL_ID = $UserPoolId
$env:VITE_USER_POOL_CLIENT_ID = $UserPoolClientId
$env:VITE_IDENTITY_POOL_ID = $IdentityPoolId
$env:VITE_AGENT_RUNTIME_ARN = $AgentRuntimeArn
$env:VITE_STATE_MACHINE_ARN = $stateMachineArn
$env:VITE_REGION = $Region

# Build frontend
Set-Location frontend
npm run build

Set-Location ..
Write-Host "Frontend build complete"
