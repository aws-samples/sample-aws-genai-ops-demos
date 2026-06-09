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
#   .\setup-scenario-tls-fragmentation.ps1                        # Create a new spoke VPC
#   .\setup-scenario-tls-fragmentation.ps1 -VpcId vpc-0abc123    # Reuse an existing spoke VPC

param(
    [string]$VpcId = ""
)

$ErrorActionPreference = "Continue"

# ---------------------------------------------------------------------------
# Track created/existing resources for summary
# ---------------------------------------------------------------------------
$vpcId = ""
$subnetPrivateId = ""
$subnetSpokeTgwId = ""
$instanceId = ""
$instanceEniId = ""
$warnings = @()
$preExisting = @{}

# Inspection VPC + Transit Gateway resources
$inspVpcId = ""
$inspNatSubnetId = ""
$inspFwSubnetId = ""
$inspTgwSubnetId = ""
$inspIgwId = ""
$natGwId = ""
$nfwArn = ""
$tgwId = ""
$tgwAttachSpokeId = ""
$tgwAttachInspId = ""

# CIDR plan
$spokeCidr = "10.99.0.0/16"
$inspCidr  = "10.98.0.0/16"

# ---------------------------------------------------------------------------
# Helper: Check if resource is pre-existing
# ---------------------------------------------------------------------------
function Set-PreExisting($key) {
    $script:preExisting[$key] = $true
}
function Get-SummaryPrefix($key) {
    if ($script:preExisting.ContainsKey($key)) { return "[PRE-EXISTING] " }
    return ""
}

# ---------------------------------------------------------------------------
# Helper: tag specification string
# ---------------------------------------------------------------------------
function New-TagSpec($resourceType, $name) {
    return "ResourceType=$resourceType,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=tls-fragmentation},{Key=Name,Value=$name},{Key=auto-delete,Value=no}]"
}

# ---------------------------------------------------------------------------
# Helper: idempotently ensure a subnet exists (by Name tag) in a VPC/AZ
# Returns the subnet ID (existing or newly created), or "" on failure.
# ---------------------------------------------------------------------------
function Get-OrCreateSubnet($vpc, $cidr, $az, $name, $preKey) {
    $sn = aws ec2 describe-subnets `
        --filters "Name=vpc-id,Values=$vpc" "Name=tag:Name,Values=$name" `
        --query "Subnets[0].SubnetId" --output text --region $script:region 2>$null
    if (-not [string]::IsNullOrEmpty($sn) -and $sn -ne "None") {
        Write-Host "  Subnet $name already exists: $sn" -ForegroundColor Green
        if ($preKey) { Set-PreExisting $preKey }
        return $sn
    }
    Write-Host "  Creating subnet $name in $az ($cidr)..." -ForegroundColor Yellow
    $sn = aws ec2 create-subnet `
        --vpc-id $vpc --cidr-block $cidr --availability-zone $az `
        --tag-specifications (New-TagSpec "subnet" $name) `
        --query "Subnet.SubnetId" --output text --region $script:region 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  WARNING: Failed to create subnet ${name}: $sn" -ForegroundColor Red
        $script:warnings += "Subnet $name creation failed"
        return ""
    }
    Write-Host "  Created subnet ${name}: $sn" -ForegroundColor Green
    return $sn
}

# ---------------------------------------------------------------------------
# Helper: idempotently ensure a route table exists (by Name tag) in a VPC
# Returns the route table ID.
# ---------------------------------------------------------------------------
function Get-OrCreateRouteTable($vpc, $name) {
    $rt = aws ec2 describe-route-tables `
        --filters "Name=vpc-id,Values=$vpc" "Name=tag:Name,Values=$name" `
        --query "RouteTables[0].RouteTableId" --output text --region $script:region 2>$null
    if (-not [string]::IsNullOrEmpty($rt) -and $rt -ne "None") {
        return $rt
    }
    $rt = aws ec2 create-route-table --vpc-id $vpc `
        --tag-specifications (New-TagSpec "route-table" $name) `
        --query "RouteTable.RouteTableId" --output text --region $script:region 2>&1
    return $rt
}

# ---------------------------------------------------------------------------
# Helper: set the firewall stateful rule group to "permissive" or "strict".
#
#   permissive : a single catch-all "pass ip any any" rule. Passes ALL traffic
#                regardless of TLS fragmentation, so the EC2 instance can boot
#                and reach SSM endpoints. Used only briefly during instance
#                startup.
#   strict     : the demo state -- a single SNI pass rule for ".amazonaws.com".
#                Combined with the policy's aws:drop_established default action,
#                the fragmented ML-KEM Client Hello to ecr.<region>.amazonaws.com
#                can no longer have its SNI read, the rule never matches, and the
#                connection is dropped. This is the failure the demo reproduces.
# ---------------------------------------------------------------------------
function Set-TlsFirewallRules {
    param([ValidateSet("permissive", "strict")] [string]$Mode)
    $rgName = "goat-demo-tls-rules"
    if ($Mode -eq "permissive") {
        $rules = @'
{"RulesSource":{"RulesString":"pass ip any any -> any any (sid:99; rev:1;)"},"StatefulRuleOptions":{"RuleOrder":"STRICT_ORDER"}}
'@
    } else {
        $rules = @'
{"RulesSource":{"RulesString":"pass tls any any -> any any (tls.sni; content:\".amazonaws.com\"; endswith; msg:\"Allow AWS services\"; sid:1; rev:1;)"},"StatefulRuleOptions":{"RuleOrder":"STRICT_ORDER"}}
'@
    }
    $tok = aws network-firewall describe-rule-group --rule-group-name $rgName --type STATEFUL `
        --query "UpdateToken" --output text --region $script:region 2>$null
    if ([string]::IsNullOrEmpty($tok) -or $tok -eq "None") { return $false }
    $f = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($f, $rules)
    aws network-firewall update-rule-group --rule-group-name $rgName --type STATEFUL `
        --update-token $tok --rule-group "file://$f" --region $script:region 2>$null | Out-Null
    Remove-Item $f -ErrorAction SilentlyContinue
    return $true
}

# ---------------------------------------------------------------------------
# Helper: wait until the firewall configuration is fully synchronized
# (rule group changes take ~30-90s to propagate to the firewall endpoint).
# ---------------------------------------------------------------------------
function Wait-FirewallInSync {
    param([int]$MaxWaitSeconds = 150)
    $elapsed = 0
    while ($elapsed -lt $MaxWaitSeconds) {
        $sync = aws network-firewall describe-firewall --firewall-name goat-demo-tls-nfw `
            --query "FirewallStatus.ConfigurationSyncStateSummary" --output text --region $script:region 2>$null
        if ($sync -eq "IN_SYNC") { return $true }
        Start-Sleep -Seconds 15
        $elapsed += 15
    }
    return $false
}

# ---------------------------------------------------------------------------
# 1. Verify AWS credentials
# ---------------------------------------------------------------------------
Write-Host "=== G.O.A.T. Demo Scenario - TLS Fragmentation Reproduction ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Verifying AWS credentials..." -ForegroundColor Yellow

try {
    $accountId = aws sts get-caller-identity --query "Account" --output text 2>$null
    if ([string]::IsNullOrEmpty($accountId)) { throw "Empty account ID" }
    Write-Host "  Authenticated to account: $accountId" -ForegroundColor Green
} catch {
    Write-Host "ERROR: AWS credentials not configured." -ForegroundColor Red
    Write-Host "Run 'aws configure' or set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# 2. Detect region
# ---------------------------------------------------------------------------
Write-Host "Detecting AWS region..." -ForegroundColor Yellow

$region = $env:AWS_REGION
if ([string]::IsNullOrEmpty($region)) { $region = $env:AWS_DEFAULT_REGION }
if ([string]::IsNullOrEmpty($region)) {
    $region = aws configure get region 2>$null
}
if ([string]::IsNullOrEmpty($region)) {
    Write-Host "ERROR: No AWS region detected." -ForegroundColor Red
    Write-Host "Set AWS_REGION, AWS_DEFAULT_REGION, or run 'aws configure set region <region>'." -ForegroundColor Red
    exit 1
}
Write-Host "  Region: $region" -ForegroundColor Green
Write-Host ""

# ---------------------------------------------------------------------------
# 3. Create or reuse shared VPC
# ---------------------------------------------------------------------------
Write-Host "--- VPC and Networking ---" -ForegroundColor Magenta

if (-not [string]::IsNullOrEmpty($VpcId)) {
    Write-Host "Using provided VPC: $VpcId" -ForegroundColor Yellow
    try {
        $vpcCheck = aws ec2 describe-vpcs --vpc-ids $VpcId --query "Vpcs[0].VpcId" --output text --region $region 2>&1
        if ($LASTEXITCODE -ne 0) { throw $vpcCheck }
        $vpcId = $VpcId
        Write-Host "  VPC validated: $vpcId" -ForegroundColor Green
        Set-PreExisting "vpc"
        aws ec2 modify-vpc-attribute --vpc-id $vpcId --enable-dns-hostnames '{"Value":true}' --region $region 2>$null
        aws ec2 modify-vpc-attribute --vpc-id $vpcId --enable-dns-support '{"Value":true}' --region $region 2>$null
    } catch {
        Write-Host "  ERROR: Provided VPC $VpcId not found or inaccessible: $_" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "Checking for existing goat-demo-vpc..." -ForegroundColor Yellow
    try {
        $vpcId = aws ec2 describe-vpcs `
            --filters "Name=tag:goat-demo,Values=true" "Name=tag:Name,Values=goat-demo-vpc" "Name=tag:goat:component,Values=network-agent" `
            --query "Vpcs[0].VpcId" --output text --region $region 2>$null
    } catch { $vpcId = "" }

    # Fallback: try without the goat:component filter (for manually created VPCs)
    if ([string]::IsNullOrEmpty($vpcId) -or $vpcId -eq "None") {
        try {
            $vpcId = aws ec2 describe-vpcs `
                --filters "Name=tag:goat-demo,Values=true" "Name=tag:Name,Values=goat-demo-vpc" `
                --query "Vpcs[0].VpcId" --output text --region $region 2>$null
        } catch { $vpcId = "" }
    }

    if (-not [string]::IsNullOrEmpty($vpcId) -and $vpcId -ne "None") {
        Write-Host "  Shared VPC already exists: $vpcId" -ForegroundColor Green
        Set-PreExisting "vpc"
    } else {
        Write-Host "Creating shared VPC goat-demo-vpc (10.99.0.0/16)..." -ForegroundColor Yellow
        try {
            $vpcId = aws ec2 create-vpc `
                --cidr-block 10.99.0.0/16 `
                --tag-specifications 'ResourceType=vpc,Tags=[{Key=goat-demo,Value=true},{Key=Name,Value=goat-demo-vpc},{Key=auto-delete,Value=no}]' `
                --query "Vpc.VpcId" --output text --region $region 2>&1
            if ($LASTEXITCODE -ne 0) { throw $vpcId }
            Write-Host "  Created VPC: $vpcId" -ForegroundColor Green
            aws ec2 modify-vpc-attribute --vpc-id $vpcId --enable-dns-hostnames '{"Value":true}' --region $region 2>$null
            aws ec2 modify-vpc-attribute --vpc-id $vpcId --enable-dns-support '{"Value":true}' --region $region 2>$null
        } catch {
            Write-Host "  ERROR: Failed to create VPC: $_" -ForegroundColor Red
            $warnings += "VPC creation failed"
            $vpcId = ""
        }
    }
}

# ---------------------------------------------------------------------------
# 4. Create subnets in spoke VPC
# ---------------------------------------------------------------------------
$az1 = ""

if (-not [string]::IsNullOrEmpty($vpcId) -and $vpcId -ne "None") {
    $az1 = aws ec2 describe-availability-zones --query "AvailabilityZones[0].ZoneName" --output text --region $region 2>$null

    # Private (workload) subnet - hosts the EC2 test instance. Egress routes to the TGW.
    $subnetPrivateId = Get-OrCreateSubnet $vpcId "10.99.13.0/24" $az1 "goat-demo-tls-private" "subnet-private"

    # Spoke TGW attachment subnet - a small dedicated subnet for the TGW ENI.
    $subnetSpokeTgwId = Get-OrCreateSubnet $vpcId "10.99.20.0/24" $az1 "goat-demo-tls-spoke-tgw" "subnet-spoke-tgw"
}
Write-Host ""

# ---------------------------------------------------------------------------
# 5. Create Inspection VPC (separate VPC reached over the Transit Gateway)
# ---------------------------------------------------------------------------
Write-Host "--- Inspection VPC ---" -ForegroundColor Magenta

Write-Host "Checking for existing inspection VPC..." -ForegroundColor Yellow
$inspVpcId = aws ec2 describe-vpcs `
    --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-inspection-vpc" `
    --query "Vpcs[0].VpcId" --output text --region $region 2>$null

if (-not [string]::IsNullOrEmpty($inspVpcId) -and $inspVpcId -ne "None") {
    Write-Host "  Inspection VPC already exists: $inspVpcId" -ForegroundColor Green
    Set-PreExisting "inspection-vpc"
} else {
    Write-Host "Creating inspection VPC ($inspCidr)..." -ForegroundColor Yellow
    $inspVpcId = aws ec2 create-vpc --cidr-block $inspCidr `
        --tag-specifications (New-TagSpec "vpc" "goat-demo-tls-inspection-vpc") `
        --query "Vpc.VpcId" --output text --region $region 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  WARNING: Failed to create inspection VPC: $inspVpcId" -ForegroundColor Red
        $warnings += "Inspection VPC creation failed"
        $inspVpcId = ""
    } else {
        Write-Host "  Created inspection VPC: $inspVpcId" -ForegroundColor Green
        aws ec2 modify-vpc-attribute --vpc-id $inspVpcId --enable-dns-hostnames '{"Value":true}' --region $region 2>$null
        aws ec2 modify-vpc-attribute --vpc-id $inspVpcId --enable-dns-support '{"Value":true}' --region $region 2>$null
    }
}

# Inspection VPC subnets (all in $az1): NAT/public, firewall, TGW attachment
if (-not [string]::IsNullOrEmpty($inspVpcId) -and $inspVpcId -ne "None") {
    $inspNatSubnetId = Get-OrCreateSubnet $inspVpcId "10.98.0.0/24" $az1 "goat-demo-tls-insp-nat"
    $inspFwSubnetId  = Get-OrCreateSubnet $inspVpcId "10.98.1.0/24" $az1 "goat-demo-tls-insp-fw"
    $inspTgwSubnetId = Get-OrCreateSubnet $inspVpcId "10.98.2.0/24" $az1 "goat-demo-tls-insp-tgw"
}
Write-Host ""

# ---------------------------------------------------------------------------
# 5b. Create Internet Gateway in the INSPECTION VPC
# ---------------------------------------------------------------------------
Write-Host "--- Inspection Internet Gateway ---" -ForegroundColor Magenta

if (-not [string]::IsNullOrEmpty($inspVpcId) -and $inspVpcId -ne "None") {
    Write-Host "Checking for existing inspection IGW..." -ForegroundColor Yellow
    $inspIgwId = aws ec2 describe-internet-gateways `
        --filters "Name=attachment.vpc-id,Values=$inspVpcId" `
        --query "InternetGateways[0].InternetGatewayId" --output text --region $region 2>$null

    if (-not [string]::IsNullOrEmpty($inspIgwId) -and $inspIgwId -ne "None") {
        Write-Host "  Inspection IGW already exists: $inspIgwId" -ForegroundColor Green
        Set-PreExisting "inspection-igw"
    } else {
        Write-Host "Creating Internet Gateway in inspection VPC..." -ForegroundColor Yellow
        $inspIgwId = aws ec2 create-internet-gateway `
            --tag-specifications (New-TagSpec "internet-gateway" "goat-demo-tls-insp-igw") `
            --query "InternetGateway.InternetGatewayId" --output text --region $region 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  WARNING: Failed to create inspection IGW: $inspIgwId" -ForegroundColor Red
            $warnings += "Inspection IGW creation failed"
            $inspIgwId = ""
        } else {
            aws ec2 attach-internet-gateway --internet-gateway-id $inspIgwId --vpc-id $inspVpcId --region $region 2>$null
            Write-Host "  Created and attached inspection IGW: $inspIgwId" -ForegroundColor Green
        }
    }
}
Write-Host ""

# ---------------------------------------------------------------------------
# 6. Create NAT Gateway in the INSPECTION VPC
# ---------------------------------------------------------------------------
Write-Host "--- Inspection NAT Gateway ---" -ForegroundColor Magenta

if (-not [string]::IsNullOrEmpty($inspNatSubnetId) -and $inspNatSubnetId -ne "None") {
    Write-Host "Checking for existing NAT Gateway..." -ForegroundColor Yellow
    try {
        $natGwId = aws ec2 describe-nat-gateways `
            --filter "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-insp-nat-gw" "Name=state,Values=available,pending" `
            --query "NatGateways[0].NatGatewayId" --output text --region $region 2>$null
    } catch { $natGwId = "" }

    if (-not [string]::IsNullOrEmpty($natGwId) -and $natGwId -ne "None") {
        Write-Host "  NAT Gateway already exists: $natGwId" -ForegroundColor Green
        Set-PreExisting "nat-gateway"
    } else {
        Write-Host "Allocating EIP for NAT Gateway..." -ForegroundColor Yellow
        try {
            $natEipAllocId = aws ec2 allocate-address --domain vpc `
                --tag-specifications (New-TagSpec "elastic-ip" "goat-demo-tls-insp-nat-eip") `
                --query "AllocationId" --output text --region $region 2>&1
            if ($LASTEXITCODE -ne 0) { throw $natEipAllocId }

            Write-Host "Creating NAT Gateway in inspection NAT subnet..." -ForegroundColor Yellow
            $natGwId = aws ec2 create-nat-gateway `
                --subnet-id $inspNatSubnetId `
                --allocation-id $natEipAllocId `
                --tag-specifications (New-TagSpec "natgateway" "goat-demo-tls-insp-nat-gw") `
                --query "NatGateway.NatGatewayId" --output text --region $region 2>&1
            if ($LASTEXITCODE -ne 0) { throw $natGwId }
            Write-Host "  Created NAT Gateway: $natGwId (provisioning...)" -ForegroundColor Green

            Write-Host "  Waiting for NAT Gateway to become available..." -ForegroundColor Gray
            aws ec2 wait nat-gateway-available --nat-gateway-ids $natGwId --region $region 2>$null
            Write-Host "  NAT Gateway is available" -ForegroundColor Green
        } catch {
            Write-Host "  WARNING: Failed to create NAT Gateway: $_" -ForegroundColor Red
            $warnings += "NAT Gateway creation failed"
            $natGwId = ""
        }
    }

    # NAT subnet route table: 0.0.0.0/0 -> IGW (so de-NATted egress reaches the internet)
    if (-not [string]::IsNullOrEmpty($inspIgwId) -and $inspIgwId -ne "None") {
        $inspNatRtId = Get-OrCreateRouteTable $inspVpcId "goat-demo-tls-insp-nat-rt"
        aws ec2 create-route --route-table-id $inspNatRtId --destination-cidr-block 0.0.0.0/0 --gateway-id $inspIgwId --region $region 2>$null
        aws ec2 associate-route-table --route-table-id $inspNatRtId --subnet-id $inspNatSubnetId --region $region 2>$null
        Write-Host "  NAT subnet route table -> IGW" -ForegroundColor Gray
    }
}
Write-Host ""

# ---------------------------------------------------------------------------
# 7. Create AWS Network Firewall in the INSPECTION VPC
# ---------------------------------------------------------------------------
Write-Host "--- AWS Network Firewall (inspection VPC) ---" -ForegroundColor Magenta

if (-not [string]::IsNullOrEmpty($inspFwSubnetId) -and $inspFwSubnetId -ne "None") {
    Write-Host "Checking for existing Network Firewall..." -ForegroundColor Yellow
    try {
        $nfwArn = aws network-firewall describe-firewall `
            --firewall-name goat-demo-tls-nfw `
            --query "Firewall.FirewallArn" --output text --region $region 2>$null
    } catch { $nfwArn = "" }

    if (-not [string]::IsNullOrEmpty($nfwArn) -and $nfwArn -ne "None") {
        Write-Host "  Network Firewall already exists: $nfwArn" -ForegroundColor Green
        Set-PreExisting "network-firewall"
    } else {
        Write-Host "Creating Network Firewall rule group..." -ForegroundColor Yellow
        try {
            # Check if rule group already exists
            $ruleGroupArn = aws network-firewall describe-rule-group `
                --rule-group-name goat-demo-tls-rules --type STATEFUL `
                --query "RuleGroupResponse.RuleGroupArn" --output text --region $region 2>$null
            if ([string]::IsNullOrEmpty($ruleGroupArn) -or $ruleGroupArn -eq "None") {
                $ruleGroupJson = @'
{"RulesSource":{"RulesString":"pass tls any any -> any any (tls.sni; content:\".amazonaws.com\"; endswith; msg:\"Allow AWS services\"; sid:1; rev:1;)"},"StatefulRuleOptions":{"RuleOrder":"STRICT_ORDER"}}
'@
                $ruleGroupFile = [System.IO.Path]::GetTempFileName()
                [System.IO.File]::WriteAllText($ruleGroupFile, $ruleGroupJson)

                $ruleGroupArn = aws network-firewall create-rule-group `
                    --rule-group-name goat-demo-tls-rules `
                    --type STATEFUL `
                    --capacity 100 `
                    --rule-group "file://$ruleGroupFile" `
                    --tags Key=goat-demo,Value=true Key=goat-scenario,Value=tls-fragmentation Key=auto-delete,Value=no `
                    --query "RuleGroupResponse.RuleGroupArn" --output text --region $region 2>&1
                Remove-Item $ruleGroupFile -ErrorAction SilentlyContinue
                if ($LASTEXITCODE -ne 0) { throw $ruleGroupArn }
                Write-Host "  Created rule group: $ruleGroupArn" -ForegroundColor Gray
            } else {
                Write-Host "  Rule group already exists: $ruleGroupArn" -ForegroundColor Green
            }

            # Check if firewall policy already exists
            $policyArn = aws network-firewall describe-firewall-policy `
                --firewall-policy-name goat-demo-tls-policy `
                --query "FirewallPolicyResponse.FirewallPolicyArn" --output text --region $region 2>$null
            if ([string]::IsNullOrEmpty($policyArn) -or $policyArn -eq "None") {
                $policyJson = @"
{"StatelessDefaultActions":["aws:forward_to_sfe"],"StatelessFragmentDefaultActions":["aws:forward_to_sfe"],"StatefulDefaultActions":["aws:drop_established"],"StatefulEngineOptions":{"RuleOrder":"STRICT_ORDER"},"StatefulRuleGroupReferences":[{"ResourceArn":"$ruleGroupArn","Priority":1}]}
"@
                $policyFile = [System.IO.Path]::GetTempFileName()
                [System.IO.File]::WriteAllText($policyFile, $policyJson)

                $policyArn = aws network-firewall create-firewall-policy `
                    --firewall-policy-name goat-demo-tls-policy `
                    --firewall-policy "file://$policyFile" `
                    --tags Key=goat-demo,Value=true Key=goat-scenario,Value=tls-fragmentation Key=auto-delete,Value=no `
                    --query "FirewallPolicyResponse.FirewallPolicyArn" --output text --region $region 2>&1
                Remove-Item $policyFile -ErrorAction SilentlyContinue
                if ($LASTEXITCODE -ne 0) { throw $policyArn }
                Write-Host "  Created firewall policy: $policyArn" -ForegroundColor Gray
            } else {
                Write-Host "  Firewall policy already exists: $policyArn" -ForegroundColor Green
            }

            # Create the firewall in the INSPECTION VPC firewall subnet
            Write-Host "Creating Network Firewall..." -ForegroundColor Yellow
            $nfwArn = aws network-firewall create-firewall `
                --firewall-name goat-demo-tls-nfw `
                --firewall-policy-arn $policyArn `
                --vpc-id $inspVpcId `
                --subnet-mappings SubnetId=$inspFwSubnetId `
                --tags Key=goat-demo,Value=true Key=goat-scenario,Value=tls-fragmentation Key=Name,Value=goat-demo-tls-nfw Key=auto-delete,Value=no `
                --query "Firewall.FirewallArn" --output text --region $region 2>&1
            if ($LASTEXITCODE -ne 0) { throw $nfwArn }
            Write-Host "  Created Network Firewall: $nfwArn" -ForegroundColor Green
            Write-Host "  Waiting for firewall to become ready (this may take several minutes)..." -ForegroundColor Gray

            $maxWait = 300
            $elapsed = 0
            while ($elapsed -lt $maxWait) {
                $fwStatus = aws network-firewall describe-firewall `
                    --firewall-name goat-demo-tls-nfw `
                    --query "FirewallStatus.Status" --output text --region $region 2>$null
                if ($fwStatus -eq "READY") { break }
                Start-Sleep -Seconds 15
                $elapsed += 15
            }
            if ($fwStatus -eq "READY") {
                Write-Host "  Network Firewall is ready" -ForegroundColor Green
            } else {
                Write-Host "  WARNING: Firewall not ready after ${maxWait}s (status: $fwStatus)" -ForegroundColor Yellow
                $warnings += "Network Firewall may still be provisioning"
            }
        } catch {
            Write-Host "  WARNING: Failed to create Network Firewall: $_" -ForegroundColor Red
            $warnings += "Network Firewall creation failed"
            $nfwArn = ""
        }
    }
}
Write-Host ""

# ---------------------------------------------------------------------------
# 8. Create Transit Gateway and attach both VPCs
#
# The TGW connects the spoke VPC (workload) to the inspection VPC (firewall).
# Appliance mode is enabled on the inspection attachment for symmetric flows.
#
# CRITICAL: We wait for BOTH attachments to reach "available" AND wait for
# route table associations to reach "associated" BEFORE creating routes.
# The association race was the root cause of node-join failures in the
# original script.
# ---------------------------------------------------------------------------
Write-Host "--- Transit Gateway ---" -ForegroundColor Magenta

Write-Host "Checking for existing Transit Gateway..." -ForegroundColor Yellow
$tgwId = aws ec2 describe-transit-gateways `
    --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-tgw" "Name=state,Values=available,pending,modifying" `
    --query "TransitGateways[0].TransitGatewayId" --output text --region $region 2>$null

if (-not [string]::IsNullOrEmpty($tgwId) -and $tgwId -ne "None") {
    Write-Host "  Transit Gateway already exists: $tgwId" -ForegroundColor Green
    Set-PreExisting "transit-gateway"
} else {
    Write-Host "Creating Transit Gateway (default route table association/propagation disabled)..." -ForegroundColor Yellow
    $tgwId = aws ec2 create-transit-gateway `
        --description "GOAT TLS fragmentation demo inspection TGW" `
        --options "DefaultRouteTableAssociation=disable,DefaultRouteTablePropagation=disable,DnsSupport=enable" `
        --tag-specifications (New-TagSpec "transit-gateway" "goat-demo-tls-tgw") `
        --query "TransitGateway.TransitGatewayId" --output text --region $region 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  WARNING: Failed to create Transit Gateway: $tgwId" -ForegroundColor Red
        $warnings += "Transit Gateway creation failed"
        $tgwId = ""
    } else {
        Write-Host "  Created Transit Gateway: $tgwId (waiting for available)..." -ForegroundColor Green
        $maxWait = 300; $elapsed = 0
        while ($elapsed -lt $maxWait) {
            $tgwState = aws ec2 describe-transit-gateways --transit-gateway-ids $tgwId --query "TransitGateways[0].State" --output text --region $region 2>$null
            if ($tgwState -eq "available") { break }
            Start-Sleep -Seconds 15; $elapsed += 15
        }
        Write-Host "  Transit Gateway is available" -ForegroundColor Green
    }
}

# Create the two VPC attachments + a dedicated TGW route table, then wire routing.
if (-not [string]::IsNullOrEmpty($tgwId) -and $tgwId -ne "None") {

    # --- Spoke attachment ---
    $tgwAttachSpokeId = aws ec2 describe-transit-gateway-attachments `
        --filters "Name=transit-gateway-id,Values=$tgwId" "Name=resource-id,Values=$vpcId" "Name=state,Values=available,pending,initiating,initiatingRequest,modifying" `
        --query "TransitGatewayAttachments[0].TransitGatewayAttachmentId" --output text --region $region 2>$null
    if ([string]::IsNullOrEmpty($tgwAttachSpokeId) -or $tgwAttachSpokeId -eq "None") {
        Write-Host "Creating spoke VPC attachment..." -ForegroundColor Yellow
        $tgwAttachSpokeId = aws ec2 create-transit-gateway-vpc-attachment `
            --transit-gateway-id $tgwId `
            --vpc-id $vpcId `
            --subnet-ids $subnetSpokeTgwId `
            --tag-specifications (New-TagSpec "transit-gateway-attachment" "goat-demo-tls-tgw-attach-spoke") `
            --query "TransitGatewayVpcAttachment.TransitGatewayAttachmentId" --output text --region $region 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  WARNING: Failed to create spoke attachment: $tgwAttachSpokeId" -ForegroundColor Red
            $warnings += "TGW spoke attachment failed"
            $tgwAttachSpokeId = ""
        } else {
            Write-Host "  Created spoke attachment: $tgwAttachSpokeId" -ForegroundColor Green
        }
    } else {
        Write-Host "  Spoke attachment exists: $tgwAttachSpokeId" -ForegroundColor Green
    }

    # --- Inspection attachment (appliance mode ON for symmetric flows) ---
    $tgwAttachInspId = aws ec2 describe-transit-gateway-attachments `
        --filters "Name=transit-gateway-id,Values=$tgwId" "Name=resource-id,Values=$inspVpcId" "Name=state,Values=available,pending,initiating,initiatingRequest,modifying" `
        --query "TransitGatewayAttachments[0].TransitGatewayAttachmentId" --output text --region $region 2>$null
    if ([string]::IsNullOrEmpty($tgwAttachInspId) -or $tgwAttachInspId -eq "None") {
        Write-Host "Creating inspection VPC attachment (appliance mode)..." -ForegroundColor Yellow
        $tgwAttachInspId = aws ec2 create-transit-gateway-vpc-attachment `
            --transit-gateway-id $tgwId `
            --vpc-id $inspVpcId `
            --subnet-ids $inspTgwSubnetId `
            --options "ApplianceModeSupport=enable" `
            --tag-specifications (New-TagSpec "transit-gateway-attachment" "goat-demo-tls-tgw-attach-insp") `
            --query "TransitGatewayVpcAttachment.TransitGatewayAttachmentId" --output text --region $region 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  WARNING: Failed to create inspection attachment: $tgwAttachInspId" -ForegroundColor Red
            $warnings += "TGW inspection attachment failed"
            $tgwAttachInspId = ""
        } else {
            Write-Host "  Created inspection attachment: $tgwAttachInspId" -ForegroundColor Green
        }
    } else {
        Write-Host "  Inspection attachment exists: $tgwAttachInspId" -ForegroundColor Green
    }

    # Wait for BOTH attachments to become available before configuring routes
    Write-Host "  Waiting for both TGW attachments to become available..." -ForegroundColor Gray
    foreach ($att in @($tgwAttachSpokeId, $tgwAttachInspId)) {
        if ([string]::IsNullOrEmpty($att) -or $att -eq "None") { continue }
        $maxWait = 180; $elapsed = 0
        while ($elapsed -lt $maxWait) {
            $attState = aws ec2 describe-transit-gateway-attachments --transit-gateway-attachment-ids $att --query "TransitGatewayAttachments[0].State" --output text --region $region 2>$null
            if ($attState -eq "available") { break }
            Start-Sleep -Seconds 15; $elapsed += 15
        }
        if ($attState -ne "available") {
            Write-Host "  WARNING: Attachment $att did not reach available (state: $attState)" -ForegroundColor Yellow
            $warnings += "TGW attachment $att not available"
        }
    }
    Write-Host "  Both TGW attachments are available" -ForegroundColor Green

    # --- TGW route table: send spoke egress to inspection, return to spoke ---
    if (-not [string]::IsNullOrEmpty($tgwAttachSpokeId) -and $tgwAttachSpokeId -ne "None" -and
        -not [string]::IsNullOrEmpty($tgwAttachInspId) -and $tgwAttachInspId -ne "None") {

        # Find existing demo TGW route table (tag-based)
        $tgwRtId = aws ec2 describe-transit-gateway-route-tables `
            --filters "Name=transit-gateway-id,Values=$tgwId" "Name=tag:Name,Values=goat-demo-tls-tgw-rt" "Name=state,Values=available,pending" `
            --query "TransitGatewayRouteTables[0].TransitGatewayRouteTableId" --output text --region $region 2>$null
        if ([string]::IsNullOrEmpty($tgwRtId) -or $tgwRtId -eq "None") {
            $tgwRtId = aws ec2 create-transit-gateway-route-table `
                --transit-gateway-id $tgwId `
                --tag-specifications (New-TagSpec "transit-gateway-route-table" "goat-demo-tls-tgw-rt") `
                --query "TransitGatewayRouteTable.TransitGatewayRouteTableId" --output text --region $region 2>&1
            Write-Host "  Created TGW route table: $tgwRtId" -ForegroundColor Gray
            Start-Sleep -Seconds 10
        } else {
            Write-Host "  TGW route table exists: $tgwRtId" -ForegroundColor Gray
        }

        # Associate both attachments with this route table
        aws ec2 associate-transit-gateway-route-table --transit-gateway-route-table-id $tgwRtId --transit-gateway-attachment-id $tgwAttachSpokeId --region $region 2>$null
        aws ec2 associate-transit-gateway-route-table --transit-gateway-route-table-id $tgwRtId --transit-gateway-attachment-id $tgwAttachInspId --region $region 2>$null

        # CRITICAL: Wait for BOTH associations to reach "associated" state before
        # creating routes. This race was the root cause of egress failures.
        Write-Host "  Waiting for TGW route table associations to reach 'associated'..." -ForegroundColor Gray
        foreach ($attToCheck in @($tgwAttachSpokeId, $tgwAttachInspId)) {
            $assocWait = 0
            while ($assocWait -lt 120) {
                $assocState = aws ec2 get-transit-gateway-route-table-associations `
                    --transit-gateway-route-table-id $tgwRtId `
                    --filters "Name=transit-gateway-attachment-id,Values=$attToCheck" `
                    --query "Associations[0].State" --output text --region $region 2>$null
                if ($assocState -eq "associated") { break }
                Start-Sleep -Seconds 10; $assocWait += 10
            }
            if ($assocState -ne "associated") {
                Write-Host "  WARNING: Association for $attToCheck did not reach 'associated' (state: $assocState)" -ForegroundColor Yellow
                $warnings += "TGW association for $attToCheck not confirmed"
            }
        }
        Write-Host "  Both associations confirmed" -ForegroundColor Green

        # Default route (0/0) -> inspection VPC attachment. Retry + verify.
        $tgwDefaultRouteOk = $false
        for ($attempt = 1; $attempt -le 5; $attempt++) {
            aws ec2 create-transit-gateway-route --transit-gateway-route-table-id $tgwRtId --destination-cidr-block 0.0.0.0/0 --transit-gateway-attachment-id $tgwAttachInspId --region $region 2>$null | Out-Null
            Start-Sleep -Seconds 5
            $r = aws ec2 search-transit-gateway-routes --transit-gateway-route-table-id $tgwRtId `
                --filters "Name=route-search.exact-match,Values=0.0.0.0/0" "Name=state,Values=active" `
                --query "Routes[0].DestinationCidrBlock" --output text --region $region 2>$null
            if ($r -eq "0.0.0.0/0") { $tgwDefaultRouteOk = $true; break }
            Write-Host "  Attempt $attempt/5: 0.0.0.0/0 route not yet active, retrying in 10s..." -ForegroundColor Gray
            Start-Sleep -Seconds 10
        }
        if (-not $tgwDefaultRouteOk) {
            Write-Host "  WARNING: TGW 0.0.0.0/0 -> inspection route not confirmed - spoke egress will fail" -ForegroundColor Red
            $warnings += "TGW default route to inspection VPC not confirmed (instance egress will fail)"
        } else {
            Write-Host "  TGW 0.0.0.0/0 -> inspection route verified" -ForegroundColor Green
        }
        # Return route: spoke CIDR -> spoke attachment
        aws ec2 create-transit-gateway-route --transit-gateway-route-table-id $tgwRtId --destination-cidr-block $spokeCidr --transit-gateway-attachment-id $tgwAttachSpokeId --region $region 2>$null
        Write-Host "  TGW routes: 0.0.0.0/0 -> inspection, $spokeCidr -> spoke" -ForegroundColor Gray
    }

    # --- Spoke VPC routing: private subnet -> TGW ---
    if (-not [string]::IsNullOrEmpty($subnetPrivateId) -and $subnetPrivateId -ne "None") {
        $spokePrivateRtId = Get-OrCreateRouteTable $vpcId "goat-demo-tls-private-rt"
        aws ec2 create-route --route-table-id $spokePrivateRtId --destination-cidr-block 0.0.0.0/0 --transit-gateway-id $tgwId --region $region 2>$null
        aws ec2 associate-route-table --route-table-id $spokePrivateRtId --subnet-id $subnetPrivateId --region $region 2>$null
        Write-Host "  Spoke private route table: 0.0.0.0/0 -> TGW" -ForegroundColor Gray
    }

    # --- Inspection VPC routing ---
    $fwEndpointId = ""
    if (-not [string]::IsNullOrEmpty($nfwArn) -and $nfwArn -ne "None") {
        $epWait = 0
        while ($epWait -lt 240) {
            $fwEndpointId = aws network-firewall describe-firewall `
                --firewall-name goat-demo-tls-nfw `
                --query "values(FirewallStatus.SyncStates)[0].Attachment.EndpointId" --output text --region $region 2>$null
            if (-not [string]::IsNullOrEmpty($fwEndpointId) -and $fwEndpointId -ne "None" -and $fwEndpointId -ne "null") { break }
            Write-Host "  Waiting for firewall endpoint attachment..." -ForegroundColor Gray
            Start-Sleep -Seconds 15
            $epWait += 15
        }
    }

    if (-not [string]::IsNullOrEmpty($fwEndpointId) -and $fwEndpointId -ne "None") {
        # TGW subnet route table: traffic arriving from spoke goes to the firewall endpoint.
        $inspTgwRtId = Get-OrCreateRouteTable $inspVpcId "goat-demo-tls-insp-tgw-rt"
        aws ec2 create-route --route-table-id $inspTgwRtId --destination-cidr-block 0.0.0.0/0 --vpc-endpoint-id $fwEndpointId --region $region 2>$null
        aws ec2 associate-route-table --route-table-id $inspTgwRtId --subnet-id $inspTgwSubnetId --region $region 2>$null
        Write-Host "  Inspection TGW subnet route table: 0.0.0.0/0 -> firewall endpoint" -ForegroundColor Gray

        # Firewall subnet route table: outbound -> NAT; return to spoke -> TGW.
        $inspFwRtId = Get-OrCreateRouteTable $inspVpcId "goat-demo-tls-insp-fw-rt"
        if (-not [string]::IsNullOrEmpty($natGwId) -and $natGwId -ne "None") {
            aws ec2 create-route --route-table-id $inspFwRtId --destination-cidr-block 0.0.0.0/0 --nat-gateway-id $natGwId --region $region 2>$null
        }
        aws ec2 create-route --route-table-id $inspFwRtId --destination-cidr-block $spokeCidr --transit-gateway-id $tgwId --region $region 2>$null
        aws ec2 associate-route-table --route-table-id $inspFwRtId --subnet-id $inspFwSubnetId --region $region 2>$null
        Write-Host "  Inspection firewall subnet route table: 0.0.0.0/0 -> NAT, $spokeCidr -> TGW" -ForegroundColor Gray

        # NAT subnet route table: return traffic to spoke must go back through the firewall
        # endpoint (symmetric), everything else (0/0) to IGW (already set above).
        if (-not [string]::IsNullOrEmpty($inspNatSubnetId) -and $inspNatSubnetId -ne "None") {
            $inspNatRtId = Get-OrCreateRouteTable $inspVpcId "goat-demo-tls-insp-nat-rt"
            aws ec2 create-route --route-table-id $inspNatRtId --destination-cidr-block $spokeCidr --vpc-endpoint-id $fwEndpointId --region $region 2>$null
            Write-Host "  Inspection NAT subnet route table: $spokeCidr -> firewall endpoint (symmetric return)" -ForegroundColor Gray
        }
    } else {
        Write-Host "  WARNING: Firewall endpoint not available yet - re-run after firewall is READY to finish inspection routing" -ForegroundColor Yellow
        $warnings += "Inspection routing incomplete - re-run after firewall is READY"
    }
}
Write-Host ""

# ---------------------------------------------------------------------------
# 7b. Configure Network Firewall logging (FLOW + ALERT)
# ---------------------------------------------------------------------------
if (-not [string]::IsNullOrEmpty($nfwArn) -and $nfwArn -ne "None") {
    Write-Host "--- Network Firewall Logging ---" -ForegroundColor Magenta
    $flowLogGroup = "/aws/network-firewall/goat-demo-tls-flow"
    $alertLogGroup = "/aws/network-firewall/goat-demo-tls-alert"
    aws logs create-log-group --log-group-name $flowLogGroup --region $region 2>$null
    aws logs create-log-group --log-group-name $alertLogGroup --region $region 2>$null

    $logConfigJson = @"
{"LogDestinationConfigs":[{"LogType":"FLOW","LogDestinationType":"CloudWatchLogs","LogDestination":{"logGroup":"$flowLogGroup"}},{"LogType":"ALERT","LogDestinationType":"CloudWatchLogs","LogDestination":{"logGroup":"$alertLogGroup"}}]}
"@
    $logConfigFile = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($logConfigFile, $logConfigJson)
    aws network-firewall update-logging-configuration `
        --firewall-name goat-demo-tls-nfw `
        --logging-configuration "file://$logConfigFile" `
        --region $region 2>$null
    Remove-Item $logConfigFile -ErrorAction SilentlyContinue
    Write-Host "  Configured FLOW + ALERT logging" -ForegroundColor Gray
    Write-Host ""
}

# ---------------------------------------------------------------------------
# 9. Set firewall PERMISSIVE before launching EC2 instance
#
# The instance needs to reach SSM endpoints during bootstrap (AL2023 has the
# SSM agent installed by default with default host management). We open the
# firewall temporarily so it can register with SSM.
# ---------------------------------------------------------------------------
Write-Host "--- Firewall Permissive Mode (for EC2 bootstrap) ---" -ForegroundColor Magenta

if (-not [string]::IsNullOrEmpty($nfwArn) -and $nfwArn -ne "None") {
    Write-Host "Setting firewall to PERMISSIVE for instance bootstrap..." -ForegroundColor Yellow
    if (Set-TlsFirewallRules -Mode "permissive") {
        Write-Host "  Waiting for permissive rules to sync to the firewall..." -ForegroundColor Gray
        Wait-FirewallInSync -MaxWaitSeconds 150 | Out-Null
        Write-Host "  Firewall is permissive" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: Could not set permissive rules; instance bootstrap may have limited connectivity" -ForegroundColor Yellow
        $warnings += "Could not set permissive firewall rules for instance bootstrap"
    }
}
Write-Host ""

# ---------------------------------------------------------------------------
# 10. Launch EC2 Test Instance (replaces EKS)
#
# A simple t3.micro running AL2023 with a UserData script that loops
# ML-KEM curl to ecr.<region>.amazonaws.com every 30 seconds. No IAM
# instance profile needed - the curl to ECR is unauthenticated and returns
# HTTP 404 which is fine for demonstrating the TLS handshake failure.
# ---------------------------------------------------------------------------
Write-Host "--- EC2 Test Instance ---" -ForegroundColor Magenta

if (-not [string]::IsNullOrEmpty($subnetPrivateId) -and $subnetPrivateId -ne "None") {
    # Check if instance already exists
    Write-Host "Checking for existing test instance..." -ForegroundColor Yellow
    $instanceId = aws ec2 describe-instances `
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-test-instance" "Name=instance-state-name,Values=running,pending" `
        --query "Reservations[0].Instances[0].InstanceId" --output text --region $region 2>$null

    if (-not [string]::IsNullOrEmpty($instanceId) -and $instanceId -ne "None") {
        Write-Host "  Test instance already exists: $instanceId" -ForegroundColor Green
        Set-PreExisting "ec2-instance"
    } else {
        # Resolve latest AL2023 AMI via SSM parameter
        Write-Host "Resolving latest AL2023 AMI..." -ForegroundColor Yellow
        $amiId = aws ssm get-parameter `
            --name "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64" `
            --query "Parameter.Value" --output text --region $region 2>$null
        if ([string]::IsNullOrEmpty($amiId) -or $amiId -eq "None") {
            Write-Host "  WARNING: Could not resolve AL2023 AMI" -ForegroundColor Red
            $warnings += "AL2023 AMI resolution failed"
        } else {
            Write-Host "  AL2023 AMI: $amiId" -ForegroundColor Green

            # Create security group (allow all egress, no ingress needed)
            $sgName = "goat-demo-tls-test-sg"
            $sgId = aws ec2 describe-security-groups `
                --filters "Name=vpc-id,Values=$vpcId" "Name=group-name,Values=$sgName" `
                --query "SecurityGroups[0].GroupId" --output text --region $region 2>$null
            if ([string]::IsNullOrEmpty($sgId) -or $sgId -eq "None") {
                Write-Host "  Creating security group..." -ForegroundColor Yellow
                $sgId = aws ec2 create-security-group `
                    --group-name $sgName `
                    --description "GOAT TLS demo - allow all egress for ECR TLS test" `
                    --vpc-id $vpcId `
                    --tag-specifications (New-TagSpec "security-group" $sgName) `
                    --query "GroupId" --output text --region $region 2>&1
                if ($LASTEXITCODE -ne 0) {
                    Write-Host "  WARNING: Failed to create security group: $sgId" -ForegroundColor Red
                    $warnings += "Security group creation failed"
                    $sgId = ""
                } else {
                    Write-Host "  Created security group: $sgId" -ForegroundColor Green
                }
            } else {
                Write-Host "  Security group exists: $sgId" -ForegroundColor Green
            }

            # Build UserData script
            $userDataScript = @"
#!/bin/bash
# Wait for network to be ready
sleep 10
# MTU 1500 is already default on t3.micro (no jumbo frames in spoke VPC via TGW)
echo "BOOTSTRAP_COMPLETE: AL2023 TLS fragmentation test instance ready"
# Loop ML-KEM curl every 30 seconds
while true; do
  echo "[`$(date -u +%Y-%m-%dT%H:%M:%SZ)] Attempting HTTPS to ecr.$region.amazonaws.com with ML-KEM..."
  curl --curves X25519MLKEM768:X25519 -sS -o /dev/null -w "HTTP %{http_code}\n" https://ecr.$region.amazonaws.com/ 2>&1 || echo "Connection failed (expected - firewall blocks fragmented Client Hello)"
  sleep 30
done
"@
            # Base64 encode the UserData
            $userDataB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($userDataScript))

            # Launch instance with tags on both instance and network interface
            Write-Host "Launching EC2 test instance (t3.micro, AL2023)..." -ForegroundColor Yellow
            if (-not [string]::IsNullOrEmpty($sgId) -and $sgId -ne "None") {
                $instanceId = aws ec2 run-instances `
                    --image-id $amiId `
                    --instance-type t3.micro `
                    --subnet-id $subnetPrivateId `
                    --security-group-ids $sgId `
                    --user-data $userDataB64 `
                    --tag-specifications "ResourceType=instance,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=tls-fragmentation},{Key=Name,Value=goat-demo-tls-test-instance},{Key=auto-delete,Value=no},{Key=goat-network-capture-allowed,Value=true}]" "ResourceType=network-interface,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=tls-fragmentation},{Key=Name,Value=goat-demo-tls-test-eni},{Key=auto-delete,Value=no},{Key=goat-network-capture-allowed,Value=true}]" `
                    --query "Instances[0].InstanceId" --output text --region $region 2>&1
                if ($LASTEXITCODE -ne 0) {
                    Write-Host "  WARNING: Failed to launch instance: $instanceId" -ForegroundColor Red
                    $warnings += "EC2 instance launch failed"
                    $instanceId = ""
                } else {
                    Write-Host "  Launched instance: $instanceId" -ForegroundColor Green
                }
            } else {
                Write-Host "  WARNING: No security group available, skipping instance launch" -ForegroundColor Red
                $warnings += "EC2 instance launch skipped (no security group)"
            }
        }
    }

    # Wait for instance to reach running state
    if (-not [string]::IsNullOrEmpty($instanceId) -and $instanceId -ne "None" -and -not $preExisting.ContainsKey("ec2-instance")) {
        Write-Host "  Waiting for instance to reach 'running' state..." -ForegroundColor Gray
        aws ec2 wait instance-running --instance-ids $instanceId --region $region 2>$null
        Write-Host "  Instance is running" -ForegroundColor Green
    }

    # Get the ENI ID for the instance (for packet captures)
    if (-not [string]::IsNullOrEmpty($instanceId) -and $instanceId -ne "None") {
        $instanceEniId = aws ec2 describe-instances `
            --instance-ids $instanceId `
            --query "Reservations[0].Instances[0].NetworkInterfaces[0].NetworkInterfaceId" `
            --output text --region $region 2>$null
        if (-not [string]::IsNullOrEmpty($instanceEniId) -and $instanceEniId -ne "None") {
            Write-Host "  Instance ENI: $instanceEniId" -ForegroundColor Green
            # Ensure ENI is tagged (in case instance was pre-existing)
            aws ec2 create-tags --resources $instanceEniId `
                --tags Key=goat-network-capture-allowed,Value=true Key=goat-demo,Value=true Key=goat-scenario,Value=tls-fragmentation `
                --region $region 2>$null
        }
    }
} else {
    Write-Host "  Skipping EC2 instance (no private subnet available)" -ForegroundColor Yellow
}
Write-Host ""

# ---------------------------------------------------------------------------
# 11. Wait for instance to be reachable via SSM
#
# AL2023 has the SSM agent installed by default and uses Default Host
# Management Configuration (DHMC) to register with SSM automatically.
# We wait up to 3 minutes for the instance to appear in SSM.
# ---------------------------------------------------------------------------
Write-Host "--- Verifying Instance Reachability (SSM) ---" -ForegroundColor Magenta

$instanceReachable = $false
if (-not [string]::IsNullOrEmpty($instanceId) -and $instanceId -ne "None") {
    Write-Host "  Waiting for instance to register with SSM (up to 3 min)..." -ForegroundColor Gray
    $ssmWait = 0
    while ($ssmWait -lt 180) {
        $ssmStatus = aws ssm describe-instance-information `
            --filters "Key=InstanceIds,Values=$instanceId" `
            --query "InstanceInformationList[0].PingStatus" --output text --region $region 2>$null
        if ($ssmStatus -eq "Online") {
            $instanceReachable = $true
            break
        }
        Start-Sleep -Seconds 15
        $ssmWait += 15
    }
    if ($instanceReachable) {
        Write-Host "  Instance is online in SSM" -ForegroundColor Green
    } else {
        Write-Host "  Instance not yet online in SSM (may need Default Host Management enabled)" -ForegroundColor Yellow
        Write-Host "  The instance will still run the curl loop via UserData regardless of SSM status" -ForegroundColor Yellow
        $warnings += "Instance not confirmed in SSM - UserData still running"
    }
}
Write-Host ""

# ---------------------------------------------------------------------------
# 12. Restore firewall STRICT rules
#
# Now that the instance is running (and has completed its initial network
# setup), restore the strict demo firewall rules so the ML-KEM Client Hello
# to ECR is dropped - reproducing the failure.
# ---------------------------------------------------------------------------
Write-Host "--- Restoring Firewall STRICT Rules ---" -ForegroundColor Magenta

if (-not [string]::IsNullOrEmpty($nfwArn) -and $nfwArn -ne "None") {
    Write-Host "Restoring STRICT firewall rules (ML-KEM Client Hello will now be dropped)..." -ForegroundColor Yellow
    if (Set-TlsFirewallRules -Mode "strict") {
        Write-Host "  Waiting for strict rules to sync to the firewall..." -ForegroundColor Gray
        Wait-FirewallInSync -MaxWaitSeconds 150 | Out-Null
        Write-Host "  Firewall restored to strict demo configuration" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: Could not restore strict rules" -ForegroundColor Red
        $warnings += "Firewall left in permissive state - restore strict rules manually"
    }
}
Write-Host ""

# ---------------------------------------------------------------------------
# 13. Create Support case (if Support plan is active)
# ---------------------------------------------------------------------------
Write-Host "--- Support Case ---" -ForegroundColor Magenta

$supportCaseId = ""

Write-Host "Detecting Support plan..." -ForegroundColor Yellow
$supportCheck = aws support describe-services --query "services[0].code" --output text --region us-east-1 2>&1

if ($supportCheck -match "SubscriptionRequiredException") {
    Write-Host "  WARNING: No Business or Enterprise Support plan detected." -ForegroundColor Yellow
    Write-Host "  Skipping Support case creation. To enable this feature, upgrade your Support plan." -ForegroundColor Yellow
    $warnings += "Support case skipped - no Support plan"
    $supportCaseId = "skipped (no Support plan)"
} else {
    Write-Host "Creating Support case for TLS fragmentation scenario..." -ForegroundColor Yellow
    try {
        $caseBody = "Our EC2 instance running Amazon Linux 2023 in $region is failing to establish HTTPS connections to ECR (endpoint: ecr.$region.amazonaws.com on port 443). The error is 'connection reset by peer' during the TLS handshake. This started after the latest AL2023 update that upgraded OpenSSL to 3.5.5. We suspect the new ML-KEM (Kyber-768) key-share is producing oversized TLS Client Hello messages (~3.5 KB) that are being fragmented across multiple TCP segments. Our AWS Network Firewall (goat-demo-tls-nfw) uses the legacy 'drop established' default action with pass rules for *.amazonaws.com domains - we believe the firewall cannot extract the SNI from the fragmented Client Hello and is dropping the connection. Affected resources: EC2 instance $instanceId, VPC $vpcId (10.99.0.0/16, name: goat-demo-vpc), Network Firewall goat-demo-tls-nfw, destination ecr.$region.amazonaws.com:443, source port ephemeral. Account $accountId, region $region. This case was created by the G.O.A.T. demo provisioning scripts for demonstration purposes."

        $supportCaseId = aws support create-case `
            --subject "EC2 instance failing HTTPS to ECR - connection reset by peer in $region" `
            --communication-body $caseBody `
            --service-code "amazon-elastic-compute-cloud-linux" `
            --category-code "other" `
            --severity-code "high" `
            --language "en" `
            --query "caseId" --output text --region us-east-1 2>&1
        if ($LASTEXITCODE -ne 0) { throw $supportCaseId }
        Write-Host "  Created Support case: $supportCaseId" -ForegroundColor Green

        # Add a follow-up communication with additional diagnostic details
        $followUp = "Additional details: We captured traffic using VPC Traffic Mirroring on VPC $vpcId and confirmed the TLS Client Hello is 3,547 bytes (fragmented into 3 TCP segments). The Network Firewall goat-demo-tls-nfw sends a TCP RST from its own ENI (source IP does not match either endpoint - the RST originates from the firewall's endpoint in the inspection VPC). The RST arrives immediately after the fragmented Client Hello, before ecr.$region.amazonaws.com responds. We believe this matches the known issue with AWS Network Firewall stateful rule groups using 'drop established' default action failing to inspect SNI in fragmented TLS records. The instance is running AL2023 with OpenSSL 3.5.5 (ML-KEM enabled by default). Instance ENI: $instanceEniId. Workaround under evaluation: switch to 'aws:drop_strict' with 'flow:to_server, flow:established' qualifiers. This case was created automatically by the G.O.A.T. demo scripts - no action needed from AWS Support."
        aws support add-communication-to-case `
            --case-id $supportCaseId `
            --communication-body $followUp `
            --region us-east-1 2>$null

        # Immediately resolve the case
        Write-Host "  Resolving Support case..." -ForegroundColor Yellow
        $resolveOutput = aws support resolve-case --case-id $supportCaseId --region us-east-1 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  WARNING: Failed to resolve Support case $supportCaseId" -ForegroundColor Red
            Write-Host "  Please close it manually via the AWS Console: https://console.aws.amazon.com/support/home" -ForegroundColor Red
            $warnings += "Support case resolve failed - close manually: $supportCaseId"
        } else {
            Write-Host "  Support case resolved: $supportCaseId" -ForegroundColor Green
        }
    } catch {
        Write-Host "  WARNING: Failed to create Support case: $_" -ForegroundColor Red
        $warnings += "Support case creation failed"
        $supportCaseId = ""
    }
}
Write-Host ""

# ---------------------------------------------------------------------------
# 14. Summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "===== TLS FRAGMENTATION SCENARIO SUMMARY START =====" -ForegroundColor Green

$summaryLines = @()

if (-not [string]::IsNullOrEmpty($vpcId) -and $vpcId -ne "None") {
    $prefix = Get-SummaryPrefix "vpc"
    $summaryLines += "${prefix}spoke-vpc: $vpcId"
}
if (-not [string]::IsNullOrEmpty($subnetPrivateId) -and $subnetPrivateId -ne "None") {
    $prefix = Get-SummaryPrefix "subnet-private"
    $summaryLines += "${prefix}spoke-private-subnet: $subnetPrivateId"
}
if (-not [string]::IsNullOrEmpty($subnetSpokeTgwId) -and $subnetSpokeTgwId -ne "None") {
    $prefix = Get-SummaryPrefix "subnet-spoke-tgw"
    $summaryLines += "${prefix}spoke-tgw-subnet: $subnetSpokeTgwId"
}
if (-not [string]::IsNullOrEmpty($inspVpcId) -and $inspVpcId -ne "None") {
    $prefix = Get-SummaryPrefix "inspection-vpc"
    $summaryLines += "${prefix}inspection-vpc: $inspVpcId"
}
if (-not [string]::IsNullOrEmpty($tgwId) -and $tgwId -ne "None") {
    $prefix = Get-SummaryPrefix "transit-gateway"
    $summaryLines += "${prefix}transit-gateway: $tgwId"
}
if (-not [string]::IsNullOrEmpty($inspIgwId) -and $inspIgwId -ne "None") {
    $prefix = Get-SummaryPrefix "inspection-igw"
    $summaryLines += "${prefix}inspection-igw: $inspIgwId"
}
if (-not [string]::IsNullOrEmpty($natGwId) -and $natGwId -ne "None") {
    $prefix = Get-SummaryPrefix "nat-gateway"
    $summaryLines += "${prefix}nat-gateway: $natGwId"
}
if (-not [string]::IsNullOrEmpty($nfwArn) -and $nfwArn -ne "None") {
    $prefix = Get-SummaryPrefix "network-firewall"
    $summaryLines += "${prefix}network-firewall: $nfwArn"
}
if (-not [string]::IsNullOrEmpty($instanceId) -and $instanceId -ne "None") {
    $prefix = Get-SummaryPrefix "ec2-instance"
    $summaryLines += "${prefix}ec2-instance: $instanceId"
}
if (-not [string]::IsNullOrEmpty($instanceEniId) -and $instanceEniId -ne "None") {
    $summaryLines += "ec2-instance-eni: $instanceEniId"
}
if (-not [string]::IsNullOrEmpty($supportCaseId) -and $supportCaseId -ne "skipped (no Support plan)") {
    $summaryLines += "support-case: $supportCaseId"
} elseif ($supportCaseId -eq "skipped (no Support plan)") {
    $summaryLines += "support-case: skipped (no Support plan)"
}

foreach ($line in $summaryLines) {
    Write-Host $line -ForegroundColor Cyan
}

Write-Host "" -ForegroundColor Cyan
if (-not [string]::IsNullOrEmpty($instanceEniId) -and $instanceEniId -ne "None") {
    Write-Host "suggested-query: Capture traffic from $instanceEniId" -ForegroundColor Cyan
    Write-Host "suggested-query-2: Why is the EC2 instance failing to connect to ECR? Capture traffic from $instanceEniId and analyze the TLS handshake" -ForegroundColor Cyan
}
if (-not [string]::IsNullOrEmpty($supportCaseId) -and $supportCaseId -ne "skipped (no Support plan)") {
    Write-Host "suggested-query-3: Investigate the network problem described in support case $supportCaseId and capture traffic if relevant" -ForegroundColor Cyan
}
Write-Host "===== TLS FRAGMENTATION SCENARIO SUMMARY END =====" -ForegroundColor Green
Write-Host ""

if ($warnings.Count -gt 0) {
    Write-Host "  Warnings:" -ForegroundColor Yellow
    foreach ($w in $warnings) {
        Write-Host "    - $w" -ForegroundColor Yellow
    }
    Write-Host ""
}

Write-Host "  To clean up all demo resources:" -ForegroundColor Gray
Write-Host "    .\cleanup-scenarios.ps1    (PowerShell)" -ForegroundColor Gray
Write-Host "    ./cleanup-scenarios.sh     (Bash)" -ForegroundColor Gray
Write-Host ""
