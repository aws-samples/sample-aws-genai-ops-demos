#!/bin/bash
# Removes everything deploy-all.sh created, Agent Space included (a demo that cannot be
# destroyed fails validation). Stacks go in dependency order: Lab, the database (Aurora takes
# about 10 minutes to delete; no final snapshot is kept), then the Agent Space in its own
# region. Each step goes through the shared deploy-cdk script.
#
# Usage: ./destroy-all.sh [--project-name aurora-bizctx]

set -e
cd "$(dirname "$0")"

PROJECT_NAME="aurora-bizctx"
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
echo -e "${YELLOW}Destroying the Aurora MySQL business-context demo in $REGION (Agent Space in $AGENT_REGION)...${NC}"
echo "  The cluster is deleted without a final snapshot; this takes about 15 minutes."

export AWS_REGION="$REGION"
# A storm left running would hold connections while the cluster deletes; switch the load off first.
aws lambda put-function-concurrency --function-name "$PROJECT_NAME-connection-load" --reserved-concurrent-executions 0 \
    --region "$REGION" --no-cli-pager > /dev/null 2>&1 || true

# Log groups Lambda creates at run time (not stack resources): note the trigger function's
# generated name before its stack goes, delete them all once the stacks are gone.
TRIGGER_FUNCTION=$(aws cloudformation describe-stacks --stack-name "AuroraBizCtxDatabase-$REGION" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='TriggerFunctionName'].OutputValue" --output text --no-cli-pager 2>/dev/null || echo "")

for STACK in "AuroraBizCtxLab-$REGION" "AuroraBizCtxDatabase-$REGION"; do
    echo ""
    echo -e "${CYAN}Removing $STACK...${NC}"
    ../../shared/scripts/deploy-cdk.sh --cdk-directory cdk --stack-name "$STACK" --destroy --skip-bootstrap "${CONTEXT[@]}"
done

echo ""
echo -e "${CYAN}Removing run-time log groups...${NC}"
LOG_GROUPS=("/aws/lambda/$PROJECT_NAME-lab-engine" "/aws/lambda/$PROJECT_NAME-lab-api")
if [ -n "$TRIGGER_FUNCTION" ] && [ "$TRIGGER_FUNCTION" != "None" ]; then LOG_GROUPS+=("/aws/lambda/$TRIGGER_FUNCTION"); fi
for PREFIX in "/aws/lambda/AuroraBizCtxLab-$REGION-" "/aws/lambda/AuroraBizCtxDatabase-$REGION-"; do
    while read -r NAME; do
        if [ -n "$NAME" ] && [ "$NAME" != "None" ]; then LOG_GROUPS+=("$NAME"); fi
    done < <(aws logs describe-log-groups --log-group-name-prefix "$PREFIX" --region "$REGION" \
        --query "logGroups[].logGroupName" --output text --no-cli-pager 2>/dev/null | tr '\t' '\n')
done
for NAME in "${LOG_GROUPS[@]}"; do
    if aws logs delete-log-group --log-group-name "$NAME" --region "$REGION" --no-cli-pager > /dev/null 2>&1; then
        echo "  deleted $NAME"
    fi
done

echo ""
echo -e "${CYAN}Removing AuroraBizCtxAgentSpace-$AGENT_REGION...${NC}"
AWS_REGION="$AGENT_REGION" ../../shared/scripts/deploy-cdk.sh --cdk-directory cdk --stack-name "AuroraBizCtxAgentSpace-$AGENT_REGION" --destroy --skip-bootstrap "${CONTEXT[@]}"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Teardown Complete${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${CYAN}  Not removed: the CDK bootstrap stack. The Agent Space is gone, and the skill registered in it with it.${NC}"
