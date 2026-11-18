#!/bin/bash
# Aurora MySQL incident investigation with business context (AWS DevOps Agent): one-command deployment.
#
#   1. Agent Space (AWS DevOps Agent), in the Agent Space region
#   2. Database: VPC (no NAT), Aurora MySQL writer with its business tags, the connection load
#      function, the DatabaseConnections alarm and the alarm -> webhook trigger chain
#   3. Lab: engine + API + CloudFront site (HTTP Basic authentication)
#   4. Lab site upload
#   5. Skill aurora-incident-business-context, fetched from the Agent Tools repository (main) and registered
#      in the Agent Space for Chat and Incident RCA
#
# Region: AWS_REGION / AWS_DEFAULT_REGION / aws configure (this demo was built for us-east-1).
# Agent Space region: DEVOPS_AGENT_REGION when the deploy region cannot host one (defaults to the deploy region).
#
# Usage: ./deploy-all.sh [--project-name aurora-bizctx] [--lab-user presenter] [--lab-password <generated>]

set -e
cd "$(dirname "$0")"
STARTED_AT=$(date +%s)

PROJECT_NAME="aurora-bizctx"
LAB_USER="presenter"
LAB_PASSWORD=""
SKILL_NAME="aurora-incident-business-context"
# The skill is fetched from main of the Agent Tools repository, so every deploy gets its latest version.
SKILL_REF="main"
SKILL_AGENT_TYPES="CHAT,INCIDENT_RCA"

while [[ $# -gt 0 ]]; do
    case $1 in
        --project-name) PROJECT_NAME="$2"; shift 2 ;;
        --lab-user) LAB_USER="$2"; shift 2 ;;
        --lab-password) LAB_PASSWORD="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; echo "Usage: $0 [--project-name <name>] [--lab-user <user>] [--lab-password <password>]"; exit 1 ;;
    esac
done

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

echo ""
echo -e "${CYAN}========================================================================${NC}"
echo -e "${CYAN}  Aurora MySQL incident investigation with business context (AWS DevOps Agent)${NC}"
echo -e "${CYAN}========================================================================${NC}"
echo "  What this deploys:  an Agent Space, a VPC with no internet path, one Aurora MySQL"
echo "                      writer (db.t4g.medium) carrying business tags, a connection load"
echo "                      function, one alarm, and the Demo Lab (control room) behind CloudFront."
echo "  Time to deploy:     14 minutes measured with the Agent Space already deployed (Aurora stack 468 s);"
echo "                      a full fresh deploy was not timed."
echo -e "${YELLOW}  Running cost:       about \$2.35 per day while deployed (Aurora db.t4g.medium ~\$1.75,${NC}"
echo -e "${YELLOW}                      two interface endpoints ~\$0.48, CloudFront, Lambda, CloudWatch,${NC}"
echo -e "${YELLOW}                      Secrets Manager ~\$0.10) plus AWS DevOps Agent time at list price${NC}"
echo -e "${YELLOW}                      (about \$2.50-3 per investigation: 4.5-6 minutes observed; GDPR right after${NC}"
echo -e "${YELLOW}                      the storm can add a second agent run). Tear down with ./destroy-all.sh.${NC}"
echo ""

# -----------------------------------------------------------------------------
# Prerequisites (shared): tooling, credentials, region, DevOps Agent availability
# -----------------------------------------------------------------------------
source ../../shared/scripts/check-prerequisites.sh --required-service devops-agent --min-aws-cli-version 2.34.64 --require-cdk --min-node-version 20
REGION="$AWS_REGION"
AGENT_REGION="$DEVOPS_AGENT_REGION"
echo "  Deploy region: $REGION   Agent Space region: $AGENT_REGION   Account: $AWS_ACCOUNT_ID"

stack_output() {   # stack key [region]
    local value
    value=$(aws cloudformation describe-stacks --stack-name "$1" --region "${3:-$REGION}" \
        --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text --no-cli-pager)
    if [ -z "$value" ] || [ "$value" = "None" ]; then echo -e "${RED}Stack $1 has no output $2${NC}" >&2; exit 1; fi
    echo "$value"
}

# -----------------------------------------------------------------------------
# 1. Agent Space
# -----------------------------------------------------------------------------
echo ""
echo -e "${CYAN}[1/5] Agent Space in $AGENT_REGION...${NC}"
AGENT_SPACE_STACK="AuroraBizCtxAgentSpace-$AGENT_REGION"
AWS_REGION="$AGENT_REGION" ../../shared/scripts/deploy-cdk.sh --cdk-directory cdk --stack-name "$AGENT_SPACE_STACK" \
    --cdk-context "projectName=$PROJECT_NAME" --cdk-context "devOpsAgentRegion=$AGENT_REGION"
WEBHOOK_URL=$(stack_output "$AGENT_SPACE_STACK" WebhookUrl "$AGENT_REGION")
WEBHOOK_SECRET_ARN=$(stack_output "$AGENT_SPACE_STACK" WebhookSecretArn "$AGENT_REGION")
AGENT_SPACE_ID=$(stack_output "$AGENT_SPACE_STACK" AgentSpaceId "$AGENT_REGION")
echo -e "${GREEN}  Agent Space: $AGENT_SPACE_ID (webhook configured; its secret stays in Secrets Manager)${NC}"

# Context every remaining stack receives. The secret VALUE never enters this script.
CONTEXT=(--cdk-context "projectName=$PROJECT_NAME" --cdk-context "devOpsAgentRegion=$AGENT_REGION"
         --cdk-context "devOpsAgentSpaceId=$AGENT_SPACE_ID" --cdk-context "devOpsAgentWebhookUrl=$WEBHOOK_URL"
         --cdk-context "devOpsAgentWebhookSecretArn=$WEBHOOK_SECRET_ARN")

# -----------------------------------------------------------------------------
# 2. Database: VPC, Aurora MySQL, load function, alarm, trigger chain
# -----------------------------------------------------------------------------
echo ""
echo -e "${CYAN}[2/5] Aurora MySQL cluster in $REGION (the long step: 468 s measured, little output until done)...${NC}"
DATABASE_STACK="AuroraBizCtxDatabase-$REGION"
export AWS_REGION="$REGION"
../../shared/scripts/deploy-cdk.sh --cdk-directory cdk --stack-name "$DATABASE_STACK" "${CONTEXT[@]}"
CLUSTER_ID=$(stack_output "$DATABASE_STACK" ClusterIdentifier)
echo -e "${GREEN}  Cluster $CLUSTER_ID is available, business tags set.${NC}"

# -----------------------------------------------------------------------------
# 3. Lab: engine, API, site
# -----------------------------------------------------------------------------
echo ""
echo -e "${CYAN}[3/5] Demo Lab...${NC}"
if [ -z "$LAB_PASSWORD" ]; then
    LAB_PASSWORD=$(LC_ALL=C tr -dc 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789' < /dev/urandom | head -c 16)  # pragma: allowlist secret
fi
LAB_STACK="AuroraBizCtxLab-$REGION"
../../shared/scripts/deploy-cdk.sh --cdk-directory cdk --stack-name "$LAB_STACK" --skip-bootstrap "${CONTEXT[@]}" \
    --cdk-context "labUser=$LAB_USER" --cdk-context "labPassword=$LAB_PASSWORD"
LAB_URL=$(stack_output "$LAB_STACK" LabUrl)
SITE_BUCKET=$(stack_output "$LAB_STACK" SiteBucketName)
DISTRIBUTION_ID=$(stack_output "$LAB_STACK" DistributionId)
# Shown now, not only in the summary: if a later step fails, a re-run needs this password
# (pass it with --lab-password, or a new one is generated and the Lab stack is redeployed).
echo -e "${GREEN}  Lab: $LAB_URL   user $LAB_USER   password $LAB_PASSWORD${NC}"
echo -e "${YELLOW}  If a later step fails, re-run with --lab-password '$LAB_PASSWORD' to keep these credentials.${NC}"

# -----------------------------------------------------------------------------
# 4. Site upload
# -----------------------------------------------------------------------------
echo ""
echo -e "${CYAN}[4/5] Lab site...${NC}"
FRONTEND_LOG="$(pwd)/frontend-build.log"
if ! (
    cd frontend
    if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
    npm run build
) > "$FRONTEND_LOG" 2>&1; then
    echo -e "${RED}  ERROR: the Lab site build failed (npm install or npm run build). Last lines of $FRONTEND_LOG:${NC}"
    tail -n 25 "$FRONTEND_LOG"
    exit 1
fi
if ! aws s3 sync frontend/dist "s3://$SITE_BUCKET" --delete --region "$REGION" --no-cli-pager > /dev/null; then
    echo -e "${RED}  ERROR: uploading the Lab site to s3://$SITE_BUCKET failed (see the AWS CLI error).${NC}"
    exit 1
fi
if ! aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" --paths "/*" --no-cli-pager > /dev/null; then
    echo -e "${RED}  ERROR: the CloudFront invalidation on $DISTRIBUTION_ID failed (see the AWS CLI error).${NC}"
    exit 1
fi
echo -e "${GREEN}  Site published.${NC}"

# -----------------------------------------------------------------------------
# 5. Skill: stays in the Agent Tools repository; fetched from main, packaged and registered in
#    the Agent Space through the Asset API for the two motions it declares (Chat, Incident RCA).
# -----------------------------------------------------------------------------
echo ""
echo -e "${CYAN}[5/5] Skill $SKILL_NAME from $SKILL_REF...${NC}"
../../shared/devops-agent/agent-tools/deploy-skill.sh --skill "$SKILL_NAME" --ref "$SKILL_REF" \
    --agent-space-id "$AGENT_SPACE_ID" --agent-space-region "$AGENT_REGION" --agent-types "$SKILL_AGENT_TYPES"

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
ELAPSED=$(( ( $(date +%s) - STARTED_AT ) / 60 ))
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Deployment Complete! ($ELAPSED min)${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${CYAN}  Demo Lab:      $LAB_URL${NC}"
echo -e "${CYAN}  Sign in:       user $LAB_USER   password $LAB_PASSWORD${NC}"
echo -e "${CYAN}  Agent Space:   https://$AGENT_SPACE_ID.aidevops.global.app.aws/  (id $AGENT_SPACE_ID, $AGENT_REGION)${NC}"
echo -e "${CYAN}  Cluster:       $CLUSTER_ID ($REGION)${NC}"
echo -e "${CYAN}  Region:        $REGION${NC}"
echo ""
echo -e "${CYAN}  Skill:         $SKILL_NAME registered in the Agent Space (agent types $SKILL_AGENT_TYPES)${NC}"
echo -e "${CYAN}                 latest version, fetched from $SKILL_REF${NC}"
echo ""
echo -e "${YELLOW}  Next: open the Lab and inject Connection storm on a business-critical cluster;${NC}"
echo -e "${YELLOW}        the alarm starts an investigation within about 3 minutes.${NC}"
echo ""
echo -e "${YELLOW}  Running cost about \$2.35/day. Tear down: ./destroy-all.sh${NC}"
