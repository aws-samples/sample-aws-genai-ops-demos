#!/bin/bash
# Build frontend with injected Vite environment variables
# Parameters are passed from the main deploy script after retrieving CDK stack outputs.
#
# Usage: ./build-frontend.sh <region> <user_pool_id> <user_pool_client_id> <identity_pool_id> <discover_arn> <analyze_arn> <transform_arn>

set -e

if [ $# -ne 7 ]; then
    echo "Usage: $0 <region> <user_pool_id> <user_pool_client_id> <identity_pool_id> <discover_arn> <analyze_arn> <transform_arn>"
    exit 1
fi

REGION="$1"
USER_POOL_ID="$2"
USER_POOL_CLIENT_ID="$3"
IDENTITY_POOL_ID="$4"
DISCOVER_RUNTIME_ARN="$5"
ANALYZE_RUNTIME_ARN="$6"
TRANSFORM_RUNTIME_ARN="$7"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(cd "$SCRIPT_DIR/../frontend" && pwd)"
ENV_FILE="$FRONTEND_DIR/.env.production.local"

echo "Building frontend from: $FRONTEND_DIR"

# Generate .env.production.local with Vite environment variables
echo "Generating .env.production.local..."
cat > "$ENV_FILE" <<EOF
VITE_REGION=$REGION
VITE_USER_POOL_ID=$USER_POOL_ID
VITE_USER_POOL_CLIENT_ID=$USER_POOL_CLIENT_ID
VITE_IDENTITY_POOL_ID=$IDENTITY_POOL_ID
VITE_DISCOVER_RUNTIME_ARN=$DISCOVER_RUNTIME_ARN
VITE_ANALYZE_RUNTIME_ARN=$ANALYZE_RUNTIME_ARN
VITE_TRANSFORM_RUNTIME_ARN=$TRANSFORM_RUNTIME_ARN
EOF

echo "  Region:              $REGION"
echo "  User Pool ID:        $USER_POOL_ID"
echo "  User Pool Client:    $USER_POOL_CLIENT_ID"
echo "  Identity Pool ID:    $IDENTITY_POOL_ID"
echo "  Discover Runtime:    $DISCOVER_RUNTIME_ARN"
echo "  Analyze Runtime:     $ANALYZE_RUNTIME_ARN"
echo "  Transform Runtime:   $TRANSFORM_RUNTIME_ARN"

# Run npm build
echo "Running npm run build..."
cd "$FRONTEND_DIR"
npm run build
cd "$SCRIPT_DIR"

echo ""
echo "Frontend built successfully!"
echo "  Output: $FRONTEND_DIR/dist"
