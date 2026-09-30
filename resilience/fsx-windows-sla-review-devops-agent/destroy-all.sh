#!/bin/bash
# Removes everything deploy-all.sh created, Agent Space included (a demo that cannot be
# destroyed fails validation). CDK orders the stacks: Lab, then the file system (FSx takes
# about 10 minutes to delete), then the directory, and the Agent Space in its own region.
#
# Usage: ./destroy-all.sh [--project-name fsx-sla-review]

set -e
cd "$(dirname "$0")"

PROJECT_NAME="fsx-sla-review"
while [[ $# -gt 0 ]]; do
    case $1 in
        --project-name) PROJECT_NAME="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

source ../../shared/scripts/check-prerequisites.sh --required-service devops-agent --min-aws-cli-version 2.34.20 --require-cdk
REGION="$AWS_REGION"
AGENT_REGION="$DEVOPS_AGENT_REGION"

echo ""
echo -e "${YELLOW}Destroying the FSx for Windows SLA review demo in $REGION (Agent Space in $AGENT_REGION)...${NC}"
echo "  The file system and its automatic backups are deleted; this takes about 15 minutes."

(cd cdk && npx -y cdk destroy --all --force -c "projectName=$PROJECT_NAME" -c "devOpsAgentRegion=$AGENT_REGION")

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Teardown Complete${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${CYAN}  Not removed: the CDK bootstrap stack, and any skill you uploaded in the Agent Space console${NC}"
echo -e "${CYAN}  (the Agent Space itself is gone, so the skill is too).${NC}"
