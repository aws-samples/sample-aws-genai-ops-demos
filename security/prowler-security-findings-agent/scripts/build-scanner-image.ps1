param(
    [Parameter(Mandatory=$true)][string]$RawBucket,
    [Parameter(Mandatory=$true)][string]$BuildProject
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ScannerDir = (Resolve-Path "$ScriptDir\..\scanner").Path
$TmpZip = Join-Path ([System.IO.Path]::GetTempPath()) "prowler-scanner-source.zip"

if (Test-Path $TmpZip) { Remove-Item $TmpZip -Force }
Write-Host "[scanner-build] zipping $ScannerDir..."
Compress-Archive -Path "$ScannerDir\*" -DestinationPath $TmpZip -Force

Write-Host "[scanner-build] uploading to s3://$RawBucket/codebuild-sources/scanner.zip..."
aws s3 cp $TmpZip "s3://$RawBucket/codebuild-sources/scanner.zip" | Out-Null

Write-Host "[scanner-build] starting CodeBuild..."
$BuildId = aws codebuild start-build --project-name $BuildProject --source-type-override S3 --source-location-override "$RawBucket/codebuild-sources/scanner.zip" --query 'build.id' --output text
Write-Host "[scanner-build] build id: $BuildId"

$Start = Get-Date
while ($true) {
    $Status = aws codebuild batch-get-builds --ids $BuildId --query 'builds[0].buildStatus' --output text
    $Phase = aws codebuild batch-get-builds --ids $BuildId --query 'builds[0].currentPhase' --output text
    [int]$Elapsed = ((Get-Date) - $Start).TotalSeconds
    $Mins = [int][math]::Floor($Elapsed / 60)
    $Secs = [int]($Elapsed % 60)
    Write-Host ("  [{0:d2}m{1:d2}s] {2} / {3}" -f $Mins, $Secs, $Status, $Phase)
    if ($Status -eq "SUCCEEDED") { Write-Host "[scanner-build] image pushed."; break }
    if ($Status -in @("FAILED","FAULT","TIMED_OUT","STOPPED")) { throw "Build $Status — see CloudWatch /aws/codebuild/$BuildProject" }
    Start-Sleep -Seconds 10
}
