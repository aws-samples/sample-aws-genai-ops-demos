# PowerShell deployment script for AnyCompany IT Portal Demo
param(
    [switch]$SkipBuild,
    [switch]$DestroyInfra,
    [switch]$PopulateData
)

$ErrorActionPreference = "Stop"

Write-Host "=== AnyCompany IT Portal Demo Deployment ===" -ForegroundColor Green

if ($DestroyInfra) {
    Write-Host "Destroying infrastructure..." -ForegroundColor Red
    
    # Use shared CDK destroy script
    & "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "infrastructure/cdk" -DestroyStack
    
    Write-Host "Infrastructure destruction completed" -ForegroundColor Green
    exit 0
}

# Use shared prerequisites check
Write-Host "Checking prerequisites..." -ForegroundColor Yellow
& "..\..\shared\scripts\check-prerequisites.ps1" -RequireCDK

if ($LASTEXITCODE -ne 0) {
    Write-Host "Prerequisites check failed" -ForegroundColor Red
    exit 1
}

# Get region from shared prerequisites
$Region = $global:AWS_REGION

# Deploy CDK infrastructure using shared script
Write-Host "Deploying AWS infrastructure..." -ForegroundColor Yellow
& "..\..\shared\scripts\deploy-cdk.ps1" -CdkDirectory "infrastructure/cdk"
if ($LASTEXITCODE -ne 0) {
    Write-Error "CDK deployment failed"
    exit 1
}

# Get CDK outputs
Write-Host "Getting CDK stack outputs..." -ForegroundColor Yellow

# Get outputs using AWS CLI
$stackName = "AnyCompanyITPortalStack-$Region"
$outputs = aws cloudformation describe-stacks --stack-name $stackName --query "Stacks[0].Outputs" --output json --no-cli-pager 2>&1

if ($LASTEXITCODE -eq 0) {
    $outputsJson = $outputs | ConvertFrom-Json
    
    Write-Host "=== Deployment Outputs ===" -ForegroundColor Green
    
    foreach ($output in $outputsJson) {
        switch ($output.OutputKey) {
            "WebsiteURL" { 
                Write-Host "Website URL: $($output.OutputValue)" -ForegroundColor Cyan
                $env:WEBSITE_URL = $output.OutputValue
            }
            "APIEndpoint" { 
                Write-Host "API Endpoint: $($output.OutputValue)" -ForegroundColor Cyan
                $env:API_ENDPOINT = $output.OutputValue
            }
            "S3BucketName" { 
                Write-Host "S3 Bucket: $($output.OutputValue)" -ForegroundColor Cyan
                $env:S3_BUCKET = $output.OutputValue
            }
            "CloudFrontDistributionId" { 
                Write-Host "CloudFront Distribution: $($output.OutputValue)" -ForegroundColor Cyan
                $env:CLOUDFRONT_ID = $output.OutputValue
            }
        }
    }
} else {
    Write-Host "Could not retrieve stack outputs, continuing..." -ForegroundColor Yellow
}

# Upload website files to S3
if ($env:S3_BUCKET) {
    Write-Host "Uploading static HTML portals to S3..." -ForegroundColor Yellow
    
    # Generate config.js with the correct API endpoint
    $configContent = @"
// Configuration file - generated during deployment
window.APP_CONFIG = {
    apiBaseUrl: '$env:API_ENDPOINT'
};
"@
    $configContent | Out-File -FilePath "frontend/config.js" -Encoding UTF8
    Write-Host "Generated config.js with API endpoint: $env:API_ENDPOINT" -ForegroundColor Green
    
    aws s3 sync frontend/ s3://$env:S3_BUCKET --delete --no-cli-pager
    
    # Invalidate CloudFront cache
    if ($env:CLOUDFRONT_ID) {
        Write-Host "Invalidating CloudFront cache..." -ForegroundColor Yellow
        aws cloudfront create-invalidation --distribution-id $env:CLOUDFRONT_ID --paths "/*" --no-cli-pager
    }
}

# Populate mock data
if ($PopulateData) {
    Write-Host "Populating mock data..." -ForegroundColor Yellow
    python scripts/seed-data.py $Region
}

Write-Host "=== Deployment Complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Website URL: $env:WEBSITE_URL" -ForegroundColor Cyan
Write-Host "API Endpoint: $env:API_ENDPOINT" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "1. Open the website URL to access the IT Portal Demo" -ForegroundColor White
Write-Host "2. Navigate between different portals to see the mock data" -ForegroundColor White
Write-Host "3. Use this environment for AI automation testing" -ForegroundColor White
Write-Host ""
Write-Host "To destroy the infrastructure later, run:" -ForegroundColor Yellow
Write-Host ".\deploy-all.ps1 -DestroyInfra" -ForegroundColor White
