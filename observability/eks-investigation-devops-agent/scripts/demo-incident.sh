#!/bin/bash
# Demo Incident Script - Safe inject and rollback for DevOps Agent demo
# 
# This script safely injects and rolls back database connection failures
# for demonstrating Amazon DevOps Agent automated incident detection.

set -e

NAMESPACE="payment-demo"
DEPLOYMENT="payment-processor"
ALARM_NAME="devops-agent-eks-dev-database-connection-errors"
REGION=$(aws configure get region 2>/dev/null || echo "")
if [ -z "$REGION" ]; then
  REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
fi
if [ -z "$REGION" ]; then
  echo "ERROR: No AWS region configured. Set AWS_REGION or run 'aws configure set region <region>'"
  exit 1
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

case "$1" in
  inject)
    echo -e "${RED}🔴 Injecting database connection failure...${NC}"
    echo ""
    
    # Store current replica count for rollback
    REPLICAS=$(kubectl get deployment $DEPLOYMENT -n $NAMESPACE -o jsonpath='{.spec.replicas}')
    echo "Current replicas: $REPLICAS"
    
    # Step 1: Inject the wrong password into the deployment spec
    echo "Step 1: Setting wrong DB_PASSWORD..."
    kubectl set env deployment/$DEPLOYMENT DB_PASSWORD=wrong-password -n $NAMESPACE
    
    # Step 2: Scale down to kill the old healthy pod
    echo "Step 2: Scaling down to remove healthy pod..."
    kubectl scale deployment/$DEPLOYMENT -n $NAMESPACE --replicas=0
    sleep 3
    
    # Step 3: Scale back up — only the broken pod will start
    echo "Step 3: Scaling up with broken configuration..."
    kubectl scale deployment/$DEPLOYMENT -n $NAMESPACE --replicas=1
    
    echo ""
    echo -e "${GREEN}✅ Incident injected. Service is now DOWN — no healthy pods.${NC}"
    echo -e "${YELLOW}   The payment-processor will CrashLoopBackOff until rollback.${NC}"
    echo ""
    echo "Watch with: kubectl get pods -n $NAMESPACE -w"
    echo "Check logs: kubectl logs deployment/$DEPLOYMENT -n $NAMESPACE --tail=20"
    echo "Alarm:      bash scripts/demo-incident.sh status"
    ;;
    
  rollback)
    echo -e "${GREEN}🟢 Rolling back to correct configuration...${NC}"
    echo ""
    
    # Step 0: Restore EKS node group scaling (inject may have left nodes at 0)
    echo "Step 0: Restoring EKS node group to 2 nodes..."
    CLUSTER_NAME="${PROJECT_NAME:-devops-agent-eks}-${ENVIRONMENT:-dev}-cluster"
    NODEGROUP=$(aws eks list-nodegroups --cluster-name "$CLUSTER_NAME" --region "$REGION" --query 'nodegroups[0]' --output text 2>/dev/null || echo "")
    if [ -n "$NODEGROUP" ] && [ "$NODEGROUP" != "None" ]; then
      CURRENT_DESIRED=$(aws eks describe-nodegroup --cluster-name "$CLUSTER_NAME" --nodegroup-name "$NODEGROUP" --region "$REGION" --query 'nodegroup.scalingConfig.desiredSize' --output text 2>/dev/null || echo "0")
      if [ "$CURRENT_DESIRED" -lt 2 ] 2>/dev/null; then
        aws eks update-nodegroup-config --cluster-name "$CLUSTER_NAME" --nodegroup-name "$NODEGROUP" --scaling-config minSize=1,maxSize=10,desiredSize=2 --region "$REGION" >/dev/null 2>&1
        echo "  Scaled node group from $CURRENT_DESIRED to 2 desired nodes"
        echo "  Waiting for nodes to be ready (up to 3 min)..."
        for i in $(seq 1 18); do
          READY_NODES=$(kubectl get nodes --no-headers 2>/dev/null | grep -c " Ready " || echo "0")
          if [ "$READY_NODES" -ge 2 ]; then
            echo "  ✓ $READY_NODES nodes ready"
            break
          fi
          sleep 10
        done
      else
        echo "  Node group already at $CURRENT_DESIRED desired nodes"
      fi
    else
      echo "  ⚠ Could not find node group, skipping"
    fi

    # Step 1: Restore the correct secret reference
    echo "Step 1: Restoring database credentials from secret..."
    kubectl patch deployment $DEPLOYMENT -n $NAMESPACE --type='json' -p='[
      {"op": "replace", "path": "/spec/template/spec/containers/0/env/4", "value": {"name": "DB_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "db-credentials", "key": "DB_PASSWORD"}}}}
    ]'
    
    # Step 2: Wait for rollout to complete
    echo ""
    echo "Step 2: Waiting for new pod to be ready..."
    kubectl rollout status deployment/$DEPLOYMENT -n $NAMESPACE --timeout=120s
    
    # Step 3: Clean up any failed/error pods that might be lingering
    echo ""
    echo "Step 3: Cleaning up any failed pods..."
    FAILED_PODS=$(kubectl get pods -n $NAMESPACE -l app=$DEPLOYMENT --field-selector=status.phase!=Running -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || true)
    if [ -n "$FAILED_PODS" ]; then
      for pod in $FAILED_PODS; do
        echo "  Deleting failed pod: $pod"
        kubectl delete pod $pod -n $NAMESPACE --grace-period=0 --force 2>/dev/null || true
      done
    else
      echo "  No failed pods to clean up"
    fi
    
    # Step 4: Verify final state
    echo ""
    echo -e "${GREEN}✅ Rollback complete. Current pod status:${NC}"
    echo ""
    kubectl get pods -n $NAMESPACE
    ;;
    
  cleanup)
    echo -e "${YELLOW}🧹 Cleaning up all failed/error pods...${NC}"
    echo ""
    
    # Delete pods in Error, CrashLoopBackOff, or other non-Running states
    kubectl get pods -n $NAMESPACE -o json | \
      jq -r '.items[] | select(.status.phase != "Running") | .metadata.name' | \
      while read pod; do
        if [ -n "$pod" ]; then
          echo "Deleting pod: $pod"
          kubectl delete pod $pod -n $NAMESPACE --grace-period=0 --force 2>/dev/null || true
        fi
      done
    
    echo ""
    echo -e "${GREEN}✅ Cleanup complete. Current pod status:${NC}"
    kubectl get pods -n $NAMESPACE
    ;;
    
  status)
    echo -e "${YELLOW}📊 Current status:${NC}"
    echo ""
    echo "=== Pods ==="
    kubectl get pods -n $NAMESPACE
    echo ""
    echo "=== Deployment ==="
    kubectl get deployment $DEPLOYMENT -n $NAMESPACE
    echo ""
    echo "=== Alarm State ==="
    aws cloudwatch describe-alarms \
      --alarm-names $ALARM_NAME \
      --region $REGION \
      --query 'MetricAlarms[0].{State:StateValue,Reason:StateReason}' \
      --output table
    ;;
    
  reset)
    echo -e "${YELLOW}🔄 Full reset - restoring healthy state...${NC}"
    echo ""
    
    # Step 0: Restore EKS node group scaling
    echo "Step 0: Restoring EKS node group to 2 nodes..."
    CLUSTER_NAME="${PROJECT_NAME:-devops-agent-eks}-${ENVIRONMENT:-dev}-cluster"
    NODEGROUP=$(aws eks list-nodegroups --cluster-name "$CLUSTER_NAME" --region "$REGION" --query 'nodegroups[0]' --output text 2>/dev/null || echo "")
    if [ -n "$NODEGROUP" ] && [ "$NODEGROUP" != "None" ]; then
      CURRENT_DESIRED=$(aws eks describe-nodegroup --cluster-name "$CLUSTER_NAME" --nodegroup-name "$NODEGROUP" --region "$REGION" --query 'nodegroup.scalingConfig.desiredSize' --output text 2>/dev/null || echo "0")
      if [ "$CURRENT_DESIRED" -lt 2 ] 2>/dev/null; then
        aws eks update-nodegroup-config --cluster-name "$CLUSTER_NAME" --nodegroup-name "$NODEGROUP" --scaling-config minSize=1,maxSize=10,desiredSize=2 --region "$REGION" >/dev/null 2>&1
        echo "  Scaled node group from $CURRENT_DESIRED to 2 desired nodes"
        echo "  Waiting for nodes to be ready (up to 3 min)..."
        for i in $(seq 1 18); do
          READY_NODES=$(kubectl get nodes --no-headers 2>/dev/null | grep -c " Ready " || echo "0")
          if [ "$READY_NODES" -ge 2 ]; then
            echo "  ✓ $READY_NODES nodes ready"
            break
          fi
          sleep 10
        done
      else
        echo "  Node group already at $CURRENT_DESIRED desired nodes"
      fi
    else
      echo "  ⚠ Could not find node group, skipping"
    fi

    # Restore credentials
    echo "Step 1: Restoring database credentials..."
    kubectl patch deployment $DEPLOYMENT -n $NAMESPACE --type='json' -p='[
      {"op": "replace", "path": "/spec/template/spec/containers/0/env/4", "value": {"name": "DB_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "db-credentials", "key": "DB_PASSWORD"}}}}
    ]' 2>/dev/null || echo "  (credentials already correct)"
    
    # Force restart to ensure clean state
    echo ""
    echo "Step 2: Restarting deployment..."
    kubectl rollout restart deployment/$DEPLOYMENT -n $NAMESPACE
    
    # Wait for rollout
    echo ""
    echo "Step 3: Waiting for healthy pods..."
    kubectl rollout status deployment/$DEPLOYMENT -n $NAMESPACE --timeout=180s
    
    # Clean up any stragglers
    echo ""
    echo "Step 4: Cleaning up old pods..."
    sleep 5  # Give K8s time to terminate old pods
    kubectl get pods -n $NAMESPACE -o json | \
      jq -r '.items[] | select(.status.phase != "Running") | .metadata.name' | \
      while read pod; do
        if [ -n "$pod" ]; then
          kubectl delete pod $pod -n $NAMESPACE --grace-period=0 --force 2>/dev/null || true
        fi
      done
    
    echo ""
    echo -e "${GREEN}✅ Reset complete. Final status:${NC}"
    kubectl get pods -n $NAMESPACE
    ;;
    
  *)
    echo "Usage: $0 {inject|rollback|cleanup|status|reset}"
    echo ""
    echo "  inject   - Inject database connection failure (for demo)"
    echo "  rollback - Restore correct database credentials"
    echo "  cleanup  - Delete any failed/error pods"
    echo "  status   - Check pods and alarm state"
    echo "  reset    - Full reset to healthy state (use before demo)"
    echo ""
    echo "Demo workflow:"
    echo "  1. Before demo: ./demo-incident.sh reset"
    echo "  2. During demo: ./demo-incident.sh inject"
    echo "  3. After demo:  ./demo-incident.sh rollback"
    exit 1
    ;;
esac
