#!/bin/bash
# =============================================================================
# G.O.A.T. - Full uninstall + redeploy NETWORK-MCP mode only
#
# Destroys every GOAT stack (both CDK apps) in dependency order, then
# redeploys ONLY the Network Agent + DevOps Agent MCP integration (no Auth,
# no Frontend, no Orchestrator) plus the network troubleshooting demo
# scenarios (TLS fragmentation + Scenarios G-L).
#
# Run from this directory:
#   ./redeploy-network-mcp.sh
#
# Common overrides:
#   ./redeploy-network-mcp.sh --region eu-west-1
#   ./redeploy-network-mcp.sh --skip-confirm   (no interactive prompt)
#   ./redeploy-network-mcp.sh --profile MyProfile
# =============================================================================
set -e

# Defaults
PROFILE="AdministratorAccess-157643525386"
REGION="us-east-1"
SKIP_CONFIRM=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --profile) PROFILE="$2"; shift 2 ;;
        --region) REGION="$2"; shift 2 ;;
        --skip-confirm) SKIP_CONFIRM=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

ROOT="$(cd "$(dirname "$0")" && pwd)"
CDK_DIR="$ROOT/infrastructure/cdk"
DEMO_DIR="$ROOT/demo-scenarios"
DEVOPS_DIR="$ROOT/devops-integration"
DEMO_APP="npx ts-node --prefer-ts-exts bin/demo-scenarios-app.ts"

export AWS_PROFILE="$PROFILE"
export AWS_REGION="$REGION"
export AWS_DEFAULT_REGION="$REGION"

write_step() { echo -e "\n\033[36m=== $1 ===\033[0m"; }

# -----------------------------------------------------------------------------
# 0. Safety: confirm we are pointed at the expected account
# -----------------------------------------------------------------------------
write_step "Verifying AWS identity"
EXPECTED_ACCOUNT=$(echo "$PROFILE" | grep -oE '[0-9]{12}')
ACTUAL_ACCOUNT=$(aws sts get-caller-identity --query "Account" --output text --no-cli-pager 2>&1)
if [ $? -ne 0 ]; then
    echo -e "\033[31mCould not call sts get-caller-identity. Is the profile '$PROFILE' valid / logged in?\033[0m"
    echo "$ACTUAL_ACCOUNT"
    exit 1
fi
echo "Profile : $PROFILE"
echo "Account : $ACTUAL_ACCOUNT"
echo "Region  : $REGION"
echo -e "\033[35mMode    : network-mcp (Network Agent + MCP only, no Auth/Frontend/Orchestrator)\033[0m"

if [ -n "$EXPECTED_ACCOUNT" ] && [ "$(echo "$ACTUAL_ACCOUNT" | tr -d '[:space:]')" != "$EXPECTED_ACCOUNT" ]; then
    echo -e "\033[31mABORT: resolved account ($ACTUAL_ACCOUNT) does not match the account in the profile name ($EXPECTED_ACCOUNT).\033[0m"
    echo -e "\033[31mPass the correct --profile, or fix your SSO login.\033[0m"
    exit 1
fi

if [ "$SKIP_CONFIRM" = false ]; then
    echo -e "\n\033[33mThis will DESTROY all GOAT stacks in account $ACTUAL_ACCOUNT ($REGION) and redeploy in network-mcp mode.\033[0m"
    echo -e "\033[33mStacks deployed: NetworkData, NetworkInfra, NetworkRuntime, DevOpsIntegration + Demo Scenarios (C + G-L)\033[0m"
    read -p "Type 'destroy' to continue: " answer
    if [ "$answer" != "destroy" ]; then echo "Cancelled."; exit 0; fi
fi

# -----------------------------------------------------------------------------
# 1. DESTROY - DevOps Agent integration (depends on NetworkAgent exports)
# -----------------------------------------------------------------------------
write_step "Destroying DevOps Agent integration stack (GOATDevOpsIntegration*)"

# Deregister MCP server
SERVICE_LIST=$(aws devops-agent list-services --output json --no-cli-pager 2>/dev/null || true)
if [ -n "$SERVICE_LIST" ]; then
    SERVICE_ID=$(echo "$SERVICE_LIST" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for svc in data.get('services', []):
        if svc.get('serviceType') == 'mcpserversigv4':
            print(svc['serviceId'])
            break
except: pass
" 2>/dev/null)

    if [ -n "$SERVICE_ID" ]; then
        echo -e "  \033[33mFound MCP service: $SERVICE_ID\033[0m"

        # Disassociate from all AgentSpaces
        AGENT_SPACES=$(aws devops-agent list-agent-spaces --output json --no-cli-pager 2>/dev/null || true)
        if [ -n "$AGENT_SPACES" ]; then
            echo "$AGENT_SPACES" | python3 -c "
import sys, json, subprocess
try:
    data = json.load(sys.stdin)
    for space in data.get('agentSpaces', []):
        space_id = space['agentSpaceId']
        assoc_json = subprocess.run(
            ['aws', 'devops-agent', 'list-associations', '--agent-space-id', space_id,
             '--filter-service-types', 'mcpserversigv4', '--output', 'json', '--no-cli-pager'],
            capture_output=True, text=True
        ).stdout
        if assoc_json:
            assocs = json.loads(assoc_json).get('associations', [])
            for a in assocs:
                if a.get('serviceId') == '$SERVICE_ID':
                    print(f'  Disassociating from AgentSpace {space_id}...')
                    subprocess.run(
                        ['aws', 'devops-agent', 'disassociate-service', '--agent-space-id', space_id,
                         '--association-id', a['associationId'], '--no-cli-pager'],
                        capture_output=True
                    )
except: pass
" 2>/dev/null || true
        fi

        echo -e "  \033[33mDeregistering MCP service: $SERVICE_ID...\033[0m"
        aws devops-agent deregister-service --service-id "$SERVICE_ID" --no-cli-pager 2>/dev/null || true
        echo -e "  \033[32mDeregistered.\033[0m"
    else
        echo -e "  \033[90mNo mcpserversigv4 service found (already deregistered).\033[0m"
    fi
fi

DEVOPS_STACK="GOATDevOpsIntegration-$REGION"
DEVOPS_STATUS=$(aws cloudformation describe-stacks --stack-name "$DEVOPS_STACK" \
    --query "Stacks[0].StackStatus" --output text --no-cli-pager 2>/dev/null || echo "NOT_FOUND")

if [ "$DEVOPS_STATUS" != "NOT_FOUND" ] && [ "$DEVOPS_STATUS" != "DELETE_COMPLETE" ]; then
    echo -e "  \033[33mDeleting $DEVOPS_STACK (status: $DEVOPS_STATUS)...\033[0m"
    aws cloudformation delete-stack --stack-name "$DEVOPS_STACK" --no-cli-pager 2>/dev/null || true
    echo -e "  \033[90mWaiting for stack deletion...\033[0m"
    aws cloudformation wait stack-delete-complete --stack-name "$DEVOPS_STACK" --no-cli-pager 2>/dev/null || {
        echo -e "  \033[33mDelete failed - retrying with --retain-resources...\033[0m"
        FAILED_RES=$(aws cloudformation describe-stack-resources --stack-name "$DEVOPS_STACK" \
            --query "StackResources[?ResourceStatus=='DELETE_FAILED'].LogicalResourceId" \
            --output text --no-cli-pager 2>/dev/null || true)
        if [ -n "$FAILED_RES" ] && [ "$FAILED_RES" != "None" ]; then
            aws cloudformation delete-stack --stack-name "$DEVOPS_STACK" --retain-resources $FAILED_RES --no-cli-pager 2>/dev/null || true
        else
            aws cloudformation delete-stack --stack-name "$DEVOPS_STACK" --no-cli-pager 2>/dev/null || true
        fi
        aws cloudformation wait stack-delete-complete --stack-name "$DEVOPS_STACK" --no-cli-pager 2>/dev/null || true
    }
    echo -e "  \033[32m$DEVOPS_STACK deleted.\033[0m"
else
    echo -e "  \033[32m$DEVOPS_STACK not found (already deleted or never deployed).\033[0m"
fi

# -----------------------------------------------------------------------------
# 1b. DESTROY - demo scenarios app
# -----------------------------------------------------------------------------
write_step "Destroying demo scenario stacks (GOATDemoScenario*)"
cd "$CDK_DIR"
npx cdk destroy --all --app "$DEMO_APP" --force --no-cli-pager 2>/dev/null || \
    echo -e "  \033[33mDemo scenario destroy reported an issue (continuing).\033[0m"

# -----------------------------------------------------------------------------
# 2. DESTROY - main app (all GOAT stacks)
# -----------------------------------------------------------------------------
write_step "Destroying core GOAT stacks (--all)"
cd "$CDK_DIR"
npx cdk destroy --all --force --no-cli-pager 2>/dev/null || \
    echo -e "  \033[33mCore destroy reported an issue (continuing).\033[0m"

# -----------------------------------------------------------------------------
# 3. Verify nothing is left + force-delete DELETE_FAILED stacks
# -----------------------------------------------------------------------------
write_step "Checking for leftover GOAT stacks"
LEFTOVER=$(aws cloudformation list-stacks \
    --query "StackSummaries[?contains(StackName,'GOAT') && StackStatus!='DELETE_COMPLETE'].[StackName,StackStatus]" \
    --output text --no-cli-pager 2>/dev/null)

if [ -z "$LEFTOVER" ]; then
    echo -e "\033[32mClean - no remaining GOAT stacks.\033[0m"
else
    echo -e "\033[33mStacks still present:\033[0m"
    echo "$LEFTOVER"

    # Force-delete DELETE_FAILED stacks
    FAILED_STACKS=$(aws cloudformation list-stacks \
        --query "StackSummaries[?contains(StackName,'GOAT') && StackStatus=='DELETE_FAILED'].StackName" \
        --output text --no-cli-pager 2>/dev/null)

    for stack in $FAILED_STACKS; do
        [ "$stack" = "None" ] && continue
        echo -e "  \033[33mForce-deleting $stack...\033[0m"
        FAILED_RES=$(aws cloudformation describe-stack-resources --stack-name "$stack" \
            --query "StackResources[?ResourceStatus=='DELETE_FAILED'].LogicalResourceId" \
            --output text --no-cli-pager 2>/dev/null || true)
        if [ -n "$FAILED_RES" ] && [ "$FAILED_RES" != "None" ]; then
            aws cloudformation delete-stack --stack-name "$stack" --retain-resources $FAILED_RES --no-cli-pager 2>/dev/null || true
        else
            aws cloudformation delete-stack --stack-name "$stack" --no-cli-pager 2>/dev/null || true
        fi
        aws cloudformation wait stack-delete-complete --stack-name "$stack" --no-cli-pager 2>/dev/null || true
    done

    # Delete remaining active stacks
    ACTIVE_STACKS=$(aws cloudformation list-stacks \
        --query "StackSummaries[?contains(StackName,'GOAT') && (StackStatus=='CREATE_COMPLETE' || StackStatus=='UPDATE_COMPLETE' || StackStatus=='UPDATE_ROLLBACK_COMPLETE')].StackName" \
        --output text --no-cli-pager 2>/dev/null)

    for stack in $ACTIVE_STACKS; do
        [ "$stack" = "None" ] && continue
        echo -e "  \033[33mDeleting $stack...\033[0m"
        aws cloudformation delete-stack --stack-name "$stack" --no-cli-pager 2>/dev/null || true
        aws cloudformation wait stack-delete-complete --stack-name "$stack" --no-cli-pager 2>/dev/null || {
            FAILED_RES=$(aws cloudformation describe-stack-resources --stack-name "$stack" \
                --query "StackResources[?ResourceStatus=='DELETE_FAILED'].LogicalResourceId" \
                --output text --no-cli-pager 2>/dev/null || true)
            if [ -n "$FAILED_RES" ] && [ "$FAILED_RES" != "None" ]; then
                aws cloudformation delete-stack --stack-name "$stack" --retain-resources $FAILED_RES --no-cli-pager 2>/dev/null || true
            fi
            aws cloudformation wait stack-delete-complete --stack-name "$stack" --no-cli-pager 2>/dev/null || true
        }
    done
fi
rm -rf "$CDK_DIR/cdk.out" 2>/dev/null || true

# -----------------------------------------------------------------------------
# 3b. Clean up orphaned Traffic Mirror resources
# -----------------------------------------------------------------------------
write_step "Cleaning up orphaned Traffic Mirror resources"

SESSIONS=$(aws ec2 describe-traffic-mirror-sessions \
    --query "TrafficMirrorSessions[].TrafficMirrorSessionId" --output text --no-cli-pager 2>/dev/null)
SESS_COUNT=0
for sid in $SESSIONS; do
    [ "$sid" = "None" ] && continue
    echo -e "  \033[33mDeleting mirror session $sid\033[0m"
    aws ec2 delete-traffic-mirror-session --traffic-mirror-session-id "$sid" --no-cli-pager 2>/dev/null || true
    SESS_COUNT=$((SESS_COUNT + 1))
done

TARGETS=$(aws ec2 describe-traffic-mirror-targets \
    --query "TrafficMirrorTargets[?contains(Description,'G.O.A.T.') || contains(Description,'goat')].TrafficMirrorTargetId" \
    --output text --no-cli-pager 2>/dev/null)
TARG_COUNT=0
for tid in $TARGETS; do
    [ "$tid" = "None" ] && continue
    echo -e "  \033[33mDeleting mirror target $tid\033[0m"
    aws ec2 delete-traffic-mirror-target --traffic-mirror-target-id "$tid" --no-cli-pager 2>/dev/null || true
    TARG_COUNT=$((TARG_COUNT + 1))
done

FILTERS=$(aws ec2 describe-traffic-mirror-filters \
    --query "TrafficMirrorFilters[?contains(Description,'G.O.A.T.') || contains(Description,'goat')].TrafficMirrorFilterId" \
    --output text --no-cli-pager 2>/dev/null)
FILT_COUNT=0
for fid in $FILTERS; do
    [ "$fid" = "None" ] && continue
    echo -e "  \033[33mDeleting mirror filter $fid\033[0m"
    aws ec2 delete-traffic-mirror-filter --traffic-mirror-filter-id "$fid" --no-cli-pager 2>/dev/null || true
    FILT_COUNT=$((FILT_COUNT + 1))
done

TOTAL=$((SESS_COUNT + TARG_COUNT + FILT_COUNT))
if [ $TOTAL -eq 0 ]; then
    echo -e "\033[32mClean - no orphaned Traffic Mirror resources.\033[0m"
else
    echo -e "\033[32mRemoved: $SESS_COUNT sessions, $TARG_COUNT targets, $FILT_COUNT filters.\033[0m"
fi

# -----------------------------------------------------------------------------
# 3c. Verify leftover GOAT EC2 instances and ENIs
# -----------------------------------------------------------------------------
write_step "Verifying GOAT EC2 instances and ENIs are gone"

GOAT_INSTANCES=$(aws ec2 describe-instances \
    --filters "Name=tag:Name,Values=goat-*" "Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down" \
    --query "Reservations[].Instances[].InstanceId" --output text --no-cli-pager 2>/dev/null)
GOAT_ENIS=$(aws ec2 describe-network-interfaces \
    --filters "Name=tag:Name,Values=goat-*" \
    --query "NetworkInterfaces[].NetworkInterfaceId" --output text --no-cli-pager 2>/dev/null)

if [ -z "$GOAT_INSTANCES" ] && [ -z "$GOAT_ENIS" ]; then
    echo -e "\033[32mClean - no GOAT EC2 instances or ENIs remain.\033[0m"
else
    echo -e "\033[33mLeftover resources detected:\033[0m"
    [ -n "$GOAT_INSTANCES" ] && echo "  Instances: $GOAT_INSTANCES"
    [ -n "$GOAT_ENIS" ] && echo "  ENIs     : $GOAT_ENIS"

    if [ "$SKIP_CONFIRM" = true ]; then
        DO_CLEANUP=true
    else
        read -p "Attempt to terminate/delete these leftovers? (yes/no): " ans
        DO_CLEANUP=$([ "$ans" = "yes" ] && echo true || echo false)
    fi

    if [ "$DO_CLEANUP" = "true" ]; then
        if [ -n "$GOAT_INSTANCES" ]; then
            echo -e "\033[33mTerminating leftover instances...\033[0m"
            aws ec2 terminate-instances --instance-ids $GOAT_INSTANCES --no-cli-pager 2>/dev/null || true
            aws ec2 wait instance-terminated --instance-ids $GOAT_INSTANCES --no-cli-pager 2>/dev/null || true
        fi
        sleep 5
        REMAINING_ENIS=$(aws ec2 describe-network-interfaces \
            --filters "Name=tag:Name,Values=goat-*" \
            --query "NetworkInterfaces[?Status=='available'].NetworkInterfaceId" --output text --no-cli-pager 2>/dev/null)
        for eni in $REMAINING_ENIS; do
            [ "$eni" = "None" ] && continue
            aws ec2 delete-network-interface --network-interface-id "$eni" --no-cli-pager 2>/dev/null || true
        done
    else
        echo -e "\033[33mAborting before redeploy.\033[0m"
        exit 1
    fi
fi

# -----------------------------------------------------------------------------
# 4. Bootstrap CDK (idempotent)
# -----------------------------------------------------------------------------
write_step "Bootstrapping CDK environment"
cd "$CDK_DIR"
npx cdk bootstrap "aws://$ACTUAL_ACCOUNT/$REGION" --no-cli-pager
if [ $? -ne 0 ]; then echo -e "\033[31mcdk bootstrap failed.\033[0m"; exit 1; fi

# -----------------------------------------------------------------------------
# 4b. Clear stale CDK context cache
# -----------------------------------------------------------------------------
write_step "Clearing stale CDK context caches"
rm -rf "$CDK_DIR/cdk.context.json" "$CDK_DIR/cdk.out" 2>/dev/null || true
echo -e "  \033[32mContext caches cleared — CDK will re-resolve VPC lookups from live exports.\033[0m"

# -----------------------------------------------------------------------------
# 5. REDEPLOY - Network Agent + MCP integration (network-mcp mode)
# -----------------------------------------------------------------------------
write_step "Building DevOps Agent MCP handler (esbuild)"
cd "$DEVOPS_DIR"
if [ -d "node_modules" ]; then
    if ! ls node_modules/@esbuild/*linux* 1>/dev/null 2>&1 && ls node_modules/@esbuild/ 1>/dev/null 2>&1; then
        echo -e "  \033[33mDetected cross-platform node_modules - reinstalling...\033[0m"
        rm -rf node_modules
        npm ci --silent
    fi
fi
if [ ! -d "node_modules" ]; then
    echo -e "  \033[90mInstalling devops-integration dependencies...\033[0m"
    npm ci --silent
fi
npx esbuild src/lambda/mcp-handler.ts --bundle --platform=node --target=node20 \
    --outfile=dist/mcp-handler.js "--external:@aws-sdk/client-bedrock-agent-runtime"
if [ $? -ne 0 ]; then echo -e "\033[31mesbuild failed.\033[0m"; exit 1; fi

write_step "Deploying in network-mcp mode (Network Agent + DevOps MCP integration only)"
cd "$ROOT"
./deploy-all.sh --deployment-mode network-mcp
if [ $? -ne 0 ]; then echo -e "\033[31mdeploy-all.sh --deployment-mode network-mcp failed.\033[0m"; exit 1; fi

# -----------------------------------------------------------------------------
# 6. DEPLOY - Demo Scenarios: TLS Fragmentation (Scenario C) + Network Troubleshooting (G-L)
# -----------------------------------------------------------------------------
write_step "Deploying demo scenarios: connectivity (TLS fragmentation) + network-troubleshooting (G-L)"
cd "$DEMO_DIR"

echo -e "  \033[36mDeploying Scenario C (TLS fragmentation)...\033[0m"
./deploy-demo-scenarios.sh connectivity || \
    echo -e "  \033[33mWarning: Scenario C deployment had issues (continuing to G-L).\033[0m"

echo -e "  \033[36mDeploying Scenarios G-L (network troubleshooting)...\033[0m"
./deploy-demo-scenarios.sh network-troubleshooting || \
    echo -e "  \033[33mWarning: Scenario G-L deployment had issues.\033[0m"

# -----------------------------------------------------------------------------
# 7. Summary
# -----------------------------------------------------------------------------
write_step "Redeploy complete (network-mcp mode)"
echo ""
echo -e "\033[32m========================================\033[0m"
echo -e "\033[32m  Network-MCP Deployment Complete!\033[0m"
echo -e "\033[32m========================================\033[0m"
echo ""
echo -e "  \033[36mAccount       : $ACTUAL_ACCOUNT\033[0m"
echo -e "  \033[36mRegion        : $REGION\033[0m"
echo -e "  \033[36mMode          : network-mcp (no Auth, no Frontend, no Orchestrator)\033[0m"
echo ""

# Network Agent ARN
NETWORK_ARN=$(aws cloudformation describe-stacks --stack-name "GOATNetworkRuntime-$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text --no-cli-pager 2>/dev/null)
if [ -n "$NETWORK_ARN" ] && [ "$NETWORK_ARN" != "None" ]; then
    echo -e "  \033[36mNetwork Agent : $NETWORK_ARN\033[0m"
fi

# MCP endpoint
MCP_ENDPOINT=$(aws cloudformation describe-stacks --stack-name "GOATDevOpsIntegration-$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='McpEndpointUrl'].OutputValue" --output text --no-cli-pager 2>/dev/null)
if [ -n "$MCP_ENDPOINT" ] && [ "$MCP_ENDPOINT" != "None" ]; then
    echo -e "  \033[36mMCP Endpoint  : $MCP_ENDPOINT\033[0m"
    echo -e "  \033[36mHealth Check  : ${MCP_ENDPOINT}health\033[0m"
fi

echo ""
echo -e "  \033[37mDemo Scenarios Deployed:\033[0m"
echo "    - Scenario C  : TLS Fragmentation (GOATDemoScenarioC-$REGION)"
echo "    - Scenario G  : Connectivity Troubleshooting (agentic_reachability_analyze)"
echo "    - Scenario H  : Routing Troubleshooting (tcp_traceroute)"
echo "    - Scenario I  : TLS Troubleshooting (tls_traceroute)"
echo "    - Scenario J  : DNS Troubleshooting (dns_resolve)"
echo "    - Scenario K  : Database Troubleshooting (db_connectivity_probe)"
echo "    - Scenario L  : SSM Troubleshooting (ssm_health_check)"
echo ""
echo -e "  \033[90mNo Cognito sign-in required - use via AWS DevOps Agent MCP integration.\033[0m"
echo -e "  \033[90mTo add the full chat UI later: ./deploy-all.sh --deployment-mode full\033[0m"
echo ""
