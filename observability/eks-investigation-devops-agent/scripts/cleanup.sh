#!/bin/bash
# =============================================================================
# DevOps Agent EKS Demo - Complete Cleanup Script
# Deletes all deployed infrastructure with zero leftover resources or costs
# =============================================================================
set -euo pipefail

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
echo "[1/14] Installing CDK dependencies..."
if [ -d "cdk" ]; then
  echo "  Running npm install in cdk/..."
  cd cdk && npm install --silent && cd ..
  echo "  ✓ CDK dependencies installed"
else
  echo "  - cdk/ directory not found, skipping"
fi

echo ""
echo "[2/14] Cleaning up CodeBuild source bundles..."
CFN_BUCKET="${PROJECT_NAME}-cfn-templates-${ACCOUNT_ID}"
if aws s3api head-bucket --bucket "$CFN_BUCKET" 2>/dev/null; then
  echo "  Removing codebuild-sources/ from $CFN_BUCKET..."
  aws s3 rm "s3://$CFN_BUCKET/codebuild-sources/" --recursive 2>/dev/null || true
  echo "  ✓ CodeBuild source bundles removed"
else
  echo "  - Bucket $CFN_BUCKET not found, skipping"
fi

echo ""
echo "[3/14] Emptying S3 buckets..."
BUCKETS=(
  "${PROJECT_NAME}-${ENVIRONMENT}-merchant-portal-${ACCOUNT_ID}"
  "${PROJECT_NAME}-cfn-templates-${ACCOUNT_ID}"
)

for bucket in "${BUCKETS[@]}"; do
  if aws s3api head-bucket --bucket "$bucket" 2>/dev/null; then
    echo "  Removing bucket policy for $bucket..."
    aws s3api delete-bucket-policy --bucket "$bucket" 2>/dev/null || true

    echo "  Removing all objects from $bucket..."
    aws s3 rm "s3://$bucket" --recursive 2>/dev/null || true
    echo "  ✓ $bucket emptied"
  else
    echo "  - $bucket not found, skipping"
  fi
done

echo ""
echo "[4/14] Deleting ECR images..."
REPOS=(
  "${PROJECT_NAME}/merchant-gateway"
  "${PROJECT_NAME}/payment-processor"
  "${PROJECT_NAME}/webhook-service"
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
echo "[5/14] Disabling RDS deletion protection..."
DB_INSTANCE="${PROJECT_NAME}-${ENVIRONMENT}-postgres"
if aws rds describe-db-instances --db-instance-identifier "$DB_INSTANCE" &>/dev/null; then
  echo "  Disabling deletion protection on $DB_INSTANCE..."
  aws rds modify-db-instance \
    --db-instance-identifier "$DB_INSTANCE" \
    --no-deletion-protection \
    --apply-immediately 2>/dev/null || true
  echo "  ✓ Deletion protection disabled"
else
  echo "  - RDS instance not found, skipping"
fi

echo ""
echo "[6/14] Cleaning up Kubernetes resources before stack deletion..."
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
echo "[7/14] Cleaning up orphaned VPC endpoints..."
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
echo "[8/14] Cleaning up orphaned load balancers, target groups, and security groups..."
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
echo "[9/14] Destroying CloudFormation stacks (reverse dependency order)..."
# CDK's --all flag cannot delete the conditional DevOpsAgent stack (it is not
# in the synth output when the webhook URL context is absent).  We therefore
# delete stacks directly via CloudFormation in explicit reverse-dependency
# order so cross-stack exports are removed before the exporting stack is
# deleted.
#
# Dependency graph (from app.ts):
#   DevOpsAgent → Compute (clusterName), Monitoring (criticalAlarmsTopicArn)
#   Database    → Network (vpc, subnets, SG)
#   Compute     → Network (subnets, SG)
#   Monitoring, Pipeline, Frontend, Auth → independent
#   Network     → base (deleted last)

STACK_DELETE_ORDER=(
  "DevOpsAgentEksDevOpsAgent-${REGION}"
  "DevOpsAgentEksFailureSimulatorApi-${REGION}"
  "DevOpsAgentEksMonitoring-${REGION}"
  "DevOpsAgentEksPipeline-${REGION}"
  "DevOpsAgentEksFrontend-${REGION}"
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
  aws cloudformation delete-stack --stack-name "$stack" 2>/dev/null || true
  aws cloudformation wait stack-delete-complete --stack-name "$stack" 2>/dev/null || true
  # Verify deletion succeeded
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
echo "[10/14] Cleaning up remaining S3 buckets (if any survived stack deletion)..."
for bucket in "${BUCKETS[@]}"; do
  if aws s3api head-bucket --bucket "$bucket" 2>/dev/null; then
    echo "  Force-deleting bucket $bucket..."
    aws s3 rm "s3://$bucket" --recursive 2>/dev/null || true
    aws s3 rb "s3://$bucket" --force 2>/dev/null || true
    echo "  ✓ $bucket deleted"
  fi
done

echo ""
echo "[11/14] Cleaning up any orphaned stacks in DELETE_FAILED state..."
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

echo ""
echo "[12/14] Deleting Secrets Manager secret..."
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

echo ""
echo "[13/14] Cleaning up CloudWatch log groups and kubeconfig..."
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
echo "[14/14] Cleaning up DevOps Agent resources..."
DEVOPS_AGENT_ENDPOINT="https://api.prod.cp.aidevops.us-east-1.api.aws"
DEVOPS_AGENT_REGION="us-east-1"

# Auto-patch AWS CLI with DevOps Agent service model if not already available
# NOTE: We test with an actual API call (list-agent-spaces) instead of 'help'
# because 'aws <service> help' is unreliable with custom service models on
# newer AWS CLI versions.
DEVOPSAGENT_CLI_AVAILABLE=false
if aws devopsagent list-agent-spaces --endpoint-url "$DEVOPS_AGENT_ENDPOINT" --region "$DEVOPS_AGENT_REGION" &>/dev/null 2>&1; then
    DEVOPSAGENT_CLI_AVAILABLE=true
else
    echo "  DevOps Agent CLI not found — patching AWS CLI..."
    if curl -sf -o /tmp/devopsagent.json https://d1co8nkiwcta1g.cloudfront.net/devopsagent.json 2>/dev/null; then
        aws configure add-model --service-model file:///tmp/devopsagent.json --service-name devopsagent 2>/dev/null
        echo "  AWS CLI patched with DevOps Agent service model."
        # Verify the patch worked with an actual API call
        if aws devopsagent list-agent-spaces --endpoint-url "$DEVOPS_AGENT_ENDPOINT" --region "$DEVOPS_AGENT_REGION" &>/dev/null 2>&1; then
            DEVOPSAGENT_CLI_AVAILABLE=true
        fi
    else
        echo "  WARNING: Could not download DevOps Agent service model. Skipping Agent Space cleanup."
    fi
fi

# Delete only the Agent Space matching our project name (not all spaces in the account)
if [ "$DEVOPSAGENT_CLI_AVAILABLE" = true ]; then
  AGENT_SPACE_ID=$(aws devopsagent list-agent-spaces \
      --endpoint-url "$DEVOPS_AGENT_ENDPOINT" \
      --region "$DEVOPS_AGENT_REGION" \
      --query "agentSpaces[?name=='${PROJECT_NAME}'].agentSpaceId | [0]" \
      --output text 2>/dev/null || echo "")
  if [[ -n "$AGENT_SPACE_ID" && "$AGENT_SPACE_ID" != "None" ]]; then
    echo "  Deleting Agent Space $AGENT_SPACE_ID (${PROJECT_NAME})..."
    # Remove associations first
    ASSOC_IDS=$(aws devopsagent list-associations \
        --agent-space-id "$AGENT_SPACE_ID" \
        --endpoint-url "$DEVOPS_AGENT_ENDPOINT" \
        --region "$DEVOPS_AGENT_REGION" \
        --query 'associations[*].associationId' \
        --output text 2>/dev/null || echo "")
    for assoc_id in $ASSOC_IDS; do
      if [[ -n "$assoc_id" && "$assoc_id" != "None" ]]; then
        echo "    Removing association $assoc_id..."
        aws devopsagent disassociate-service \
            --agent-space-id "$AGENT_SPACE_ID" \
            --association-id "$assoc_id" \
            --endpoint-url "$DEVOPS_AGENT_ENDPOINT" \
            --region "$DEVOPS_AGENT_REGION" 2>/dev/null || true
      fi
    done
    aws devopsagent delete-agent-space \
        --agent-space-id "$AGENT_SPACE_ID" \
        --endpoint-url "$DEVOPS_AGENT_ENDPOINT" \
        --region "$DEVOPS_AGENT_REGION" 2>/dev/null || true
    echo "  ✓ Agent Space $AGENT_SPACE_ID deleted"
  else
    echo "  - No Agent Space named '${PROJECT_NAME}' found"
  fi
else
  echo "  - DevOps Agent CLI not available, skipping Agent Space deletion"
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
