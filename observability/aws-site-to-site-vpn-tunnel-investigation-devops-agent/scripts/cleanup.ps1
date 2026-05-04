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
    $versions = aws s3api list-object-versions --bucket $cdkBucket --region $Region --no-cli-pager --query "Versions[].{Key:Key,VersionId:VersionId}" --output json 2>$null | ConvertFrom-Json
    if ($versions -and $versions.Count -gt 0) {
        $deleteJson = @{ Objects = $versions } | ConvertTo-Json -Compress -Depth 5
        $tmpFile = [System.IO.Path]::GetTempFileName()
        Set-Content -Path $tmpFile -Value $deleteJson -Encoding ASCII
        aws s3api delete-objects --bucket $cdkBucket --region $Region --no-cli-pager --delete "file://$($tmpFile.Replace('\','/'))" 2>$null | Out-Null
        Remove-Item $tmpFile -Force
    }
    $markers = aws s3api list-object-versions --bucket $cdkBucket --region $Region --no-cli-pager --query "DeleteMarkers[].{Key:Key,VersionId:VersionId}" --output json 2>$null | ConvertFrom-Json
    if ($markers -and $markers.Count -gt 0) {
        $deleteJson = @{ Objects = $markers } | ConvertTo-Json -Compress -Depth 5
        $tmpFile = [System.IO.Path]::GetTempFileName()
        Set-Content -Path $tmpFile -Value $deleteJson -Encoding ASCII
        aws s3api delete-objects --bucket $cdkBucket --region $Region --no-cli-pager --delete "file://$($tmpFile.Replace('\','/'))" 2>$null | Out-Null
        Remove-Item $tmpFile -Force
    }
    Write-Host ">> Deleting CDK bootstrap bucket..."
    aws s3api delete-bucket --bucket $cdkBucket --region $Region --no-cli-pager 2>$null
    Write-Host ">> Deleting CDKToolkit stack..."
    aws cloudformation delete-stack --stack-name CDKToolkit --region $Region --no-cli-pager 2>$null
    aws cloudformation wait stack-delete-complete --stack-name CDKToolkit --region $Region --no-cli-pager 2>$null
    Write-Host "  Done."
} else {
    Write-Host ">> Skipped. To delete later:"
    Write-Host "   aws s3 rb s3://$cdkBucket --force --region $Region"
    Write-Host "   aws cloudformation delete-stack --stack-name CDKToolkit --region $Region"
}
