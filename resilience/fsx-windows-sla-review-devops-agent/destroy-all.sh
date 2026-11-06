#!/bin/bash
# Removes everything deploy-all.sh created, Agent Space included (a demo that cannot be
# destroyed fails validation). Stacks go in dependency order: Lab, the file system (FSx takes
# about 10 minutes to delete, its backups with it), the directory, then the Agent Space in
# its own region. Each step goes through the shared deploy-cdk script.
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

source ../../shared/scripts/check-prerequisites.sh --required-service devops-agent --min-aws-cli-version 2.34.64 --require-cdk
REGION="$AWS_REGION"
AGENT_REGION="$DEVOPS_AGENT_REGION"
CONTEXT=(--cdk-context "projectName=$PROJECT_NAME" --cdk-context "devOpsAgentRegion=$AGENT_REGION")

echo ""
echo -e "${YELLOW}Destroying the FSx for Windows SLA review demo in $REGION (Agent Space in $AGENT_REGION)...${NC}"
echo "  The file system and its automatic backups are deleted; this takes about 15 minutes."

export AWS_REGION="$REGION"
for STACK in "FsxSlaReviewLab-$REGION" "FsxSlaReviewFileSystem-$REGION" "FsxSlaReviewDirectory-$REGION"; do
    echo ""
    echo -e "${CYAN}Removing $STACK...${NC}"
    ../../shared/scripts/deploy-cdk.sh --cdk-directory cdk --stack-name "$STACK" --destroy --skip-bootstrap "${CONTEXT[@]}"
done

echo ""
echo -e "${CYAN}Removing FsxSlaReviewAgentSpace-$AGENT_REGION...${NC}"
AWS_REGION="$AGENT_REGION" ../../shared/scripts/deploy-cdk.sh --cdk-directory cdk --stack-name "FsxSlaReviewAgentSpace-$AGENT_REGION" --destroy --skip-bootstrap "${CONTEXT[@]}"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Teardown Complete${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${CYAN}  Not removed: the CDK bootstrap stack. The Agent Space is gone, and the skill registered in it with it.${NC}"
