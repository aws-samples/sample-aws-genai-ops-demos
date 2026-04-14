#!/bin/bash
set -e

# =============================================================================
# DevOps Agent EKS Demo Platform - Zero-Touch Deployment Script
# =============================================================================
# Deploys the entire DevOps Agent EKS Demo stack with no manual intervention:
#   1. Install CDK dependencies and upload CodeBuild sources to S3
#   2. Deploy CDK stacks (includes RDS credentials secret)
#   3. Configure kubectl for EKS
#   4. Build and push container images via CodeBuild
#   5. Apply Kubernetes manifests
#   6. Create Cognito user & run DB migrations (before pod wait)
#   7. Wait for pods and NLB
#   8. Update CloudFront with NLB API origin via CDK
#   9. Build and deploy frontend
# =============================================================================

ENVIRONMENT="${1:-dev}"
PROJECT_NAME="devops-agent-eks"

echo "=============================================="
echo " DevOps Agent EKS Demo - Automated Deployment"
echo "=============================================="
echo ""

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "[prereqs] Running prerequisites check..."
source "$REPO_ROOT/shared/scripts/check-prerequisites.sh" --require-cdk --require-kubectl --skip-service-check --min-aws-cli-version 2.34.21

# Check for zip utility (required for CodeBuild source packaging)
if ! command -v zip &>/dev/null; then
    echo ""
    echo "ERROR: 'zip' command not found. It is required to package service source code for CodeBuild."
    echo ""
    echo "Install it with one of the following:"
    echo "  macOS:       brew install zip"
    echo "  Ubuntu/Debian: sudo apt-get install zip"
    echo "  Amazon Linux/RHEL: sudo yum install zip"
    echo ""
    exit 1
fi
echo ""

AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Account:     $AWS_ACCOUNT_ID"
echo "Region:      $AWS_REGION"
echo "Environment: $ENVIRONMENT"
echo ""

# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# DevOps Agent setup (Agent Space + webhook)
# ---------------------------------------------------------------------------
# Agent Space is created via CLI in the DevOps Agent region (e.g. us-east-1)
# because AWS::DevOpsAgent CloudFormation resources are not available in all regions.
if [ -z "$DEVOPS_AGENT_WEBHOOK_URL" ] || [ -z "$DEVOPS_AGENT_WEBHOOK_SECRET" ]; then
    source "$SCRIPT_DIR/scripts/setup-devops-agent.sh"
    setup_devops_agent "$PROJECT_NAME"
fi

if [ -z "$DEVOPS_AGENT_WEBHOOK_URL" ] || [ -z "$DEVOPS_AGENT_WEBHOOK_SECRET" ]; then
    echo ""
    echo "ERROR: DevOps Agent webhook URL and secret are required."
    echo ""
    echo "Either provide them as environment variables before running this script:"
    echo "  export DEVOPS_AGENT_WEBHOOK_URL=\"https://...\""
    echo "  export DEVOPS_AGENT_WEBHOOK_SECRET=\"...\""
    echo "  bash deploy-all.sh"
    echo ""
    exit 1
fi

DEVOPS_WEBHOOK_URL="$DEVOPS_AGENT_WEBHOOK_URL"
DEVOPS_WEBHOOK_SECRET="$DEVOPS_AGENT_WEBHOOK_SECRET"
DEVOPS_AGENT_SPACE_ID="${DEVOPS_AGENT_SPACE_ID:-}"
DEVOPS_AGENT_REGION="${DEVOPS_AGENT_REGION:-us-east-1}"
echo "DevOps Agent webhook: CONFIGURED"
echo ""

# EKS node architecture — override via EKS_ARCHITECTURE env var, default arm64
# (matches the CloudFormation EksNodeArchitecture parameter default)
# ---------------------------------------------------------------------------
EKS_ARCHITECTURE="${EKS_ARCHITECTURE:-arm64}"
case "$EKS_ARCHITECTURE" in
    arm64)  EKS_INSTANCE_TYPE="t4g.medium" ;;
    amd64)  EKS_INSTANCE_TYPE="t3.medium"  ;;
    *)
        echo "ERROR: Invalid EKS_ARCHITECTURE '$EKS_ARCHITECTURE'. Must be arm64 or amd64."
        exit 1
        ;;
esac
echo "EKS architecture: $EKS_ARCHITECTURE"
echo "EKS instance:     $EKS_INSTANCE_TYPE"
echo ""

ECR_REGISTRY="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

# =============================================================================
# STEP 1: Install CDK dependencies and create S3 bucket for CodeBuild sources
# =============================================================================
echo "[1/9] Installing CDK dependencies and preparing S3 bucket..."
BUCKET_NAME="$PROJECT_NAME-cfn-templates-$AWS_ACCOUNT_ID"
aws s3 mb "s3://$BUCKET_NAME" --region "$AWS_REGION" 2>/dev/null || true

cd cdk && npm install && cd ..
echo "  done."
echo ""

# =============================================================================
# STEP 2: Deploy CDK stacks (initial, no API endpoint yet)
# =============================================================================
# NOTE: RDS credentials secret is created by CDK's DatabaseStack (not by this
# script) to avoid CloudFormation AlreadyExists conflicts.
echo "[2/9] Deploying CDK stacks (this takes ~15 minutes)..."

CDK_CONTEXT="-c environment=$ENVIRONMENT -c projectName=$PROJECT_NAME -c eksNodeArchitecture=$EKS_ARCHITECTURE -c eksNodeInstanceType=$EKS_INSTANCE_TYPE -c eksNodeDesiredCapacity=2 -c devOpsAgentWebhookUrl=$DEVOPS_WEBHOOK_URL -c devOpsAgentWebhookSecret=$DEVOPS_WEBHOOK_SECRET -c devOpsAgentRegion=$DEVOPS_AGENT_REGION -c devOpsAgentSpaceId=$DEVOPS_AGENT_SPACE_ID"

cd cdk
npx cdk deploy --all \
    $CDK_CONTEXT \
    --require-approval never \
    --no-cli-pager
cd ..
echo "  Infrastructure deployed."
echo ""

# =============================================================================
# STEP 3: Configure kubectl for EKS
# =============================================================================
echo "[3/9] Configuring kubectl..."
aws eks update-kubeconfig \
    --name "$PROJECT_NAME-$ENVIRONMENT-cluster" \
    --region "$AWS_REGION"
echo "  kubectl configured."

# Grant the deploying IAM principal cluster-admin access via EKS access entries
CALLER_ARN=$(aws sts get-caller-identity --query Arn --output text)
echo "  Granting EKS access to $CALLER_ARN..."
aws eks create-access-entry \
    --cluster-name "$PROJECT_NAME-$ENVIRONMENT-cluster" \
    --principal-arn "$CALLER_ARN" \
    --type STANDARD \
    --region "$AWS_REGION" >/dev/null 2>&1 || true
aws eks associate-access-policy \
    --cluster-name "$PROJECT_NAME-$ENVIRONMENT-cluster" \
    --principal-arn "$CALLER_ARN" \
    --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy \
    --access-scope type=cluster \
    --region "$AWS_REGION" >/dev/null 2>&1 || true
echo "  EKS access granted."

# Grant Failure Simulator Lambda access to EKS cluster
FAILURE_SIM_LAMBDA_ROLE_ARN=$(aws cloudformation describe-stacks \
    --stack-name "DevOpsAgentEksFailureSimulatorApi-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='FailureSimulatorLambdaRoleArn'].OutputValue" \
    --output text --region "$AWS_REGION" 2>/dev/null || echo "")
if [ -n "$FAILURE_SIM_LAMBDA_ROLE_ARN" ] && [ "$FAILURE_SIM_LAMBDA_ROLE_ARN" != "None" ]; then
    echo "  Granting EKS access to Failure Simulator Lambda ($FAILURE_SIM_LAMBDA_ROLE_ARN)..."
    aws eks create-access-entry \
        --cluster-name "$PROJECT_NAME-$ENVIRONMENT-cluster" \
        --principal-arn "$FAILURE_SIM_LAMBDA_ROLE_ARN" \
        --type STANDARD \
        --region "$AWS_REGION" >/dev/null 2>&1 || true
    aws eks associate-access-policy \
        --cluster-name "$PROJECT_NAME-$ENVIRONMENT-cluster" \
        --principal-arn "$FAILURE_SIM_LAMBDA_ROLE_ARN" \
        --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSEditPolicy \
        --access-scope type=namespace,namespaces=payment-demo,kube-system \
        --region "$AWS_REGION" >/dev/null 2>&1 || true
    echo "  Failure Simulator Lambda EKS access granted (namespaces: payment-demo, kube-system)."
fi
echo ""

# Wait for EKS API authentication to propagate (access entries are eventually consistent)
echo "  Waiting for EKS API access to propagate..."
for i in $(seq 1 30); do
    if kubectl get ns default >/dev/null 2>&1; then
        echo "  EKS API access confirmed."
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "  WARNING: EKS API access not confirmed after 150 seconds. Continuing anyway..."
    fi
    sleep 5
done
echo ""

# Wait for nodes to be ready
echo "  Waiting for EKS nodes to be Ready..."
for i in $(seq 1 60); do
    READY_NODES=$(kubectl get nodes --no-headers 2>/dev/null | grep -c " Ready" || true)
    if [ "$READY_NODES" -ge 1 ]; then
        echo "  $READY_NODES node(s) ready."
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "  WARNING: Nodes not ready after 5 minutes. Continuing anyway..."
    fi
    sleep 5
done
echo ""

# =============================================================================
# STEP 4: Build and push container images via CodeBuild
# =============================================================================
echo "[4/9] Building and pushing container images via CodeBuild..."

IMAGE_TAG="$ENVIRONMENT"
SERVICES=("merchant-gateway" "payment-processor" "webhook-service")

# --- 5a: Zip each service source directory and upload to S3 ---
echo "  Packaging service source bundles..."
for SERVICE in "${SERVICES[@]}"; do
    echo "    Zipping $SERVICE..."
    TMPZIP="/tmp/${SERVICE}.zip"
    rm -f "$TMPZIP"
    (cd "services/$SERVICE" && zip -r "$TMPZIP" . \
        -x "node_modules/*" "dist/*" "target/*" ".git/*" \
           "*.test.*" "jest.config.*" ".jqwik-database" >/dev/null)
    echo "    Uploading $SERVICE.zip to S3..."
    aws s3 cp "$TMPZIP" "s3://$BUCKET_NAME/codebuild-sources/${SERVICE}.zip" \
        --region "$AWS_REGION" >/dev/null
    rm -f "$TMPZIP"
done
echo "  Source bundles uploaded."

# --- 5b: Get CodeBuild project names from CloudFormation outputs ---
# NOTE: Uses indexed arrays (not associative) for Bash 3.2 (macOS) compatibility.
# Index mapping: 0=merchant-gateway, 1=payment-processor, 2=webhook-service
echo "  Retrieving CodeBuild project names..."
CB_PROJECTS=()
CB_PROJECTS[0]=$(aws cloudformation describe-stacks \
    --stack-name "DevOpsAgentEksPipeline-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='MerchantGatewayBuildProject'].OutputValue" \
    --output text --region "$AWS_REGION")
CB_PROJECTS[1]=$(aws cloudformation describe-stacks \
    --stack-name "DevOpsAgentEksPipeline-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='PaymentProcessorBuildProject'].OutputValue" \
    --output text --region "$AWS_REGION")
CB_PROJECTS[2]=$(aws cloudformation describe-stacks \
    --stack-name "DevOpsAgentEksPipeline-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='WebhookServiceBuildProject'].OutputValue" \
    --output text --region "$AWS_REGION")

for i in 0 1 2; do
    if [ -z "${CB_PROJECTS[$i]}" ] || [ "${CB_PROJECTS[$i]}" = "None" ]; then
        echo "  ERROR: Could not find CodeBuild project name for ${SERVICES[$i]}."
        echo "  Make sure the CloudFormation stack deployed successfully."
        exit 1
    fi
    echo "    ${SERVICES[$i]} → ${CB_PROJECTS[$i]}"
done

# --- 5c: Start CodeBuild builds ---
echo "  Starting CodeBuild builds..."
BUILD_IDS=()
for i in 0 1 2; do
    # payment-processor is a Java/Maven project — its own buildspec.yml runs
    # 'mvn clean package' before 'docker build'. Override the inline buildspec
    # so CodeBuild uses the buildspec.yml from the S3 source zip.
    BUILDSPEC_ARGS=""
    if [ "${SERVICES[$i]}" = "payment-processor" ]; then
        BUILDSPEC_ARGS="--buildspec-override buildspec.yml"
    fi

    BUILD_ID=$(aws codebuild start-build \
        --project-name "${CB_PROJECTS[$i]}" \
        --source-type-override S3 \
        --source-location-override "$BUCKET_NAME/codebuild-sources/${SERVICES[$i]}.zip" \
        $BUILDSPEC_ARGS \
        --environment-variables-override "name=IMAGE_TAG,value=$IMAGE_TAG,type=PLAINTEXT" \
        --query 'build.id' --output text \
        --region "$AWS_REGION")
    BUILD_IDS[$i]="$BUILD_ID"
    echo "    Started ${SERVICES[$i]}: $BUILD_ID"
done

# --- 5d: Poll builds until all complete ---
echo ""
echo "  Waiting for builds to complete..."
BUILD_STATUS=()
BUILD_PHASE=()
BUILD_DONE=()
START_TIME=$(date +%s)

for i in 0 1 2; do
    BUILD_DONE[$i]="false"
    BUILD_STATUS[$i]="IN_PROGRESS"
    BUILD_PHASE[$i]="SUBMITTED"
done

while true; do
    ALL_DONE="true"

    # Collect pending build IDs for batch query
    PENDING_IDS=""
    for i in 0 1 2; do
        if [ "${BUILD_DONE[$i]}" = "false" ]; then
            ALL_DONE="false"
            if [ -n "$PENDING_IDS" ]; then
                PENDING_IDS="$PENDING_IDS ${BUILD_IDS[$i]}"
            else
                PENDING_IDS="${BUILD_IDS[$i]}"
            fi
        fi
    done

    if [ "$ALL_DONE" = "true" ]; then
        break
    fi

    # Batch query — returns tab-separated rows: buildId \t buildStatus \t currentPhase
    BATCH_RESULT=$(aws codebuild batch-get-builds \
        --ids $PENDING_IDS \
        --query 'builds[].[id,buildStatus,currentPhase]' \
        --output text --region "$AWS_REGION")

    # Parse each row
    while IFS=$'\t' read -r BID BSTATUS BPHASE; do
        for i in 0 1 2; do
            if [ "${BUILD_IDS[$i]}" = "$BID" ]; then
                BUILD_PHASE[$i]="$BPHASE"
                if [ "$BSTATUS" = "SUCCEEDED" ] || [ "$BSTATUS" = "FAILED" ] || [ "$BSTATUS" = "FAULT" ] || [ "$BSTATUS" = "TIMED_OUT" ] || [ "$BSTATUS" = "STOPPED" ] || [ "$BSTATUS" = "COMPLETED" ]; then
                    BUILD_DONE[$i]="true"
                    BUILD_STATUS[$i]="$BSTATUS"
                fi
                break
            fi
        done
    done <<< "$BATCH_RESULT"

    # Print status line
    ELAPSED=$(( $(date +%s) - START_TIME ))
    ELAPSED_MIN=$(( ELAPSED / 60 ))
    ELAPSED_SEC=$(( ELAPSED % 60 ))
    STATUS_LINE="    [${ELAPSED_MIN}m${ELAPSED_SEC}s]"
    for i in 0 1 2; do
        if [ "${BUILD_DONE[$i]}" = "true" ]; then
            STATUS_LINE="$STATUS_LINE  ${SERVICES[$i]}:${BUILD_STATUS[$i]}"
        else
            STATUS_LINE="$STATUS_LINE  ${SERVICES[$i]}:${BUILD_PHASE[$i]}"
        fi
    done
    echo "$STATUS_LINE"

    # Re-check if all done after this iteration
    ALL_DONE="true"
    for i in 0 1 2; do
        if [ "${BUILD_DONE[$i]}" = "false" ]; then
            ALL_DONE="false"
            break
        fi
    done
    if [ "$ALL_DONE" = "true" ]; then
        break
    fi

    sleep 15
done

# --- 5e: Report results ---
echo ""
FAILED="false"
for i in 0 1 2; do
    STATUS="${BUILD_STATUS[$i]}"
    BID="${BUILD_IDS[$i]}"
    PROJECT="${CB_PROJECTS[$i]}"

    if [ "$STATUS" = "SUCCEEDED" ]; then
        ECR_URI="$ECR_REGISTRY/$PROJECT_NAME/${SERVICES[$i]}"
        echo "  ✅ ${SERVICES[$i]}: SUCCEEDED — image $ECR_URI:$IMAGE_TAG"
    else
        FAILED="true"
        LOG_GROUP="/aws/codebuild/$PROJECT"
        ENCODED_LOG_GROUP=$(echo "$LOG_GROUP" | sed 's|/|$252F|g')
        LOGS_URL="https://${AWS_REGION}.console.aws.amazon.com/cloudwatch/home?region=${AWS_REGION}#logsV2:log-groups/log-group/${ENCODED_LOG_GROUP}"
        echo "  ❌ ${SERVICES[$i]}: $STATUS"
        echo "     Build ID: $BID"
        echo "     Logs: $LOGS_URL"
    fi
done

if [ "$FAILED" = "true" ]; then
    echo ""
    echo "  ERROR: One or more CodeBuild builds failed. See logs above."
    exit 1
fi

echo "  All container images built and pushed successfully."
echo ""

# =============================================================================
# STEP 5: Apply Kubernetes manifests (with dynamic substitution)
# =============================================================================
echo "[5/9] Applying Kubernetes manifests..."

# Create namespace
kubectl create namespace payment-demo --dry-run=client -o yaml | kubectl apply -f -

# Get RDS endpoint
RDS_ENDPOINT=$(aws rds describe-db-instances \
    --db-instance-identifier "$PROJECT_NAME-$ENVIRONMENT-postgres" \
    --query 'DBInstances[0].Endpoint.Address' \
    --output text \
    --region "$AWS_REGION")
echo "  RDS endpoint: $RDS_ENDPOINT"

# Get DB password from Secrets Manager (no python3 dependency)
SECRET_JSON=$(aws secretsmanager get-secret-value \
    --secret-id "$PROJECT_NAME-$ENVIRONMENT-rds-credentials" \
    --query SecretString \
    --output text \
    --region "$AWS_REGION")
if command -v jq &>/dev/null; then
    DB_PASSWORD=$(echo "$SECRET_JSON" | jq -r '.password')
else
    DB_PASSWORD=$(echo "$SECRET_JSON" | grep -o '"password":"[^"]*"' | cut -d'"' -f4)
fi

# Create K8s secret for DB credentials
kubectl create secret generic db-credentials \
    --from-literal=DB_HOST="$RDS_ENDPOINT" \
    --from-literal=DB_USERNAME=paymentadmin \
    --from-literal=DB_PASSWORD="$DB_PASSWORD" \
    --from-literal=DB_NAME=paymentdb \
    -n payment-demo --dry-run=client -o yaml | kubectl apply -f -

# Substitute placeholders in configmap
echo "  Patching configmap placeholders..."

# Fetch Cognito values from CloudFormation outputs
COGNITO_USER_POOL_ID=$(aws cloudformation describe-stacks \
    --stack-name "DevOpsAgentEksAuth-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" \
    --output text --region "$AWS_REGION" 2>/dev/null || echo "")
COGNITO_CLIENT_ID=$(aws cloudformation describe-stacks \
    --stack-name "DevOpsAgentEksAuth-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" \
    --output text --region "$AWS_REGION" 2>/dev/null || echo "")

sed -i.bak \
    -e "s|__AWS_REGION__|$AWS_REGION|g" \
    -e "s|__RDS_ENDPOINT__|$RDS_ENDPOINT|g" \
    -e "s|__COGNITO_USER_POOL_ID__|$COGNITO_USER_POOL_ID|g" \
    -e "s|__COGNITO_CLIENT_ID__|$COGNITO_CLIENT_ID|g" \
    k8s/base/configmap.yaml
rm -f k8s/base/configmap.yaml.bak

# Substitute placeholders in kustomization overlay
echo "  Patching kustomization overlay placeholders..."
sed -i.bak \
    -e "s|__ACCOUNT_ID__|$AWS_ACCOUNT_ID|g" \
    -e "s|__AWS_REGION__|$AWS_REGION|g" \
    k8s/overlays/$ENVIRONMENT/kustomization.yaml
rm -f k8s/overlays/$ENVIRONMENT/kustomization.yaml.bak

# Apply kustomize
kubectl apply -k "k8s/overlays/$ENVIRONMENT"

# Restore placeholders so git stays clean
# IMPORTANT: Replace longer/more-specific values BEFORE shorter substrings
# (e.g. COGNITO_USER_POOL_ID contains AWS_REGION as a prefix, so it must be
# restored first to avoid partial replacement corruption).
sed -i.bak \
    -e "s|$COGNITO_USER_POOL_ID|__COGNITO_USER_POOL_ID__|g" \
    -e "s|$COGNITO_CLIENT_ID|__COGNITO_CLIENT_ID__|g" \
    -e "s|$RDS_ENDPOINT|__RDS_ENDPOINT__|g" \
    -e "s|$AWS_REGION|__AWS_REGION__|g" \
    k8s/base/configmap.yaml
rm -f k8s/base/configmap.yaml.bak

sed -i.bak \
    -e "s|$AWS_ACCOUNT_ID|__ACCOUNT_ID__|g" \
    -e "s|$AWS_REGION|__AWS_REGION__|g" \
    k8s/overlays/$ENVIRONMENT/kustomization.yaml
rm -f k8s/overlays/$ENVIRONMENT/kustomization.yaml.bak

echo "  Kubernetes manifests applied."
echo ""

# Deploy Fluent Bit (log shipping to CloudWatch)
echo "  Deploying Fluent Bit DaemonSet..."
FLUENT_BIT_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${PROJECT_NAME}-${ENVIRONMENT}-fluent-bit-role"

# Substitute placeholders in Fluent Bit manifests
sed -i.bak \
    -e "s|__ACCOUNT_ID__|$AWS_ACCOUNT_ID|g" \
    -e "s|__AWS_REGION__|$AWS_REGION|g" \
    -e "s|__ENVIRONMENT__|$ENVIRONMENT|g" \
    k8s/base/fluent-bit/service-account.yaml \
    k8s/base/fluent-bit/configmap.yaml
rm -f k8s/base/fluent-bit/service-account.yaml.bak k8s/base/fluent-bit/configmap.yaml.bak

kubectl apply -f k8s/base/fluent-bit/

# Restore placeholders so git stays clean
sed -i.bak \
    -e "s|$AWS_ACCOUNT_ID|__ACCOUNT_ID__|g" \
    -e "s|$AWS_REGION|__AWS_REGION__|g" \
    -e "s|$ENVIRONMENT|__ENVIRONMENT__|g" \
    k8s/base/fluent-bit/service-account.yaml \
    k8s/base/fluent-bit/configmap.yaml
rm -f k8s/base/fluent-bit/service-account.yaml.bak k8s/base/fluent-bit/configmap.yaml.bak

echo "  Fluent Bit deployed."
echo ""

# Restart deployments to pick up latest images
echo "  Restarting deployments to pull latest images..."
kubectl rollout restart deployment/merchant-gateway -n payment-demo
kubectl rollout restart deployment/payment-processor -n payment-demo
kubectl rollout restart deployment/webhook-service -n payment-demo
echo ""

# =============================================================================
# STEP 6: Create Cognito user & run DB migrations (BEFORE pod wait)
# =============================================================================
# Running migrations before waiting for pods ensures payment-processor finds
# the transactions table on startup, preventing CrashLoopBackOff.
echo "[6/9] Creating Cognito user and running database migrations..."

# --- 7a: Create Cognito demo user FIRST so we can capture its sub UUID ---
USER_POOL_ID=$(aws cloudformation describe-stacks \
    --stack-name "DevOpsAgentEksAuth-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" \
    --output text \
    --region "$AWS_REGION")

COGNITO_SUB=""
if [ -n "$USER_POOL_ID" ] && [ "$USER_POOL_ID" != "None" ]; then
    DEMO_USERNAME="demo-merchant-1"
    DEMO_EMAIL="demo@helios-electronics.com"
    DEMO_PASSWORD="DemoPass2026!"
    # MERCHANT_ID will be set to COGNITO_SUB after we retrieve it.
    # The payment-processor uses the JWT sub directly as merchant_id FK,
    # so the merchants.id column MUST equal the Cognito sub UUID.

    # Check if user exists; create if not
    if aws cognito-idp admin-get-user \
        --user-pool-id "$USER_POOL_ID" \
        --username "$DEMO_USERNAME" \
        --region "$AWS_REGION" >/dev/null 2>&1; then
        echo "  Cognito user '$DEMO_USERNAME' already exists."
        # Ensure password is set correctly (handles redeployments)
        aws cognito-idp admin-set-user-password \
            --user-pool-id "$USER_POOL_ID" \
            --username "$DEMO_USERNAME" \
            --password "$DEMO_PASSWORD" \
            --permanent \
            --region "$AWS_REGION" >/dev/null 2>&1 || true
    else
        echo "  Creating Cognito user '$DEMO_USERNAME'..."
        aws cognito-idp admin-create-user \
            --user-pool-id "$USER_POOL_ID" \
            --username "$DEMO_USERNAME" \
            --user-attributes \
                Name=email,Value="$DEMO_EMAIL" \
                Name=email_verified,Value=true \
            --temporary-password "$DEMO_PASSWORD" \
            --message-action SUPPRESS \
            --region "$AWS_REGION" >/dev/null

        aws cognito-idp admin-set-user-password \
            --user-pool-id "$USER_POOL_ID" \
            --username "$DEMO_USERNAME" \
            --password "$DEMO_PASSWORD" \
            --permanent \
            --region "$AWS_REGION" >/dev/null
        echo "  Cognito user created."
    fi

    # Capture the Cognito sub UUID (this is what appears in JWT access tokens)
    COGNITO_SUB=$(aws cognito-idp admin-get-user \
        --user-pool-id "$USER_POOL_ID" \
        --username "$DEMO_USERNAME" \
        --query "UserAttributes[?Name=='sub'].Value" \
        --output text \
        --region "$AWS_REGION")
    echo "  Cognito sub: $COGNITO_SUB"
else
    echo "  WARNING: Could not find Cognito User Pool ID."
fi

# Fallback if we couldn't get the sub
if [ -z "$COGNITO_SUB" ]; then
    COGNITO_SUB="demo-merchant-1"
    echo "  WARNING: Using username as cognito_sub fallback."
fi

# The payment-processor uses the JWT sub directly as merchant_id,
# so merchants.id MUST equal the Cognito sub UUID.
MERCHANT_ID="$COGNITO_SUB"

# Update the custom:merchant_id attribute now that we have the real sub
if [ -n "$USER_POOL_ID" ] && [ "$USER_POOL_ID" != "None" ]; then
    aws cognito-idp admin-update-user-attributes \
        --user-pool-id "$USER_POOL_ID" \
        --username "$DEMO_USERNAME" \
        --user-attributes Name=custom:merchant_id,Value="$MERCHANT_ID" \
        --region "$AWS_REGION" >/dev/null 2>&1 || true
    echo "  Updated custom:merchant_id to $MERCHANT_ID"
fi

# --- 7b: Run database migrations and seed with the real Cognito sub ---

# Clean up any previous seed job
kubectl delete job db-seed-job -n payment-demo --ignore-not-found 2>/dev/null

echo "  Creating DB seed job..."
kubectl apply -f - <<SEEDJOB
apiVersion: batch/v1
kind: Job
metadata:
  name: db-seed-job
  namespace: payment-demo
spec:
  backoffLimit: 1
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: db-seed
          image: postgres:16-alpine
          env:
            - name: PGHOST
              valueFrom:
                secretKeyRef:
                  name: db-credentials
                  key: DB_HOST
            - name: PGUSER
              valueFrom:
                secretKeyRef:
                  name: db-credentials
                  key: DB_USERNAME
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: db-credentials
                  key: DB_PASSWORD
            - name: PGDATABASE
              valueFrom:
                secretKeyRef:
                  name: db-credentials
                  key: DB_NAME
          command: ["/bin/sh", "-c"]
          args:
            - |
              # Phase 1: Create schema (no variable expansion needed)
              psql <<'EOSQL'
              -- ============================================================
              -- 001: Create merchants table
              -- ============================================================
              CREATE TABLE IF NOT EXISTS merchants (
                  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                  cognito_sub VARCHAR(255) UNIQUE NOT NULL,
                  name VARCHAR(255) NOT NULL,
                  email VARCHAR(255) NOT NULL,
                  webhook_url VARCHAR(500),
                  webhook_secret VARCHAR(255),
                  rate_limit INTEGER DEFAULT 100,
                  status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
                  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                  CONSTRAINT valid_merchant_status CHECK (status IN ('ACTIVE', 'SUSPENDED', 'CLOSED'))
              );

              CREATE UNIQUE INDEX IF NOT EXISTS idx_merchants_email ON merchants(email);
              CREATE INDEX IF NOT EXISTS idx_merchants_cognito_sub ON merchants(cognito_sub);
              CREATE INDEX IF NOT EXISTS idx_merchants_status ON merchants(status);
              CREATE INDEX IF NOT EXISTS idx_merchants_created_at ON merchants(created_at);

              CREATE OR REPLACE FUNCTION update_updated_at_column()
              RETURNS TRIGGER AS $func$
              BEGIN
                  NEW.updated_at = NOW();
                  RETURN NEW;
              END;
              $func$ language 'plpgsql';

              DROP TRIGGER IF EXISTS update_merchants_updated_at ON merchants;
              CREATE TRIGGER update_merchants_updated_at
                  BEFORE UPDATE ON merchants
                  FOR EACH ROW
                  EXECUTE FUNCTION update_updated_at_column();

              -- ============================================================
              -- 002: Create transactions table
              -- ============================================================
              CREATE TABLE IF NOT EXISTS transactions (
                  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                  merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
                  amount DECIMAL(12,2) NOT NULL,
                  currency VARCHAR(3) NOT NULL DEFAULT 'EUR',
                  status VARCHAR(20) NOT NULL,
                  payment_method VARCHAR(50),
                  payment_method_token VARCHAR(255),
                  card_brand VARCHAR(50),
                  card_last_four VARCHAR(4),
                  description TEXT,
                  idempotency_key VARCHAR(255),
                  correlation_id VARCHAR(255),
                  authorization_code VARCHAR(50),
                  capture_id VARCHAR(50),
                  refund_id VARCHAR(50),
                  error_code VARCHAR(50),
                  error_message TEXT,
                  metadata JSONB,
                  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                  CONSTRAINT valid_status CHECK (status IN ('CREATED', 'PENDING', 'AUTHORIZED', 'CAPTURED', 'REFUNDED', 'CANCELED', 'CANCELLED', 'FAILED')),
                  CONSTRAINT positive_amount CHECK (amount > 0),
                  CONSTRAINT valid_currency CHECK (currency ~ '^[A-Z]{3}')
              );

              CREATE INDEX IF NOT EXISTS idx_transactions_merchant_id ON transactions(merchant_id);
              CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status);
              CREATE INDEX IF NOT EXISTS idx_transactions_created_at ON transactions(created_at);
              CREATE INDEX IF NOT EXISTS idx_transactions_merchant_status ON transactions(merchant_id, status);
              CREATE INDEX IF NOT EXISTS idx_transactions_merchant_created ON transactions(merchant_id, created_at DESC);
              CREATE INDEX IF NOT EXISTS idx_transactions_amount ON transactions(amount);
              CREATE INDEX IF NOT EXISTS idx_transactions_currency ON transactions(currency);

              CREATE INDEX IF NOT EXISTS idx_transactions_active ON transactions(merchant_id, created_at DESC)
                  WHERE status IN ('CREATED', 'AUTHORIZED');

              CREATE INDEX IF NOT EXISTS idx_transactions_metadata ON transactions USING GIN (metadata);

              DROP TRIGGER IF EXISTS update_transactions_updated_at ON transactions;
              CREATE TRIGGER update_transactions_updated_at
                  BEFORE UPDATE ON transactions
                  FOR EACH ROW
                  EXECUTE FUNCTION update_updated_at_column();

              -- ============================================================
              -- 003: Create webhook_deliveries table
              -- ============================================================
              CREATE TABLE IF NOT EXISTS webhook_deliveries (
                  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                  transaction_id UUID NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
                  merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
                  event_type VARCHAR(50) NOT NULL,
                  payload JSONB NOT NULL,
                  attempt_count INTEGER DEFAULT 0,
                  last_attempt_at TIMESTAMP WITH TIME ZONE,
                  next_attempt_at TIMESTAMP WITH TIME ZONE,
                  status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                  response_code INTEGER,
                  response_body TEXT,
                  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                  CONSTRAINT valid_delivery_status CHECK (status IN ('PENDING', 'DELIVERED', 'FAILED')),
                  CONSTRAINT valid_attempt_count CHECK (attempt_count >= 0 AND attempt_count <= 5),
                  CONSTRAINT valid_event_type CHECK (event_type IN (
                      'payment.created',
                      'payment.authorized',
                      'payment.captured',
                      'payment.refunded',
                      'payment.canceled',
                      'payment.failed'
                  ))
              );

              CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_status ON webhook_deliveries(status);
              CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_merchant_id ON webhook_deliveries(merchant_id);
              CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_transaction_id ON webhook_deliveries(transaction_id);
              CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_created_at ON webhook_deliveries(created_at);
              CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_event_type ON webhook_deliveries(event_type);

              CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_next_attempt
                  ON webhook_deliveries(next_attempt_at)
                  WHERE status = 'PENDING';

              CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_merchant_created
                  ON webhook_deliveries(merchant_id, created_at DESC);

              CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_payload ON webhook_deliveries USING GIN (payload);
              EOSQL

              # Phase 2: Seed demo data
              # Use MERCHANT_ID (always a valid UUID) for the id column,
              # and COGNITO_SUB (UUID or fallback string) for the cognito_sub column.
              psql -c "
              INSERT INTO merchants (id, cognito_sub, name, email, webhook_url, webhook_secret, rate_limit, status, created_at, updated_at)
              VALUES
              ('$MERCHANT_ID', '$COGNITO_SUB', 'Helios Electronics', 'demo@helios-electronics.com', 'https://webhook.helios-electronics.com/payments', 'demo_webhook_secret_helios_2026', 100, 'ACTIVE', NOW() - INTERVAL '90 days', NOW() - INTERVAL '90 days'),
              ('22222222-2222-2222-2222-222222222222', 'demo-merchant-2', 'TechStore Global', 'payments@techstore-global.com', 'https://api.techstore-global.com/webhooks/payment', 'demo_webhook_secret_techstore_2026', 200, 'ACTIVE', NOW() - INTERVAL '60 days', NOW() - INTERVAL '60 days'),
              ('33333333-3333-3333-3333-333333333333', 'demo-merchant-3', 'Fashion Boutique', 'admin@fashion-boutique.com', NULL, 'demo_webhook_secret_fashion_2026', 50, 'SUSPENDED', NOW() - INTERVAL '30 days', NOW() - INTERVAL '5 days')
              ON CONFLICT (id) DO UPDATE SET cognito_sub = EXCLUDED.cognito_sub;

              INSERT INTO transactions (id, merchant_id, amount, currency, status, payment_method_token, authorization_code, capture_id, metadata, created_at, updated_at)
              VALUES
              ('a1111111-1111-1111-1111-111111111111', '$MERCHANT_ID', 1299.99, 'EUR', 'CAPTURED', 'pm_tok_demo_visa_4242', 'AUTH_20260112_001', 'CAP_20260112_001', '{\"product\": \"Laptop Pro 15\", \"customer_id\": \"CUST_001\", \"order_id\": \"ORD_2026_001\"}'::jsonb, NOW() - INTERVAL '2 hours', NOW() - INTERVAL '2 hours'),
              ('a2222222-2222-2222-2222-222222222222', '$MERCHANT_ID', 899.00, 'EUR', 'CAPTURED', 'pm_tok_demo_mastercard_5555', 'AUTH_20260112_002', 'CAP_20260112_002', '{\"product\": \"Smartphone X12\", \"customer_id\": \"CUST_002\", \"order_id\": \"ORD_2026_002\"}'::jsonb, NOW() - INTERVAL '5 hours', NOW() - INTERVAL '5 hours'),
              ('a3333333-3333-3333-3333-333333333333', '$MERCHANT_ID', 549.99, 'EUR', 'CAPTURED', 'pm_tok_demo_amex_3782', 'AUTH_20260111_003', 'CAP_20260111_003', '{\"product\": \"Tablet Pro 11\", \"customer_id\": \"CUST_003\", \"order_id\": \"ORD_2026_003\"}'::jsonb, NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day'),
              ('a4444444-4444-4444-4444-444444444444', '$MERCHANT_ID', 199.99, 'EUR', 'AUTHORIZED', 'pm_tok_demo_visa_4111', 'AUTH_20260112_004', NULL, '{\"product\": \"Wireless Headphones Pro\", \"customer_id\": \"CUST_004\", \"order_id\": \"ORD_2026_004\"}'::jsonb, NOW() - INTERVAL '30 minutes', NOW() - INTERVAL '30 minutes'),
              ('a5555555-5555-5555-5555-555555555555', '$MERCHANT_ID', 399.00, 'EUR', 'REFUNDED', 'pm_tok_demo_visa_4242', 'AUTH_20260110_005', 'CAP_20260110_005', '{\"product\": \"Smartwatch Elite\", \"customer_id\": \"CUST_005\", \"order_id\": \"ORD_2026_005\", \"refund_reason\": \"Customer requested\"}'::jsonb, NOW() - INTERVAL '2 days', NOW() - INTERVAL '1 day'),
              ('a6666666-6666-6666-6666-666666666666', '$MERCHANT_ID', 2499.99, 'EUR', 'FAILED', 'pm_tok_demo_visa_4000', NULL, NULL, '{\"product\": \"Gaming Laptop Ultra\", \"customer_id\": \"CUST_006\", \"order_id\": \"ORD_2026_006\"}'::jsonb, NOW() - INTERVAL '3 hours', NOW() - INTERVAL '3 hours')
              ON CONFLICT (id) DO NOTHING;

              INSERT INTO transactions (id, merchant_id, amount, currency, status, payment_method_token, authorization_code, capture_id, metadata, created_at, updated_at)
              VALUES
              ('b1111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222', 449.99, 'EUR', 'CAPTURED', 'pm_tok_demo_visa_4242', 'AUTH_20260111_007', 'CAP_20260111_007', '{\"product\": \"4K Monitor 32inch\", \"customer_id\": \"TECH_CUST_001\", \"order_id\": \"TECH_ORD_001\"}'::jsonb, NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day'),
              ('b2222222-2222-2222-2222-222222222222', '22222222-2222-2222-2222-222222222222', 129.99, 'EUR', 'CAPTURED', 'pm_tok_demo_mastercard_5555', 'AUTH_20260112_008', 'CAP_20260112_008', '{\"product\": \"Mechanical Keyboard + Mouse\", \"customer_id\": \"TECH_CUST_002\", \"order_id\": \"TECH_ORD_002\"}'::jsonb, NOW() - INTERVAL '6 hours', NOW() - INTERVAL '6 hours'),
              ('b3333333-3333-3333-3333-333333333333', '22222222-2222-2222-2222-222222222222', 179.99, 'EUR', 'CAPTURED', 'pm_tok_demo_visa_4111', 'AUTH_20260110_009', 'CAP_20260110_009', '{\"product\": \"External SSD 2TB\", \"customer_id\": \"TECH_CUST_003\", \"order_id\": \"TECH_ORD_003\"}'::jsonb, NOW() - INTERVAL '2 days', NOW() - INTERVAL '2 days')
              ON CONFLICT (id) DO NOTHING;

              INSERT INTO webhook_deliveries (id, transaction_id, merchant_id, event_type, payload, status, attempt_count, last_attempt_at, next_attempt_at, created_at)
              VALUES
              ('d1111111-1111-1111-1111-111111111111', 'a1111111-1111-1111-1111-111111111111', '$MERCHANT_ID', 'payment.captured', '{\"event\": \"payment.captured\", \"transaction_id\": \"a1111111-1111-1111-1111-111111111111\", \"amount\": 1299.99, \"currency\": \"EUR\"}'::jsonb, 'DELIVERED', 1, NOW() - INTERVAL '2 hours', NULL, NOW() - INTERVAL '2 hours'),
              ('d2222222-2222-2222-2222-222222222222', 'a4444444-4444-4444-4444-444444444444', '$MERCHANT_ID', 'payment.authorized', '{\"event\": \"payment.authorized\", \"transaction_id\": \"a4444444-4444-4444-4444-444444444444\", \"amount\": 199.99, \"currency\": \"EUR\"}'::jsonb, 'PENDING', 0, NULL, NOW() + INTERVAL '5 minutes', NOW() - INTERVAL '30 minutes')
              ON CONFLICT (id) DO NOTHING;
              "
SEEDJOB

echo "  Waiting for DB seed job to complete (up to 120s)..."
if ! kubectl wait --for=condition=complete job/db-seed-job -n payment-demo --timeout=120s 2>/dev/null; then
    echo "  ERROR: DB seed job failed. Pod logs:"
    kubectl logs job/db-seed-job -n payment-demo 2>/dev/null || true
    exit 1
fi
echo "  Database migrations and seed data applied successfully."
echo ""

# =============================================================================
# STEP 7: Wait for pods and NLB
# =============================================================================
echo "[7/9] Waiting for pods to be ready..."
for DEPLOY in merchant-gateway payment-processor webhook-service; do
    echo "  Waiting for $DEPLOY..."
    kubectl rollout status deployment/$DEPLOY -n payment-demo --timeout=300s || true
done
echo ""

echo "  Waiting for NLB to get external hostname..."
NLB_HOSTNAME=""
for i in $(seq 1 60); do
    NLB_HOSTNAME=$(kubectl get svc merchant-gateway-nlb -n payment-demo \
        -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)
    if [ -n "$NLB_HOSTNAME" ]; then
        echo "  NLB hostname: $NLB_HOSTNAME"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "  WARNING: NLB hostname not available after 5 minutes."
    fi
    sleep 5
done
echo ""

# =============================================================================
# STEP 8: Update CloudFront with NLB API origin via CDK
# =============================================================================
echo "[8/9] Updating CloudFront with API origin..."
if [ -n "$NLB_HOSTNAME" ]; then
    # Admin API is wired to CloudFront via cross-stack reference (no extra context needed)
    cd cdk
    npx cdk deploy DevOpsAgentEksFrontend-$AWS_REGION \
        -c environment=$ENVIRONMENT \
        -c projectName=$PROJECT_NAME \
        -c eksNodeArchitecture=$EKS_ARCHITECTURE \
        -c eksNodeInstanceType=$EKS_INSTANCE_TYPE \
        -c eksNodeDesiredCapacity=2 \
        -c apiGatewayEndpoint=$NLB_HOSTNAME \
        -c devOpsAgentWebhookUrl=$DEVOPS_WEBHOOK_URL \
        -c devOpsAgentWebhookSecret=$DEVOPS_WEBHOOK_SECRET \
        -c devOpsAgentRegion=${DEVOPS_AGENT_REGION:-us-east-1} \
        --require-approval never \
        --no-cli-pager
    cd ..
    echo "  CloudFront updated with API origin."
else
    echo "  WARNING: Skipping CloudFront API origin (NLB hostname not available)."
    echo "  You can re-run this script later to add the API origin."
fi
echo ""

# =============================================================================
# STEP 9: Build and deploy frontend
# =============================================================================
echo "[9/9] Building and deploying frontend..."

CLIENT_ID=$(aws cloudformation describe-stacks \
    --stack-name "DevOpsAgentEksAuth-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" \
    --output text \
    --region "$AWS_REGION")

CLOUDFRONT_DOMAIN=$(aws cloudformation describe-stacks \
    --stack-name "DevOpsAgentEksFrontend-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDomainName'].OutputValue" \
    --output text \
    --region "$AWS_REGION")

DISTRIBUTION_ID=$(aws cloudformation describe-stacks \
    --stack-name "DevOpsAgentEksFrontend-$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDistributionId'].OutputValue" \
    --output text \
    --region "$AWS_REGION")

# Generate frontend environment config
cat > services/merchant-portal/.env.production.local <<EOF
VITE_COGNITO_USER_POOL_ID=$USER_POOL_ID
VITE_COGNITO_CLIENT_ID=$CLIENT_ID
VITE_COGNITO_REGION=$AWS_REGION
VITE_API_BASE_URL=/api/v1
EOF

echo "  Installing frontend dependencies..."
cd services/merchant-portal
npm install --silent --legacy-peer-deps
if [ $? -ne 0 ]; then
    echo "  ERROR: Frontend npm install failed."
    exit 1
fi
echo "  Building frontend..."
npm run build
if [ $? -ne 0 ]; then
    echo "  ERROR: Frontend build failed."
    exit 1
fi
cd ../..

# Find S3 bucket
S3_BUCKET="$PROJECT_NAME-$ENVIRONMENT-merchant-portal-$AWS_ACCOUNT_ID"

echo "  Uploading to S3..."
aws s3 sync services/merchant-portal/dist/ "s3://$S3_BUCKET/" --delete --region "$AWS_REGION"

echo "  Invalidating CloudFront cache..."
aws cloudfront create-invalidation \
    --distribution-id "$DISTRIBUTION_ID" \
    --paths "/*" >/dev/null
echo "  Frontend deployed."
echo ""

# =============================================================================
# DEPLOYMENT SUMMARY
# =============================================================================
echo "=============================================="
echo " Deployment Complete"
echo "=============================================="
echo ""
echo "Portal URL:     https://$CLOUDFRONT_DOMAIN"
echo "API Endpoint:   https://$CLOUDFRONT_DOMAIN/api/v1"
echo ""
echo "Demo Login:"
echo "  Username: demo-merchant-1"
echo "  Password: DemoPass2026!"
echo ""
echo "Useful commands:"
echo "  kubectl get pods -n payment-demo"
echo "  kubectl logs -f deployment/merchant-gateway -n payment-demo"
echo "  kubectl logs -f deployment/payment-processor -n payment-demo"
echo ""
echo "----------------------------------------------"
echo " Next Steps"
echo "----------------------------------------------"
echo ""
echo "1. Open the Portal URL above in your browser and log in with the demo credentials."
echo ""
echo "2. Test the payment flow:"
echo "   - Browse the product catalog"
echo "   - Add items to cart and complete a checkout"
echo "   - View transaction history on the dashboard"
echo ""
echo "3. Test the DevOps Agent incident investigation:"
echo "   a. Open the DevOps Agent Lab (🧪 icon in the portal)"
echo "   b. Click 'Inject' on a scenario to trigger a real infrastructure failure"
echo "   c. Wait ~2 minutes for the CloudWatch alarm to trigger"
echo "   d. Open the DevOps Agent console to see the automated investigation"
echo "   e. Click 'Rollback' in the Simulator when done (or wait for auto-revert)"
echo ""
echo "4. Clean up all resources when finished:"
echo "     bash scripts/cleanup.sh"
echo ""
