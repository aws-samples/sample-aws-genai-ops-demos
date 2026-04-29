# cleanup.ps1 — Delete CloudWatch alarms, metric filter, and both CDK stacks.
#
# Usage: .\cleanup.ps1 -Region <region>

param(
    [Parameter(Mandatory=$true)]
    [string]$Region
)

$ErrorActionPreference = "Continue"

$vpnStack = "VpnDemoStack-$Region"
$mcpStack = "VpnDemoMcpServer-$Region"

Write-Host ">> Region: $Region"

Write-Host ">> Deleting CloudWatch alarms..."
aws cloudwatch delete-alarms `
    --alarm-names vpn-demo-tunnel1-down vpn-demo-tunnel2-down vpn-demo-throughput-drop vpn-demo-route-withdrawn `
    --region $Region --no-cli-pager 2>$null

Write-Host ">> Deleting metric filter..."
aws logs delete-metric-filter `
    --log-group-name /vpn-demo/tunnel-logs `
    --filter-name vpn-demo-route-withdrawn `
    --region $Region --no-cli-pager 2>$null

Write-Host ">> Deleting MCP server stack: $mcpStack ..."
aws cloudformation delete-stack --stack-name $mcpStack --region $Region --no-cli-pager 2>$null
aws cloudformation wait stack-delete-complete --stack-name $mcpStack --region $Region --no-cli-pager 2>$null
Write-Host "  Done."

Write-Host ">> Deleting VPN stack: $vpnStack ..."
aws cloudformation delete-stack --stack-name $vpnStack --region $Region --no-cli-pager

Write-Host ">> Waiting for stack deletion..."
aws cloudformation wait stack-delete-complete --stack-name $vpnStack --region $Region --no-cli-pager

Write-Host ""
Write-Host ">> Cleanup complete."
Write-Host "   Deleted: $vpnStack, $mcpStack, 4 alarms, 1 metric filter"

# CDK bootstrap resources (shared across all CDK apps in this account/region)
$accountId = aws sts get-caller-identity --query Account --output text --no-cli-pager
$cdkBucket = "cdk-hnb659fds-assets-${accountId}-${Region}"
Write-Host ""
Write-Host ">> WARNING: The next step deletes CDK bootstrap resources that are SHARED across ALL CDK apps in this account/region." -ForegroundColor Yellow -BackgroundColor Black
Write-Host ">> If you have other CDK apps in $Region, DO NOT delete these." -ForegroundColor Yellow -BackgroundColor Black
$confirm = Read-Host ">> Delete CDK bootstrap resources ($cdkBucket + CDKToolkit stack)? [y/N]"
if ($confirm -eq "y" -or $confirm -eq "Y") {
    Write-Host ">> Deleting CDK bootstrap bucket: $cdkBucket ..."
    aws s3 rb "s3://$cdkBucket" --force --region $Region --no-cli-pager 2>$null
    Write-Host ">> Deleting CDKToolkit stack..."
    aws cloudformation delete-stack --stack-name CDKToolkit --region $Region --no-cli-pager 2>$null
    aws cloudformation wait stack-delete-complete --stack-name CDKToolkit --region $Region --no-cli-pager 2>$null
    Write-Host "  Done."
} else {
    Write-Host ">> Skipped. To delete later:"
    Write-Host "   aws s3 rb s3://$cdkBucket --force --region $Region"
    Write-Host "   aws cloudformation delete-stack --stack-name CDKToolkit --region $Region"
}
