#!/usr/bin/env bash
# Deploy the AI Load Test Generator Agent to AgentCore Runtime (Form B build + Form A spec input).
#
# WHAT THIS DOES (pre-deploy upload model):
#   1. resolve DLT inputs from just the DLT stack name + region
#      (StackId, API endpoint -> execute-api ARN, scenarios bucket -> S3 ARN)
#   2. let the operator pick the Bedrock model(s) from the profiles available
#      in the Bedrock region (falls back to manual entry if listing fails)
#   3. zip the agent source (git archive => tracked files, stable hash)
#   4. ensure an S3 source bucket exists, upload the zip there
#   5. deploy the CloudFormation stack, which builds the ARM64 image in-stack
#      (CodeBuild reads the zip from S3) and creates the private runtime.
#
# There is NO `aws cloudformation package` step: the custom-resource Lambda is
# inlined in the template, so the only artifact uploaded is the source zip.
#
# Usage (script-only, no DLT):
#   infrastructure/cloudformation/deploy.sh          # prompts for a Bedrock model, deploys a generator agent
#
# Usage (with DLT wired):
#   infrastructure/cloudformation/deploy.sh --dlt-stack LaunchWizard-dlt-poc --dlt-region us-west-2
#     # -> resolves DLT ARNs automatically, then prompts you to pick a Bedrock model
#
# Options:
#   --dlt-stack NAME           (optional) DLT CloudFormation stack name; omit for
#                              a script-only agent. Connect DLT later by re-running
#                              with this flag (a CloudFormation update wires it in).
#   --dlt-region REGION        required only when --dlt-stack is given
#   --bedrock-region REGION    Bedrock invoke region        [default: --region]
#   --bedrock-model ID         non-interactive: primary model/profile id
#   --bedrock-fallback ID      non-interactive: fallback model/profile id
#   --region REGION            agent stack region  [default: auto-detect from env/AWS config, else us-east-1]
#   --stack-name NAME          agent stack name       [default: ai-load-test-gen]
#   --source-bucket NAME       [default: <stack>-src-<account>-<region>]
#   --network-mode public|vpc  runtime network placement  [default: public]
#                              public = AWS-managed egress, no VPC created;
#                              vpc    = private VPC (NAT + endpoints + SG).
#                              Inbound is IAM SigV4 in both modes.
#   --create-vpc true|false    new vs existing VPC (vpc mode only) [default: true]
#   --enable-xray true|false   X-Ray opt-in (needs Transaction Search)
#   --force-xray-without-ts    allow --enable-xray true even when TS is off
#   # advanced manual overrides (skip auto-derivation):
#   --dlt-api-arn ARN  --dlt-bucket-arn ARN  --dlt-stack-arn ARN
#   --bedrock-profiles 'arn1,arn2'   (IAM invoke scope; else derived from picks)
set -euo pipefail

REGION=""                  # explicit --region override; empty = auto-detect (env → CLI config → us-east-1)
STACK_NAME="ai-load-test-gen"
NETWORK_MODE="public"
CREATE_VPC="true"
SOURCE_BUCKET=""
ENABLE_XRAY="false"        # X-Ray perms are OFF unless explicitly requested
FORCE_XRAY_NO_TS="false"   # allow --enable-xray true even when TS is off

# DLT + Bedrock inputs (ARNs are optional overrides; else auto-derived)
DLT_STACK=""
DLT_REGION=""
BEDROCK_REGION=""
DLT_API_ARN=""
DLT_BUCKET_ARN=""
DLT_STACK_ARN=""
BEDROCK_PROFILES=""
BEDROCK_MODEL=""
BEDROCK_FALLBACK=""

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEMPLATE="$REPO_ROOT/infrastructure/cloudformation/template.yaml"

while [ $# -gt 0 ]; do
  case "$1" in
    --dlt-stack) DLT_STACK="$2"; shift 2 ;;
    --dlt-region) DLT_REGION="$2"; shift 2 ;;
    --bedrock-region) BEDROCK_REGION="$2"; shift 2 ;;
    --bedrock-model) BEDROCK_MODEL="$2"; shift 2 ;;
    --bedrock-fallback) BEDROCK_FALLBACK="$2"; shift 2 ;;
    --dlt-api-arn) DLT_API_ARN="$2"; shift 2 ;;
    --dlt-bucket-arn) DLT_BUCKET_ARN="$2"; shift 2 ;;
    --dlt-stack-arn) DLT_STACK_ARN="$2"; shift 2 ;;
    --bedrock-profiles) BEDROCK_PROFILES="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --stack-name) STACK_NAME="$2"; shift 2 ;;
    --network-mode) NETWORK_MODE="$2"; shift 2 ;;
    --create-vpc) CREATE_VPC="$2"; shift 2 ;;
    --source-bucket) SOURCE_BUCKET="$2"; shift 2 ;;
    --enable-xray) ENABLE_XRAY="$2"; shift 2 ;;
    --force-xray-without-ts) FORCE_XRAY_NO_TS="true"; shift 1 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Region: explicit --region wins; else shared detection (env → CLI config → us-east-1).
# Prefer shared/utils/aws-utils.sh when present (monorepo); fall back to an inline
# probe so the script still works if the demo is used standalone.
if [ -z "$REGION" ]; then
  _AWS_UTILS="$REPO_ROOT/../../shared/utils/aws-utils.sh"
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

[ -z "$BEDROCK_REGION" ] && BEDROCK_REGION="$REGION"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

# ---------------------------------------------------------------------------
# 1) DLT is OPTIONAL. The agent's core job is generating test scripts; wiring
#    it to a DLT stack (to launch real load tests) is opt-in. Resolve DLT
#    inputs only when a DLT stack was supplied — otherwise deploy a script-only
#    agent with no DLT-scoped IAM. To connect DLT later, just re-run deploy.sh
#    with --dlt-stack/--dlt-region (a CloudFormation update adds the env vars
#    and the DLT IAM statements; the runtime rolls to a new version).
# ---------------------------------------------------------------------------
if [ -n "$DLT_STACK" ]; then
: "${DLT_REGION:?--dlt-region required when --dlt-stack is given}"
echo "Resolving DLT stack '$DLT_STACK' in $DLT_REGION ..."

if [ -z "$DLT_STACK_ARN" ]; then
  DLT_STACK_ARN="$(aws cloudformation describe-stacks \
    --stack-name "$DLT_STACK" --region "$DLT_REGION" \
    --query 'Stacks[0].StackId' --output text 2>/dev/null || true)"
fi
if [ -z "$DLT_STACK_ARN" ] || [ "$DLT_STACK_ARN" = "None" ]; then
  echo "ERROR: cannot describe DLT stack '$DLT_STACK' in $DLT_REGION." >&2
  echo "       Check the name/region and your credentials." >&2
  exit 4
fi

if [ -z "$DLT_API_ARN" ]; then
  # The API output key carries a logical-ID hash (e.g. DLTApiEndpointD98B09AC),
  # so match by prefix, not an exact key.
  API_URL="$(aws cloudformation describe-stacks \
    --stack-name "$DLT_STACK" --region "$DLT_REGION" \
    --query "Stacks[0].Outputs[?starts_with(OutputKey, 'DLTApiEndpoint')].OutputValue | [0]" \
    --output text 2>/dev/null || true)"
  if [ -z "$API_URL" ] || [ "$API_URL" = "None" ]; then
    echo "ERROR: no DLTApiEndpoint* output on stack '$DLT_STACK'; is this a DLT stack?" >&2
    exit 4
  fi
  # https://<apiId>.execute-api.<region>.amazonaws.com/<stage>/
  _hostpath="${API_URL#https://}"
  API_ID="${_hostpath%%.*}"
  _path="${API_URL#*amazonaws.com/}"
  API_STAGE="${_path%%/*}"
  [ -z "$API_STAGE" ] && API_STAGE="*"
  # Scope to the stage (covers POST /scenarios, GET|POST /scenarios/*, GET /regions);
  # tighter than the all-stages /*/*/* form.
  DLT_API_ARN="arn:aws:execute-api:${DLT_REGION}:${ACCOUNT_ID}:${API_ID}/${API_STAGE}/*"
fi

if [ -z "$DLT_BUCKET_ARN" ]; then
  SCEN_BUCKET="$(aws cloudformation describe-stacks \
    --stack-name "$DLT_STACK" --region "$DLT_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ScenariosBucket'].OutputValue | [0]" \
    --output text 2>/dev/null || true)"
  if [ -z "$SCEN_BUCKET" ] || [ "$SCEN_BUCKET" = "None" ]; then
    echo "ERROR: no ScenariosBucket output on stack '$DLT_STACK'; is this a DLT stack?" >&2
    exit 4
  fi
  DLT_BUCKET_ARN="arn:aws:s3:::${SCEN_BUCKET}"
fi

echo "  stack ARN : $DLT_STACK_ARN"
echo "  api  ARN  : $DLT_API_ARN"
echo "  bucket ARN: $DLT_BUCKET_ARN"
else
  echo "DLT not connected (script-only agent)."
  echo "  -> to wire DLT later, re-run: infrastructure/cloudformation/deploy.sh --dlt-stack <name> --dlt-region <region> ..."
fi

# ---------------------------------------------------------------------------
# 2) Pick the Bedrock model(s). List the ACTIVE inference profiles available in
#    the Bedrock region and let the operator choose; if listing fails (perms /
#    unsupported) or a non-interactive run supplies --bedrock-model, use that.
#    The chosen id(s) set BEDROCK_MODEL_PRIMARY/FALLBACK (what the agent invokes)
#    AND, unless --bedrock-profiles is given, the IAM invoke scope.
# ---------------------------------------------------------------------------
SEL_PRIMARY=""
SEL_FALLBACK=""
IDS=()
NAMES=()

_have_tty() {  # true only if /dev/tty can actually be opened for reading
  # -e is not enough: in a background job or a CI runner there is no
  # controlling terminal, yet the /dev/tty device node still exists, so the
  # existence test passes and the open then fails with ENXIO ("Device not
  # configured"). Try the open instead of asking whether the file is there.
  { : < /dev/tty; } 2>/dev/null
}

_read_tty() {  # $1=prompt ; echoes the entered line (reads from the terminal)
  local _ans=""
  if _have_tty; then
    read -r -p "$1" _ans < /dev/tty || true
  fi
  printf '%s' "$_ans"
}

_choice_to_id() {  # $1=number ; echoes id or __INVALID__
  local n="$1"
  case "$n" in ''|*[!0-9]*) echo "__INVALID__"; return ;; esac
  if [ "$n" -ge 1 ] && [ "$n" -le "${#IDS[@]}" ]; then
    echo "${IDS[$((n-1))]}"
  else
    echo "__INVALID__"
  fi
}

select_bedrock_models() {
  # Full manual override: caller supplied both the IAM scope and the model id.
  if [ -n "$BEDROCK_PROFILES" ] && [ -n "$BEDROCK_MODEL" ]; then
    SEL_PRIMARY="$BEDROCK_MODEL"
    SEL_FALLBACK="$BEDROCK_FALLBACK"
    return
  fi

  local tsv
  tsv="$(aws bedrock list-inference-profiles --region "$BEDROCK_REGION" \
        --query "inferenceProfileSummaries[?status=='ACTIVE'].[inferenceProfileId,inferenceProfileName]" \
        --output text 2>/dev/null || true)"

  if [ -z "$tsv" ]; then
    echo "WARN: could not list inference profiles in $BEDROCK_REGION (permission or unsupported region)." >&2
    if [ -n "$BEDROCK_MODEL" ]; then
      SEL_PRIMARY="$BEDROCK_MODEL"; SEL_FALLBACK="$BEDROCK_FALLBACK"; return
    fi
    if ! _have_tty; then
      echo "ERROR: listing failed and no TTY; pass --bedrock-model <id> [--bedrock-fallback <id>]." >&2
      exit 5
    fi
    SEL_PRIMARY="$(_read_tty 'Enter primary model/inference-profile id: ')"
    [ -z "$SEL_PRIMARY" ] && { echo "ERROR: primary model id is required." >&2; exit 5; }
    SEL_FALLBACK="$(_read_tty 'Enter fallback id (Enter to reuse primary): ')"
    return
  fi

  # Parse the tab-separated id/name rows into parallel arrays (bash 3.2 safe).
  while IFS=$'\t' read -r _id _name; do
    [ -z "$_id" ] && continue
    IDS+=("$_id"); NAMES+=("$_name")
  done <<EOF
$tsv
EOF

  # Non-interactive: honor --bedrock-model as the primary.
  if [ -n "$BEDROCK_MODEL" ]; then
    SEL_PRIMARY="$BEDROCK_MODEL"; SEL_FALLBACK="$BEDROCK_FALLBACK"; return
  fi
  if ! _have_tty; then
    echo "ERROR: no TTY for interactive model selection; pass --bedrock-model <id> [--bedrock-fallback <id>]." >&2
    exit 5
  fi

  echo "" >&2
  echo "Bedrock inference profiles available in $BEDROCK_REGION:" >&2
  local i=1 idx
  for idx in "${!IDS[@]}"; do
    printf "  %3d) %-55s %s\n" "$i" "${IDS[$idx]}" "${NAMES[$idx]}" >&2
    i=$((i+1))
  done
  echo "" >&2

  # Primary (required, must be valid). Bounded: a prompt that can never be
  # answered must fail the deploy, not spin. _have_tty above rules out the
  # no-terminal case, so this bound only catches a terminal that keeps
  # returning something unusable (closed pipe, non-numeric paste).
  local tries=0
  while [ "$tries" -lt 5 ]; do
    local pn pid
    tries=$((tries+1))
    pn="$(_read_tty 'Select PRIMARY model number: ')"
    pid="$(_choice_to_id "$pn")"
    if [ "$pid" != "__INVALID__" ]; then SEL_PRIMARY="$pid"; break; fi
    echo "  invalid selection; enter a number from the list." >&2
  done
  if [ -z "$SEL_PRIMARY" ]; then
    echo "ERROR: no valid model selection after $tries attempts; pass --bedrock-model <id> [--bedrock-fallback <id>]." >&2
    exit 5
  fi

  # Fallback (optional: Enter = reuse primary). Bounded like the primary; an
  # exhausted count falls back to reusing the primary, which is what an empty
  # answer means anyway, so it never blocks the deploy.
  tries=0
  while [ "$tries" -lt 5 ]; do
    local fn fid
    tries=$((tries+1))
    fn="$(_read_tty 'Select FALLBACK model number (Enter to reuse primary): ')"
    if [ -z "$fn" ]; then SEL_FALLBACK=""; break; fi
    fid="$(_choice_to_id "$fn")"
    if [ "$fid" != "__INVALID__" ]; then SEL_FALLBACK="$fid"; break; fi
    echo "  invalid selection; enter a number from the list (or Enter)." >&2
  done
}

select_bedrock_models

# Keep primary/fallback consistent with the IAM scope: an empty fallback reuses
# the primary so the agent never falls back to a model IAM does not allow.
[ -z "$SEL_FALLBACK" ] && SEL_FALLBACK="$SEL_PRIMARY"

# Derive the IAM invoke scope from the chosen ids unless overridden. CRIS
# (cross-region inference) requires bedrock:InvokeModel on BOTH the inference
# profile ARN AND the underlying foundation-model ARNs of every region the
# profile routes to — get-inference-profile lists those model ARNs.
if [ -z "$BEDROCK_PROFILES" ]; then
  _arns=""
  _add_arn() {
    [ -z "$1" ] && return 0
    case ",$_arns," in *",$1,"*) ;; *) _arns="${_arns:+$_arns,}$1" ;; esac
  }
  for _id in "$SEL_PRIMARY" "$SEL_FALLBACK"; do
    [ -z "$_id" ] && continue
    _add_arn "arn:aws:bedrock:${BEDROCK_REGION}:${ACCOUNT_ID}:inference-profile/${_id}"
    _fm="$(aws bedrock get-inference-profile --region "$BEDROCK_REGION" \
             --inference-profile-identifier "$_id" \
             --query 'models[].modelArn' --output text 2>/dev/null || true)"
    for _m in $_fm; do _add_arn "$_m"; done
  done
  BEDROCK_PROFILES="$_arns"
fi

echo "  primary   : $SEL_PRIMARY"
echo "  fallback  : $SEL_FALLBACK"
echo "  invoke ARNs: $BEDROCK_PROFILES"

[ -z "$SOURCE_BUCKET" ] && SOURCE_BUCKET="${STACK_NAME}-src-${ACCOUNT_ID}-${REGION}"

# ---------------------------------------------------------------------------
# 3) Deterministic source zip. Archive the WORKING TREE (tracked files with
#    current, possibly-uncommitted edits) so we can test before committing:
#    `git stash create` snapshots the working state to a throwaway commit
#    without touching the index; falls back to HEAD when the tree is clean.
# ---------------------------------------------------------------------------
BUILD_DIR="$REPO_ROOT/infrastructure/cloudformation/build"
mkdir -p "$BUILD_DIR"
SRC_ZIP="$BUILD_DIR/agent-source.zip"
TREE="$(git -C "$REPO_ROOT" stash create || true)"
[ -z "$TREE" ] && TREE="HEAD"
git -C "$REPO_ROOT" archive --format=zip -o "$SRC_ZIP" "$TREE"
SRC_HASH="$(git -C "$REPO_ROOT" rev-parse --short "$TREE")"
SRC_KEY="agent-source/${SRC_HASH}.zip"
IMAGE_TAG="git-${SRC_HASH}"

# 4) Ensure the source bucket exists (private, idempotent), then upload.
if ! aws s3api head-bucket --bucket "$SOURCE_BUCKET" --region "$REGION" 2>/dev/null; then
  echo "creating source bucket s3://${SOURCE_BUCKET}"
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$SOURCE_BUCKET" --region "$REGION"
  else
    aws s3api create-bucket --bucket "$SOURCE_BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION"
  fi
  aws s3api put-public-access-block --bucket "$SOURCE_BUCKET" \
    --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
  aws s3api put-bucket-encryption --bucket "$SOURCE_BUCKET" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms"}}]}'
fi
aws s3 cp "$SRC_ZIP" "s3://${SOURCE_BUCKET}/${SRC_KEY}" --region "$REGION"

# 5) VPC mode only: look up the region's S3 prefix list id for the runtime SG
#    egress rule. In public mode no VPC/SG exists, so leave it empty.
if [ "$NETWORK_MODE" = "vpc" ]; then
  S3_PL_ID="$(aws ec2 describe-prefix-lists --region "$REGION" \
    --filters "Name=prefix-list-name,Values=com.amazonaws.${REGION}.s3" \
    --query 'PrefixLists[0].PrefixListId' --output text 2>/dev/null || echo "")"
  [ "$S3_PL_ID" = "None" ] && S3_PL_ID=""
else
  S3_PL_ID=""
fi

# 5b) X-Ray perms are opt-in (--enable-xray true). They are only useful when
#     CloudWatch Transaction Search is enabled; if it is OFF, refuse unless the
#     operator re-confirms with --force-xray-without-ts (perms would be granted
#     but spans won't be searchable and may incur cost with no benefit).
if [ "$ENABLE_XRAY" = "true" ]; then
  XRAY_TS="$(aws xray get-trace-segment-destination --region "$REGION" \
    --query 'Destination' --output text 2>/dev/null || echo "unknown")"
  if [ "$XRAY_TS" != "CloudWatchLogs" ] && [ "$FORCE_XRAY_NO_TS" != "true" ]; then
    echo "REFUSED: --enable-xray true but CloudWatch Transaction Search is not enabled (Destination=$XRAY_TS)." >&2
    echo "X-Ray permissions would be granted but spans would not be searchable and X-Ray/CloudWatch may still bill for exported data." >&2
    echo "Enable Transaction Search first, or re-run with --force-xray-without-ts to proceed anyway." >&2
    exit 3
  fi
  echo "X-Ray tracing ENABLED (EnableXrayTracing=true, TS destination=$XRAY_TS)"
else
  echo "X-Ray tracing disabled (EnableXrayTracing=false)"
fi

# 6) Deploy (no `package` — Lambda code is inlined in the template).
aws cloudformation deploy \
  --template-file "$TEMPLATE" \
  --stack-name "$STACK_NAME" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$REGION" \
  --parameter-overrides \
    ArtifactBucketName="$SOURCE_BUCKET" \
    SourceS3Key="$SRC_KEY" \
    ImageTag="$IMAGE_TAG" \
    DltStackName="$DLT_STACK" \
    DltRegion="$DLT_REGION" \
    BedrockRegion="$BEDROCK_REGION" \
    DltApiGatewayArn="$DLT_API_ARN" \
    DltScenariosBucketArn="$DLT_BUCKET_ARN" \
    DltStackArn="$DLT_STACK_ARN" \
    BedrockInferenceProfileArns="$BEDROCK_PROFILES" \
    BedrockModelPrimary="$SEL_PRIMARY" \
    BedrockModelFallback="$SEL_FALLBACK" \
    NetworkMode="$NETWORK_MODE" \
    CreateVpc="$CREATE_VPC" \
    S3PrefixListId="$S3_PL_ID" \
    EnableXrayTracing="$ENABLE_XRAY"

echo
echo "Deployed. Outputs:"
aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs' --output table
