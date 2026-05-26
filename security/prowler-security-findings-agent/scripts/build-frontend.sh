#!/bin/bash
# Build the React dashboard with Vite env vars injected from CDK outputs.
#
# Usage: ./build-frontend.sh <region> <user_pool_id> <user_pool_client_id> <identity_pool_id> <api_function_url>

set -euo pipefail

if [ $# -ne 5 ]; then
    echo "Usage: $0 <region> <user_pool_id> <user_pool_client_id> <identity_pool_id> <api_function_url>"
    exit 1
fi

REGION="$1"
USER_POOL_ID="$2"
USER_POOL_CLIENT_ID="$3"
IDENTITY_POOL_ID="$4"
API_URL="$5"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(cd "$SCRIPT_DIR/../frontend" && pwd)"

echo "[frontend-build] generating .env.production.local..."
cat > "$FRONTEND_DIR/.env.production.local" <<EOF
VITE_REGION=$REGION
VITE_USER_POOL_ID=$USER_POOL_ID
VITE_USER_POOL_CLIENT_ID=$USER_POOL_CLIENT_ID
VITE_IDENTITY_POOL_ID=$IDENTITY_POOL_ID
VITE_API_FUNCTION_URL=$API_URL
EOF

echo "[frontend-build] npm install..."
cd "$FRONTEND_DIR"
npm install --silent

echo "[frontend-build] npm run build..."
npm run build

echo "[frontend-build] done — output: $FRONTEND_DIR/dist"
