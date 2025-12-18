#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Deploy AWS Transform Documentation Generator using CDK and CodeBuild

.DESCRIPTION
    This script deploys a CodeBuild-based solution that runs AWS Transform to generate
    comprehensive documentation from any Git repository. Infrastructure is deployed via CDK.

.PARAMETER RepositoryUrl
    Git repository URL to analyze (default: AWS sample repository)

.PARAMETER Region
    AWS region for deployment (default: us-east-1)

.PARAMETER DeployOnly
    Only deploy infrastructure, don't start a build

.EXAMPLE
    .\generate-docs.ps1
    .\generate-docs.ps1 -RepositoryUrl "https://github.com/owner/repo"
    .\generate-docs.ps1 -Region "us-west-2"
#>

param(
    [string]$RepositoryUrl = "",
    [string]$Region = "us-east-1",
    [switch]$DeployOnly = $false
)

$ErrorActionPreference = "Stop"
$defaultRepo = "https://github.com/aws-samples/sample-serverless-digital-asset-payments"

Write-Host "=== AWS Transform Documentation Generator ===" -ForegroundColor Cyan
Write-Host "Generates comprehensive documentation from any Git repository using AWS Transform" -ForegroundColor Green
Write-Host ""
Write-Host "Uses Enhanced AWS-managed transformation with custom organizational context:" -ForegroundColor Yellow
Write-Host "  - AWS-maintained transformation logic for deep technical analysis" -ForegroundColor Gray
Write-Host "  - Custom context for structured, operational documentation" -ForegroundColor Gray
Write-Host "  - Role-based navigation and multi-audience content" -ForegroundColor Gray
Write-Host "  - Expected time: ~53 minutes" -ForegroundColor Gray
Write-Host ""

$transformationType = "enhanced"
$buildspecFile = "buildspec.yml"
$expectedTime = "~53 minutes"

Write-Host ""

# Check if repository URL was provided
if ([string]::IsNullOrEmpty($RepositoryUrl)) {
    Write-Host "No repository URL specified. Documentation will be generated based on the default sample repository:" -ForegroundColor Yellow
    Write-Host "  $defaultRepo" -ForegroundColor White
    Write-Host ""
    Write-Host "To analyze your own repository, run:" -ForegroundColor Gray
    Write-Host "  .\generate-docs.ps1 -RepositoryUrl `"https://github.com/owner/repo`"" -ForegroundColor White
    Write-Host ""
    $RepositoryUrl = $defaultRepo
}

# Get script directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$cdkDir = Join-Path $scriptDir "infrastructure/cdk"
$sharedScriptsDir = Join-Path $scriptDir "..\..\shared\scripts"

# Run prerequisites check
Write-Host "Running prerequisites check..." -ForegroundColor Yellow
& "$sharedScriptsDir\check-prerequisites.ps1" `
    -RequiredService "transform" `
    -RequireCDK

if ($LASTEXITCODE -ne 0) {
    Write-Host "Prerequisites check failed" -ForegroundColor Red
    exit 1
}

# Get AWS account and region info
$accountId = aws sts get-caller-identity --query Account --output text --no-cli-pager
$currentRegion = aws configure get region
if ([string]::IsNullOrEmpty($currentRegion)) {
    $currentRegion = $Region
}

Write-Host ""
Write-Host "Deploying infrastructure via CDK..." -ForegroundColor Yellow
Write-Host "      Region: $currentRegion" -ForegroundColor Gray

# Deploy CDK stack using shared script
& "$sharedScriptsDir\deploy-cdk.ps1" -CdkDirectory $cdkDir

if ($LASTEXITCODE -ne 0) {
    Write-Host "CDK deployment failed" -ForegroundColor Red
    exit 1
}

# Get bucket name and project name from CloudFormation outputs
Write-Host ""
Write-Host "Getting stack outputs..." -ForegroundColor Yellow
$outputBucket = aws cloudformation describe-stacks --stack-name DocumentationGeneratorStack --region $currentRegion --no-cli-pager --query "Stacks[0].Outputs[?OutputKey=='OutputBucketName'].OutputValue" --output text
$projectName = aws cloudformation describe-stacks --stack-name DocumentationGeneratorStack --region $currentRegion --no-cli-pager --query "Stacks[0].Outputs[?OutputKey=='CodeBuildProjectName'].OutputValue" --output text
if ([string]::IsNullOrEmpty($outputBucket) -or [string]::IsNullOrEmpty($projectName)) {
    Write-Host "      ❌ Failed to get stack outputs" -ForegroundColor Red
    exit 1
}
Write-Host "      Output Bucket: $outputBucket" -ForegroundColor Gray
Write-Host "      CodeBuild Project: $projectName" -ForegroundColor Gray

# Upload appropriate buildspec to S3
Write-Host ""
Write-Host "Uploading buildspec to S3..." -ForegroundColor Yellow
$buildspecPath = Join-Path $scriptDir $buildspecFile
aws s3 cp $buildspecPath "s3://$outputBucket/config/buildspec.yml" --region $currentRegion --no-cli-pager | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "      ✓ Buildspec uploaded successfully ($buildspecFile)" -ForegroundColor Green
} else {
    Write-Host "      ❌ Failed to upload buildspec" -ForegroundColor Red
    exit 1
}

# Upload enhanced transformation context
Write-Host ""
Write-Host "Uploading enhanced transformation context..." -ForegroundColor Yellow
$enhancedTransformDir = Join-Path $scriptDir "enhanced-transformation"
aws s3 cp $enhancedTransformDir "s3://$outputBucket/enhanced-transformation/" --recursive --region $currentRegion --no-cli-pager | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "      ✓ Enhanced transformation context uploaded successfully" -ForegroundColor Green
} else {
    Write-Host "      ❌ Failed to upload enhanced transformation context" -ForegroundColor Red
    exit 1
}

if ($DeployOnly) {
    Write-Host ""
    Write-Host "=== Infrastructure Deployment Complete ===" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "To generate documentation, run:" -ForegroundColor Yellow
    Write-Host "  aws codebuild start-build --project-name $projectName --region $currentRegion ``" -ForegroundColor Gray
    Write-Host "    --environment-variables-override name=REPOSITORY_URL,value=https://github.com/owner/repo" -ForegroundColor Gray
    exit 0
}

# Start documentation generation build
Write-Host ""
Write-Host "Starting documentation generation build..." -ForegroundColor Yellow
Write-Host "      Repository: $RepositoryUrl" -ForegroundColor Gray
Write-Host "      Expected time: $expectedTime" -ForegroundColor Gray

# Generate unique job ID
$jobId = "doc-gen-$(Get-Date -Format 'yyyyMMdd-HHmmss')"

# Start build with environment variable overrides
$buildResult = aws codebuild start-build `
    --project-name $projectName `
    --region $currentRegion `
    --no-cli-pager `
    --environment-variables-override "name=REPOSITORY_URL,value=$RepositoryUrl,type=PLAINTEXT" "name=OUTPUT_BUCKET,value=$outputBucket,type=PLAINTEXT" "name=JOB_ID,value=$jobId,type=PLAINTEXT" `
    --query 'build.id' --output text

if ($LASTEXITCODE -eq 0) {
    Write-Host "      ✓ Build started: $buildResult" -ForegroundColor Green
} else {
    Write-Host "      ❌ Failed to start build" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== Deployment Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Resources deployed via CDK:" -ForegroundColor Yellow
Write-Host "  - S3 Bucket: $outputBucket" -ForegroundColor White
Write-Host "  - CodeBuild Project: $projectName" -ForegroundColor White
Write-Host ""
Write-Host "Monitor build progress:" -ForegroundColor Yellow
Write-Host "  aws codebuild batch-get-builds --ids $buildResult --region $currentRegion --no-cli-pager" -ForegroundColor Gray
Write-Host ""
Write-Host "Stream build logs:" -ForegroundColor Yellow
Write-Host "  aws logs tail /aws/codebuild/$projectName --follow --region $currentRegion" -ForegroundColor Gray
Write-Host ""
Write-Host "Check generated documentation:" -ForegroundColor Yellow
Write-Host "  aws s3 ls s3://$outputBucket/documentation/$jobId/ --recursive" -ForegroundColor Gray
Write-Host ""
Write-Host "Currently analyzing:" -ForegroundColor Yellow
Write-Host "  $RepositoryUrl" -ForegroundColor White
Write-Host ""
Write-Host "Generate docs for a different repository:" -ForegroundColor Yellow
Write-Host "  aws codebuild start-build --project-name $projectName --region $currentRegion --no-cli-pager ``" -ForegroundColor Gray
Write-Host "    --environment-variables-override name=REPOSITORY_URL,value=https://github.com/owner/repo" -ForegroundColor Gray

# Offer to wait and download documentation
Write-Host ""
Write-Host "=== Download Documentation ===" -ForegroundColor Cyan
Write-Host "The build is expected to take $expectedTime to complete depending on repository size." -ForegroundColor Yellow
Write-Host ""
$waitChoice = Read-Host "Would you like to wait for the build to complete and download the documentation? (y/n)"

if ($waitChoice -eq "y" -or $waitChoice -eq "Y") {
    Write-Host ""
    Write-Host "Waiting for build to complete..." -ForegroundColor Yellow
    Write-Host "You can press Ctrl+C to cancel and download later using:" -ForegroundColor Gray
    Write-Host "  aws s3 cp s3://$outputBucket/documentation/$jobId/ ./generated-docs --recursive" -ForegroundColor Gray
    Write-Host ""
    
    # Poll for build completion
    $buildComplete = $false
    $buildStatus = ""
    $startTime = Get-Date
    while (-not $buildComplete) {
        Start-Sleep -Seconds 30
        $buildInfo = aws codebuild batch-get-builds --ids $buildResult --region $currentRegion --no-cli-pager --query 'builds[0].buildStatus' --output text 2>$null
        
        if ($buildInfo -eq "SUCCEEDED") {
            $buildComplete = $true
            $buildStatus = "SUCCEEDED"
            Write-Host "Build completed successfully!" -ForegroundColor Green
        } elseif ($buildInfo -eq "FAILED" -or $buildInfo -eq "FAULT" -or $buildInfo -eq "STOPPED" -or $buildInfo -eq "TIMED_OUT") {
            $buildComplete = $true
            $buildStatus = $buildInfo
            Write-Host "Build ended with status: $buildInfo" -ForegroundColor Red
        } else {
            $elapsed = [math]::Round(((Get-Date) - $startTime).TotalMinutes, 1)
            Write-Host "  Build in progress... (Status: $buildInfo, Elapsed: $elapsed min)" -ForegroundColor Gray
        }
    }
    
    if ($buildStatus -eq "SUCCEEDED") {
        # Create local directory for documentation
        $localDocsDir = ".\generated-docs-$jobId"
        Write-Host ""
        Write-Host "Downloading documentation to: $localDocsDir" -ForegroundColor Yellow
        
        New-Item -ItemType Directory -Path $localDocsDir -Force | Out-Null
        aws s3 cp "s3://$outputBucket/documentation/$jobId/" $localDocsDir --recursive --region $currentRegion --no-cli-pager
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            Write-Host "Documentation downloaded successfully!" -ForegroundColor Green
            Write-Host "Location: $localDocsDir" -ForegroundColor White
            
            # List downloaded files
            Write-Host ""
            Write-Host "Downloaded files:" -ForegroundColor Yellow
            Get-ChildItem -Path $localDocsDir -Recurse -File | ForEach-Object {
                Write-Host "  $($_.FullName)" -ForegroundColor Gray
            }
        } else {
            Write-Host "Failed to download documentation" -ForegroundColor Red
        }
    }
} else {
    Write-Host ""
    Write-Host "To download documentation later (after build completes):" -ForegroundColor Yellow
    Write-Host "  aws s3 cp s3://$outputBucket/documentation/$jobId/ ./generated-docs --recursive" -ForegroundColor Gray
}