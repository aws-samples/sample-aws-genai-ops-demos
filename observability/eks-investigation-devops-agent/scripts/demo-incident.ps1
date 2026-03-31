# Demo Incident Script - Safe inject and rollback for DevOps Agent demo
#
# This script safely injects and rolls back database connection failures
# for demonstrating Amazon DevOps Agent automated incident detection.
#
# Usage: .\demo-incident.ps1 -Action <inject|rollback|cleanup|status|reset>

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("inject", "rollback", "cleanup", "status", "reset")]
    [string]$Action
)

$ErrorActionPreference = "Stop"

$Namespace = "payment-demo"
$Deployment = "payment-processor"
$AlarmName = "devops-agent-eks-dev-database-connection-errors"

# Region detection
$Region = aws configure get region 2>$null
if (-not $Region) { $Region = $env:AWS_REGION }
if (-not $Region) { $Region = $env:AWS_DEFAULT_REGION }
if (-not $Region) {
    Write-Host "ERROR: No AWS region configured. Set AWS_REGION or run 'aws configure set region <region>'" -ForegroundColor Red
    exit 1
}

switch ($Action) {
    "inject" {
        Write-Host "Injecting database connection failure..." -ForegroundColor Red
        Write-Host ""

        # Store current replica count
        $Replicas = kubectl get deployment $Deployment -n $Namespace -o jsonpath='{.spec.replicas}'
        Write-Host "Current replicas: $Replicas"

        # Step 1: Inject the wrong password into the deployment spec
        Write-Host "Step 1: Setting wrong DB_PASSWORD..."
        kubectl set env deployment/$Deployment DB_PASSWORD=wrong-password -n $Namespace

        # Step 2: Scale down to kill the old healthy pod
        Write-Host "Step 2: Scaling down to remove healthy pod..."
        kubectl scale deployment/$Deployment -n $Namespace --replicas=0
        Start-Sleep -Seconds 3

        # Step 3: Scale back up — only the broken pod will start
        Write-Host "Step 3: Scaling up with broken configuration..."
        kubectl scale deployment/$Deployment -n $Namespace --replicas=1

        Write-Host ""
        Write-Host "Incident injected. Service is now DOWN - no healthy pods." -ForegroundColor Green
        Write-Host "The payment-processor will CrashLoopBackOff until rollback." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Watch with: kubectl get pods -n $Namespace -w"
        Write-Host "Check logs: kubectl logs deployment/$Deployment -n $Namespace --tail=20"
        Write-Host "Alarm:      .\demo-incident.ps1 -Action status"
    }

    "rollback" {
        Write-Host "Rolling back to correct configuration..." -ForegroundColor Green
        Write-Host ""

        # Step 0: Restore EKS node group scaling (inject may have left nodes at 0)
        Write-Host "Step 0: Restoring EKS node group to 2 nodes..."
        $projName = if ($env:PROJECT_NAME) { $env:PROJECT_NAME } else { "devops-agent-eks" }
        $envName = if ($env:ENVIRONMENT) { $env:ENVIRONMENT } else { "dev" }
        $clusterName = "${projName}-${envName}-cluster"
        $nodegroup = aws eks list-nodegroups --cluster-name $clusterName --region $Region --query "nodegroups[0]" --output text 2>$null
        if ($nodegroup -and $nodegroup -ne "None") {
            $currentDesired = aws eks describe-nodegroup --cluster-name $clusterName --nodegroup-name $nodegroup --region $Region --query "nodegroup.scalingConfig.desiredSize" --output text 2>$null
            if ([int]$currentDesired -lt 2) {
                aws eks update-nodegroup-config --cluster-name $clusterName --nodegroup-name $nodegroup --scaling-config minSize=1,maxSize=10,desiredSize=2 --region $Region 2>$null | Out-Null
                Write-Host "  Scaled node group from $currentDesired to 2 desired nodes"
                Write-Host "  Waiting for nodes to be ready (up to 3 min)..."
                for ($i = 1; $i -le 18; $i++) {
                    $readyNodes = (kubectl get nodes --no-headers 2>$null | Select-String " Ready " | Measure-Object).Count
                    if ($readyNodes -ge 2) {
                        Write-Host "  ✓ $readyNodes nodes ready" -ForegroundColor Green
                        break
                    }
                    Start-Sleep -Seconds 10
                }
            } else {
                Write-Host "  Node group already at $currentDesired desired nodes"
            }
        } else {
            Write-Host "  ⚠ Could not find node group, skipping" -ForegroundColor Yellow
        }

        # Step 1: Restore the correct secret reference
        Write-Host "Step 1: Restoring database credentials from secret..."
        $patch = '[{"op": "replace", "path": "/spec/template/spec/containers/0/env/4", "value": {"name": "DB_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "db-credentials", "key": "DB_PASSWORD"}}}}]'
        $patchFile = [System.IO.Path]::GetTempFileName()
        Set-Content -Path $patchFile -Value $patch -Encoding UTF8
        kubectl patch deployment $Deployment -n $Namespace --type='json' --patch-file=$patchFile
        Remove-Item $patchFile -ErrorAction SilentlyContinue

        # Step 2: Wait for rollout to complete
        Write-Host ""
        Write-Host "Step 2: Waiting for new pod to be ready..."
        kubectl rollout status deployment/$Deployment -n $Namespace --timeout=120s

        # Step 3: Clean up any failed/error pods
        Write-Host ""
        Write-Host "Step 3: Cleaning up any failed pods..."
        $failedPods = kubectl get pods -n $Namespace -l app=$Deployment --field-selector=status.phase!=Running -o jsonpath='{.items[*].metadata.name}' 2>$null
        if ($failedPods) {
            foreach ($pod in $failedPods.Split(" ", [System.StringSplitOptions]::RemoveEmptyEntries)) {
                Write-Host "  Deleting failed pod: $pod"
                kubectl delete pod $pod -n $Namespace --grace-period=0 --force 2>$null
            }
        } else {
            Write-Host "  No failed pods to clean up"
        }

        # Step 4: Verify final state
        Write-Host ""
        Write-Host "Rollback complete. Current pod status:" -ForegroundColor Green
        Write-Host ""
        kubectl get pods -n $Namespace
    }

    "cleanup" {
        Write-Host "Cleaning up all failed/error pods..." -ForegroundColor Yellow
        Write-Host ""

        $podsJson = kubectl get pods -n $Namespace -o json | ConvertFrom-Json
        foreach ($item in $podsJson.items) {
            if ($item.status.phase -ne "Running") {
                $podName = $item.metadata.name
                Write-Host "Deleting pod: $podName"
                kubectl delete pod $podName -n $Namespace --grace-period=0 --force 2>$null
            }
        }

        Write-Host ""
        Write-Host "Cleanup complete. Current pod status:" -ForegroundColor Green
        kubectl get pods -n $Namespace
    }

    "status" {
        Write-Host "Current status:" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "=== Pods ==="
        kubectl get pods -n $Namespace
        Write-Host ""
        Write-Host "=== Deployment ==="
        kubectl get deployment $Deployment -n $Namespace
        Write-Host ""
        Write-Host "=== Alarm State ==="
        aws cloudwatch describe-alarms `
            --alarm-names $AlarmName `
            --region $Region `
            --query 'MetricAlarms[0].{State:StateValue,Reason:StateReason}' `
            --output table
    }

    "reset" {
        Write-Host "Full reset - restoring healthy state..." -ForegroundColor Yellow
        Write-Host ""

        # Step 0: Restore EKS node group scaling
        Write-Host "Step 0: Restoring EKS node group to 2 nodes..."
        $projName = if ($env:PROJECT_NAME) { $env:PROJECT_NAME } else { "devops-agent-eks" }
        $envName = if ($env:ENVIRONMENT) { $env:ENVIRONMENT } else { "dev" }
        $clusterName = "${projName}-${envName}-cluster"
        $nodegroup = aws eks list-nodegroups --cluster-name $clusterName --region $Region --query "nodegroups[0]" --output text 2>$null
        if ($nodegroup -and $nodegroup -ne "None") {
            $currentDesired = aws eks describe-nodegroup --cluster-name $clusterName --nodegroup-name $nodegroup --region $Region --query "nodegroup.scalingConfig.desiredSize" --output text 2>$null
            if ([int]$currentDesired -lt 2) {
                aws eks update-nodegroup-config --cluster-name $clusterName --nodegroup-name $nodegroup --scaling-config minSize=1,maxSize=10,desiredSize=2 --region $Region 2>$null | Out-Null
                Write-Host "  Scaled node group from $currentDesired to 2 desired nodes"
                Write-Host "  Waiting for nodes to be ready (up to 3 min)..."
                for ($i = 1; $i -le 18; $i++) {
                    $readyNodes = (kubectl get nodes --no-headers 2>$null | Select-String " Ready " | Measure-Object).Count
                    if ($readyNodes -ge 2) {
                        Write-Host "  ✓ $readyNodes nodes ready" -ForegroundColor Green
                        break
                    }
                    Start-Sleep -Seconds 10
                }
            } else {
                Write-Host "  Node group already at $currentDesired desired nodes"
            }
        } else {
            Write-Host "  ⚠ Could not find node group, skipping" -ForegroundColor Yellow
        }

        # Restore credentials
        Write-Host "Step 1: Restoring database credentials..."
        $patch = '[{"op": "replace", "path": "/spec/template/spec/containers/0/env/4", "value": {"name": "DB_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "db-credentials", "key": "DB_PASSWORD"}}}}]'
        $patchFile = [System.IO.Path]::GetTempFileName()
        Set-Content -Path $patchFile -Value $patch -Encoding UTF8
        try {
            kubectl patch deployment $Deployment -n $Namespace --type='json' --patch-file=$patchFile
        } catch {
            Write-Host "  (credentials already correct)"
        }
        Remove-Item $patchFile -ErrorAction SilentlyContinue

        # Force restart to ensure clean state
        Write-Host ""
        Write-Host "Step 2: Restarting deployment..."
        kubectl rollout restart deployment/$Deployment -n $Namespace

        # Wait for rollout
        Write-Host ""
        Write-Host "Step 3: Waiting for healthy pods..."
        kubectl rollout status deployment/$Deployment -n $Namespace --timeout=180s

        # Clean up any stragglers
        Write-Host ""
        Write-Host "Step 4: Cleaning up old pods..."
        Start-Sleep -Seconds 5
        $podsJson = kubectl get pods -n $Namespace -o json | ConvertFrom-Json
        foreach ($item in $podsJson.items) {
            if ($item.status.phase -ne "Running") {
                $podName = $item.metadata.name
                kubectl delete pod $podName -n $Namespace --grace-period=0 --force 2>$null
            }
        }

        Write-Host ""
        Write-Host "Reset complete. Final status:" -ForegroundColor Green
        kubectl get pods -n $Namespace
    }
}
