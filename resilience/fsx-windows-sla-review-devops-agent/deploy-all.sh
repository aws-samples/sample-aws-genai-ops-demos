#!/bin/bash
# FSx for Windows SLA review with AWS DevOps Agent: one-command deployment.
#
#   1. Agent Space (AWS DevOps Agent), in the Agent Space region
#   2. Directory: VPC + self-managed Active Directory domain controller (EC2)
#      ... wait until the domain controller reports the forest and the service account ready
#   3. File system (FSx for Windows, Single-AZ, 32 GiB, 8 MB/s), alarms, lifecycle canary, trigger chain
#   4. Lab: engine + API + CloudFront site (HTTP Basic authentication)
#   5. Lab site upload, skill package for the console
#
# Region: AWS_REGION / AWS_DEFAULT_REGION / aws configure. Agent Space region: DEVOPS_AGENT_REGION
# when the deploy region cannot host one (defaults to the deploy region).
#
# Usage: ./deploy-all.sh [--project-name fsx-sla-review] [--lab-user presenter] [--lab-password <generated>]

set -e
cd "$(dirname "$0")"
STARTED_AT=$(date +%s)

PROJECT_NAME="fsx-sla-review"
LAB_USER="presenter"
LAB_PASSWORD=""
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
echo -e "${CYAN}  FSx for Windows SLA review with AWS DevOps Agent${NC}"
echo -e "${CYAN}========================================================================${NC}"
echo "  What this deploys:  an Agent Space, a VPC with one Active Directory domain"
echo "                      controller, one FSx for Windows file system with its alarms,"
echo "                      and the Demo Lab (control room) behind CloudFront."
echo "  Time to deploy:     about 1 hour. The domain controller promotes itself (~10 min),"
echo "                      then FSx joins the domain and builds the file system (25-35 min)."
echo -e "${YELLOW}  Running cost:       about \$5 per day while deployed (EC2 Windows t3.medium ~\$1.50,${NC}"
echo -e "${YELLOW}                      FSx 32 GiB / 8 MB/s ~\$0.75, NAT gateway ~\$1.10, CloudFront, Lambda,${NC}"
echo -e "${YELLOW}                      CloudWatch < \$0.50) plus AWS DevOps Agent time at list price${NC}"
echo -e "${YELLOW}                      (about \$0.50 per investigation). Tear down with ./destroy-all.sh.${NC}"
echo ""

# -----------------------------------------------------------------------------
# Prerequisites (shared): tooling, credentials, region, DevOps Agent availability
# -----------------------------------------------------------------------------
source ../../shared/scripts/check-prerequisites.sh --required-service devops-agent --min-aws-cli-version 2.34.20 --require-cdk --min-node-version 20
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
AGENT_SPACE_STACK="FsxSlaReviewAgentSpace-$AGENT_REGION"
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
# 2. Directory: VPC + domain controller, then wait for the domain
# -----------------------------------------------------------------------------
echo ""
echo -e "${CYAN}[2/5] VPC and Active Directory domain controller in $REGION...${NC}"
DIRECTORY_STACK="FsxSlaReviewDirectory-$REGION"
export AWS_REGION="$REGION"
../../shared/scripts/deploy-cdk.sh --cdk-directory cdk --stack-name "$DIRECTORY_STACK" "${CONTEXT[@]}"
READY_PARAMETER=$(stack_output "$DIRECTORY_STACK" ReadyParameterName)

echo "  Waiting for the domain controller to promote the forest and create the service account (~10 min)..."
DEADLINE=$(( $(date +%s) + 1800 ))
READY=false
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    VALUE=$(aws ssm get-parameter --name "$READY_PARAMETER" --region "$REGION" --query "Parameter.Value" --output text --no-cli-pager 2>/dev/null || echo "")
    if [ "$VALUE" = "ready" ]; then READY=true; break; fi
    echo "    $(date +%H:%M:%S)  not yet"
    sleep 30
done
if [ "$READY" != true ]; then
    echo -e "${RED}ERROR: the domain controller did not report ready within 30 minutes.${NC}"
    echo -e "${RED}       Open a Session Manager shell on it (output DomainControllerInstanceId of $DIRECTORY_STACK)${NC}"
    echo -e "${RED}       and read C:\\dc-setup.log, then re-run this script: it resumes from here.${NC}"
    exit 1
fi
echo -e "${GREEN}  Domain ready.${NC}"

# -----------------------------------------------------------------------------
# 3. File system, alarms, canary, trigger chain
# -----------------------------------------------------------------------------
echo ""
echo -e "${CYAN}[3/5] FSx for Windows file system (this is the long step: 25-35 minutes, no output until done)...${NC}"
FILE_SYSTEM_STACK="FsxSlaReviewFileSystem-$REGION"
if ! ../../shared/scripts/deploy-cdk.sh --cdk-directory cdk --stack-name "$FILE_SYSTEM_STACK" --skip-bootstrap "${CONTEXT[@]}"; then
    echo -e "${YELLOW}  If the file system failed to join the domain, run the AWSSupport-ValidateFSxWindowsADConfig${NC}"
    echo -e "${YELLOW}  Systems Manager automation with the ServiceAccountSecretArn output of $DIRECTORY_STACK.${NC}"
    exit 1
fi
FILE_SYSTEM_ID=$(stack_output "$FILE_SYSTEM_STACK" FileSystemId)
echo -e "${GREEN}  File system $FILE_SYSTEM_ID is AVAILABLE.${NC}"

# -----------------------------------------------------------------------------
# 4. Lab: engine, API, site
# -----------------------------------------------------------------------------
echo ""
echo -e "${CYAN}[4/5] Demo Lab...${NC}"
if [ -z "$LAB_PASSWORD" ]; then
    LAB_PASSWORD=$(LC_ALL=C tr -dc 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789' < /dev/urandom | head -c 16)
fi
LAB_STACK="FsxSlaReviewLab-$REGION"
../../shared/scripts/deploy-cdk.sh --cdk-directory cdk --stack-name "$LAB_STACK" --skip-bootstrap "${CONTEXT[@]}" \
    --cdk-context "labUser=$LAB_USER" --cdk-context "labPassword=$LAB_PASSWORD"
LAB_URL=$(stack_output "$LAB_STACK" LabUrl)
SITE_BUCKET=$(stack_output "$LAB_STACK" SiteBucketName)
DISTRIBUTION_ID=$(stack_output "$LAB_STACK" DistributionId)

# -----------------------------------------------------------------------------
# 5. Site upload and skill package
# -----------------------------------------------------------------------------
echo ""
echo -e "${CYAN}[5/5] Lab site and skill package...${NC}"
(
    cd frontend
    if [ -f package-lock.json ]; then npm ci --no-audit --no-fund > /dev/null 2>&1; else npm install --no-audit --no-fund > /dev/null 2>&1; fi
    npm run build > /dev/null 2>&1
)
aws s3 sync frontend/dist "s3://$SITE_BUCKET" --delete --region "$REGION" --no-cli-pager > /dev/null
aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" --paths "/*" --no-cli-pager > /dev/null
echo -e "${GREEN}  Site published.${NC}"

# The capability stays in the Agent Tools repository; this packages it for the console upload.
source ../../shared/devops-agent/agent-tools/deploy-skill.sh --skill storage-fsx-windows-sla-optimizer --ref main
SKILL_ZIP="$AGENT_TOOLS_SKILL_ZIP"

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
echo -e "${CYAN}  File system:   $FILE_SYSTEM_ID ($REGION)${NC}"
echo -e "${CYAN}  Region:        $REGION${NC}"
echo ""
echo -e "${YELLOW}  Next: upload the skill in the Agent Space (Skills, Add skill, Upload):${NC}"
echo -e "${YELLOW}        $SKILL_ZIP${NC}"
echo -e "${YELLOW}        pick agent types Chat tasks, Evaluation and Incident RCA (or Generic).${NC}"
echo -e "${YELLOW}  Then: open the Lab, inject a scenario, and ask in Chat:${NC}"
echo -e "${YELLOW}        Review all my FSx for Windows file systems in $REGION for SLA readiness.${NC}"
echo ""
echo -e "${YELLOW}  Running cost about \$5/day. Tear down: ./destroy-all.sh${NC}"
