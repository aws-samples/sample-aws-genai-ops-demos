#!/bin/bash
# G.O.A.T. Demo Scenario - TLS Fragmentation Reproduction (Transit Gateway topology)
#
# Reproduces the AWS Network Firewall + Amazon Linux 2023 OpenSSL 3.5.5
# ML-KEM TLS Client Hello fragmentation failure mode using a centralized
# inspection architecture - the same topology where the issue was originally
# observed: the firewall lives in a SEPARATE inspection VPC reached over a
# Transit Gateway, so egress traffic crosses the TGW before inspection.
#
# Topology:
#   Spoke VPC (goat-demo-vpc, 10.99.0.0/16)
#     - EC2 test instance (AL2023, t3.micro) in private subnet
#     - Private subnet: 0.0.0.0/0 -> Transit Gateway
#     - TGW attachment subnet
#   Transit Gateway (appliance mode on inspection attachment for symmetric flows)
#   Inspection VPC (10.98.0.0/16)
#     - AWS Network Firewall (legacy drop-established + SNI pass rule)
#     - NAT Gateway + Internet Gateway
#     - TGW attachment subnet, firewall subnet, NAT/public subnet
#
# Egress path:
#   EC2 instance -> spoke TGW -> TGW -> inspection TGW subnet -> NFW endpoint ->
#   NAT -> IGW -> internet  (return path is symmetric via appliance mode)
#
# All resources tagged: goat-demo=true, goat-scenario=tls-fragmentation, auto-delete=no
# Script is idempotent - safe to re-run after partial failures.
#
# Usage:
#   ./setup-scenario-tls-fragmentation.sh                        # Create a new spoke VPC
#   ./setup-scenario-tls-fragmentation.sh --vpc-id vpc-0abc123   # Reuse an existing spoke VPC

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
# Parse arguments
# ---------------------------------------------------------------------------
VPC_ID_PARAM=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --vpc-id) VPC_ID_PARAM="$2"; shift 2 ;;
        *) print_red "Unknown option: $1"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Track created/existing resources for summary
# ---------------------------------------------------------------------------
VPC_ID=""
SUBNET_PRIVATE_ID=""
SUBNET_SPOKE_TGW_ID=""
INSTANCE_ID=""
INSTANCE_ENI_ID=""
WARNINGS=()
declare -A PRE_EXISTING

# Inspection VPC + Transit Gateway resources
INSP_VPC_ID=""
INSP_NAT_SUBNET_ID=""
INSP_FW_SUBNET_ID=""
INSP_TGW_SUBNET_ID=""
INSP_IGW_ID=""
NAT_GW_ID=""
NFW_ARN=""
TGW_ID=""
TGW_ATTACH_SPOKE_ID=""
TGW_ATTACH_INSP_ID=""

# CIDR plan
SPOKE_CIDR="10.99.0.0/16"
INSP_CIDR="10.98.0.0/16"

# ---------------------------------------------------------------------------
# Helper: summary prefix for pre-existing resources
# ---------------------------------------------------------------------------
get_summary_prefix() {
    local key="$1"
    if [[ "${PRE_EXISTING[$key]:-}" == "true" ]]; then
        echo "[PRE-EXISTING] "
    else
        echo ""
    fi
}

# ---------------------------------------------------------------------------
# Helper: tag specification string
# ---------------------------------------------------------------------------
new_tag_spec() {
    local resource_type="$1"
    local name="$2"
    echo "ResourceType=${resource_type},Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=tls-fragmentation},{Key=Name,Value=${name}},{Key=auto-delete,Value=no}]"
}

# ---------------------------------------------------------------------------
# Helper: idempotently ensure a subnet exists (by Name tag) in a VPC/AZ
# Returns the subnet ID (existing or newly created), or "" on failure.
# ---------------------------------------------------------------------------
get_or_create_subnet() {
    local vpc="$1"
    local cidr="$2"
    local az="$3"
    local name="$4"
    local pre_key="${5:-}"

    local sn
    sn=$(aws ec2 describe-subnets \
        --filters "Name=vpc-id,Values=$vpc" "Name=tag:Name,Values=$name" \
        --query "Subnets[0].SubnetId" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$sn" ] && [ "$sn" != "None" ]; then
        print_green "  Subnet $name already exists: $sn"
        if [ -n "$pre_key" ]; then PRE_EXISTING["$pre_key"]="true"; fi
        echo "$sn"
        return 0
    fi

    print_yellow "  Creating subnet $name in $az ($cidr)..."
    sn=$(aws ec2 create-subnet \
        --vpc-id "$vpc" --cidr-block "$cidr" --availability-zone "$az" \
        --tag-specifications "$(new_tag_spec subnet "$name")" \
        --query "Subnet.SubnetId" --output text --region "$REGION" 2>&1)
    if [ $? -ne 0 ] || [ -z "$sn" ] || [ "$sn" == "None" ]; then
        print_red "  WARNING: Failed to create subnet ${name}: $sn"
        WARNINGS+=("Subnet $name creation failed")
        echo ""
        return 1
    fi
    print_green "  Created subnet ${name}: $sn"
    echo "$sn"
    return 0
}

# ---------------------------------------------------------------------------
# Helper: idempotently ensure a route table exists (by Name tag) in a VPC
# Returns the route table ID.
# ---------------------------------------------------------------------------
get_or_create_route_table() {
    local vpc="$1"
    local name="$2"

    local rt
    rt=$(aws ec2 describe-route-tables \
        --filters "Name=vpc-id,Values=$vpc" "Name=tag:Name,Values=$name" \
        --query "RouteTables[0].RouteTableId" --output text --region "$REGION" 2>/dev/null)
    if [ -n "$rt" ] && [ "$rt" != "None" ]; then
        echo "$rt"
        return 0
    fi
    rt=$(aws ec2 create-route-table --vpc-id "$vpc" \
        --tag-specifications "$(new_tag_spec route-table "$name")" \
        --query "RouteTable.RouteTableId" --output text --region "$REGION" 2>&1)
    echo "$rt"
    return 0
}

# ---------------------------------------------------------------------------
# Helper: set the firewall stateful rule group to "permissive" or "strict".
#
#   permissive : a single catch-all "pass ip any any" rule.
#   strict     : SNI pass rule for ".amazonaws.com" only.
# ---------------------------------------------------------------------------
set_tls_firewall_rules() {
    local mode="$1"  # "permissive" or "strict"
    local rg_name="goat-demo-tls-rules"
    local rules

    if [ "$mode" == "permissive" ]; then
        rules='{"RulesSource":{"RulesString":"pass ip any any -> any any (sid:99; rev:1;)"},"StatefulRuleOptions":{"RuleOrder":"STRICT_ORDER"}}'
    else
        rules='{"RulesSource":{"RulesString":"pass tls any any -> any any (tls.sni; content:\".amazonaws.com\"; endswith; msg:\"Allow AWS services\"; sid:1; rev:1;)"},"StatefulRuleOptions":{"RuleOrder":"STRICT_ORDER"}}'
    fi

    local tok
    tok=$(aws network-firewall describe-rule-group --rule-group-name "$rg_name" --type STATEFUL \
        --query "UpdateToken" --output text --region "$REGION" 2>/dev/null)
    if [ -z "$tok" ] || [ "$tok" == "None" ]; then
        return 1
    fi

    local tmpfile
    tmpfile=$(mktemp)
    echo "$rules" > "$tmpfile"
    aws network-firewall update-rule-group --rule-group-name "$rg_name" --type STATEFUL \
        --update-token "$tok" --rule-group "file://$tmpfile" --region "$REGION" >/dev/null 2>&1
    local rc=$?
    rm -f "$tmpfile"
    return $rc
}

# ---------------------------------------------------------------------------
# Helper: wait until the firewall configuration is fully synchronized
# ---------------------------------------------------------------------------
wait_firewall_in_sync() {
    local max_wait="${1:-150}"
    local elapsed=0
    while [ $elapsed -lt $max_wait ]; do
        local sync
        sync=$(aws network-firewall describe-firewall --firewall-name goat-demo-tls-nfw \
            --query "FirewallStatus.ConfigurationSyncStateSummary" --output text --region "$REGION" 2>/dev/null)
        if [ "$sync" == "IN_SYNC" ]; then return 0; fi
        sleep 15
        elapsed=$((elapsed + 15))
    done
    return 1
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
# 2. Detect region
# ---------------------------------------------------------------------------
print_yellow "Detecting AWS region..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../../shared/utils/get-aws-region.sh
source "$SCRIPT_DIR/../../../shared/utils/get-aws-region.sh"
REGION=$(get_aws_region)

if [ -z "$REGION" ]; then
    print_red "ERROR: No AWS region detected."
    print_red "Set AWS_REGION, AWS_DEFAULT_REGION, or run 'aws configure set region <region>'."
    exit 1
fi
print_green "  Region: $REGION"
echo ""

# ---------------------------------------------------------------------------
# 3. Create or reuse shared spoke VPC
# ---------------------------------------------------------------------------
print_magenta "--- VPC and Networking ---"

if [ -n "$VPC_ID_PARAM" ]; then
    print_yellow "Using provided VPC: $VPC_ID_PARAM"
    VPC_CHECK=$(aws ec2 describe-vpcs --vpc-ids "$VPC_ID_PARAM" --query "Vpcs[0].VpcId" --output text --region "$REGION" 2>&1)
    if [ $? -ne 0 ]; then
        print_red "  ERROR: Provided VPC $VPC_ID_PARAM not found or inaccessible: $VPC_CHECK"
        exit 1
    fi
    VPC_ID="$VPC_ID_PARAM"
    print_green "  VPC validated: $VPC_ID"
    PRE_EXISTING["vpc"]="true"
    aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-hostnames '{"Value":true}' --region "$REGION" 2>/dev/null
    aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-support '{"Value":true}' --region "$REGION" 2>/dev/null
else
    print_yellow "Checking for existing goat-demo-vpc..."
    VPC_ID=$(aws ec2 describe-vpcs \
        --filters "Name=tag:goat-demo,Values=true" "Name=tag:Name,Values=goat-demo-vpc" "Name=tag:goat:component,Values=network-agent" \
        --query "Vpcs[0].VpcId" --output text --region "$REGION" 2>/dev/null)

    # Fallback: try without the goat:component filter (for manually created VPCs)
    if [ -z "$VPC_ID" ] || [ "$VPC_ID" == "None" ]; then
        VPC_ID=$(aws ec2 describe-vpcs \
            --filters "Name=tag:goat-demo,Values=true" "Name=tag:Name,Values=goat-demo-vpc" \
            --query "Vpcs[0].VpcId" --output text --region "$REGION" 2>/dev/null)
    fi

    if [ -n "$VPC_ID" ] && [ "$VPC_ID" != "None" ]; then
        print_green "  Shared VPC already exists: $VPC_ID"
        PRE_EXISTING["vpc"]="true"
    else
        print_yellow "Creating shared VPC goat-demo-vpc (10.99.0.0/16)..."
        VPC_ID=$(aws ec2 create-vpc \
            --cidr-block 10.99.0.0/16 \
            --tag-specifications 'ResourceType=vpc,Tags=[{Key=goat-demo,Value=true},{Key=Name,Value=goat-demo-vpc},{Key=auto-delete,Value=no}]' \
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
fi

# ---------------------------------------------------------------------------
# 4. Create subnets in spoke VPC
# ---------------------------------------------------------------------------
AZ1=""

if [ -n "$VPC_ID" ] && [ "$VPC_ID" != "None" ]; then
    AZ1=$(aws ec2 describe-availability-zones --query "AvailabilityZones[0].ZoneName" --output text --region "$REGION" 2>/dev/null)

    # Private (workload) subnet - hosts the EC2 test instance. Egress routes to the TGW.
    SUBNET_PRIVATE_ID=$(get_or_create_subnet "$VPC_ID" "10.99.13.0/24" "$AZ1" "goat-demo-tls-private" "subnet-private")

    # Spoke TGW attachment subnet - a small dedicated subnet for the TGW ENI.
    SUBNET_SPOKE_TGW_ID=$(get_or_create_subnet "$VPC_ID" "10.99.20.0/24" "$AZ1" "goat-demo-tls-spoke-tgw" "subnet-spoke-tgw")
fi
echo ""

# ---------------------------------------------------------------------------
# 5. Create Inspection VPC (separate VPC reached over the Transit Gateway)
# ---------------------------------------------------------------------------
print_magenta "--- Inspection VPC ---"

print_yellow "Checking for existing inspection VPC..."
INSP_VPC_ID=$(aws ec2 describe-vpcs \
    --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-inspection-vpc" \
    --query "Vpcs[0].VpcId" --output text --region "$REGION" 2>/dev/null)

if [ -n "$INSP_VPC_ID" ] && [ "$INSP_VPC_ID" != "None" ]; then
    print_green "  Inspection VPC already exists: $INSP_VPC_ID"
    PRE_EXISTING["inspection-vpc"]="true"
else
    print_yellow "Creating inspection VPC ($INSP_CIDR)..."
    INSP_VPC_ID=$(aws ec2 create-vpc --cidr-block "$INSP_CIDR" \
        --tag-specifications "$(new_tag_spec vpc goat-demo-tls-inspection-vpc)" \
        --query "Vpc.VpcId" --output text --region "$REGION" 2>&1)
    if [ $? -ne 0 ] || [ -z "$INSP_VPC_ID" ] || [ "$INSP_VPC_ID" == "None" ]; then
        print_red "  WARNING: Failed to create inspection VPC: $INSP_VPC_ID"
        WARNINGS+=("Inspection VPC creation failed")
        INSP_VPC_ID=""
    else
        print_green "  Created inspection VPC: $INSP_VPC_ID"
        aws ec2 modify-vpc-attribute --vpc-id "$INSP_VPC_ID" --enable-dns-hostnames '{"Value":true}' --region "$REGION" 2>/dev/null
        aws ec2 modify-vpc-attribute --vpc-id "$INSP_VPC_ID" --enable-dns-support '{"Value":true}' --region "$REGION" 2>/dev/null
    fi
fi

# Inspection VPC subnets (all in AZ1): NAT/public, firewall, TGW attachment
if [ -n "$INSP_VPC_ID" ] && [ "$INSP_VPC_ID" != "None" ]; then
    INSP_NAT_SUBNET_ID=$(get_or_create_subnet "$INSP_VPC_ID" "10.98.0.0/24" "$AZ1" "goat-demo-tls-insp-nat" "")
    INSP_FW_SUBNET_ID=$(get_or_create_subnet "$INSP_VPC_ID" "10.98.1.0/24" "$AZ1" "goat-demo-tls-insp-fw" "")
    INSP_TGW_SUBNET_ID=$(get_or_create_subnet "$INSP_VPC_ID" "10.98.2.0/24" "$AZ1" "goat-demo-tls-insp-tgw" "")
fi
echo ""

# ---------------------------------------------------------------------------
# 5b. Create Internet Gateway in the INSPECTION VPC
# ---------------------------------------------------------------------------
print_magenta "--- Inspection Internet Gateway ---"

if [ -n "$INSP_VPC_ID" ] && [ "$INSP_VPC_ID" != "None" ]; then
    print_yellow "Checking for existing inspection IGW..."
    INSP_IGW_ID=$(aws ec2 describe-internet-gateways \
        --filters "Name=attachment.vpc-id,Values=$INSP_VPC_ID" \
        --query "InternetGateways[0].InternetGatewayId" --output text --region "$REGION" 2>/dev/null)

    if [ -n "$INSP_IGW_ID" ] && [ "$INSP_IGW_ID" != "None" ]; then
        print_green "  Inspection IGW already exists: $INSP_IGW_ID"
        PRE_EXISTING["inspection-igw"]="true"
    else
        print_yellow "Creating Internet Gateway in inspection VPC..."
        INSP_IGW_ID=$(aws ec2 create-internet-gateway \
            --tag-specifications "$(new_tag_spec internet-gateway goat-demo-tls-insp-igw)" \
            --query "InternetGateway.InternetGatewayId" --output text --region "$REGION" 2>&1)
        if [ $? -ne 0 ] || [ -z "$INSP_IGW_ID" ] || [ "$INSP_IGW_ID" == "None" ]; then
            print_red "  WARNING: Failed to create inspection IGW: $INSP_IGW_ID"
            WARNINGS+=("Inspection IGW creation failed")
            INSP_IGW_ID=""
        else
            aws ec2 attach-internet-gateway --internet-gateway-id "$INSP_IGW_ID" --vpc-id "$INSP_VPC_ID" --region "$REGION" 2>/dev/null
            print_green "  Created and attached inspection IGW: $INSP_IGW_ID"
        fi
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# 6. Create NAT Gateway in the INSPECTION VPC
# ---------------------------------------------------------------------------
print_magenta "--- Inspection NAT Gateway ---"

if [ -n "$INSP_NAT_SUBNET_ID" ] && [ "$INSP_NAT_SUBNET_ID" != "None" ]; then
    print_yellow "Checking for existing NAT Gateway..."
    NAT_GW_ID=$(aws ec2 describe-nat-gateways \
        --filter "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-insp-nat-gw" "Name=state,Values=available,pending" \
        --query "NatGateways[0].NatGatewayId" --output text --region "$REGION" 2>/dev/null)

    if [ -n "$NAT_GW_ID" ] && [ "$NAT_GW_ID" != "None" ]; then
        print_green "  NAT Gateway already exists: $NAT_GW_ID"
        PRE_EXISTING["nat-gateway"]="true"
    else
        print_yellow "Allocating EIP for NAT Gateway..."
        NAT_EIP_ALLOC_ID=$(aws ec2 allocate-address --domain vpc \
            --tag-specifications "$(new_tag_spec elastic-ip goat-demo-tls-insp-nat-eip)" \
            --query "AllocationId" --output text --region "$REGION" 2>&1)
        if [ $? -ne 0 ] || [ -z "$NAT_EIP_ALLOC_ID" ] || [ "$NAT_EIP_ALLOC_ID" == "None" ]; then
            print_red "  WARNING: Failed to allocate EIP: $NAT_EIP_ALLOC_ID"
            WARNINGS+=("NAT EIP allocation failed")
        else
            print_yellow "Creating NAT Gateway in inspection NAT subnet..."
            NAT_GW_ID=$(aws ec2 create-nat-gateway \
                --subnet-id "$INSP_NAT_SUBNET_ID" \
                --allocation-id "$NAT_EIP_ALLOC_ID" \
                --tag-specifications "$(new_tag_spec natgateway goat-demo-tls-insp-nat-gw)" \
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

    # NAT subnet route table: 0.0.0.0/0 -> IGW (so de-NATted egress reaches the internet)
    if [ -n "$INSP_IGW_ID" ] && [ "$INSP_IGW_ID" != "None" ]; then
        INSP_NAT_RT_ID=$(get_or_create_route_table "$INSP_VPC_ID" "goat-demo-tls-insp-nat-rt")
        aws ec2 create-route --route-table-id "$INSP_NAT_RT_ID" --destination-cidr-block 0.0.0.0/0 --gateway-id "$INSP_IGW_ID" --region "$REGION" 2>/dev/null
        aws ec2 associate-route-table --route-table-id "$INSP_NAT_RT_ID" --subnet-id "$INSP_NAT_SUBNET_ID" --region "$REGION" 2>/dev/null
        print_gray "  NAT subnet route table -> IGW"
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# 7. Create AWS Network Firewall in the INSPECTION VPC
# ---------------------------------------------------------------------------
print_magenta "--- AWS Network Firewall (inspection VPC) ---"

if [ -n "$INSP_FW_SUBNET_ID" ] && [ "$INSP_FW_SUBNET_ID" != "None" ]; then
    print_yellow "Checking for existing Network Firewall..."
    NFW_ARN=$(aws network-firewall describe-firewall \
        --firewall-name goat-demo-tls-nfw \
        --query "Firewall.FirewallArn" --output text --region "$REGION" 2>/dev/null)

    if [ -n "$NFW_ARN" ] && [ "$NFW_ARN" != "None" ]; then
        print_green "  Network Firewall already exists: $NFW_ARN"
        PRE_EXISTING["network-firewall"]="true"
    else
        print_yellow "Creating Network Firewall rule group..."

        # Check if rule group already exists
        RULE_GROUP_ARN=$(aws network-firewall describe-rule-group \
            --rule-group-name goat-demo-tls-rules --type STATEFUL \
            --query "RuleGroupResponse.RuleGroupArn" --output text --region "$REGION" 2>/dev/null)
        if [ -z "$RULE_GROUP_ARN" ] || [ "$RULE_GROUP_ARN" == "None" ]; then
            RULE_GROUP_JSON='{"RulesSource":{"RulesString":"pass tls any any -> any any (tls.sni; content:\".amazonaws.com\"; endswith; msg:\"Allow AWS services\"; sid:1; rev:1;)"},"StatefulRuleOptions":{"RuleOrder":"STRICT_ORDER"}}'
            RULE_GROUP_FILE=$(mktemp)
            echo "$RULE_GROUP_JSON" > "$RULE_GROUP_FILE"

            RULE_GROUP_ARN=$(aws network-firewall create-rule-group \
                --rule-group-name goat-demo-tls-rules \
                --type STATEFUL \
                --capacity 100 \
                --rule-group "file://$RULE_GROUP_FILE" \
                --tags Key=goat-demo,Value=true Key=goat-scenario,Value=tls-fragmentation Key=auto-delete,Value=no \
                --query "RuleGroupResponse.RuleGroupArn" --output text --region "$REGION" 2>&1)
            rm -f "$RULE_GROUP_FILE"
            if [ $? -ne 0 ] || [ -z "$RULE_GROUP_ARN" ] || [ "$RULE_GROUP_ARN" == "None" ]; then
                print_red "  WARNING: Failed to create rule group: $RULE_GROUP_ARN"
                WARNINGS+=("Network Firewall rule group creation failed")
                RULE_GROUP_ARN=""
            else
                print_gray "  Created rule group: $RULE_GROUP_ARN"
            fi
        else
            print_green "  Rule group already exists: $RULE_GROUP_ARN"
        fi

        if [ -n "$RULE_GROUP_ARN" ] && [ "$RULE_GROUP_ARN" != "None" ]; then
            # Check if firewall policy already exists
            POLICY_ARN=$(aws network-firewall describe-firewall-policy \
                --firewall-policy-name goat-demo-tls-policy \
                --query "FirewallPolicyResponse.FirewallPolicyArn" --output text --region "$REGION" 2>/dev/null)
            if [ -z "$POLICY_ARN" ] || [ "$POLICY_ARN" == "None" ]; then
                POLICY_JSON="{\"StatelessDefaultActions\":[\"aws:forward_to_sfe\"],\"StatelessFragmentDefaultActions\":[\"aws:forward_to_sfe\"],\"StatefulDefaultActions\":[\"aws:drop_established\"],\"StatefulEngineOptions\":{\"RuleOrder\":\"STRICT_ORDER\"},\"StatefulRuleGroupReferences\":[{\"ResourceArn\":\"$RULE_GROUP_ARN\",\"Priority\":1}]}"
                POLICY_FILE=$(mktemp)
                echo "$POLICY_JSON" > "$POLICY_FILE"

                POLICY_ARN=$(aws network-firewall create-firewall-policy \
                    --firewall-policy-name goat-demo-tls-policy \
                    --firewall-policy "file://$POLICY_FILE" \
                    --tags Key=goat-demo,Value=true Key=goat-scenario,Value=tls-fragmentation Key=auto-delete,Value=no \
                    --query "FirewallPolicyResponse.FirewallPolicyArn" --output text --region "$REGION" 2>&1)
                rm -f "$POLICY_FILE"
                if [ $? -ne 0 ] || [ -z "$POLICY_ARN" ] || [ "$POLICY_ARN" == "None" ]; then
                    print_red "  WARNING: Failed to create firewall policy: $POLICY_ARN"
                    WARNINGS+=("Network Firewall policy creation failed")
                    POLICY_ARN=""
                else
                    print_gray "  Created firewall policy: $POLICY_ARN"
                fi
            else
                print_green "  Firewall policy already exists: $POLICY_ARN"
            fi

            if [ -n "$POLICY_ARN" ] && [ "$POLICY_ARN" != "None" ]; then
                # Create the firewall in the INSPECTION VPC firewall subnet
                print_yellow "Creating Network Firewall..."
                NFW_ARN=$(aws network-firewall create-firewall \
                    --firewall-name goat-demo-tls-nfw \
                    --firewall-policy-arn "$POLICY_ARN" \
                    --vpc-id "$INSP_VPC_ID" \
                    --subnet-mappings "SubnetId=$INSP_FW_SUBNET_ID" \
                    --tags Key=goat-demo,Value=true Key=goat-scenario,Value=tls-fragmentation Key=Name,Value=goat-demo-tls-nfw Key=auto-delete,Value=no \
                    --query "Firewall.FirewallArn" --output text --region "$REGION" 2>&1)
                if [ $? -ne 0 ] || [ -z "$NFW_ARN" ] || [ "$NFW_ARN" == "None" ]; then
                    print_red "  WARNING: Failed to create Network Firewall: $NFW_ARN"
                    WARNINGS+=("Network Firewall creation failed")
                    NFW_ARN=""
                else
                    print_green "  Created Network Firewall: $NFW_ARN"
                    print_gray "  Waiting for firewall to become ready (this may take several minutes)..."

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
fi
echo ""

# ---------------------------------------------------------------------------
# 8. Create Transit Gateway and attach both VPCs
#
# The TGW connects the spoke VPC (workload) to the inspection VPC (firewall).
# Appliance mode is enabled on the inspection attachment for symmetric flows.
#
# CRITICAL: We wait for BOTH attachments to reach "available" AND wait for
# route table associations to reach "associated" BEFORE creating routes.
# ---------------------------------------------------------------------------
print_magenta "--- Transit Gateway ---"

print_yellow "Checking for existing Transit Gateway..."
TGW_ID=$(aws ec2 describe-transit-gateways \
    --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-tgw" "Name=state,Values=available,pending,modifying" \
    --query "TransitGateways[0].TransitGatewayId" --output text --region "$REGION" 2>/dev/null)

if [ -n "$TGW_ID" ] && [ "$TGW_ID" != "None" ]; then
    print_green "  Transit Gateway already exists: $TGW_ID"
    PRE_EXISTING["transit-gateway"]="true"
else
    print_yellow "Creating Transit Gateway (default route table association/propagation disabled)..."
    TGW_ID=$(aws ec2 create-transit-gateway \
        --description "GOAT TLS fragmentation demo inspection TGW" \
        --options "DefaultRouteTableAssociation=disable,DefaultRouteTablePropagation=disable,DnsSupport=enable" \
        --tag-specifications "$(new_tag_spec transit-gateway goat-demo-tls-tgw)" \
        --query "TransitGateway.TransitGatewayId" --output text --region "$REGION" 2>&1)
    if [ $? -ne 0 ] || [ -z "$TGW_ID" ] || [ "$TGW_ID" == "None" ]; then
        print_red "  WARNING: Failed to create Transit Gateway: $TGW_ID"
        WARNINGS+=("Transit Gateway creation failed")
        TGW_ID=""
    else
        print_green "  Created Transit Gateway: $TGW_ID (waiting for available)..."
        MAX_WAIT=300
        ELAPSED=0
        while [ $ELAPSED -lt $MAX_WAIT ]; do
            TGW_STATE=$(aws ec2 describe-transit-gateways --transit-gateway-ids "$TGW_ID" \
                --query "TransitGateways[0].State" --output text --region "$REGION" 2>/dev/null)
            if [ "$TGW_STATE" == "available" ]; then break; fi
            sleep 15
            ELAPSED=$((ELAPSED + 15))
        done
        print_green "  Transit Gateway is available"
    fi
fi

# Create the two VPC attachments + a dedicated TGW route table, then wire routing.
if [ -n "$TGW_ID" ] && [ "$TGW_ID" != "None" ]; then

    # --- Spoke attachment ---
    TGW_ATTACH_SPOKE_ID=$(aws ec2 describe-transit-gateway-attachments \
        --filters "Name=transit-gateway-id,Values=$TGW_ID" "Name=resource-id,Values=$VPC_ID" "Name=state,Values=available,pending,initiating,initiatingRequest,modifying" \
        --query "TransitGatewayAttachments[0].TransitGatewayAttachmentId" --output text --region "$REGION" 2>/dev/null)
    if [ -z "$TGW_ATTACH_SPOKE_ID" ] || [ "$TGW_ATTACH_SPOKE_ID" == "None" ]; then
        print_yellow "Creating spoke VPC attachment..."
        TGW_ATTACH_SPOKE_ID=$(aws ec2 create-transit-gateway-vpc-attachment \
            --transit-gateway-id "$TGW_ID" \
            --vpc-id "$VPC_ID" \
            --subnet-ids "$SUBNET_SPOKE_TGW_ID" \
            --tag-specifications "$(new_tag_spec transit-gateway-attachment goat-demo-tls-tgw-attach-spoke)" \
            --query "TransitGatewayVpcAttachment.TransitGatewayAttachmentId" --output text --region "$REGION" 2>&1)
        if [ $? -ne 0 ] || [ -z "$TGW_ATTACH_SPOKE_ID" ] || [ "$TGW_ATTACH_SPOKE_ID" == "None" ]; then
            print_red "  WARNING: Failed to create spoke attachment: $TGW_ATTACH_SPOKE_ID"
            WARNINGS+=("TGW spoke attachment failed")
            TGW_ATTACH_SPOKE_ID=""
        else
            print_green "  Created spoke attachment: $TGW_ATTACH_SPOKE_ID"
        fi
    else
        print_green "  Spoke attachment exists: $TGW_ATTACH_SPOKE_ID"
    fi

    # --- Inspection attachment (appliance mode ON for symmetric flows) ---
    TGW_ATTACH_INSP_ID=$(aws ec2 describe-transit-gateway-attachments \
        --filters "Name=transit-gateway-id,Values=$TGW_ID" "Name=resource-id,Values=$INSP_VPC_ID" "Name=state,Values=available,pending,initiating,initiatingRequest,modifying" \
        --query "TransitGatewayAttachments[0].TransitGatewayAttachmentId" --output text --region "$REGION" 2>/dev/null)
    if [ -z "$TGW_ATTACH_INSP_ID" ] || [ "$TGW_ATTACH_INSP_ID" == "None" ]; then
        print_yellow "Creating inspection VPC attachment (appliance mode)..."
        TGW_ATTACH_INSP_ID=$(aws ec2 create-transit-gateway-vpc-attachment \
            --transit-gateway-id "$TGW_ID" \
            --vpc-id "$INSP_VPC_ID" \
            --subnet-ids "$INSP_TGW_SUBNET_ID" \
            --options "ApplianceModeSupport=enable" \
            --tag-specifications "$(new_tag_spec transit-gateway-attachment goat-demo-tls-tgw-attach-insp)" \
            --query "TransitGatewayVpcAttachment.TransitGatewayAttachmentId" --output text --region "$REGION" 2>&1)
        if [ $? -ne 0 ] || [ -z "$TGW_ATTACH_INSP_ID" ] || [ "$TGW_ATTACH_INSP_ID" == "None" ]; then
            print_red "  WARNING: Failed to create inspection attachment: $TGW_ATTACH_INSP_ID"
            WARNINGS+=("TGW inspection attachment failed")
            TGW_ATTACH_INSP_ID=""
        else
            print_green "  Created inspection attachment: $TGW_ATTACH_INSP_ID"
        fi
    else
        print_green "  Inspection attachment exists: $TGW_ATTACH_INSP_ID"
    fi

    # Wait for BOTH attachments to become available before configuring routes
    print_gray "  Waiting for both TGW attachments to become available..."
    for att in "$TGW_ATTACH_SPOKE_ID" "$TGW_ATTACH_INSP_ID"; do
        if [ -z "$att" ] || [ "$att" == "None" ]; then continue; fi
        MAX_WAIT=180
        ELAPSED=0
        ATT_STATE=""
        while [ $ELAPSED -lt $MAX_WAIT ]; do
            ATT_STATE=$(aws ec2 describe-transit-gateway-attachments --transit-gateway-attachment-ids "$att" \
                --query "TransitGatewayAttachments[0].State" --output text --region "$REGION" 2>/dev/null)
            if [ "$ATT_STATE" == "available" ]; then break; fi
            sleep 15
            ELAPSED=$((ELAPSED + 15))
        done
        if [ "$ATT_STATE" != "available" ]; then
            print_yellow "  WARNING: Attachment $att did not reach available (state: $ATT_STATE)"
            WARNINGS+=("TGW attachment $att not available")
        fi
    done
    print_green "  Both TGW attachments are available"

    # --- TGW route table: send spoke egress to inspection, return to spoke ---
    if [ -n "$TGW_ATTACH_SPOKE_ID" ] && [ "$TGW_ATTACH_SPOKE_ID" != "None" ] && \
       [ -n "$TGW_ATTACH_INSP_ID" ] && [ "$TGW_ATTACH_INSP_ID" != "None" ]; then

        # Find existing demo TGW route table (tag-based)
        TGW_RT_ID=$(aws ec2 describe-transit-gateway-route-tables \
            --filters "Name=transit-gateway-id,Values=$TGW_ID" "Name=tag:Name,Values=goat-demo-tls-tgw-rt" "Name=state,Values=available,pending" \
            --query "TransitGatewayRouteTables[0].TransitGatewayRouteTableId" --output text --region "$REGION" 2>/dev/null)
        if [ -z "$TGW_RT_ID" ] || [ "$TGW_RT_ID" == "None" ]; then
            TGW_RT_ID=$(aws ec2 create-transit-gateway-route-table \
                --transit-gateway-id "$TGW_ID" \
                --tag-specifications "$(new_tag_spec transit-gateway-route-table goat-demo-tls-tgw-rt)" \
                --query "TransitGatewayRouteTable.TransitGatewayRouteTableId" --output text --region "$REGION" 2>/dev/null)
            if [ -z "$TGW_RT_ID" ] || [ "$TGW_RT_ID" == "None" ]; then
                print_red "  ERROR: Failed to create TGW route table"
                WARNINGS+=("TGW route table creation failed")
            else
                print_gray "  Created TGW route table: $TGW_RT_ID"
            fi
        else
            print_gray "  TGW route table exists: $TGW_RT_ID"
        fi

        # Wait for route table to be available before associating
        if [ -n "$TGW_RT_ID" ] && [ "$TGW_RT_ID" != "None" ]; then
            RT_WAIT=0
            while [ $RT_WAIT -lt 60 ]; do
                RT_STATE=$(aws ec2 describe-transit-gateway-route-tables \
                    --transit-gateway-route-table-ids "$TGW_RT_ID" \
                    --query "TransitGatewayRouteTables[0].State" --output text --region "$REGION" 2>/dev/null)
                if [ "$RT_STATE" == "available" ]; then break; fi
                sleep 5
                RT_WAIT=$((RT_WAIT + 5))
            done

            # Associate both attachments with this route table
            aws ec2 associate-transit-gateway-route-table --transit-gateway-route-table-id "$TGW_RT_ID" \
                --transit-gateway-attachment-id "$TGW_ATTACH_SPOKE_ID" --region "$REGION" 2>/dev/null
            aws ec2 associate-transit-gateway-route-table --transit-gateway-route-table-id "$TGW_RT_ID" \
                --transit-gateway-attachment-id "$TGW_ATTACH_INSP_ID" --region "$REGION" 2>/dev/null

            # CRITICAL: Wait for BOTH associations to reach "associated" state
            print_gray "  Waiting for TGW route table associations to reach 'associated'..."
            for att_to_check in "$TGW_ATTACH_SPOKE_ID" "$TGW_ATTACH_INSP_ID"; do
                ASSOC_WAIT=0
                ASSOC_STATE=""
                while [ $ASSOC_WAIT -lt 180 ]; do
                    ASSOC_STATE=$(aws ec2 get-transit-gateway-route-table-associations \
                        --transit-gateway-route-table-id "$TGW_RT_ID" \
                        --filters "Name=transit-gateway-attachment-id,Values=$att_to_check" \
                        --query "Associations[0].State" --output text --region "$REGION" 2>/dev/null)
                    if [ "$ASSOC_STATE" == "associated" ]; then break; fi
                    sleep 10
                    ASSOC_WAIT=$((ASSOC_WAIT + 10))
                done
                if [ "$ASSOC_STATE" != "associated" ]; then
                    print_yellow "  WARNING: Association for $att_to_check did not reach 'associated' (state: $ASSOC_STATE)"
                    WARNINGS+=("TGW association for $att_to_check not confirmed")
                fi
            done
            print_green "  Both associations confirmed"
        fi

        # Default route (0/0) -> inspection VPC attachment. Retry + verify.
        TGW_DEFAULT_ROUTE_OK=false
        for attempt in 1 2 3 4 5; do
            aws ec2 create-transit-gateway-route --transit-gateway-route-table-id "$TGW_RT_ID" \
                --destination-cidr-block 0.0.0.0/0 --transit-gateway-attachment-id "$TGW_ATTACH_INSP_ID" \
                --region "$REGION" 2>/dev/null
            sleep 5
            ROUTE_CHECK=$(aws ec2 search-transit-gateway-routes --transit-gateway-route-table-id "$TGW_RT_ID" \
                --filters "Name=route-search.exact-match,Values=0.0.0.0/0" "Name=state,Values=active" \
                --query "Routes[0].DestinationCidrBlock" --output text --region "$REGION" 2>/dev/null)
            if [ "$ROUTE_CHECK" == "0.0.0.0/0" ]; then TGW_DEFAULT_ROUTE_OK=true; break; fi
            print_gray "  Attempt $attempt/5: 0.0.0.0/0 route not yet active, retrying in 10s..."
            sleep 10
        done
        if [ "$TGW_DEFAULT_ROUTE_OK" != "true" ]; then
            print_red "  WARNING: TGW 0.0.0.0/0 -> inspection route not confirmed - spoke egress will fail"
            WARNINGS+=("TGW default route to inspection VPC not confirmed (instance egress will fail)")
        else
            print_green "  TGW 0.0.0.0/0 -> inspection route verified"
        fi

        # Return route: spoke CIDR -> spoke attachment
        aws ec2 create-transit-gateway-route --transit-gateway-route-table-id "$TGW_RT_ID" \
            --destination-cidr-block "$SPOKE_CIDR" --transit-gateway-attachment-id "$TGW_ATTACH_SPOKE_ID" \
            --region "$REGION" 2>/dev/null
        print_gray "  TGW routes: 0.0.0.0/0 -> inspection, $SPOKE_CIDR -> spoke"
    fi

    # --- Spoke VPC routing: private subnet -> TGW ---
    if [ -n "$SUBNET_PRIVATE_ID" ] && [ "$SUBNET_PRIVATE_ID" != "None" ]; then
        SPOKE_PRIVATE_RT_ID=$(get_or_create_route_table "$VPC_ID" "goat-demo-tls-private-rt")
        aws ec2 create-route --route-table-id "$SPOKE_PRIVATE_RT_ID" --destination-cidr-block 0.0.0.0/0 \
            --transit-gateway-id "$TGW_ID" --region "$REGION" 2>/dev/null
        aws ec2 associate-route-table --route-table-id "$SPOKE_PRIVATE_RT_ID" --subnet-id "$SUBNET_PRIVATE_ID" \
            --region "$REGION" 2>/dev/null
        print_gray "  Spoke private route table: 0.0.0.0/0 -> TGW"
    fi

    # --- Inspection VPC routing ---
    FW_ENDPOINT_ID=""
    if [ -n "$NFW_ARN" ] && [ "$NFW_ARN" != "None" ]; then
        EP_WAIT=0
        while [ $EP_WAIT -lt 240 ]; do
            FW_ENDPOINT_ID=$(aws network-firewall describe-firewall \
                --firewall-name goat-demo-tls-nfw \
                --query "values(FirewallStatus.SyncStates)[0].Attachment.EndpointId" --output text --region "$REGION" 2>/dev/null)
            if [ -n "$FW_ENDPOINT_ID" ] && [ "$FW_ENDPOINT_ID" != "None" ] && [ "$FW_ENDPOINT_ID" != "null" ]; then break; fi
            print_gray "  Waiting for firewall endpoint attachment..."
            sleep 15
            EP_WAIT=$((EP_WAIT + 15))
        done
    fi

    if [ -n "$FW_ENDPOINT_ID" ] && [ "$FW_ENDPOINT_ID" != "None" ]; then
        # TGW subnet route table: traffic arriving from spoke goes to the firewall endpoint.
        INSP_TGW_RT_ID=$(get_or_create_route_table "$INSP_VPC_ID" "goat-demo-tls-insp-tgw-rt")
        aws ec2 create-route --route-table-id "$INSP_TGW_RT_ID" --destination-cidr-block 0.0.0.0/0 \
            --vpc-endpoint-id "$FW_ENDPOINT_ID" --region "$REGION" 2>/dev/null
        aws ec2 associate-route-table --route-table-id "$INSP_TGW_RT_ID" --subnet-id "$INSP_TGW_SUBNET_ID" \
            --region "$REGION" 2>/dev/null
        print_gray "  Inspection TGW subnet route table: 0.0.0.0/0 -> firewall endpoint"

        # Firewall subnet route table: outbound -> NAT; return to spoke -> TGW.
        INSP_FW_RT_ID=$(get_or_create_route_table "$INSP_VPC_ID" "goat-demo-tls-insp-fw-rt")
        if [ -n "$NAT_GW_ID" ] && [ "$NAT_GW_ID" != "None" ]; then
            aws ec2 create-route --route-table-id "$INSP_FW_RT_ID" --destination-cidr-block 0.0.0.0/0 \
                --nat-gateway-id "$NAT_GW_ID" --region "$REGION" 2>/dev/null
        fi
        aws ec2 create-route --route-table-id "$INSP_FW_RT_ID" --destination-cidr-block "$SPOKE_CIDR" \
            --transit-gateway-id "$TGW_ID" --region "$REGION" 2>/dev/null
        aws ec2 associate-route-table --route-table-id "$INSP_FW_RT_ID" --subnet-id "$INSP_FW_SUBNET_ID" \
            --region "$REGION" 2>/dev/null
        print_gray "  Inspection firewall subnet route table: 0.0.0.0/0 -> NAT, $SPOKE_CIDR -> TGW"

        # NAT subnet route table: return traffic to spoke must go back through the firewall
        # endpoint (symmetric), everything else (0/0) to IGW (already set above).
        if [ -n "$INSP_NAT_SUBNET_ID" ] && [ "$INSP_NAT_SUBNET_ID" != "None" ]; then
            INSP_NAT_RT_ID=$(get_or_create_route_table "$INSP_VPC_ID" "goat-demo-tls-insp-nat-rt")
            aws ec2 create-route --route-table-id "$INSP_NAT_RT_ID" --destination-cidr-block "$SPOKE_CIDR" \
                --vpc-endpoint-id "$FW_ENDPOINT_ID" --region "$REGION" 2>/dev/null
            print_gray "  Inspection NAT subnet route table: $SPOKE_CIDR -> firewall endpoint (symmetric return)"
        fi
    else
        print_yellow "  WARNING: Firewall endpoint not available yet - re-run after firewall is READY to finish inspection routing"
        WARNINGS+=("Inspection routing incomplete - re-run after firewall is READY")
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# 7b. Configure Network Firewall logging (FLOW + ALERT)
# ---------------------------------------------------------------------------
if [ -n "$NFW_ARN" ] && [ "$NFW_ARN" != "None" ]; then
    print_magenta "--- Network Firewall Logging ---"

    FLOW_LOG_GROUP="/aws/network-firewall/goat-demo-tls-flow"
    ALERT_LOG_GROUP="/aws/network-firewall/goat-demo-tls-alert"
    aws logs create-log-group --log-group-name "$FLOW_LOG_GROUP" --region "$REGION" 2>/dev/null
    aws logs create-log-group --log-group-name "$ALERT_LOG_GROUP" --region "$REGION" 2>/dev/null

    # NFW requires adding log destinations one at a time
    # Step 1: Add FLOW logging
    FLOW_CONFIG_JSON="{\"LogDestinationConfigs\":[{\"LogType\":\"FLOW\",\"LogDestinationType\":\"CloudWatchLogs\",\"LogDestination\":{\"logGroup\":\"$FLOW_LOG_GROUP\"}}]}"
    LOG_CONFIG_FILE=$(mktemp)
    echo "$FLOW_CONFIG_JSON" > "$LOG_CONFIG_FILE"
    aws network-firewall update-logging-configuration \
        --firewall-name goat-demo-tls-nfw \
        --logging-configuration "file://$LOG_CONFIG_FILE" \
        --region "$REGION" >/dev/null 2>&1

    # Step 2: Add ALERT logging (include FLOW to avoid removing it)
    BOTH_CONFIG_JSON="{\"LogDestinationConfigs\":[{\"LogType\":\"FLOW\",\"LogDestinationType\":\"CloudWatchLogs\",\"LogDestination\":{\"logGroup\":\"$FLOW_LOG_GROUP\"}},{\"LogType\":\"ALERT\",\"LogDestinationType\":\"CloudWatchLogs\",\"LogDestination\":{\"logGroup\":\"$ALERT_LOG_GROUP\"}}]}"
    echo "$BOTH_CONFIG_JSON" > "$LOG_CONFIG_FILE"
    aws network-firewall update-logging-configuration \
        --firewall-name goat-demo-tls-nfw \
        --logging-configuration "file://$LOG_CONFIG_FILE" \
        --region "$REGION" >/dev/null 2>&1

    rm -f "$LOG_CONFIG_FILE"
    print_gray "  Configured FLOW + ALERT logging"
    echo ""
fi

# ---------------------------------------------------------------------------
# 9. Set firewall PERMISSIVE before launching EC2 instance
#
# The instance needs to reach SSM endpoints during bootstrap (AL2023 has the
# SSM agent installed by default with default host management). We open the
# firewall temporarily so it can register with SSM.
# ---------------------------------------------------------------------------
print_magenta "--- Firewall Permissive Mode (for EC2 bootstrap) ---"

if [ -n "$NFW_ARN" ] && [ "$NFW_ARN" != "None" ]; then
    print_yellow "Setting firewall to PERMISSIVE for instance bootstrap..."
    if set_tls_firewall_rules "permissive"; then
        print_gray "  Waiting for permissive rules to sync to the firewall..."
        wait_firewall_in_sync 150
        print_green "  Firewall is permissive"
    else
        print_yellow "  WARNING: Could not set permissive rules; instance bootstrap may have limited connectivity"
        WARNINGS+=("Could not set permissive firewall rules for instance bootstrap")
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# 10. Launch EC2 Test Instance
#
# A simple t3.micro running AL2023 with a UserData script that loops
# ML-KEM curl to ecr.<region>.amazonaws.com every 30 seconds.
# IAM instance profile with SSM access enables manual verification via
# aws ssm send-command.
# ---------------------------------------------------------------------------
print_magenta "--- EC2 Test Instance ---"

# Create IAM role + instance profile for SSM access (idempotent)
SSM_ROLE_NAME="goat-demo-tls-ssm-role"
SSM_PROFILE_NAME="goat-demo-tls-ssm-profile"
print_yellow "Ensuring IAM instance profile for SSM..."

ROLE_CHECK=$(aws iam get-role --role-name "$SSM_ROLE_NAME" --query "Role.RoleName" --output text 2>/dev/null)
if [ -z "$ROLE_CHECK" ] || [ "$ROLE_CHECK" == "None" ]; then
    TRUST_POLICY='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
    aws iam create-role --role-name "$SSM_ROLE_NAME" --assume-role-policy-document "$TRUST_POLICY" \
        --tags Key=goat-demo,Value=true Key=goat-scenario,Value=tls-fragmentation --no-cli-pager >/dev/null 2>&1
    aws iam attach-role-policy --role-name "$SSM_ROLE_NAME" \
        --policy-arn "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" 2>/dev/null
    print_green "  Created IAM role: $SSM_ROLE_NAME"
else
    print_gray "  IAM role exists: $SSM_ROLE_NAME"
fi

PROFILE_CHECK=$(aws iam get-instance-profile --instance-profile-name "$SSM_PROFILE_NAME" \
    --query "InstanceProfile.InstanceProfileName" --output text 2>/dev/null)
if [ -z "$PROFILE_CHECK" ] || [ "$PROFILE_CHECK" == "None" ]; then
    aws iam create-instance-profile --instance-profile-name "$SSM_PROFILE_NAME" \
        --tags Key=goat-demo,Value=true Key=goat-scenario,Value=tls-fragmentation >/dev/null 2>&1
    aws iam add-role-to-instance-profile --instance-profile-name "$SSM_PROFILE_NAME" \
        --role-name "$SSM_ROLE_NAME" 2>/dev/null
    # Wait for instance profile to propagate (IAM is eventually consistent)
    sleep 10
    print_green "  Created instance profile: $SSM_PROFILE_NAME"
else
    print_gray "  Instance profile exists: $SSM_PROFILE_NAME"
fi

if [ -n "$SUBNET_PRIVATE_ID" ] && [ "$SUBNET_PRIVATE_ID" != "None" ]; then
    # Check if instance already exists
    print_yellow "Checking for existing test instance..."
    INSTANCE_ID=$(aws ec2 describe-instances \
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-test-instance" "Name=instance-state-name,Values=running,pending" \
        --query "Reservations[0].Instances[0].InstanceId" --output text --region "$REGION" 2>/dev/null)

    if [ -n "$INSTANCE_ID" ] && [ "$INSTANCE_ID" != "None" ]; then
        print_green "  Test instance already exists: $INSTANCE_ID"
        PRE_EXISTING["ec2-instance"]="true"
    else
        # Resolve latest AL2023 AMI via SSM parameter
        print_yellow "Resolving latest AL2023 AMI..."
        AMI_ID=$(aws ssm get-parameter \
            --name "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64" \
            --query "Parameter.Value" --output text --region "$REGION" 2>/dev/null)
        if [ -z "$AMI_ID" ] || [ "$AMI_ID" == "None" ]; then
            print_red "  WARNING: Could not resolve AL2023 AMI"
            WARNINGS+=("AL2023 AMI resolution failed")
        else
            print_green "  AL2023 AMI: $AMI_ID"

            # Create security group (allow all egress, no ingress needed)
            SG_NAME="goat-demo-tls-test-sg"
            SG_ID=$(aws ec2 describe-security-groups \
                --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=$SG_NAME" \
                --query "SecurityGroups[0].GroupId" --output text --region "$REGION" 2>/dev/null)
            if [ -z "$SG_ID" ] || [ "$SG_ID" == "None" ]; then
                print_yellow "  Creating security group..."
                SG_ID=$(aws ec2 create-security-group \
                    --group-name "$SG_NAME" \
                    --description "GOAT TLS demo - allow all egress for ECR TLS test" \
                    --vpc-id "$VPC_ID" \
                    --tag-specifications "$(new_tag_spec security-group "$SG_NAME")" \
                    --query "GroupId" --output text --region "$REGION" 2>&1)
                if [ $? -ne 0 ] || [ -z "$SG_ID" ] || [ "$SG_ID" == "None" ]; then
                    print_red "  WARNING: Failed to create security group: $SG_ID"
                    WARNINGS+=("Security group creation failed")
                    SG_ID=""
                else
                    print_green "  Created security group: $SG_ID"
                fi
            else
                print_green "  Security group exists: $SG_ID"
            fi

            # Build UserData script — creates a systemd service for the curl loop
            USER_DATA_SCRIPT='#!/bin/bash
# Wait for network to be ready
sleep 10

# Create the curl script
cat > /usr/local/bin/goat-tls-curl.sh << '\''SCRIPT'\''
#!/bin/bash
REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region 2>/dev/null || echo "us-east-1")
while true; do
  curl --curves X25519MLKEM768:X25519 -sS -o /dev/null -w "[%{time_total}s] HTTP %{http_code}\n" "https://ecr.${REGION}.amazonaws.com/" 2>&1 || echo "Connection failed"
  sleep 20
done
SCRIPT
chmod +x /usr/local/bin/goat-tls-curl.sh

# Create systemd service
cat > /etc/systemd/system/goat-tls-curl.service << '\''UNIT'\''
[Unit]
Description=G.O.A.T. TLS Fragmentation Demo - ML-KEM curl loop
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/goat-tls-curl.sh
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

# Enable and start
systemctl daemon-reload
systemctl enable goat-tls-curl.service
systemctl start goat-tls-curl.service

echo "BOOTSTRAP_COMPLETE: AL2023 TLS fragmentation test instance ready (curl loop every 20s)"
'
            # Base64 encode the UserData
            USER_DATA_B64=$(echo "$USER_DATA_SCRIPT" | base64 -w 0 2>/dev/null || echo "$USER_DATA_SCRIPT" | base64 2>/dev/null)

            # Launch instance
            if [ -n "$SG_ID" ] && [ "$SG_ID" != "None" ]; then
                print_yellow "Launching EC2 test instance (t3.micro, AL2023)..."
                INSTANCE_ID=$(aws ec2 run-instances \
                    --image-id "$AMI_ID" \
                    --instance-type t3.micro \
                    --subnet-id "$SUBNET_PRIVATE_ID" \
                    --security-group-ids "$SG_ID" \
                    --iam-instance-profile "Name=$SSM_PROFILE_NAME" \
                    --user-data "$USER_DATA_B64" \
                    --tag-specifications "ResourceType=instance,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=tls-fragmentation},{Key=Name,Value=goat-demo-tls-test-instance},{Key=auto-delete,Value=no},{Key=goat-network-capture-allowed,Value=true}]" "ResourceType=network-interface,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=tls-fragmentation},{Key=Name,Value=goat-demo-tls-test-eni},{Key=auto-delete,Value=no},{Key=goat-network-capture-allowed,Value=true}]" \
                    --query "Instances[0].InstanceId" --output text --region "$REGION" 2>&1)
                if [ $? -ne 0 ] || [ -z "$INSTANCE_ID" ] || [ "$INSTANCE_ID" == "None" ]; then
                    print_red "  WARNING: Failed to launch instance: $INSTANCE_ID"
                    WARNINGS+=("EC2 instance launch failed")
                    INSTANCE_ID=""
                else
                    print_green "  Launched instance: $INSTANCE_ID"
                fi
            else
                print_red "  WARNING: No security group available, skipping instance launch"
                WARNINGS+=("EC2 instance launch skipped (no security group)")
            fi
        fi
    fi

    # Wait for instance to reach running state
    if [ -n "$INSTANCE_ID" ] && [ "$INSTANCE_ID" != "None" ] && [ "${PRE_EXISTING[ec2-instance]:-}" != "true" ]; then
        print_gray "  Waiting for instance to reach 'running' state..."
        aws ec2 wait instance-running --instance-ids "$INSTANCE_ID" --region "$REGION" 2>/dev/null
        print_green "  Instance is running"
    fi

    # Get the ENI ID for the instance (for packet captures)
    if [ -n "$INSTANCE_ID" ] && [ "$INSTANCE_ID" != "None" ]; then
        INSTANCE_ENI_ID=$(aws ec2 describe-instances \
            --instance-ids "$INSTANCE_ID" \
            --query "Reservations[0].Instances[0].NetworkInterfaces[0].NetworkInterfaceId" \
            --output text --region "$REGION" 2>/dev/null)
        if [ -n "$INSTANCE_ENI_ID" ] && [ "$INSTANCE_ENI_ID" != "None" ]; then
            print_green "  Instance ENI: $INSTANCE_ENI_ID"
            # Ensure ENI is tagged (in case instance was pre-existing)
            aws ec2 create-tags --resources "$INSTANCE_ENI_ID" \
                --tags Key=goat-network-capture-allowed,Value=true Key=goat-demo,Value=true Key=goat-scenario,Value=tls-fragmentation \
                --region "$REGION" 2>/dev/null
        fi
    fi
else
    print_yellow "  Skipping EC2 instance (no private subnet available)"
fi
echo ""

# ---------------------------------------------------------------------------
# 11. Wait for instance to be reachable via SSM
#
# AL2023 has the SSM agent installed by default and uses Default Host
# Management Configuration (DHMC) to register with SSM automatically.
# We wait up to 3 minutes for the instance to appear in SSM.
# ---------------------------------------------------------------------------
print_magenta "--- Verifying Instance Reachability (SSM) ---"

INSTANCE_REACHABLE=false
if [ -n "$INSTANCE_ID" ] && [ "$INSTANCE_ID" != "None" ]; then
    print_gray "  Waiting for instance to register with SSM (up to 3 min)..."
    SSM_WAIT=0
    while [ $SSM_WAIT -lt 180 ]; do
        SSM_STATUS=$(aws ssm describe-instance-information \
            --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
            --query "InstanceInformationList[0].PingStatus" --output text --region "$REGION" 2>/dev/null)
        if [ "$SSM_STATUS" == "Online" ]; then
            INSTANCE_REACHABLE=true
            break
        fi
        sleep 15
        SSM_WAIT=$((SSM_WAIT + 15))
    done
    if [ "$INSTANCE_REACHABLE" == "true" ]; then
        print_green "  Instance is online in SSM"
    else
        print_yellow "  Instance not yet online in SSM (may need Default Host Management enabled)"
        print_yellow "  The instance will still run the curl loop via UserData regardless of SSM status"
        WARNINGS+=("Instance not confirmed in SSM - UserData still running")
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# 12. Restore firewall STRICT rules
#
# Now that the instance is running (and has completed its initial network
# setup), restore the strict demo firewall rules so the ML-KEM Client Hello
# to ECR is dropped - reproducing the failure.
# ---------------------------------------------------------------------------
print_magenta "--- Restoring Firewall STRICT Rules ---"

if [ -n "$NFW_ARN" ] && [ "$NFW_ARN" != "None" ]; then
    print_yellow "Restoring STRICT firewall rules (ML-KEM Client Hello will now be dropped)..."
    if set_tls_firewall_rules "strict"; then
        print_gray "  Waiting for strict rules to sync to the firewall..."
        wait_firewall_in_sync 150
        print_green "  Firewall restored to strict demo configuration"
    else
        print_red "  WARNING: Could not restore strict rules"
        WARNINGS+=("Firewall left in permissive state - restore strict rules manually")
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# 13. Create Support case (if Support plan is active)
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

    CASE_BODY="Our EC2 instance running Amazon Linux 2023 in $REGION is failing to establish HTTPS connections to ECR (endpoint: ecr.$REGION.amazonaws.com on port 443). The error is 'connection reset by peer' during the TLS handshake. This started after the latest AL2023 update that upgraded OpenSSL to 3.5.5. We suspect the new ML-KEM (Kyber-768) key-share is producing oversized TLS Client Hello messages (~3.5 KB) that are being fragmented across multiple TCP segments. Our AWS Network Firewall (goat-demo-tls-nfw) uses the legacy 'drop established' default action with pass rules for *.amazonaws.com domains - we believe the firewall cannot extract the SNI from the fragmented Client Hello and is dropping the connection. Affected resources: EC2 instance $INSTANCE_ID, VPC $VPC_ID (10.99.0.0/16, name: goat-demo-vpc), Network Firewall goat-demo-tls-nfw, destination ecr.$REGION.amazonaws.com:443, source port ephemeral. Account $ACCOUNT_ID, region $REGION. This case was created by the G.O.A.T. demo provisioning scripts for demonstration purposes."

    SUPPORT_CASE_ID=$(aws support create-case \
        --subject "EC2 instance failing HTTPS to ECR - connection reset by peer in $REGION" \
        --communication-body "$CASE_BODY" \
        --service-code "amazon-elastic-compute-cloud-linux" \
        --category-code "other" \
        --severity-code "high" \
        --language "en" \
        --query "caseId" --output text --region us-east-1 2>&1)

    if [ $? -ne 0 ] || [ -z "$SUPPORT_CASE_ID" ] || [ "$SUPPORT_CASE_ID" == "None" ]; then
        print_red "  WARNING: Failed to create Support case: $SUPPORT_CASE_ID"
        WARNINGS+=("Support case creation failed")
        SUPPORT_CASE_ID=""
    else
        print_green "  Created Support case: $SUPPORT_CASE_ID"

        # Add a follow-up communication with additional diagnostic details
        FOLLOW_UP="Additional details: We captured traffic using VPC Traffic Mirroring on VPC $VPC_ID and confirmed the TLS Client Hello is 3,547 bytes (fragmented into 3 TCP segments). The Network Firewall goat-demo-tls-nfw sends a TCP RST from its own ENI (source IP does not match either endpoint - the RST originates from the firewall's endpoint in the inspection VPC). The RST arrives immediately after the fragmented Client Hello, before ecr.$REGION.amazonaws.com responds. We believe this matches the known issue with AWS Network Firewall stateful rule groups using 'drop established' default action failing to inspect SNI in fragmented TLS records. The instance is running AL2023 with OpenSSL 3.5.5 (ML-KEM enabled by default). Instance ENI: $INSTANCE_ENI_ID. Workaround under evaluation: switch to 'aws:drop_strict' with 'flow:to_server, flow:established' qualifiers. This case was created automatically by the G.O.A.T. demo scripts - no action needed from AWS Support."
        aws support add-communication-to-case \
            --case-id "$SUPPORT_CASE_ID" \
            --communication-body "$FOLLOW_UP" \
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
# 14. Summary
# ---------------------------------------------------------------------------
echo ""
print_green "===== TLS FRAGMENTATION SCENARIO SUMMARY START ====="

if [ -n "$VPC_ID" ] && [ "$VPC_ID" != "None" ]; then
    print_cyan "$(get_summary_prefix vpc)spoke-vpc: $VPC_ID"
fi
if [ -n "$SUBNET_PRIVATE_ID" ] && [ "$SUBNET_PRIVATE_ID" != "None" ]; then
    print_cyan "$(get_summary_prefix subnet-private)spoke-private-subnet: $SUBNET_PRIVATE_ID"
fi
if [ -n "$SUBNET_SPOKE_TGW_ID" ] && [ "$SUBNET_SPOKE_TGW_ID" != "None" ]; then
    print_cyan "$(get_summary_prefix subnet-spoke-tgw)spoke-tgw-subnet: $SUBNET_SPOKE_TGW_ID"
fi
if [ -n "$INSP_VPC_ID" ] && [ "$INSP_VPC_ID" != "None" ]; then
    print_cyan "$(get_summary_prefix inspection-vpc)inspection-vpc: $INSP_VPC_ID"
fi
if [ -n "$TGW_ID" ] && [ "$TGW_ID" != "None" ]; then
    print_cyan "$(get_summary_prefix transit-gateway)transit-gateway: $TGW_ID"
fi
if [ -n "$INSP_IGW_ID" ] && [ "$INSP_IGW_ID" != "None" ]; then
    print_cyan "$(get_summary_prefix inspection-igw)inspection-igw: $INSP_IGW_ID"
fi
if [ -n "$NAT_GW_ID" ] && [ "$NAT_GW_ID" != "None" ]; then
    print_cyan "$(get_summary_prefix nat-gateway)nat-gateway: $NAT_GW_ID"
fi
if [ -n "$NFW_ARN" ] && [ "$NFW_ARN" != "None" ]; then
    print_cyan "$(get_summary_prefix network-firewall)network-firewall: $NFW_ARN"
fi
if [ -n "$INSTANCE_ID" ] && [ "$INSTANCE_ID" != "None" ]; then
    print_cyan "$(get_summary_prefix ec2-instance)ec2-instance: $INSTANCE_ID"
fi
if [ -n "$INSTANCE_ENI_ID" ] && [ "$INSTANCE_ENI_ID" != "None" ]; then
    print_cyan "ec2-instance-eni: $INSTANCE_ENI_ID"
fi
if [ -n "$SUPPORT_CASE_ID" ] && [ "$SUPPORT_CASE_ID" != "skipped (no Support plan)" ]; then
    print_cyan "support-case: $SUPPORT_CASE_ID"
elif [ "$SUPPORT_CASE_ID" == "skipped (no Support plan)" ]; then
    print_cyan "support-case: skipped (no Support plan)"
fi

echo ""
if [ -n "$INSTANCE_ENI_ID" ] && [ "$INSTANCE_ENI_ID" != "None" ]; then
    print_cyan "suggested-query: Capture traffic from $INSTANCE_ENI_ID"
    print_cyan "suggested-query-2: Why is the EC2 instance failing to connect to ECR? Capture traffic from $INSTANCE_ENI_ID and analyze the TLS handshake"
fi
if [ -n "$SUPPORT_CASE_ID" ] && [ "$SUPPORT_CASE_ID" != "skipped (no Support plan)" ] && [ -n "$SUPPORT_CASE_ID" ]; then
    print_cyan "suggested-query-3: Investigate the network problem described in support case $SUPPORT_CASE_ID and capture traffic if relevant"
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
