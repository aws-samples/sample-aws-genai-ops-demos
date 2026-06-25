#!/bin/bash
# G.O.A.T. Demo Scenario - TLS Fragmentation Reproduction
#
# !!! OUT OF DATE - DO NOT USE !!!
# This Bash version still builds the OLD in-VPC firewall topology. The
# PowerShell version (setup-scenario-tls-fragmentation.ps1) has been rewritten
# to use a separate inspection VPC reached over a Transit Gateway, which is the
# topology that reproduces the failure. This .sh script has NOT yet been ported
# and will create a DIFFERENT architecture. Use the .ps1 version until this is
# updated. Set GOAT_ALLOW_STALE_SH=1 to bypass this guard intentionally.
#
# Reproduces the AWS Network Firewall + Amazon Linux 2023 OpenSSL 3.5.5
# ML-KEM TLS Client Hello fragmentation failure mode.
#
# All resources tagged: goat-demo=true, goat-scenario=tls-fragmentation, auto-delete=no
# Script is idempotent - safe to re-run after partial failures.
#
# Usage: ./setup-scenario-tls-fragmentation.sh

set -o pipefail

if [ "${GOAT_ALLOW_STALE_SH:-0}" != "1" ]; then
    echo "ERROR: This Bash script is out of date and builds a different (old) topology" >&2
    echo "       than setup-scenario-tls-fragmentation.ps1. Use the PowerShell version," >&2
    echo "       or set GOAT_ALLOW_STALE_SH=1 to run this stale script intentionally." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Color helpers
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
SUBNET_PUBLIC_ID=""
SUBNET_PRIVATE_ID=""
SUBNET_FIREWALL_ID=""
IGW_ID=""
NAT_GW_ID=""
NFW_ARN=""
EKS_CLUSTER_NAME=""
EKS_NODEGROUP_NAME=""
TEST_POD_NAME=""
WARNINGS=()

# Pre-existing tracking
declare -A PRE_EXISTING

get_summary_prefix() {
    local key="$1"
    if [[ "${PRE_EXISTING[$key]:-}" == "true" ]]; then
        echo "[PRE-EXISTING] "
    else
        echo ""
    fi
}

# ---------------------------------------------------------------------------
# 1. Verify AWS credentials
# ---------------------------------------------------------------------------
print_cyan "=== G.O.A.T. Demo Scenario - TLS Fragmentation Reproduction ==="
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
# 2. Detect region (Req 12.9)
# ---------------------------------------------------------------------------
print_yellow "Detecting AWS region..."

REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
if [ -z "$REGION" ]; then
    REGION=$(aws configure get region 2>/dev/null)
fi
if [ -z "$REGION" ]; then
    print_red "ERROR: No AWS region detected."
    print_red "Set AWS_REGION, AWS_DEFAULT_REGION, or run 'aws configure set region <region>'."
    exit 1
fi
print_green "  Region: $REGION"
echo ""

# ---------------------------------------------------------------------------
# 3. Create VPC (Req 12.1)
# ---------------------------------------------------------------------------
print_magenta "--- VPC and Networking ---"

print_yellow "Checking for existing goat-demo-tls-vpc..."
VPC_ID=$(aws ec2 describe-vpcs \
    --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-vpc" \
    --query "Vpcs[0].VpcId" --output text --region "$REGION" 2>/dev/null)

if [ -n "$VPC_ID" ] && [ "$VPC_ID" != "None" ]; then
    print_green "  VPC already exists: $VPC_ID"
    PRE_EXISTING["vpc"]="true"
else
    print_yellow "Creating VPC goat-demo-tls-vpc (10.99.0.0/16)..."
    VPC_ID=$(aws ec2 create-vpc \
        --cidr-block 10.99.0.0/16 \
        --tag-specifications 'ResourceType=vpc,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=tls-fragmentation},{Key=Name,Value=goat-demo-tls-vpc},{Key=auto-delete,Value=no}]' \
        --query "Vpc.VpcId" --output text --region "$REGION" 2>&1)
    if [ $? -ne 0 ] || [ -z "$VPC_ID" ] || [ "$VPC_ID" == "None" ]; then
        print_red "  ERROR: Failed to create VPC: $VPC_ID"
        WARNINGS+=("VPC creation failed")
        VPC_ID=""
    else
        print_green "  Created VPC: $VPC_ID"
        aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-hostnames '{"Value":true}' --region "$REGION" 2>/dev/null
        aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-support '{"Value":true}' --region "$REGION" 2>/dev/null
    fi
fi

# ---------------------------------------------------------------------------
# 4. Create subnets across 2 AZs (Req 12.1)
# ---------------------------------------------------------------------------
AZ1=""
AZ2=""

if [ -n "$VPC_ID" ] && [ "$VPC_ID" != "None" ]; then
    AZ1=$(aws ec2 describe-availability-zones --query "AvailabilityZones[0].ZoneName" --output text --region "$REGION" 2>/dev/null)
    AZ2=$(aws ec2 describe-availability-zones --query "AvailabilityZones[1].ZoneName" --output text --region "$REGION" 2>/dev/null)

    # Public subnet
    print_yellow "Checking for existing public subnet..."
    SUBNET_PUBLIC_ID=$(aws ec2 describe-subnets \
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-public" \
        --query "Subnets[0].SubnetId" --output text --region "$REGION" 2>/dev/null)

    if [ -n "$SUBNET_PUBLIC_ID" ] && [ "$SUBNET_PUBLIC_ID" != "None" ]; then
        print_green "  Public subnet already exists: $SUBNET_PUBLIC_ID"
        PRE_EXISTING["subnet-public"]="true"
    else
        print_yellow "Creating public subnet in $AZ1 (10.99.1.0/24)..."
        SUBNET_PUBLIC_ID=$(aws ec2 create-subnet \
            --vpc-id "$VPC_ID" \
            --cidr-block 10.99.1.0/24 \
            --availability-zone "$AZ1" \
            --tag-specifications 'ResourceType=subnet,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=tls-fragmentation},{Key=Name,Value=goat-demo-tls-public},{Key=auto-delete,Value=no}]' \
            --query "Subnet.SubnetId" --output text --region "$REGION" 2>&1)
        if [ $? -ne 0 ] || [ -z "$SUBNET_PUBLIC_ID" ] || [ "$SUBNET_PUBLIC_ID" == "None" ]; then
            print_red "  WARNING: Failed to create public subnet: $SUBNET_PUBLIC_ID"
            WARNINGS+=("Public subnet creation failed")
            SUBNET_PUBLIC_ID=""
        else
            print_green "  Created public subnet: $SUBNET_PUBLIC_ID"
        fi
    fi

    # Private subnet
    print_yellow "Checking for existing private subnet..."
    SUBNET_PRIVATE_ID=$(aws ec2 describe-subnets \
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-private" \
        --query "Subnets[0].SubnetId" --output text --region "$REGION" 2>/dev/null)

    if [ -n "$SUBNET_PRIVATE_ID" ] && [ "$SUBNET_PRIVATE_ID" != "None" ]; then
        print_green "  Private subnet already exists: $SUBNET_PRIVATE_ID"
        PRE_EXISTING["subnet-private"]="true"
    else
        print_yellow "Creating private subnet in $AZ2 (10.99.2.0/24)..."
        SUBNET_PRIVATE_ID=$(aws ec2 create-subnet \
            --vpc-id "$VPC_ID" \
            --cidr-block 10.99.2.0/24 \
            --availability-zone "$AZ2" \
            --tag-specifications 'ResourceType=subnet,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=tls-fragmentation},{Key=Name,Value=goat-demo-tls-private},{Key=auto-delete,Value=no}]' \
            --query "Subnet.SubnetId" --output text --region "$REGION" 2>&1)
        if [ $? -ne 0 ] || [ -z "$SUBNET_PRIVATE_ID" ] || [ "$SUBNET_PRIVATE_ID" == "None" ]; then
            print_red "  WARNING: Failed to create private subnet: $SUBNET_PRIVATE_ID"
            WARNINGS+=("Private subnet creation failed")
            SUBNET_PRIVATE_ID=""
        else
            print_green "  Created private subnet: $SUBNET_PRIVATE_ID"
        fi
    fi

    # Firewall subnet
    print_yellow "Checking for existing firewall subnet..."
    SUBNET_FIREWALL_ID=$(aws ec2 describe-subnets \
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-firewall" \
        --query "Subnets[0].SubnetId" --output text --region "$REGION" 2>/dev/null)

    if [ -n "$SUBNET_FIREWALL_ID" ] && [ "$SUBNET_FIREWALL_ID" != "None" ]; then
        print_green "  Firewall subnet already exists: $SUBNET_FIREWALL_ID"
        PRE_EXISTING["subnet-firewall"]="true"
    else
        print_yellow "Creating firewall subnet in $AZ1 (10.99.3.0/24)..."
        SUBNET_FIREWALL_ID=$(aws ec2 create-subnet \
            --vpc-id "$VPC_ID" \
            --cidr-block 10.99.3.0/24 \
            --availability-zone "$AZ1" \
            --tag-specifications 'ResourceType=subnet,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=tls-fragmentation},{Key=Name,Value=goat-demo-tls-firewall},{Key=auto-delete,Value=no}]' \
            --query "Subnet.SubnetId" --output text --region "$REGION" 2>&1)
        if [ $? -ne 0 ] || [ -z "$SUBNET_FIREWALL_ID" ] || [ "$SUBNET_FIREWALL_ID" == "None" ]; then
            print_red "  WARNING: Failed to create firewall subnet: $SUBNET_FIREWALL_ID"
            WARNINGS+=("Firewall subnet creation failed")
            SUBNET_FIREWALL_ID=""
        else
            print_green "  Created firewall subnet: $SUBNET_FIREWALL_ID"
        fi
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# 5. Create Internet Gateway (Req 12.1)
# ---------------------------------------------------------------------------
print_magenta "--- Internet Gateway ---"

if [ -n "$VPC_ID" ] && [ "$VPC_ID" != "None" ]; then
    print_yellow "Checking for existing IGW..."
    IGW_ID=$(aws ec2 describe-internet-gateways \
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-igw" \
        --query "InternetGateways[0].InternetGatewayId" --output text --region "$REGION" 2>/dev/null)

    if [ -n "$IGW_ID" ] && [ "$IGW_ID" != "None" ]; then
        print_green "  IGW already exists: $IGW_ID"
        PRE_EXISTING["internet-gateway"]="true"
    else
        print_yellow "Creating Internet Gateway..."
        IGW_ID=$(aws ec2 create-internet-gateway \
            --tag-specifications 'ResourceType=internet-gateway,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=tls-fragmentation},{Key=Name,Value=goat-demo-tls-igw},{Key=auto-delete,Value=no}]' \
            --query "InternetGateway.InternetGatewayId" --output text --region "$REGION" 2>&1)
        if [ $? -ne 0 ] || [ -z "$IGW_ID" ] || [ "$IGW_ID" == "None" ]; then
            print_red "  WARNING: Failed to create IGW: $IGW_ID"
            WARNINGS+=("IGW creation failed")
            IGW_ID=""
        else
            print_green "  Created IGW: $IGW_ID"
            aws ec2 attach-internet-gateway --internet-gateway-id "$IGW_ID" --vpc-id "$VPC_ID" --region "$REGION" 2>/dev/null
            print_gray "  Attached IGW to VPC"
        fi
    fi

    # Create public route table and route
    if [ -n "$IGW_ID" ] && [ "$IGW_ID" != "None" ] && [ -n "$SUBNET_PUBLIC_ID" ] && [ "$SUBNET_PUBLIC_ID" != "None" ]; then
        PUBLIC_RT_ID=$(aws ec2 describe-route-tables \
            --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-public-rt" \
            --query "RouteTables[0].RouteTableId" --output text --region "$REGION" 2>/dev/null)
        if [ -z "$PUBLIC_RT_ID" ] || [ "$PUBLIC_RT_ID" == "None" ]; then
            PUBLIC_RT_ID=$(aws ec2 create-route-table --vpc-id "$VPC_ID" \
                --tag-specifications 'ResourceType=route-table,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=tls-fragmentation},{Key=Name,Value=goat-demo-tls-public-rt},{Key=auto-delete,Value=no}]' \
                --query "RouteTable.RouteTableId" --output text --region "$REGION" 2>/dev/null)
            aws ec2 create-route --route-table-id "$PUBLIC_RT_ID" --destination-cidr-block 0.0.0.0/0 --gateway-id "$IGW_ID" --region "$REGION" 2>/dev/null
            aws ec2 associate-route-table --route-table-id "$PUBLIC_RT_ID" --subnet-id "$SUBNET_PUBLIC_ID" --region "$REGION" 2>/dev/null
            print_gray "  Created public route table with IGW route"
        fi
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# 6. Create NAT Gateway (Req 12.1)
# ---------------------------------------------------------------------------
print_magenta "--- NAT Gateway ---"

if [ -n "$SUBNET_PUBLIC_ID" ] && [ "$SUBNET_PUBLIC_ID" != "None" ]; then
    print_yellow "Checking for existing NAT Gateway..."
    NAT_GW_ID=$(aws ec2 describe-nat-gateways \
        --filter "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-nat" "Name=state,Values=available,pending" \
        --query "NatGateways[0].NatGatewayId" --output text --region "$REGION" 2>/dev/null)

    if [ -n "$NAT_GW_ID" ] && [ "$NAT_GW_ID" != "None" ]; then
        print_green "  NAT Gateway already exists: $NAT_GW_ID"
        PRE_EXISTING["nat-gateway"]="true"
    else
        print_yellow "Allocating EIP for NAT Gateway..."
        NAT_EIP_ALLOC_ID=$(aws ec2 allocate-address --domain vpc \
            --tag-specifications 'ResourceType=elastic-ip,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=tls-fragmentation},{Key=Name,Value=goat-demo-tls-nat-eip},{Key=auto-delete,Value=no}]' \
            --query "AllocationId" --output text --region "$REGION" 2>&1)
        if [ $? -ne 0 ] || [ -z "$NAT_EIP_ALLOC_ID" ] || [ "$NAT_EIP_ALLOC_ID" == "None" ]; then
            print_red "  WARNING: Failed to allocate EIP: $NAT_EIP_ALLOC_ID"
            WARNINGS+=("NAT EIP allocation failed")
        else
            print_yellow "Creating NAT Gateway in public subnet..."
            NAT_GW_ID=$(aws ec2 create-nat-gateway \
                --subnet-id "$SUBNET_PUBLIC_ID" \
                --allocation-id "$NAT_EIP_ALLOC_ID" \
                --tag-specifications 'ResourceType=natgateway,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=tls-fragmentation},{Key=Name,Value=goat-demo-tls-nat},{Key=auto-delete,Value=no}]' \
                --query "NatGateway.NatGatewayId" --output text --region "$REGION" 2>&1)
            if [ $? -ne 0 ] || [ -z "$NAT_GW_ID" ] || [ "$NAT_GW_ID" == "None" ]; then
                print_red "  WARNING: Failed to create NAT Gateway: $NAT_GW_ID"
                WARNINGS+=("NAT Gateway creation failed")
                NAT_GW_ID=""
            else
                print_green "  Created NAT Gateway: $NAT_GW_ID (provisioning...)"
                print_gray "  Waiting for NAT Gateway to become available..."
                aws ec2 wait nat-gateway-available --nat-gateway-ids "$NAT_GW_ID" --region "$REGION" 2>/dev/null
                print_green "  NAT Gateway is available"
            fi
        fi
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# 7. Create AWS Network Firewall (Req 12.2)
# ---------------------------------------------------------------------------
print_magenta "--- AWS Network Firewall ---"

if [ -n "$SUBNET_FIREWALL_ID" ] && [ "$SUBNET_FIREWALL_ID" != "None" ]; then
    print_yellow "Checking for existing Network Firewall..."
    NFW_ARN=$(aws network-firewall describe-firewall \
        --firewall-name goat-demo-tls-nfw \
        --query "Firewall.FirewallArn" --output text --region "$REGION" 2>/dev/null)

    if [ -n "$NFW_ARN" ] && [ "$NFW_ARN" != "None" ]; then
        print_green "  Network Firewall already exists: $NFW_ARN"
        PRE_EXISTING["network-firewall"]="true"
    else
        print_yellow "Creating Network Firewall rule group..."
        RULE_GROUP_ARN=$(aws network-firewall create-rule-group \
            --rule-group-name goat-demo-tls-rules \
            --type STATEFUL \
            --capacity 100 \
            --rule-group '{"RulesSource":{"RulesString":"pass tls any any -> any any (tls.sni; content:\".amazonaws.com\"; endswith; msg:\"Allow AWS services\"; sid:1; rev:1;)"},"StatefulRuleOptions":{"RuleOrder":"STRICT_ORDER"}}' \
            --tags Key=goat-demo,Value=true Key=goat-scenario,Value=tls-fragmentation Key=auto-delete,Value=no \
            --query "RuleGroupResponse.RuleGroupArn" --output text --region "$REGION" 2>&1)
        if [ $? -ne 0 ] || [ -z "$RULE_GROUP_ARN" ] || [ "$RULE_GROUP_ARN" == "None" ]; then
            print_red "  WARNING: Failed to create rule group: $RULE_GROUP_ARN"
            WARNINGS+=("Network Firewall rule group creation failed")
        else
            print_gray "  Created rule group: $RULE_GROUP_ARN"

            # Create firewall policy with drop established default action
            POLICY_ARN=$(aws network-firewall create-firewall-policy \
                --firewall-policy-name goat-demo-tls-policy \
                --firewall-policy "{\"StatelessDefaultActions\":[\"aws:forward_to_sfe\"],\"StatelessFragmentDefaultActions\":[\"aws:forward_to_sfe\"],\"StatefulDefaultActions\":[\"aws:drop_established\"],\"StatefulEngineOptions\":{\"RuleOrder\":\"STRICT_ORDER\"},\"StatefulRuleGroupReferences\":[{\"ResourceArn\":\"$RULE_GROUP_ARN\"}]}" \
                --tags Key=goat-demo,Value=true Key=goat-scenario,Value=tls-fragmentation Key=auto-delete,Value=no \
                --query "FirewallPolicyResponse.FirewallPolicyArn" --output text --region "$REGION" 2>&1)
            if [ $? -ne 0 ] || [ -z "$POLICY_ARN" ] || [ "$POLICY_ARN" == "None" ]; then
                print_red "  WARNING: Failed to create firewall policy: $POLICY_ARN"
                WARNINGS+=("Network Firewall policy creation failed")
            else
                print_gray "  Created firewall policy: $POLICY_ARN"

                # Create the firewall
                print_yellow "Creating Network Firewall..."
                NFW_ARN=$(aws network-firewall create-firewall \
                    --firewall-name goat-demo-tls-nfw \
                    --firewall-policy-arn "$POLICY_ARN" \
                    --vpc-id "$VPC_ID" \
                    --subnet-mappings "SubnetId=$SUBNET_FIREWALL_ID" \
                    --tags Key=goat-demo,Value=true Key=goat-scenario,Value=tls-fragmentation Key=Name,Value=goat-demo-tls-nfw Key=auto-delete,Value=no \
                    --query "Firewall.FirewallArn" --output text --region "$REGION" 2>&1)
                if [ $? -ne 0 ] || [ -z "$NFW_ARN" ] || [ "$NFW_ARN" == "None" ]; then
                    print_red "  WARNING: Failed to create Network Firewall: $NFW_ARN"
                    WARNINGS+=("Network Firewall creation failed")
                    NFW_ARN=""
                else
                    print_green "  Created Network Firewall: $NFW_ARN"
                    print_gray "  Waiting for firewall to become ready (this may take several minutes)..."

                    # Wait for firewall to be ready
                    MAX_WAIT=300
                    ELAPSED=0
                    FW_STATUS=""
                    while [ $ELAPSED -lt $MAX_WAIT ]; do
                        FW_STATUS=$(aws network-firewall describe-firewall \
                            --firewall-name goat-demo-tls-nfw \
                            --query "FirewallStatus.Status" --output text --region "$REGION" 2>/dev/null)
                        if [ "$FW_STATUS" == "READY" ]; then break; fi
                        sleep 15
                        ELAPSED=$((ELAPSED + 15))
                    done
                    if [ "$FW_STATUS" == "READY" ]; then
                        print_green "  Network Firewall is ready"
                    else
                        print_yellow "  WARNING: Firewall not ready after ${MAX_WAIT}s (status: $FW_STATUS)"
                        WARNINGS+=("Network Firewall may still be provisioning")
                    fi
                fi
            fi
        fi
    fi

    # Set up routing: private subnet -> firewall -> NAT -> IGW
    if [ -n "$NAT_GW_ID" ] && [ "$NAT_GW_ID" != "None" ] && [ -n "$SUBNET_PRIVATE_ID" ] && [ "$SUBNET_PRIVATE_ID" != "None" ]; then
        # Get firewall endpoint ID for routing
        FW_ENDPOINT_ID=""
        if [ -n "$NFW_ARN" ] && [ "$NFW_ARN" != "None" ]; then
            FW_ENDPOINT_ID=$(aws network-firewall describe-firewall \
                --firewall-name goat-demo-tls-nfw \
                --query "FirewallStatus.SyncStates.*.Attachment.EndpointId | [0]" --output text --region "$REGION" 2>/dev/null)
        fi

        # Private route table -> firewall endpoint
        PRIVATE_RT_ID=$(aws ec2 describe-route-tables \
            --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-private-rt" \
            --query "RouteTables[0].RouteTableId" --output text --region "$REGION" 2>/dev/null)
        if [ -z "$PRIVATE_RT_ID" ] || [ "$PRIVATE_RT_ID" == "None" ]; then
            PRIVATE_RT_ID=$(aws ec2 create-route-table --vpc-id "$VPC_ID" \
                --tag-specifications 'ResourceType=route-table,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=tls-fragmentation},{Key=Name,Value=goat-demo-tls-private-rt},{Key=auto-delete,Value=no}]' \
                --query "RouteTable.RouteTableId" --output text --region "$REGION" 2>/dev/null)
            if [ -n "$FW_ENDPOINT_ID" ] && [ "$FW_ENDPOINT_ID" != "None" ]; then
                aws ec2 create-route --route-table-id "$PRIVATE_RT_ID" --destination-cidr-block 0.0.0.0/0 --vpc-endpoint-id "$FW_ENDPOINT_ID" --region "$REGION" 2>/dev/null
            else
                aws ec2 create-route --route-table-id "$PRIVATE_RT_ID" --destination-cidr-block 0.0.0.0/0 --nat-gateway-id "$NAT_GW_ID" --region "$REGION" 2>/dev/null
            fi
            aws ec2 associate-route-table --route-table-id "$PRIVATE_RT_ID" --subnet-id "$SUBNET_PRIVATE_ID" --region "$REGION" 2>/dev/null
            print_gray "  Created private route table"
        fi

        # Firewall route table -> NAT Gateway
        FW_RT_ID=$(aws ec2 describe-route-tables \
            --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-firewall-rt" \
            --query "RouteTables[0].RouteTableId" --output text --region "$REGION" 2>/dev/null)
        if [ -z "$FW_RT_ID" ] || [ "$FW_RT_ID" == "None" ]; then
            FW_RT_ID=$(aws ec2 create-route-table --vpc-id "$VPC_ID" \
                --tag-specifications 'ResourceType=route-table,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=tls-fragmentation},{Key=Name,Value=goat-demo-tls-firewall-rt},{Key=auto-delete,Value=no}]' \
                --query "RouteTable.RouteTableId" --output text --region "$REGION" 2>/dev/null)
            aws ec2 create-route --route-table-id "$FW_RT_ID" --destination-cidr-block 0.0.0.0/0 --nat-gateway-id "$NAT_GW_ID" --region "$REGION" 2>/dev/null
            aws ec2 associate-route-table --route-table-id "$FW_RT_ID" --subnet-id "$SUBNET_FIREWALL_ID" --region "$REGION" 2>/dev/null
            print_gray "  Created firewall route table"
        fi
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# 8. Create EKS Cluster (Req 12.3, 12.4)
# ---------------------------------------------------------------------------
print_magenta "--- EKS Cluster ---"

EKS_CLUSTER_NAME="goat-demo-tls-cluster"
EKS_NODEGROUP_NAME="goat-demo-tls-nodes"

if [ -n "$SUBNET_PRIVATE_ID" ] && [ "$SUBNET_PRIVATE_ID" != "None" ]; then
    print_yellow "Checking for existing EKS cluster..."
    EKS_STATUS=$(aws eks describe-cluster --name "$EKS_CLUSTER_NAME" \
        --query "cluster.status" --output text --region "$REGION" 2>/dev/null)

    if [ -n "$EKS_STATUS" ] && [ "$EKS_STATUS" != "None" ]; then
        print_green "  EKS cluster already exists: $EKS_CLUSTER_NAME (status: $EKS_STATUS)"
        PRE_EXISTING["eks-cluster"]="true"
    else
        # Create EKS service role if not exists
        print_yellow "Ensuring EKS service role exists..."
        EKS_ROLE_ARN=$(aws iam get-role --role-name goat-demo-tls-eks-role \
            --query "Role.Arn" --output text 2>/dev/null)
        if [ -z "$EKS_ROLE_ARN" ] || [ "$EKS_ROLE_ARN" == "None" ]; then
            TRUST_POLICY='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"eks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
            EKS_ROLE_ARN=$(aws iam create-role --role-name goat-demo-tls-eks-role \
                --assume-role-policy-document "$TRUST_POLICY" \
                --tags Key=goat-demo,Value=true Key=goat-scenario,Value=tls-fragmentation Key=auto-delete,Value=no \
                --query "Role.Arn" --output text 2>/dev/null)
            aws iam attach-role-policy --role-name goat-demo-tls-eks-role \
                --policy-arn arn:aws:iam::aws:policy/AmazonEKSClusterPolicy 2>/dev/null
            print_gray "  Created EKS service role"
            sleep 10
        fi

        print_yellow "Creating EKS cluster $EKS_CLUSTER_NAME..."
        aws eks create-cluster \
            --name "$EKS_CLUSTER_NAME" \
            --role-arn "$EKS_ROLE_ARN" \
            --resources-vpc-config "subnetIds=$SUBNET_PRIVATE_ID,$SUBNET_PUBLIC_ID,securityGroupIds=" \
            --tags goat-demo=true,goat-scenario=tls-fragmentation,auto-delete=no \
            --region "$REGION" 2>&1 >/dev/null
        if [ $? -ne 0 ]; then
            print_red "  WARNING: Failed to create EKS cluster"
            WARNINGS+=("EKS cluster creation failed")
            EKS_CLUSTER_NAME=""
        else
            print_green "  EKS cluster creation initiated (this takes 10-15 minutes)..."
            print_gray "  Waiting for cluster to become ACTIVE..."
            aws eks wait cluster-active --name "$EKS_CLUSTER_NAME" --region "$REGION" 2>/dev/null
            print_green "  EKS cluster is ACTIVE"
        fi
    fi

    # Create managed node group (Req 12.3, 12.4)
    if [ -n "$EKS_CLUSTER_NAME" ] && [ "$EKS_CLUSTER_NAME" != "None" ]; then
        NG_STATUS=$(aws eks describe-nodegroup --cluster-name "$EKS_CLUSTER_NAME" \
            --nodegroup-name "$EKS_NODEGROUP_NAME" \
            --query "nodegroup.status" --output text --region "$REGION" 2>/dev/null)

        if [ -n "$NG_STATUS" ] && [ "$NG_STATUS" != "None" ]; then
            print_green "  Node group already exists: $EKS_NODEGROUP_NAME (status: $NG_STATUS)"
            PRE_EXISTING["eks-nodegroup"]="true"
        else
            # Create node role
            NODE_ROLE_ARN=$(aws iam get-role --role-name goat-demo-tls-node-role \
                --query "Role.Arn" --output text 2>/dev/null)
            if [ -z "$NODE_ROLE_ARN" ] || [ "$NODE_ROLE_ARN" == "None" ]; then
                NODE_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
                NODE_ROLE_ARN=$(aws iam create-role --role-name goat-demo-tls-node-role \
                    --assume-role-policy-document "$NODE_TRUST" \
                    --tags Key=goat-demo,Value=true Key=goat-scenario,Value=tls-fragmentation Key=auto-delete,Value=no \
                    --query "Role.Arn" --output text 2>/dev/null)
                aws iam attach-role-policy --role-name goat-demo-tls-node-role \
                    --policy-arn arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy 2>/dev/null
                aws iam attach-role-policy --role-name goat-demo-tls-node-role \
                    --policy-arn arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy 2>/dev/null
                aws iam attach-role-policy --role-name goat-demo-tls-node-role \
                    --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly 2>/dev/null
                print_gray "  Created node IAM role"
                sleep 10
            fi

            # Check for AL2023 AMI via SSM (60s timeout per Req 12.4)
            print_yellow "  Checking for AL2023 EKS AMI..."
            USE_BOTTLEROCKET=false
            AMI_TYPE="AL2023_x86_64_STANDARD"
            SSM_PARAM="/aws/service/eks/optimized-ami/1.29/amazon-linux-2023/x86_64/standard/recommended/image_id"
            AL2023_AMI=$(timeout 60 aws ssm get-parameter --name "$SSM_PARAM" \
                --query "Parameter.Value" --output text --region "$REGION" 2>/dev/null)
            if [ -z "$AL2023_AMI" ] || [ "$AL2023_AMI" == "None" ]; then
                print_yellow "  WARNING: FALLBACK_BOTTLEROCKET $REGION - AL2023 AMI not available"
                WARNINGS+=("FALLBACK_BOTTLEROCKET $REGION")
                USE_BOTTLEROCKET=true
                AMI_TYPE="BOTTLEROCKET_x86_64"
            else
                print_green "  AL2023 AMI available: $AL2023_AMI"
            fi

            print_yellow "  Creating managed node group ($AMI_TYPE)..."
            aws eks create-nodegroup \
                --cluster-name "$EKS_CLUSTER_NAME" \
                --nodegroup-name "$EKS_NODEGROUP_NAME" \
                --node-role "$NODE_ROLE_ARN" \
                --subnets "$SUBNET_PRIVATE_ID" \
                --instance-types t3.medium \
                --scaling-config minSize=1,maxSize=2,desiredSize=1 \
                --ami-type "$AMI_TYPE" \
                --tags goat-demo=true,goat-scenario=tls-fragmentation,auto-delete=no \
                --region "$REGION" 2>&1 >/dev/null
            if [ $? -ne 0 ]; then
                print_red "  WARNING: Failed to create node group"
                WARNINGS+=("EKS node group creation failed")
                EKS_NODEGROUP_NAME=""
            else
                print_green "  Node group creation initiated..."
                print_gray "  Waiting for node group to become ACTIVE (this takes several minutes)..."
                aws eks wait nodegroup-active --cluster-name "$EKS_CLUSTER_NAME" \
                    --nodegroup-name "$EKS_NODEGROUP_NAME" --region "$REGION" 2>/dev/null
                print_green "  Node group is ACTIVE"
            fi
        fi
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# 9. Deploy Test Pod (Req 12.5)
# ---------------------------------------------------------------------------
print_magenta "--- Test Pod ---"

TEST_POD_NAME="goat-tls-tester"

if [ -n "$EKS_CLUSTER_NAME" ] && [ "$EKS_CLUSTER_NAME" != "None" ]; then
    print_yellow "Updating kubeconfig..."
    aws eks update-kubeconfig --name "$EKS_CLUSTER_NAME" --region "$REGION" 2>/dev/null

    # Check if pod already exists
    POD_EXISTS=$(kubectl get pod "$TEST_POD_NAME" --namespace default --no-headers 2>/dev/null)
    if [ -n "$POD_EXISTS" ]; then
        print_green "  Test pod already exists: $TEST_POD_NAME"
        PRE_EXISTING["test-pod"]="true"
    else
        print_yellow "Deploying test pod ($TEST_POD_NAME)..."
        # shellcheck disable=SC2002 — heredoc pipe to kubectl is idiomatic, not useless-cat
        # nosemgrep: useless-cat — piping heredoc to kubectl apply is the standard pattern
        cat <<EOF | kubectl apply -f - 2>&1 >/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: $TEST_POD_NAME
  namespace: default
  labels:
    goat-demo: "true"
    goat-scenario: tls-fragmentation
spec:
  containers:
  - name: tls-tester
    image: public.ecr.aws/amazonlinux/amazonlinux:2023
    command:
    - /bin/bash
    - -c
    - |
      yum install -y curl openssl > /dev/null 2>&1
      echo "Starting TLS fragmentation test loop..."
      while true; do
        echo "[\$(date -u +%Y-%m-%dT%H:%M:%SZ)] Attempting HTTPS to ecr.$REGION.amazonaws.com..."
        curl -sS -o /dev/null -w "HTTP %{http_code} TLS %{ssl_version}\n" \
          "https://ecr.$REGION.amazonaws.com/" 2>&1 || echo "Connection failed"
        sleep 60
      done
    env:
    - name: AWS_DEFAULT_REGION
      value: "$REGION"
  restartPolicy: Always
EOF
        if [ $? -ne 0 ]; then
            print_red "  WARNING: Failed to deploy test pod"
            WARNINGS+=("Test pod deployment failed")
            TEST_POD_NAME=""
        else
            print_green "  Deployed test pod: $TEST_POD_NAME"
        fi
    fi
else
    print_yellow "  Skipping test pod (no EKS cluster)"
    TEST_POD_NAME=""
fi
# ---------------------------------------------------------------------------
# 10. Create Support case (if Support plan is active)
#
# Creates a resolved case describing the TLS fragmentation failure so the
# agent can correlate it with the Network capture and Health event during
# the "investigate support case" demo flow.
# ---------------------------------------------------------------------------
print_magenta "--- Support Case ---"

SUPPORT_CASE_ID=""

print_yellow "Detecting Support plan..."
SUPPORT_CHECK=$(aws support describe-services --query "services[0].code" --output text --region us-east-1 2>&1)

if echo "$SUPPORT_CHECK" | grep -qi "SubscriptionRequiredException"; then
    print_yellow "  WARNING: No Business or Enterprise Support plan detected."
    print_yellow "  Skipping Support case creation. To enable this feature, upgrade your Support plan."
    WARNINGS+=("Support case skipped - no Support plan")
    SUPPORT_CASE_ID="skipped (no Support plan)"
else
    print_yellow "Creating Support case for TLS fragmentation scenario..."
    SUPPORT_CASE_ID=$(aws support create-case \
        --subject "EKS pods failing to pull ECR images - connection reset by peer in $REGION" \
        --communication-body "Our EKS pods running Amazon Linux 2023 in $REGION are intermittently failing to pull container images from ECR (endpoint: ecr.$REGION.amazonaws.com on port 443). The error is 'connection reset by peer' during the TLS handshake. This started after the latest AL2023 update that upgraded OpenSSL to 3.5.5. We suspect the new ML-KEM (Kyber-768) key-share is producing oversized TLS Client Hello messages (~3.5 KB) that are being fragmented across multiple TCP segments. Our AWS Network Firewall (goat-demo-tls-nfw) uses the legacy 'drop established' default action with pass rules for *.amazonaws.com domains — we believe the firewall cannot extract the SNI from the fragmented Client Hello and is dropping the connection. Affected resources: EKS cluster $EKS_CLUSTER_NAME, VPC $VPC_ID (10.99.0.0/16, name: goat-demo-tls-vpc), Network Firewall goat-demo-tls-nfw, destination ecr.$REGION.amazonaws.com:443, source port ephemeral. Account $ACCOUNT_ID, region $REGION. This case was created by the G.O.A.T. demo provisioning scripts for demonstration purposes." \
        --service-code "amazon-elastic-kubernetes-service" \
        --category-code "other" \
        --severity-code "high" \
        --language "en" \
        --query "caseId" --output text --region us-east-1 2>&1)

    if [ $? -ne 0 ]; then
        print_red "  WARNING: Failed to create Support case: $SUPPORT_CASE_ID"
        WARNINGS+=("Support case creation failed")
        SUPPORT_CASE_ID=""
    else
        print_green "  Created Support case: $SUPPORT_CASE_ID"

        # Add a follow-up communication with additional diagnostic details
        aws support add-communication-to-case \
            --case-id "$SUPPORT_CASE_ID" \
            --communication-body "Additional details: We captured traffic using VPC Traffic Mirroring on VPC $VPC_ID and confirmed the TLS Client Hello is 3,547 bytes (fragmented into 3 TCP segments). The Network Firewall goat-demo-tls-nfw sends a TCP RST from its own ENI (source IP does not match either endpoint — the RST originates from the firewall's endpoint in subnet $SUBNET_FIREWALL_ID). The RST arrives immediately after the fragmented Client Hello, before ecr.$REGION.amazonaws.com responds. We believe this matches the known issue with AWS Network Firewall stateful rule groups using 'drop established' default action failing to inspect SNI in fragmented TLS records. EKS node group $EKS_NODEGROUP_NAME is running AL2023 with OpenSSL 3.5.5 (ML-KEM enabled by default). Workaround under evaluation: switch to 'aws:drop_strict' with 'flow:to_server, flow:established' qualifiers. This case was created automatically by the G.O.A.T. demo scripts — no action needed from AWS Support." \
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
# 11. Summary (Req 12.7)
# ---------------------------------------------------------------------------
echo ""
print_green "===== TLS FRAGMENTATION SCENARIO SUMMARY START ====="

if [ -n "$VPC_ID" ] && [ "$VPC_ID" != "None" ]; then
    echo "$(get_summary_prefix "vpc")vpc: $VPC_ID"
fi
if [ -n "$SUBNET_PUBLIC_ID" ] && [ "$SUBNET_PUBLIC_ID" != "None" ]; then
    echo "$(get_summary_prefix "subnet-public")subnet-public: $SUBNET_PUBLIC_ID"
fi
if [ -n "$SUBNET_PRIVATE_ID" ] && [ "$SUBNET_PRIVATE_ID" != "None" ]; then
    echo "$(get_summary_prefix "subnet-private")subnet-private: $SUBNET_PRIVATE_ID"
fi
if [ -n "$SUBNET_FIREWALL_ID" ] && [ "$SUBNET_FIREWALL_ID" != "None" ]; then
    echo "$(get_summary_prefix "subnet-firewall")subnet-firewall: $SUBNET_FIREWALL_ID"
fi
if [ -n "$IGW_ID" ] && [ "$IGW_ID" != "None" ]; then
    echo "$(get_summary_prefix "internet-gateway")internet-gateway: $IGW_ID"
fi
if [ -n "$NAT_GW_ID" ] && [ "$NAT_GW_ID" != "None" ]; then
    echo "$(get_summary_prefix "nat-gateway")nat-gateway: $NAT_GW_ID"
fi
if [ -n "$NFW_ARN" ] && [ "$NFW_ARN" != "None" ]; then
    echo "$(get_summary_prefix "network-firewall")network-firewall: $NFW_ARN"
fi
if [ -n "$EKS_CLUSTER_NAME" ] && [ "$EKS_CLUSTER_NAME" != "None" ]; then
    echo "$(get_summary_prefix "eks-cluster")eks-cluster: $EKS_CLUSTER_NAME"
fi
if [ -n "$EKS_NODEGROUP_NAME" ] && [ "$EKS_NODEGROUP_NAME" != "None" ]; then
    echo "$(get_summary_prefix "eks-nodegroup")eks-nodegroup: $EKS_NODEGROUP_NAME"
fi
if [ -n "$TEST_POD_NAME" ]; then
    echo "$(get_summary_prefix "test-pod")test-pod: $TEST_POD_NAME"
fi
if [ -n "$SUPPORT_CASE_ID" ] && [ "$SUPPORT_CASE_ID" != "skipped (no Support plan)" ]; then
    echo "support-case: $SUPPORT_CASE_ID"
elif [ "$SUPPORT_CASE_ID" = "skipped (no Support plan)" ]; then
    echo "support-case: skipped (no Support plan)"
fi

echo "suggested-query: Capture traffic from the EKS test pod and explain why ECR connections fail"
if [ -n "$SUPPORT_CASE_ID" ] && [ "$SUPPORT_CASE_ID" != "skipped (no Support plan)" ]; then
    echo "suggested-query-2: Investigate the network problem described in support case $SUPPORT_CASE_ID and capture traffic if relevant"
fi
print_green "===== TLS FRAGMENTATION SCENARIO SUMMARY END ====="
echo ""

if [ ${#WARNINGS[@]} -gt 0 ]; then
    print_yellow "  Warnings:"
    for w in "${WARNINGS[@]}"; do
        print_yellow "    - $w"
    done
    echo ""
fi

print_gray "  To clean up all demo resources:"
print_gray "    ./cleanup-scenarios.sh     (Bash)"
print_gray "    .\\cleanup-scenarios.ps1    (PowerShell)"
echo ""
