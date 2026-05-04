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
    Write-Host ">> Emptying CDK bootstrap bucket: $cdkBucket (including versioned objects)..."
    # CDK bootstrap bucket has versioning enabled — must delete all versions and delete markers
    # Use raw JSON from AWS CLI directly (avoids PowerShell 5.1 ConvertTo-Json array issues)
    $tmpFile = [System.IO.Path]::GetTempFileName()
    $versions = aws s3api list-object-versions --bucket $cdkBucket --region $Region --no-cli-pager --query "Versions[].{Key:Key,VersionId:VersionId}" --output json 2>$null
    if ($versions -and $versions -ne "null") {
        Set-Content -Path $tmpFile -Value "{`"Objects`":$versions}" -Encoding ASCII
        aws s3api delete-objects --bucket $cdkBucket --region $Region --no-cli-pager --delete "file://$($tmpFile.Replace('\','/'))" 2>$null | Out-Null
    }
    $markers = aws s3api list-object-versions --bucket $cdkBucket --region $Region --no-cli-pager --query "DeleteMarkers[].{Key:Key,VersionId:VersionId}" --output json 2>$null
    if ($markers -and $markers -ne "null") {
        Set-Content -Path $tmpFile -Value "{`"Objects`":$markers}" -Encoding ASCII
        aws s3api delete-objects --bucket $cdkBucket --region $Region --no-cli-pager --delete "file://$($tmpFile.Replace('\','/'))" 2>$null | Out-Null
    }
    Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue
    Write-Host ">> Deleting CDK bootstrap bucket..."
    aws s3api delete-bucket --bucket $cdkBucket --region $Region --no-cli-pager 2>$null
    Write-Host ">> Deleting CDKToolkit stack..."
    aws cloudformation delete-stack --stack-name CDKToolkit --region $Region --no-cli-pager 2>$null
    aws cloudformation wait stack-delete-complete --stack-name CDKToolkit --region $Region --no-cli-pager 2>$null
    Write-Host "  Done."
} else {
    Write-Host ">> Skipped. To delete later:"
    Write-Host "   CLI:     1. Empty bucket (versioned): aws s3api list-object-versions --bucket $cdkBucket --region $Region (then delete-objects for all versions)"
    Write-Host "            2. Delete bucket: aws s3api delete-bucket --bucket $cdkBucket --region $Region"
    Write-Host "            3. Delete stack:  aws cloudformation delete-stack --stack-name CDKToolkit --region $Region"
    Write-Host "   Console: Open https://console.aws.amazon.com/s3/"
    Write-Host "            1. Select bucket '$cdkBucket'"
    Write-Host "            2. Click 'Empty', type 'permanently delete', click Empty"
    Write-Host "            3. Click 'Delete', type the bucket name, click Delete bucket"
    Write-Host "            Then open https://console.aws.amazon.com/cloudformation/ (region: $Region)"
    Write-Host "            4. Select stack 'CDKToolkit', click Delete"
}
