# verify-cleanup.ps1 — Check for leftover VPN demo resources in a region
#
# Usage: .\verify-cleanup.ps1 -Region <region>

param(
    [Parameter(Mandatory=$true)]
    [string]$Region
)

$ErrorActionPreference = "Continue"
$accountId = aws sts get-caller-identity --query Account --output text --no-cli-pager
$found = 0

Write-Host "Checking for leftover VPN demo resources in $Region (account $accountId)..."
Write-Host ""

# CloudFormation stacks
Write-Host ">> CloudFormation stacks..."
foreach ($stack in "VpnDemoStack-$Region", "VpnDemoMcpServer-$Region") {
    $status = aws cloudformation describe-stacks --stack-name $stack --region $Region `
        --query 'Stacks[0].StackStatus' --output text --no-cli-pager 2>$null
    if ($LASTEXITCODE -eq 0 -and $status -ne "DELETE_COMPLETE") {
        Write-Host "   FOUND: $stack ($status)" -ForegroundColor Yellow
        $found = 1
    }
}
Write-Host "   CDKToolkit:"
$status = aws cloudformation describe-stacks --stack-name CDKToolkit --region $Region `
    --query 'Stacks[0].StackStatus' --output text --no-cli-pager 2>$null
if ($LASTEXITCODE -eq 0 -and $status -ne "DELETE_COMPLETE") {
    Write-Host "   FOUND: CDKToolkit ($status)" -ForegroundColor Yellow
    $found = 1
} else {
    Write-Host "   Clean"
}

# CloudWatch alarms
Write-Host ">> CloudWatch alarms..."
$alarms = aws cloudwatch describe-alarms --region $Region `
    --alarm-name-prefix vpn-demo `
    --query 'MetricAlarms[].AlarmName' --output text --no-cli-pager 2>$null
if (-not [string]::IsNullOrEmpty($alarms)) {
    Write-Host "   FOUND: $alarms" -ForegroundColor Yellow
    $found = 1
} else {
    Write-Host "   Clean"
}

# Metric filter
Write-Host ">> CloudWatch metric filter..."
$filter = aws logs describe-metric-filters --region $Region `
    --log-group-name /vpn-demo/tunnel-logs `
    --query 'metricFilters[].filterName' --output text --no-cli-pager 2>$null
if (-not [string]::IsNullOrEmpty($filter)) {
    Write-Host "   FOUND: $filter" -ForegroundColor Yellow
    $found = 1
} else {
    Write-Host "   Clean"
}

# CDK bootstrap bucket
Write-Host ">> CDK bootstrap bucket..."
$cdkBucket = "cdk-hnb659fds-assets-${accountId}-${Region}"
aws s3api head-bucket --bucket $cdkBucket --region $Region --no-cli-pager 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "   FOUND: $cdkBucket" -ForegroundColor Yellow
    $found = 1
} else {
    Write-Host "   Clean"
}

# Agent Spaces
Write-Host ">> DevOps Agent spaces..."
$spaces = aws devops-agent list-agent-spaces --region $Region `
    --query "agentSpaces[?contains(name,'vpn-demo')].{Name:name,Id:agentSpaceId}" `
    --output text --no-cli-pager 2>$null
if (-not [string]::IsNullOrEmpty($spaces)) {
    Write-Host "   FOUND: $spaces" -ForegroundColor Yellow
    $found = 1
} else {
    Write-Host "   Clean"
}

# IAM roles (global)
Write-Host ">> IAM roles..."
$rolesFound = 0
foreach ($role in "DevOpsAgentRole-AgentSpace", "DevOpsAgentRole-WebappAdmin") {
    $roleName = aws iam get-role --role-name $role --query 'Role.RoleName' --output text --no-cli-pager 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "   FOUND: $role" -ForegroundColor Yellow
        $found = 1
        $rolesFound = 1
    }
}
if ($rolesFound -eq 0) { Write-Host "   Clean" }

# EC2 key pairs
Write-Host ">> EC2 key pairs..."
$keys = aws ec2 describe-key-pairs --region $Region `
    --query "KeyPairs[?contains(KeyName,'vpn-demo')].KeyName" `
    --output text --no-cli-pager 2>$null
if (-not [string]::IsNullOrEmpty($keys)) {
    Write-Host "   FOUND: $keys" -ForegroundColor Yellow
    $found = 1
} else {
    Write-Host "   Clean"
}

Write-Host ""
if ($found -eq 0) {
    Write-Host "All clean. No leftover VPN demo resources found in $Region." -ForegroundColor Green
} else {
    Write-Host "Some VPN demo resources remain. See above for details." -ForegroundColor Yellow
}
