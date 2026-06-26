#!/bin/bash
# G.O.A.T. Demo Cleanup - Remove All Demo Resources
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
#   5. NAT Gateways (must complete before EIPs and subnets)
#   6. Elastic IPs (no dependencies)
#   7. DynamoDB tables (no dependencies)
#   8. Subnets (depends on EC2/RDS being gone)
#   9. VPCs (depends on subnets being gone)
#
# Deletion order (TLS Fragmentation):
#   1. Kubernetes test pod
#   2. EKS managed node group
#   3. EKS cluster
#   4. Transit Gateway (attachments, route tables, TGW itself)
#   5. Network Firewall (logging config cleared first, + policy + rule group)
#   6. NAT Gateway
#   7. Internet Gateway
#   8. Subnets
#   9. Route tables
#  10. Security groups
#  11. Elastic IPs
#  12. CloudWatch log groups
#  13. IAM roles
#  14. Inspection VPC
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
        instance_id=$(echo "$arn" | grep -oP 'i-[a-f0-9]+')
        if [ -z "$instance_id" ]; then continue; fi
        TOTAL_FOUND=$((TOTAL_FOUND + 1))

        # Check if instance is already terminated or gone
        state=$(aws ec2 describe-instances \
            --instance-ids "$instance_id" \
            --query "Reservations[].Instances[].State.Name" --output text --region "$REGION" 2>/dev/null)
        if [ $? -ne 0 ] || [ -z "$state" ] || [ "$state" = "None" ]; then
            print_gray "  Instance $instance_id no longer exists, skipping"
            continue
        fi
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

    # Wait for all EC2 instances to fully terminate before proceeding
    if [ ${#TERMINATED_EC2[@]} -gt 0 ]; then
        print_gray "  Waiting for EC2 instances to fully terminate..."
        for inst_id in "${TERMINATED_EC2[@]}"; do
            max_wait=180
            elapsed=0
            while [ $elapsed -lt $max_wait ]; do
                inst_state=$(aws ec2 describe-instances --instance-ids "$inst_id" \
                    --query "Reservations[].Instances[].State.Name" --output text --region "$REGION" 2>/dev/null)
                if [ "$inst_state" = "terminated" ] || [ -z "$inst_state" ]; then break; fi
                sleep 15
                elapsed=$((elapsed + 15))
            done
        done
        print_green "  All EC2 instances terminated"
    fi
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
        vol_id=$(echo "$arn" | grep -oP 'vol-[a-f0-9]+')
        if [ -z "$vol_id" ]; then continue; fi
        TOTAL_FOUND=$((TOTAL_FOUND + 1))

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
# 7. Delete NAT Gateways (must complete before EIPs and subnets)
# ---------------------------------------------------------------------------
print_magenta "--- NAT Gateways ---"

print_yellow "Finding NAT Gateways tagged goat-demo=true..."
NAT_GW_IDS=$(aws ec2 describe-nat-gateways \
    --filter "Name=tag:goat-demo,Values=true" "Name=state,Values=available,pending" \
    --query "NatGateways[].NatGatewayId" --output text --region "$REGION" 2>/dev/null)

if [ -n "$NAT_GW_IDS" ] && [ "$NAT_GW_IDS" != "None" ]; then
    for nat_id in $NAT_GW_IDS; do
        if [ -z "$nat_id" ]; then continue; fi
        TOTAL_FOUND=$((TOTAL_FOUND + 1))
        print_yellow "  Deleting NAT Gateway: $nat_id..."
        aws ec2 delete-nat-gateway --nat-gateway-id "$nat_id" --region "$REGION" 2>/dev/null >/dev/null
        if [ $? -ne 0 ]; then
            print_red "  WARNING: Failed to delete NAT Gateway $nat_id"
            WARNINGS+=("NAT Gateway delete failed: $nat_id")
        else
            print_green "  Deleted: $nat_id (waiting for completion...)"
        fi
    done
    # Wait for all NAT Gateways to finish deleting before proceeding to EIPs/subnets
    print_gray "  Waiting for NAT Gateways to fully delete..."
    for nat_id in $NAT_GW_IDS; do
        if [ -z "$nat_id" ]; then continue; fi
        max_wait=120
        elapsed=0
        while [ $elapsed -lt $max_wait ]; do
            nat_state=$(aws ec2 describe-nat-gateways --nat-gateway-ids "$nat_id" \
                --query "NatGateways[0].State" --output text --region "$REGION" 2>/dev/null)
            if [ "$nat_state" = "deleted" ] || [ "$nat_state" = "None" ] || [ -z "$nat_state" ]; then break; fi
            sleep 10
            elapsed=$((elapsed + 10))
        done
    done
    print_green "  NAT Gateways deleted"
else
    print_gray "  No NAT Gateways found"
fi

echo ""

# ---------------------------------------------------------------------------
# 8. Release Elastic IPs
# ---------------------------------------------------------------------------
print_magenta "--- Elastic IPs ---"

print_yellow "Finding Elastic IPs tagged goat-demo=true..."
EIP_ARNS=$(aws resourcegroupstaggingapi get-resources \
    --tag-filters Key=goat-demo,Values=true \
    --resource-type-filters ec2:elastic-ip \
    --query "ResourceTagMappingList[].ResourceARN" --output text --region "$REGION" 2>/dev/null)

if [ -n "$EIP_ARNS" ] && [ "$EIP_ARNS" != "None" ]; then
    for arn in $EIP_ARNS; do
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
# 9. Delete DynamoDB tables
# ---------------------------------------------------------------------------
print_magenta "--- DynamoDB Tables ---"

print_yellow "Finding DynamoDB tables tagged goat-demo=true..."
DDB_ARNS=$(aws resourcegroupstaggingapi get-resources \
    --tag-filters Key=goat-demo,Values=true \
    --resource-type-filters dynamodb:table \
    --query "ResourceTagMappingList[].ResourceARN" --output text --region "$REGION" 2>/dev/null)

if [ -n "$DDB_ARNS" ] && [ "$DDB_ARNS" != "None" ]; then
    for arn in $DDB_ARNS; do
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
# 10. Delete subnets
# ---------------------------------------------------------------------------

# IMPORTANT: Initiate TGW attachment and NFW deletion now (they hold ENIs in
# subnets). Section 12 will wait for completion and handle cleanup of the
# TGW itself, route tables, rule groups, etc.
print_magenta "--- Pre-Subnet: Initiating TGW/NFW Cleanup ---"

TGW_ATTACHMENTS=$(aws ec2 describe-transit-gateway-attachments \
    --filters "Name=tag:goat-demo,Values=true" "Name=state,Values=available" \
    --query "TransitGatewayAttachments[].TransitGatewayAttachmentId" --output text --region "$REGION" 2>/dev/null)
if [ -n "$TGW_ATTACHMENTS" ] && [ "$TGW_ATTACHMENTS" != "None" ]; then
    for att_id in $TGW_ATTACHMENTS; do
        if [ -z "$att_id" ]; then continue; fi
        print_yellow "  Initiating TGW attachment deletion: $att_id..."
        aws ec2 delete-transit-gateway-vpc-attachment --transit-gateway-attachment-id "$att_id" --region "$REGION" 2>/dev/null >/dev/null
    done
    print_gray "  TGW attachment deletions initiated (section 12 will wait)"
else
    print_gray "  No active TGW attachments found"
fi

# Initiate NFW deletion (don't wait — takes 5-10 min; section 12 waits)
NFW_LIST=$(aws network-firewall list-firewalls --query "Firewalls[?contains(FirewallName,'goat-demo')].FirewallName" --output text --region "$REGION" 2>/dev/null)
if [ -n "$NFW_LIST" ] && [ "$NFW_LIST" != "None" ]; then
    for nfw_name in $NFW_LIST; do
        if [ -z "$nfw_name" ]; then continue; fi
        print_yellow "  Initiating Network Firewall deletion: $nfw_name..."
        aws network-firewall delete-firewall --firewall-name "$nfw_name" --region "$REGION" 2>/dev/null >/dev/null
    done
    print_gray "  NFW deletion initiated (section 12 will wait)"
fi

echo ""

print_magenta "--- Subnets ---"

print_yellow "Finding subnets tagged goat-scenario..."
# Only delete subnets tagged with goat-scenario (scenario-owned), NOT CDK-owned subnets.
# CDK-owned subnets (CollectorSubnet) are in the goat-demo-vpc and would break the collector if deleted.
SUBNET_IDS=$(aws ec2 describe-subnets \
    --filters "Name=tag:goat-scenario,Values=a,b,tls-fragmentation" \
    --query "Subnets[].SubnetId" --output text --region "$REGION" 2>/dev/null)

if [ -n "$SUBNET_IDS" ] && [ "$SUBNET_IDS" != "None" ]; then
    for subnet_id in $SUBNET_IDS; do
        if [ -z "$subnet_id" ]; then continue; fi
        TOTAL_FOUND=$((TOTAL_FOUND + 1))

        print_yellow "  Deleting subnet: $subnet_id..."
        subnet_deleted=false
        for attempt in 1 2 3; do
            RESULT=$(aws ec2 delete-subnet --subnet-id "$subnet_id" --region "$REGION" 2>&1)
            if [ $? -eq 0 ]; then
                print_green "  Deleted: $subnet_id"
                DELETED_SUBNETS+=("$subnet_id")
                subnet_deleted=true
                break
            fi
            if echo "$RESULT" | grep -qi "not found"; then
                print_gray "  Subnet $subnet_id already deleted, skipping"
                subnet_deleted=true
                break
            elif echo "$RESULT" | grep -qi "DependencyViolation"; then
                if [ $attempt -lt 3 ]; then
                    print_gray "  Subnet $subnet_id has dependencies, waiting 20s... (attempt $attempt/3)"
                    sleep 20
                else
                    print_yellow "  Subnet $subnet_id still has dependencies after retries. Skipping."
                    WARNINGS+=("Subnet dependency: $subnet_id")
                fi
            else
                print_red "  WARNING: Failed to delete subnet $subnet_id: $RESULT"
                WARNINGS+=("Subnet delete failed: $subnet_id")
                break
            fi
        done
    done
else
    print_gray "  No subnets found"
fi

echo ""

# ---------------------------------------------------------------------------
# 11. Delete VPCs
# ---------------------------------------------------------------------------
print_magenta "--- VPCs ---"

print_yellow "Finding VPCs tagged goat-demo=true..."
VPC_IDS=$(aws ec2 describe-vpcs \
    --filters "Name=tag:goat-demo,Values=true" \
    --query "Vpcs[].VpcId" --output text --region "$REGION" 2>/dev/null)

if [ -n "$VPC_IDS" ] && [ "$VPC_IDS" != "None" ]; then
    for vpc_id in $VPC_IDS; do
        if [ -z "$vpc_id" ]; then continue; fi

        # SKIP the shared GOAT VPC (owned by CDK, name=goat-demo-vpc).
        # Deleting it or detaching its IGW breaks the collector.
        vpc_name=$(aws ec2 describe-vpcs --vpc-ids "$vpc_id" \
            --query "Vpcs[0].Tags[?Key=='Name']|[0].Value" --output text --region "$REGION" 2>/dev/null)
        if [ "$vpc_name" = "goat-demo-vpc" ]; then
            print_gray "  Skipping shared GOAT VPC $vpc_id (owned by CDK, not scenario)"
            continue
        fi

        TOTAL_FOUND=$((TOTAL_FOUND + 1))

        # Detach and delete any IGWs attached to this VPC
        vpc_igw_ids=$(aws ec2 describe-internet-gateways \
            --filters "Name=attachment.vpc-id,Values=$vpc_id" \
            --query "InternetGateways[].InternetGatewayId" --output text --region "$REGION" 2>/dev/null)
        if [ -n "$vpc_igw_ids" ] && [ "$vpc_igw_ids" != "None" ]; then
            for igw_id in $vpc_igw_ids; do
                if [ -z "$igw_id" ]; then continue; fi
                print_gray "  Detaching IGW $igw_id from VPC $vpc_id..."
                aws ec2 detach-internet-gateway --internet-gateway-id "$igw_id" --vpc-id "$vpc_id" --region "$REGION" 2>/dev/null
                aws ec2 delete-internet-gateway --internet-gateway-id "$igw_id" --region "$REGION" 2>/dev/null
            done
        fi

        # Delete any remaining subnets in this VPC
        vpc_subnets=$(aws ec2 describe-subnets \
            --filters "Name=vpc-id,Values=$vpc_id" \
            --query "Subnets[].SubnetId" --output text --region "$REGION" 2>/dev/null)
        if [ -n "$vpc_subnets" ] && [ "$vpc_subnets" != "None" ]; then
            for sub_id in $vpc_subnets; do
                if [ -z "$sub_id" ]; then continue; fi
                aws ec2 delete-subnet --subnet-id "$sub_id" --region "$REGION" 2>/dev/null
            done
        fi

        # Delete non-default route tables
        vpc_rts=$(aws ec2 describe-route-tables \
            --filters "Name=vpc-id,Values=$vpc_id" \
            --query "RouteTables[?Associations[0].Main!=\`true\`].RouteTableId" --output text --region "$REGION" 2>/dev/null)
        if [ -n "$vpc_rts" ] && [ "$vpc_rts" != "None" ]; then
            for rt_id in $vpc_rts; do
                if [ -z "$rt_id" ]; then continue; fi
                # Disassociate first
                assoc_ids=$(aws ec2 describe-route-tables --route-table-ids "$rt_id" \
                    --query "RouteTables[0].Associations[?!Main].RouteTableAssociationId" --output text --region "$REGION" 2>/dev/null)
                if [ -n "$assoc_ids" ] && [ "$assoc_ids" != "None" ]; then
                    for assoc_id in $assoc_ids; do
                        if [ -n "$assoc_id" ]; then
                            aws ec2 disassociate-route-table --association-id "$assoc_id" --region "$REGION" 2>/dev/null
                        fi
                    done
                fi
                aws ec2 delete-route-table --route-table-id "$rt_id" --region "$REGION" 2>/dev/null
            done
        fi

        print_yellow "  Deleting VPC: $vpc_id..."
        vpc_deleted=false
        for attempt in 1 2 3; do
            RESULT=$(aws ec2 delete-vpc --vpc-id "$vpc_id" --region "$REGION" 2>&1)
            if [ $? -eq 0 ]; then
                print_green "  Deleted: $vpc_id"
                DELETED_VPCS+=("$vpc_id")
                vpc_deleted=true
                break
            fi
            if echo "$RESULT" | grep -qi "not found"; then
                print_gray "  VPC $vpc_id already deleted, skipping"
                vpc_deleted=true
                break
            elif echo "$RESULT" | grep -qi "DependencyViolation"; then
                if [ $attempt -lt 3 ]; then
                    print_gray "  VPC $vpc_id has dependencies, waiting 30s... (attempt $attempt/3)"
                    sleep 30
                else
                    print_yellow "  VPC $vpc_id still has dependencies after retries. Skipping."
                    WARNINGS+=("VPC dependency: $vpc_id")
                fi
            else
                print_red "  WARNING: Failed to delete VPC $vpc_id: $RESULT"
                WARNINGS+=("VPC delete failed: $vpc_id")
                break
            fi
        done
    done
else
    print_gray "  No VPCs found"
fi

echo ""

# ---------------------------------------------------------------------------
# 12. TLS Fragmentation Scenario Cleanup (goat-scenario=tls-fragmentation)
# ---------------------------------------------------------------------------
print_magenta "--- TLS Fragmentation Scenario (goat-scenario=tls-fragmentation) ---"
echo ""

# Check for any TLS-tagged subnets to determine if cleanup is needed.
TLS_SUBNET_CHECK=$(aws ec2 describe-subnets \
    --filters "Name=tag:goat-scenario,Values=tls-fragmentation" \
    --query "Subnets[0].SubnetId" --output text --region "$REGION" 2>/dev/null)

# Also check for EKS clusters tagged for TLS scenario
TLS_EKS_CHECK=""
EKS_CLUSTERS=$(aws eks list-clusters --query "clusters" --output text --region "$REGION" 2>/dev/null)
if [ -n "$EKS_CLUSTERS" ] && [ "$EKS_CLUSTERS" != "None" ]; then
    for cluster in $EKS_CLUSTERS; do
        CLUSTER_TAGS=$(aws eks describe-cluster --name "$cluster" --query "cluster.tags" --output json --region "$REGION" 2>/dev/null)
        if echo "$CLUSTER_TAGS" | grep -q '"goat-scenario".*"tls-fragmentation"' 2>/dev/null; then
            TLS_EKS_CHECK="$cluster"
            break
        fi
    done
fi

# Also check for a Transit Gateway tagged for the TLS scenario
TLS_TGW_CHECK=$(aws ec2 describe-transit-gateways \
    --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=state,Values=available,pending,modifying" \
    --query "TransitGateways[0].TransitGatewayId" --output text --region "$REGION" 2>/dev/null)

HAS_TLS_RESOURCES=false
if { [ -n "$TLS_SUBNET_CHECK" ] && [ "$TLS_SUBNET_CHECK" != "None" ]; } || \
   [ -n "$TLS_EKS_CHECK" ] || \
   { [ -n "$TLS_TGW_CHECK" ] && [ "$TLS_TGW_CHECK" != "None" ]; }; then
    HAS_TLS_RESOURCES=true
fi

if [ "$HAS_TLS_RESOURCES" = true ]; then
    TOTAL_FOUND=$((TOTAL_FOUND + 1))

    # 12a. Delete Kubernetes test pod and EKS resources
    print_yellow "Finding EKS clusters tagged goat-scenario=tls-fragmentation..."
    EKS_CLUSTER_NAME="$TLS_EKS_CHECK"

    if [ -n "$EKS_CLUSTER_NAME" ]; then
        TOTAL_FOUND=$((TOTAL_FOUND + 1))

        # Delete test pod (best-effort via kubectl if available)
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

    # 12a2. Delete Transit Gateway attachments, route table, and the TGW itself.
    # Must happen BEFORE subnet deletion (attachments hold ENIs in the TGW subnets)
    # and before VPC deletion.
    print_yellow "  Finding Transit Gateway tagged goat-scenario=tls-fragmentation..."
    TLS_TGW_ID=$(aws ec2 describe-transit-gateways \
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=state,Values=available,pending,modifying" \
        --query "TransitGateways[0].TransitGatewayId" --output text --region "$REGION" 2>/dev/null)

    if [ -n "$TLS_TGW_ID" ] && [ "$TLS_TGW_ID" != "None" ]; then
        TOTAL_FOUND=$((TOTAL_FOUND + 1))

        # Delete VPC attachments first and wait for them to be gone
        tgw_attach_ids=$(aws ec2 describe-transit-gateway-attachments \
            --filters "Name=transit-gateway-id,Values=$TLS_TGW_ID" "Name=state,Values=available,pending,modifying" \
            --query "TransitGatewayAttachments[].TransitGatewayAttachmentId" --output text --region "$REGION" 2>/dev/null)
        if [ -n "$tgw_attach_ids" ] && [ "$tgw_attach_ids" != "None" ]; then
            for att_id in $tgw_attach_ids; do
                if [ -z "$att_id" ]; then continue; fi
                print_yellow "  Deleting TGW attachment: $att_id..."
                aws ec2 delete-transit-gateway-vpc-attachment --transit-gateway-attachment-id "$att_id" --region "$REGION" 2>/dev/null >/dev/null
                COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
            done
            # Wait for attachments to be deleted (they block subnet/VPC deletion)
            print_gray "  Waiting for TGW attachments to delete..."
            max_wait=300
            elapsed=0
            while [ $elapsed -lt $max_wait ]; do
                remaining=$(aws ec2 describe-transit-gateway-attachments \
                    --filters "Name=transit-gateway-id,Values=$TLS_TGW_ID" "Name=state,Values=available,pending,modifying,deleting" \
                    --query "length(TransitGatewayAttachments)" --output text --region "$REGION" 2>/dev/null)
                if [ "$remaining" = "0" ] || [ -z "$remaining" ] || [ "$remaining" = "None" ]; then break; fi
                sleep 15
                elapsed=$((elapsed + 15))
            done
        fi

        # Delete the custom TGW route table (default RT cannot be deleted; ignore errors)
        tgw_rt_ids=$(aws ec2 describe-transit-gateway-route-tables \
            --filters "Name=transit-gateway-id,Values=$TLS_TGW_ID" "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=state,Values=available,pending" \
            --query "TransitGatewayRouteTables[].TransitGatewayRouteTableId" --output text --region "$REGION" 2>/dev/null)
        if [ -n "$tgw_rt_ids" ] && [ "$tgw_rt_ids" != "None" ]; then
            for rt_id in $tgw_rt_ids; do
                if [ -z "$rt_id" ]; then continue; fi
                print_yellow "  Deleting TGW route table: $rt_id..."
                aws ec2 delete-transit-gateway-route-table --transit-gateway-route-table-id "$rt_id" --region "$REGION" 2>/dev/null >/dev/null
                COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
            done
            sleep 10
        fi

        # Delete the Transit Gateway
        print_yellow "  Deleting Transit Gateway: $TLS_TGW_ID..."
        RESULT=$(aws ec2 delete-transit-gateway --transit-gateway-id "$TLS_TGW_ID" --region "$REGION" 2>&1)
        if [ $? -ne 0 ]; then
            if echo "$RESULT" | grep -qi "not found\|InvalidTransitGatewayID"; then
                print_gray "  Transit Gateway already deleted, skipping"
            else
                print_red "  WARNING: Failed to delete Transit Gateway $TLS_TGW_ID: $RESULT"
                WARNINGS+=("TGW delete failed: $TLS_TGW_ID")
                HAS_ERRORS=true
            fi
        else
            print_green "  Transit Gateway deletion initiated: $TLS_TGW_ID"
            COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
        fi
    else
        print_gray "  No Transit Gateway found for tls-fragmentation scenario"
    fi

    # 12b. Delete Network Firewall
    print_yellow "  Finding Network Firewall resources..."
    NFW_NAME="goat-demo-tls-nfw"
    NFW_STATUS=$(aws network-firewall describe-firewall --firewall-name "$NFW_NAME" \
        --query "Firewall.FirewallArn" --output text --region "$REGION" 2>/dev/null)

    if [ -n "$NFW_STATUS" ] && [ "$NFW_STATUS" != "None" ]; then
        TOTAL_FOUND=$((TOTAL_FOUND + 1))

        # A firewall with a logging configuration cannot be deleted. Remove log
        # destinations one at a time (the API rejects removing multiple at once),
        # then delete the firewall.
        LOG_CONFIG=$(aws network-firewall describe-logging-configuration --firewall-name "$NFW_NAME" \
            --query "LoggingConfiguration.LogDestinationConfigs[].LogType" --output text --region "$REGION" 2>/dev/null)
        if [ -n "$LOG_CONFIG" ] && [ "$LOG_CONFIG" != "None" ]; then
            print_yellow "  Clearing firewall logging configuration before delete..."
            FLOW_GROUP="/aws/network-firewall/goat-demo-tls-flow"
            # Step 1: keep only FLOW (removes ALERT if present)
            KEEP_FLOW="{\"LogDestinationConfigs\":[{\"LogType\":\"FLOW\",\"LogDestinationType\":\"CloudWatchLogs\",\"LogDestination\":{\"logGroup\":\"$FLOW_GROUP\"}}]}"
            TMPFILE1=$(mktemp)
            echo "$KEEP_FLOW" > "$TMPFILE1"
            aws network-firewall update-logging-configuration --firewall-name "$NFW_NAME" --logging-configuration "file://$TMPFILE1" --region "$REGION" 2>/dev/null >/dev/null
            rm -f "$TMPFILE1"
            # Step 2: remove the remaining FLOW destination (empty config)
            TMPFILE2=$(mktemp)
            echo '{"LogDestinationConfigs":[]}' > "$TMPFILE2"
            aws network-firewall update-logging-configuration --firewall-name "$NFW_NAME" --logging-configuration "file://$TMPFILE2" --region "$REGION" 2>/dev/null >/dev/null
            rm -f "$TMPFILE2"
        fi

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
                if [ -z "$FW_CHECK" ] || [ "$FW_CHECK" = "None" ]; then
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

    # Delete firewall rule group (may need retries if policy deletion hasn't propagated)
    NFW_RULE_GROUP_NAME="goat-demo-tls-rules"
    RG_CHECK=$(aws network-firewall describe-rule-group --rule-group-name "$NFW_RULE_GROUP_NAME" --type STATEFUL \
        --query "RuleGroupResponse.RuleGroupArn" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$RG_CHECK" ] && [ "$RG_CHECK" != "None" ]; then
        TOTAL_FOUND=$((TOTAL_FOUND + 1))
        print_yellow "  Deleting firewall rule group: $NFW_RULE_GROUP_NAME..."
        rg_deleted=false
        for attempt in 1 2 3 4 5; do
            RESULT=$(aws network-firewall delete-rule-group --rule-group-name "$NFW_RULE_GROUP_NAME" --type STATEFUL --region "$REGION" 2>&1)
            if [ $? -eq 0 ]; then
                print_green "  Deleted firewall rule group: $NFW_RULE_GROUP_NAME"
                COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
                rg_deleted=true
                break
            fi
            if echo "$RESULT" | grep -qi "ResourceNotFoundException\|not found"; then
                print_gray "  Rule group already deleted, skipping"
                rg_deleted=true
                break
            elif echo "$RESULT" | grep -qi "InvalidOperationException\|still in use"; then
                if [ $attempt -lt 5 ]; then
                    print_gray "  Rule group still in use, waiting 15s... (attempt $attempt/5)"
                    sleep 15
                else
                    print_red "  WARNING: Failed to delete rule group after retries: $RESULT"
                    WARNINGS+=("Firewall rule group delete failed: $NFW_RULE_GROUP_NAME")
                    HAS_ERRORS=true
                fi
            else
                print_red "  WARNING: Failed to delete rule group: $RESULT"
                WARNINGS+=("Firewall rule group delete failed: $NFW_RULE_GROUP_NAME")
                HAS_ERRORS=true
                break
            fi
        done
    fi

    # 12c. Delete NAT Gateway
    print_yellow "  Finding NAT Gateways tagged goat-scenario=tls-fragmentation..."
    TLS_NAT_GW_IDS=$(aws ec2 describe-nat-gateways \
        --filter "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=state,Values=available,pending" \
        --query "NatGateways[].NatGatewayId" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$TLS_NAT_GW_IDS" ] && [ "$TLS_NAT_GW_IDS" != "None" ]; then
        for nat_id in $TLS_NAT_GW_IDS; do
            if [ -z "$nat_id" ]; then continue; fi
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
                # Wait for NAT Gateway deletion
                for i in $(seq 1 30); do
                    sleep 10
                    nat_state=$(aws ec2 describe-nat-gateways --nat-gateway-ids "$nat_id" \
                        --query "NatGateways[0].State" --output text --region "$REGION" 2>/dev/null)
                    if [ "$nat_state" = "deleted" ] || [ -z "$nat_state" ] || [ "$nat_state" = "None" ]; then break; fi
                done
                COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
            fi
        done
    else
        print_gray "  No NAT Gateways found"
    fi

    # 12d. Detach and delete Internet Gateway
    print_yellow "  Finding Internet Gateways tagged goat-scenario=tls-fragmentation..."
    TLS_IGW_IDS=$(aws ec2 describe-internet-gateways \
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" \
        --query "InternetGateways[].InternetGatewayId" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$TLS_IGW_IDS" ] && [ "$TLS_IGW_IDS" != "None" ]; then
        for igw_id in $TLS_IGW_IDS; do
            if [ -z "$igw_id" ]; then continue; fi
            TOTAL_FOUND=$((TOTAL_FOUND + 1))
            # Detach from VPC first — look up the attached VPC from the IGW itself
            igw_vpc_id=$(aws ec2 describe-internet-gateways --internet-gateway-ids "$igw_id" \
                --query "InternetGateways[0].Attachments[0].VpcId" --output text --region "$REGION" 2>/dev/null)
            if [ -n "$igw_vpc_id" ] && [ "$igw_vpc_id" != "None" ]; then
                aws ec2 detach-internet-gateway --internet-gateway-id "$igw_id" --vpc-id "$igw_vpc_id" --region "$REGION" 2>/dev/null
            fi
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

    # 12e. Delete subnets tagged goat-scenario=tls-fragmentation
    print_yellow "  Finding subnets tagged goat-scenario=tls-fragmentation..."
    TLS_SUBNET_IDS=$(aws ec2 describe-subnets \
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" \
        --query "Subnets[].SubnetId" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$TLS_SUBNET_IDS" ] && [ "$TLS_SUBNET_IDS" != "None" ]; then
        for subnet_id in $TLS_SUBNET_IDS; do
            if [ -z "$subnet_id" ]; then continue; fi
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

    # 12f. Delete route tables tagged goat-scenario=tls-fragmentation
    print_yellow "  Finding route tables tagged goat-scenario=tls-fragmentation..."
    TLS_RT_IDS=$(aws ec2 describe-route-tables \
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" \
        --query "RouteTables[].RouteTableId" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$TLS_RT_IDS" ] && [ "$TLS_RT_IDS" != "None" ]; then
        for rt_id in $TLS_RT_IDS; do
            if [ -z "$rt_id" ]; then continue; fi
            TOTAL_FOUND=$((TOTAL_FOUND + 1))
            # Disassociate any subnet associations first (skip main)
            assoc_ids=$(aws ec2 describe-route-tables --route-table-ids "$rt_id" \
                --query "RouteTables[0].Associations[?!Main].RouteTableAssociationId" --output text --region "$REGION" 2>/dev/null)
            if [ -n "$assoc_ids" ] && [ "$assoc_ids" != "None" ]; then
                for assoc_id in $assoc_ids; do
                    if [ -n "$assoc_id" ]; then
                        aws ec2 disassociate-route-table --association-id "$assoc_id" --region "$REGION" 2>/dev/null
                    fi
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

    # 12g. Delete security groups tagged goat-scenario=tls-fragmentation (non-default only)
    print_yellow "  Finding security groups tagged goat-scenario=tls-fragmentation..."
    TLS_SG_IDS=$(aws ec2 describe-security-groups \
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" \
        --query "SecurityGroups[?GroupName!='default'].GroupId" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$TLS_SG_IDS" ] && [ "$TLS_SG_IDS" != "None" ]; then
        for sg_id in $TLS_SG_IDS; do
            if [ -z "$sg_id" ]; then continue; fi
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
        print_gray "  No TLS-tagged security groups found"
    fi

    # Release any EIPs tagged for TLS scenario
    print_yellow "  Finding Elastic IPs tagged goat-scenario=tls-fragmentation..."
    TLS_EIP_ARNS=$(aws resourcegroupstaggingapi get-resources \
        --tag-filters Key=goat-scenario,Values=tls-fragmentation \
        --resource-type-filters ec2:elastic-ip \
        --query "ResourceTagMappingList[].ResourceARN" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$TLS_EIP_ARNS" ] && [ "$TLS_EIP_ARNS" != "None" ]; then
        for arn in $TLS_EIP_ARNS; do
            if [ -z "$arn" ]; then continue; fi
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

    # 12g2. Delete firewall CloudWatch log groups and scenario IAM roles.
    print_yellow "  Deleting firewall CloudWatch log groups..."
    for lg in "/aws/network-firewall/goat-demo-tls-flow" "/aws/network-firewall/goat-demo-tls-alert"; do
        lg_check=$(aws logs describe-log-groups --log-group-name-prefix "$lg" \
            --query "logGroups[0].logGroupName" --output text --region "$REGION" 2>/dev/null)
        if [ -n "$lg_check" ] && [ "$lg_check" != "None" ]; then
            aws logs delete-log-group --log-group-name "$lg" --region "$REGION" 2>/dev/null
            print_green "  Deleted log group: $lg"
            COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
        fi
    done

    print_yellow "  Deleting scenario IAM roles..."
    for role_name in "goat-demo-tls-eks-role" "goat-demo-tls-node-role" "goat-demo-tls-ssm-role"; do
        role_check=$(aws iam get-role --role-name "$role_name" --query "Role.RoleName" --output text 2>/dev/null)
        if [ -n "$role_check" ] && [ "$role_check" != "None" ] && [ $? -eq 0 ]; then
            # Remove from instance profile first (SSM role)
            profile_name=$(echo "$role_name" | sed 's/-role$/-profile/')
            prof_check=$(aws iam get-instance-profile --instance-profile-name "$profile_name" \
                --query "InstanceProfile.InstanceProfileName" --output text 2>/dev/null)
            if [ -n "$prof_check" ] && [ "$prof_check" != "None" ] && [ $? -eq 0 ]; then
                aws iam remove-role-from-instance-profile --instance-profile-name "$profile_name" --role-name "$role_name" 2>/dev/null
                aws iam delete-instance-profile --instance-profile-name "$profile_name" 2>/dev/null
                print_green "  Deleted instance profile: $profile_name"
            fi
            # Detach managed policies
            attached=$(aws iam list-attached-role-policies --role-name "$role_name" \
                --query "AttachedPolicies[].PolicyArn" --output text 2>/dev/null)
            if [ -n "$attached" ] && [ "$attached" != "None" ]; then
                for p in $attached; do
                    if [ -n "$p" ]; then
                        aws iam detach-role-policy --role-name "$role_name" --policy-arn "$p" 2>/dev/null
                    fi
                done
            fi
            # Delete inline policies
            inline=$(aws iam list-role-policies --role-name "$role_name" \
                --query "PolicyNames" --output text 2>/dev/null)
            if [ -n "$inline" ] && [ "$inline" != "None" ]; then
                for ip in $inline; do
                    if [ -n "$ip" ]; then
                        aws iam delete-role-policy --role-name "$role_name" --policy-name "$ip" 2>/dev/null
                    fi
                done
            fi
            aws iam delete-role --role-name "$role_name" 2>/dev/null
            print_green "  Deleted IAM role: $role_name"
            COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
        fi
    done

    # 12h. Delete the dedicated inspection VPC (tagged goat-scenario=tls-fragmentation).
    # Its subnets, route tables, security groups, IGW, and NAT were removed above by the
    # tag-based steps; only the VPC shell remains. The shared spoke VPC (goat-demo-vpc) is
    # handled by section 11 and is NOT deleted here.
    print_yellow "  Finding inspection VPC tagged goat-scenario=tls-fragmentation..."
    INSP_VPC_IDS=$(aws ec2 describe-vpcs \
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-inspection-vpc" \
        --query "Vpcs[].VpcId" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$INSP_VPC_IDS" ] && [ "$INSP_VPC_IDS" != "None" ]; then
        for insp_vpc in $INSP_VPC_IDS; do
            if [ -z "$insp_vpc" ]; then continue; fi
            TOTAL_FOUND=$((TOTAL_FOUND + 1))

            # Detach + delete any IGWs still attached to this VPC
            vpc_igws=$(aws ec2 describe-internet-gateways \
                --filters "Name=attachment.vpc-id,Values=$insp_vpc" \
                --query "InternetGateways[].InternetGatewayId" --output text --region "$REGION" 2>/dev/null)
            if [ -n "$vpc_igws" ] && [ "$vpc_igws" != "None" ]; then
                for igw in $vpc_igws; do
                    if [ -z "$igw" ]; then continue; fi
                    aws ec2 detach-internet-gateway --internet-gateway-id "$igw" --vpc-id "$insp_vpc" --region "$REGION" 2>/dev/null
                    aws ec2 delete-internet-gateway --internet-gateway-id "$igw" --region "$REGION" 2>/dev/null
                done
            fi

            # Delete any leftover subnets in the inspection VPC
            vpc_subnets=$(aws ec2 describe-subnets \
                --filters "Name=vpc-id,Values=$insp_vpc" \
                --query "Subnets[].SubnetId" --output text --region "$REGION" 2>/dev/null)
            if [ -n "$vpc_subnets" ] && [ "$vpc_subnets" != "None" ]; then
                for sn in $vpc_subnets; do
                    if [ -n "$sn" ]; then aws ec2 delete-subnet --subnet-id "$sn" --region "$REGION" 2>/dev/null; fi
                done
            fi

            # Delete non-main route tables in the inspection VPC
            vpc_rts=$(aws ec2 describe-route-tables \
                --filters "Name=vpc-id,Values=$insp_vpc" \
                --query "RouteTables[?length(Associations[?Main]) == \`0\`].RouteTableId" --output text --region "$REGION" 2>/dev/null)
            if [ -n "$vpc_rts" ] && [ "$vpc_rts" != "None" ]; then
                for rt in $vpc_rts; do
                    if [ -n "$rt" ]; then aws ec2 delete-route-table --route-table-id "$rt" --region "$REGION" 2>/dev/null; fi
                done
            fi

            print_yellow "  Deleting inspection VPC: $insp_vpc..."
            RESULT=$(aws ec2 delete-vpc --vpc-id "$insp_vpc" --region "$REGION" 2>&1)
            if [ $? -ne 0 ]; then
                if echo "$RESULT" | grep -qi "not found\|InvalidVpcID"; then
                    print_gray "  Inspection VPC already deleted, skipping"
                else
                    print_red "  WARNING: Failed to delete inspection VPC $insp_vpc: $RESULT"
                    print_gray "  (TGW attachment or firewall endpoint may still be detaching - re-run cleanup)"
                    WARNINGS+=("Inspection VPC delete failed: $insp_vpc")
                    HAS_ERRORS=true
                fi
            else
                print_green "  Deleted inspection VPC: $insp_vpc"
                COUNT_TLS_FRAG=$((COUNT_TLS_FRAG + 1))
            fi
        done
    else
        print_gray "  No inspection VPC found"
    fi

else
    print_gray "  No TLS Fragmentation Scenario resources found"
fi

echo ""

# ---------------------------------------------------------------------------
# 13. Summary
# ---------------------------------------------------------------------------

# Count remaining resources per scenario
COUNT_SCENARIO_A=$(aws resourcegroupstaggingapi get-resources \
    --tag-filters Key=goat-scenario,Values=a \
    --query "length(ResourceTagMappingList)" --output text --region "$REGION" 2>/dev/null)
if [ -z "$COUNT_SCENARIO_A" ]; then COUNT_SCENARIO_A="0"; fi
COUNT_SCENARIO_B=$(aws resourcegroupstaggingapi get-resources \
    --tag-filters Key=goat-scenario,Values=b \
    --query "length(ResourceTagMappingList)" --output text --region "$REGION" 2>/dev/null)
if [ -z "$COUNT_SCENARIO_B" ]; then COUNT_SCENARIO_B="0"; fi

if [ $TOTAL_FOUND -eq 0 ]; then
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
    if [ "$HAS_ERRORS" = true ]; then exit 1; fi
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
    print_cyan "  Terminated EC2:      ${TERMINATED_EC2[*]}"
fi
if [ ${#DELETED_RDS[@]} -gt 0 ]; then
    print_cyan "  Deleted RDS:         ${DELETED_RDS[*]}"
fi
if [ ${#DELETED_DB_SUBNET_GROUPS[@]} -gt 0 ]; then
    print_cyan "  Deleted DB SubGrp:   ${DELETED_DB_SUBNET_GROUPS[*]}"
fi
if [ ${#DELETED_EBS[@]} -gt 0 ]; then
    print_cyan "  Deleted EBS:         ${DELETED_EBS[*]}"
fi
if [ ${#RELEASED_EIP[@]} -gt 0 ]; then
    print_cyan "  Released EIP:        ${RELEASED_EIP[*]}"
fi
if [ ${#DELETED_DDB[@]} -gt 0 ]; then
    print_cyan "  Deleted DynamoDB:    ${DELETED_DDB[*]}"
fi
if [ ${#DELETED_SUBNETS[@]} -gt 0 ]; then
    print_cyan "  Deleted Subnets:     ${DELETED_SUBNETS[*]}"
fi
if [ ${#DELETED_VPCS[@]} -gt 0 ]; then
    print_cyan "  Deleted VPCs:        ${DELETED_VPCS[*]}"
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

if [ "$HAS_ERRORS" = true ]; then exit 1; fi
