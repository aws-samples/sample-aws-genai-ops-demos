#!/bin/bash
# Build and push the Prowler scanner container image via CodeBuild.
#
# Usage: ./build-scanner-image.sh <raw_reports_bucket> <build_project_name>

set -euo pipefail

if [ $# -ne 2 ]; then
    echo "Usage: $0 <raw_reports_bucket> <build_project_name>"
    exit 1
fi

BUCKET="$1"
PROJECT="$2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCANNER_DIR="$(cd "$SCRIPT_DIR/../scanner" && pwd)"

TMPZIP="/tmp/prowler-scanner-source.zip"
rm -f "$TMPZIP"
echo "[scanner-build] zipping $SCANNER_DIR..."
(cd "$SCANNER_DIR" && zip -r "$TMPZIP" . >/dev/null)

echo "[scanner-build] uploading to s3://$BUCKET/codebuild-sources/scanner.zip..."
aws s3 cp "$TMPZIP" "s3://$BUCKET/codebuild-sources/scanner.zip" >/dev/null

echo "[scanner-build] starting CodeBuild project $PROJECT..."
BUILD_ID=$(aws codebuild start-build \
    --project-name "$PROJECT" \
    --source-type-override S3 \
    --source-location-override "$BUCKET/codebuild-sources/scanner.zip" \
    --query 'build.id' \
    --output text)
echo "[scanner-build] build id: $BUILD_ID"

echo "[scanner-build] waiting for build to complete..."
START_TIME=$(date +%s)
while true; do
    STATUS=$(aws codebuild batch-get-builds --ids "$BUILD_ID" --query 'builds[0].buildStatus' --output text)
    PHASE=$(aws codebuild batch-get-builds --ids "$BUILD_ID" --query 'builds[0].currentPhase' --output text)
    ELAPSED=$(( $(date +%s) - START_TIME ))
    printf "  [%02dm%02ds] %s / %s\n" $((ELAPSED/60)) $((ELAPSED%60)) "$STATUS" "$PHASE"
    case "$STATUS" in
        SUCCEEDED) echo "[scanner-build] image built and pushed."; break ;;
        FAILED|FAULT|TIMED_OUT|STOPPED) echo "[scanner-build] build $STATUS — see CloudWatch logs for /aws/codebuild/$PROJECT"; exit 1 ;;
    esac
    sleep 10
done
