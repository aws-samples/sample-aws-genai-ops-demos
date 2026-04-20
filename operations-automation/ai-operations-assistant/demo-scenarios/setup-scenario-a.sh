#!/bin/bash
# G.O.A.T. Demo Scenario A - Full Account Health Check
#
# Creates AWS resources that generate data across all five agent domains:
# - 2x EC2 t3.micro instances (Cost Explorer, CUR)
# - 1x RDS db.t3.micro MySQL instance (Cost Explorer, CUR)
# - 1x unattached EBS volume (Trusted Advisor finding)
# - 1x unassociated Elastic IP (Trusted Advisor finding)
# - 1x resolved Support case (Support Cases domain)
# - Dedicated VPC with 2 subnets and DB subnet group
#
# All resources are tagged with goat-demo=true for cleanup.
# Script is idempotent - safe to re-run after partial failures.
#
# Usage: ./setup-scenario-a.sh

set -o pipefail

# ---------------------------------------------------------------------------
# Color helpers (matching deploy-all.sh patterns)
# ---------------------------------------------------------------------------
print_cyan()    { echo -e "\033[0;36m$1\033[0m"; }
print_green()   { echo -e "\033[0;32m$1\033[0m"; }
print_yellow()  { echo -e "\033[0;33m$1\033[0m"; }
print_red()     { echo -e "\033[0;31m$1\033[0m"; }
print_gray()    { echo -e "\033[0;90m$1\033[0m"; }
print_magenta() { echo -e "\033[0;35m$1\033[0m"; }

# ---------------------------------------------------------------------------
# Track created/existing resources for summary
# ---------------------------------------------------------------------------
VPC_ID=""
SUBNET_1_ID=""
SUBNET_2_ID=""
DB_SUBNET_GROUP=""
INSTANCE_1_ID=""
INSTANCE_2_ID=""
RDS_ID=""
EBS_ID=""
EIP_ID=""
SUPPORT_CASE_ID=""
WARNINGS=()

# ---------------------------------------------------------------------------
# 1. Verify AWS credentials
# ---------------------------------------------------------------------------
print_cyan "=== G.O.A.T. Demo Scenario A - Full Account Health Check ==="
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
# 3. Create dedicated VPC (idempotent)
# ---------------------------------------------------------------------------
print_magenta "--- VPC and Networking ---"

print_yellow "Checking for existing goat-demo VPC..."
VPC_ID=$(aws ec2 describe-vpcs \
    --filters "Name=tag:goat-demo,Values=true" "Name=tag:Name,Values=goat-demo-vpc" \
    --query "Vpcs[0].VpcId" --output text --region "$REGION" 2>/dev/null)

if [ -n "$VPC_ID" ] && [ "$VPC_ID" != "None" ]; then
    print_green "  VPC already exists: $VPC_ID"
else
    print_yellow "Creating dedicated VPC (10.99.0.0/16)..."
    VPC_ID=$(aws ec2 create-vpc \
        --cidr-block 10.99.0.0/16 \
        --tag-specifications 'ResourceType=vpc,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=a},{Key=Name,Value=goat-demo-vpc},{Key=auto-delete,Value=no}]' \
        --query "Vpc.VpcId" --output text --region "$REGION" 2>&1)
    if [ $? -ne 0 ]; then
        print_red "  WARNING: Failed to create VPC: $VPC_ID"
        WARNINGS+=("VPC creation failed")
        VPC_ID=""
    else
        print_green "  Created VPC: $VPC_ID"

        # Enable DNS hostnames (required for RDS)
        aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-hostnames '{"Value":true}' --region "$REGION" 2>/dev/null
        print_gray "  Enabled DNS hostnames on VPC"
    fi
fi

# ---------------------------------------------------------------------------
# 4. Create subnets in two AZs (idempotent)
# ---------------------------------------------------------------------------
if [ -n "$VPC_ID" ] && [ "$VPC_ID" != "None" ]; then
    # Get availability zones
    AZ1=$(aws ec2 describe-availability-zones --query "AvailabilityZones[0].ZoneName" --output text --region "$REGION" 2>/dev/null)
    AZ2=$(aws ec2 describe-availability-zones --query "AvailabilityZones[1].ZoneName" --output text --region "$REGION" 2>/dev/null)

    # Subnet 1
    print_yellow "Checking for existing subnet-1..."
    SUBNET_1_ID=$(aws ec2 describe-subnets \
        --filters "Name=tag:goat-demo,Values=true" "Name=tag:Name,Values=goat-demo-subnet-1" \
        --query "Subnets[0].SubnetId" --output text --region "$REGION" 2>/dev/null)

    if [ -n "$SUBNET_1_ID" ] && [ "$SUBNET_1_ID" != "None" ]; then
        print_green "  Subnet 1 already exists: $SUBNET_1_ID"
        # Retrieve AZ from existing subnet for EBS volume placement
        AZ1=$(aws ec2 describe-subnets --subnet-ids "$SUBNET_1_ID" --query "Subnets[0].AvailabilityZone" --output text --region "$REGION" 2>/dev/null)
    else
        print_yellow "Creating subnet-1 in $AZ1 (10.99.1.0/24)..."
        SUBNET_1_ID=$(aws ec2 create-subnet \
            --vpc-id "$VPC_ID" \
            --cidr-block 10.99.1.0/24 \
            --availability-zone "$AZ1" \
            --tag-specifications 'ResourceType=subnet,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=a},{Key=Name,Value=goat-demo-subnet-1},{Key=auto-delete,Value=no}]' \
            --query "Subnet.SubnetId" --output text --region "$REGION" 2>&1)
        if [ $? -ne 0 ]; then
            print_red "  WARNING: Failed to create subnet-1: $SUBNET_1_ID"
            WARNINGS+=("Subnet-1 creation failed")
            SUBNET_1_ID=""
        else
            print_green "  Created subnet-1: $SUBNET_1_ID ($AZ1)"
        fi
    fi

    # Subnet 2
    print_yellow "Checking for existing subnet-2..."
    SUBNET_2_ID=$(aws ec2 describe-subnets \
        --filters "Name=tag:goat-demo,Values=true" "Name=tag:Name,Values=goat-demo-subnet-2" \
        --query "Subnets[0].SubnetId" --output text --region "$REGION" 2>/dev/null)

    if [ -n "$SUBNET_2_ID" ] && [ "$SUBNET_2_ID" != "None" ]; then
        print_green "  Subnet 2 already exists: $SUBNET_2_ID"
    else
        print_yellow "Creating subnet-2 in $AZ2 (10.99.2.0/24)..."
        SUBNET_2_ID=$(aws ec2 create-subnet \
            --vpc-id "$VPC_ID" \
            --cidr-block 10.99.2.0/24 \
            --availability-zone "$AZ2" \
            --tag-specifications 'ResourceType=subnet,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=a},{Key=Name,Value=goat-demo-subnet-2},{Key=auto-delete,Value=no}]' \
            --query "Subnet.SubnetId" --output text --region "$REGION" 2>&1)
        if [ $? -ne 0 ]; then
            print_red "  WARNING: Failed to create subnet-2: $SUBNET_2_ID"
            WARNINGS+=("Subnet-2 creation failed")
            SUBNET_2_ID=""
        else
            print_green "  Created subnet-2: $SUBNET_2_ID ($AZ2)"
        fi
    fi

    # DB Subnet Group
    if [ -n "$SUBNET_1_ID" ] && [ "$SUBNET_1_ID" != "None" ] && [ -n "$SUBNET_2_ID" ] && [ "$SUBNET_2_ID" != "None" ]; then
        print_yellow "Checking for existing DB subnet group..."
        DB_SUBNET_GROUP=$(aws rds describe-db-subnet-groups \
            --db-subnet-group-name goat-demo-db-subnet-group \
            --query "DBSubnetGroups[0].DBSubnetGroupName" --output text --region "$REGION" 2>/dev/null)

        if [ -n "$DB_SUBNET_GROUP" ] && [ "$DB_SUBNET_GROUP" != "None" ]; then
            print_green "  DB subnet group already exists: $DB_SUBNET_GROUP"
        else
            print_yellow "Creating DB subnet group..."
            DB_SUBNET_GROUP=$(aws rds create-db-subnet-group \
                --db-subnet-group-name goat-demo-db-subnet-group \
                --db-subnet-group-description "G.O.A.T. demo DB subnet group" \
                --subnet-ids "$SUBNET_1_ID" "$SUBNET_2_ID" \
                --tags Key=goat-demo,Value=true Key=goat-scenario,Value=a Key=Name,Value=goat-demo-db-subnet-group Key=auto-delete,Value=no \
                --query "DBSubnetGroup.DBSubnetGroupName" --output text --region "$REGION" 2>&1)
            if [ $? -ne 0 ]; then
                print_red "  WARNING: Failed to create DB subnet group: $DB_SUBNET_GROUP"
                WARNINGS+=("DB subnet group creation failed")
                DB_SUBNET_GROUP=""
            else
                print_green "  Created DB subnet group: $DB_SUBNET_GROUP"
            fi
        fi
    fi
fi

echo ""

# ---------------------------------------------------------------------------
# 5. Create EC2 instances (idempotent)
# ---------------------------------------------------------------------------
print_magenta "--- EC2 Instances ---"

# Instance 1
print_yellow "Checking for existing goat-demo-instance-1..."
INSTANCE_1_ID=$(aws ec2 describe-instances \
    --filters "Name=tag:goat-demo,Values=true" "Name=tag:Name,Values=goat-demo-instance-1" \
              "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query "Reservations[].Instances[].InstanceId" --output text --region "$REGION" 2>/dev/null)

if [ -n "$INSTANCE_1_ID" ] && [ "$INSTANCE_1_ID" != "None" ]; then
    print_green "  Instance 1 already exists: $INSTANCE_1_ID"
else
    SUBNET_FOR_EC2="${SUBNET_1_ID}"
    SUBNET_ARG=""
    if [ -n "$SUBNET_FOR_EC2" ] && [ "$SUBNET_FOR_EC2" != "None" ]; then
        SUBNET_ARG="--subnet-id $SUBNET_FOR_EC2"
    fi

    print_yellow "Creating EC2 instance goat-demo-instance-1 (t3.micro)..."
    INSTANCE_1_ID=$(aws ec2 run-instances \
        --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
        --instance-type t3.micro \
        $SUBNET_ARG \
        --tag-specifications 'ResourceType=instance,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=a},{Key=Name,Value=goat-demo-instance-1},{Key=auto-delete,Value=no}]' \
        --count 1 \
        --query "Instances[0].InstanceId" --output text --region "$REGION" 2>&1)
    if [ $? -ne 0 ]; then
        print_red "  WARNING: Failed to create instance-1: $INSTANCE_1_ID"
        WARNINGS+=("EC2 instance-1 creation failed")
        INSTANCE_1_ID=""
    else
        print_green "  Created instance-1: $INSTANCE_1_ID"
    fi
fi

# Instance 2
print_yellow "Checking for existing goat-demo-instance-2..."
INSTANCE_2_ID=$(aws ec2 describe-instances \
    --filters "Name=tag:goat-demo,Values=true" "Name=tag:Name,Values=goat-demo-instance-2" \
              "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query "Reservations[].Instances[].InstanceId" --output text --region "$REGION" 2>/dev/null)

if [ -n "$INSTANCE_2_ID" ] && [ "$INSTANCE_2_ID" != "None" ]; then
    print_green "  Instance 2 already exists: $INSTANCE_2_ID"
else
    SUBNET_FOR_EC2="${SUBNET_1_ID}"
    SUBNET_ARG=""
    if [ -n "$SUBNET_FOR_EC2" ] && [ "$SUBNET_FOR_EC2" != "None" ]; then
        SUBNET_ARG="--subnet-id $SUBNET_FOR_EC2"
    fi

    print_yellow "Creating EC2 instance goat-demo-instance-2 (t3.micro)..."
    INSTANCE_2_ID=$(aws ec2 run-instances \
        --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
        --instance-type t3.micro \
        $SUBNET_ARG \
        --tag-specifications 'ResourceType=instance,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=a},{Key=Name,Value=goat-demo-instance-2},{Key=auto-delete,Value=no}]' \
        --count 1 \
        --query "Instances[0].InstanceId" --output text --region "$REGION" 2>&1)
    if [ $? -ne 0 ]; then
        print_red "  WARNING: Failed to create instance-2: $INSTANCE_2_ID"
        WARNINGS+=("EC2 instance-2 creation failed")
        INSTANCE_2_ID=""
    else
        print_green "  Created instance-2: $INSTANCE_2_ID"
    fi
fi

echo ""

# ---------------------------------------------------------------------------
# 6. Create RDS instance (idempotent)
# ---------------------------------------------------------------------------
print_magenta "--- RDS Instance ---"

print_yellow "Checking for existing goat-demo-db..."
RDS_ID=$(aws rds describe-db-instances \
    --db-instance-identifier goat-demo-db \
    --query "DBInstances[0].DBInstanceIdentifier" --output text --region "$REGION" 2>/dev/null)

if [ -n "$RDS_ID" ] && [ "$RDS_ID" != "None" ]; then
    print_green "  RDS instance already exists: $RDS_ID"
else
    DB_SUBNET_ARG=""
    if [ -n "$DB_SUBNET_GROUP" ] && [ "$DB_SUBNET_GROUP" != "None" ]; then
        DB_SUBNET_ARG="--db-subnet-group-name goat-demo-db-subnet-group"
    fi

    print_yellow "Creating RDS instance goat-demo-db (db.t3.micro, MySQL)..."
    print_gray "  (Instance will take several minutes to become available)"
    RDS_ID=$(aws rds create-db-instance \
        --db-instance-identifier goat-demo-db \
        --db-instance-class db.t3.micro \
        --engine mysql \
        --master-username goatadmin \
        --master-user-password GoatDemo2025Temp \
        --allocated-storage 20 \
        --no-multi-az \
        --no-publicly-accessible \
        $DB_SUBNET_ARG \
        --tags Key=goat-demo,Value=true Key=goat-scenario,Value=a Key=Name,Value=goat-demo-db Key=auto-delete,Value=no \
        --query "DBInstance.DBInstanceIdentifier" --output text --region "$REGION" 2>&1)
    if [ $? -ne 0 ]; then
        print_red "  WARNING: Failed to create RDS instance: $RDS_ID"
        WARNINGS+=("RDS instance creation failed")
        RDS_ID=""
    else
        print_green "  Created RDS instance: $RDS_ID (creating...)"
    fi
fi

echo ""

# ---------------------------------------------------------------------------
# 7. Create unattached EBS volume (idempotent)
# ---------------------------------------------------------------------------
print_magenta "--- EBS Volume ---"

print_yellow "Checking for existing goat-demo-ebs-unused..."
EBS_ID=$(aws ec2 describe-volumes \
    --filters "Name=tag:goat-demo,Values=true" "Name=tag:Name,Values=goat-demo-ebs-unused" \
              "Name=status,Values=available,creating,in-use" \
    --query "Volumes[0].VolumeId" --output text --region "$REGION" 2>/dev/null)

if [ -n "$EBS_ID" ] && [ "$EBS_ID" != "None" ]; then
    print_green "  EBS volume already exists: $EBS_ID"
else
    # Use the AZ from subnet-1 for consistency
    EBS_AZ="$AZ1"
    if [ -z "$EBS_AZ" ]; then
        EBS_AZ=$(aws ec2 describe-availability-zones --query "AvailabilityZones[0].ZoneName" --output text --region "$REGION" 2>/dev/null)
    fi

    print_yellow "Creating unattached EBS volume (gp2, 10GB) in $EBS_AZ..."
    EBS_ID=$(aws ec2 create-volume \
        --volume-type gp2 \
        --size 10 \
        --availability-zone "$EBS_AZ" \
        --tag-specifications 'ResourceType=volume,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=a},{Key=Name,Value=goat-demo-ebs-unused},{Key=auto-delete,Value=no}]' \
        --query "VolumeId" --output text --region "$REGION" 2>&1)
    if [ $? -ne 0 ]; then
        print_red "  WARNING: Failed to create EBS volume: $EBS_ID"
        WARNINGS+=("EBS volume creation failed")
        EBS_ID=""
    else
        print_green "  Created EBS volume: $EBS_ID (unattached)"
    fi
fi

echo ""

# ---------------------------------------------------------------------------
# 8. Allocate unassociated Elastic IP (idempotent)
# ---------------------------------------------------------------------------
print_magenta "--- Elastic IP ---"

print_yellow "Checking for existing goat-demo-eip-unused..."
EIP_ID=$(aws ec2 describe-addresses \
    --filters "Name=tag:goat-demo,Values=true" "Name=tag:Name,Values=goat-demo-eip-unused" \
    --query "Addresses[0].AllocationId" --output text --region "$REGION" 2>/dev/null)

if [ -n "$EIP_ID" ] && [ "$EIP_ID" != "None" ]; then
    print_green "  Elastic IP already exists: $EIP_ID"
else
    print_yellow "Allocating Elastic IP (unassociated)..."
    EIP_ID=$(aws ec2 allocate-address \
        --domain vpc \
        --tag-specifications 'ResourceType=elastic-ip,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=a},{Key=Name,Value=goat-demo-eip-unused},{Key=auto-delete,Value=no}]' \
        --query "AllocationId" --output text --region "$REGION" 2>&1)
    if [ $? -ne 0 ]; then
        print_red "  WARNING: Failed to allocate Elastic IP: $EIP_ID"
        WARNINGS+=("Elastic IP allocation failed")
        EIP_ID=""
    else
        print_green "  Allocated Elastic IP: $EIP_ID (unassociated)"
    fi
fi

echo ""

# ---------------------------------------------------------------------------
# 9. Create Support case (if Support plan is active)
# ---------------------------------------------------------------------------
print_magenta "--- Support Case ---"

print_yellow "Detecting Support plan..."
SUPPORT_CHECK=$(aws support describe-services --query "services[0].code" --output text --region us-east-1 2>&1)

if echo "$SUPPORT_CHECK" | grep -qi "SubscriptionRequiredException"; then
    print_yellow "  WARNING: No Business or Enterprise Support plan detected."
    print_yellow "  Skipping Support case creation. To enable this feature, upgrade your Support plan."
    WARNINGS+=("Support case skipped - no Support plan")
    SUPPORT_CASE_ID="skipped (no Support plan)"
else
    print_yellow "Creating Support case..."
    SUPPORT_CASE_ID=$(aws support create-case \
        --subject "General account review - G.O.A.T. demo" \
        --communication-body "This case was created for demo purposes by the G.O.A.T. provisioning scripts. It demonstrates cross-domain correlation in the AI Operations Assistant. No action is required." \
        --service-code "general-info" \
        --category-code "other" \
        --severity-code "low" \
        --language "en" \
        --query "caseId" --output text --region us-east-1 2>&1)

    if [ $? -ne 0 ]; then
        print_red "  WARNING: Failed to create Support case: $SUPPORT_CASE_ID"
        WARNINGS+=("Support case creation failed")
        SUPPORT_CASE_ID=""
    else
        print_green "  Created Support case: $SUPPORT_CASE_ID"

        # Add demo-purpose communication
        aws support add-communication-to-case \
            --case-id "$SUPPORT_CASE_ID" \
            --communication-body "This Support case was created automatically by the G.O.A.T. demo provisioning scripts for demonstration purposes only. It is being resolved immediately. No action is needed from AWS Support." \
            --region us-east-1 2>/dev/null

        # Immediately resolve the case
        print_yellow "  Resolving Support case..."
        RESOLVE_OUTPUT=$(aws support resolve-case --case-id "$SUPPORT_CASE_ID" --region us-east-1 2>&1)
        if [ $? -ne 0 ]; then
            print_red "  WARNING: Failed to resolve Support case $SUPPORT_CASE_ID"
            print_red "  Please close it manually via the AWS Console: https://console.aws.amazon.com/support/home"
            WARNINGS+=("Support case resolve failed - close manually: $SUPPORT_CASE_ID")
        else
            print_green "  Support case resolved: $SUPPORT_CASE_ID"
        fi
    fi
fi

echo ""

# ---------------------------------------------------------------------------
# 10. Summary
# ---------------------------------------------------------------------------
print_green "========================================"
print_green "  G.O.A.T. Scenario A Setup Complete!"
print_green "========================================"
echo ""
print_cyan "  Region:              $REGION"

if [ -n "$VPC_ID" ] && [ "$VPC_ID" != "None" ]; then
    print_cyan "  VPC:                 $VPC_ID"
fi
if [ -n "$SUBNET_1_ID" ] && [ "$SUBNET_1_ID" != "None" ]; then
    print_cyan "  Subnet 1:            $SUBNET_1_ID"
fi
if [ -n "$SUBNET_2_ID" ] && [ "$SUBNET_2_ID" != "None" ]; then
    print_cyan "  Subnet 2:            $SUBNET_2_ID"
fi
if [ -n "$DB_SUBNET_GROUP" ] && [ "$DB_SUBNET_GROUP" != "None" ]; then
    print_cyan "  DB Subnet Group:     $DB_SUBNET_GROUP"
fi
if [ -n "$INSTANCE_1_ID" ] && [ "$INSTANCE_1_ID" != "None" ]; then
    print_cyan "  EC2 Instance 1:      $INSTANCE_1_ID"
fi
if [ -n "$INSTANCE_2_ID" ] && [ "$INSTANCE_2_ID" != "None" ]; then
    print_cyan "  EC2 Instance 2:      $INSTANCE_2_ID"
fi
if [ -n "$RDS_ID" ] && [ "$RDS_ID" != "None" ]; then
    print_cyan "  RDS Instance:        $RDS_ID (creating...)"
fi
if [ -n "$EBS_ID" ] && [ "$EBS_ID" != "None" ]; then
    print_cyan "  EBS Volume:          $EBS_ID (unattached)"
fi
if [ -n "$EIP_ID" ] && [ "$EIP_ID" != "None" ]; then
    print_cyan "  Elastic IP:          $EIP_ID (unassociated)"
fi
if [ -n "$SUPPORT_CASE_ID" ]; then
    print_cyan "  Support Case:        $SUPPORT_CASE_ID"
fi

if [ ${#WARNINGS[@]} -gt 0 ]; then
    echo ""
    print_yellow "  Warnings:"
    for w in "${WARNINGS[@]}"; do
        print_yellow "    - $w"
    done
fi

echo ""
print_cyan "  Suggested Demo Query:"
print_green "    \"Give me a complete health check of my AWS account\""
echo ""
print_gray "  To clean up all demo resources:"
print_gray "    ./cleanup-scenarios.sh     (Bash)"
print_gray "    .\\cleanup-scenarios.ps1    (PowerShell)"
echo ""
