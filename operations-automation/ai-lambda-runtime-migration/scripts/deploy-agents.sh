#!/bin/bash
# Package all 3 agents, upload to S3, and redeploy Runtime stack if needed
# Usage: ./scripts/deploy-agents.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Check prerequisites using shared script
source "$DEMO_ROOT/../../shared/scripts/check-prerequisites.sh" --required-service agentcore --require-cdk
region=$AWS_REGION
data_stack="LambdaRuntimeMigrationData-$region"
runtime_stack="LambdaRuntimeMigrationRuntime-$region"

# Get S3 bucket
bucket=$(aws cloudformation describe-stacks --stack-name "$data_stack" --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text --no-cli-pager)
if [ -z "$bucket" ]; then echo "ERROR: Cannot find S3 bucket"; exit 1; fi
echo "S3 Bucket: $bucket"

agents=("discover" "analyze" "transform")
shared_dir="$DEMO_ROOT/agent/_shared"

for agent in "${agents[@]}"; do
    echo ""
    echo "=== Packaging $agent agent ==="
    agent_dir="$DEMO_ROOT/agent/$agent"
    deploy_dir="$agent_dir/deployment_package"
    zip_path="$agent_dir/deployment_package.zip"

    # Clean
    [ -d "$deploy_dir" ] && rm -rf "$deploy_dir"
    [ -f "$zip_path" ] && rm -f "$zip_path"

    # Install deps
    echo "  Installing dependencies..."
    uv pip install -r "$agent_dir/requirements.txt" --python-platform aarch64-unknown-linux-gnu --python-version 3.13 --target "$deploy_dir"

    # Copy main.py + shared constants
    cp "$agent_dir/main.py" "$deploy_dir/main.py"
    if [ -d "$shared_dir" ]; then
        mkdir -p "$deploy_dir/_shared"
        cp "$shared_dir"/* "$deploy_dir/_shared/"
    fi

    # Zip
    echo "  Creating zip..."
    cd "$deploy_dir"
    zip -r "$zip_path" . -q
    cd "$SCRIPT_DIR"
    rm -rf "$deploy_dir"

    zip_size=$(du -h "$zip_path" | cut -f1)
    echo "  Packaged: $zip_size"

    # Upload
    echo "  Uploading to S3..."
    aws s3 cp "$zip_path" "s3://$bucket/agent/$agent/deployment_package.zip" --no-cli-pager > /dev/null
    echo "  Uploaded to s3://$bucket/agent/$agent/deployment_package.zip"
done

# Deploy Runtime stack (picks up IAM changes)
echo ""
echo "=== Deploying Runtime stack ==="
timestamp=$(date +%Y%m%d%H%M%S)
cd "$DEMO_ROOT/cdk"
npx cdk deploy "$runtime_stack" --output "cdk.out.$timestamp" --no-cli-pager --require-approval never
cd "$SCRIPT_DIR"

# Force-update each runtime so AgentCore picks up the new S3 zip
echo ""
echo "=== Force-updating AgentCore runtimes ==="
role_arn=$(aws cloudformation describe-stacks --stack-name "$runtime_stack" --query "Stacks[0].Outputs[?OutputKey=='AgentRoleArn'].OutputValue" --output text --no-cli-pager)

declare -A output_keys=(
    [discover]="DiscoverRuntimeArn"
    [analyze]="AnalyzeRuntimeArn"
    [transform]="TransformRuntimeArn"
)

for agent in "${agents[@]}"; do
    key="${output_keys[$agent]}"
    arn=$(aws cloudformation describe-stacks --stack-name "$runtime_stack" --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" --output text --no-cli-pager)
    rt_id="${arn##*/}"
    echo "  Updating $agent ($rt_id)..."
    artifact="{\"codeConfiguration\":{\"code\":{\"s3\":{\"bucket\":\"$bucket\",\"prefix\":\"agent/$agent/deployment_package.zip\"}},\"runtime\":\"PYTHON_3_13\",\"entryPoint\":[\"main.py\"]}}"
    env_vars="TABLE_NAME=lambda-runtime-migration,BUCKET_NAME=$bucket,AWS_DEFAULT_REGION=$region"
    if aws bedrock-agentcore-control update-agent-runtime --agent-runtime-id "$rt_id" --role-arn "$role_arn" --network-configuration networkMode=PUBLIC --agent-runtime-artifact "$artifact" --environment-variables "$env_vars" --region "$region" --no-cli-pager > /dev/null 2>&1; then
        echo "  Updated $agent"
    else
        echo "  WARNING: update-agent-runtime failed for $agent"
    fi
done

# Wait for runtimes to become READY
echo ""
echo "=== Waiting for runtimes to be READY ==="
for agent in "${agents[@]}"; do
    key="${output_keys[$agent]}"
    arn=$(aws cloudformation describe-stacks --stack-name "$runtime_stack" --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" --output text --no-cli-pager)
    rt_id="${arn##*/}"
    waited=0
    max_wait=120
    while [ $waited -lt $max_wait ]; do
        status=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$rt_id" --region "$region" --query "status" --output text --no-cli-pager 2>/dev/null)
        if [ "$status" = "READY" ]; then echo "  $agent is READY"; break; fi
        sleep 5
        waited=$((waited + 5))
    done
    if [ $waited -ge $max_wait ]; then echo "  WARNING: $agent still not READY after ${max_wait}s (status: $status)"; fi
done

echo ""
echo "=== All agents deployed and updated ==="
echo "Try 'Scan' in the dashboard now."
