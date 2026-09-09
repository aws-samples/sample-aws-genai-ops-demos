#!/bin/bash
# GenAI Ops Demo Library - Shared CDK Deployment Script
# This script handles CDK bootstrap, dependency installation, and deployment

set -e

# Parse arguments
CDK_DIRECTORY=""
STACK_NAME=""
DESTROY_STACK=false
SKIP_BOOTSTRAP=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --cdk-directory)
            CDK_DIRECTORY="$2"
            shift 2
            ;;
        --stack-name)
            STACK_NAME="$2"
            shift 2
            ;;
        --destroy)
            DESTROY_STACK=true
            shift
            ;;
        --skip-bootstrap)
            SKIP_BOOTSTRAP=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 --cdk-directory <path> [--stack-name <name>] [--destroy] [--skip-bootstrap]"
            exit 1
            ;;
    esac
done

if [ -z "$CDK_DIRECTORY" ]; then
    echo "❌ --cdk-directory is required"
    exit 1
fi

# Set PYTHONPATH to include shared utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT"

# Get AWS account and region.
# Resolve region with the same priority the rest of the repo uses (AWS_REGION /
# AWS_DEFAULT_REGION env vars win over the CLI's configured region). A caller that
# exports AWS_REGION to target a specific region -- e.g. build-playbooks.sh, which
# resolves the region itself and invokes Bedrock there -- would otherwise be silently
# overridden by whatever `aws configure get region` returns, deploying the stack to the
# wrong region and breaking the caller's subsequent region-suffixed stack lookup.
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --no-cli-pager)
CURRENT_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
if [ -z "$CURRENT_REGION" ]; then
    CURRENT_REGION=$(aws configure get region)
fi

if [ -z "$CURRENT_REGION" ]; then
    echo -e "\033[0;31m❌ No AWS region configured\033[0m"
    exit 1
fi

echo ""
echo -e "\033[0;36m=== CDK Deployment (Shared Script) ===\033[0m"
echo -e "\033[0;90m      Directory: $CDK_DIRECTORY\033[0m"
echo -e "\033[0;90m      Region: $CURRENT_REGION\033[0m"
echo -e "\033[0;90m      Account: $ACCOUNT_ID\033[0m"

# Verify CDK directory exists
if [ ! -d "$CDK_DIRECTORY" ]; then
    echo -e "\033[0;31m❌ CDK directory not found: $CDK_DIRECTORY\033[0m"
    exit 1
fi

pushd "$CDK_DIRECTORY" > /dev/null

# Install dependencies
echo ""
echo -e "\033[0;33mInstalling CDK dependencies...\033[0m"

# Determine CDK app override for Python projects (use python3 for cross-platform compatibility)
CDK_APP_OVERRIDE=""

if [ -f "requirements.txt" ]; then
    # Python CDK project. Install deps and FAIL LOUDLY if the install does not succeed.
    # Previously both attempts were silenced (2>/dev/null) under `set +e` and the exit code
    # was never checked, so a failed install still printed "OK" and the script proceeded to
    # `cdk deploy` -> `python3 app.py`, which then died with `ModuleNotFoundError: No module
    # named 'aws_cdk'`. That made a broken install look like a success on a clean runner.
    #
    # Try a normal install. If it fails, we do NOT silently force it through.
    #
    # A common failure is PEP 668 ("externally-managed-environment"): the OS marks the
    # system Python as owned by its package manager and pip refuses to install into it.
    # The old code auto-retried with --break-system-packages, which overrides that guard
    # and mutates the user's SYSTEM Python -- a surprising, out-of-scope, and on some
    # distros genuinely damaging side effect for a customer just trying the demo. The
    # safe default is to STOP and tell the user to use a virtual environment (which is
    # exactly what PEP 668 is steering them toward). The --break-system-packages bypass
    # remains available ONLY when the user explicitly opts in via the env var below --
    # intended for throwaway/ephemeral environments such as CI runners.
    if ! pip3 install -r requirements.txt -q; then
        if [ "${DEPLOY_CDK_ALLOW_BREAK_SYSTEM_PACKAGES:-}" = "1" ]; then
            echo -e "\033[0;33m      Normal pip install failed; DEPLOY_CDK_ALLOW_BREAK_SYSTEM_PACKAGES=1 set,\033[0m"
            echo -e "\033[0;33m      retrying with --break-system-packages (mutates the system Python)...\033[0m"
            if ! pip3 install -r requirements.txt -q --break-system-packages; then
                echo -e "\033[0;31m      ERROR: Failed to install Python CDK dependencies (requirements.txt)\033[0m"
                exit 1
            fi
        else
            echo -e "\033[0;31m      ERROR: Failed to install Python CDK dependencies (requirements.txt).\033[0m"
            echo -e "\033[0;33m      If this is an 'externally-managed-environment' (PEP 668) error, do NOT force it\033[0m"
            echo -e "\033[0;33m      into your system Python. Create and activate a virtual environment first:\033[0m"
            echo -e "\033[0;90m          python3 -m venv .venv && source .venv/bin/activate\033[0m"
            echo -e "\033[0;90m      then re-run this command. On a throwaway/ephemeral host (e.g. a CI runner)\033[0m"
            echo -e "\033[0;90m      where mutating the system Python is acceptable, you may instead re-run with:\033[0m"
            echo -e "\033[0;90m          DEPLOY_CDK_ALLOW_BREAK_SYSTEM_PACKAGES=1\033[0m"
            exit 1
        fi
    fi
    # Verify the CDK library is actually importable by the same interpreter that will synth
    # the app (cdk.json runs `python3 app.py`). A green pip does not guarantee this if pip and
    # python3 resolve to different environments, so check the real precondition, not a proxy.
    if ! python3 -c "import aws_cdk" 2>/dev/null; then
        echo -e "\033[0;31m      ERROR: 'aws_cdk' is not importable by python3 after installing requirements.txt.\033[0m"
        echo -e "\033[0;31m             pip3 and python3 may resolve to different environments.\033[0m"
        echo -e "\033[0;90m             pip3:    $(command -v pip3)\033[0m"
        echo -e "\033[0;90m             python3: $(command -v python3)\033[0m"
        exit 1
    fi
    # Override CDK app command to use python3 (some systems only have python3, not python)
    CDK_APP_OVERRIDE="--app 'python3 app.py'"
    echo -e "\033[0;32m      OK: Python CDK dependencies installed\033[0m"
elif [ -f "package.json" ]; then
    # TypeScript/JavaScript CDK project.
    # Install when node_modules is missing OR incomplete. A previous interrupted
    # install can leave a partial node_modules that lacks declared dependencies,
    # which then causes confusing CDK synth/compile errors. Verify completeness
    # with `npm ls` (non-zero exit => missing/unmet deps) instead of only checking
    # for the directory's existence.
    if [ ! -d "node_modules" ] || ! npm ls --prod --silent > /dev/null 2>&1; then
        if [ -f "package-lock.json" ]; then
            # Deterministic, clean install from the lockfile.
            npm ci
        else
            npm install
        fi
    fi
    echo -e "\033[0;32m      ✓ Node.js CDK dependencies installed\033[0m"
else
    echo -e "\033[0;33m      ⚠ No requirements.txt or package.json found\033[0m"
fi

# Bootstrap CDK (always run to ensure latest version)
if [ "$SKIP_BOOTSTRAP" = false ]; then
    echo ""
    echo -e "\033[0;33mEnsuring CDK bootstrap is up to date...\033[0m"
    set +e
    eval npx -y cdk bootstrap "aws://$ACCOUNT_ID/$CURRENT_REGION" --no-cli-pager $CDK_APP_OVERRIDE 2>&1
    bootstrap_exit=$?
    set -e
    if [ $bootstrap_exit -ne 0 ]; then
        echo -e "\033[0;31m      ERROR: CDK bootstrap failed\033[0m"
        exit 1
    fi
    echo -e "\033[0;32m      OK: CDK bootstrap is up to date\033[0m"
fi

# Deploy or destroy stack
if [ "$DESTROY_STACK" = true ]; then
    echo ""
    echo -e "\033[0;33mDestroying CDK stack...\033[0m"
    set +e
    if [ -z "$STACK_NAME" ]; then
        eval npx -y cdk destroy --force --no-cli-pager $CDK_APP_OVERRIDE 2>&1
    else
        eval npx -y cdk destroy "$STACK_NAME" --force --no-cli-pager $CDK_APP_OVERRIDE 2>&1
    fi
    cdk_exit=$?
    set -e
    if [ $cdk_exit -ne 0 ]; then
        echo -e "\033[0;31m      ERROR: CDK destroy failed\033[0m"
        exit 1
    fi
    echo -e "\033[0;32m      OK: Stack destroyed\033[0m"
else
    echo ""
    echo -e "\033[0;33mDeploying CDK stack...\033[0m"
    set +e
    if [ -z "$STACK_NAME" ]; then
        eval npx -y cdk deploy --require-approval never --no-cli-pager $CDK_APP_OVERRIDE 2>&1
    else
        eval npx -y cdk deploy "$STACK_NAME" --require-approval never --no-cli-pager $CDK_APP_OVERRIDE 2>&1
    fi
    cdk_exit=$?
    set -e
    if [ $cdk_exit -ne 0 ]; then
        echo -e "\033[0;31m      ERROR: CDK deployment failed\033[0m"
        exit 1
    fi
    echo -e "\033[0;32m      OK: Stack deployed successfully\033[0m"
fi

popd > /dev/null

# Export variables for use by calling script
export CDK_ACCOUNT_ID="$ACCOUNT_ID"
export CDK_REGION="$CURRENT_REGION"
