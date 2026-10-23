#!/bin/bash
# Build frontend with injected Vite environment variables
# macOS/Linux version - mirrors build-frontend.ps1
#
# The SPA talks to the HTTP API (Api stack) with the Cognito ID token; it holds
# no AWS credentials, so only these three values are baked into the build.
#
# Usage: ./build-frontend.sh <api_url> <user_pool_id> <user_pool_client_id>

set -e

if [ $# -ne 3 ]; then
    echo "Usage: $0 <api_url> <user_pool_id> <user_pool_client_id>"
    exit 1
fi

API_URL="$1"
USER_POOL_ID="$2"
USER_POOL_CLIENT_ID="$3"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(cd "$SCRIPT_DIR/../frontend" && pwd)"
ENV_FILE="$FRONTEND_DIR/.env.production.local"

echo "Building frontend from: $FRONTEND_DIR"

# Generate .env.production.local with Vite environment variables
echo "Generating .env.production.local..."
cat > "$ENV_FILE" <<EOF
VITE_API_URL=$API_URL
VITE_USER_POOL_ID=$USER_POOL_ID
VITE_USER_POOL_CLIENT_ID=$USER_POOL_CLIENT_ID
EOF

echo "  API URL:             $API_URL"
echo "  User Pool ID:        $USER_POOL_ID"
echo "  User Pool Client ID: $USER_POOL_CLIENT_ID"

# Run npm build
echo "Running npm run build..."
cd "$FRONTEND_DIR"
npm run build
cd "$SCRIPT_DIR"

echo ""
echo "Frontend built successfully!"
echo "  Output: $FRONTEND_DIR/dist"
