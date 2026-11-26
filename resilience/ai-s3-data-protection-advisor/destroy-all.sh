#!/bin/bash
# S3 data protection advisor - complete teardown script
# macOS/Linux version - mirrors destroy-all.ps1
#   --mock-data   delete the mock estate first (tagged buckets and role only)

set -e

MOCK_DATA=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --mock-data) MOCK_DATA=true; shift ;;
        *) echo "Unknown option: $1"; echo "Usage: $0 [--mock-data]"; exit 1 ;;
    esac
done

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "\033[0;36m=== S3 data protection advisor teardown ===\033[0m"

source ../../shared/scripts/check-prerequisites.sh --skip-service-check --min-python-version "3.12" --require-cdk --min-cdk-version "2.1030.0"

if [ "$MOCK_DATA" = true ]; then
    echo -e "\n\033[0;33mDeleting the mock estate...\033[0m"
    if [ ! -d backend/.venv ]; then
        python3 -m venv backend/.venv
    fi
    backend/.venv/bin/python -m pip install --quiet -r backend/requirements.txt
    (cd backend && ../backend/.venv/bin/python -m s3dp.cli seed-cleanup --yes)
fi

echo -e "\n\033[0;33mDestroying the stacks in $AWS_REGION...\033[0m"
(cd cdk && npm install && npx cdk destroy --all --force --no-cli-pager)

echo -e "\n\033[0;32mTeardown complete\033[0m"
