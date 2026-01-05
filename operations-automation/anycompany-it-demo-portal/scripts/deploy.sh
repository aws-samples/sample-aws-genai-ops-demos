#!/bin/bash

# Bash deployment script for AnyCompany IT Portal Demo

set -e

REGION="us-east-1"
SKIP_BUILD=false
DESTROY_INFRA=false
POPULATE_DATA=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --region)
            REGION="$2"
            shift 2
            ;;
        --skip-build)
            SKIP_BUILD=true
            shift
            ;;
        --destroy-infra)
            DESTROY_INFRA=true
            shift
            ;;
        --populate-data)
            POPULATE_DATA=true
            shift
            ;;
        *)
            echo "Unknown option $1"
            exit 1
            ;;
    esac
done

echo "=== AnyCompany IT Portal Demo Deployment ==="

if [ "$DESTROY_INFRA" = true ]; then
    echo "Destroying infrastructure..."
    
    # Use shared CDK destroy script
    ../../shared/scripts/deploy-cdk.sh -CdkDirectory infrastructure/cdk -DestroyStack
    
    echo "Infrastructure destruction completed"
    exit 0
fi

# Use shared prerequisites check
echo "Checking prerequisites..."
../../shared/scripts/check-prerequisites.sh --require-cdk

# Deploy CDK infrastructure using shared script
echo "Deploying AWS infrastructure..."
../../shared/scripts/deploy-cdk.sh -CdkDirectory infrastructure/cdk

if [ $? -ne 0 ]; then
    echo "Error: CDK deployment failed"
    exit 1
fi

# Get CDK outputs
echo "Getting CDK stack outputs..."

# Get outputs using AWS CLI
STACK_NAME="AnyCompanyITPortalStack"
OUTPUTS=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs" --output json --no-cli-pager 2>&1)

if [ $? -eq 0 ]; then
    echo "=== Deployment Outputs ==="
    
    WEBSITE_URL=$(echo "$OUTPUTS" | python3 -c "import sys, json; outputs = json.load(sys.stdin); print(next((o['OutputValue'] for o in outputs if o['OutputKey'] == 'WebsiteURL'), ''))")
    API_ENDPOINT=$(echo "$OUTPUTS" | python3 -c "import sys, json; outputs = json.load(sys.stdin); print(next((o['OutputValue'] for o in outputs if o['OutputKey'] == 'APIEndpoint'), ''))")
    S3_BUCKET=$(echo "$OUTPUTS" | python3 -c "import sys, json; outputs = json.load(sys.stdin); print(next((o['OutputValue'] for o in outputs if o['OutputKey'] == 'S3BucketName'), ''))")
    CLOUDFRONT_ID=$(echo "$OUTPUTS" | python3 -c "import sys, json; outputs = json.load(sys.stdin); print(next((o['OutputValue'] for o in outputs if o['OutputKey'] == 'CloudFrontDistributionId'), ''))")
    
    echo "Website URL: $WEBSITE_URL"
    echo "API Endpoint: $API_ENDPOINT"
    echo "S3 Bucket: $S3_BUCKET"
    echo "CloudFront Distribution: $CLOUDFRONT_ID"
else
    echo "⚠ Could not retrieve stack outputs, continuing..."
fi

# Upload website files to S3
if [ ! -z "$S3_BUCKET" ]; then
    echo "Uploading static HTML portals to S3..."
    
    # Generate config.js with the correct API endpoint
    cat > frontend/config.js << EOF
// Configuration file - generated during deployment
window.APP_CONFIG = {
    apiBaseUrl: '$API_ENDPOINT'
};
EOF
    echo "Generated config.js with API endpoint: $API_ENDPOINT"
    
    aws s3 sync frontend/ "s3://$S3_BUCKET" --delete --no-cli-pager
    
    # Invalidate CloudFront cache
    if [ ! -z "$CLOUDFRONT_ID" ]; then
        echo "Invalidating CloudFront cache..."
        aws cloudfront create-invalidation --distribution-id "$CLOUDFRONT_ID" --paths "/*" --no-cli-pager
    fi
fi

# Populate mock data
if [ "$POPULATE_DATA" = true ]; then
    echo "Populating mock data..."
    python3 scripts/seed-data.py "$REGION"
fi

echo "=== Deployment Complete ==="
echo ""
echo "🌐 Website URL: $WEBSITE_URL"
echo "🔗 API Endpoint: $API_ENDPOINT"
echo ""
echo "Next Steps:"
echo "1. Open the website URL to access the IT Portal Demo"
echo "2. Navigate between different portals to see the mock data"
echo "3. Use this environment for AI automation testing"
echo ""
echo "To destroy the infrastructure later, run:"
echo "./scripts/deploy.sh --destroy-infra"