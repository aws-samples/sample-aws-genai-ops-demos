#!/bin/bash
# =============================================================================
# DevOps Agent EKS Demo - Complete Cleanup Script
# Deletes all deployed infrastructure with zero leftover resources or costs
# =============================================================================
set -uo pipefail

PROJECT_NAME="${PROJECT_NAME:-devops-agent-eks}"
ENVIRONMENT="${ENVIRONMENT:-dev}"

# Region detection (same priority as deploy scripts and shared prerequisites)
REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-}}"
if [ -z "$REGION" ]; then
    REGION=$(aws configure get region 2>/dev/null || echo "")
fi
if [ -z "$REGION" ]; then
    echo "ERROR: No AWS region configured."
    echo "  Set AWS_DEFAULT_REGION, AWS_REGION, or run: aws configure set region <region>"
    exit 1
fi
export AWS_REGION="$REGION"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "============================================"
echo "DevOps Agent EKS Demo - Cleanup"
echo "============================================"
echo "Project:     $PROJECT_NAME"
echo "Environment: $ENVIRONMENT"
echo "Account:     $ACCOUNT_ID"
echo "Region:      $REGION"
echo "============================================"
echo ""
read -p "This will DELETE all resources. Continue? (y/N): " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
  echo "Aborted."
  exit 0
fi

echo ""
echo "[1/15] Installing CDK dependencies..."
if [ -d "cdk" ]; then
  echo "  Running npm install in cdk/..."
  cd cdk && npm install --silent && cd ..
  echo "  ✓ CDK dependencies installed"
else
  echo "  - cdk/ directory not found, skipping"
fi

echo ""
echo "[2/15] Cleaning up CodeBuild source bundles..."
CFN_BUCKET="${PROJECT_NAME}-cfn-templates-${ACCOUNT_ID}"
if aws s3api head-bucket --bucket "$CFN_BUCKET" 2>/dev/null; then
  echo "  Removing codebuild-sources/ from $CFN_BUCKET..."
  aws s3 rm "s3://$CFN_BUCKET/codebuild-sources/" --recursive 2>/dev/null || true
  echo "  ✓ CodeBuild source bundles removed"
else
  echo "  - Bucket $CFN_BUCKET not found, skipping"
fi

echo ""
echo "[3/15] Emptying and deleting S3 buckets..."
BUCKETS=(
  "${PROJECT_NAME}-${ENVIRONMENT}-portal-${ACCOUNT_ID}"
  "${PROJECT_NAME}-${ENVIRONMENT}-merchant-portal-${ACCOUNT_ID}"
  "${PROJECT_NAME}-cfn-templates-${ACCOUNT_ID}"
)

for bucket in "${BUCKETS[@]}"; do
  if aws s3api head-bucket --bucket "$bucket" 2>/dev/null; then
    echo "  Removing bucket policy for $bucket..."
    aws s3api delete-bucket-policy --bucket "$bucket" 2>/dev/null || true

    echo "  Removing all objects from $bucket..."
    aws s3 rm "s3://$bucket" --recursive 2>/dev/null || true

    # Delete all object versions and delete markers (for versioned buckets)
    echo "  Removing object versions from $bucket..."
    VERSIONS=$(aws s3api list-object-versions --bucket "$bucket" --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json 2>/dev/null || echo '{"Objects":[]}')
    if [ "$(echo "$VERSIONS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('Objects',None) or []))" 2>/dev/null)" != "0" ]; then
      echo "$VERSIONS" | python3 -c "
import sys, json
data = json.load(sys.stdin)
objects = data.get('Objects') or []
if objects:
    # s3api delete-objects accepts max 1000 at a time
    for i in range(0, len(objects), 1000):
        batch = objects[i:i+1000]
        print(json.dumps({'Objects': batch, 'Quiet': True}))
" 2>/dev/null | while read -r batch; do
        aws s3api delete-objects --bucket "$bucket" --delete "$batch" --no-cli-pager >/dev/null 2>&1 || true
      done
    fi

    DELETE_MARKERS=$(aws s3api list-object-versions --bucket "$bucket" --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' --output json 2>/dev/null || echo '{"Objects":[]}')
    if [ "$(echo "$DELETE_MARKERS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('Objects',None) or []))" 2>/dev/null)" != "0" ]; then
      echo "$DELETE_MARKERS" | python3 -c "
import sys, json
data = json.load(sys.stdin)
objects = data.get('Objects') or []
if objects:
    for i in range(0, len(objects), 1000):
        batch = objects[i:i+1000]
        print(json.dumps({'Objects': batch, 'Quiet': True}))
" 2>/dev/null | while read -r batch; do
        aws s3api delete-objects --bucket "$bucket" --delete "$batch" --no-cli-pager >/dev/null 2>&1 || true
      done
    fi

    echo "  Deleting bucket $bucket..."
    aws s3 rb "s3://$bucket" --force 2>/dev/null || true
    echo "  ✓ $bucket deleted"
  else
    echo "  - $bucket not found, skipping"
  fi
done

# Clean up CloudFront distribution and associated policies
echo "  Cleaning up CloudFront resources..."
CF_DIST_ID=$(aws cloudfront list-distributions \
    --query "DistributionList.Items[?Comment=='Merchant Portal - ${ENVIRONMENT}'].Id | [0]" \
    --output text --no-cli-pager 2>/dev/null || echo "")
if [ -n "$CF_DIST_ID" ] && [ "$CF_DIST_ID" != "None" ]; then
    echo "  Disabling CloudFront distribution $CF_DIST_ID..."
    CF_ETAG=$(aws cloudfront get-distribution-config --id "$CF_DIST_ID" --query ETag --output text --no-cli-pager)
    aws cloudfront get-distribution-config --id "$CF_DIST_ID" --no-cli-pager --output json \
        | jq '.DistributionConfig.Enabled = false | .DistributionConfig' > /tmp/cf-disable.json
    aws cloudfront update-distribution --id "$CF_DIST_ID" --if-match "$CF_ETAG" \
        --distribution-config file:///tmp/cf-disable.json --no-cli-pager >/dev/null 2>&1 || true
    echo "  Waiting for distribution to disable (up to 10 minutes)..."
    aws cloudfront wait distribution-deployed --id "$CF_DIST_ID" 2>/dev/null || true
    CF_ETAG=$(aws cloudfront get-distribution --id "$CF_DIST_ID" --query ETag --output text --no-cli-pager)
    aws cloudfront delete-distribution --id "$CF_DIST_ID" --if-match "$CF_ETAG" --no-cli-pager 2>/dev/null || true
    echo "  ✓ CloudFront distribution deleted"
    rm -f /tmp/cf-disable.json
fi

# Delete orphaned CloudFront policies
for CP_ID in $(aws cloudfront list-cache-policies --type custom \
    --query "CachePolicyList.Items[?CachePolicy.CachePolicyConfig.Name=='${PROJECT_NAME}-${ENVIRONMENT}-cache-policy'].CachePolicy.Id" \
    --output text --no-cli-pager 2>/dev/null || echo ""); do
    [ -z "$CP_ID" ] || [ "$CP_ID" = "None" ] && continue
    CP_ETAG=$(aws cloudfront get-cache-policy --id "$CP_ID" --query ETag --output text --no-cli-pager 2>/dev/null || echo "")
    [ -n "$CP_ETAG" ] && [ "$CP_ETAG" != "None" ] && aws cloudfront delete-cache-policy --id "$CP_ID" --if-match "$CP_ETAG" --no-cli-pager 2>/dev/null || true
done
for RHP_ID in $(aws cloudfront list-response-headers-policies --type custom \
    --query "ResponseHeadersPolicyList.Items[?ResponseHeadersPolicy.ResponseHeadersPolicyConfig.Name=='${PROJECT_NAME}-${ENVIRONMENT}-security-headers'].ResponseHeadersPolicy.Id" \
    --output text --no-cli-pager 2>/dev/null || echo ""); do
    [ -z "$RHP_ID" ] || [ "$RHP_ID" = "None" ] && continue
    RHP_ETAG=$(aws cloudfront get-response-headers-policy --id "$RHP_ID" --query ETag --output text --no-cli-pager 2>/dev/null || echo "")
    [ -n "$RHP_ETAG" ] && [ "$RHP_ETAG" != "None" ] && aws cloudfront delete-response-headers-policy --id "$RHP_ID" --if-match "$RHP_ETAG" --no-cli-pager 2>/dev/null || true
done
for OAC_ID in $(aws cloudfront list-origin-access-controls \
    --query "OriginAccessControlList.Items[?Name=='${PROJECT_NAME}-${ENVIRONMENT}-oac'].Id" \
    --output text --no-cli-pager 2>/dev/null || echo ""); do
    [ -z "$OAC_ID" ] || [ "$OAC_ID" = "None" ] && continue
    OAC_ETAG=$(aws cloudfront get-origin-access-control --id "$OAC_ID" --query ETag --output text --no-cli-pager 2>/dev/null || echo "")
    [ -n "$OAC_ETAG" ] && [ "$OAC_ETAG" != "None" ] && aws cloudfront delete-origin-access-control --id "$OAC_ID" --if-match "$OAC_ETAG" --no-cli-pager 2>/dev/null || true
done
echo "  ✓ CloudFront policies cleaned up"

echo ""
echo "[4/15] Deleting ECR images..."
REPOS=(
  "${PROJECT_NAME}/merchant-gateway"
  "${PROJECT_NAME}/payment-processor"
  "${PROJECT_NAME}/webhook-service"
  "${PROJECT_NAME}/mcp-server"
  "${PROJECT_NAME}-${ENVIRONMENT}/merchant-gateway"
  "${PROJECT_NAME}-${ENVIRONMENT}/payment-processor"
  "${PROJECT_NAME}-${ENVIRONMENT}/webhook-service"
)

for repo in "${REPOS[@]}"; do
  if aws ecr describe-repositories --repository-names "$repo" &>/dev/null; then
    echo "  Deleting repository $repo (force, including all images)..."
    aws ecr delete-repository --repository-name "$repo" --force 2>/dev/null || true
    echo "  ✓ $repo deleted"
  else
    echo "  - $repo not found, skipping"
  fi
done

echo ""
echo "[5/15] Disabling RDS deletion protection..."
DB_INSTANCE="${PROJECT_NAME}-${ENVIRONMENT}-postgres"
if aws rds describe-db-instances --db-instance-identifier "$DB_INSTANCE" &>/dev/null; then
  echo "  Disabling deletion protection on $DB_INSTANCE..."
  aws rds modify-db-instance \
    --db-instance-identifier "$DB_INSTANCE" \
    --no-deletion-protection \
    --apply-immediately \
    --no-cli-pager >/dev/null 2>&1 || true
  echo "  ✓ Deletion protection disabled"
else
  echo "  - RDS instance not found, skipping"
fi

echo ""
echo "[6/15] Cleaning up Kubernetes resources before stack deletion..."
EKS_CLUSTER="${PROJECT_NAME}-${ENVIRONMENT}-cluster"
if aws eks describe-cluster --name "$EKS_CLUSTER" &>/dev/null 2>&1; then
  # Delete Fluent Bit and K8s resources while cluster is still running
  # (CloudFormation doesn't manage these — they were applied via kubectl)
  echo "  Deleting Fluent Bit resources..."
  kubectl delete -f k8s/base/fluent-bit/ --ignore-not-found 2>/dev/null || true
  echo "  Deleting payment-demo namespace resources..."
  kubectl delete namespace payment-demo --ignore-not-found 2>/dev/null || true
  echo "  ✓ Kubernetes resources cleaned up"
  # NOTE: Do NOT delete the EKS cluster or nodegroups here.
  # CloudFormation will handle that in step 9 when we delete the Compute stack.
  # Deleting EKS resources directly causes ghost state in CloudFormation.
else
  echo "  - EKS cluster not found, skipping"
fi

# Clean up IAM instance profiles for EKS node role
EKS_NODE_ROLE="${PROJECT_NAME}-${ENVIRONMENT}-eks-node-role"
if aws iam get-role --role-name "$EKS_NODE_ROLE" &>/dev/null 2>&1; then
  PROFILES=$(aws iam list-instance-profiles-for-role --role-name "$EKS_NODE_ROLE" --query 'InstanceProfiles[*].InstanceProfileName' --output text 2>/dev/null || echo "")
  for profile in $PROFILES; do
    echo "  Removing role from instance profile $profile..."
    aws iam remove-role-from-instance-profile --instance-profile-name "$profile" --role-name "$EKS_NODE_ROLE" 2>/dev/null || true
  done
fi

echo ""
echo "[7/15] Cleaning up orphaned VPC endpoints..."
VPC_ID=$(aws ec2 describe-vpcs --filters "Name=tag:Project,Values=${PROJECT_NAME}" --query 'Vpcs[0].VpcId' --output text 2>/dev/null || echo "None")
# Fallback: query the NetworkStack CloudFormation output if tag lookup fails
if [[ "$VPC_ID" == "None" || -z "$VPC_ID" ]]; then
  VPC_ID=$(aws cloudformation describe-stacks \
    --stack-name "DevOpsAgentEksNetwork-${REGION}" \
    --query "Stacks[0].Outputs[?OutputKey=='VpcId'].OutputValue" \
    --output text 2>/dev/null || echo "None")
fi
if [[ "$VPC_ID" != "None" && "$VPC_ID" != "" ]]; then
  # Delete ALL VPC endpoints (interface + gateway) to prevent subnet/VPC deletion failures
  VPCE_IDS=$(aws ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=${VPC_ID}" --query 'VpcEndpoints[*].VpcEndpointId' --output text 2>/dev/null || echo "")
  if [[ -n "$VPCE_IDS" ]]; then
    echo "  Deleting VPC endpoints: $VPCE_IDS..."
    aws ec2 delete-vpc-endpoints --vpc-endpoint-ids $VPCE_IDS 2>/dev/null || true
    # Wait for VPC endpoint ENIs to fully detach and release
    echo "  Waiting for VPC endpoint deletion to complete..."
    for i in {1..12}; do
      REMAINING=$(aws ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=${VPC_ID}" "Name=vpc-endpoint-state,Values=deleting,available" --query 'VpcEndpoints[*].VpcEndpointId' --output text 2>/dev/null || echo "")
      if [[ -z "$REMAINING" ]]; then
        echo "  ✓ All VPC endpoints deleted"
        break
      fi
      echo "  Still deleting ($i/12)... waiting 10s"
      sleep 10
    done
  else
    echo "  - No VPC endpoints found"
  fi

  # Clean up any orphaned ENIs left behind by VPC endpoints or EKS
  echo "  Checking for orphaned ENIs..."
  ORPHAN_ENIS=$(aws ec2 describe-network-interfaces --filters "Name=vpc-id,Values=${VPC_ID}" "Name=status,Values=available" --query 'NetworkInterfaces[*].NetworkInterfaceId' --output text 2>/dev/null || echo "")
  if [[ -n "$ORPHAN_ENIS" ]]; then
    for eni in $ORPHAN_ENIS; do
      echo "  Deleting orphaned ENI $eni..."
      aws ec2 delete-network-interface --network-interface-id "$eni" 2>/dev/null || true
    done
    echo "  ✓ Orphaned ENIs cleaned up"
  else
    echo "  - No orphaned ENIs found"
  fi
else
  echo "  - VPC not found, skipping"
fi

echo ""
echo "[8/15] Cleaning up orphaned load balancers, target groups, and security groups..."
if [[ "$VPC_ID" != "None" && "$VPC_ID" != "" ]]; then
  # Delete load balancers in the VPC
  LB_ARNS=$(aws elbv2 describe-load-balancers --query "LoadBalancers[?VpcId=='${VPC_ID}'].LoadBalancerArn" --output text 2>/dev/null || echo "")
  for arn in $LB_ARNS; do
    echo "  Deleting load balancer..."
    aws elbv2 delete-load-balancer --load-balancer-arn "$arn" 2>/dev/null || true
  done

  # Wait for ENIs to release if we deleted any LBs
  if [[ -n "$LB_ARNS" ]]; then
    echo "  Waiting 60s for load balancer ENIs to release..."
    sleep 60
  fi

  # Delete orphaned target groups
  TG_ARNS=$(aws elbv2 describe-target-groups --query "TargetGroups[?VpcId=='${VPC_ID}'].TargetGroupArn" --output text 2>/dev/null || echo "")
  for arn in $TG_ARNS; do
    echo "  Deleting orphaned target group..."
    aws elbv2 delete-target-group --target-group-arn "$arn" 2>/dev/null || true
  done

  # Delete orphaned security groups (non-default)
  SG_IDS=$(aws ec2 describe-security-groups --filters "Name=vpc-id,Values=${VPC_ID}" --query "SecurityGroups[?GroupName!='default'].GroupId" --output text 2>/dev/null || echo "")
  for sg in $SG_IDS; do
    echo "  Deleting orphaned security group $sg..."
    aws ec2 delete-security-group --group-id "$sg" 2>/dev/null || true
  done
else
  echo "  - VPC not found, skipping"
fi

echo ""
echo "[9/15] Destroying CloudFormation stacks (reverse dependency order)..."
# CDK's --all flag cannot delete the conditional DevOpsAgent stack (it is not
# in the synth output when the webhook URL context is absent).  We therefore
# delete stacks directly via CloudFormation in explicit reverse-dependency
# order so cross-stack exports are removed before the exporting stack is
# deleted.
#
# Dependency graph (from app.ts):
#   FrontendStack imports FailureSimulatorApi.apiId → delete Frontend FIRST
#   DevOpsAgent → Compute (clusterName), Monitoring (criticalAlarmsTopicArn)
#   FailureSimulatorApi → Compute (clusterName), Network (vpc, subnets, SG)
#   Database    → Network (vpc, subnets, SG)
#   Compute     → Network (subnets, SG)
#   Network     → base (deleted last)

STACK_DELETE_ORDER=(
  "DevOpsAgentEksDevOpsAgent-${REGION}"
  "DevOpsAgentEksFrontend-${REGION}"
  "DevOpsAgentEksFailureSimulatorApi-${REGION}"
  "DevOpsAgentEksMcpServer-${REGION}"
  "DevOpsAgentEksMonitoring-${REGION}"
  "DevOpsAgentEksPipeline-${REGION}"
  "DevOpsAgentEksAuth-${REGION}"
  "DevOpsAgentEksDatabase-${REGION}"
  "DevOpsAgentEksCompute-${REGION}"
  "DevOpsAgentEksNetwork-${REGION}"
)

for stack in "${STACK_DELETE_ORDER[@]}"; do
  STATUS=$(aws cloudformation describe-stacks --stack-name "$stack" --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo "DOES_NOT_EXIST")
  if [[ "$STATUS" == "DOES_NOT_EXIST" || "$STATUS" == "DELETE_COMPLETE" ]]; then
    echo "  - $stack not found, skipping"
    continue
  fi
  echo "  Deleting $stack ($STATUS)..."

  # Network stack: AgentCore private connection ENIs are service-managed and
  # cannot be force-detached. After the private connection is deleted (step 14),
  # they typically release within a few minutes but can take up to several hours.
  # Strategy: poll for up to 5 minutes, then fall back to retain-and-cleanup.
  if [[ "$stack" == *"Network"* && "$VPC_ID" != "None" && -n "$VPC_ID" ]]; then
    echo "  Waiting for AgentCore ENIs to release (up to 5 minutes)..."
    ENI_CLEAR="false"
    for attempt in $(seq 1 30); do  # 30 × 10s = 5 minutes
      # Exclude NAT Gateway ENIs — they're stack-managed and deleted automatically
      IN_USE_ENIS=$(aws ec2 describe-network-interfaces \
          --filters "Name=vpc-id,Values=${VPC_ID}" "Name=status,Values=in-use" \
          --query "NetworkInterfaces[?!contains(Description,'NAT Gateway')].NetworkInterfaceId" \
          --output text 2>/dev/null || echo "")
      if [[ -z "$IN_USE_ENIS" ]]; then
        echo "  ✓ All non-stack ENIs released"
        ENI_CLEAR="true"
        break
      fi
      echo "  ENIs still in-use ($attempt/30)... waiting 10s"
      sleep 10
    done

    # Delete any available (detached) ENIs
    AVAIL_ENIS=$(aws ec2 describe-network-interfaces \
        --filters "Name=vpc-id,Values=${VPC_ID}" "Name=status,Values=available" \
        --query 'NetworkInterfaces[*].NetworkInterfaceId' --output text 2>/dev/null || echo "")
    for eni in $AVAIL_ENIS; do
      aws ec2 delete-network-interface --network-interface-id "$eni" 2>/dev/null || true
    done

    if [[ "$ENI_CLEAR" == "true" ]]; then
      aws cloudformation delete-stack --stack-name "$stack" 2>/dev/null || true
    else
      echo "  ⚠ AgentCore ENIs still attached (service-managed, cannot force-detach)."
      echo "    Deleting stack with subnet retention. Run this later to finish cleanup:"
      echo "      aws cloudformation delete-stack --stack-name $stack"
      aws cloudformation delete-stack --stack-name "$stack" \
          --retain-resources Vpc8378EB38 VpcPrivateComputeSubnet1Subnet106EC80E VpcPrivateComputeSubnet2Subnet2118056D 2>/dev/null || true
    fi
  else
    aws cloudformation delete-stack --stack-name "$stack" 2>/dev/null || true
  fi

  aws cloudformation wait stack-delete-complete --stack-name "$stack" 2>/dev/null || true
  FINAL=$(aws cloudformation describe-stacks --stack-name "$stack" --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo "DELETE_COMPLETE")
  if [[ "$FINAL" == "DELETE_FAILED" ]]; then
    echo "  ⚠ $stack stuck in DELETE_FAILED — will retry in step 11"
  else
    echo "  ✓ $stack deleted"
  fi
done

# Fallback: try deleting legacy CloudFormation root stack if it exists
if aws cloudformation describe-stacks --stack-name "$PROJECT_NAME" &>/dev/null; then
  echo "  Deleting legacy root stack $PROJECT_NAME..."
  aws cloudformation delete-stack --stack-name "$PROJECT_NAME"
  aws cloudformation wait stack-delete-complete --stack-name "$PROJECT_NAME" 2>/dev/null || true
  echo "  ✓ Root stack deleted"
fi

echo ""
echo "[10/15] Cleaning up remaining S3 buckets (if any survived stack deletion)..."
for bucket in "${BUCKETS[@]}"; do
  if aws s3api head-bucket --bucket "$bucket" 2>/dev/null; then
    echo "  Force-deleting bucket $bucket..."
    aws s3 rm "s3://$bucket" --recursive 2>/dev/null || true
    aws s3 rb "s3://$bucket" --force 2>/dev/null || true
    echo "  ✓ $bucket deleted"
  fi
done

echo ""
echo "[11/15] Cleaning up any orphaned stacks in DELETE_FAILED state..."
FAILED_STACKS=""
# Check for both legacy CloudFormation and CDK stack name patterns
for pattern in "$PROJECT_NAME" "DevOpsAgentEks"; do
  FOUND=$(aws cloudformation list-stacks --stack-status-filter DELETE_FAILED \
    --query "StackSummaries[?contains(StackName,'${pattern}')].StackName" --output text 2>/dev/null || echo "")
  FAILED_STACKS="$FAILED_STACKS $FOUND"
done
FAILED_STACKS=$(echo "$FAILED_STACKS" | tr '\t' '\n' | sort -u | xargs)
if [[ -n "$FAILED_STACKS" ]]; then
  for stack in $FAILED_STACKS; do
    echo "  Retrying deletion of $stack..."
    aws cloudformation delete-stack --stack-name "$stack" 2>/dev/null || true
    aws cloudformation wait stack-delete-complete --stack-name "$stack" 2>/dev/null || true
    # If still stuck, identify failed resources and retain them to force-delete the stack
    RETRY_STATUS=$(aws cloudformation describe-stacks --stack-name "$stack" --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo "DELETE_COMPLETE")
    if [[ "$RETRY_STATUS" == "DELETE_FAILED" ]]; then
      echo "  ⚠ $stack still in DELETE_FAILED — retaining failed resources..."
      FAILED_RESOURCES=$(aws cloudformation describe-stack-events --stack-name "$stack" \
        --query "StackEvents[?ResourceStatus=='DELETE_FAILED'].LogicalResourceId" --output text 2>/dev/null | tr '\t' ' ' | xargs -n1 | sort -u | xargs)
      if [[ -n "$FAILED_RESOURCES" ]]; then
        aws cloudformation delete-stack --stack-name "$stack" --retain-resources $FAILED_RESOURCES 2>/dev/null || true
        aws cloudformation wait stack-delete-complete --stack-name "$stack" 2>/dev/null || true
      fi
    fi
    echo "  ✓ $stack deleted"
  done
else
  echo "  - No orphaned stacks found"
fi

# Clean up orphaned Cognito user pools (created by AgentCore Gateway CDK construct)
echo "  Cleaning up orphaned Cognito user pools..."
for POOL_ID in $(aws cognito-idp list-user-pools --max-results 20 --region "$REGION" --query "UserPools[?contains(Name,'mcp')].Id" --output text --no-cli-pager 2>/dev/null); do
  DOMAIN=$(aws cognito-idp describe-user-pool --user-pool-id "$POOL_ID" --region "$REGION" --query "UserPool.Domain" --output text --no-cli-pager 2>/dev/null || echo "")
  if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "None" ]; then
    aws cognito-idp delete-user-pool-domain --domain "$DOMAIN" --user-pool-id "$POOL_ID" --region "$REGION" --no-cli-pager 2>/dev/null || true
  fi
  aws cognito-idp delete-user-pool --user-pool-id "$POOL_ID" --region "$REGION" --no-cli-pager 2>/dev/null || true
  echo "  Deleted Cognito pool $POOL_ID"
done

echo ""
echo "[12/15] Deleting Secrets Manager secret..."

# Clean up orphaned IAM roles (after all stacks are deleted)
echo "  Cleaning up orphaned IAM roles..."
for ROLE in $(aws iam list-roles --query "Roles[?contains(RoleName,'${PROJECT_NAME}-${ENVIRONMENT}')].RoleName" --output text --no-cli-pager 2>/dev/null); do
  for P in $(aws iam list-attached-role-policies --role-name "$ROLE" --query 'AttachedPolicies[*].PolicyArn' --output text 2>/dev/null); do
    aws iam detach-role-policy --role-name "$ROLE" --policy-arn "$P" 2>/dev/null || true
  done
  for P in $(aws iam list-role-policies --role-name "$ROLE" --query 'PolicyNames[*]' --output text 2>/dev/null); do
    aws iam delete-role-policy --role-name "$ROLE" --policy-name "$P" 2>/dev/null || true
  done
  for IP in $(aws iam list-instance-profiles-for-role --role-name "$ROLE" --query 'InstanceProfiles[*].InstanceProfileName' --output text 2>/dev/null); do
    aws iam remove-role-from-instance-profile --instance-profile-name "$IP" --role-name "$ROLE" 2>/dev/null || true
  done
  aws iam delete-role --role-name "$ROLE" 2>/dev/null || true
done
echo "  ✓ IAM roles cleaned up"
SECRET_NAME="${PROJECT_NAME}-${ENVIRONMENT}-rds-credentials"
if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" &>/dev/null 2>&1; then
  echo "  Deleting $SECRET_NAME (force, no recovery window)..."
  aws secretsmanager delete-secret --secret-id "$SECRET_NAME" --force-delete-without-recovery 2>/dev/null || true
  echo "  ✓ Secret deleted"
else
  echo "  - Secret not found, skipping"
fi
# Also clean up DevOps Agent webhook secret if it survived stack deletion
WEBHOOK_SECRET_NAME="${PROJECT_NAME}-${ENVIRONMENT}/devops-agent-webhook-secret"
if aws secretsmanager describe-secret --secret-id "$WEBHOOK_SECRET_NAME" &>/dev/null 2>&1; then
  echo "  Deleting $WEBHOOK_SECRET_NAME (force, no recovery window)..."
  aws secretsmanager delete-secret --secret-id "$WEBHOOK_SECRET_NAME" --force-delete-without-recovery 2>/dev/null || true
  echo "  ✓ Webhook secret deleted"
fi
# Clean up MCP server read-only DB credentials secret
MCP_SECRET_NAME="${PROJECT_NAME}-${ENVIRONMENT}-mcp-readonly-credentials"
if aws secretsmanager describe-secret --secret-id "$MCP_SECRET_NAME" &>/dev/null 2>&1; then
  echo "  Deleting $MCP_SECRET_NAME (force, no recovery window)..."
  aws secretsmanager delete-secret --secret-id "$MCP_SECRET_NAME" --force-delete-without-recovery 2>/dev/null || true
  echo "  ✓ MCP secret deleted"
fi

echo ""
echo "[13/15] Cleaning up CloudWatch log groups and kubeconfig..."
# Delete EKS, Lambda, and CodeBuild log groups created outside CloudFormation
LOG_GROUPS=$(aws logs describe-log-groups \
  --log-group-name-prefix "/aws/eks/${PROJECT_NAME}" \
  --query 'logGroups[*].logGroupName' --output text 2>/dev/null || echo "")
LAMBDA_LOG_GROUPS=$(aws logs describe-log-groups \
  --log-group-name-prefix "/aws/lambda/${PROJECT_NAME}" \
  --query 'logGroups[*].logGroupName' --output text 2>/dev/null || echo "")
CODEBUILD_LOG_GROUPS=$(aws logs describe-log-groups \
  --log-group-name-prefix "/aws/codebuild/${PROJECT_NAME}-" \
  --query 'logGroups[*].logGroupName' --output text 2>/dev/null || echo "")
COGNITO_LOG_GROUPS=$(aws logs describe-log-groups \
  --log-group-name-prefix "/aws/cognito/${PROJECT_NAME}-" \
  --query 'logGroups[*].logGroupName' --output text 2>/dev/null || echo "")
LOG_GROUPS="$LOG_GROUPS $LAMBDA_LOG_GROUPS $CODEBUILD_LOG_GROUPS $COGNITO_LOG_GROUPS"
if [[ -n "${LOG_GROUPS// /}" ]]; then
  for lg in $LOG_GROUPS; do
    echo "  Deleting log group $lg..."
    aws logs delete-log-group --log-group-name "$lg" 2>/dev/null || true
  done
  echo "  ✓ Log groups deleted"
else
  echo "  - No orphaned log groups found"
fi

# Remove kubeconfig context for the deleted cluster
EKS_CONTEXT="arn:aws:eks:${REGION}:${ACCOUNT_ID}:cluster/${PROJECT_NAME}-${ENVIRONMENT}-cluster"
if kubectl config get-contexts "$EKS_CONTEXT" &>/dev/null 2>&1; then
  echo "  Removing kubeconfig context..."
  kubectl config delete-context "$EKS_CONTEXT" 2>/dev/null || true
  kubectl config delete-cluster "$EKS_CONTEXT" 2>/dev/null || true
  echo "  ✓ kubeconfig cleaned"
else
  echo "  - kubeconfig context not found, skipping"
fi

echo ""
echo "[14/15] Cleaning up AgentCore Gateway MCP server..."
DEVOPS_AGENT_REGION="${DEVOPS_AGENT_REGION:-us-east-1}"

# Disassociate MCP services from ALL agent spaces, then deregister
echo "  Disassociating MCP services from agent spaces..."
for SPACE in $(aws devops-agent list-agent-spaces --region "$DEVOPS_AGENT_REGION" --query 'agentSpaces[*].agentSpaceId' --output text --no-cli-pager 2>/dev/null); do
    for AID in $(aws devops-agent list-associations --agent-space-id "$SPACE" --region "$DEVOPS_AGENT_REGION" --no-cli-pager --output json 2>/dev/null | jq -r '.associations[] | select(.configuration.mcpserver != null) | .associationId' 2>/dev/null); do
        aws devops-agent disassociate-service --agent-space-id "$SPACE" --association-id "$AID" --region "$DEVOPS_AGENT_REGION" --no-cli-pager 2>/dev/null || true
    done
done

# Deregister all MCP services matching our project
for SID in $(aws devops-agent list-services --region "$DEVOPS_AGENT_REGION" --no-cli-pager --output json 2>/dev/null | jq -r '.services[].serviceId' 2>/dev/null); do
    aws devops-agent deregister-service --service-id "$SID" --region "$DEVOPS_AGENT_REGION" --no-cli-pager 2>/dev/null || true
done
echo "  ✓ MCP services deregistered"

# Delete private connections
for CONN_NAME in "mcp-gw-conn" "mcp-gateway-conn" "mcp-paytxninsights" "mcp-test-conn"; do
    CONN_EXISTS=$(aws devops-agent describe-private-connection --name "$CONN_NAME" --region "$DEVOPS_AGENT_REGION" --query "status" --output text --no-cli-pager 2>/dev/null || echo "GONE")
    if [ "$CONN_EXISTS" != "GONE" ] && [ "$CONN_EXISTS" != "None" ]; then
        echo "  Deleting private connection '$CONN_NAME' (status: $CONN_EXISTS)..."
        aws devops-agent delete-private-connection --name "$CONN_NAME" --region "$DEVOPS_AGENT_REGION" --no-cli-pager 2>/dev/null || true
    fi
done
echo "  Waiting for private connections to fully delete..."
for CONN_NAME in "mcp-gw-conn" "mcp-gateway-conn" "mcp-paytxninsights" "mcp-test-conn"; do
    for i in $(seq 1 36); do  # up to 6 minutes
        STATUS=$(aws devops-agent describe-private-connection --name "$CONN_NAME" --region "$DEVOPS_AGENT_REGION" --query "status" --output text --no-cli-pager 2>/dev/null || echo "GONE")
        if [ "$STATUS" = "GONE" ] || [ "$STATUS" = "None" ]; then
            break
        fi
        if [ "$i" -eq 1 ]; then
            echo "  Waiting for '$CONN_NAME' ($STATUS)..."
        fi
        sleep 10
    done
done
echo "  ✓ Private connections deleted"

# Delete orphaned AgentCore runtimes from test deployments
for RT in $(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" --no-cli-pager --output json 2>/dev/null | jq -r '.agentRuntimes[] | select(.agentRuntimeName | test("paytxn")) | .agentRuntimeId' 2>/dev/null); do
    echo "  Deleting AgentCore runtime $RT..."
    aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id "$RT" --region "$REGION" --no-cli-pager 2>/dev/null || true
done

# Wait for private connection ENIs to release (they block VPC/subnet deletion)
echo "  Waiting for private connection ENIs to release (up to 5 minutes)..."
sleep 60
for i in $(seq 1 24); do
    REMAINING=$(aws devops-agent describe-private-connection --name mcp-gw-conn --region "$DEVOPS_AGENT_REGION" --query status --output text --no-cli-pager 2>/dev/null || echo "GONE")
    if [ "$REMAINING" = "GONE" ] || [ "$REMAINING" = "None" ]; then
        echo "  ✓ Private connections fully deleted"
        break
    fi
    if [ "$i" -eq 24 ]; then
        echo "  WARNING: Private connections may still have ENIs. VPC deletion might fail."
    fi
    sleep 10
done

echo ""
echo "[15/15] Cleaning up DevOps Agent resources..."

# Delete only the Agent Space matching our project name (not all spaces in the account)
AGENT_SPACE_ID=$(aws devops-agent list-agent-spaces \
    --region "$DEVOPS_AGENT_REGION" \
    --query "agentSpaces[?name=='${PROJECT_NAME}'].agentSpaceId | [0]" \
    --output text --no-cli-pager 2>/dev/null || echo "")

if [[ -n "$AGENT_SPACE_ID" && "$AGENT_SPACE_ID" != "None" ]]; then
  echo "  Deleting Agent Space $AGENT_SPACE_ID (${PROJECT_NAME})..."
  # Remove associations first
  ASSOC_IDS=$(aws devops-agent list-associations \
      --agent-space-id "$AGENT_SPACE_ID" \
      --region "$DEVOPS_AGENT_REGION" \
      --query 'associations[*].associationId' \
      --output text --no-cli-pager 2>/dev/null || echo "")
  for assoc_id in $ASSOC_IDS; do
    if [[ -n "$assoc_id" && "$assoc_id" != "None" ]]; then
      echo "    Removing association $assoc_id..."
      aws devops-agent disassociate-service \
          --agent-space-id "$AGENT_SPACE_ID" \
          --association-id "$assoc_id" \
          --region "$DEVOPS_AGENT_REGION" \
          --no-cli-pager 2>/dev/null || true
    fi
  done
  aws devops-agent delete-agent-space \
      --agent-space-id "$AGENT_SPACE_ID" \
      --region "$DEVOPS_AGENT_REGION" \
      --no-cli-pager 2>/dev/null || true
  echo "  ✓ Agent Space $AGENT_SPACE_ID deleted"
else
  echo "  - No Agent Space named '${PROJECT_NAME}' found"
fi

# Delete DevOps Agent IAM roles (both old script-created and CDK-managed names)
for ROLE_NAME in "${PROJECT_NAME}-AgentSpaceRole" "${PROJECT_NAME}-OperatorRole" "${PROJECT_NAME}-${ENVIRONMENT}-AgentSpaceRole" "${PROJECT_NAME}-${ENVIRONMENT}-OperatorRole"; do
  if aws iam get-role --role-name "$ROLE_NAME" &>/dev/null 2>&1; then
    echo "  Deleting IAM role $ROLE_NAME..."
    # Detach managed policies
    POLICIES=$(aws iam list-attached-role-policies --role-name "$ROLE_NAME" --query 'AttachedPolicies[*].PolicyArn' --output text 2>/dev/null || echo "")
    for policy_arn in $POLICIES; do
      if [[ -n "$policy_arn" && "$policy_arn" != "None" ]]; then
        aws iam detach-role-policy --role-name "$ROLE_NAME" --policy-arn "$policy_arn" 2>/dev/null || true
      fi
    done
    # Delete inline policies
    INLINE_POLICIES=$(aws iam list-role-policies --role-name "$ROLE_NAME" --query 'PolicyNames[*]' --output text 2>/dev/null || echo "")
    for policy_name in $INLINE_POLICIES; do
      if [[ -n "$policy_name" && "$policy_name" != "None" ]]; then
        aws iam delete-role-policy --role-name "$ROLE_NAME" --policy-name "$policy_name" 2>/dev/null || true
      fi
    done
    aws iam delete-role --role-name "$ROLE_NAME" 2>/dev/null || true
    echo "  ✓ $ROLE_NAME deleted"
  else
    echo "  - $ROLE_NAME not found, skipping"
  fi
done

echo ""
echo "============================================"
echo "Cleanup complete!"
echo "============================================"
