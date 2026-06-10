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
# Usage: .\cleanup-scenarios.ps1

$ErrorActionPreference = "Continue"

# ---------------------------------------------------------------------------
# Track removed resources for summary
# ---------------------------------------------------------------------------
$terminatedEc2 = @()
$deletedRds = @()
$deletedDbSubnetGroups = @()
$deletedEbs = @()
$releasedEip = @()
$deletedDdb = @()
$deletedSubnets = @()
$deletedVpcs = @()
$warnings = @()
$totalFound = 0

# Per-scenario counters
$countTlsFrag = 0
$hasErrors = $false

# ---------------------------------------------------------------------------
# 1. Verify AWS credentials
# ---------------------------------------------------------------------------
Write-Host "=== G.O.A.T. Demo Cleanup ===" -ForegroundColor Cyan
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
# 3. Terminate EC2 instances
# ---------------------------------------------------------------------------
Write-Host "--- EC2 Instances ---" -ForegroundColor Magenta

Write-Host "Finding EC2 instances tagged goat-demo=true..." -ForegroundColor Yellow
$ec2Arns = aws resourcegroupstaggingapi get-resources `
    --tag-filters Key=goat-demo,Values=true `
    --resource-type-filters ec2:instance `
    --query "ResourceTagMappingList[].ResourceARN" --output text --region $region 2>$null

if (-not [string]::IsNullOrEmpty($ec2Arns) -and $ec2Arns -ne "None") {
    foreach ($arn in ($ec2Arns -split '\s+')) {
        if ([string]::IsNullOrEmpty($arn)) { continue }
        # Extract instance ID from ARN
        if ($arn -match '(i-[a-f0-9]+)') {
            $instanceId = $Matches[1]
        } else { continue }
        $totalFound++

        # Check if instance is already terminated or gone
        try {
            $state = aws ec2 describe-instances `
                --instance-ids $instanceId `
                --query "Reservations[].Instances[].State.Name" --output text --region $region 2>&1
            if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrEmpty($state) -or $state -eq "None") {
                Write-Host "  Instance $instanceId no longer exists, skipping" -ForegroundColor Gray
                continue
            }
            if ($state -eq "terminated" -or $state -eq "shutting-down") {
                Write-Host "  Instance $instanceId already terminated, skipping" -ForegroundColor Gray
                continue
            }
        } catch {
            Write-Host "  Instance $instanceId no longer exists, skipping" -ForegroundColor Gray
            continue
        }

        Write-Host "  Terminating EC2 instance: $instanceId..." -ForegroundColor Yellow
        try {
            $result = aws ec2 terminate-instances --instance-ids $instanceId --region $region 2>&1
            if ($LASTEXITCODE -ne 0) { throw $result }
            Write-Host "  Terminated: $instanceId" -ForegroundColor Green
            $terminatedEc2 += $instanceId
        } catch {
            Write-Host "  WARNING: Failed to terminate $instanceId`: $_" -ForegroundColor Red
            $warnings += "EC2 terminate failed: $instanceId"
        }
    }

    # Wait for all EC2 instances to fully terminate before proceeding
    if ($terminatedEc2.Count -gt 0) {
        Write-Host "  Waiting for EC2 instances to fully terminate..." -ForegroundColor Gray
        foreach ($instId in $terminatedEc2) {
            $maxWait = 180
            $elapsed = 0
            while ($elapsed -lt $maxWait) {
                $instState = aws ec2 describe-instances --instance-ids $instId `
                    --query "Reservations[].Instances[].State.Name" --output text --region $region 2>$null
                if ($instState -eq "terminated" -or [string]::IsNullOrEmpty($instState)) { break }
                Start-Sleep -Seconds 15
                $elapsed += 15
            }
        }
        Write-Host "  All EC2 instances terminated" -ForegroundColor Green
    }
} else {
    Write-Host "  No EC2 instances found" -ForegroundColor Gray
}

Write-Host ""

# ---------------------------------------------------------------------------
# 4. Delete RDS instances (skip final snapshot)
# ---------------------------------------------------------------------------
Write-Host "--- RDS Instances ---" -ForegroundColor Magenta

Write-Host "Finding RDS instances tagged goat-demo=true..." -ForegroundColor Yellow
$rdsArns = aws resourcegroupstaggingapi get-resources `
    --tag-filters Key=goat-demo,Values=true `
    --resource-type-filters rds:db `
    --query "ResourceTagMappingList[].ResourceARN" --output text --region $region 2>$null

if (-not [string]::IsNullOrEmpty($rdsArns) -and $rdsArns -ne "None") {
    foreach ($arn in ($rdsArns -split '\s+')) {
        if ([string]::IsNullOrEmpty($arn)) { continue }
        # Extract DB instance identifier from ARN
        $dbId = ($arn -split ':db:')[-1]
        if ([string]::IsNullOrEmpty($dbId)) { continue }
        $totalFound++

        # Check if instance is already deleting
        try {
            $dbStatus = aws rds describe-db-instances `
                --db-instance-identifier $dbId `
                --query "DBInstances[0].DBInstanceStatus" --output text --region $region 2>$null
            if ($dbStatus -eq "deleting") {
                Write-Host "  RDS instance $dbId already deleting, skipping" -ForegroundColor Gray
                $deletedRds += $dbId
                continue
            }
        } catch { }

        Write-Host "  Deleting RDS instance: $dbId (skip-final-snapshot)..." -ForegroundColor Yellow
        Write-Host "  (This may take several minutes to complete)" -ForegroundColor Gray
        try {
            $result = aws rds delete-db-instance `
                --db-instance-identifier $dbId `
                --skip-final-snapshot `
                --delete-automated-backups `
                --region $region 2>&1
            if ($LASTEXITCODE -ne 0) { throw $result }
            Write-Host "  Deleting: $dbId (in progress)" -ForegroundColor Green
            $deletedRds += $dbId
        } catch {
            Write-Host "  WARNING: Failed to delete RDS $dbId`: $_" -ForegroundColor Red
            $warnings += "RDS delete failed: $dbId"
        }
    }
} else {
    Write-Host "  No RDS instances found" -ForegroundColor Gray
}

Write-Host ""

# ---------------------------------------------------------------------------
# 5. Delete DB subnet groups
# ---------------------------------------------------------------------------
Write-Host "--- DB Subnet Groups ---" -ForegroundColor Magenta

Write-Host "Checking for goat-demo DB subnet group..." -ForegroundColor Yellow
try {
    $dbSgCheck = aws rds describe-db-subnet-groups `
        --db-subnet-group-name goat-demo-db-subnet-group `
        --query "DBSubnetGroups[0].DBSubnetGroupName" --output text --region $region 2>$null
} catch { $dbSgCheck = "" }

if (-not [string]::IsNullOrEmpty($dbSgCheck) -and $dbSgCheck -ne "None") {
    $totalFound++

    Write-Host "  Deleting DB subnet group: goat-demo-db-subnet-group..." -ForegroundColor Yellow
    try {
        $result = aws rds delete-db-subnet-group `
            --db-subnet-group-name goat-demo-db-subnet-group `
            --region $region 2>&1
        if ($LASTEXITCODE -ne 0) { throw $result }
        Write-Host "  Deleted DB subnet group: goat-demo-db-subnet-group" -ForegroundColor Green
        $deletedDbSubnetGroups += "goat-demo-db-subnet-group"
    } catch {
        if ("$_" -match "is still being used") {
            Write-Host "  DB subnet group still in use (RDS deleting). Re-run cleanup after RDS deletion completes." -ForegroundColor Yellow
            $warnings += "DB subnet group in use - re-run cleanup later"
        } else {
            Write-Host "  WARNING: Failed to delete DB subnet group: $_" -ForegroundColor Red
            $warnings += "DB subnet group delete failed"
        }
    }
} else {
    Write-Host "  No DB subnet group found" -ForegroundColor Gray
}

Write-Host ""

# ---------------------------------------------------------------------------
# 6. Delete EBS volumes
# ---------------------------------------------------------------------------
Write-Host "--- EBS Volumes ---" -ForegroundColor Magenta

Write-Host "Finding EBS volumes tagged goat-demo=true..." -ForegroundColor Yellow
$ebsArns = aws resourcegroupstaggingapi get-resources `
    --tag-filters Key=goat-demo,Values=true `
    --resource-type-filters ec2:volume `
    --query "ResourceTagMappingList[].ResourceARN" --output text --region $region 2>$null

if (-not [string]::IsNullOrEmpty($ebsArns) -and $ebsArns -ne "None") {
    foreach ($arn in ($ebsArns -split '\s+')) {
        if ([string]::IsNullOrEmpty($arn)) { continue }
        # Extract volume ID from ARN
        if ($arn -match '(vol-[a-f0-9]+)') {
            $volId = $Matches[1]
        } else { continue }
        $totalFound++

        # Check volume state
        try {
            $volState = aws ec2 describe-volumes `
                --volume-ids $volId `
                --query "Volumes[0].State" --output text --region $region 2>$null
            if ([string]::IsNullOrEmpty($volState) -or $volState -eq "None") {
                Write-Host "  Volume $volId not found, skipping" -ForegroundColor Gray
                continue
            }
            if ($volState -eq "in-use") {
                Write-Host "  Volume $volId is in-use (attached). Will retry after EC2 termination completes." -ForegroundColor Yellow
                $warnings += "EBS volume in-use: $volId - re-run cleanup later"
                continue
            }
        } catch { }

        Write-Host "  Deleting EBS volume: $volId..." -ForegroundColor Yellow
        try {
            $result = aws ec2 delete-volume --volume-id $volId --region $region 2>&1
            if ($LASTEXITCODE -ne 0) { throw $result }
            Write-Host "  Deleted: $volId" -ForegroundColor Green
            $deletedEbs += $volId
        } catch {
            Write-Host "  WARNING: Failed to delete volume $volId`: $_" -ForegroundColor Red
            $warnings += "EBS delete failed: $volId"
        }
    }
} else {
    Write-Host "  No EBS volumes found" -ForegroundColor Gray
}

Write-Host ""

# ---------------------------------------------------------------------------
# 7. Delete NAT Gateways (must complete before EIPs and subnets)
# ---------------------------------------------------------------------------
Write-Host "--- NAT Gateways ---" -ForegroundColor Magenta

Write-Host "Finding NAT Gateways tagged goat-demo=true..." -ForegroundColor Yellow
$natGwIds = aws ec2 describe-nat-gateways `
    --filter "Name=tag:goat-demo,Values=true" "Name=state,Values=available,pending" `
    --query "NatGateways[].NatGatewayId" --output text --region $region 2>$null

if (-not [string]::IsNullOrEmpty($natGwIds) -and $natGwIds -ne "None") {
    foreach ($natId in ($natGwIds -split '\s+')) {
        if ([string]::IsNullOrEmpty($natId)) { continue }
        $totalFound++
        Write-Host "  Deleting NAT Gateway: $natId..." -ForegroundColor Yellow
        try {
            aws ec2 delete-nat-gateway --nat-gateway-id $natId --region $region 2>$null | Out-Null
            Write-Host "  Deleted: $natId (waiting for completion...)" -ForegroundColor Green
        } catch {
            Write-Host "  WARNING: Failed to delete NAT Gateway $natId`: $_" -ForegroundColor Red
            $warnings += "NAT Gateway delete failed: $natId"
        }
    }
    # Wait for all NAT Gateways to finish deleting before proceeding to EIPs/subnets
    Write-Host "  Waiting for NAT Gateways to fully delete..." -ForegroundColor Gray
    foreach ($natId in ($natGwIds -split '\s+')) {
        if ([string]::IsNullOrEmpty($natId)) { continue }
        $maxWait = 120
        $elapsed = 0
        while ($elapsed -lt $maxWait) {
            $natState = aws ec2 describe-nat-gateways --nat-gateway-ids $natId `
                --query "NatGateways[0].State" --output text --region $region 2>$null
            if ($natState -eq "deleted" -or $natState -eq "None" -or [string]::IsNullOrEmpty($natState)) { break }
            Start-Sleep -Seconds 10
            $elapsed += 10
        }
    }
    Write-Host "  NAT Gateways deleted" -ForegroundColor Green
} else {
    Write-Host "  No NAT Gateways found" -ForegroundColor Gray
}

Write-Host ""

# ---------------------------------------------------------------------------
# 8. Release Elastic IPs
# ---------------------------------------------------------------------------
Write-Host "--- Elastic IPs ---" -ForegroundColor Magenta

Write-Host "Finding Elastic IPs tagged goat-demo=true..." -ForegroundColor Yellow
$eipArns = aws resourcegroupstaggingapi get-resources `
    --tag-filters Key=goat-demo,Values=true `
    --resource-type-filters ec2:elastic-ip `
    --query "ResourceTagMappingList[].ResourceARN" --output text --region $region 2>$null

if (-not [string]::IsNullOrEmpty($eipArns) -and $eipArns -ne "None") {
    foreach ($arn in ($eipArns -split '\s+')) {
        if ([string]::IsNullOrEmpty($arn)) { continue }
        # Extract allocation ID from ARN
        if ($arn -match '(eipalloc-[a-f0-9]+)') {
            $allocId = $Matches[1]
        } else { continue }
        $totalFound++

        Write-Host "  Releasing Elastic IP: $allocId..." -ForegroundColor Yellow
        try {
            $result = aws ec2 release-address --allocation-id $allocId --region $region 2>&1
            if ($LASTEXITCODE -ne 0) { throw $result }
            Write-Host "  Released: $allocId" -ForegroundColor Green
            $releasedEip += $allocId
        } catch {
            if ("$_" -match "not found") {
                Write-Host "  Elastic IP $allocId already released, skipping" -ForegroundColor Gray
            } else {
                Write-Host "  WARNING: Failed to release $allocId`: $_" -ForegroundColor Red
                $warnings += "EIP release failed: $allocId"
            }
        }
    }
} else {
    Write-Host "  No Elastic IPs found" -ForegroundColor Gray
}

Write-Host ""

# ---------------------------------------------------------------------------
# 8. Delete DynamoDB tables
# ---------------------------------------------------------------------------
Write-Host "--- DynamoDB Tables ---" -ForegroundColor Magenta

Write-Host "Finding DynamoDB tables tagged goat-demo=true..." -ForegroundColor Yellow
$ddbArns = aws resourcegroupstaggingapi get-resources `
    --tag-filters Key=goat-demo,Values=true `
    --resource-type-filters dynamodb:table `
    --query "ResourceTagMappingList[].ResourceARN" --output text --region $region 2>$null

if (-not [string]::IsNullOrEmpty($ddbArns) -and $ddbArns -ne "None") {
    foreach ($arn in ($ddbArns -split '\s+')) {
        if ([string]::IsNullOrEmpty($arn)) { continue }
        # Extract table name from ARN
        $tableName = ($arn -split 'table/')[-1]
        if ([string]::IsNullOrEmpty($tableName)) { continue }
        $totalFound++

        Write-Host "  Deleting DynamoDB table: $tableName..." -ForegroundColor Yellow
        try {
            $result = aws dynamodb delete-table --table-name $tableName --region $region 2>&1
            if ($LASTEXITCODE -ne 0) { throw $result }
            Write-Host "  Deleted: $tableName" -ForegroundColor Green
            $deletedDdb += $tableName
        } catch {
            if ("$_" -match "not found|ResourceNotFoundException") {
                Write-Host "  Table $tableName already deleted, skipping" -ForegroundColor Gray
            } else {
                Write-Host "  WARNING: Failed to delete table $tableName`: $_" -ForegroundColor Red
                $warnings += "DynamoDB delete failed: $tableName"
            }
        }
    }
} else {
    Write-Host "  No DynamoDB tables found" -ForegroundColor Gray
}

Write-Host ""

# ---------------------------------------------------------------------------
# 9. Delete subnets
# ---------------------------------------------------------------------------

# IMPORTANT: Before deleting subnets, ensure any TGW attachments in those
# subnets are fully deleted. TGW attachments hold ENIs that block subnet deletion.
Write-Host "--- Pre-Subnet: Cleaning TGW Attachments ---" -ForegroundColor Magenta
$tgwAttachments = aws ec2 describe-transit-gateway-attachments `
    --filters "Name=tag:goat-demo,Values=true" "Name=state,Values=available,deleting,pendingAcceptance" `
    --query "TransitGatewayAttachments[].TransitGatewayAttachmentId" --output text --region $region 2>$null
if (-not [string]::IsNullOrEmpty($tgwAttachments) -and $tgwAttachments -ne "None") {
    foreach ($attId in ($tgwAttachments -split '\s+')) {
        if ([string]::IsNullOrEmpty($attId)) { continue }
        $attState = aws ec2 describe-transit-gateway-attachments --transit-gateway-attachment-ids $attId `
            --query "TransitGatewayAttachments[0].State" --output text --region $region 2>$null
        if ($attState -eq "available") {
            Write-Host "  Deleting TGW attachment: $attId..." -ForegroundColor Yellow
            aws ec2 delete-transit-gateway-vpc-attachment --transit-gateway-attachment-id $attId --region $region 2>$null | Out-Null
        }
    }
    # Wait for all TGW attachments to fully delete
    Write-Host "  Waiting for TGW attachments to fully delete..." -ForegroundColor Gray
    foreach ($attId in ($tgwAttachments -split '\s+')) {
        if ([string]::IsNullOrEmpty($attId)) { continue }
        $maxWait = 180; $elapsed = 0
        while ($elapsed -lt $maxWait) {
            $attState = aws ec2 describe-transit-gateway-attachments --transit-gateway-attachment-ids $attId `
                --query "TransitGatewayAttachments[0].State" --output text --region $region 2>$null
            if ($attState -eq "deleted" -or [string]::IsNullOrEmpty($attState) -or $attState -eq "None") { break }
            Start-Sleep -Seconds 15; $elapsed += 15
        }
    }
    Write-Host "  TGW attachments cleared" -ForegroundColor Green
} else {
    Write-Host "  No active TGW attachments found" -ForegroundColor Gray
}

# Also delete any Network Firewalls BEFORE subnets (NFW holds ENIs in firewall subnets)
$nfwList = aws network-firewall list-firewalls --query "Firewalls[?contains(FirewallName,'goat-demo')].FirewallName" --output text --region $region 2>$null
if (-not [string]::IsNullOrEmpty($nfwList) -and $nfwList -ne "None") {
    foreach ($nfwName in ($nfwList -split '\s+')) {
        if ([string]::IsNullOrEmpty($nfwName)) { continue }
        Write-Host "  Deleting Network Firewall: $nfwName..." -ForegroundColor Yellow
        aws network-firewall delete-firewall --firewall-name $nfwName --region $region 2>$null | Out-Null
        # Wait for firewall to be gone
        for ($i = 0; $i -lt 40; $i++) {
            Start-Sleep -Seconds 15
            $fwCheck = aws network-firewall describe-firewall --firewall-name $nfwName --query "Firewall.FirewallArn" --output text --region $region 2>$null
            if ([string]::IsNullOrEmpty($fwCheck) -or $fwCheck -eq "None") { break }
        }
        Write-Host "  Network Firewall deleted: $nfwName" -ForegroundColor Green
    }
}

Write-Host ""

Write-Host "--- Subnets ---" -ForegroundColor Magenta

Write-Host "Finding subnets tagged goat-demo=true..." -ForegroundColor Yellow
$subnetIds = aws ec2 describe-subnets `
    --filters "Name=tag:goat-demo,Values=true" `
    --query "Subnets[].SubnetId" --output text --region $region 2>$null

if (-not [string]::IsNullOrEmpty($subnetIds) -and $subnetIds -ne "None") {
    foreach ($subnetId in ($subnetIds -split '\s+')) {
        if ([string]::IsNullOrEmpty($subnetId)) { continue }
        $totalFound++

        Write-Host "  Deleting subnet: $subnetId..." -ForegroundColor Yellow
        $subnetRetries = 3
        $subnetDeleted = $false
        for ($attempt = 1; $attempt -le $subnetRetries; $attempt++) {
            try {
                $result = aws ec2 delete-subnet --subnet-id $subnetId --region $region 2>&1
                if ($LASTEXITCODE -ne 0) { throw $result }
                Write-Host "  Deleted: $subnetId" -ForegroundColor Green
                $deletedSubnets += $subnetId
                $subnetDeleted = $true
                break
            } catch {
                if ("$_" -match "not found") {
                    Write-Host "  Subnet $subnetId already deleted, skipping" -ForegroundColor Gray
                    $subnetDeleted = $true
                    break
                } elseif ("$_" -match "DependencyViolation" -and $attempt -lt $subnetRetries) {
                    Write-Host "  Subnet $subnetId has dependencies, waiting 20s... (attempt $attempt/$subnetRetries)" -ForegroundColor Gray
                    Start-Sleep -Seconds 20
                } elseif ("$_" -match "DependencyViolation") {
                    Write-Host "  Subnet $subnetId still has dependencies after retries. Skipping." -ForegroundColor Yellow
                    $warnings += "Subnet dependency: $subnetId"
                } else {
                    Write-Host "  WARNING: Failed to delete subnet $subnetId`: $_" -ForegroundColor Red
                    $warnings += "Subnet delete failed: $subnetId"
                    break
                }
            }
        }
    }
} else {
    Write-Host "  No subnets found" -ForegroundColor Gray
}

Write-Host ""

# ---------------------------------------------------------------------------
# 10. Delete VPCs
# ---------------------------------------------------------------------------
Write-Host "--- VPCs ---" -ForegroundColor Magenta

Write-Host "Finding VPCs tagged goat-demo=true..." -ForegroundColor Yellow
$vpcIds = aws ec2 describe-vpcs `
    --filters "Name=tag:goat-demo,Values=true" `
    --query "Vpcs[].VpcId" --output text --region $region 2>$null

if (-not [string]::IsNullOrEmpty($vpcIds) -and $vpcIds -ne "None") {
    foreach ($vpcId in ($vpcIds -split '\s+')) {
        if ([string]::IsNullOrEmpty($vpcId)) { continue }
        $totalFound++

        # Detach and delete any IGWs attached to this VPC
        $vpcIgwIds = aws ec2 describe-internet-gateways `
            --filters "Name=attachment.vpc-id,Values=$vpcId" `
            --query "InternetGateways[].InternetGatewayId" --output text --region $region 2>$null
        if (-not [string]::IsNullOrEmpty($vpcIgwIds) -and $vpcIgwIds -ne "None") {
            foreach ($igwId in ($vpcIgwIds -split '\s+')) {
                if ([string]::IsNullOrEmpty($igwId)) { continue }
                Write-Host "  Detaching IGW $igwId from VPC $vpcId..." -ForegroundColor Gray
                aws ec2 detach-internet-gateway --internet-gateway-id $igwId --vpc-id $vpcId --region $region 2>$null
                aws ec2 delete-internet-gateway --internet-gateway-id $igwId --region $region 2>$null
            }
        }

        # Delete any remaining subnets in this VPC
        $vpcSubnets = aws ec2 describe-subnets `
            --filters "Name=vpc-id,Values=$vpcId" `
            --query "Subnets[].SubnetId" --output text --region $region 2>$null
        if (-not [string]::IsNullOrEmpty($vpcSubnets) -and $vpcSubnets -ne "None") {
            foreach ($subId in ($vpcSubnets -split '\s+')) {
                if ([string]::IsNullOrEmpty($subId)) { continue }
                aws ec2 delete-subnet --subnet-id $subId --region $region 2>$null
            }
        }

        # Delete non-default route tables
        $vpcRts = aws ec2 describe-route-tables `
            --filters "Name=vpc-id,Values=$vpcId" `
            --query "RouteTables[?Associations[0].Main!=``true``].RouteTableId" --output text --region $region 2>$null
        if (-not [string]::IsNullOrEmpty($vpcRts) -and $vpcRts -ne "None") {
            foreach ($rtId in ($vpcRts -split '\s+')) {
                if ([string]::IsNullOrEmpty($rtId)) { continue }
                # Disassociate first
                $assocIds = aws ec2 describe-route-tables --route-table-ids $rtId `
                    --query "RouteTables[0].Associations[?!Main].RouteTableAssociationId" --output text --region $region 2>$null
                if (-not [string]::IsNullOrEmpty($assocIds) -and $assocIds -ne "None") {
                    foreach ($assocId in ($assocIds -split '\s+')) {
                        aws ec2 disassociate-route-table --association-id $assocId --region $region 2>$null
                    }
                }
                aws ec2 delete-route-table --route-table-id $rtId --region $region 2>$null
            }
        }

        Write-Host "  Deleting VPC: $vpcId..." -ForegroundColor Yellow
        $vpcRetries = 3
        $vpcDeleted = $false
        for ($attempt = 1; $attempt -le $vpcRetries; $attempt++) {
            try {
                $result = aws ec2 delete-vpc --vpc-id $vpcId --region $region 2>&1
                if ($LASTEXITCODE -ne 0) { throw $result }
                Write-Host "  Deleted: $vpcId" -ForegroundColor Green
                $deletedVpcs += $vpcId
                $vpcDeleted = $true
                break
            } catch {
                if ("$_" -match "not found") {
                    Write-Host "  VPC $vpcId already deleted, skipping" -ForegroundColor Gray
                    $vpcDeleted = $true
                    break
                } elseif ("$_" -match "DependencyViolation" -and $attempt -lt $vpcRetries) {
                    Write-Host "  VPC $vpcId has dependencies, waiting 30s... (attempt $attempt/$vpcRetries)" -ForegroundColor Gray
                    Start-Sleep -Seconds 30
                } elseif ("$_" -match "DependencyViolation") {
                    Write-Host "  VPC $vpcId still has dependencies after retries. Skipping." -ForegroundColor Yellow
                    $warnings += "VPC dependency: $vpcId"
                } else {
                    Write-Host "  WARNING: Failed to delete VPC $vpcId`: $_" -ForegroundColor Red
                    $warnings += "VPC delete failed: $vpcId"
                    break
                }
            }
        }
    }
} else {
    Write-Host "  No VPCs found" -ForegroundColor Gray
}

Write-Host ""

# ---------------------------------------------------------------------------
# 11. TLS Fragmentation Scenario Cleanup (goat-scenario=tls-fragmentation)
# ---------------------------------------------------------------------------
Write-Host "--- TLS Fragmentation Scenario (goat-scenario=tls-fragmentation) ---" -ForegroundColor Magenta
Write-Host ""

# The TLS scenario uses the shared goat-demo-vpc (not a dedicated VPC).
# Check for any TLS-tagged subnets to determine if cleanup is needed.
$tlsSubnetCheck = aws ec2 describe-subnets `
    --filters "Name=tag:goat-scenario,Values=tls-fragmentation" `
    --query "Subnets[0].SubnetId" --output text --region $region 2>$null

# Also check for EKS clusters tagged for TLS scenario
$tlsEksCheck = ""
$eksClusters = aws eks list-clusters --query "clusters" --output json --region $region 2>$null | ConvertFrom-Json
if ($eksClusters) {
    foreach ($cluster in $eksClusters) {
        try {
            $clusterTags = aws eks describe-cluster --name $cluster --query "cluster.tags" --output json --region $region 2>$null | ConvertFrom-Json
            if ($clusterTags.'goat-scenario' -eq "tls-fragmentation") {
                $tlsEksCheck = $cluster
                break
            }
        } catch { }
    }
}

# Also check for a Transit Gateway tagged for the TLS scenario (it may outlive subnets)
$tlsTgwCheck = aws ec2 describe-transit-gateways `
    --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=state,Values=available,pending,modifying" `
    --query "TransitGateways[0].TransitGatewayId" --output text --region $region 2>$null

$hasTlsResources = ((-not [string]::IsNullOrEmpty($tlsSubnetCheck) -and $tlsSubnetCheck -ne "None") -or (-not [string]::IsNullOrEmpty($tlsEksCheck)) -or (-not [string]::IsNullOrEmpty($tlsTgwCheck) -and $tlsTgwCheck -ne "None"))

if ($hasTlsResources) {
    $totalFound++

    # 11a. Delete Kubernetes test pod and EKS resources
    Write-Host "Finding EKS clusters tagged goat-scenario=tls-fragmentation..." -ForegroundColor Yellow
    $eksClusterName = $tlsEksCheck

    if (-not [string]::IsNullOrEmpty($eksClusterName)) {
        $totalFound++

        # Delete test pod (best-effort via kubectl if available)
        Write-Host "  Attempting to delete TLS test pod..." -ForegroundColor Yellow
        aws eks update-kubeconfig --name $eksClusterName --region $region 2>$null
        $kubectlAvailable = Get-Command kubectl -ErrorAction SilentlyContinue
        if ($kubectlAvailable) {
            kubectl delete pod -l app=goat-tls-test --ignore-not-found=true 2>$null
            kubectl delete deployment -l app=goat-tls-test --ignore-not-found=true 2>$null
            Write-Host "  Test pod deletion initiated" -ForegroundColor Green
            $countTlsFrag++
        } else {
            Write-Host "  kubectl not available, skipping pod deletion (EKS cluster deletion will remove pods)" -ForegroundColor Yellow
        }

        # Delete managed node groups
        Write-Host "  Finding managed node groups..." -ForegroundColor Yellow
        $nodeGroups = aws eks list-nodegroups --cluster-name $eksClusterName `
            --query "nodegroups" --output json --region $region 2>$null | ConvertFrom-Json
        if ($nodeGroups -and $nodeGroups.Count -gt 0) {
            foreach ($ng in $nodeGroups) {
                Write-Host "  Deleting node group: $ng..." -ForegroundColor Yellow
                try {
                    $result = aws eks delete-nodegroup --cluster-name $eksClusterName `
                        --nodegroup-name $ng --region $region 2>&1
                    if ($LASTEXITCODE -ne 0) { throw $result }
                    Write-Host "  Deleting node group: $ng (waiting for completion...)" -ForegroundColor Green
                    aws eks wait nodegroup-deleted --cluster-name $eksClusterName `
                        --nodegroup-name $ng --region $region 2>$null
                    $countTlsFrag++
                } catch {
                    if ("$_" -match "ResourceNotFoundException|not found|No node group") {
                        Write-Host "  Node group $ng already deleted, skipping" -ForegroundColor Gray
                    } else {
                        Write-Host "  WARNING: Failed to delete node group $ng`: $_" -ForegroundColor Red
                        $warnings += "EKS node group delete failed: $ng"
                        $hasErrors = $true
                    }
                }
            }
        }

        # Delete EKS cluster
        Write-Host "  Deleting EKS cluster: $eksClusterName..." -ForegroundColor Yellow
        try {
            $result = aws eks delete-cluster --name $eksClusterName --region $region 2>&1
            if ($LASTEXITCODE -ne 0) { throw $result }
            Write-Host "  Deleting EKS cluster: $eksClusterName (waiting for completion...)" -ForegroundColor Green
            aws eks wait cluster-deleted --name $eksClusterName --region $region 2>$null
            $countTlsFrag++
        } catch {
            if ("$_" -match "ResourceNotFoundException|not found") {
                Write-Host "  EKS cluster $eksClusterName already deleted, skipping" -ForegroundColor Gray
            } else {
                Write-Host "  WARNING: Failed to delete EKS cluster $eksClusterName`: $_" -ForegroundColor Red
                $warnings += "EKS cluster delete failed: $eksClusterName"
                $hasErrors = $true
            }
        }
    } else {
        Write-Host "  No EKS cluster found for tls-fragmentation scenario" -ForegroundColor Gray
    }

    # 11a2. Delete Transit Gateway attachments, route table, and the TGW itself.
    # Must happen BEFORE subnet deletion (attachments hold ENIs in the TGW subnets)
    # and before VPC deletion.
    Write-Host "  Finding Transit Gateway tagged goat-scenario=tls-fragmentation..." -ForegroundColor Yellow
    $tlsTgwId = aws ec2 describe-transit-gateways `
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=state,Values=available,pending,modifying" `
        --query "TransitGateways[0].TransitGatewayId" --output text --region $region 2>$null

    if (-not [string]::IsNullOrEmpty($tlsTgwId) -and $tlsTgwId -ne "None") {
        $totalFound++

        # Delete VPC attachments first and wait for them to be gone
        $tgwAttachIds = aws ec2 describe-transit-gateway-attachments `
            --filters "Name=transit-gateway-id,Values=$tlsTgwId" "Name=state,Values=available,pending,modifying" `
            --query "TransitGatewayAttachments[].TransitGatewayAttachmentId" --output text --region $region 2>$null
        if (-not [string]::IsNullOrEmpty($tgwAttachIds) -and $tgwAttachIds -ne "None") {
            foreach ($attId in ($tgwAttachIds -split '\s+')) {
                if ([string]::IsNullOrEmpty($attId)) { continue }
                Write-Host "  Deleting TGW attachment: $attId..." -ForegroundColor Yellow
                aws ec2 delete-transit-gateway-vpc-attachment --transit-gateway-attachment-id $attId --region $region 2>$null | Out-Null
                $countTlsFrag++
            }
            # Wait for attachments to be deleted (they block subnet/VPC deletion)
            Write-Host "  Waiting for TGW attachments to delete..." -ForegroundColor Gray
            $maxWait = 300; $elapsed = 0
            while ($elapsed -lt $maxWait) {
                $remaining = aws ec2 describe-transit-gateway-attachments `
                    --filters "Name=transit-gateway-id,Values=$tlsTgwId" "Name=state,Values=available,pending,modifying,deleting" `
                    --query "length(TransitGatewayAttachments)" --output text --region $region 2>$null
                if ($remaining -eq "0" -or [string]::IsNullOrEmpty($remaining) -or $remaining -eq "None") { break }
                Start-Sleep -Seconds 15; $elapsed += 15
            }
        }

        # Delete the custom TGW route table (default RT cannot be deleted; ignore errors)
        $tgwRtIds = aws ec2 describe-transit-gateway-route-tables `
            --filters "Name=transit-gateway-id,Values=$tlsTgwId" "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=state,Values=available,pending" `
            --query "TransitGatewayRouteTables[].TransitGatewayRouteTableId" --output text --region $region 2>$null
        if (-not [string]::IsNullOrEmpty($tgwRtIds) -and $tgwRtIds -ne "None") {
            foreach ($rtId in ($tgwRtIds -split '\s+')) {
                if ([string]::IsNullOrEmpty($rtId)) { continue }
                Write-Host "  Deleting TGW route table: $rtId..." -ForegroundColor Yellow
                aws ec2 delete-transit-gateway-route-table --transit-gateway-route-table-id $rtId --region $region 2>$null | Out-Null
                $countTlsFrag++
            }
            Start-Sleep -Seconds 10
        }

        # Delete the Transit Gateway
        Write-Host "  Deleting Transit Gateway: $tlsTgwId..." -ForegroundColor Yellow
        try {
            $result = aws ec2 delete-transit-gateway --transit-gateway-id $tlsTgwId --region $region 2>&1
            if ($LASTEXITCODE -ne 0) { throw $result }
            Write-Host "  Transit Gateway deletion initiated: $tlsTgwId" -ForegroundColor Green
            $countTlsFrag++
        } catch {
            if ("$_" -match "not found|InvalidTransitGatewayID") {
                Write-Host "  Transit Gateway already deleted, skipping" -ForegroundColor Gray
            } else {
                Write-Host "  WARNING: Failed to delete Transit Gateway $tlsTgwId`: $_" -ForegroundColor Red
                $warnings += "TGW delete failed: $tlsTgwId"
                $hasErrors = $true
            }
        }
    } else {
        Write-Host "  No Transit Gateway found for tls-fragmentation scenario" -ForegroundColor Gray
    }

    # 11b. Delete Network Firewall
    Write-Host "  Finding Network Firewall resources..." -ForegroundColor Yellow
    $nfwName = "goat-demo-tls-nfw"
    try {
        $nfwStatus = aws network-firewall describe-firewall --firewall-name $nfwName `
            --query "Firewall.FirewallArn" --output text --region $region 2>$null
    } catch { $nfwStatus = "" }

    if (-not [string]::IsNullOrEmpty($nfwStatus) -and $nfwStatus -ne "None") {
        $totalFound++

        # A firewall with a logging configuration cannot be deleted. Remove log
        # destinations one at a time (the API rejects removing multiple at once),
        # then delete the firewall.
        $logConfig = aws network-firewall describe-logging-configuration --firewall-name $nfwName `
            --query "LoggingConfiguration.LogDestinationConfigs[].LogType" --output text --region $region 2>$null
        if (-not [string]::IsNullOrEmpty($logConfig) -and $logConfig -ne "None") {
            Write-Host "  Clearing firewall logging configuration before delete..." -ForegroundColor Yellow
            $flowGroup = "/aws/network-firewall/goat-demo-tls-flow"
            # Step 1: keep only FLOW (removes ALERT if present)
            $keepFlow = "{`"LogDestinationConfigs`":[{`"LogType`":`"FLOW`",`"LogDestinationType`":`"CloudWatchLogs`",`"LogDestination`":{`"logGroup`":`"$flowGroup`"}}]}"
            $lf1 = [System.IO.Path]::GetTempFileName()
            [System.IO.File]::WriteAllText($lf1, $keepFlow)
            aws network-firewall update-logging-configuration --firewall-name $nfwName --logging-configuration "file://$lf1" --region $region 2>$null | Out-Null
            Remove-Item $lf1 -ErrorAction SilentlyContinue
            # Step 2: remove the remaining FLOW destination (empty config)
            $lf2 = [System.IO.Path]::GetTempFileName()
            [System.IO.File]::WriteAllText($lf2, '{"LogDestinationConfigs":[]}')
            aws network-firewall update-logging-configuration --firewall-name $nfwName --logging-configuration "file://$lf2" --region $region 2>$null | Out-Null
            Remove-Item $lf2 -ErrorAction SilentlyContinue
        }

        Write-Host "  Deleting Network Firewall: $nfwName..." -ForegroundColor Yellow
        try {
            $result = aws network-firewall delete-firewall --firewall-name $nfwName --region $region 2>&1
            if ($LASTEXITCODE -ne 0) { throw $result }
            Write-Host "  Deleting Network Firewall: $nfwName (waiting for completion...)" -ForegroundColor Green
            # Wait for firewall deletion (poll status)
            for ($i = 0; $i -lt 60; $i++) {
                Start-Sleep -Seconds 10
                try {
                    $fwCheck = aws network-firewall describe-firewall --firewall-name $nfwName `
                        --query "Firewall.FirewallArn" --output text --region $region 2>$null
                    if ([string]::IsNullOrEmpty($fwCheck) -or $fwCheck -eq "None") { break }
                } catch { break }
            }
            $countTlsFrag++
        } catch {
            if ("$_" -match "ResourceNotFoundException|not found") {
                Write-Host "  Network Firewall $nfwName already deleted, skipping" -ForegroundColor Gray
            } else {
                Write-Host "  WARNING: Failed to delete Network Firewall $nfwName`: $_" -ForegroundColor Red
                $warnings += "Network Firewall delete failed: $nfwName"
                $hasErrors = $true
            }
        }
    } else {
        Write-Host "  No Network Firewall found" -ForegroundColor Gray
    }

    # Delete firewall policy
    $nfwPolicyName = "goat-demo-tls-policy"
    try {
        $policyCheck = aws network-firewall describe-firewall-policy --firewall-policy-name $nfwPolicyName `
            --query "FirewallPolicyResponse.FirewallPolicyArn" --output text --region $region 2>$null
    } catch { $policyCheck = "" }
    if (-not [string]::IsNullOrEmpty($policyCheck) -and $policyCheck -ne "None") {
        $totalFound++
        Write-Host "  Deleting firewall policy: $nfwPolicyName..." -ForegroundColor Yellow
        try {
            $result = aws network-firewall delete-firewall-policy --firewall-policy-name $nfwPolicyName --region $region 2>&1
            if ($LASTEXITCODE -ne 0) { throw $result }
            Write-Host "  Deleted firewall policy: $nfwPolicyName" -ForegroundColor Green
            $countTlsFrag++
        } catch {
            if ("$_" -match "ResourceNotFoundException|not found") {
                Write-Host "  Firewall policy already deleted, skipping" -ForegroundColor Gray
            } else {
                Write-Host "  WARNING: Failed to delete firewall policy: $_" -ForegroundColor Red
                $warnings += "Firewall policy delete failed: $nfwPolicyName"
                $hasErrors = $true
            }
        }
    }

    # Delete firewall rule group (may need retries if policy deletion hasn't propagated)
    $nfwRuleGroupName = "goat-demo-tls-rules"
    try {
        $rgCheck = aws network-firewall describe-rule-group --rule-group-name $nfwRuleGroupName --type STATEFUL `
            --query "RuleGroupResponse.RuleGroupArn" --output text --region $region 2>$null
    } catch { $rgCheck = "" }
    if (-not [string]::IsNullOrEmpty($rgCheck) -and $rgCheck -ne "None") {
        $totalFound++
        Write-Host "  Deleting firewall rule group: $nfwRuleGroupName..." -ForegroundColor Yellow
        $rgDeleted = $false
        for ($attempt = 1; $attempt -le 5; $attempt++) {
            try {
                $result = aws network-firewall delete-rule-group --rule-group-name $nfwRuleGroupName --type STATEFUL --region $region 2>&1
                if ($LASTEXITCODE -ne 0) { throw $result }
                Write-Host "  Deleted firewall rule group: $nfwRuleGroupName" -ForegroundColor Green
                $countTlsFrag++
                $rgDeleted = $true
                break
            } catch {
                if ("$_" -match "ResourceNotFoundException|not found") {
                    Write-Host "  Rule group already deleted, skipping" -ForegroundColor Gray
                    $rgDeleted = $true
                    break
                } elseif ("$_" -match "InvalidOperationException|still in use" -and $attempt -lt 5) {
                    Write-Host "  Rule group still in use, waiting 15s... (attempt $attempt/5)" -ForegroundColor Gray
                    Start-Sleep -Seconds 15
                } else {
                    Write-Host "  WARNING: Failed to delete rule group after retries: $_" -ForegroundColor Red
                    $warnings += "Firewall rule group delete failed: $nfwRuleGroupName"
                    $hasErrors = $true
                    break
                }
            }
        }
    }

    # 11c. Delete NAT Gateway
    Write-Host "  Finding NAT Gateways tagged goat-scenario=tls-fragmentation..." -ForegroundColor Yellow
    $natGwIds = aws ec2 describe-nat-gateways `
        --filter "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=state,Values=available,pending" `
        --query "NatGateways[].NatGatewayId" --output text --region $region 2>$null
    if (-not [string]::IsNullOrEmpty($natGwIds) -and $natGwIds -ne "None") {
        foreach ($natId in ($natGwIds -split '\s+')) {
            if ([string]::IsNullOrEmpty($natId)) { continue }
            $totalFound++
            Write-Host "  Deleting NAT Gateway: $natId..." -ForegroundColor Yellow
            try {
                $result = aws ec2 delete-nat-gateway --nat-gateway-id $natId --region $region 2>&1
                if ($LASTEXITCODE -ne 0) { throw $result }
                Write-Host "  Deleting NAT Gateway: $natId (waiting for completion...)" -ForegroundColor Green
                # Wait for NAT Gateway deletion
                for ($i = 0; $i -lt 30; $i++) {
                    Start-Sleep -Seconds 10
                    $natState = aws ec2 describe-nat-gateways --nat-gateway-ids $natId `
                        --query "NatGateways[0].State" --output text --region $region 2>$null
                    if ($natState -eq "deleted" -or [string]::IsNullOrEmpty($natState) -or $natState -eq "None") { break }
                }
                $countTlsFrag++
            } catch {
                if ("$_" -match "not found|NatGatewayNotFound") {
                    Write-Host "  NAT Gateway $natId already deleted, skipping" -ForegroundColor Gray
                } else {
                    Write-Host "  WARNING: Failed to delete NAT Gateway $natId`: $_" -ForegroundColor Red
                    $warnings += "NAT Gateway delete failed: $natId"
                    $hasErrors = $true
                }
            }
        }
    } else {
        Write-Host "  No NAT Gateways found" -ForegroundColor Gray
    }

    # 11d. Detach and delete Internet Gateway
    Write-Host "  Finding Internet Gateways tagged goat-scenario=tls-fragmentation..." -ForegroundColor Yellow
    $igwIds = aws ec2 describe-internet-gateways `
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" `
        --query "InternetGateways[].InternetGatewayId" --output text --region $region 2>$null
    if (-not [string]::IsNullOrEmpty($igwIds) -and $igwIds -ne "None") {
        foreach ($igwId in ($igwIds -split '\s+')) {
            if ([string]::IsNullOrEmpty($igwId)) { continue }
            $totalFound++
            # Detach from VPC first — look up the attached VPC from the IGW itself
            $igwVpcId = aws ec2 describe-internet-gateways --internet-gateway-ids $igwId `
                --query "InternetGateways[0].Attachments[0].VpcId" --output text --region $region 2>$null
            if (-not [string]::IsNullOrEmpty($igwVpcId) -and $igwVpcId -ne "None") {
                aws ec2 detach-internet-gateway --internet-gateway-id $igwId --vpc-id $igwVpcId --region $region 2>$null
            }
            Write-Host "  Deleting Internet Gateway: $igwId..." -ForegroundColor Yellow
            try {
                $result = aws ec2 delete-internet-gateway --internet-gateway-id $igwId --region $region 2>&1
                if ($LASTEXITCODE -ne 0) { throw $result }
                Write-Host "  Deleted Internet Gateway: $igwId" -ForegroundColor Green
                $countTlsFrag++
            } catch {
                if ("$_" -match "not found|InvalidInternetGatewayID") {
                    Write-Host "  Internet Gateway $igwId already deleted, skipping" -ForegroundColor Gray
                } else {
                    Write-Host "  WARNING: Failed to delete Internet Gateway $igwId`: $_" -ForegroundColor Red
                    $warnings += "Internet Gateway delete failed: $igwId"
                    $hasErrors = $true
                }
            }
        }
    } else {
        Write-Host "  No Internet Gateways found" -ForegroundColor Gray
    }

    # 11e. Delete subnets tagged goat-scenario=tls-fragmentation
    Write-Host "  Finding subnets tagged goat-scenario=tls-fragmentation..." -ForegroundColor Yellow
    $tlsSubnetIds = aws ec2 describe-subnets `
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" `
        --query "Subnets[].SubnetId" --output text --region $region 2>$null
    if (-not [string]::IsNullOrEmpty($tlsSubnetIds) -and $tlsSubnetIds -ne "None") {
        foreach ($subnetId in ($tlsSubnetIds -split '\s+')) {
            if ([string]::IsNullOrEmpty($subnetId)) { continue }
            $totalFound++
            Write-Host "  Deleting subnet: $subnetId..." -ForegroundColor Yellow
            try {
                $result = aws ec2 delete-subnet --subnet-id $subnetId --region $region 2>&1
                if ($LASTEXITCODE -ne 0) { throw $result }
                Write-Host "  Deleted subnet: $subnetId" -ForegroundColor Green
                $countTlsFrag++
            } catch {
                if ("$_" -match "not found|InvalidSubnetID") {
                    Write-Host "  Subnet $subnetId already deleted, skipping" -ForegroundColor Gray
                } else {
                    Write-Host "  WARNING: Failed to delete subnet $subnetId`: $_" -ForegroundColor Red
                    $warnings += "TLS subnet delete failed: $subnetId"
                    $hasErrors = $true
                }
            }
        }
    } else {
        Write-Host "  No TLS scenario subnets found" -ForegroundColor Gray
    }

    # 11f. Delete route tables tagged goat-scenario=tls-fragmentation
    Write-Host "  Finding route tables tagged goat-scenario=tls-fragmentation..." -ForegroundColor Yellow
    $tlsRtIds = aws ec2 describe-route-tables `
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" `
        --query "RouteTables[].RouteTableId" --output text --region $region 2>$null
    if (-not [string]::IsNullOrEmpty($tlsRtIds) -and $tlsRtIds -ne "None") {
        foreach ($rtId in ($tlsRtIds -split '\s+')) {
            if ([string]::IsNullOrEmpty($rtId)) { continue }
            $totalFound++
            # Disassociate any subnet associations first (skip main)
            $assocIds = aws ec2 describe-route-tables --route-table-ids $rtId `
                --query "RouteTables[0].Associations[?!Main].RouteTableAssociationId" --output text --region $region 2>$null
            if (-not [string]::IsNullOrEmpty($assocIds) -and $assocIds -ne "None") {
                foreach ($assocId in ($assocIds -split '\s+')) {
                    if (-not [string]::IsNullOrEmpty($assocId)) {
                        aws ec2 disassociate-route-table --association-id $assocId --region $region 2>$null
                    }
                }
            }
            Write-Host "  Deleting route table: $rtId..." -ForegroundColor Yellow
            try {
                $result = aws ec2 delete-route-table --route-table-id $rtId --region $region 2>&1
                if ($LASTEXITCODE -ne 0) { throw $result }
                Write-Host "  Deleted route table: $rtId" -ForegroundColor Green
                $countTlsFrag++
            } catch {
                if ("$_" -match "not found|InvalidRouteTableID") {
                    Write-Host "  Route table $rtId already deleted, skipping" -ForegroundColor Gray
                } else {
                    Write-Host "  WARNING: Failed to delete route table $rtId`: $_" -ForegroundColor Red
                    $warnings += "TLS route table delete failed: $rtId"
                    $hasErrors = $true
                }
            }
        }
    } else {
        Write-Host "  No TLS scenario route tables found" -ForegroundColor Gray
    }

    # 11g. Delete security groups tagged goat-scenario=tls-fragmentation (non-default only)
    Write-Host "  Finding security groups tagged goat-scenario=tls-fragmentation..." -ForegroundColor Yellow
    $tlsSgIds = aws ec2 describe-security-groups `
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" `
        --query "SecurityGroups[?GroupName!='default'].GroupId" --output text --region $region 2>$null
    if (-not [string]::IsNullOrEmpty($tlsSgIds) -and $tlsSgIds -ne "None") {
        foreach ($sgId in ($tlsSgIds -split '\s+')) {
            if ([string]::IsNullOrEmpty($sgId)) { continue }
            $totalFound++
            Write-Host "  Deleting security group: $sgId..." -ForegroundColor Yellow
            try {
                $result = aws ec2 delete-security-group --group-id $sgId --region $region 2>&1
                if ($LASTEXITCODE -ne 0) { throw $result }
                Write-Host "  Deleted security group: $sgId" -ForegroundColor Green
                $countTlsFrag++
            } catch {
                if ("$_" -match "not found|InvalidGroup") {
                    Write-Host "  Security group $sgId already deleted, skipping" -ForegroundColor Gray
                } else {
                    Write-Host "  WARNING: Failed to delete security group $sgId`: $_" -ForegroundColor Red
                    $warnings += "TLS security group delete failed: $sgId"
                    $hasErrors = $true
                }
            }
        }
    } else {
        Write-Host "  No TLS-tagged security groups found" -ForegroundColor Gray
    }

    # Note: The shared VPC (goat-demo-vpc) is cleaned up by section 10 above.
    # No dedicated TLS VPC deletion needed.

    # Release any EIPs tagged for TLS scenario
    Write-Host "  Finding Elastic IPs tagged goat-scenario=tls-fragmentation..." -ForegroundColor Yellow
    $tlsEipArns = aws resourcegroupstaggingapi get-resources `
        --tag-filters Key=goat-scenario,Values=tls-fragmentation `
        --resource-type-filters ec2:elastic-ip `
        --query "ResourceTagMappingList[].ResourceARN" --output text --region $region 2>$null
    if (-not [string]::IsNullOrEmpty($tlsEipArns) -and $tlsEipArns -ne "None") {
        foreach ($arn in ($tlsEipArns -split '\s+')) {
            if ([string]::IsNullOrEmpty($arn)) { continue }
            if ($arn -match '(eipalloc-[a-f0-9]+)') {
                $allocId = $Matches[1]
            } else { continue }
            $totalFound++
            Write-Host "  Releasing Elastic IP: $allocId..." -ForegroundColor Yellow
            try {
                $result = aws ec2 release-address --allocation-id $allocId --region $region 2>&1
                if ($LASTEXITCODE -ne 0) { throw $result }
                Write-Host "  Released: $allocId" -ForegroundColor Green
                $countTlsFrag++
            } catch {
                if ("$_" -match "not found|InvalidAllocationID") {
                    Write-Host "  Elastic IP $allocId already released, skipping" -ForegroundColor Gray
                } else {
                    Write-Host "  WARNING: Failed to release $allocId`: $_" -ForegroundColor Red
                    $warnings += "TLS EIP release failed: $allocId"
                    $hasErrors = $true
                }
            }
        }
    }

    # 11g2. Delete firewall CloudWatch log groups and scenario IAM roles.
    Write-Host "  Deleting firewall CloudWatch log groups..." -ForegroundColor Yellow
    foreach ($lg in @("/aws/network-firewall/goat-demo-tls-flow", "/aws/network-firewall/goat-demo-tls-alert")) {
        $lgCheck = aws logs describe-log-groups --log-group-name-prefix $lg --query "logGroups[0].logGroupName" --output text --region $region 2>$null
        if (-not [string]::IsNullOrEmpty($lgCheck) -and $lgCheck -ne "None") {
            aws logs delete-log-group --log-group-name $lg --region $region 2>$null
            Write-Host "  Deleted log group: $lg" -ForegroundColor Green
            $countTlsFrag++
        }
    }

    Write-Host "  Deleting scenario IAM roles..." -ForegroundColor Yellow
    foreach ($roleName in @("goat-demo-tls-eks-role", "goat-demo-tls-node-role", "goat-demo-tls-ssm-role")) {
        $roleCheck = aws iam get-role --role-name $roleName --query "Role.RoleName" --output text 2>$null
        if (-not [string]::IsNullOrEmpty($roleCheck) -and $roleCheck -ne "None") {
            # Remove from instance profile first (SSM role)
            $profileName = $roleName -replace '-role$', '-profile'
            $profCheck = aws iam get-instance-profile --instance-profile-name $profileName --query "InstanceProfile.InstanceProfileName" --output text 2>$null
            if (-not [string]::IsNullOrEmpty($profCheck) -and $profCheck -ne "None" -and $LASTEXITCODE -eq 0) {
                aws iam remove-role-from-instance-profile --instance-profile-name $profileName --role-name $roleName 2>$null
                aws iam delete-instance-profile --instance-profile-name $profileName 2>$null
                Write-Host "  Deleted instance profile: $profileName" -ForegroundColor Green
            }
            # Detach managed policies
            $attached = aws iam list-attached-role-policies --role-name $roleName --query "AttachedPolicies[].PolicyArn" --output text 2>$null
            if (-not [string]::IsNullOrEmpty($attached) -and $attached -ne "None") {
                foreach ($p in ($attached -split '\s+')) {
                    if (-not [string]::IsNullOrEmpty($p)) { aws iam detach-role-policy --role-name $roleName --policy-arn $p 2>$null }
                }
            }
            # Delete inline policies
            $inline = aws iam list-role-policies --role-name $roleName --query "PolicyNames" --output text 2>$null
            if (-not [string]::IsNullOrEmpty($inline) -and $inline -ne "None") {
                foreach ($ip in ($inline -split '\s+')) {
                    if (-not [string]::IsNullOrEmpty($ip)) { aws iam delete-role-policy --role-name $roleName --policy-name $ip 2>$null }
                }
            }
            aws iam delete-role --role-name $roleName 2>$null | Out-Null
            Write-Host "  Deleted IAM role: $roleName" -ForegroundColor Green
            $countTlsFrag++
        }
    }

    # 11h. Delete the dedicated inspection VPC (tagged goat-scenario=tls-fragmentation).
    # Its subnets, route tables, security groups, IGW, and NAT were removed above by the
    # tag-based steps; only the VPC shell remains. The shared spoke VPC (goat-demo-vpc) is
    # handled by section 10 and is NOT deleted here.
    Write-Host "  Finding inspection VPC tagged goat-scenario=tls-fragmentation..." -ForegroundColor Yellow
    $inspVpcIds = aws ec2 describe-vpcs `
        --filters "Name=tag:goat-scenario,Values=tls-fragmentation" "Name=tag:Name,Values=goat-demo-tls-inspection-vpc" `
        --query "Vpcs[].VpcId" --output text --region $region 2>$null
    if (-not [string]::IsNullOrEmpty($inspVpcIds) -and $inspVpcIds -ne "None") {
        foreach ($inspVpc in ($inspVpcIds -split '\s+')) {
            if ([string]::IsNullOrEmpty($inspVpc)) { continue }
            $totalFound++

            # Detach + delete any IGWs still attached to this VPC
            $vpcIgws = aws ec2 describe-internet-gateways `
                --filters "Name=attachment.vpc-id,Values=$inspVpc" `
                --query "InternetGateways[].InternetGatewayId" --output text --region $region 2>$null
            if (-not [string]::IsNullOrEmpty($vpcIgws) -and $vpcIgws -ne "None") {
                foreach ($igw in ($vpcIgws -split '\s+')) {
                    if ([string]::IsNullOrEmpty($igw)) { continue }
                    aws ec2 detach-internet-gateway --internet-gateway-id $igw --vpc-id $inspVpc --region $region 2>$null
                    aws ec2 delete-internet-gateway --internet-gateway-id $igw --region $region 2>$null
                }
            }

            # Delete any leftover subnets in the inspection VPC
            $vpcSubnets = aws ec2 describe-subnets `
                --filters "Name=vpc-id,Values=$inspVpc" `
                --query "Subnets[].SubnetId" --output text --region $region 2>$null
            if (-not [string]::IsNullOrEmpty($vpcSubnets) -and $vpcSubnets -ne "None") {
                foreach ($sn in ($vpcSubnets -split '\s+')) {
                    if (-not [string]::IsNullOrEmpty($sn)) { aws ec2 delete-subnet --subnet-id $sn --region $region 2>$null }
                }
            }

            # Delete non-main route tables in the inspection VPC
            $vpcRts = aws ec2 describe-route-tables `
                --filters "Name=vpc-id,Values=$inspVpc" `
                --query "RouteTables[?length(Associations[?Main]) == ``0``].RouteTableId" --output text --region $region 2>$null
            if (-not [string]::IsNullOrEmpty($vpcRts) -and $vpcRts -ne "None") {
                foreach ($rt in ($vpcRts -split '\s+')) {
                    if (-not [string]::IsNullOrEmpty($rt)) { aws ec2 delete-route-table --route-table-id $rt --region $region 2>$null }
                }
            }

            Write-Host "  Deleting inspection VPC: $inspVpc..." -ForegroundColor Yellow
            try {
                $result = aws ec2 delete-vpc --vpc-id $inspVpc --region $region 2>&1
                if ($LASTEXITCODE -ne 0) { throw $result }
                Write-Host "  Deleted inspection VPC: $inspVpc" -ForegroundColor Green
                $countTlsFrag++
            } catch {
                if ("$_" -match "not found|InvalidVpcID") {
                    Write-Host "  Inspection VPC already deleted, skipping" -ForegroundColor Gray
                } else {
                    Write-Host "  WARNING: Failed to delete inspection VPC $inspVpc`: $_" -ForegroundColor Red
                    Write-Host "  (TGW attachment or firewall endpoint may still be detaching - re-run cleanup)" -ForegroundColor Gray
                    $warnings += "Inspection VPC delete failed: $inspVpc"
                    $hasErrors = $true
                }
            }
        }
    } else {
        Write-Host "  No inspection VPC found" -ForegroundColor Gray
    }

} else {
    Write-Host "  No TLS Fragmentation Scenario resources found" -ForegroundColor Gray
}

Write-Host ""

# ---------------------------------------------------------------------------
# 12. Summary
# ---------------------------------------------------------------------------

# Count remaining resources per scenario
$countScenarioA = aws resourcegroupstaggingapi get-resources `
    --tag-filters Key=goat-scenario,Values=a `
    --query "length(ResourceTagMappingList)" --output text --region $region 2>$null
if ([string]::IsNullOrEmpty($countScenarioA)) { $countScenarioA = "0" }
$countScenarioB = aws resourcegroupstaggingapi get-resources `
    --tag-filters Key=goat-scenario,Values=b `
    --query "length(ResourceTagMappingList)" --output text --region $region 2>$null
if ([string]::IsNullOrEmpty($countScenarioB)) { $countScenarioB = "0" }

if ($totalFound -eq 0) {
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  No Demo Resources Found" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  No resources tagged with goat-demo=true were found in $region." -ForegroundColor Gray
    Write-Host "  No goat-scenario=a resources found." -ForegroundColor Gray
    Write-Host "  No goat-scenario=b resources found." -ForegroundColor Gray
    Write-Host "  No goat-scenario=tls-fragmentation resources found." -ForegroundColor Gray
    Write-Host "  Nothing to clean up." -ForegroundColor Gray
    Write-Host ""
    if ($hasErrors) { exit 1 }
    exit 0
}

Write-Host "========================================" -ForegroundColor Green
Write-Host "  G.O.A.T. Demo Cleanup Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Region:              $region" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Resources removed per scenario:" -ForegroundColor Cyan
Write-Host "    goat-scenario=a:                 removed from account (found $countScenarioA remaining)" -ForegroundColor Cyan
Write-Host "    goat-scenario=b:                 removed from account (found $countScenarioB remaining)" -ForegroundColor Cyan
Write-Host "    goat-scenario=tls-fragmentation: $countTlsFrag removed" -ForegroundColor Cyan
Write-Host ""

if ($terminatedEc2.Count -gt 0) {
    Write-Host "  Terminated EC2:      $($terminatedEc2 -join ', ')" -ForegroundColor Cyan
}
if ($deletedRds.Count -gt 0) {
    Write-Host "  Deleted RDS:         $($deletedRds -join ', ')" -ForegroundColor Cyan
}
if ($deletedDbSubnetGroups.Count -gt 0) {
    Write-Host "  Deleted DB SubGrp:   $($deletedDbSubnetGroups -join ', ')" -ForegroundColor Cyan
}
if ($deletedEbs.Count -gt 0) {
    Write-Host "  Deleted EBS:         $($deletedEbs -join ', ')" -ForegroundColor Cyan
}
if ($releasedEip.Count -gt 0) {
    Write-Host "  Released EIP:        $($releasedEip -join ', ')" -ForegroundColor Cyan
}
if ($deletedDdb.Count -gt 0) {
    Write-Host "  Deleted DynamoDB:    $($deletedDdb -join ', ')" -ForegroundColor Cyan
}
if ($deletedSubnets.Count -gt 0) {
    Write-Host "  Deleted Subnets:     $($deletedSubnets -join ', ')" -ForegroundColor Cyan
}
if ($deletedVpcs.Count -gt 0) {
    Write-Host "  Deleted VPCs:        $($deletedVpcs -join ', ')" -ForegroundColor Cyan
}

if ($warnings.Count -gt 0) {
    Write-Host ""
    Write-Host "  Warnings:" -ForegroundColor Yellow
    foreach ($w in $warnings) {
        Write-Host "    - $w" -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "  Some resources may still be terminating. Re-run this script" -ForegroundColor Yellow
    Write-Host "  in a few minutes to clean up remaining dependencies." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  All demo resources have been removed." -ForegroundColor Green
Write-Host "  (Support cases are already resolved and cannot be deleted via API)" -ForegroundColor Gray
Write-Host ""

if ($hasErrors) { exit 1 }
