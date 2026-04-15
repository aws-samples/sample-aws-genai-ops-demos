# =============================================================================
# DevOps Agent EKS Demo - Complete Cleanup Script (PowerShell)
# Deletes all deployed infrastructure with zero leftover resources or costs
# =============================================================================
param()

$ErrorActionPreference = "Stop"

$PROJECT_NAME = if ($env:PROJECT_NAME) { $env:PROJECT_NAME } else { "devops-agent-eks" }
$ENVIRONMENT = if ($env:ENVIRONMENT) { $env:ENVIRONMENT } else { "dev" }

# Region detection (same priority as deploy scripts and shared prerequisites)
$REGION = if ($env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION }
          elseif ($env:AWS_REGION) { $env:AWS_REGION }
          else { $null }
if (-not $REGION) {
    $REGION = aws configure get region 2>$null
}
if (-not $REGION) {
    Write-Host "ERROR: No AWS region configured." -ForegroundColor Red
    Write-Host "  Set AWS_DEFAULT_REGION, AWS_REGION, or run: aws configure set region <region>"
    exit 1
}
$env:AWS_REGION = $REGION

$ACCOUNT_ID = aws sts get-caller-identity --query Account --output text

Write-Host "============================================"
Write-Host "DevOps Agent EKS Demo - Cleanup"
Write-Host "============================================"
Write-Host "Project:     $PROJECT_NAME"
Write-Host "Environment: $ENVIRONMENT"
Write-Host "Account:     $ACCOUNT_ID"
Write-Host "Region:      $REGION"
Write-Host "============================================"
Write-Host ""
$confirm = Read-Host "This will DELETE all resources. Continue? (y/N)"
if ($confirm -ne "y" -and $confirm -ne "Y") {
    Write-Host "Aborted."
    exit 0
}

Write-Host ""
Write-Host "[1/14] Installing CDK dependencies..." -ForegroundColor Cyan
if (Test-Path "cdk") {
    Write-Host "  Running npm install in cdk/..."
    Push-Location cdk
    npm install --silent
    Pop-Location
    Write-Host "  ✓ CDK dependencies installed" -ForegroundColor Green
} else {
    Write-Host "  - cdk/ directory not found, skipping" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[2/14] Cleaning up CodeBuild source bundles..." -ForegroundColor Cyan
$CFN_BUCKET = "${PROJECT_NAME}-cfn-templates-${ACCOUNT_ID}"
$cfnBucketExists = aws s3api head-bucket --bucket $CFN_BUCKET 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  Removing codebuild-sources/ from $CFN_BUCKET..."
    aws s3 rm "s3://$CFN_BUCKET/codebuild-sources/" --recursive 2>$null
    Write-Host "  ✓ CodeBuild source bundles removed" -ForegroundColor Green
} else {
    Write-Host "  - Bucket $CFN_BUCKET not found, skipping" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[3/14] Emptying and deleting S3 buckets..." -ForegroundColor Cyan
$BUCKETS = @(
    "${PROJECT_NAME}-${ENVIRONMENT}-merchant-portal-${ACCOUNT_ID}",
    "${PROJECT_NAME}-cfn-templates-${ACCOUNT_ID}"
)

foreach ($bucket in $BUCKETS) {
    $bucketExists = aws s3api head-bucket --bucket $bucket 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Removing bucket policy for $bucket..."
        aws s3api delete-bucket-policy --bucket $bucket 2>$null
        Write-Host "  Removing all objects from $bucket..."
        aws s3 rm "s3://$bucket" --recursive 2>$null

        # Delete all object versions and delete markers (for versioned buckets)
        Write-Host "  Removing object versions from $bucket..."
        $versions = aws s3api list-object-versions --bucket $bucket --query "Versions[].{Key:Key,VersionId:VersionId}" --output json --no-cli-pager 2>$null | ConvertFrom-Json
        if ($versions -and $versions.Count -gt 0) {
            # s3api delete-objects accepts max 1000 at a time
            for ($i = 0; $i -lt $versions.Count; $i += 1000) {
                $batch = $versions[$i..[Math]::Min($i + 999, $versions.Count - 1)]
                $deletePayload = @{ Objects = $batch; Quiet = $true } | ConvertTo-Json -Compress -Depth 5
                $deletePayload | Out-File -FilePath "$env:TEMP\s3-delete-batch.json" -Encoding UTF8
                aws s3api delete-objects --bucket $bucket --delete "file://$env:TEMP\s3-delete-batch.json" --no-cli-pager 2>$null | Out-Null
            }
        }

        $deleteMarkers = aws s3api list-object-versions --bucket $bucket --query "DeleteMarkers[].{Key:Key,VersionId:VersionId}" --output json --no-cli-pager 2>$null | ConvertFrom-Json
        if ($deleteMarkers -and $deleteMarkers.Count -gt 0) {
            for ($i = 0; $i -lt $deleteMarkers.Count; $i += 1000) {
                $batch = $deleteMarkers[$i..[Math]::Min($i + 999, $deleteMarkers.Count - 1)]
                $deletePayload = @{ Objects = $batch; Quiet = $true } | ConvertTo-Json -Compress -Depth 5
                $deletePayload | Out-File -FilePath "$env:TEMP\s3-delete-batch.json" -Encoding UTF8
                aws s3api delete-objects --bucket $bucket --delete "file://$env:TEMP\s3-delete-batch.json" --no-cli-pager 2>$null | Out-Null
            }
        }

        Write-Host "  Deleting bucket $bucket..."
        aws s3 rb "s3://$bucket" --force 2>$null
        Write-Host "  ✓ $bucket deleted" -ForegroundColor Green
    } else {
        Write-Host "  - $bucket not found, skipping" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "[4/14] Deleting ECR images..." -ForegroundColor Cyan
$REPOS = @(
    "${PROJECT_NAME}/merchant-gateway",
    "${PROJECT_NAME}/payment-processor",
    "${PROJECT_NAME}/webhook-service",
    "${PROJECT_NAME}-${ENVIRONMENT}/merchant-gateway",
    "${PROJECT_NAME}-${ENVIRONMENT}/payment-processor",
    "${PROJECT_NAME}-${ENVIRONMENT}/webhook-service"
)

foreach ($repo in $REPOS) {
    $repoExists = aws ecr describe-repositories --repository-names $repo 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Deleting repository $repo (force, including all images)..."
        aws ecr delete-repository --repository-name $repo --force 2>$null
        Write-Host "  ✓ $repo deleted" -ForegroundColor Green
    } else {
        Write-Host "  - $repo not found, skipping" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "[5/14] Disabling RDS deletion protection..." -ForegroundColor Cyan
$DB_INSTANCE = "${PROJECT_NAME}-${ENVIRONMENT}-postgres"
$dbExists = aws rds describe-db-instances --db-instance-identifier $DB_INSTANCE 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  Disabling deletion protection on $DB_INSTANCE..."
    aws rds modify-db-instance `
        --db-instance-identifier $DB_INSTANCE `
        --no-deletion-protection `
        --apply-immediately 2>$null
    Write-Host "  ✓ Deletion protection disabled" -ForegroundColor Green
} else {
    Write-Host "  - RDS instance not found, skipping" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[6/14] Cleaning up Kubernetes resources before stack deletion..." -ForegroundColor Cyan
$EKS_CLUSTER = "${PROJECT_NAME}-${ENVIRONMENT}-cluster"
$clusterExists = aws eks describe-cluster --name $EKS_CLUSTER 2>$null
if ($LASTEXITCODE -eq 0) {
    # Delete Fluent Bit and K8s resources while cluster is still running
    # (CloudFormation doesn't manage these — they were applied via kubectl)
    Write-Host "  Deleting Fluent Bit resources..."
    kubectl delete -f k8s/base/fluent-bit/ --ignore-not-found 2>$null
    Write-Host "  Deleting payment-demo namespace resources..."
    kubectl delete namespace payment-demo --ignore-not-found 2>$null
    Write-Host "  ✓ Kubernetes resources cleaned up" -ForegroundColor Green
    # NOTE: Do NOT delete the EKS cluster or nodegroups here.
    # CloudFormation will handle that in step 9 when we delete the Compute stack.
    # Deleting EKS resources directly causes ghost state in CloudFormation.
} else {
    Write-Host "  - EKS cluster not found, skipping" -ForegroundColor Yellow
}

# Clean up IAM instance profiles for EKS node role
$EKS_NODE_ROLE = "${PROJECT_NAME}-${ENVIRONMENT}-eks-node-role"
$roleExists = aws iam get-role --role-name $EKS_NODE_ROLE 2>$null
if ($LASTEXITCODE -eq 0) {
    $PROFILES = aws iam list-instance-profiles-for-role --role-name $EKS_NODE_ROLE --query "InstanceProfiles[*].InstanceProfileName" --output text 2>$null
    if ($PROFILES) {
        foreach ($profile in $PROFILES.Split("`t", [System.StringSplitOptions]::RemoveEmptyEntries)) {
            Write-Host "  Removing role from instance profile $profile..."
            aws iam remove-role-from-instance-profile --instance-profile-name $profile --role-name $EKS_NODE_ROLE 2>$null
        }
    }
}

Write-Host ""
Write-Host "[7/14] Cleaning up orphaned VPC endpoints..." -ForegroundColor Cyan
$VPC_ID = aws ec2 describe-vpcs --filters "Name=tag:Project,Values=${PROJECT_NAME}" --query "Vpcs[0].VpcId" --output text 2>$null
# Fallback: query the NetworkStack CloudFormation output if tag lookup fails
if (-not $VPC_ID -or $VPC_ID -eq "None") {
    $VPC_ID = aws cloudformation describe-stacks `
        --stack-name "DevOpsAgentEksNetwork-${REGION}" `
        --query "Stacks[0].Outputs[?OutputKey=='VpcId'].OutputValue" `
        --output text 2>$null
}
if ($VPC_ID -and $VPC_ID -ne "None") {
    # Delete ALL VPC endpoints to prevent subnet/VPC deletion failures
    $VPCE_IDS = aws ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=${VPC_ID}" --query "VpcEndpoints[*].VpcEndpointId" --output text 2>$null
    if ($VPCE_IDS -and $VPCE_IDS.Trim()) {
        Write-Host "  Deleting VPC endpoints: $VPCE_IDS..."
        aws ec2 delete-vpc-endpoints --vpc-endpoint-ids $VPCE_IDS.Split("`t") 2>$null
        # Wait for VPC endpoint ENIs to fully detach and release
        Write-Host "  Waiting for VPC endpoint deletion to complete..."
        for ($i = 1; $i -le 12; $i++) {
            $REMAINING = aws ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=${VPC_ID}" "Name=vpc-endpoint-state,Values=deleting,available" --query "VpcEndpoints[*].VpcEndpointId" --output text 2>$null
            if (-not $REMAINING -or -not $REMAINING.Trim()) {
                Write-Host "  ✓ All VPC endpoints deleted" -ForegroundColor Green
                break
            }
            Write-Host "  Still deleting ($i/12)... waiting 10s"
            Start-Sleep -Seconds 10
        }
    } else {
        Write-Host "  - No VPC endpoints found" -ForegroundColor Yellow
    }

    # Clean up orphaned ENIs
    Write-Host "  Checking for orphaned ENIs..."
    $ORPHAN_ENIS = aws ec2 describe-network-interfaces --filters "Name=vpc-id,Values=${VPC_ID}" "Name=status,Values=available" --query "NetworkInterfaces[*].NetworkInterfaceId" --output text 2>$null
    if ($ORPHAN_ENIS -and $ORPHAN_ENIS.Trim()) {
        foreach ($eni in $ORPHAN_ENIS.Split("`t", [System.StringSplitOptions]::RemoveEmptyEntries)) {
            Write-Host "  Deleting orphaned ENI $eni..."
            aws ec2 delete-network-interface --network-interface-id $eni 2>$null
        }
        Write-Host "  ✓ Orphaned ENIs cleaned up" -ForegroundColor Green
    } else {
        Write-Host "  - No orphaned ENIs found" -ForegroundColor Yellow
    }
} else {
    Write-Host "  - VPC not found, skipping" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[8/14] Cleaning up orphaned load balancers, target groups, and security groups..." -ForegroundColor Cyan
if ($VPC_ID -and $VPC_ID -ne "None") {
    # Delete load balancers in the VPC
    $LB_ARNS = aws elbv2 describe-load-balancers --query "LoadBalancers[?VpcId=='${VPC_ID}'].LoadBalancerArn" --output text 2>$null
    if ($LB_ARNS -and $LB_ARNS.Trim()) {
        foreach ($arn in $LB_ARNS.Split("`t", [System.StringSplitOptions]::RemoveEmptyEntries)) {
            Write-Host "  Deleting load balancer..."
            aws elbv2 delete-load-balancer --load-balancer-arn $arn 2>$null
        }
        Write-Host "  Waiting 60s for load balancer ENIs to release..."
        Start-Sleep -Seconds 60
    }

    # Delete orphaned target groups
    $TG_ARNS = aws elbv2 describe-target-groups --query "TargetGroups[?VpcId=='${VPC_ID}'].TargetGroupArn" --output text 2>$null
    if ($TG_ARNS -and $TG_ARNS.Trim()) {
        foreach ($arn in $TG_ARNS.Split("`t", [System.StringSplitOptions]::RemoveEmptyEntries)) {
            Write-Host "  Deleting orphaned target group..."
            aws elbv2 delete-target-group --target-group-arn $arn 2>$null
        }
    }

    # Delete orphaned security groups (non-default)
    $SG_IDS = aws ec2 describe-security-groups --filters "Name=vpc-id,Values=${VPC_ID}" --query "SecurityGroups[?GroupName!='default'].GroupId" --output text 2>$null
    if ($SG_IDS -and $SG_IDS.Trim()) {
        foreach ($sg in $SG_IDS.Split("`t", [System.StringSplitOptions]::RemoveEmptyEntries)) {
            Write-Host "  Deleting orphaned security group $sg..."
            aws ec2 delete-security-group --group-id $sg 2>$null
        }
    }
} else {
    Write-Host "  - VPC not found, skipping" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[9/14] Destroying CloudFormation stacks (reverse dependency order)..." -ForegroundColor Cyan
# CDK's --all flag cannot delete the conditional DevOpsAgent stack (it is not
# in the synth output when the webhook URL context is absent).  We therefore
# delete stacks directly via CloudFormation in explicit reverse-dependency
# order so cross-stack exports are removed before the exporting stack is
# deleted.
#
# Dependency graph (from app.ts):
#   FrontendStack imports FailureSimulatorApi.apiId -> delete Frontend FIRST
#   DevOpsAgent -> Compute (clusterName), Monitoring (criticalAlarmsTopicArn)
#   FailureSimulatorApi -> Compute (clusterName), Network (vpc, subnets, SG)
#   Database    -> Network (vpc, subnets, SG)
#   Compute     -> Network (subnets, SG)
#   Network     -> base (deleted last)

$STACK_DELETE_ORDER = @(
    "DevOpsAgentEksDevOpsAgent-${REGION}",
    "DevOpsAgentEksFrontend-${REGION}",
    "DevOpsAgentEksFailureSimulatorApi-${REGION}",
    "DevOpsAgentEksMonitoring-${REGION}",
    "DevOpsAgentEksPipeline-${REGION}",
    "DevOpsAgentEksAuth-${REGION}",
    "DevOpsAgentEksDatabase-${REGION}",
    "DevOpsAgentEksCompute-${REGION}",
    "DevOpsAgentEksNetwork-${REGION}"
)

foreach ($stack in $STACK_DELETE_ORDER) {
    $stackStatus = aws cloudformation describe-stacks --stack-name $stack --query "Stacks[0].StackStatus" --output text 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $stackStatus -or $stackStatus -eq "DELETE_COMPLETE") {
        Write-Host "  - $stack not found, skipping" -ForegroundColor Yellow
        continue
    }
    Write-Host "  Deleting $stack ($stackStatus)..."
    aws cloudformation delete-stack --stack-name $stack 2>$null
    aws cloudformation wait stack-delete-complete --stack-name $stack 2>$null
    # Verify deletion succeeded
    $finalStatus = aws cloudformation describe-stacks --stack-name $stack --query "Stacks[0].StackStatus" --output text 2>$null
    if ($LASTEXITCODE -eq 0 -and $finalStatus -eq "DELETE_FAILED") {
        Write-Host "  ⚠ $stack stuck in DELETE_FAILED — will retry in step 11" -ForegroundColor Yellow
    } else {
        Write-Host "  ✓ $stack deleted" -ForegroundColor Green
    }
}

# Fallback: try deleting legacy CloudFormation root stack if it exists
$rootStackExists = aws cloudformation describe-stacks --stack-name $PROJECT_NAME 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  Deleting legacy root stack $PROJECT_NAME..."
    aws cloudformation delete-stack --stack-name $PROJECT_NAME
    aws cloudformation wait stack-delete-complete --stack-name $PROJECT_NAME 2>$null
    Write-Host "  ✓ Root stack deleted" -ForegroundColor Green
}

Write-Host ""
Write-Host "[10/14] Cleaning up remaining S3 buckets (if any survived stack deletion)..." -ForegroundColor Cyan
foreach ($bucket in $BUCKETS) {
    $bucketStillExists = aws s3api head-bucket --bucket $bucket 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Force-deleting bucket $bucket..."
        aws s3 rm "s3://$bucket" --recursive 2>$null
        aws s3 rb "s3://$bucket" --force 2>$null
        Write-Host "  ✓ $bucket deleted" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "[11/14] Cleaning up any orphaned stacks in DELETE_FAILED state..." -ForegroundColor Cyan
$allFailedStacks = @()
# Check for both legacy CloudFormation and CDK stack name patterns
foreach ($pattern in @($PROJECT_NAME, "DevOpsAgentEks")) {
    $found = aws cloudformation list-stacks --stack-status-filter DELETE_FAILED `
        --query "StackSummaries[?contains(StackName,'${pattern}')].StackName" --output text 2>$null
    if ($found -and $found.Trim()) {
        $allFailedStacks += $found.Split("`t", [System.StringSplitOptions]::RemoveEmptyEntries)
    }
}
$allFailedStacks = $allFailedStacks | Sort-Object -Unique
if ($allFailedStacks.Count -gt 0) {
    foreach ($stack in $allFailedStacks) {
        Write-Host "  Retrying deletion of $stack..."
        aws cloudformation delete-stack --stack-name $stack 2>$null
        aws cloudformation wait stack-delete-complete --stack-name $stack 2>$null
        # If still stuck, identify failed resources and retain them to force-delete the stack
        $retryStatus = aws cloudformation describe-stacks --stack-name $stack --query "Stacks[0].StackStatus" --output text 2>$null
        if ($LASTEXITCODE -eq 0 -and $retryStatus -eq "DELETE_FAILED") {
            Write-Host "  ⚠ $stack still in DELETE_FAILED — retaining failed resources..." -ForegroundColor Yellow
            $failedResources = aws cloudformation describe-stack-events --stack-name $stack `
                --query "StackEvents[?ResourceStatus=='DELETE_FAILED'].LogicalResourceId" --output text 2>$null
            if ($failedResources -and $failedResources.Trim()) {
                $resourceList = ($failedResources.Split("`t", [System.StringSplitOptions]::RemoveEmptyEntries) | Sort-Object -Unique)
                aws cloudformation delete-stack --stack-name $stack --retain-resources $resourceList 2>$null
                aws cloudformation wait stack-delete-complete --stack-name $stack 2>$null
            }
        }
        Write-Host "  ✓ $stack deleted" -ForegroundColor Green
    }
} else {
    Write-Host "  - No orphaned stacks found" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[12/14] Deleting Secrets Manager secret..." -ForegroundColor Cyan
$SECRET_NAME = "${PROJECT_NAME}-${ENVIRONMENT}-rds-credentials"
$secretExists = aws secretsmanager describe-secret --secret-id $SECRET_NAME 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  Deleting $SECRET_NAME (force, no recovery window)..."
    aws secretsmanager delete-secret --secret-id $SECRET_NAME --force-delete-without-recovery 2>$null
    Write-Host "  ✓ Secret deleted" -ForegroundColor Green
} else {
    Write-Host "  - Secret not found, skipping" -ForegroundColor Yellow
}
# Also clean up DevOps Agent webhook secret if it survived stack deletion
$WEBHOOK_SECRET_NAME = "${PROJECT_NAME}-${ENVIRONMENT}/devops-agent-webhook-secret"
$webhookSecretExists = aws secretsmanager describe-secret --secret-id $WEBHOOK_SECRET_NAME 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  Deleting $WEBHOOK_SECRET_NAME (force, no recovery window)..."
    aws secretsmanager delete-secret --secret-id $WEBHOOK_SECRET_NAME --force-delete-without-recovery 2>$null
    Write-Host "  ✓ Webhook secret deleted" -ForegroundColor Green
}

Write-Host ""
Write-Host "[13/14] Cleaning up CloudWatch log groups and kubeconfig..." -ForegroundColor Cyan
# Delete EKS, Lambda, and CodeBuild log groups created outside CloudFormation
$LOG_GROUPS = aws logs describe-log-groups `
    --log-group-name-prefix "/aws/eks/${PROJECT_NAME}" `
    --query "logGroups[*].logGroupName" --output text 2>$null
$LAMBDA_LOG_GROUPS = aws logs describe-log-groups `
    --log-group-name-prefix "/aws/lambda/${PROJECT_NAME}" `
    --query "logGroups[*].logGroupName" --output text 2>$null
$CODEBUILD_LOG_GROUPS = aws logs describe-log-groups `
    --log-group-name-prefix "/aws/codebuild/${PROJECT_NAME}-" `
    --query "logGroups[*].logGroupName" --output text 2>$null
$COGNITO_LOG_GROUPS = aws logs describe-log-groups `
    --log-group-name-prefix "/aws/cognito/${PROJECT_NAME}-" `
    --query "logGroups[*].logGroupName" --output text 2>$null

$allLogGroups = @()
if ($LOG_GROUPS -and $LOG_GROUPS.Trim()) { $allLogGroups += $LOG_GROUPS.Split("`t", [System.StringSplitOptions]::RemoveEmptyEntries) }
if ($LAMBDA_LOG_GROUPS -and $LAMBDA_LOG_GROUPS.Trim()) { $allLogGroups += $LAMBDA_LOG_GROUPS.Split("`t", [System.StringSplitOptions]::RemoveEmptyEntries) }
if ($CODEBUILD_LOG_GROUPS -and $CODEBUILD_LOG_GROUPS.Trim()) { $allLogGroups += $CODEBUILD_LOG_GROUPS.Split("`t", [System.StringSplitOptions]::RemoveEmptyEntries) }
if ($COGNITO_LOG_GROUPS -and $COGNITO_LOG_GROUPS.Trim()) { $allLogGroups += $COGNITO_LOG_GROUPS.Split("`t", [System.StringSplitOptions]::RemoveEmptyEntries) }

if ($allLogGroups.Count -gt 0) {
    foreach ($lg in $allLogGroups) {
        Write-Host "  Deleting log group $lg..."
        aws logs delete-log-group --log-group-name $lg 2>$null
    }
    Write-Host "  ✓ Log groups deleted" -ForegroundColor Green
} else {
    Write-Host "  - No orphaned log groups found" -ForegroundColor Yellow
}

# Remove kubeconfig context for the deleted cluster
$EKS_CONTEXT = "arn:aws:eks:${REGION}:${ACCOUNT_ID}:cluster/${PROJECT_NAME}-${ENVIRONMENT}-cluster"
$contextExists = kubectl config get-contexts $EKS_CONTEXT 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  Removing kubeconfig context..."
    kubectl config delete-context $EKS_CONTEXT 2>$null
    kubectl config delete-cluster $EKS_CONTEXT 2>$null
    Write-Host "  ✓ kubeconfig cleaned" -ForegroundColor Green
} else {
    Write-Host "  - kubeconfig context not found, skipping" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[14/14] Cleaning up DevOps Agent resources..." -ForegroundColor Cyan
$DEVOPS_AGENT_REGION = if ($env:DEVOPS_AGENT_REGION) { $env:DEVOPS_AGENT_REGION } else { "us-east-1" }

# Delete only the Agent Space matching our project name (not all spaces in the account)
$AGENT_SPACE_ID = aws devops-agent list-agent-spaces `
    --region $DEVOPS_AGENT_REGION `
    --query "agentSpaces[?name=='${PROJECT_NAME}'].agentSpaceId | [0]" `
    --output text --no-cli-pager 2>$null

if ($AGENT_SPACE_ID -and $AGENT_SPACE_ID.Trim() -and $AGENT_SPACE_ID -ne "None") {
    Write-Host "  Deleting Agent Space $AGENT_SPACE_ID ($PROJECT_NAME)..."
    # Remove associations first
    $ASSOC_IDS = aws devops-agent list-associations `
        --agent-space-id $AGENT_SPACE_ID `
        --region $DEVOPS_AGENT_REGION `
        --query "associations[*].associationId" `
        --output text --no-cli-pager 2>$null
    if ($ASSOC_IDS -and $ASSOC_IDS.Trim() -and $ASSOC_IDS -ne "None") {
        foreach ($assocId in $ASSOC_IDS.Split("`t", [System.StringSplitOptions]::RemoveEmptyEntries)) {
            Write-Host "    Removing association $assocId..."
            aws devops-agent disassociate-service `
                --agent-space-id $AGENT_SPACE_ID `
                --association-id $assocId `
                --region $DEVOPS_AGENT_REGION `
                --no-cli-pager 2>$null
        }
    }
    aws devops-agent delete-agent-space `
        --agent-space-id $AGENT_SPACE_ID `
        --region $DEVOPS_AGENT_REGION `
        --no-cli-pager 2>$null
    Write-Host "  ✓ Agent Space $AGENT_SPACE_ID deleted" -ForegroundColor Green
} else {
    Write-Host "  - No Agent Space named '$PROJECT_NAME' found" -ForegroundColor Yellow
}

# Delete DevOps Agent IAM roles (both old script-created and CDK-managed names)
foreach ($roleName in @(
    "${PROJECT_NAME}-AgentSpaceRole",
    "${PROJECT_NAME}-OperatorRole",
    "${PROJECT_NAME}-${ENVIRONMENT}-AgentSpaceRole",
    "${PROJECT_NAME}-${ENVIRONMENT}-OperatorRole"
)) {
    $roleCheck = aws iam get-role --role-name $roleName 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Deleting IAM role $roleName..."
        # Detach managed policies
        $policies = aws iam list-attached-role-policies --role-name $roleName --query "AttachedPolicies[*].PolicyArn" --output text 2>$null
        if ($policies -and $policies.Trim() -and $policies -ne "None") {
            foreach ($policyArn in $policies.Split("`t", [System.StringSplitOptions]::RemoveEmptyEntries)) {
                aws iam detach-role-policy --role-name $roleName --policy-arn $policyArn 2>$null
            }
        }
        # Delete inline policies
        $inlinePolicies = aws iam list-role-policies --role-name $roleName --query "PolicyNames[*]" --output text 2>$null
        if ($inlinePolicies -and $inlinePolicies.Trim() -and $inlinePolicies -ne "None") {
            foreach ($policyName in $inlinePolicies.Split("`t", [System.StringSplitOptions]::RemoveEmptyEntries)) {
                aws iam delete-role-policy --role-name $roleName --policy-name $policyName 2>$null
            }
        }
        aws iam delete-role --role-name $roleName 2>$null
        Write-Host "  ✓ $roleName deleted" -ForegroundColor Green
    } else {
        Write-Host "  - $roleName not found, skipping" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "============================================"
Write-Host "Cleanup complete!" -ForegroundColor Green
Write-Host "============================================"
