#!/bin/bash
# Build frontend with Cognito config and Agent Runtime ARN
set -e

# Parse named arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --user-pool-id) USER_POOL_ID="$2"; shift 2 ;;
        --user-pool-client-id) USER_POOL_CLIENT_ID="$2"; shift 2 ;;
        --identity-pool-id) IDENTITY_POOL_ID="$2"; shift 2 ;;
        --agent-runtime-arn) AGENT_RUNTIME_ARN="$2"; shift 2 ;;
        --region) REGION="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [ -z "$USER_POOL_ID" ] || [ -z "$USER_POOL_CLIENT_ID" ] || [ -z "$IDENTITY_POOL_ID" ] || [ -z "$AGENT_RUNTIME_ARN" ] || [ -z "$REGION" ]; then
    echo "Usage: $0 --user-pool-id <ID> --user-pool-client-id <ID> --identity-pool-id <ID> --agent-runtime-arn <ARN> --region <REGION>"
    exit 1
fi

echo "Building frontend with:"
echo "  User Pool ID:        $USER_POOL_ID"
echo "  User Pool Client ID: $USER_POOL_CLIENT_ID"
echo "  Identity Pool ID:    $IDENTITY_POOL_ID"
echo "  Agent Runtime ARN:   $AGENT_RUNTIME_ARN"
echo "  Region:              $REGION"

cd frontend

# Remove local development environment file if it exists
if [ -f ".env.local" ]; then
    echo "Removing local development environment file..."
    rm ".env.local"
fi

# Generate production environment file with VITE_* variables
cat > .env.production.local << EOF
VITE_USER_POOL_ID=$USER_POOL_ID
VITE_USER_POOL_CLIENT_ID=$USER_POOL_CLIENT_ID
VITE_IDENTITY_POOL_ID=$IDENTITY_POOL_ID
VITE_AGENT_RUNTIME_ARN=$AGENT_RUNTIME_ARN
VITE_REGION=$REGION
EOF

echo "Created .env.production.local"

# Build frontend
npm run build

# Verify build output exists
if [ ! -d "dist" ]; then
    echo "ERROR: Frontend build output (dist/) not found"
    cd ..
    exit 1
fi

cd ..
echo "Frontend build complete"
