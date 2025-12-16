#!/bin/bash
set -e

AGENT_RUNTIME_ARN=$1
REGION=$2
IDENTITY_POOL_ID=$3
UNAUTH_ROLE_ARN=$4

if [ -z "$AGENT_RUNTIME_ARN" ] || [ -z "$REGION" ] || [ -z "$IDENTITY_POOL_ID" ] || [ -z "$UNAUTH_ROLE_ARN" ]; then
    echo "Usage: ./build-frontend.sh <AgentRuntimeArn> <Region> <IdentityPoolId> <UnauthRoleArn>"
    exit 1
fi

echo "Building frontend with:"
echo "  Agent Runtime ARN: $AGENT_RUNTIME_ARN"
echo "  Region: $REGION"
echo "  Identity Pool ID: $IDENTITY_POOL_ID"
echo "  Unauth Role ARN: $UNAUTH_ROLE_ARN"

cd frontend

# Remove local development environment file if it exists
if [ -f ".env.local" ]; then
    echo "Removing local development environment file..."
    rm ".env.local"
fi

# Create production environment file for basic auth flow
cat > .env.production.local << EOF
VITE_AGENT_RUNTIME_ARN=$AGENT_RUNTIME_ARN
VITE_REGION=$REGION
VITE_IDENTITY_POOL_ID=$IDENTITY_POOL_ID
VITE_UNAUTH_ROLE_ARN=$UNAUTH_ROLE_ARN
VITE_LOCAL_DEV=false
EOF

echo "Created production environment configuration"

# Build frontend
npm run build

cd ..
echo "Frontend build complete"
