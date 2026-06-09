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
# Usage:
#   .\setup-scenario-account-health.ps1                        # Create a new VPC
#   .\setup-scenario-account-health.ps1 -VpcId vpc-0abc123    # Reuse an existing VPC

param(
    [string]$VpcId = ""
)

$ErrorActionPreference = "Continue"

# ---------------------------------------------------------------------------
# Track created/existing resources for summary
# ---------------------------------------------------------------------------
$vpcId = ""
$subnet1Id = ""
$subnet2Id = ""
$dbSubnetGroup = ""
$instance1Id = ""
$instance2Id = ""
$rdsId = ""
$ebsId = ""
$eipId = ""
$supportCaseId = ""
$warnings = @()

# ---------------------------------------------------------------------------
# 1. Verify AWS credentials
# ---------------------------------------------------------------------------
Write-Host "=== G.O.A.T. Demo Scenario A - Full Account Health Check ===" -ForegroundColor Cyan
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

$region = $env:AWS_DEFAULT_REGION
if ([string]::IsNullOrEmpty($region)) { $region = $env:AWS_REGION }
if ([string]::IsNullOrEmpty($region)) {
    $region = aws configure get region 2>$null
}
if ([string]::IsNullOrEmpty($region)) {
    $region = "us-east-1"
    Write-Host "  No region configured, falling back to us-east-1" -ForegroundColor Yellow
}
Write-Host "  Region: $region" -ForegroundColor Green
Write-Host ""

# ---------------------------------------------------------------------------
# 3. Create or reuse shared VPC (idempotent)
#
# All demo scenarios share a single VPC named "goat-demo-vpc" (10.99.0.0/16).
# Each scenario uses different subnets within this VPC to avoid conflicts:
#   - Scenario A:              10.99.1.0/24, 10.99.2.0/24
#   - TLS Fragmentation:      10.99.10.0/24, 10.99.11.0/24, 10.99.12.0/24
# ---------------------------------------------------------------------------
Write-Host "--- VPC and Networking ---" -ForegroundColor Magenta

if (-not [string]::IsNullOrEmpty($VpcId)) {
    # User provided an existing VPC - validate it exists
    Write-Host "Using provided VPC: $VpcId" -ForegroundColor Yellow
    try {
        $vpcCheck = aws ec2 describe-vpcs --vpc-ids $VpcId --query "Vpcs[0].VpcId" --output text --region $region 2>&1
        if ($LASTEXITCODE -ne 0) { throw $vpcCheck }
        $vpcId = $VpcId
        Write-Host "  VPC validated: $vpcId" -ForegroundColor Green
        # Ensure DNS hostnames enabled (required for RDS)
        aws ec2 modify-vpc-attribute --vpc-id $vpcId --enable-dns-hostnames '{"Value":true}' --region $region 2>$null
    } catch {
        Write-Host "  ERROR: Provided VPC $VpcId not found or inaccessible: $_" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "Checking for existing goat-demo-vpc..." -ForegroundColor Yellow
    try {
        $vpcId = aws ec2 describe-vpcs `
            --filters "Name=tag:goat-demo,Values=true" "Name=tag:Name,Values=goat-demo-vpc" `
            --query "Vpcs[0].VpcId" --output text --region $region 2>$null
    } catch { $vpcId = "" }

    if (-not [string]::IsNullOrEmpty($vpcId) -and $vpcId -ne "None") {
        Write-Host "  Shared VPC already exists: $vpcId" -ForegroundColor Green
    } else {
        Write-Host "Creating shared VPC goat-demo-vpc (10.99.0.0/16)..." -ForegroundColor Yellow
        try {
            $vpcId = aws ec2 create-vpc `
                --cidr-block 10.99.0.0/16 `
                --tag-specifications 'ResourceType=vpc,Tags=[{Key=goat-demo,Value=true},{Key=Name,Value=goat-demo-vpc},{Key=auto-delete,Value=no}]' `
                --query "Vpc.VpcId" --output text --region $region 2>&1
            if ($LASTEXITCODE -ne 0) { throw $vpcId }
            Write-Host "  Created VPC: $vpcId" -ForegroundColor Green

            # Enable DNS hostnames (required for RDS)
            aws ec2 modify-vpc-attribute --vpc-id $vpcId --enable-dns-hostnames '{"Value":true}' --region $region 2>$null
            Write-Host "  Enabled DNS hostnames on VPC" -ForegroundColor Gray
        } catch {
            Write-Host "  WARNING: Failed to create VPC: $_" -ForegroundColor Red
            $warnings += "VPC creation failed"
            $vpcId = ""
        }
    }
}

# ---------------------------------------------------------------------------
# 4. Create subnets in two AZs (idempotent)
# ---------------------------------------------------------------------------
$az1 = ""
$az2 = ""

if (-not [string]::IsNullOrEmpty($vpcId) -and $vpcId -ne "None") {
    # Get availability zones
    $az1 = aws ec2 describe-availability-zones --query "AvailabilityZones[0].ZoneName" --output text --region $region 2>$null
    $az2 = aws ec2 describe-availability-zones --query "AvailabilityZones[1].ZoneName" --output text --region $region 2>$null

    # Subnet 1
    Write-Host "Checking for existing subnet-1..." -ForegroundColor Yellow
    try {
        $subnet1Id = aws ec2 describe-subnets `
            --filters "Name=tag:goat-demo,Values=true" "Name=tag:Name,Values=goat-demo-subnet-1" `
            --query "Subnets[0].SubnetId" --output text --region $region 2>$null
    } catch { $subnet1Id = "" }

    if (-not [string]::IsNullOrEmpty($subnet1Id) -and $subnet1Id -ne "None") {
        Write-Host "  Subnet 1 already exists: $subnet1Id" -ForegroundColor Green
        $az1 = aws ec2 describe-subnets --subnet-ids $subnet1Id --query "Subnets[0].AvailabilityZone" --output text --region $region 2>$null
    } else {
        Write-Host "Creating subnet-1 in $az1 (10.99.1.0/24)..." -ForegroundColor Yellow
        try {
            $subnet1Id = aws ec2 create-subnet `
                --vpc-id $vpcId `
                --cidr-block 10.99.1.0/24 `
                --availability-zone $az1 `
                --tag-specifications 'ResourceType=subnet,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=a},{Key=Name,Value=goat-demo-subnet-1},{Key=auto-delete,Value=no}]' `
                --query "Subnet.SubnetId" --output text --region $region 2>&1
            if ($LASTEXITCODE -ne 0) { throw $subnet1Id }
            Write-Host "  Created subnet-1: $subnet1Id ($az1)" -ForegroundColor Green
        } catch {
            Write-Host "  WARNING: Failed to create subnet-1: $_" -ForegroundColor Red
            $warnings += "Subnet-1 creation failed"
            $subnet1Id = ""
        }
    }

    # Subnet 2
    Write-Host "Checking for existing subnet-2..." -ForegroundColor Yellow
    try {
        $subnet2Id = aws ec2 describe-subnets `
            --filters "Name=tag:goat-demo,Values=true" "Name=tag:Name,Values=goat-demo-subnet-2" `
            --query "Subnets[0].SubnetId" --output text --region $region 2>$null
    } catch { $subnet2Id = "" }

    if (-not [string]::IsNullOrEmpty($subnet2Id) -and $subnet2Id -ne "None") {
        Write-Host "  Subnet 2 already exists: $subnet2Id" -ForegroundColor Green
    } else {
        Write-Host "Creating subnet-2 in $az2 (10.99.2.0/24)..." -ForegroundColor Yellow
        try {
            $subnet2Id = aws ec2 create-subnet `
                --vpc-id $vpcId `
                --cidr-block 10.99.2.0/24 `
                --availability-zone $az2 `
                --tag-specifications 'ResourceType=subnet,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=a},{Key=Name,Value=goat-demo-subnet-2},{Key=auto-delete,Value=no}]' `
                --query "Subnet.SubnetId" --output text --region $region 2>&1
            if ($LASTEXITCODE -ne 0) { throw $subnet2Id }
            Write-Host "  Created subnet-2: $subnet2Id ($az2)" -ForegroundColor Green
        } catch {
            Write-Host "  WARNING: Failed to create subnet-2: $_" -ForegroundColor Red
            $warnings += "Subnet-2 creation failed"
            $subnet2Id = ""
        }
    }

    # DB Subnet Group
    if (-not [string]::IsNullOrEmpty($subnet1Id) -and $subnet1Id -ne "None" -and
        -not [string]::IsNullOrEmpty($subnet2Id) -and $subnet2Id -ne "None") {

        Write-Host "Checking for existing DB subnet group..." -ForegroundColor Yellow
        try {
            $dbSubnetGroup = aws rds describe-db-subnet-groups `
                --db-subnet-group-name goat-demo-db-subnet-group `
                --query "DBSubnetGroups[0].DBSubnetGroupName" --output text --region $region 2>$null
        } catch { $dbSubnetGroup = "" }

        if (-not [string]::IsNullOrEmpty($dbSubnetGroup) -and $dbSubnetGroup -ne "None") {
            Write-Host "  DB subnet group already exists: $dbSubnetGroup" -ForegroundColor Green
        } else {
            Write-Host "Creating DB subnet group..." -ForegroundColor Yellow
            try {
                $dbSubnetGroup = aws rds create-db-subnet-group `
                    --db-subnet-group-name goat-demo-db-subnet-group `
                    --db-subnet-group-description "G.O.A.T. demo DB subnet group" `
                    --subnet-ids $subnet1Id $subnet2Id `
                    --tags Key=goat-demo,Value=true Key=goat-scenario,Value=a Key=Name,Value=goat-demo-db-subnet-group Key=auto-delete,Value=no `
                    --query "DBSubnetGroup.DBSubnetGroupName" --output text --region $region 2>&1
                if ($LASTEXITCODE -ne 0) { throw $dbSubnetGroup }
                Write-Host "  Created DB subnet group: $dbSubnetGroup" -ForegroundColor Green
            } catch {
                Write-Host "  WARNING: Failed to create DB subnet group: $_" -ForegroundColor Red
                $warnings += "DB subnet group creation failed"
                $dbSubnetGroup = ""
            }
        }
    }
}

Write-Host ""

# ---------------------------------------------------------------------------
# 5. Create EC2 instances (idempotent)
# ---------------------------------------------------------------------------
Write-Host "--- EC2 Instances ---" -ForegroundColor Magenta

# Instance 1
Write-Host "Checking for existing goat-demo-instance-1..." -ForegroundColor Yellow
try {
    $instance1Id = aws ec2 describe-instances `
        --filters "Name=tag:goat-demo,Values=true" "Name=tag:Name,Values=goat-demo-instance-1" `
                  "Name=instance-state-name,Values=pending,running,stopping,stopped" `
        --query "Reservations[].Instances[].InstanceId" --output text --region $region 2>$null
} catch { $instance1Id = "" }

if (-not [string]::IsNullOrEmpty($instance1Id) -and $instance1Id -ne "None") {
    Write-Host "  Instance 1 already exists: $instance1Id" -ForegroundColor Green
} else {
    $subnetArg = @()
    if (-not [string]::IsNullOrEmpty($subnet1Id) -and $subnet1Id -ne "None") {
        $subnetArg = @("--subnet-id", $subnet1Id)
    }

    Write-Host "Creating EC2 instance goat-demo-instance-1 (t3.micro)..." -ForegroundColor Yellow
    try {
        $instance1Id = aws ec2 run-instances `
            --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 `
            --instance-type t3.micro `
            @subnetArg `
            --tag-specifications 'ResourceType=instance,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=a},{Key=Name,Value=goat-demo-instance-1},{Key=auto-delete,Value=no}]' `
            --count 1 `
            --query "Instances[0].InstanceId" --output text --region $region 2>&1
        if ($LASTEXITCODE -ne 0) { throw $instance1Id }
        Write-Host "  Created instance-1: $instance1Id" -ForegroundColor Green
    } catch {
        Write-Host "  WARNING: Failed to create instance-1: $_" -ForegroundColor Red
        $warnings += "EC2 instance-1 creation failed"
        $instance1Id = ""
    }
}

# Instance 2
Write-Host "Checking for existing goat-demo-instance-2..." -ForegroundColor Yellow
try {
    $instance2Id = aws ec2 describe-instances `
        --filters "Name=tag:goat-demo,Values=true" "Name=tag:Name,Values=goat-demo-instance-2" `
                  "Name=instance-state-name,Values=pending,running,stopping,stopped" `
        --query "Reservations[].Instances[].InstanceId" --output text --region $region 2>$null
} catch { $instance2Id = "" }

if (-not [string]::IsNullOrEmpty($instance2Id) -and $instance2Id -ne "None") {
    Write-Host "  Instance 2 already exists: $instance2Id" -ForegroundColor Green
} else {
    $subnetArg = @()
    if (-not [string]::IsNullOrEmpty($subnet1Id) -and $subnet1Id -ne "None") {
        $subnetArg = @("--subnet-id", $subnet1Id)
    }

    Write-Host "Creating EC2 instance goat-demo-instance-2 (t3.micro)..." -ForegroundColor Yellow
    try {
        $instance2Id = aws ec2 run-instances `
            --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 `
            --instance-type t3.micro `
            @subnetArg `
            --tag-specifications 'ResourceType=instance,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=a},{Key=Name,Value=goat-demo-instance-2},{Key=auto-delete,Value=no}]' `
            --count 1 `
            --query "Instances[0].InstanceId" --output text --region $region 2>&1
        if ($LASTEXITCODE -ne 0) { throw $instance2Id }
        Write-Host "  Created instance-2: $instance2Id" -ForegroundColor Green
    } catch {
        Write-Host "  WARNING: Failed to create instance-2: $_" -ForegroundColor Red
        $warnings += "EC2 instance-2 creation failed"
        $instance2Id = ""
    }
}

Write-Host ""

# ---------------------------------------------------------------------------
# 6. Create RDS instance (idempotent)
# ---------------------------------------------------------------------------
Write-Host "--- RDS Instance ---" -ForegroundColor Magenta

Write-Host "Checking for existing goat-demo-db..." -ForegroundColor Yellow
try {
    $rdsId = aws rds describe-db-instances `
        --db-instance-identifier goat-demo-db `
        --query "DBInstances[0].DBInstanceIdentifier" --output text --region $region 2>$null
} catch { $rdsId = "" }

if (-not [string]::IsNullOrEmpty($rdsId) -and $rdsId -ne "None") {
    Write-Host "  RDS instance already exists: $rdsId" -ForegroundColor Green
} else {
    $dbSubnetArg = @()
    if (-not [string]::IsNullOrEmpty($dbSubnetGroup) -and $dbSubnetGroup -ne "None") {
        $dbSubnetArg = @("--db-subnet-group-name", "goat-demo-db-subnet-group")
    }

    Write-Host "Creating RDS instance goat-demo-db (db.t3.micro, MySQL)..." -ForegroundColor Yellow
    Write-Host "  (Instance will take several minutes to become available)" -ForegroundColor Gray
    try {
        $rdsId = aws rds create-db-instance `
            --db-instance-identifier goat-demo-db `
            --db-instance-class db.t3.micro `
            --engine mysql `
            --master-username goatadmin `
            --master-user-password GoatDemo2025Temp `
            --allocated-storage 20 `
            --no-multi-az `
            --no-publicly-accessible `
            @dbSubnetArg `
            --tags Key=goat-demo,Value=true Key=goat-scenario,Value=a Key=Name,Value=goat-demo-db Key=auto-delete,Value=no `
            --query "DBInstance.DBInstanceIdentifier" --output text --region $region 2>&1
        if ($LASTEXITCODE -ne 0) { throw $rdsId }
        Write-Host "  Created RDS instance: $rdsId (creating...)" -ForegroundColor Green
    } catch {
        Write-Host "  WARNING: Failed to create RDS instance: $_" -ForegroundColor Red
        $warnings += "RDS instance creation failed"
        $rdsId = ""
    }
}

Write-Host ""

# ---------------------------------------------------------------------------
# 7. Create unattached EBS volume (idempotent)
# ---------------------------------------------------------------------------
Write-Host "--- EBS Volume ---" -ForegroundColor Magenta

Write-Host "Checking for existing goat-demo-ebs-unused..." -ForegroundColor Yellow
try {
    $ebsId = aws ec2 describe-volumes `
        --filters "Name=tag:goat-demo,Values=true" "Name=tag:Name,Values=goat-demo-ebs-unused" `
                  "Name=status,Values=available,creating,in-use" `
        --query "Volumes[0].VolumeId" --output text --region $region 2>$null
} catch { $ebsId = "" }

if (-not [string]::IsNullOrEmpty($ebsId) -and $ebsId -ne "None") {
    Write-Host "  EBS volume already exists: $ebsId" -ForegroundColor Green
} else {
    $ebsAz = $az1
    if ([string]::IsNullOrEmpty($ebsAz)) {
        $ebsAz = aws ec2 describe-availability-zones --query "AvailabilityZones[0].ZoneName" --output text --region $region 2>$null
    }

    Write-Host "Creating unattached EBS volume (gp2, 10GB) in $ebsAz..." -ForegroundColor Yellow
    try {
        $ebsId = aws ec2 create-volume `
            --volume-type gp2 `
            --size 10 `
            --availability-zone $ebsAz `
            --tag-specifications 'ResourceType=volume,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=a},{Key=Name,Value=goat-demo-ebs-unused},{Key=auto-delete,Value=no}]' `
            --query "VolumeId" --output text --region $region 2>&1
        if ($LASTEXITCODE -ne 0) { throw $ebsId }
        Write-Host "  Created EBS volume: $ebsId (unattached)" -ForegroundColor Green
    } catch {
        Write-Host "  WARNING: Failed to create EBS volume: $_" -ForegroundColor Red
        $warnings += "EBS volume creation failed"
        $ebsId = ""
    }
}

Write-Host ""

# ---------------------------------------------------------------------------
# 8. Allocate unassociated Elastic IP (idempotent)
# ---------------------------------------------------------------------------
Write-Host "--- Elastic IP ---" -ForegroundColor Magenta

Write-Host "Checking for existing goat-demo-eip-unused..." -ForegroundColor Yellow
try {
    $eipId = aws ec2 describe-addresses `
        --filters "Name=tag:goat-demo,Values=true" "Name=tag:Name,Values=goat-demo-eip-unused" `
        --query "Addresses[0].AllocationId" --output text --region $region 2>$null
} catch { $eipId = "" }

if (-not [string]::IsNullOrEmpty($eipId) -and $eipId -ne "None") {
    Write-Host "  Elastic IP already exists: $eipId" -ForegroundColor Green
} else {
    Write-Host "Allocating Elastic IP (unassociated)..." -ForegroundColor Yellow
    try {
        $eipId = aws ec2 allocate-address `
            --domain vpc `
            --tag-specifications 'ResourceType=elastic-ip,Tags=[{Key=goat-demo,Value=true},{Key=goat-scenario,Value=a},{Key=Name,Value=goat-demo-eip-unused},{Key=auto-delete,Value=no}]' `
            --query "AllocationId" --output text --region $region 2>&1
        if ($LASTEXITCODE -ne 0) { throw $eipId }
        Write-Host "  Allocated Elastic IP: $eipId (unassociated)" -ForegroundColor Green
    } catch {
        Write-Host "  WARNING: Failed to allocate Elastic IP: $_" -ForegroundColor Red
        $warnings += "Elastic IP allocation failed"
        $eipId = ""
    }
}

Write-Host ""

# ---------------------------------------------------------------------------
# 9. Create Support case (if Support plan is active)
# ---------------------------------------------------------------------------
Write-Host "--- Support Case ---" -ForegroundColor Magenta

Write-Host "Detecting Support plan..." -ForegroundColor Yellow
$supportCheck = aws support describe-services --query "services[0].code" --output text --region us-east-1 2>&1

if ($supportCheck -match "SubscriptionRequiredException") {
    Write-Host "  WARNING: No Business or Enterprise Support plan detected." -ForegroundColor Yellow
    Write-Host "  Skipping Support case creation. To enable this feature, upgrade your Support plan." -ForegroundColor Yellow
    $warnings += "Support case skipped - no Support plan"
    $supportCaseId = "skipped (no Support plan)"
} else {
    Write-Host "Creating Support case..." -ForegroundColor Yellow
    try {
        $supportCaseId = aws support create-case `
            --subject "General account review - G.O.A.T. demo" `
            --communication-body "This case was created for demo purposes by the G.O.A.T. provisioning scripts. It demonstrates cross-domain correlation in the AI Operations Assistant. No action is required." `
            --service-code "general-info" `
            --category-code "other" `
            --severity-code "low" `
            --language "en" `
            --query "caseId" --output text --region us-east-1 2>&1
        if ($LASTEXITCODE -ne 0) { throw $supportCaseId }
        Write-Host "  Created Support case: $supportCaseId" -ForegroundColor Green

        # Add demo-purpose communication
        aws support add-communication-to-case `
            --case-id $supportCaseId `
            --communication-body "This Support case was created automatically by the G.O.A.T. demo provisioning scripts for demonstration purposes only. It is being resolved immediately. No action is needed from AWS Support." `
            --region us-east-1 2>$null | Out-Null

        # Immediately resolve the case
        Write-Host "  Resolving Support case..." -ForegroundColor Yellow
        try {
            aws support resolve-case --case-id $supportCaseId --region us-east-1 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "resolve failed" }
            Write-Host "  Support case resolved: $supportCaseId" -ForegroundColor Green
        } catch {
            Write-Host "  WARNING: Failed to resolve Support case $supportCaseId" -ForegroundColor Red
            Write-Host "  Please close it manually via the AWS Console: https://console.aws.amazon.com/support/home" -ForegroundColor Red
            $warnings += "Support case resolve failed - close manually: $supportCaseId"
        }
    } catch {
        Write-Host "  WARNING: Failed to create Support case: $_" -ForegroundColor Red
        $warnings += "Support case creation failed"
        $supportCaseId = ""
    }
}

Write-Host ""

# ---------------------------------------------------------------------------
# 10. Summary
# ---------------------------------------------------------------------------
Write-Host "========================================" -ForegroundColor Green
Write-Host "  G.O.A.T. Scenario A Setup Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Region:              $region" -ForegroundColor Cyan

if (-not [string]::IsNullOrEmpty($vpcId) -and $vpcId -ne "None") {
    Write-Host "  VPC:                 $vpcId" -ForegroundColor Cyan
}
if (-not [string]::IsNullOrEmpty($subnet1Id) -and $subnet1Id -ne "None") {
    Write-Host "  Subnet 1:            $subnet1Id" -ForegroundColor Cyan
}
if (-not [string]::IsNullOrEmpty($subnet2Id) -and $subnet2Id -ne "None") {
    Write-Host "  Subnet 2:            $subnet2Id" -ForegroundColor Cyan
}
if (-not [string]::IsNullOrEmpty($dbSubnetGroup) -and $dbSubnetGroup -ne "None") {
    Write-Host "  DB Subnet Group:     $dbSubnetGroup" -ForegroundColor Cyan
}
if (-not [string]::IsNullOrEmpty($instance1Id) -and $instance1Id -ne "None") {
    Write-Host "  EC2 Instance 1:      $instance1Id" -ForegroundColor Cyan
}
if (-not [string]::IsNullOrEmpty($instance2Id) -and $instance2Id -ne "None") {
    Write-Host "  EC2 Instance 2:      $instance2Id" -ForegroundColor Cyan
}
if (-not [string]::IsNullOrEmpty($rdsId) -and $rdsId -ne "None") {
    Write-Host "  RDS Instance:        $rdsId (creating...)" -ForegroundColor Cyan
}
if (-not [string]::IsNullOrEmpty($ebsId) -and $ebsId -ne "None") {
    Write-Host "  EBS Volume:          $ebsId (unattached)" -ForegroundColor Cyan
}
if (-not [string]::IsNullOrEmpty($eipId) -and $eipId -ne "None") {
    Write-Host "  Elastic IP:          $eipId (unassociated)" -ForegroundColor Cyan
}
if (-not [string]::IsNullOrEmpty($supportCaseId)) {
    Write-Host "  Support Case:        $supportCaseId" -ForegroundColor Cyan
}

if ($warnings.Count -gt 0) {
    Write-Host ""
    Write-Host "  Warnings:" -ForegroundColor Yellow
    foreach ($w in $warnings) {
        Write-Host "    - $w" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "  Suggested Demo Query:" -ForegroundColor Cyan
Write-Host "    `"Give me a complete health check of my AWS account`"" -ForegroundColor Green
Write-Host ""
Write-Host "  To clean up all demo resources:" -ForegroundColor Gray
Write-Host "    .\cleanup-scenarios.ps1    (PowerShell)" -ForegroundColor Gray
Write-Host "    ./cleanup-scenarios.sh     (Bash)" -ForegroundColor Gray
Write-Host ""
