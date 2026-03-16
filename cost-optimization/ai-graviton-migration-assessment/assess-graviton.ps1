#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Deploy Graviton Migration Assessment using CDK and CodeBuild

.DESCRIPTION
    This script deploys a CodeBuild-based solution that runs AWS Transform to generate
    comprehensive Graviton migration assessments from any Git repository. Infrastructure is deployed via CDK.

.PARAMETER RepositoryUrl
    Git repository URL to analyze (default: AWS sample repository)

.PARAMETER SkipSetup
    Skip infrastructure deployment, only start an assessment (requires infrastructure already deployed)

.EXAMPLE
    .\assess-graviton.ps1
    .\assess-graviton.ps1 -RepositoryUrl "https://github.com/owner/repo"
    .\assess-graviton.ps1 -SkipSetup

.NOTES
    This script uses the AWS region configured in your AWS CLI profile.
    To set your region: aws configure set region <your-region>
#>

param(
    [string]$RepositoryUrl = "",
    [switch]$SkipSetup = $false
)

$ErrorActionPreference = "Stop"
$defaultRepo = "https://github.com/aws-samples/sample-serverless-digital-asset-payments"

Write-Host "=== AI-Powered Graviton Migration Assessment ===" -ForegroundColor Cyan
Write-Host "Analyzes codebases for Graviton migration opportunities and cost optimization" -ForegroundColor Green
Write-Host ""
Write-Host "Uses AWS Transform with specialized Graviton migration context:" -ForegroundColor Yellow
Write-Host "  - Architecture compatibility assessment for ARM64" -ForegroundColor Gray
Write-Host "  - Cost savings analysis (typically 10-20% reduction)" -ForegroundColor Gray
Write-Host "  - Migration complexity scoring and phased approach" -ForegroundColor Gray
Write-Host "  - Expected time: ~60 minutes" -ForegroundColor Gray
Write-Host ""

$transformationType = "graviton"
$buildspecFile = "buildspec.yml"
$expectedTime = "~60 minutes"

Write-Host ""

# Check if repository URL was provided
if ([string]::IsNullOrEmpty($RepositoryUrl)) {
    Write-Host "No repository URL specified. Assessment will be generated based on the default sample repository:" -ForegroundColor Yellow
    Write-Host "  $defaultRepo" -ForegroundColor White
    Write-Host ""
    Write-Host "To analyze your own repository, run:" -ForegroundColor Gray
    Write-Host "  .\assess-graviton.ps1 -RepositoryUrl `"https://github.com/owner/repo`"" -ForegroundColor White
    Write-Host ""
    $RepositoryUrl = $defaultRepo
}

# Get script directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$cdkDir = Join-Path $scriptDir "infrastructure/cdk"
$sharedScriptsDir = Join-Path $scriptDir "..\..\shared\scripts"

if (-not $SkipSetup) {
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

    # Use region from prerequisites check
    $currentRegion = $global:AWS_REGION

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
    $stackName = "GravitonAssessmentStack-$currentRegion"
    $outputBucket = aws cloudformation describe-stacks --stack-name $stackName --region $currentRegion --no-cli-pager --query "Stacks[0].Outputs[?OutputKey=='OutputBucketName'].OutputValue" --output text
    $projectName = aws cloudformation describe-stacks --stack-name $stackName --region $currentRegion --no-cli-pager --query "Stacks[0].Outputs[?OutputKey=='CodeBuildProjectName'].OutputValue" --output text
    if ([string]::IsNullOrEmpty($outputBucket) -or [string]::IsNullOrEmpty($projectName)) {
        Write-Host "      ❌ Failed to get stack outputs" -ForegroundColor Red
        exit 1
    }
    Write-Host "      Output Bucket: $outputBucket" -ForegroundColor Gray
    Write-Host "      CodeBuild Project: $projectName" -ForegroundColor Gray

    # Upload buildspec to S3
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

    # Upload custom transformation definition
    Write-Host ""
    Write-Host "Uploading custom Graviton transformation definition..." -ForegroundColor Yellow
    $gravitonTransformDir = Join-Path $scriptDir "graviton-transformation-definition"
    aws s3 cp $gravitonTransformDir "s3://$outputBucket/graviton-transformation-definition/" --recursive --region $currentRegion --no-cli-pager | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "      ✓ Custom transformation definition uploaded successfully" -ForegroundColor Green
    } else {
        Write-Host "      ❌ Failed to upload custom transformation definition" -ForegroundColor Red
        exit 1
    }

    # Upload knowledge items
    Write-Host ""
    Write-Host "Uploading Graviton knowledge items..." -ForegroundColor Yellow
    $knowledgeItemsDir = Join-Path $scriptDir "knowledge-items"
    aws s3 cp $knowledgeItemsDir "s3://$outputBucket/knowledge-items/" --recursive --region $currentRegion --no-cli-pager | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "      ✓ Knowledge items uploaded successfully" -ForegroundColor Green
    } else {
        Write-Host "      ❌ Failed to upload knowledge items" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "[SETUP] Skipping infrastructure deployment..." -ForegroundColor Yellow
    # Get region directly (prerequisites check was skipped)
    $currentRegion = $env:AWS_DEFAULT_REGION
    if ([string]::IsNullOrEmpty($currentRegion)) {
        $currentRegion = aws configure get region 2>$null
    }
    if ([string]::IsNullOrEmpty($currentRegion)) {
        Write-Host "ERROR: AWS region not configured" -ForegroundColor Red
        Write-Host "Please configure your AWS region: aws configure set region <your-region>" -ForegroundColor Yellow
        exit 1
    }
    
    # Get stack outputs
    Write-Host ""
    Write-Host "Getting stack outputs..." -ForegroundColor Yellow
    $stackName = "GravitonAssessmentStack-$currentRegion"
    $outputBucket = aws cloudformation describe-stacks --stack-name $stackName --region $currentRegion --no-cli-pager --query "Stacks[0].Outputs[?OutputKey=='OutputBucketName'].OutputValue" --output text 2>$null
    $projectName = aws cloudformation describe-stacks --stack-name $stackName --region $currentRegion --no-cli-pager --query "Stacks[0].Outputs[?OutputKey=='CodeBuildProjectName'].OutputValue" --output text 2>$null
    
    if ([string]::IsNullOrEmpty($outputBucket) -or [string]::IsNullOrEmpty($projectName)) {
        Write-Host "      ❌ Stack not found. Please deploy infrastructure first:" -ForegroundColor Red
        Write-Host "      .\assess-graviton.ps1" -ForegroundColor Gray
        exit 1
    }
    Write-Host "      Output Bucket: $outputBucket" -ForegroundColor Gray
    Write-Host "      CodeBuild Project: $projectName" -ForegroundColor Gray
}
# Start Graviton assessment build
Write-Host ""
Write-Host "Starting Graviton migration assessment..." -ForegroundColor Yellow
Write-Host "      Repository: $RepositoryUrl" -ForegroundColor Gray
Write-Host "      Expected time: $expectedTime" -ForegroundColor Gray

# Generate unique job ID
$jobId = "graviton-assessment-$(Get-Date -Format 'yyyyMMdd-HHmmss')"

# Start build with environment variable overrides
$buildResult = aws codebuild start-build `
    --project-name $projectName `
    --region $currentRegion `
    --no-cli-pager `
    --environment-variables-override "name=REPOSITORY_URL,value=$RepositoryUrl,type=PLAINTEXT" "name=OUTPUT_BUCKET,value=$outputBucket,type=PLAINTEXT" "name=JOB_ID,value=$jobId,type=PLAINTEXT" `
    --query 'build.id' --output text

if ($LASTEXITCODE -eq 0) {
    Write-Host "      ✓ Assessment started: $buildResult" -ForegroundColor Green
} else {
    Write-Host "      ❌ Failed to start assessment" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== Deployment Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Resources deployed via CDK:" -ForegroundColor Yellow
Write-Host "  - S3 Bucket: $outputBucket" -ForegroundColor White
Write-Host "  - CodeBuild Project: $projectName" -ForegroundColor White
Write-Host ""
Write-Host "Monitor assessment progress:" -ForegroundColor Yellow
Write-Host "  aws codebuild batch-get-builds --ids $buildResult --region $currentRegion --no-cli-pager" -ForegroundColor Gray
Write-Host ""
Write-Host "Stream build logs:" -ForegroundColor Yellow
Write-Host "  aws logs tail /aws/codebuild/$projectName --follow --region $currentRegion" -ForegroundColor Gray
Write-Host ""
Write-Host "Check generated assessment:" -ForegroundColor Yellow
Write-Host "  aws s3 ls s3://$outputBucket/assessments/$jobId/ --recursive" -ForegroundColor Gray
Write-Host ""
Write-Host "Currently analyzing:" -ForegroundColor Yellow
Write-Host "  $RepositoryUrl" -ForegroundColor White
Write-Host ""
Write-Host "Generate assessment for a different repository:" -ForegroundColor Yellow
Write-Host "  aws codebuild start-build --project-name $projectName --region $currentRegion --no-cli-pager ``" -ForegroundColor Gray
Write-Host "    --environment-variables-override name=REPOSITORY_URL,value=https://github.com/owner/repo" -ForegroundColor Gray

# Offer to wait and download assessment
Write-Host ""
Write-Host "=== Download Assessment ===" -ForegroundColor Cyan
Write-Host "The assessment is expected to take $expectedTime to complete depending on repository size." -ForegroundColor Yellow
Write-Host ""
$waitChoice = Read-Host "Would you like to wait for the assessment to complete and download the results? (y/n)"

if ($waitChoice -eq "y" -or $waitChoice -eq "Y") {
    Write-Host ""
    Write-Host "Waiting for assessment to complete..." -ForegroundColor Yellow
    Write-Host "You can press Ctrl+C to cancel and download later using:" -ForegroundColor Gray
    Write-Host "  aws s3 cp s3://$outputBucket/assessments/$jobId/ ./graviton-assessment --recursive" -ForegroundColor Gray
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
            Write-Host "Assessment completed successfully!" -ForegroundColor Green
        } elseif ($buildInfo -eq "FAILED" -or $buildInfo -eq "FAULT" -or $buildInfo -eq "STOPPED" -or $buildInfo -eq "TIMED_OUT") {
            $buildComplete = $true
            $buildStatus = $buildInfo
            Write-Host "Assessment ended with status: $buildInfo" -ForegroundColor Red
        } else {
            $elapsed = [math]::Round(((Get-Date) - $startTime).TotalMinutes, 1)
            Write-Host "  Assessment in progress... (Status: $buildInfo, Elapsed: $elapsed min)" -ForegroundColor Gray
        }
    }
    
    if ($buildStatus -eq "SUCCEEDED") {
        # Create local directory for assessment
        $localAssessmentDir = ".\graviton-assessment-$jobId"
        Write-Host ""
        Write-Host "Downloading assessment to: $localAssessmentDir" -ForegroundColor Yellow
        
        New-Item -ItemType Directory -Path $localAssessmentDir -Force | Out-Null
        aws s3 cp "s3://$outputBucket/assessments/$jobId/" $localAssessmentDir --recursive --region $currentRegion --no-cli-pager
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            Write-Host "Graviton migration assessment downloaded successfully!" -ForegroundColor Green
            Write-Host "Location: $localAssessmentDir" -ForegroundColor White
            
            # List downloaded files
            Write-Host ""
            Write-Host "Assessment files:" -ForegroundColor Yellow
            Get-ChildItem -Path $localAssessmentDir -Recurse -File | ForEach-Object {
                Write-Host "  $($_.FullName)" -ForegroundColor Gray
            }
            
            Write-Host ""
            Write-Host "=== Next Steps ===" -ForegroundColor Cyan
            Write-Host "1. Review the executive summary in README.md" -ForegroundColor Yellow
            Write-Host "2. Examine cost savings projections in cost-analysis/" -ForegroundColor Yellow
            Write-Host "3. Check migration complexity in migration-plan/" -ForegroundColor Yellow
            Write-Host "4. Identify pilot candidates for initial testing" -ForegroundColor Yellow
        } else {
            Write-Host "Failed to download assessment" -ForegroundColor Red
        }
    }
} else {
    Write-Host ""
    Write-Host "To download assessment later (after build completes):" -ForegroundColor Yellow
    Write-Host "  aws s3 cp s3://$outputBucket/assessments/$jobId/ ./graviton-assessment --recursive" -ForegroundColor Gray
}