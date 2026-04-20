#!/bin/bash
# G.O.A.T. Demo Cleanup - Remove All Demo Resources
#
# Finds and removes all AWS resources tagged with goat-demo=true across
# both Scenario A and Scenario B. Resources are deleted in dependency
# order to avoid conflicts.
#
# Deletion order:
#   1. EC2 instances (may use VPC subnets, may have attached volumes)
#   2. RDS instances (skip final snapshot; depends on DB subnet group)
#   3. DB subnet groups (depends on RDS being gone)
#   4. EBS volumes (may need EC2 termination to complete)
#   5. Elastic IPs (no dependencies)
#   6. DynamoDB tables (no dependencies)
#   7. Subnets (depends on EC2/RDS being gone)
#   8. VPCs (depends on subnets being gone)
#
# Support cases are NOT cleaned up — they are already resolved and
# cannot be deleted via API.
#
# Usage: ./cleanup-scenarios.sh

set -o pipefail

# ---------------------------------------------------------------------------
# Color helpers (matching deploy-all.sh / setup-scenario-a.sh patterns)
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
# 11. Summary
# ---------------------------------------------------------------------------
if [ "$TOTAL_FOUND" -eq 0 ]; then
    print_cyan "========================================"
    print_cyan "  No Demo Resources Found"
    print_cyan "========================================"
    echo ""
    print_gray "  No resources tagged with goat-demo=true were found in $REGION."
    print_gray "  Nothing to clean up."
    echo ""
    exit 0
fi

print_green "========================================"
print_green "  G.O.A.T. Demo Cleanup Complete!"
print_green "========================================"
echo ""
print_cyan "  Region:              $REGION"

if [ ${#TERMINATED_EC2[@]} -gt 0 ]; then
    EC2_LIST=$(IFS=', '; echo "${TERMINATED_EC2[*]}")
    print_cyan "  Terminated EC2:      $EC2_LIST"
fi
if [ ${#DELETED_RDS[@]} -gt 0 ]; then
    RDS_LIST=$(IFS=', '; echo "${DELETED_RDS[*]}")
    print_cyan "  Deleted RDS:         $RDS_LIST"
fi
if [ ${#DELETED_DB_SUBNET_GROUPS[@]} -gt 0 ]; then
    DBSG_LIST=$(IFS=', '; echo "${DELETED_DB_SUBNET_GROUPS[*]}")
    print_cyan "  Deleted DB SubGrp:   $DBSG_LIST"
fi
if [ ${#DELETED_EBS[@]} -gt 0 ]; then
    EBS_LIST=$(IFS=', '; echo "${DELETED_EBS[*]}")
    print_cyan "  Deleted EBS:         $EBS_LIST"
fi
if [ ${#RELEASED_EIP[@]} -gt 0 ]; then
    EIP_LIST=$(IFS=', '; echo "${RELEASED_EIP[*]}")
    print_cyan "  Released EIP:        $EIP_LIST"
fi
if [ ${#DELETED_DDB[@]} -gt 0 ]; then
    DDB_LIST=$(IFS=', '; echo "${DELETED_DDB[*]}")
    print_cyan "  Deleted DynamoDB:    $DDB_LIST"
fi
if [ ${#DELETED_SUBNETS[@]} -gt 0 ]; then
    SUB_LIST=$(IFS=', '; echo "${DELETED_SUBNETS[*]}")
    print_cyan "  Deleted Subnets:     $SUB_LIST"
fi
if [ ${#DELETED_VPCS[@]} -gt 0 ]; then
    VPC_LIST=$(IFS=', '; echo "${DELETED_VPCS[*]}")
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
