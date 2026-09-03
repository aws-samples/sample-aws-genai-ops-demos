#!/bin/bash
# GenAI Ops Demo Library - Shared Prerequisites Check
# This script validates common requirements across all demos

set -e  # Exit on error

# Parse command line arguments
REQUIRED_SERVICE=""
MIN_AWS_CLI_VERSION="2.31.13"
MIN_PYTHON_VERSION=""
MIN_NODE_VERSION=""
SKIP_SERVICE_CHECK=false
REQUIRE_CDK=false
REQUIRE_KUBECTL=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --required-service)
            REQUIRED_SERVICE="$2"
            shift 2
            ;;
        --min-aws-cli-version)
            MIN_AWS_CLI_VERSION="$2"
            shift 2
            ;;
        --min-python-version)
            MIN_PYTHON_VERSION="$2"
            shift 2
            ;;
        --min-node-version)
            MIN_NODE_VERSION="$2"
            shift 2
            ;;
        --skip-service-check)
            SKIP_SERVICE_CHECK=true
            shift
            ;;
        --require-cdk)
            REQUIRE_CDK=true
            shift
            ;;
        --require-kubectl)
            REQUIRE_KUBECTL=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo -e "\033[0;36m=== GenAI Ops Demo Prerequisites Check (Shared Script) ===\033[0m"

# Check Python version (if required)
if [ -n "$MIN_PYTHON_VERSION" ]; then
    echo -e "\n\033[0;33mChecking Python version...\033[0m"
    PYTHON_VERSION=$(python3 --version 2>&1 || python --version 2>&1)
    if [[ $PYTHON_VERSION =~ Python\ ([0-9]+)\.([0-9]+) ]]; then
        PY_MAJOR=${BASH_REMATCH[1]}
        PY_MINOR=${BASH_REMATCH[2]}
        IFS='.' read -r MIN_PY_MAJOR MIN_PY_MINOR <<< "$MIN_PYTHON_VERSION"
        
        if [ "$PY_MAJOR" -gt "$MIN_PY_MAJOR" ] || [ "$PY_MAJOR" -eq "$MIN_PY_MAJOR" -a "$PY_MINOR" -ge "$MIN_PY_MINOR" ]; then
            echo -e "\033[0;32m      ✓ Python $PY_MAJOR.$PY_MINOR (required: $MIN_PYTHON_VERSION+)\033[0m"
        else
            echo -e "\033[0;31m      ❌ Python $MIN_PYTHON_VERSION+ required (found $PY_MAJOR.$PY_MINOR)\033[0m"
            echo -e "\033[0;36m      Install from: https://python.org\033[0m"
            exit 1
        fi
    else
        echo -e "\033[0;31m      ❌ Python not found. Install from https://python.org\033[0m"
        exit 1
    fi
fi

# Check Node.js version (if required for CDK)
if [ "$REQUIRE_CDK" = true ] || [ -n "$MIN_NODE_VERSION" ]; then
    NODE_MIN=${MIN_NODE_VERSION:-20}
    echo -e "\n\033[0;33mChecking Node.js version...\033[0m"
    if ! command -v node &> /dev/null; then
        echo -e "\033[0;31m      ❌ Node.js not found. Install from https://nodejs.org\033[0m"
        exit 1
    fi
    NODE_VERSION=$(node --version 2>&1)
    if [[ $NODE_VERSION =~ v([0-9]+) ]]; then
        NODE_MAJOR=${BASH_REMATCH[1]}
        if [ "$NODE_MAJOR" -ge "$NODE_MIN" ]; then
            echo -e "\033[0;32m      ✓ Node.js v$NODE_MAJOR (required: v$NODE_MIN+)\033[0m"
        else
            echo -e "\033[0;31m      ❌ Node.js v$NODE_MIN+ required (found v$NODE_MAJOR)\033[0m"
            echo -e "\033[0;36m      Install from: https://nodejs.org\033[0m"
            exit 1
        fi
    else
        echo -e "\033[0;31m      ❌ Node.js not found. Install from https://nodejs.org\033[0m"
        exit 1
    fi
fi

# Check kubectl (if required for EKS demos)
if [ "$REQUIRE_KUBECTL" = true ]; then
    echo -e "\n\033[0;33mChecking kubectl...\033[0m"
    if command -v kubectl &> /dev/null; then
        echo -e "\033[0;32m      ✓ kubectl installed\033[0m"
    else
        echo -e "\033[0;31m      ❌ kubectl not found. Install from https://kubernetes.io/docs/tasks/tools/\033[0m"
        exit 1
    fi
fi

# Verify AWS credentials
echo -e "\n\033[0;33mVerifying AWS credentials...\033[0m"
echo -e "\033[0;90m      (Checking AWS CLI configuration and validating access)\033[0m"

# Check if AWS credentials are configured
if ! CALLER_IDENTITY=$(aws sts get-caller-identity 2>&1); then
    echo -e "\033[0;31mAWS credentials are not configured or have expired\033[0m"
    echo -e "\n\033[0;33mPlease configure AWS credentials using one of these methods:\033[0m"
    echo -e "\033[0;36m  1. Run: aws configure\033[0m"
    echo -e "\033[0;36m  2. Set environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY\033[0m"
    echo -e "\033[0;36m  3. Use AWS SSO: aws sso login --profile <profile-name>\033[0m"
    echo -e "\n\033[0;90mFor more info: https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-quickstart.html\033[0m"
    exit 1
fi

# Display current AWS identity
ACCOUNT_ID=$(echo "$CALLER_IDENTITY" | grep -o '"Account": "[^"]*' | cut -d'"' -f4)
ARN=$(echo "$CALLER_IDENTITY" | grep -o '"Arn": "[^"]*' | cut -d'"' -f4)
echo -e "\033[0;32m      Authenticated as: $ARN\033[0m"
echo -e "\033[0;32m      AWS Account: $ACCOUNT_ID\033[0m"

# Check AWS CLI version
echo -e "\n\033[0;33mChecking AWS CLI version...\033[0m"
AWS_VERSION=$(aws --version 2>&1)
if [[ $AWS_VERSION =~ aws-cli/([0-9]+)\.([0-9]+)\.([0-9]+) ]]; then
    MAJOR=${BASH_REMATCH[1]}
    MINOR=${BASH_REMATCH[2]}
    PATCH=${BASH_REMATCH[3]}
    echo -e "\033[0;90m      Current version: aws-cli/$MAJOR.$MINOR.$PATCH\033[0m"
    
    # Parse minimum version requirement
    IFS='.' read -r MIN_MAJOR MIN_MINOR MIN_PATCH <<< "$MIN_AWS_CLI_VERSION"
    
    # Check if version meets minimum requirement
    if [ "$MAJOR" -gt "$MIN_MAJOR" ] || \
       [ "$MAJOR" -eq "$MIN_MAJOR" -a "$MINOR" -gt "$MIN_MINOR" ] || \
       [ "$MAJOR" -eq "$MIN_MAJOR" -a "$MINOR" -eq "$MIN_MINOR" -a "$PATCH" -ge "$MIN_PATCH" ]; then
        echo -e "\033[0;32m      ✓ AWS CLI version is compatible\033[0m"
    else
        echo -e "\033[0;31m      ❌ AWS CLI version $MIN_AWS_CLI_VERSION or later is required\033[0m"
        echo -e ""
        echo -e "\033[0;33m      Your current version: aws-cli/$MAJOR.$MINOR.$PATCH\033[0m"
        echo -e "\033[0;33m      Required version: aws-cli/$MIN_AWS_CLI_VERSION or later\033[0m"
        echo -e ""
        echo -e "\033[0;33m      Please upgrade your AWS CLI:\033[0m"
        echo -e "\033[0;36m        https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html\033[0m"
        exit 1
    fi
else
    echo -e "\033[0;33m      ⚠ Could not parse AWS CLI version, continuing anyway...\033[0m"
fi

# Check AWS region configuration
echo -e "\n\033[0;33mChecking AWS region configuration...\033[0m"

# Source shared region detection utility (env var → CLI config → fallback)
_PREREQ_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_PREREQ_SCRIPT_DIR/../utils/aws-utils.sh"
unset _PREREQ_SCRIPT_DIR
CURRENT_REGION=$(get_aws_region)

# Reject the us-east-1 fallback — if get_aws_region fell back, the user has no region configured
# We check by verifying that the region came from an actual source, not the fallback
REGION_FROM_ENV="${AWS_DEFAULT_REGION:-${AWS_REGION:-}}"
REGION_FROM_CLI=$(aws configure get region 2>/dev/null || true)
if [ -z "$REGION_FROM_ENV" ] && [ -z "$REGION_FROM_CLI" ]; then
    echo -e "\033[0;31m      ❌ No AWS region configured\033[0m"
    echo -e ""
    echo -e "\033[0;33m      Please configure your AWS region using one of:\033[0m"
    echo -e "\033[0;36m        export AWS_REGION=<your-region>\033[0m"
    echo -e "\033[0;36m        export AWS_DEFAULT_REGION=<your-region>\033[0m"
    echo -e "\033[0;36m        aws configure set region <your-region>\033[0m"
    echo -e ""
    echo -e "\033[0;90m      For supported regions, see AWS service documentation\033[0m"
    exit 1
fi
echo -e "\033[0;90m      Target region: $CURRENT_REGION\033[0m"

# ─── AWS DevOps Agent region resolution ──────────────────────────────────────
# An Agent Space is a regional resource and AWS DevOps Agent is available in a
# subset of AWS Regions. Resolution is opt-in and deliberately simple:
#
#   1. DEVOPS_AGENT_REGION — explicit override, wins outright
#   2. the resolved deploy region — same-region default
#
# Same-region is the default because an Agent Space discovers and monitors
# resources across ALL Regions of an associated account. Splitting the two is
# therefore only needed when the deploy Region cannot host an Agent Space, or
# for data-residency / console-availability reasons.
#
# No hardcoded Region list lives here: the "devops-agent" service check below
# probes the live API instead, so a Region stays usable the moment the service
# reaches it. See shared/README.md ("AWS DevOps Agent Region") for the full
# convention and its caveats.
#
# IMPORTANT: this script is *sourced* by most demos, so any assignment lands in the
# caller's shell. The resolved value is therefore held in an internal variable and
# published as DEVOPS_AGENT_REGION only when the caller actually asked for the
# devops-agent check (see the export block at the end). Demos that do their own
# `${DEVOPS_AGENT_REGION:-<default>}` defaulting must keep seeing it unset.
_DEVOPS_AGENT_REGION_EFFECTIVE="${DEVOPS_AGENT_REGION:-$CURRENT_REGION}"
if [ "$_DEVOPS_AGENT_REGION_EFFECTIVE" != "$CURRENT_REGION" ]; then
    echo -e "\033[0;36m      ℹ DevOps Agent region overridden: $_DEVOPS_AGENT_REGION_EFFECTIVE (deploy region: $CURRENT_REGION)\033[0m"
fi

# Check specific AWS service availability (if specified)
if [ "$SKIP_SERVICE_CHECK" = false ] && [ -n "$REQUIRED_SERVICE" ]; then
    # DevOps Agent is probed in the Agent Space region (which may differ from the
    # deploy region); every other service is probed in the deploy region.
    if [ "$(echo "$REQUIRED_SERVICE" | tr '[:upper:]' '[:lower:]')" = "devops-agent" ]; then
        SERVICE_CHECK_REGION="$_DEVOPS_AGENT_REGION_EFFECTIVE"
    else
        SERVICE_CHECK_REGION="$CURRENT_REGION"
    fi
    echo -e "\n\033[0;33mChecking $REQUIRED_SERVICE availability in $SERVICE_CHECK_REGION...\033[0m"

    case "$(echo "$REQUIRED_SERVICE" | tr '[:upper:]' '[:lower:]')" in
        "bedrock")
            if ! aws bedrock list-foundation-models --region "$CURRENT_REGION" --no-cli-pager > /dev/null 2>&1; then
                echo -e "\033[0;31m      ❌ Amazon Bedrock is not available in region: $CURRENT_REGION\033[0m"
                echo -e ""
                echo -e "\033[0;90m      For supported regions, see:\033[0m"
                echo -e "\033[0;90m      https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-regions.html\033[0m"
                exit 1
            fi
            echo -e "\033[0;32m      ✓ Amazon Bedrock is available in $CURRENT_REGION\033[0m"
            ;;
        "agentcore")
            if ! aws bedrock-agentcore-control list-agent-runtimes --region "$CURRENT_REGION" --max-results 1 > /dev/null 2>&1; then
                echo -e "\033[0;31m      ❌ Amazon Bedrock AgentCore is not available in region: $CURRENT_REGION\033[0m"
                echo -e ""
                echo -e "\033[0;90m      For supported regions, see:\033[0m"
                echo -e "\033[0;90m      https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-regions.html\033[0m"
                exit 1
            fi
            echo -e "\033[0;32m      ✓ Amazon Bedrock AgentCore is available in $CURRENT_REGION\033[0m"
            ;;
        "agentcore-browser")
            if ! aws bedrock-agentcore-control list-browsers --region "$CURRENT_REGION" > /dev/null 2>&1; then
                echo -e "\033[0;31m      ❌ AgentCore Browser Tool is not available in region: $CURRENT_REGION\033[0m"
                echo -e ""
                echo -e "\033[0;90m      For supported regions, see:\033[0m"
                echo -e "\033[0;90m      https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-building-agents.html\033[0m"
                exit 1
            fi
            echo -e "\033[0;32m      ✓ AgentCore Browser Tool is available in $CURRENT_REGION\033[0m"
            ;;
        "devops-agent")
            # Probe the actual AWS DevOps Agent service (aidevops) in the Agent
            # Space region. A region with no reachable aidevops endpoint fails at
            # endpoint resolution and we stop here. Probing the live service avoids
            # a hardcoded region list, which rots and would reject regions where the
            # service is already reachable ahead of the docs. (This is a different
            # service from Bedrock AgentCore, which the "agentcore" case probes.)
            if ! aws devops-agent list-agent-spaces --region "$_DEVOPS_AGENT_REGION_EFFECTIVE" --no-cli-pager > /dev/null 2>&1; then
                echo -e "\033[0;31m      ❌ AWS DevOps Agent is not available in region: $_DEVOPS_AGENT_REGION_EFFECTIVE\033[0m"
                echo -e ""
                echo -e "\033[0;33m      Pin the Agent Space to a supported Region and re-run:\033[0m"
                echo -e "\033[0;36m        export DEVOPS_AGENT_REGION=<supported-region>\033[0m"
                echo -e ""
                echo -e "\033[0;90m      An Agent Space monitors resources in ALL Regions of an associated\033[0m"
                echo -e "\033[0;90m      account, so it does not need to live in your deploy Region.\033[0m"
                echo -e "\033[0;90m      https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-supported-regions.html\033[0m"
                exit 1
            fi
            if [ "$_DEVOPS_AGENT_REGION_EFFECTIVE" != "$CURRENT_REGION" ]; then
                echo -e "\033[0;32m      ✓ AWS DevOps Agent is available in $_DEVOPS_AGENT_REGION_EFFECTIVE (Agent Space region)\033[0m"
            else
                echo -e "\033[0;32m      ✓ AWS DevOps Agent is available in $_DEVOPS_AGENT_REGION_EFFECTIVE\033[0m"
            fi
            ;;
        "nova-act")
            if ! aws nova-act list-workflow-definitions --region "$CURRENT_REGION" > /dev/null 2>&1; then
                echo -e "\033[0;31m      ❌ Amazon Nova Act is not available in region: $CURRENT_REGION\033[0m"
                echo -e ""
                echo -e "\033[0;90m      Nova Act is currently available in us-east-1\033[0m"
                echo -e "\033[0;90m      https://aws.amazon.com/nova/act/\033[0m"
                exit 1
            fi
            echo -e "\033[0;32m      ✓ Amazon Nova Act is available in $CURRENT_REGION\033[0m"
            ;;
        "transform")
            # AWS Transform service availability (informational only)
            echo -e "\033[0;36m      ℹ AWS Transform service check (informational)\033[0m"
            echo -e "\033[0;90m        AWS Transform service must be available in $CURRENT_REGION\033[0m"
            echo -e "\033[0;90m        Please check the documentation for AWS Transform supported regions:\033[0m"
            echo -e "\033[0;90m        https://docs.aws.amazon.com/transform/latest/userguide/regions.html\033[0m"
            ;;
        *)
            echo -e "\033[0;33m      ⚠ Unknown service '$REQUIRED_SERVICE', skipping service check...\033[0m"
            ;;
    esac
else
    echo -e "\n\033[0;33mSkipping service availability check...\033[0m"
fi

echo -e "\n\033[0;32m✅ All prerequisites validated successfully!\033[0m"
echo -e "\033[0;36mReady to proceed with demo deployment.\033[0m"

# Export variables for use by calling script
export AWS_ACCOUNT_ID="$ACCOUNT_ID"
export AWS_REGION="$CURRENT_REGION"
export AWS_ARN="$ARN"
# Agent Space region for AWS DevOps Agent demos — equals $AWS_REGION unless
# DEVOPS_AGENT_REGION was set by the caller.
#
# Published ONLY when the caller requested the devops-agent check. This script is
# sourced, so unconditionally setting this name would pre-empt a demo's own
# `${DEVOPS_AGENT_REGION:-<default>}` defaulting and silently relocate its Agent
# Space. Demos that want the resolved value ask for the check; demos that manage
# the variable themselves keep seeing it unset.
if [ "$(echo "$REQUIRED_SERVICE" | tr '[:upper:]' '[:lower:]')" = "devops-agent" ]; then
    export DEVOPS_AGENT_REGION="$_DEVOPS_AGENT_REGION_EFFECTIVE"
fi
unset _DEVOPS_AGENT_REGION_EFFECTIVE