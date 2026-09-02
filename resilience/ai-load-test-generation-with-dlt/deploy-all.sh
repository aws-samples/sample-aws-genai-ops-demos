#!/usr/bin/env bash
# Deploy the AI Load Test Generator Agent to AgentCore Runtime via AWS CDK.
#
# Conformity path (sample-aws-genai-ops-demos): CDK IaC, shared prerequisites,
# region auto-detect, user-friendly summary. Coexists with the CloudFormation
# path (infrastructure/cloudformation/deploy.sh) — this one deploys the CDK stack `AILoadTestGen-<region>`.
#
# DLT is OPTIONAL: omit --dlt-stack for a script-only agent; pass it (now or via
# a later re-run) to wire DLT. The image is built locally by the detected
# container engine (docker/finch/nerdctl) via CDK's DockerImageAsset.
#
# Usage:
#   ./deploy-all.sh --bedrock-model us.anthropic.claude-opus-4-8 \
#       [--dlt-stack LaunchWizard-dlt-poc --dlt-region us-west-2] \
#       [--bedrock-fallback <id>] [--region <region>] [--bedrock-region <region>] \
#       [--network-mode public|vpc] [--enable-xray true]
#
#   --network-mode public|vpc  runtime network placement [default: public].
#       public = AWS-managed egress, no VPC created (lightweight, no NAT/endpoint
#       cost, no lingering service-managed ENIs on delete). vpc = private VPC
#       (NAT + endpoints + egress-only SG) for egress control / private targets.
#       Inbound is IAM SigV4 in both modes.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CDK_DIR="$HERE/infrastructure/cdk"

REGION=""              # explicit --region override; empty = auto-detect (env → CLI config → us-east-1)
BEDROCK_REGION=""
BEDROCK_MODEL=""
BEDROCK_FALLBACK=""
DLT_STACK=""
DLT_REGION=""
NETWORK_MODE="public"
ENABLE_XRAY="false"

while [ $# -gt 0 ]; do
  case "$1" in
    --region) REGION="$2"; shift 2 ;;
    --bedrock-region) BEDROCK_REGION="$2"; shift 2 ;;
    --bedrock-model) BEDROCK_MODEL="$2"; MODEL_GIVEN=1; shift 2 ;;
    --bedrock-fallback) BEDROCK_FALLBACK="$2"; shift 2 ;;
    --dlt-stack) DLT_STACK="$2"; shift 2 ;;
    --dlt-region) DLT_REGION="$2"; shift 2 ;;
    --network-mode) NETWORK_MODE="$2"; shift 2 ;;
    --enable-xray) ENABLE_XRAY="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# 1) Prerequisites — shared script when present (monorepo), else a local check.
SHARED_PREREQ="$HERE/../../shared/scripts/check-prerequisites.sh"
if [ -f "$SHARED_PREREQ" ]; then
  # shellcheck disable=SC1090
  source "$SHARED_PREREQ" \
  --required-service agentcore \
  --min-aws-cli-version 2.31.13
  # Explicit --region wins; otherwise adopt the region the shared check detected.
  REGION="${REGION:-${AWS_REGION:-}}"
else
  command -v aws >/dev/null 2>&1 || { echo "aws CLI v2 required" >&2; exit 1; }
  command -v npx >/dev/null 2>&1 || { echo "node/npx required for CDK" >&2; exit 1; }
  aws sts get-caller-identity >/dev/null 2>&1 || { echo "no valid AWS credentials" >&2; exit 1; }
fi

# Region: explicit --region wins; else env → CLI config → us-east-1
# (mirrors shared/utils/aws-utils.sh get_aws_region so standalone use matches the monorepo).
if [ -z "$REGION" ]; then
  REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-}}"
  [ -z "$REGION" ] && REGION="$(aws configure get region 2>/dev/null || true)"
  [ -z "$REGION" ] && REGION="us-east-1"
fi

# Container engine auto-detect (docker preferred; falls back to finch/nerdctl).
ENGINE="${CONTAINER_ENGINE:-}"
if [ -z "$ENGINE" ]; then
  for c in docker finch nerdctl; do
    command -v "$c" >/dev/null 2>&1 && ENGINE="$c" && break
  done
fi
[ -z "$ENGINE" ] && { echo "no container engine (docker/finch/nerdctl) found" >&2; exit 1; }

# The engine must actually be running — CDK builds the ARM64 image locally. A
# stopped daemon otherwise surfaces as a cryptic mid-build "cannot connect to
# the docker API" failure, so fail early with an actionable hint.
if ! "$ENGINE" info >/dev/null 2>&1; then
  echo "ERROR: '$ENGINE' is installed but not running." >&2
  case "$ENGINE" in
    finch) echo "  start it with:  finch vm start" >&2 ;;
    *)     echo "  start the $ENGINE daemon (e.g. open Docker Desktop) and re-run." >&2 ;;
  esac
  exit 1
fi

[ -z "$BEDROCK_REGION" ] && BEDROCK_REGION="$REGION"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

# 2) DLT is optional — derive its ARNs from the stack name + region.
DLT_API_ARN=""; DLT_BUCKET_ARN=""; DLT_STACK_ARN=""
if [ -n "$DLT_STACK" ]; then
  : "${DLT_REGION:?--dlt-region required when --dlt-stack is given}"
  echo "Resolving DLT stack '$DLT_STACK' in $DLT_REGION ..."
  DLT_STACK_ARN="$(aws cloudformation describe-stacks --stack-name "$DLT_STACK" --region "$DLT_REGION" \
    --query 'Stacks[0].StackId' --output text 2>/dev/null || true)"
  [ -z "$DLT_STACK_ARN" ] || [ "$DLT_STACK_ARN" = "None" ] && { echo "cannot describe DLT stack" >&2; exit 4; }
  API_URL="$(aws cloudformation describe-stacks --stack-name "$DLT_STACK" --region "$DLT_REGION" \
    --query "Stacks[0].Outputs[?starts_with(OutputKey, 'DLTApiEndpoint')].OutputValue | [0]" --output text 2>/dev/null || true)"
  _hostpath="${API_URL#https://}"; API_ID="${_hostpath%%.*}"; _path="${API_URL#*amazonaws.com/}"; API_STAGE="${_path%%/*}"
  [ -z "$API_STAGE" ] && API_STAGE="*"
  DLT_API_ARN="arn:aws:execute-api:${DLT_REGION}:${ACCOUNT_ID}:${API_ID}/${API_STAGE}/*"
  SCEN_BUCKET="$(aws cloudformation describe-stacks --stack-name "$DLT_STACK" --region "$DLT_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ScenariosBucket'].OutputValue | [0]" --output text 2>/dev/null || true)"
  DLT_BUCKET_ARN="arn:aws:s3:::${SCEN_BUCKET}"
  echo "  api=$DLT_API_ARN bucket=$DLT_BUCKET_ARN"
else
  echo "DLT not connected (script-only agent). Re-run with --dlt-stack to wire it later."
fi

# 3) Bedrock model — OPTIONAL. Default to the agent's built-in models, then
# resolve each to a cross-region inference profile that ACTUALLY EXISTS in
# BEDROCK_REGION. We never fabricate a prefixed id (the Asia-Pacific prefix is
# `apac`, not `ap`, and the newest models ship global-only in some regions):
# instead we list the system profiles for the base model in BEDROCK_REGION and
# pick deterministically — the region's own geography (us/eu/apac) first, else
# the global profile, else the sole match; none or ambiguous fails fast.
# https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html
[ -z "$BEDROCK_MODEL" ] && BEDROCK_MODEL="anthropic.claude-opus-4-8"
if [ -z "$BEDROCK_FALLBACK" ]; then
  # a caller-supplied primary doubles as its own fallback; otherwise keep the
  # agent's built-in opus->sonnet pairing.
  [ -n "${MODEL_GIVEN:-}" ] && BEDROCK_FALLBACK="$BEDROCK_MODEL" \
                           || BEDROCK_FALLBACK="anthropic.claude-sonnet-5"
fi

# CRIS geo prefix for a region — used ONLY to prefer among profiles that really
# exist, never to construct an id. Asia-Pacific is `apac` (not `ap`); regions
# without a dedicated geography (ca/me/af/sa/il/mx) use the global profile.
_geo_prefix() { case "${1%%-*}" in us) echo us ;; eu) echo eu ;; ap) echo apac ;; *) echo global ;; esac; }
# strip a leading known CRIS prefix to get the bare model name
_base_model() { case "$1" in us.*|eu.*|apac.*|ap.*|global.*) echo "${1#*.}" ;; *) echo "$1" ;; esac; }

# resolve_profile <model-in> -> prints the resolved profile id on stdout; returns
# non-zero (set -e aborts the deploy) with clear guidance when it cannot pick one.
resolve_profile() {
  _want="$1"; _base="$(_base_model "$_want")"; _geo="$(_geo_prefix "$BEDROCK_REGION")"
  # every system-defined profile in BEDROCK_REGION whose id ends with the base
  _ids="$(aws bedrock list-inference-profiles --region "$BEDROCK_REGION" \
            --type-equals SYSTEM_DEFINED \
            --query "inferenceProfileSummaries[?ends_with(inferenceProfileId, '${_base}')].inferenceProfileId" \
            --output text 2>/dev/null | tr '\t' '\n' | sed '/^$/d')"
  # 1) the region's own geography wins (deterministic where geo+global co-exist)
  if printf '%s\n' "$_ids" | grep -Fxq "${_geo}.${_base}"; then
    printf '%s' "${_geo}.${_base}"; return 0
  fi
  # 2) else the global profile (its destination list is all commercial regions)
  if printf '%s\n' "$_ids" | grep -Fxq "global.${_base}"; then
    echo "note: no '${_geo}.' profile for '${_base}' in $BEDROCK_REGION; using 'global.${_base}'." >&2
    printf '%s' "global.${_base}"; return 0
  fi
  # 3) else a single unambiguous match
  _n="$(printf '%s\n' "$_ids" | sed '/^$/d' | wc -l | tr -d ' ')"
  if [ "$_n" = "1" ]; then
    echo "note: '$_want' resolved to '$_ids' in $BEDROCK_REGION." >&2
    printf '%s' "$_ids"; return 0
  fi
  # 4) none or ambiguous -> fail fast with guidance
  {
    echo "ERROR: cannot pick a single inference profile for '${_base}' in $BEDROCK_REGION."
    if [ -z "$_ids" ]; then
      echo "  None are offered here (or bedrock:ListInferenceProfiles is denied)."
      echo "  Deploy in a region that offers this model, or pass --bedrock-model."
    else
      echo "  Multiple candidates — re-run with --bedrock-model set to one of:"
      printf '    %s\n' $_ids
    fi
  } >&2
  return 1
}

PRIMARY_ID="$(resolve_profile "$BEDROCK_MODEL")"
FALLBACK_ID="$(resolve_profile "$BEDROCK_FALLBACK")"
echo "Bedrock: primary=$PRIMARY_ID fallback=$FALLBACK_ID (region $BEDROCK_REGION)"

# Invoke scope — the resolved profile ARN(s) + each routed foundation-model ARN.
PROFILE_ARNS=""
_add() { case ",$PROFILE_ARNS," in *",$1,"*) ;; *) PROFILE_ARNS="${PROFILE_ARNS:+$PROFILE_ARNS,}$1" ;; esac; }
for _id in "$PRIMARY_ID" "$FALLBACK_ID"; do
  _add "arn:aws:bedrock:${BEDROCK_REGION}:${ACCOUNT_ID}:inference-profile/${_id}"
  for _m in $(aws bedrock get-inference-profile --region "$BEDROCK_REGION" \
                --inference-profile-identifier "$_id" --query 'models[].modelArn' --output text); do
    _add "$_m"
  done
done

# 4) Python deps for CDK.
python3 -m venv "$CDK_DIR/.venv"
# shellcheck disable=SC1091
. "$CDK_DIR/.venv/bin/activate"
pip install -q -r "$CDK_DIR/requirements.txt"

# 5) Deploy. CDK builds the ARM64 image with the detected engine; bootstrap once.
cd "$CDK_DIR"
export CDK_DOCKER="$ENGINE"
CDK="npx --yes aws-cdk@latest"
$CDK bootstrap "aws://${ACCOUNT_ID}/${REGION}" >/dev/null 2>&1 || true
$CDK deploy --require-approval never \
  -c region="$REGION" \
  -c bedrockRegion="$BEDROCK_REGION" \
  -c bedrockModelPrimary="$PRIMARY_ID" \
  -c bedrockModelFallback="$FALLBACK_ID" \
  -c bedrockProfileArns="$PROFILE_ARNS" \
  -c dltStackName="$DLT_STACK" \
  -c dltRegion="$DLT_REGION" \
  -c dltApiGatewayArn="$DLT_API_ARN" \
  -c dltScenariosBucketArn="$DLT_BUCKET_ARN" \
  -c dltStackArn="$DLT_STACK_ARN" \
  -c networkMode="$NETWORK_MODE" \
  -c enableXray="$ENABLE_XRAY"

# 6) User-friendly summary.
STACK="AILoadTestGen-${REGION}"
RT_ARN="$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text 2>/dev/null || true)"
SPEC_BUCKET="$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='SpecInputBucketName'].OutputValue" --output text 2>/dev/null || true)"
if [ -z "$RT_ARN" ] || [ "$RT_ARN" = "None" ]; then
  echo "ERROR: stack $STACK has no AgentRuntimeArn output — the deploy did not complete." >&2
  exit 6
fi
echo "========================================"
echo "  Deployment Complete!"
echo "========================================"
echo "  Runtime ARN : $RT_ARN"
echo "  Spec bucket : $SPEC_BUCKET"
echo "  Region      : $REGION"
echo "  DLT wired   : $([ -n "$DLT_STACK" ] && echo yes || echo no)"
echo "  Teardown    : cd infrastructure/cdk && CDK_DOCKER=$ENGINE npx aws-cdk@latest destroy $STACK"
