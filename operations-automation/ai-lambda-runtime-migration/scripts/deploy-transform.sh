#!/bin/bash
# AI Lambda Runtime Migration Assistant - Transform Build Plane Deploy/Destroy Script
# macOS/Linux version - mirrors deploy-transform.ps1
#
# Opt-in Phase-2 build plane: a persistent CodeBuild project running AWS
# Transform (atx), its IAM role, and an EventBridge rule reporting terminal
# build state. The two Phase-2 Lambdas (orchestrator + completion handler) are
# created in the Pipeline stack under the same `transform` CDK context.
#
# This is NOT part of deploy-all. It deploys ONLY the Transform stack
# (LambdaRuntimeMigrationTransform-<region>), gated behind the `transform` CDK
# context, through the SHARED deploy-cdk script (never `cdk deploy` directly),
# then uploads the buildspec to the artifacts bucket.
#
#   ./scripts/deploy-transform.sh             # deploy the build plane
#   ./scripts/deploy-transform.sh --destroy   # tear it down
#
# Prerequisite: deploy the mock fixture and run a scan first, so there are
# functions to migrate. Migrations are SLOW (tens of minutes to hours).

set -e  # Exit on error

DESTROY=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --destroy) DESTROY=true; shift ;;
        *) echo "Unknown option: $1"; echo "Usage: $0 [--destroy]"; exit 1 ;;
    esac
done

# Run from the demo root so shared-script relative paths and frontend/dist
# resolve exactly as deploy-all.sh sees them (../../shared/...).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

echo -e "\033[0;36m=== AI Lambda Runtime Migration - Transform Build Plane ===\033[0m"

# Run shared prerequisites check (transform service: the pinned Transform CLI versions the buildspec installs).
echo -e "\n\033[0;33mRunning prerequisites check...\033[0m"
source ../../shared/scripts/check-prerequisites.sh --required-service "transform" --min-aws-cli-version "2.31.13" --min-python-version "3.12" --require-cdk --min-cdk-version "2.1030.0"

# Create placeholder dist BEFORE any CDK commands. CDK synthesizes ALL stacks in
# the app even when targeting one, and FrontendStack references frontend/dist,
# so it must exist or synth fails on a missing asset.
echo -e "\n\033[0;33mEnsuring placeholder frontend build exists...\033[0m"
if [ ! -d "frontend/dist" ]; then
    mkdir -p frontend/dist
    echo "<!DOCTYPE html><html><body><h1>Building...</h1></body></html>" > frontend/dist/index.html
else
    echo -e "\033[0;90m      Placeholder already exists, skipping...\033[0m"
fi

# Region + stack names (region set by check-prerequisites.sh)
region="$AWS_REGION"
stack_name="LambdaRuntimeMigrationTransform-$region"
data_stack_name="LambdaRuntimeMigrationData-$region"

if [ "$DESTROY" = true ]; then
    echo -e "\n\033[0;33mDestroying Transform build-plane stack...\033[0m"
    echo -e "\033[0;90m      (Removing the CodeBuild project, build role and EventBridge rule)\033[0m"
    # Pass the same transform=true context so the stack synthesizes during destroy.
    ../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "$stack_name" --skip-bootstrap --destroy --cdk-context "transform=true"

    echo -e "\n\033[0;32m========================================\033[0m"
    echo -e "\033[0;32m  Transform Build Plane Destroyed\033[0m"
    echo -e "\033[0;32m========================================\033[0m"
    echo -e "\033[0;36m  Stack:  $stack_name\033[0m"
    echo -e "\033[0;36m  Region: $region\033[0m"
    exit 0
fi

# Deploy ONLY the Transform stack via the SHARED script (never `cdk deploy` directly).
echo -e "\n\033[0;33mDeploying Transform build-plane stack...\033[0m"
echo -e "\033[0;90m      (Creating the CodeBuild project, build role and EventBridge rule)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "$stack_name" --skip-bootstrap --cdk-context "transform=true"

# Read the artifacts bucket name from the DataStack output, then upload the
# buildspec the CodeBuild project reads from config/.
bucket=$(aws cloudformation describe-stacks --stack-name "$data_stack_name" --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text --no-cli-pager)
project_name=$(aws cloudformation describe-stacks --stack-name "$stack_name" --query "Stacks[0].Outputs[?OutputKey=='TransformProjectName'].OutputValue" --output text --no-cli-pager)

echo -e "\n\033[0;33mUploading buildspec to the artifacts bucket...\033[0m"
# Object MUST be named buildspec.yml: CodeBuild's S3 single-file source looks for
# a file named buildspec.yml in the downloaded source root.
aws s3 cp cdk/buildspec/transform-migrate.buildspec.yml "s3://$bucket/config/buildspec.yml" --no-cli-pager

echo -e "\n\033[0;32m========================================\033[0m"
echo -e "\033[0;32m  Transform Build Plane Deployed!\033[0m"
echo -e "\033[0;32m========================================\033[0m"
echo -e "\033[0;36m  CodeBuild project: $project_name\033[0m"
echo -e "\033[0;36m  Artifacts bucket:  $bucket\033[0m"
echo -e "\033[0;36m  Region:            $region\033[0m"
echo -e "\n\033[0;33mNote: Migrations run AWS Transform in CodeBuild and are SLOW -\033[0m"
echo -e "\033[0;33m      tens of minutes to hours per function. Deploy the mock fixture\033[0m"
echo -e "\033[0;33m      (scripts/deploy-mock.sh) and run a scan first so there are\033[0m"
echo -e "\033[0;33m      functions to migrate.\033[0m"
