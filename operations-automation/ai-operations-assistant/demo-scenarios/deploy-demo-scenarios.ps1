# G.O.A.T. Demo Scenarios - CDK Deployment Script
#
# Deploys demo scenario CDK stacks and creates Support cases for G.O.A.T. demos.
# Uses the separate demo-scenarios-app.ts CDK entry point.

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("all", "account-health", "cloudwatch-incident", "connectivity", "network-troubleshooting")]
    [string]$Scenario
)

$ErrorActionPreference = "Stop"

Write-Host "=== G.O.A.T. Demo Scenarios Deployment ===" -ForegroundColor Cyan
Write-Host "      Scenario: $Scenario" -ForegroundColor Gray

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
Write-Host "`nRunning prerequisites check..." -ForegroundColor Yellow
& "$PSScriptRoot\..\..\..\shared\scripts\check-prerequisites.ps1" -RequireCDK

if ($LASTEXITCODE -ne 0) {
    Write-Host "Prerequisites check failed" -ForegroundColor Red
    exit 1
}

$region = $global:AWS_REGION
$cdkDir = "$PSScriptRoot\..\infrastructure\cdk"
$cdkApp = "npx ts-node --prefer-ts-exts bin/demo-scenarios-app.ts"

# Track created/found case IDs for summary
$script:caseIds = @()

# ---------------------------------------------------------------------------
# Helper: Deploy a CDK stack
# ---------------------------------------------------------------------------
function Invoke-CdkDeploy {
    param([string]$StackName)
    Write-Host "`nDeploying $StackName..." -ForegroundColor Yellow
    Push-Location $cdkDir
    npx cdk deploy $StackName --app $cdkApp --require-approval never --no-cli-pager
    $exitCode = $LASTEXITCODE
    Pop-Location
    if ($exitCode -ne 0) {
        Write-Host "ERROR: Deployment of $StackName failed" -ForegroundColor Red
        exit 1
    }
}

# ---------------------------------------------------------------------------
# Helper: Create and resolve a Support case (with duplicate check)
# Returns the case displayId (or existing one if duplicate found)
# ---------------------------------------------------------------------------
function New-DemoSupportCase {
    param(
        [string]$Subject,
        [string]$Body,
        [string]$ServiceCode = "general-info",
        [string]$CategoryCode = "other"
    )
    try {
        # Check for existing case with the same subject (avoid duplicates)
        $existing = aws support describe-cases `
            --include-resolved-cases `
            --region us-east-1 `
            --query "cases[?contains(subject,'$Subject')].displayId" `
            --output text --no-cli-pager 2>&1
        if ($LASTEXITCODE -ne 0) {
            if ($existing -match "SubscriptionRequired" -or $existing -match "not subscribed") {
                Write-Host "  No Business or Enterprise Support plan -- skipping case creation" -ForegroundColor Yellow
                return ""
            }
            Write-Host "  WARNING: Cannot query Support API: $existing" -ForegroundColor Yellow
            return ""
        }
        if ($existing -and $existing.Trim() -ne "" -and $existing.Trim() -ne "None") {
            $displayId = ($existing.Trim() -split "\s+")[0]
            Write-Host "  Support case already exists: $displayId -- skipping creation" -ForegroundColor Gray
            $script:caseIds += $displayId
            return $displayId
        }

        $caseId = aws support create-case `
            --subject $Subject `
            --communication-body $Body `
            --service-code $ServiceCode `
            --category-code $CategoryCode `
            --severity-code "low" `
            --language "en" `
            --region us-east-1 `
            --query "caseId" --output text --no-cli-pager 2>&1
        if ($LASTEXITCODE -ne 0) {
            if ($caseId -match "SubscriptionRequiredException") {
                Write-Host "  No Business or Enterprise Support plan detected -- skipping" -ForegroundColor Yellow
                return ""
            }
            Write-Host "  WARNING: CreateCase failed: $caseId" -ForegroundColor Yellow
            return ""
        }
        Start-Sleep -Seconds 5
        aws support resolve-case --case-id $caseId --region us-east-1 --no-cli-pager 2>$null
        # Get the display ID for user-friendly output
        $displayId = aws support describe-cases `
            --case-id-list $caseId `
            --include-resolved-cases `
            --region us-east-1 `
            --query "cases[0].displayId" `
            --output text --no-cli-pager 2>$null
        if (-not $displayId -or $displayId -eq "None") { $displayId = $caseId }
        Write-Host "  Created and resolved Support case: $displayId" -ForegroundColor Green
        $script:caseIds += $displayId
        return $displayId
    } catch {
        Write-Host "  WARNING: Support API error: $_" -ForegroundColor Yellow
        return ""
    }
}

# ---------------------------------------------------------------------------
# Deployment Logic
# ---------------------------------------------------------------------------
$stackA = "GOATDemoScenarioA-$region"
$stackC = "GOATDemoScenarioC-$region"

switch ($Scenario) {
    "all" {
        Invoke-CdkDeploy $stackA
        New-DemoSupportCase "General account review - G.O.A.T. demo" "This case was created for demo purposes by the G.O.A.T. provisioning scripts."
        New-DemoSupportCase "CloudWatch monitoring gaps and missing alarms on Apr 1 - G.O.A.T. demo" "Our team noticed a CloudWatch lifecycle event on April 1 resulting in monitoring gaps. Several alarms were missing or misconfigured."
        Invoke-CdkDeploy $stackC
        New-DemoSupportCase `
            -Subject "EC2 instance failing HTTPS to ECR - connection reset by peer in $region" `
            -Body "Our instance in goat-demo-vpc is failing to establish HTTPS connections to ECR (endpoint: ecr.$region.amazonaws.com on port 443). The connexion is going through the TGW and the NFW in goat-demo-security-vpc but it is dropped. This case was created by the G.O.A.T. demo provisioning scripts for demonstration purposes." `
            -ServiceCode "service-network-firewall" `
            -CategoryCode "general-guidance"
        $stackGL = "GOATDemoScenariosGL-$region"
        Invoke-CdkDeploy $stackGL
    }
    "account-health" {
        Invoke-CdkDeploy $stackA
        New-DemoSupportCase "General account review - G.O.A.T. demo" "This case was created for demo purposes by the G.O.A.T. provisioning scripts."
    }
    "cloudwatch-incident" {
        New-DemoSupportCase "CloudWatch monitoring gaps and missing alarms on Apr 1 - G.O.A.T. demo" "Our team noticed a CloudWatch lifecycle event on April 1 resulting in monitoring gaps. Several alarms were missing or misconfigured."
    }
    "connectivity" {
        Invoke-CdkDeploy $stackC
        New-DemoSupportCase `
            -Subject "EC2 instance failing HTTPS to ECR - connection reset by peer in $region" `
            -Body "Our instance in goat-demo-vpc is failing to establish HTTPS connections to ECR (endpoint: ecr.$region.amazonaws.com on port 443). The connexion is going through the TGW and the NFW in goat-demo-security-vpc but it is dropped. This case was created by the G.O.A.T. demo provisioning scripts for demonstration purposes." `
            -ServiceCode "service-network-firewall" `
            -CategoryCode "general-guidance"
    }
    "network-troubleshooting" {
        # --- Pre-deploy: Ensure pymysql Lambda layer exists ---
        Write-Host "`nChecking pymysql Lambda layer..." -ForegroundColor Yellow
        $layerExists = aws lambda list-layer-versions --layer-name "pymysql" --query "LayerVersions[0].LayerVersionArn" --output text --no-cli-pager 2>&1
        if (-not $layerExists -or $layerExists -eq "None" -or $layerExists -match "error") {
            Write-Host "  Publishing pymysql Lambda layer..." -ForegroundColor Cyan
            $layerDir = "$env:TEMP\pymysql_layer_$(Get-Date -Format 'yyyyMMddHHmmss')"
            New-Item -ItemType Directory -Force -Path "$layerDir\python" | Out-Null
            python -m pip install pymysql -t "$layerDir\python" -q 2>&1 | Out-Null
            Compress-Archive -Path "$layerDir\python" -DestinationPath "$layerDir\pymysql-layer.zip" -Force
            $layerArn = aws lambda publish-layer-version --layer-name "pymysql" --zip-file "fileb://$layerDir/pymysql-layer.zip" --compatible-runtimes "python3.12" --description "PyMySQL library for Lambda" --output text --no-cli-pager --query "LayerVersionArn"
            Remove-Item -Recurse -Force $layerDir -ErrorAction SilentlyContinue
            Write-Host "  Published: $layerArn" -ForegroundColor Green
        } else {
            Write-Host "  pymysql layer exists: $layerExists" -ForegroundColor Green
        }

        # --- CDK Deploy ---
        $stackGL = "GOATDemoScenariosGL-$region"
        Invoke-CdkDeploy $stackGL

        # --- Post-deploy: Configure MySQL auth for pool saturator ---
        Write-Host "`nConfiguring MySQL authentication for pool saturator..." -ForegroundColor Yellow

        # Get svc-alpha instance ID from stack outputs
        $svcAlphaId = aws cloudformation describe-stacks --stack-name $stackGL --query "Stacks[0].Outputs[?OutputKey=='ScenarioHSvcAlphaInstanceId'].OutputValue" --output text --no-cli-pager
        $rdsEndpoint = aws cloudformation describe-stacks --stack-name $stackGL --query "Stacks[0].Outputs[?OutputKey=='ScenarioKRdsEndpoint'].OutputValue" --output text --no-cli-pager

        if ($svcAlphaId -and $rdsEndpoint -and $svcAlphaId -ne "None") {
            # Wait for SSM agent to be online
            Write-Host "  Waiting for SSM agent on svc-alpha ($svcAlphaId)..." -ForegroundColor DarkGray
            $ssmReady = $false
            for ($i = 0; $i -lt 30; $i++) {
                $pingStatus = aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$svcAlphaId" --query "InstanceInformationList[0].PingStatus" --output text --no-cli-pager 2>&1
                if ($pingStatus -eq "Online") { $ssmReady = $true; break }
                Start-Sleep -Seconds 10
            }

            if ($ssmReady) {
                # Install mariadb client and run ALTER USER via SSM
                # Note: For fresh deployments, the authentication_policy parameter group
                # takes effect immediately and ALTER USER may not be needed. If the pool
                # is already saturated (Lambda connected successfully), skip ALTER USER.
                Write-Host "  Checking if pool saturator is already working..." -ForegroundColor Cyan
                $prevEAP0 = $ErrorActionPreference; $ErrorActionPreference = "Continue"
                aws lambda invoke --function-name "svc-data-sync-worker" --payload '{}' "$env:TEMP\saturator_check.json" --output text --no-cli-pager 2>$null | Out-Null
                $ErrorActionPreference = $prevEAP0
                $satCheck = Get-Content "$env:TEMP\saturator_check.json" -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
                if ($satCheck -and $satCheck.active -gt 10) {
                    Write-Host "  Pool saturator already holding $($satCheck.active) connections - skipping ALTER USER." -ForegroundColor Green
                } else {
                    Write-Host "  Running ALTER USER to set mysql_native_password..." -ForegroundColor Cyan
                    $ssmScript = "#!/bin/bash`nyum install -y mariadb105 -q 2>/dev/null`nmysql -h $rdsEndpoint -u admin -pGoatDemoK2026! -e `"ALTER USER admin IDENTIFIED WITH mysql_native_password BY 'GoatDemoK2026!'; TRUNCATE TABLE performance_schema.host_cache;`" 2>&1"
                    $ssmParamsFile = "$env:TEMP\ssm_alter_user.json"
                    $json = @{ commands = @($ssmScript) } | ConvertTo-Json -Depth 3
                    [System.IO.File]::WriteAllText($ssmParamsFile, $json, [System.Text.UTF8Encoding]::new($false))
                    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = "Continue"
                    $cmdId = aws ssm send-command --instance-ids $svcAlphaId --document-name "AWS-RunShellScript" --parameters "file://$ssmParamsFile" --output text --no-cli-pager --query "Command.CommandId" 2>&1
                    $ErrorActionPreference = $prevEAP
                    if ($cmdId -and $cmdId -notmatch "error|Error") {
                        Start-Sleep -Seconds 25
                        $prevEAP2 = $ErrorActionPreference; $ErrorActionPreference = "Continue"
                        $cmdResult = aws ssm get-command-invocation --command-id $cmdId --instance-id $svcAlphaId --query "Status" --output text --no-cli-pager 2>&1
                        $ErrorActionPreference = $prevEAP2
                        if ($cmdResult -eq "Success") {
                            Write-Host "  MySQL auth configured successfully." -ForegroundColor Green
                        } else {
                            Write-Host "  Warning: ALTER USER may have failed (status: $cmdResult). Pool saturator may not authenticate." -ForegroundColor Yellow
                        }
                    } else {
                        Write-Host "  Warning: SSM send-command failed. Pool saturator may not authenticate." -ForegroundColor Yellow
                    }
                    Remove-Item $ssmParamsFile -ErrorAction SilentlyContinue
                }

                # Invoke pool saturator to pre-saturate connections
                Write-Host "  Pre-saturating connection pool..." -ForegroundColor Cyan
                for ($j = 0; $j -lt 3; $j++) {
                    aws lambda invoke --function-name "svc-data-sync-worker" --payload '{}' "$env:TEMP\saturator_out.json" --output text --no-cli-pager 2>$null | Out-Null
                    Start-Sleep -Seconds 3
                }
                $satResult = Get-Content "$env:TEMP\saturator_out.json" -ErrorAction SilentlyContinue
                Write-Host "  Pool saturator result: $satResult" -ForegroundColor DarkGray
            } else {
                Write-Host "  Warning: SSM agent not ready on svc-alpha. Pool saturator may not authenticate." -ForegroundColor Yellow
                Write-Host "  Manual fix after instance is ready:" -ForegroundColor DarkGray
                Write-Host "    mysql -h $rdsEndpoint -u admin -pGoatDemoK2026! -e `"ALTER USER admin IDENTIFIED WITH mysql_native_password BY 'GoatDemoK2026!';`"" -ForegroundColor DarkGray
            }
        } else {
            Write-Host "  Warning: Could not find svc-alpha or RDS endpoint from stack outputs." -ForegroundColor Yellow
        }
    }
}

# ---------------------------------------------------------------------------
# Deployment Summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  G.O.A.T. Demo Scenario Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Region: $region" -ForegroundColor Cyan

if ($Scenario -eq "all" -or $Scenario -eq "account-health") {
    $vpcId = aws cloudformation describe-stacks --stack-name $stackA --query "Stacks[0].Outputs[?OutputKey=='VpcId'].OutputValue" --output text --no-cli-pager
    $inst1 = aws cloudformation describe-stacks --stack-name $stackA --query "Stacks[0].Outputs[?OutputKey=='Instance1Id'].OutputValue" --output text --no-cli-pager
    Write-Host "  VPC:          $vpcId" -ForegroundColor Cyan
    Write-Host "  EC2 Instance: $inst1" -ForegroundColor Cyan
}

if ($Scenario -eq "all" -or $Scenario -eq "connectivity") {
    $ec2Id = aws cloudformation describe-stacks --stack-name $stackC --query "Stacks[0].Outputs[?OutputKey=='AppInstanceId'].OutputValue" --output text --no-cli-pager
    $eniId = aws cloudformation describe-stacks --stack-name $stackC --query "Stacks[0].Outputs[?OutputKey=='AppInstanceEniId'].OutputValue" --output text --no-cli-pager
    Write-Host "  App EC2:      $ec2Id" -ForegroundColor Cyan
    Write-Host "  App ENI:      $eniId (for Network Agent capture)" -ForegroundColor Cyan
}

if ($script:caseIds.Count -gt 0) {
    Write-Host ""
    Write-Host "  Support Cases:" -ForegroundColor Yellow
    foreach ($id in $script:caseIds) {
        Write-Host "    $id" -ForegroundColor Cyan
    }
    Write-Host ""
    Write-Host "  Try in G.O.A.T.:" -ForegroundColor Yellow
    Write-Host "    `"Help me troubleshoot case $($script:caseIds[-1])`"" -ForegroundColor Gray
}

Write-Host ""
Write-Host "  Suggested Demo Queries:" -ForegroundColor Yellow
Write-Host '    "Give me a complete health check of my AWS account"' -ForegroundColor Gray
Write-Host '    "We had application errors on April 1 - was there an AWS issue?"' -ForegroundColor Gray
Write-Host '    "My EC2 instance cannot connect to ECR over HTTPS"' -ForegroundColor Gray
Write-Host ""
Write-Host "  Cleanup:" -ForegroundColor Yellow
Write-Host '    .\cleanup-scenarios.ps1    (PowerShell)' -ForegroundColor Gray
Write-Host '    ./cleanup-scenarios.sh     (Bash)' -ForegroundColor Gray
Write-Host ""
