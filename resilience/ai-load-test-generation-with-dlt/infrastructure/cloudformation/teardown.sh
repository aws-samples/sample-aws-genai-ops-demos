#!/usr/bin/env bash
# Tear down the DLT-agent AgentCore deployment created by deploy.sh.
#
# Deletes, in order:
#   1. Empties the spec-input S3 bucket (a stack resource, versioned) so the
#      stack can delete it.
#   2. Deletes the CloudFormation stack (runtime, VPC, NAT, endpoints, ECR,
#      roles). ECR is EmptyOnDelete; NAT/EIP/endpoints are removed by CFN.
#   3. Empties + deletes the source bucket (script-managed, NOT a stack resource).
#
# DESTRUCTIVE. Requires explicit confirmation (type "delete") or --yes.
# Only touches the AGENT stack and its own buckets — never the DLT stack/buckets.
#
# Usage:
#   infrastructure/cloudformation/teardown.sh [--stack-name ai-load-test-gen] [--region <region>] \
#                     [--source-bucket NAME] [--yes]
set -euo pipefail

REGION=""                  # explicit --region override; empty = auto-detect (env → CLI config → us-east-1)
STACK_NAME="ai-load-test-gen"
SOURCE_BUCKET=""
ASSUME_YES="false"

while [ $# -gt 0 ]; do
  case "$1" in
    --region) REGION="$2"; shift 2 ;;
    --stack-name) STACK_NAME="$2"; shift 2 ;;
    --source-bucket) SOURCE_BUCKET="$2"; shift 2 ;;
    --yes) ASSUME_YES="true"; shift 1 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Region: explicit --region wins; else shared detection (env → CLI config → us-east-1).
# Prefer shared/utils/aws-utils.sh when present (monorepo); fall back to an inline
# probe so the script still works if the demo is used standalone. Must match the
# region deploy.sh used, or the DescribeStacks lookup below finds nothing.
if [ -z "$REGION" ]; then
  _HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  _AWS_UTILS="$_HERE/../../../../shared/utils/aws-utils.sh"
  if [ -f "$_AWS_UTILS" ]; then
    # shellcheck disable=SC1090
    source "$_AWS_UTILS"
    REGION="$(get_aws_region)"
  else
    REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-}}"
    [ -z "$REGION" ] && REGION="$(aws configure get region 2>/dev/null || true)"
    [ -z "$REGION" ] && REGION="us-east-1"
  fi
fi

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
[ -z "$SOURCE_BUCKET" ] && SOURCE_BUCKET="${STACK_NAME}-src-${ACCOUNT_ID}-${REGION}"

# Resolve the spec-input bucket from the stack BEFORE deleting the stack.
SPEC_BUCKET="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='SpecInputBucketName'].OutputValue" \
  --output text 2>/dev/null || echo "")"
[ "$SPEC_BUCKET" = "None" ] && SPEC_BUCKET=""

# empty_bucket <name> — remove all objects AND versions/delete-markers.
empty_bucket() {
  local b="$1"
  aws s3api head-bucket --bucket "$b" --region "$REGION" 2>/dev/null || { echo "  (bucket $b not found, skip)"; return 0; }
  echo "  emptying s3://$b ..."
  # current objects
  aws s3 rm "s3://$b" --recursive --region "$REGION" >/dev/null 2>&1 || true
  # versions + delete markers (for versioned buckets)
  local payload
  while :; do
    payload="$(aws s3api list-object-versions --bucket "$b" --region "$REGION" --max-items 500 \
      --query '{Objects: (Versions[].{Key:Key,VersionId:VersionId} || `[]`) [] , DeleteMarkers: (DeleteMarkers[].{Key:Key,VersionId:VersionId} || `[]`)[]}' \
      --output json 2>/dev/null || echo '{}')"
    # build a delete request combining versions + delete markers
    local del
    del="$(python3 - "$payload" <<'PY'
import json,sys
d=json.loads(sys.argv[1] or "{}")
objs=(d.get("Objects") or [])+(d.get("DeleteMarkers") or [])
print(json.dumps({"Objects":[{"Key":o["Key"],"VersionId":o["VersionId"]} for o in objs if o]}) if objs else "")
PY
)"
    [ -z "$del" ] && break
    aws s3api delete-objects --bucket "$b" --region "$REGION" --delete "$del" >/dev/null 2>&1 || break
  done
}

echo "About to DELETE (region=$REGION):"
echo "  - CloudFormation stack : $STACK_NAME"
echo "  - spec-input bucket     : ${SPEC_BUCKET:-<none>} (emptied, then removed by the stack)"
echo "  - source bucket         : $SOURCE_BUCKET (emptied + deleted)"
echo "  NOTE: the DLT stack and DLT buckets are NOT touched."
if [ "$ASSUME_YES" != "true" ]; then
  printf 'Type "delete" to proceed: '
  read -r CONFIRM
  [ "$CONFIRM" = "delete" ] || { echo "aborted."; exit 1; }
fi

# 1) empty spec-input bucket so the stack delete does not fail on a non-empty bucket
[ -n "$SPEC_BUCKET" ] && empty_bucket "$SPEC_BUCKET"

# 2) delete the stack and wait
echo "deleting stack $STACK_NAME ..."
aws cloudformation delete-stack --region "$REGION" --stack-name "$STACK_NAME"
echo "waiting for stack deletion (this can take several minutes; NAT/ENIs are slow)..."
aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$STACK_NAME" \
  && echo "stack deleted." \
  || echo "WARN: wait ended without confirmation — check the stack status in the console."

# 3) delete the script-managed source bucket (not part of the stack)
empty_bucket "$SOURCE_BUCKET"
aws s3api head-bucket --bucket "$SOURCE_BUCKET" --region "$REGION" 2>/dev/null \
  && aws s3 rb "s3://$SOURCE_BUCKET" --region "$REGION" 2>/dev/null \
  && echo "source bucket deleted." || echo "  (source bucket already gone)"

echo "Teardown complete."
