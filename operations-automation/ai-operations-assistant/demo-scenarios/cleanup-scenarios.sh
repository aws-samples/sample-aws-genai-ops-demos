#!/bin/bash
# G.O.A.T. Demo Cleanup - Remove All Demo Resources
#
# NOTE: The TLS Fragmentation teardown in this Bash script is OUT OF DATE.
# The PowerShell version (cleanup-scenarios.ps1) was updated to also remove the
# Transit Gateway (+ attachments + route table), the separate inspection VPC,
# the firewall logging configuration (which must be cleared before the firewall
# can be deleted), the firewall CloudWatch log groups, and the scenario IAM
# roles. This .sh has NOT yet been ported, so prefer cleanup-scenarios.ps1 for
# the TLS scenario until this is updated.
#
# Finds and removes all AWS resources tagged with goat-demo=true across
# Scenario A, Scenario B, and the TLS Fragmentation Scenario. Resources
# are deleted in dependency order to avoid conflicts.
#
# Deletion order (Scenarios A & B):
#   1. EC2 instances (may use VPC subnets, may have attached volumes)
#   2. RDS instances (skip final snapshot; depends on DB subnet group)
#   3. DB subnet groups (depends on RDS being gone)
#   4. EBS volumes (may need EC2 termination to complete)
#   5. Elastic IPs (no dependencies)
#   6. DynamoDB tables (no dependencies)
#   7. Subnets (depends on EC2/RDS being gone)
#   8. VPCs (depends on subnets being gone)
#
# Deletion order (TLS Fragmentation):
#   1. Kubernetes test pod
#   2. EKS managed node group
#   3. EKS cluster
#   4. Network Firewall (+ policy + rule group)
#   5. NAT Gateway
#   6. Internet Gateway
#   7. Subnets
#   8. Route tables
#   9. Security groups
#  10. VPC
#
# Support cases are NOT cleaned up — they are already resolved and
# cannot be deleted via API.
#
# Usage: ./cleanup-scenarios.sh

set -o pipefail

# ---------------------------------------------------------------------------
# Color helpers (matching deploy-all.sh / setup-scenario-account-health.sh patterns)
# ---------------------------------------------------------------------------
print_cyan()    { echo -e "\033[0;36m$1\033[0m"; }
print_green()   { echo -e "\033[0;32m$1\033[0m"; }
print_yellow()  { echo -e "\033[0;33m$1\033[0m"; }
print_red()     { echo -e "\033[0;31m$1\033[0m"; }
print_gray()    { echo -e "\033[0;90m$1\033[0m"; }
print_magenta() { echo -e "\033[0;35m$1\033[0m"; }

# ---------------------------------------------------------------------------
# Track removed resources for summary
# ---------------------------------------------------------------------------
TERMINATED_EC2=()
DELETED_RDS=()
DELETED_DB_SUBNET_GROUPS=()
DELETED_EBS=()
RELEASED_EIP=()
DELETED_DDB=()
DELETED_SUBNETS=()
DELETED_VPCS=()
WARNINGS=()
TOTAL_FOUND=0

# Per-scenario counters
COUNT_SCENARIO_A=0
COUNT_SCENARIO_B=0
COUNT_TLS_FRAG=0
HAS_ERRORS=false

# ---------------------------------------------------------------------------
# 1. Verify AWS credentials
# ---------------------------------------------------------------------------
print_cyan "=== G.O.A.T. Demo Cleanup ==="
echo ""
print_yellow "Verifying AWS credentials..."

ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text 2>/dev/null)
if [ $? -ne 0 ] || [ -z "$ACCOUNT_ID" ]; then
    print_red "ERROR: AWS credentials not configured."
    print_red "Run 'aws configure' or set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY."
    exit 1
fi
print_green "  Authenticated to account: $ACCOUNT_ID"

# ---------------------------------------------------------------------------
# 2. Detect region
# ---------------------------------------------------------------------------
print_yellow "Detecting AWS region..."

REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-}}"
if [ -z "$REGION" ]; then
    REGION=$(aws configure get region 2>/dev/null)
fi
if [ -z "$REGION" ]; then
    REGION="us-east-1"
    print_yellow "  No region configured, falling back to us-east-1"
fi
print_green "  Region: $REGION"
echo ""

# ---------------------------------------------------------------------------
# 3. Terminate EC2 instances
# ---------------------------------------------------------------------------
print_magenta "--- EC2 Instances ---"

print_yellow "Finding EC2 instances tagged goat-demo=true..."
EC2_ARNS=$(aws resourcegroupstaggingapi get-resources \
    --tag-filters Key=goat-demo,Values=true \
    --resource-type-filters ec2:instance \
    --query "ResourceTagMappingList[].ResourceARN" --output text --region "$REGION" 2>/dev/null)

if [ -n "$EC2_ARNS" ] && [ "$EC2_ARNS" != "None" ]; then
    for arn in $EC2_ARNS; do
        # Extract instance ID from ARN (arn:aws:ec2:region:account:instance/i-xxx)
        instance_id=$(echo "$arn" | grep -oP 'i-[a-f0-9]+')
        if [ -z "$instance_id" ]; then continue; fi
        TOTAL_FOUND=$((TOTAL_FOUND + 1))

        # Check if instance is already terminated
        state=$(aws ec2 describe-instances \
            --instance-ids "$instance_id" \
            --query "Reservations[].Instances[].State.Name" --output text --region "$REGION" 2>/dev/null)
        if [ "$state" = "terminated" ] || [ "$state" = "shutting-down" ]; then
            print_gray "  Instance $instance_id already terminated, skipping"
            continue
        fi

        print_yellow "  Terminating EC2 instance: $instance_id..."
        RESULT=$(aws ec2 terminate-instances --instance-ids "$instance_id" --region "$REGION" 2>&1)
        if [ $? -ne 0 ]; then
            print_red "  WARNING: Failed to terminate $instance_id: $RESULT"
            WARNINGS+=("EC2 terminate failed: $instance_id")
        else
            print_green "  Terminated: $instance_id"
            TERMINATED_EC2+=("$instance_id")
        fi
    done
else
    print_gray "  No EC2 instances found"
fi

echo ""

# ---------------------------------------------------------------------------
# 4. Delete RDS instances (skip final snapshot)
# ---------------------------------------------------------------------------
print_magenta "--- RDS Instances ---"

print_yellow "Finding RDS instances tagged goat-demo=true..."
RDS_ARNS=$(aws resourcegroupstaggingapi get-resources \
    --tag-filters Key=goat-demo,Values=true \
    --resource-type-filters rds:db \
    --query "ResourceTagMappingList[].ResourceARN" --output text --region "$REGION" 2>/dev/null)

if [ -n "$RDS_ARNS" ] && [ "$RDS_ARNS" != "None" ]; then
    for arn in $RDS_ARNS; do
        # Extract DB instance identifier from ARN (arn:aws:rds:region:account:db:identifier)
        db_id=$(echo "$arn" | sed 's/.*:db://')
        if [ -z "$db_id" ]; then continue; fi
        TOTAL_FOUND=$((TOTAL_FOUND + 1))

        # Check if instance is already deleting
        db_status=$(aws rds describe-db-instances \
            --db-instance-identifier "$db_id" \
            --query "DBInstances[0].DBInstanceStatus" --output text --region "$REGION" 2>/dev/null)
        if [ "$db_status" = "deleting" ]; then
            print_gray "  RDS instance $db_id already deleting, skipping"
            DELETED_RDS+=("$db_id")
            continue
        fi

        print_yellow "  Deleting RDS instance: $db_id (skip-final-snapshot)..."
        print_gray "  (This may take several minutes to complete)"
        RESULT=$(aws rds delete-db-instance \
            --db-instance-identifier "$db_id" \
            --skip-final-snapshot \
            --delete-automated-backups \
            --region "$REGION" 2>&1)
        if [ $? -ne 0 ]; then
            print_red "  WARNING: Failed to delete RDS $db_id: $RESULT"
            WARNINGS+=("RDS delete failed: $db_id")
        else
            print_green "  Deleting: $db_id (in progress)"
            DELETED_RDS+=("$db_id")
        fi
    done
else
    print_gray "  No RDS instances found"
fi

echo ""

# ---------------------------------------------------------------------------
# 5. Delete DB subnet groups
# ---------------------------------------------------------------------------
print_magenta "--- DB Subnet Groups ---"

print_yellow "Checking for goat-demo DB subnet group..."
DB_SG_CHECK=$(aws rds describe-db-subnet-groups \
    --db-subnet-group-name goat-demo-db-subnet-group \
    --query "DBSubnetGroups[0].DBSubnetGroupName" --output text --region "$REGION" 2>/dev/null)

if [ -n "$DB_SG_CHECK" ] && [ "$DB_SG_CHECK" != "None" ]; then
    TOTAL_FOUND=$((TOTAL_FOUND + 1))

    # DB subnet group can only be deleted after RDS instances are fully gone.
    # If RDS is still deleting, this will fail — warn and continue.
    print_yellow "  Deleting DB subnet group: goat-demo-db-subnet-group..."
    RESULT=$(aws rds delete-db-subnet-group \
        --db-subnet-group-name goat-demo-db-subnet-group \
        --region "$REGION" 2>&1)
    if [ $? -ne 0 ]; then
        if echo "$RESULT" | grep -qi "is still being used"; then
            print_yellow "  DB subnet group still in use (RDS deleting). Re-run cleanup after RDS deletion completes."
            WARNINGS+=("DB subnet group in use - re-run cleanup later")
        else
            print_red "  WARNING: Failed to delete DB subnet group: $RESULT"
            WARNINGS+=("DB subnet group delete failed")
        fi
    else
        print_green "  Deleted DB subnet group: goat-demo-db-subnet-group"
        DELETED_DB_SUBNET_GROUPS+=("goat-demo-db-subnet-group")
    fi
else
    print_gray "  No DB subnet group found"
fi

echo ""

# ---------------------------------------------------------------------------
# 6. Delete EBS volumes
# ---------------------------------------------------------------------------
print_magenta "--- EBS Volumes ---"

print_yellow "Finding EBS volumes tagged goat-demo=true..."
EBS_ARNS=$(aws resourcegroupstaggingapi get-resources \
    --tag-filters Key=goat-demo,Values=true \
    --resource-type-filters ec2:volume \
    --query "ResourceTagMappingList[].ResourceARN" --output text --region "$REGION" 2>/dev/null)

if [ -n "$EBS_ARNS" ] && [ "$EBS_ARNS" != "None" ]; then
    for arn in $EBS_ARNS; do
        # Extract volume ID from ARN
        vol_id=$(echo "$arn" | grep -oP 'vol-[a-f0-9]+')
        if [ -z "$vol_id" ]; then continue; fi
        TOTAL_FOUND=$((TOTAL_FOUND + 1))

        # Check volume state
        vol_state=$(aws ec2 describe-volumes \
            --volume-ids "$vol_id" \
            --query "Volumes[0].State" --output text --region "$REGION" 2>/dev/null)
        if [ -z "$vol_state" ] || [ "$vol_state" = "None" ]; then
            print_gray "  Volume $vol_id not found, skipping"
            continue
        fi
        if [ "$vol_state" = "in-use" ]; then
            print_yellow "  Volume $vol_id is in-use (attached). Will retry after EC2 termination completes."
            WARNINGS+=("EBS volume in-use: $vol_id - re-run cleanup later")
            continue
        fi

        print_yellow "  Deleting EBS volume: $vol_id..."
        RESULT=$(aws ec2 delete-volume --volume-id "$vol_id" --region "$REGION" 2>&1)
        if [ $? -ne 0 ]; then
            print_red "  WARNING: Failed to delete volume $vol_id: $RESULT"
            WARNINGS+=("EBS delete failed: $vol_id")
        else
            print_green "  Deleted: $vol_id"
            DELETED_EBS+=("$vol_id")
        fi
    done
else
    print_gray "  No EBS volumes found"
fi

echo ""

# ---------------------------------------------------------------------------
# 7. Release Elastic IPs
# ---------------------------------------------------------------------------
print_magenta "--- Elastic IPs ---"

print_yellow "Finding Elastic IPs tagged goat-demo=true..."
EIP_ARNS=$(aws resourcegroupstaggingapi get-resources \
    --tag-filters Key=goat-demo,Values=true \
    --resource-type-filters ec2:elastic-ip \
    --query "ResourceTagMappingList[].ResourceARN" --output text --region "$REGION" 2>/dev/null)

if [ -n "$EIP_ARNS" ] && [ "$EIP_ARNS" != "None" ]; then
    for arn in $EIP_ARNS; do
        # Extract allocation ID from ARN (arn:aws:ec2:region:account:elastic-ip/eipalloc-xxx)
        alloc_id=$(echo "$arn" | grep -oP 'eipalloc-[a-f0-9]+')
        if [ -z "$alloc_id" ]; then continue; fi
        TOTAL_FOUND=$((TOTAL_FOUND + 1))

        print_yellow "  Releasing Elastic IP: $alloc_id..."
        RESULT=$(aws ec2 release-address --allocation-id "$alloc_id" --region "$REGION" 2>&1)
        if [ $? -ne 0 ]; then
            if echo "$RESULT" | grep -qi "not found"; then
                print_gray "  Elastic IP $alloc_id already released, skipping"
            else
                print_red "  WARNING: Failed to release $alloc_id: $RESULT"
                WARNINGS+=("EIP release failed: $alloc_id")
            fi
        else
            print_green "  Released: $alloc_id"
            RELEASED_EIP+=("$alloc_id")
        fi
    done
else
    print_gray "  No Elastic IPs found"
fi

echo ""

# ---------------------------------------------------------------------------
# 8. Delete DynamoDB tables
# ---------------------------------------------------------------------------
print_magenta "--- DynamoDB Tables ---"

print_yellow "Finding DynamoDB tables tagged goat-demo=true..."
DDB_ARNS=$(aws resourcegroupstaggingapi get-resources \
    --tag-filters Key=goat-demo,Values=true \
    --resource-type-filters dynamodb:table \
    --query "ResourceTagMappingList[].ResourceARN" --output text --region "$REGION" 2>/dev/null)

if [ -n "$DDB_ARNS" ] && [ "$DDB_ARNS" != "None" ]; then
    for arn in $DDB_ARNS; do
        # Extract table name from ARN (arn:aws:dynamodb:region:account:table/name)
        table_name=$(echo "$arn" | sed 's/.*table\///')
        if [ -z "$table_name" ]; then continue; fi
        TOTAL_FOUND=$((TOTAL_FOUND + 1))

        print_yellow "  Deleting DynamoDB table: $table_name..."
        RESULT=$(aws dynamodb delete-table --table-name "$table_name" --region "$REGION" 2>&1)
        if [ $? -ne 0 ]; then
            if echo "$RESULT" | grep -qi "not found\|ResourceNotFoundException"; then
                print_gray "  Table $table_name already deleted, skipping"
            else
                print_red "  WARNING: Failed to delete table $table_name: $RESULT"
                WARNINGS+=("DynamoDB delete failed: $table_name")
            fi
        else
            print_green "  Deleted: $table_name"
            DELETED_DDB+=("$table_name")
        fi
    done
else
    print_gray "  No DynamoDB tables found"
fi

echo ""

# ---------------------------------------------------------------------------
# 9. Delete subnets
# ---------------------------------------------------------------------------
print_magenta "--- Subnets ---"

print_yellow "Finding subnets tagged goat-demo=true..."
SUBNET_IDS=$(aws ec2 describe-subnets \
    --filters "Name=tag:goat-demo,Values=true" \
    --query "Subnets[].SubnetId" --output text --region "$REGION" 2>/dev/null)

if [ -n "$SUBNET_IDS" ] && [ "$SUBNET_IDS" != "None" ]; then
    for subnet_id in $SUBNET_IDS; do
        TOTAL_FOUND=$((TOTAL_FOUND + 1))

        print_yellow "  Deleting subnet: $subnet_id..."
        RESULT=$(aws ec2 delete-subnet --subnet-id "$subnet_id" --region "$REGION" 2>&1)
        if [ $? -ne 0 ]; then
            if echo "$RESULT" | grep -qi "not found"; then
                print_gray "  Subnet $subnet_id already deleted, skipping"
            elif echo "$RESULT" | grep -qi "DependencyViolation"; then
                print_yellow "  Subnet $subnet_id has dependencies (resources still terminating). Re-run cleanup later."
                WARNINGS+=("Subnet dependency: $subnet_id - re-run cleanup later")
            else
                print_red "  WARNING: Failed to delete subnet $subnet_id: $RESULT"
                WARNINGS+=("Subnet delete failed: $subnet_id")
            fi
        else
            print_green "  Deleted: $subnet_id"
            DELETED_SUBNETS+=("$subnet_id")
        fi
    done
else
    print_gray "  No subnets found"
fi

echo ""

# ---------------------------------------------------------------------------
# 10. Delete VPCs
# ---------------------------------------------------------------------------
print_magenta "--- VPCs ---"

print_yellow "Finding VPCs tagged goat-demo=true..."
VPC_IDS=$(aws ec2 describe-vpcs \
    --filters "Name=tag:goat-demo,Values=true" \
    --query "Vpcs[].VpcId" --output text --region "$REGION" 2>/dev/null)

if [ -n "$VPC_IDS" ] && [ "$VPC_IDS" != "None" ]; then
    for vpc_id in $VPC_IDS; do
        TOTAL_FOUND=$((TOTAL_FOUND + 1))

        print_yellow "  Deleting VPC: $vpc_id..."
        RESULT=$(aws ec2 delete-vpc --vpc-id "$vpc_id" --region "$REGION" 2>&1)
        if [ $? -ne 0 ]; then
            if echo "$RESULT" | grep -qi "not found"; then
                print_gray "  VPC $vpc_id already deleted, skipping"
            elif echo "$RESULT" | grep -qi "DependencyViolation"; then
                print_yellow "  VPC $vpc_id has dependencies (subnets/instances still terminating). Re-run cleanup later."
                WARNINGS+=("VPC dependency: $vpc_id - re-run cleanup later")
            else
                print_red "  WARNING: Failed to delete VPC $vpc_id: $RESULT"
                WARNINGS+=("VPC delete failed: $vpc_id")
            fi
        else
            print_green "  Deleted: $vpc_id"
            DELETED_VPCS+=("$vpc_id")
        fi
    done
else
    print_gray "  No VPCs found"
fi

echo ""

# ---------------------------------------------------------------------------
# 11. TLS Fragmentation Scenario Cleanup (goat-scenario=tls-fragmentation)
# ---------------------------------------------------------------------------
print_magenta "--- TLS Fragmentation Scenario (goat-scenario=tls-fragmentation) ---"
echo ""

# Find the TLS scenario VPC
TLS_VPC_ID=$(aws ec2 describe-vpcs \
    --filters "Name=tag:goat-scenario,Values=tls-fragmentation" \
    --query "Vpcs[0].VpcId" --output text --region "$REGION" 2>/dev/null)

if [ -n "$TLS_VPC_ID" ] && [ "$TLS_VPC_ID" != "None" ]; then
    TOTAL_FOUND=$((TOTAL_FOUND + 1))

    # 11a. Delete Kubernetes test pod and EKS resources
    print_yellow "Finding EKS clusters tagged goat-scenario=tls-fragmentation..."
    EKS_CLUSTER_NAME=""
    EKS_CLUSTERS=$(aws eks list-clusters --query "clusters" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$EKS_CLUSTERS" ] && [ "$EKS_CLUSTERS" != "None" ]; then
        for cluster in $EKS_CLUSTERS; do
            CLUSTER_TAGS=$(aws eks describe-cluster --name "$cluster" --query "cluster.tags" --output json --region "$REGION" 2>/dev/null)
            if echo "$CLUSTER_TAGS" | grep -q '"goat-scenario".*"tls-fragmentation"' 2>/dev/null; then
                EKS_CLUSTER_NAME="$cluster"
                break
            fi
        done
    fi

    if [ -n "$EKS_CLUSTER_NAME" ]; then
        TOTAL_FOUND=$((TOTAL_FOUND + 1))

        # Delete test pod (best-effort via kubectl if kubeconfig available)
        print_yellow "  Attempting to delete TLS test pod..."
        aws eks update-kubeconfig --name "$EKS_CLUSTER_NAME" --region "$REGION" 2>/dev/null
        if command -v kubectl &>/dev/null; then
            kubectl delete pod -l app=goat-tls-test --ignore-not-found=true 2>/dev/null
            kubectl delete deployment -l app=goat-tls-test --ignore-not-found=true 2>/dev/null
            print_green "  Test pod deletion initiated"
            COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
        else
            print_yellow "  kubectl not available, skipping pod deletion (EKS cluster deletion will remove pods)"
        fi

        # Delete managed node groups
        print_yellow "  Finding managed node groups..."
        NODE_GROUPS=$(aws eks list-nodegroups --cluster-name "$EKS_CLUSTER_NAME" \
            --query "nodegroups" --output text --region "$REGION" 2>/dev/null)
        if [ -n "$NODE_GROUPS" ] && [ "$NODE_GROUPS" != "None" ]; then
            for ng in $NODE_GROUPS; do
                print_yellow "  Deleting node group: $ng..."
                RESULT=$(aws eks delete-nodegroup --cluster-name "$EKS_CLUSTER_NAME" \
                    --nodegroup-name "$ng" --region "$REGION" 2>&1)
                if [ $? -ne 0 ]; then
                    if echo "$RESULT" | grep -qi "ResourceNotFoundException\|not found\|No node group"; then
                        print_gray "  Node group $ng already deleted, skipping"
                    else
                        print_red "  WARNING: Failed to delete node group $ng: $RESULT"
                        WARNINGS+=("EKS node group delete failed: $ng")
                        HAS_ERRORS=true
                    fi
                else
                    print_green "  Deleting node group: $ng (waiting for completion...)"
                    # Wait for node group deletion
                    aws eks wait nodegroup-deleted --cluster-name "$EKS_CLUSTER_NAME" \
                        --nodegroup-name "$ng" --region "$REGION" 2>/dev/null
                    COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
                fi
            done
        fi

        # Delete EKS cluster
        print_yellow "  Deleting EKS cluster: $EKS_CLUSTER_NAME..."
        RESULT=$(aws eks delete-cluster --name "$EKS_CLUSTER_NAME" --region "$REGION" 2>&1)
        if [ $? -ne 0 ]; then
            if echo "$RESULT" | grep -qi "ResourceNotFoundException\|not found"; then
                print_gray "  EKS cluster $EKS_CLUSTER_NAME already deleted, skipping"
            else
                print_red "  WARNING: Failed to delete EKS cluster $EKS_CLUSTER_NAME: $RESULT"
                WARNINGS+=("EKS cluster delete failed: $EKS_CLUSTER_NAME")
                HAS_ERRORS=true
            fi
        else
            print_green "  Deleting EKS cluster: $EKS_CLUSTER_NAME (waiting for completion...)"
            aws eks wait cluster-deleted --name "$EKS_CLUSTER_NAME" --region "$REGION" 2>/dev/null
            COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
        fi
    else
        print_gray "  No EKS cluster found for tls-fragmentation scenario"
    fi

    # 11b. Delete Network Firewall
    print_yellow "  Finding Network Firewall resources..."
    NFW_NAME="goat-demo-tls-nfw"
    NFW_STATUS=$(aws network-firewall describe-firewall --firewall-name "$NFW_NAME" \
        --query "Firewall.FirewallArn" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$NFW_STATUS" ] && [ "$NFW_STATUS" != "None" ]; then
        TOTAL_FOUND=$((TOTAL_FOUND + 1))
        print_yellow "  Deleting Network Firewall: $NFW_NAME..."
        RESULT=$(aws network-firewall delete-firewall --firewall-name "$NFW_NAME" --region "$REGION" 2>&1)
        if [ $? -ne 0 ]; then
            if echo "$RESULT" | grep -qi "ResourceNotFoundException\|not found"; then
                print_gray "  Network Firewall $NFW_NAME already deleted, skipping"
            else
                print_red "  WARNING: Failed to delete Network Firewall $NFW_NAME: $RESULT"
                WARNINGS+=("Network Firewall delete failed: $NFW_NAME")
                HAS_ERRORS=true
            fi
        else
            print_green "  Deleting Network Firewall: $NFW_NAME (waiting for completion...)"
            # Wait for firewall deletion (poll status)
            for i in $(seq 1 60); do
                sleep 10
                FW_CHECK=$(aws network-firewall describe-firewall --firewall-name "$NFW_NAME" \
                    --query "Firewall.FirewallArn" --output text --region "$REGION" 2>/dev/null)
                if [ -z "$FW_CHECK" ] || [ "$FW_CHECK" == "None" ]; then
                    break
                fi
            done
            COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
        fi
    else
        print_gray "  No Network Firewall found"
    fi

    # Delete firewall policy
    NFW_POLICY_NAME="goat-demo-tls-policy"
    POLICY_CHECK=$(aws network-firewall describe-firewall-policy --firewall-policy-name "$NFW_POLICY_NAME" \
        --query "FirewallPolicyResponse.FirewallPolicyArn" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$POLICY_CHECK" ] && [ "$POLICY_CHECK" != "None" ]; then
        TOTAL_FOUND=$((TOTAL_FOUND + 1))
        print_yellow "  Deleting firewall policy: $NFW_POLICY_NAME..."
        RESULT=$(aws network-firewall delete-firewall-policy --firewall-policy-name "$NFW_POLICY_NAME" --region "$REGION" 2>&1)
        if [ $? -ne 0 ]; then
            if echo "$RESULT" | grep -qi "ResourceNotFoundException\|not found"; then
                print_gray "  Firewall policy already deleted, skipping"
            else
                print_red "  WARNING: Failed to delete firewall policy: $RESULT"
                WARNINGS+=("Firewall policy delete failed: $NFW_POLICY_NAME")
                HAS_ERRORS=true
            fi
        else
            print_green "  Deleted firewall policy: $NFW_POLICY_NAME"
            COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
        fi
    fi

    # Delete firewall rule group
    NFW_RULE_GROUP_NAME="goat-demo-tls-rule-group"
    RG_CHECK=$(aws network-firewall describe-rule-group --rule-group-name "$NFW_RULE_GROUP_NAME" --type STATEFUL \
        --query "RuleGroupResponse.RuleGroupArn" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$RG_CHECK" ] && [ "$RG_CHECK" != "None" ]; then
        TOTAL_FOUND=$((TOTAL_FOUND + 1))
        print_yellow "  Deleting firewall rule group: $NFW_RULE_GROUP_NAME..."
        RESULT=$(aws network-firewall delete-rule-group --rule-group-name "$NFW_RULE_GROUP_NAME" --type STATEFUL --region "$REGION" 2>&1)
        if [ $? -ne 0 ]; then
            if echo "$RESULT" | grep -qi "ResourceNotFoundException\|not found"; then
                print_gray "  Rule group already deleted, skipping"
            else
                print_red "  WARNING: Failed to delete rule group: $RESULT"
                WARNINGS+=("Firewall rule group delete failed: $NFW_RULE_GROUP_NAME")
                HAS_ERRORS=true
            fi
        else
            print_green "  Deleted firewall rule group: $NFW_RULE_GROUP_NAME"
            COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
        fi
    fi

    # 11c. Delete NAT Gateway
    print_yellow "  Finding NAT Gateways tagged goat-scenario=tls-fragmentation..."
    NAT_GW_IDS=$(aws ec2 describe-nat-gateways \
        --filter "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=state,Values=available,pending" \
        --query "NatGateways[].NatGatewayId" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$NAT_GW_IDS" ] && [ "$NAT_GW_IDS" != "None" ]; then
        for nat_id in $NAT_GW_IDS; do
            TOTAL_FOUND=$((TOTAL_FOUND + 1))
            print_yellow "  Deleting NAT Gateway: $nat_id..."
            RESULT=$(aws ec2 delete-nat-gateway --nat-gateway-id "$nat_id" --region "$REGION" 2>&1)
            if [ $? -ne 0 ]; then
                if echo "$RESULT" | grep -qi "not found\|NatGatewayNotFound"; then
                    print_gray "  NAT Gateway $nat_id already deleted, skipping"
                else
                    print_red "  WARNING: Failed to delete NAT Gateway $nat_id: $RESULT"
                    WARNINGS+=("NAT Gateway delete failed: $nat_id")
                    HAS_ERRORS=true
                fi
            else
                print_green "  Deleting NAT Gateway: $nat_id (waiting for completion...)"
                aws ec2 wait nat-gateway-deleted --nat-gateway-ids "$nat_id" --region "$REGION" 2>/dev/null || true
                COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
            fi
        done
    else
        print_gray "  No NAT Gateways found"
    fi

    # 11d. Detach and delete Internet Gateway
    print_yellow "  Finding Internet Gateways tagged goat-scenario=tls-fragmentation..."
    IGW_IDS=$(aws ec2 describe-internet-gateways \
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" \
        --query "InternetGateways[].InternetGatewayId" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$IGW_IDS" ] && [ "$IGW_IDS" != "None" ]; then
        for igw_id in $IGW_IDS; do
            TOTAL_FOUND=$((TOTAL_FOUND + 1))
            # Detach from VPC first
            aws ec2 detach-internet-gateway --internet-gateway-id "$igw_id" --vpc-id "$TLS_VPC_ID" --region "$REGION" 2>/dev/null
            print_yellow "  Deleting Internet Gateway: $igw_id..."
            RESULT=$(aws ec2 delete-internet-gateway --internet-gateway-id "$igw_id" --region "$REGION" 2>&1)
            if [ $? -ne 0 ]; then
                if echo "$RESULT" | grep -qi "not found\|InvalidInternetGatewayID"; then
                    print_gray "  Internet Gateway $igw_id already deleted, skipping"
                else
                    print_red "  WARNING: Failed to delete Internet Gateway $igw_id: $RESULT"
                    WARNINGS+=("Internet Gateway delete failed: $igw_id")
                    HAS_ERRORS=true
                fi
            else
                print_green "  Deleted Internet Gateway: $igw_id"
                COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
            fi
        done
    else
        print_gray "  No Internet Gateways found"
    fi

    # 11e. Delete subnets tagged goat-scenario=tls-fragmentation
    print_yellow "  Finding subnets tagged goat-scenario=tls-fragmentation..."
    TLS_SUBNET_IDS=$(aws ec2 describe-subnets \
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" \
        --query "Subnets[].SubnetId" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$TLS_SUBNET_IDS" ] && [ "$TLS_SUBNET_IDS" != "None" ]; then
        for subnet_id in $TLS_SUBNET_IDS; do
            TOTAL_FOUND=$((TOTAL_FOUND + 1))
            print_yellow "  Deleting subnet: $subnet_id..."
            RESULT=$(aws ec2 delete-subnet --subnet-id "$subnet_id" --region "$REGION" 2>&1)
            if [ $? -ne 0 ]; then
                if echo "$RESULT" | grep -qi "not found\|InvalidSubnetID"; then
                    print_gray "  Subnet $subnet_id already deleted, skipping"
                else
                    print_red "  WARNING: Failed to delete subnet $subnet_id: $RESULT"
                    WARNINGS+=("TLS subnet delete failed: $subnet_id")
                    HAS_ERRORS=true
                fi
            else
                print_green "  Deleted subnet: $subnet_id"
                COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
            fi
        done
    else
        print_gray "  No TLS scenario subnets found"
    fi

    # 11f. Delete route tables tagged goat-scenario=tls-fragmentation
    print_yellow "  Finding route tables tagged goat-scenario=tls-fragmentation..."
    TLS_RT_IDS=$(aws ec2 describe-route-tables \
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" \
        --query "RouteTables[].RouteTableId" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$TLS_RT_IDS" ] && [ "$TLS_RT_IDS" != "None" ]; then
        for rt_id in $TLS_RT_IDS; do
            TOTAL_FOUND=$((TOTAL_FOUND + 1))
            # Disassociate any subnet associations first (skip main)
            ASSOC_IDS=$(aws ec2 describe-route-tables --route-table-ids "$rt_id" \
                --query "RouteTables[0].Associations[?!Main].RouteTableAssociationId" --output text --region "$REGION" 2>/dev/null)
            if [ -n "$ASSOC_IDS" ] && [ "$ASSOC_IDS" != "None" ]; then
                for assoc_id in $ASSOC_IDS; do
                    aws ec2 disassociate-route-table --association-id "$assoc_id" --region "$REGION" 2>/dev/null
                done
            fi
            print_yellow "  Deleting route table: $rt_id..."
            RESULT=$(aws ec2 delete-route-table --route-table-id "$rt_id" --region "$REGION" 2>&1)
            if [ $? -ne 0 ]; then
                if echo "$RESULT" | grep -qi "not found\|InvalidRouteTableID"; then
                    print_gray "  Route table $rt_id already deleted, skipping"
                else
                    print_red "  WARNING: Failed to delete route table $rt_id: $RESULT"
                    WARNINGS+=("TLS route table delete failed: $rt_id")
                    HAS_ERRORS=true
                fi
            else
                print_green "  Deleted route table: $rt_id"
                COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
            fi
        done
    else
        print_gray "  No TLS scenario route tables found"
    fi

    # 11g. Delete security groups in the TLS VPC (non-default only)
    print_yellow "  Finding security groups in TLS VPC..."
    TLS_SG_IDS=$(aws ec2 describe-security-groups \
        --filters "Name=vpc-id,Values=$TLS_VPC_ID" \
        --query "SecurityGroups[?GroupName!='default'].GroupId" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$TLS_SG_IDS" ] && [ "$TLS_SG_IDS" != "None" ]; then
        for sg_id in $TLS_SG_IDS; do
            TOTAL_FOUND=$((TOTAL_FOUND + 1))
            print_yellow "  Deleting security group: $sg_id..."
            RESULT=$(aws ec2 delete-security-group --group-id "$sg_id" --region "$REGION" 2>&1)
            if [ $? -ne 0 ]; then
                if echo "$RESULT" | grep -qi "not found\|InvalidGroup"; then
                    print_gray "  Security group $sg_id already deleted, skipping"
                else
                    print_red "  WARNING: Failed to delete security group $sg_id: $RESULT"
                    WARNINGS+=("TLS security group delete failed: $sg_id")
                    HAS_ERRORS=true
                fi
            else
                print_green "  Deleted security group: $sg_id"
                COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
            fi
        done
    else
        print_gray "  No non-default security groups found in TLS VPC"
    fi

    # 11h. Delete the TLS VPC
    print_yellow "  Deleting TLS VPC: $TLS_VPC_ID..."
    RESULT=$(aws ec2 delete-vpc --vpc-id "$TLS_VPC_ID" --region "$REGION" 2>&1)
    if [ $? -ne 0 ]; then
        if echo "$RESULT" | grep -qi "not found\|InvalidVpcID"; then
            print_gray "  VPC $TLS_VPC_ID already deleted, skipping"
        elif echo "$RESULT" | grep -qi "DependencyViolation"; then
            print_yellow "  VPC $TLS_VPC_ID has dependencies still terminating. Re-run cleanup later."
            WARNINGS+=("TLS VPC dependency: $TLS_VPC_ID - re-run cleanup later")
            HAS_ERRORS=true
        else
            print_red "  WARNING: Failed to delete VPC $TLS_VPC_ID: $RESULT"
            WARNINGS+=("TLS VPC delete failed: $TLS_VPC_ID")
            HAS_ERRORS=true
        fi
    else
        print_green "  Deleted TLS VPC: $TLS_VPC_ID"
        COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
    fi

    # Release any EIPs tagged for TLS scenario
    print_yellow "  Finding Elastic IPs tagged goat-scenario=tls-fragmentation..."
    TLS_EIP_ARNS=$(aws resourcegroupstaggingapi get-resources \
        --tag-filters Key=goat-scenario,Values=tls-fragmentation \
        --resource-type-filters ec2:elastic-ip \
        --query "ResourceTagMappingList[].ResourceARN" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$TLS_EIP_ARNS" ] && [ "$TLS_EIP_ARNS" != "None" ]; then
        for arn in $TLS_EIP_ARNS; do
            alloc_id=$(echo "$arn" | grep -oP 'eipalloc-[a-f0-9]+')
            if [ -z "$alloc_id" ]; then continue; fi
            TOTAL_FOUND=$((TOTAL_FOUND + 1))
            print_yellow "  Releasing Elastic IP: $alloc_id..."
            RESULT=$(aws ec2 release-address --allocation-id "$alloc_id" --region "$REGION" 2>&1)
            if [ $? -ne 0 ]; then
                if echo "$RESULT" | grep -qi "not found\|InvalidAllocationID"; then
                    print_gray "  Elastic IP $alloc_id already released, skipping"
                else
                    print_red "  WARNING: Failed to release $alloc_id: $RESULT"
                    WARNINGS+=("TLS EIP release failed: $alloc_id")
                    HAS_ERRORS=true
                fi
            else
                print_green "  Released: $alloc_id"
                COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
            fi
        done
    fi

else
    print_gray "  No TLS Fragmentation Scenario resources found (no VPC with goat-scenario=tls-fragmentation)"
fi

echo ""
# ---------------------------------------------------------------------------
# 12. Summary
# ---------------------------------------------------------------------------

# Count resources per scenario from the goat-demo=true tagged resources
# (Scenarios A & B are identified by goat-scenario=a or goat-scenario=b)
COUNT_SCENARIO_A=$(aws resourcegroupstaggingapi get-resources \
    --tag-filters Key=goat-scenario,Values=a \
    --query "length(ResourceTagMappingList)" --output text --region "$REGION" 2>/dev/null || echo "0")
COUNT_SCENARIO_B=$(aws resourcegroupstaggingapi get-resources \
    --tag-filters Key=goat-scenario,Values=b \
    --query "length(ResourceTagMappingList)" --output text --region "$REGION" 2>/dev/null || echo "0")

if [ "$TOTAL_FOUND" -eq 0 ]; then
    print_cyan "========================================"
    print_cyan "  No Demo Resources Found"
    print_cyan "========================================"
    echo ""
    print_gray "  No resources tagged with goat-demo=true were found in $REGION."
    print_gray "  No goat-scenario=a resources found."
    print_gray "  No goat-scenario=b resources found."
    print_gray "  No goat-scenario=tls-fragmentation resources found."
    print_gray "  Nothing to clean up."
    echo ""
    if [ "$HAS_ERRORS" = true ]; then
        exit 1
    fi
    exit 0
fi

print_green "========================================"
print_green "  G.O.A.T. Demo Cleanup Complete!"
print_green "========================================"
echo ""
print_cyan "  Region:              $REGION"
echo ""
print_cyan "  Resources removed per scenario:"
print_cyan "    goat-scenario=a:                 removed from account (found $COUNT_SCENARIO_A remaining)"
print_cyan "    goat-scenario=b:                 removed from account (found $COUNT_SCENARIO_B remaining)"
print_cyan "    goat-scenario=tls-fragmentation: $COUNT_TLS_FRAG removed"
echo ""

if [ ${#TERMINATED_EC2[@]} -gt 0 ]; then
    EC2_LIST=$(printf '%s, ' "${TERMINATED_EC2[@]}" | sed 's/, $//')
    print_cyan "  Terminated EC2:      $EC2_LIST"
fi
if [ ${#DELETED_RDS[@]} -gt 0 ]; then
    RDS_LIST=$(printf '%s, ' "${DELETED_RDS[@]}" | sed 's/, $//')
    print_cyan "  Deleted RDS:         $RDS_LIST"
fi
if [ ${#DELETED_DB_SUBNET_GROUPS[@]} -gt 0 ]; then
    DBSG_LIST=$(printf '%s, ' "${DELETED_DB_SUBNET_GROUPS[@]}" | sed 's/, $//')
    print_cyan "  Deleted DB SubGrp:   $DBSG_LIST"
fi
if [ ${#DELETED_EBS[@]} -gt 0 ]; then
    EBS_LIST=$(printf '%s, ' "${DELETED_EBS[@]}" | sed 's/, $//')
    print_cyan "  Deleted EBS:         $EBS_LIST"
fi
if [ ${#RELEASED_EIP[@]} -gt 0 ]; then
    EIP_LIST=$(printf '%s, ' "${RELEASED_EIP[@]}" | sed 's/, $//')
    print_cyan "  Released EIP:        $EIP_LIST"
fi
if [ ${#DELETED_DDB[@]} -gt 0 ]; then
    DDB_LIST=$(printf '%s, ' "${DELETED_DDB[@]}" | sed 's/, $//')
    print_cyan "  Deleted DynamoDB:    $DDB_LIST"
fi
if [ ${#DELETED_SUBNETS[@]} -gt 0 ]; then
    SUB_LIST=$(printf '%s, ' "${DELETED_SUBNETS[@]}" | sed 's/, $//')
    print_cyan "  Deleted Subnets:     $SUB_LIST"
fi
if [ ${#DELETED_VPCS[@]} -gt 0 ]; then
    VPC_LIST=$(printf '%s, ' "${DELETED_VPCS[@]}" | sed 's/, $//')
    print_cyan "  Deleted VPCs:        $VPC_LIST"
fi

if [ ${#WARNINGS[@]} -gt 0 ]; then
    echo ""
    print_yellow "  Warnings:"
    for w in "${WARNINGS[@]}"; do
        print_yellow "    - $w"
    done
    echo ""
    print_yellow "  Some resources may still be terminating. Re-run this script"
    print_yellow "  in a few minutes to clean up remaining dependencies."
fi

echo ""
print_green "  All demo resources have been removed."
print_gray "  (Support cases are already resolved and cannot be deleted via API)"
echo ""

if [ "$HAS_ERRORS" = true ]; then
    exit 1
fi
