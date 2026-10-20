#!/bin/bash
# AI Lambda Runtime Migration Assistant - Mock Fixture Deploy/Destroy Script
# macOS/Linux version - mirrors deploy-mock.ps1
#
# Opt-in, self-contained test fixture: a CodeCommit repository plus three
# deprecated-runtime Lambda functions (.NET / Node.js / Python), each tagged so
# the demo's code-behind detection resolves the function to that repository.
#
# This is NOT part of deploy-all. It deploys ONLY the mock stack
# (LambdaRuntimeMigrationMock-<region>), gated behind the `mockFixture` CDK
# context, through the SHARED deploy-cdk script (never `cdk deploy` directly).
#
#   ./scripts/deploy-mock.sh             # deploy the mock fixture
#   ./scripts/deploy-mock.sh --destroy   # tear it down
#
# Note: Trusted Advisor's deprecated-runtime detection has a lag, so the mock
# functions may take time (often hours) to appear in the dashboard inventory.

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

echo -e "\033[0;36m=== AI Lambda Runtime Migration - Mock Fixture ===\033[0m"

# Run shared prerequisites check (same signature as deploy-all.sh)
echo -e "\n\033[0;33mRunning prerequisites check...\033[0m"
source ../../shared/scripts/check-prerequisites.sh --min-aws-cli-version "2.31.13" --min-python-version "3.12" --require-cdk --min-cdk-version "2.1030.0"

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

# Region + stack name (region set by check-prerequisites.sh)
region="$AWS_REGION"
stack_name="LambdaRuntimeMigrationMock-$region"

if [ "$DESTROY" = true ]; then
    echo -e "\n\033[0;33mDestroying mock fixture stack...\033[0m"
    echo -e "\033[0;90m      (Removing the CodeCommit repo and the three deprecated-runtime Lambda functions)\033[0m"
    # Pass the same mockFixture=true context so the stack synthesizes during destroy.
    ../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "$stack_name" --skip-bootstrap --destroy --cdk-context "mockFixture=true"

    echo -e "\n\033[0;32m========================================\033[0m"
    echo -e "\033[0;32m  Mock Fixture Destroyed\033[0m"
    echo -e "\033[0;32m========================================\033[0m"
    echo -e "\033[0;36m  Stack:  $stack_name\033[0m"
    echo -e "\033[0;36m  Region: $region\033[0m"
    exit 0
fi

# Deploy ONLY the mock stack via the SHARED script (never `cdk deploy` directly).
echo -e "\n\033[0;33mDeploying mock fixture stack...\033[0m"
echo -e "\033[0;90m      (Creating the CodeCommit repo and the three deprecated-runtime Lambda functions)\033[0m"
../../shared/scripts/deploy-cdk.sh --cdk-directory "cdk" --stack-name "$stack_name" --skip-bootstrap --cdk-context "mockFixture=true"

# Read back outputs for a user-friendly summary.
repo_url=$(aws cloudformation describe-stacks --stack-name "$stack_name" --query "Stacks[0].Outputs[?OutputKey=='FixtureRepoCloneUrlHttp'].OutputValue" --output text --no-cli-pager)
python_fn=$(aws cloudformation describe-stacks --stack-name "$stack_name" --query "Stacks[0].Outputs[?OutputKey=='MockPythonFunctionName'].OutputValue" --output text --no-cli-pager)
nodejs_fn=$(aws cloudformation describe-stacks --stack-name "$stack_name" --query "Stacks[0].Outputs[?OutputKey=='MockNodejsFunctionName'].OutputValue" --output text --no-cli-pager)
dotnet_fn=$(aws cloudformation describe-stacks --stack-name "$stack_name" --query "Stacks[0].Outputs[?OutputKey=='MockDotnetFunctionName'].OutputValue" --output text --no-cli-pager)

echo -e "\n\033[0;32m========================================\033[0m"
echo -e "\033[0;32m  Mock Fixture Deployed!\033[0m"
echo -e "\033[0;32m========================================\033[0m"
echo -e "\033[0;36m  CodeCommit repo URL: $repo_url\033[0m"
echo -e "\033[0;36m  Functions:\033[0m"
echo -e "\033[0;36m    - $python_fn  (python3.9)\033[0m"
echo -e "\033[0;36m    - $nodejs_fn  (nodejs16.x)\033[0m"
echo -e "\033[0;36m    - $dotnet_fn  (dotnet6)\033[0m"
echo -e "\033[0;36m  Region:              $region\033[0m"
echo -e "\n\033[0;33mNote: Trusted Advisor's deprecated-runtime detection has a lag,\033[0m"
echo -e "\033[0;33m      so these functions may take time (often hours) to appear in the\033[0m"
echo -e "\033[0;33m      dashboard inventory after the next scan.\033[0m"
