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

        # Check if instance is already terminated
        try {
            $state = aws ec2 describe-instances `
                --instance-ids $instanceId `
                --query "Reservations[].Instances[].State.Name" --output text --region $region 2>$null
            if ($state -eq "terminated" -or $state -eq "shutting-down") {
                Write-Host "  Instance $instanceId already terminated, skipping" -ForegroundColor Gray
                continue
            }
        } catch { }

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
# 7. Release Elastic IPs
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
        try {
            $result = aws ec2 delete-subnet --subnet-id $subnetId --region $region 2>&1
            if ($LASTEXITCODE -ne 0) { throw $result }
            Write-Host "  Deleted: $subnetId" -ForegroundColor Green
            $deletedSubnets += $subnetId
        } catch {
            if ("$_" -match "not found") {
                Write-Host "  Subnet $subnetId already deleted, skipping" -ForegroundColor Gray
            } elseif ("$_" -match "DependencyViolation") {
                Write-Host "  Subnet $subnetId has dependencies (resources still terminating). Re-run cleanup later." -ForegroundColor Yellow
                $warnings += "Subnet dependency: $subnetId - re-run cleanup later"
            } else {
                Write-Host "  WARNING: Failed to delete subnet $subnetId`: $_" -ForegroundColor Red
                $warnings += "Subnet delete failed: $subnetId"
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

        Write-Host "  Deleting VPC: $vpcId..." -ForegroundColor Yellow
        try {
            $result = aws ec2 delete-vpc --vpc-id $vpcId --region $region 2>&1
            if ($LASTEXITCODE -ne 0) { throw $result }
            Write-Host "  Deleted: $vpcId" -ForegroundColor Green
            $deletedVpcs += $vpcId
        } catch {
            if ("$_" -match "not found") {
                Write-Host "  VPC $vpcId already deleted, skipping" -ForegroundColor Gray
            } elseif ("$_" -match "DependencyViolation") {
                Write-Host "  VPC $vpcId has dependencies (subnets/instances still terminating). Re-run cleanup later." -ForegroundColor Yellow
                $warnings += "VPC dependency: $vpcId - re-run cleanup later"
            } else {
                Write-Host "  WARNING: Failed to delete VPC $vpcId`: $_" -ForegroundColor Red
                $warnings += "VPC delete failed: $vpcId"
            }
        }
    }
} else {
    Write-Host "  No VPCs found" -ForegroundColor Gray
}

Write-Host ""

# ---------------------------------------------------------------------------
# 11. Summary
# ---------------------------------------------------------------------------
if ($totalFound -eq 0) {
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  No Demo Resources Found" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  No resources tagged with goat-demo=true were found in $region." -ForegroundColor Gray
    Write-Host "  Nothing to clean up." -ForegroundColor Gray
    Write-Host ""
    exit 0
}

Write-Host "========================================" -ForegroundColor Green
Write-Host "  G.O.A.T. Demo Cleanup Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Region:              $region" -ForegroundColor Cyan

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
