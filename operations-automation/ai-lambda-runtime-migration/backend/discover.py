"""
Discover: find deprecated-runtime Lambda functions and enrich/prioritize them.

Plain, step-callable helpers for the durable pipeline (backend/pipeline.py):
Trusted Advisor's deprecated-runtime check is the source of truth, each flagged
function is enriched with its Lambda configuration, tags and CloudWatch usage,
diffed against the stored inventory and prioritized with Bedrock. No DynamoDB
resource or client is created at import time and no retry sleeps live here:
step-level retry (StepConfig) handles Trusted Advisor flakiness and every
boto3/Bedrock call runs inside a step.

Rules this module enforces:
  * Rows are keyed `item_key` = fn#<account_id>#<region>#<function_arn> and
    carry account_id / account_name / region attributes, so the same ARN in two
    accounts never collides.
  * The Support (Trusted Advisor) client uses the partition-home endpoint, never
    a hardcoded region.
  * The Bedrock model id comes from model_id.py (computed in the Lambda from the
    staged aws_utils.get_bedrock_model_id); never a hardcoded region-prefixed id.
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from aws_utils import get_region  # staged copy of shared/utils/aws_utils.py
from common import convert_floats, extract_region, extract_function_name, error_code as _error_code
from model_id import model_id as _model_id  # computed once at init from staged aws_utils

logger = logging.getLogger(__name__)

TABLE_NAME = os.environ.get("TABLE_NAME", "lambda-runtime-migration")

# Trusted Advisor check ID for deprecated Lambda runtimes.
DEPRECATED_RUNTIMES_CHECK_ID = "L4dfs2Q4C5"

# CloudWatch metrics lookback window.
METRICS_LOOKBACK_DAYS = 14

# AWS Support / Trusted Advisor is partition-global, reached through a single
# home region per partition — never a literal us-east-1 (contributor-guide).
SUPPORT_HOME = {"aws": "us-east-1", "aws-cn": "cn-north-1", "aws-us-gov": "us-gov-west-1"}


# ---------------------------------------------------------------------------
# Key / ARN helpers (pure)
# ---------------------------------------------------------------------------

def canonical_arn(arn: str) -> str:
    """Normalize a function ARN to its canonical form for keying.

    Trusted Advisor check L4dfs2Q4C5 ALWAYS reports the '$LATEST'-qualified ARN
    (arn:...:function:f:$LATEST), one row per function, never per published
    version: the check "Checks for Lambda functions whose $LATEST version is
    configured to use a runtime that is approaching deprecation, or is
    deprecated" (https://docs.aws.amazon.com/awssupport/latest/user/security-checks.html#aws-lambda-functions-deprecated-runtimes).
    Other producers of the same function's ARN do NOT carry that qualifier:
    Lambda GetFunction / ListFunctions return the unqualified form
    (arn:...:function:f), and so does a user pasting an ARN into the UI. The
    normalisation exists so the inventory key does not depend on WHICH API
    produced the ARN. Strip ONLY a trailing ':$LATEST' qualifier; numeric
    version qualifiers (':1', ':42') are a different resource and are left
    untouched. Pure.
    """
    if not arn:
        return arn
    if arn.endswith(":$LATEST"):
        return arn[: -len(":$LATEST")]
    return arn


def item_key(account_id: str, region: str, function_arn: str) -> str:
    """Partition key for the inventory table.

    The ARN is canonicalized (trailing ':$LATEST' stripped) so the TA-qualified
    and the Lambda-API unqualified forms of the SAME function produce one
    identical key, whichever API the ARN came from (see canonical_arn).
    """
    return f"fn#{account_id}#{region}#{canonical_arn(function_arn)}"


def mig_item_key(account_id: str, region: str, function_arn: str) -> str:
    """Partition key of a function's migration-run item (migrate.py owns the
    item; this is the ONE place its shape lives): mig#<account>#<region>#<canonical
    ARN>, i.e. item_key with the prefix swapped, so the fn# and mig# rows of one
    function line up and re-keying one can re-key the other. Pure."""
    return f"mig#{account_id}#{region}#{canonical_arn(function_arn)}"


def index_migration_runs(mig_items: List[dict]) -> Dict[str, dict]:
    """mig# items keyed by canonical function ARN — the ONE matching rule between
    a function row and its migration-run item. api._merge_migration_runs (the
    table) and build_rollup (the Statistics series) both go through here so the
    two views can never disagree on which run belongs to which function. Rows
    that are not mig# items are ignored. Pure."""
    return {canonical_arn(m.get("function_arn", "")): m
            for m in mig_items if str(m.get("item_key", "")).startswith("mig#")}


def migration_run_for(row: dict, mig_by_arn: Dict[str, dict]) -> Optional[dict]:
    """The mig# item of a fn# row (by canonical ARN), or None when the function
    has never been migrated. Pure."""
    return mig_by_arn.get(canonical_arn(row.get("function_arn", "")))


# Identity attributes a re-key refreshes; everything else on a row is history
# (first_seen_at, assessment_*, priority_*, migration_*) and is carried as-is.
_IDENTITY_ATTRS = ("item_key", "account_id", "account_name", "region", "function_arn")
# Axis attributes that are never back-filled from a dropped duplicate: the kept
# row's deprecation / migration state must stay the one the kept row has.
_AXIS_ATTRS = ("resolved_at", "migration_status", "alert_status")


def support_home_region(region: str) -> str:
    """Partition-home region for the Support/Trusted Advisor endpoint."""
    partition = boto3.session.Session().get_partition_for_region(region)
    return SUPPORT_HOME.get(partition, region)


# ---------------------------------------------------------------------------
# Session (hub or assumed spoke role)
# ---------------------------------------------------------------------------

def session_for_account(account_id: str, region: str) -> boto3.session.Session:
    """boto3 session for an account.

    The hub's own id (what STS reports for the pipeline) or an empty id (the
    hub when STS was unreachable) -> the Lambda's own credentials. Any other account
    id assumes the fixed-name read-only spoke role (opt-in multi-account scope);
    SPOKE_ROLE_NAME / SPOKE_EXTERNAL_ID come from the pipeline environment.
    """
    hub = _caller_account(region)
    if not account_id or account_id == hub:
        return boto3.session.Session(region_name=region)

    spoke_role = os.environ.get("SPOKE_ROLE_NAME")
    if not spoke_role:
        # Multi-account not configured: fall back to local credentials.
        return boto3.session.Session(region_name=region)

    partition = boto3.session.Session().get_partition_for_region(region)
    role_arn = f"arn:{partition}:iam::{account_id}:role/{spoke_role}"
    assume_kwargs = {"RoleArn": role_arn, "RoleSessionName": "lambda-runtime-migration-scan"}
    external_id = os.environ.get("SPOKE_EXTERNAL_ID")
    if external_id:
        assume_kwargs["ExternalId"] = external_id
    creds = boto3.client("sts", region_name=region).assume_role(**assume_kwargs)["Credentials"]
    return boto3.session.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region,
    )


_CALLER_CACHE: Dict[str, str] = {}


def _caller_account(region: str) -> str:
    """The account these credentials belong to (the hub), "" when STS failed.

    Only a SUCCESS is cached. Caching a failure for the container's lifetime
    would make every later hub scan in that container key its rows under the
    empty-id prefix (fn##...) and re-create the twin rows the identity
    match exists to prevent; a transient STS error must stay transient.
    """
    if "account" not in _CALLER_CACHE:
        try:
            _CALLER_CACHE["account"] = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
        except Exception:  # pragma: no cover - best effort
            logger.warning("sts:GetCallerIdentity failed; hub account id unresolved for this call",
                           exc_info=True)
            return ""
    return _CALLER_CACHE["account"]


# ---------------------------------------------------------------------------
# Trusted Advisor
# ---------------------------------------------------------------------------

# --- Support tier (per-account TA availability) ------------------------------
#
# No API returns an account's Support plan. AWS documents inferring it from the
# case severities the account may open (support:DescribeSeverityLevels).
# Trusted Advisor's API needs a
# Business/Enterprise-tier plan, so a Basic/Developer account cannot be scanned
# and the scope must say "not checked", never silently empty.
#   SubscriptionRequiredException -> basic       (no Support/TA API)
#   highest 'normal'              -> developer   (no TA API)
#   'urgent'/'high'               -> business    (TA available)
#   'critical'                    -> enterprise  (TA available)
TA_AVAILABLE_TIERS = ("business", "enterprise")


def support_tier(session: boto3.session.Session, region: str) -> Dict[str, Any]:
    """Infer the Support tier of the account behind `session`. Never raises.

    Returns {"tier": basic|developer|business|enterprise|unknown,
             "reason": str|None, "ta_available": bool}.
    """
    client = session.client("support", region_name=support_home_region(region))
    try:
        codes = [s["code"] for s in client.describe_severity_levels(language="en").get("severityLevels", [])]
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "SubscriptionRequiredException":
            return {"tier": "basic", "reason": None, "ta_available": False}
        return {"tier": "unknown", "reason": f"{code}: {str(e)[:120]}", "ta_available": False}
    except Exception as e:  # pragma: no cover - best effort
        return {"tier": "unknown", "reason": f"{type(e).__name__}: {str(e)[:120]}", "ta_available": False}
    if "critical" in codes:
        tier = "enterprise"
    elif "urgent" in codes or "high" in codes:
        tier = "business"
    elif codes:
        tier = "developer"
    else:
        tier = "unknown"
    return {"tier": tier, "reason": None, "ta_available": tier in TA_AVAILABLE_TIERS}


def query_trusted_advisor(session: boto3.session.Session, region: str) -> Dict[str, Any]:
    """Query Trusted Advisor check L4dfs2Q4C5 for deprecated-runtime functions.

    Returns {"functions": [...], "message": str}. A missing Support plan is an
    expected, non-fatal outcome (empty list + message), not an exception — the
    run succeeds and the summary records that TA was unavailable. Transient
    ClientErrors are raised so the durable step retry (StepConfig) can replay
    the step; the old in-code `time.sleep(3)` retries are gone.
    """
    support = session.client("support", region_name=support_home_region(region))
    try:
        response = support.describe_trusted_advisor_check_result(
            checkId=DEPRECATED_RUNTIMES_CHECK_ID, language="en"
        )
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "SubscriptionRequiredException":
            msg = "AWS Business/Enterprise Support plan required for Trusted Advisor access."
            logger.warning(msg)
            return {"functions": [], "message": msg}
        logger.error("Trusted Advisor call failed: %s", error_code)
        raise

    flagged = response.get("result", {}).get("flaggedResources", [])
    logger.info("Trusted Advisor: status=%s, flagged=%d resources",
                response.get("result", {}).get("status", "unknown"), len(flagged))
    if not flagged:
        return {"functions": [], "message": "No deprecated-runtime Lambda functions found."}

    functions, parse_errors = [], []
    for resource in flagged:
        md = resource.get("metadata", [])
        try:
            # Official TA column order:
            # 0 Status, 1 Region, 2 Function ARN, 3 Runtime,
            # 4 Days to Deprecation, 5 Deprecation Date,
            # 6 Average Daily Invokes, 7 Last Updated Time
            if len(md) < 6:
                parse_errors.append(f"Too few columns ({len(md)}): {md}")
                continue
            try:
                days_val = int(md[4]) if len(md) > 4 else 0
            except (ValueError, TypeError):
                days_val = 0
            try:
                invokes_val = float(md[6]) if len(md) > 6 else 0.0
            except (ValueError, TypeError):
                invokes_val = 0.0
            functions.append({
                "function_arn": md[2],
                "runtime": md[3],
                "deprecation_date": md[5] if len(md) > 5 else "",
                "days_to_deprecation": days_val,
                "alert_status": md[0],
                "avg_daily_invokes": invokes_val,
                "region": md[1],
                "ta_last_updated": md[7] if len(md) > 7 else "",
            })
        except Exception as e:  # pragma: no cover - defensive parse guard
            parse_errors.append(f"metadata={md}, error={e}")
            logger.warning("Skipping malformed resource: %s (metadata: %s)", e, md)

    msg = f"Found {len(functions)} function(s)."
    if parse_errors:
        msg += f" ({len(parse_errors)} parse errors: {parse_errors[0]})"
    return {"functions": functions, "message": msg}


# ---------------------------------------------------------------------------
# Enrichment — Lambda API + CloudWatch
# ---------------------------------------------------------------------------

def _log_ref(value: str) -> str:
    """A short, stable reference for a function name or ARN in log lines.

    The function name and ARN are read from the scanned account's API responses
    through the assumed-role session, so static analysis treats them as data
    that must not reach a log in clear text. An 8-char SHA-256 prefix lets an
    operator correlate log lines for one function across a run without the raw
    value ever being logged.
    """
    return hashlib.sha256((value or "").encode()).hexdigest()[:8]


def enrich_function(session: boto3.session.Session, function_arn: str, default_region: str) -> Dict[str, Any]:
    """Enrich a function with Lambda config, tags, and CloudWatch metrics.

    Env var values are masked to '***' so no secret crosses a checkpoint or
    reaches the UI. Failures are logged by error code and a hashed function
    reference only (see _error_code / _log_ref).
    """
    region = extract_region(function_arn, default_region)
    function_name = extract_function_name(function_arn)
    fn_ref = _log_ref(function_name)

    lambda_client = session.client("lambda", region_name=region)
    logs_client = session.client("logs", region_name=region)
    cw_client = session.client("cloudwatch", region_name=region)

    enrichment: Dict[str, Any] = {"function_arn": function_arn}

    try:
        cfg = lambda_client.get_function_configuration(FunctionName=function_arn)
        enrichment.update({
            "package_type": cfg.get("PackageType", "Zip"),
            "memory_size": cfg.get("MemorySize", 0),
            "timeout": cfg.get("Timeout", 0),
            "handler": cfg.get("Handler", ""),
            "code_size": cfg.get("CodeSize", 0),
            "architectures": cfg.get("Architectures", ["x86_64"]),
            "layers": [l["Arn"] for l in cfg.get("Layers", [])],
            "env_vars": {k: "***" for k in cfg.get("Environment", {}).get("Variables", {})},
        })
    except Exception as e:
        logger.error("GetFunctionConfiguration failed for fn %s: %s", fn_ref, _error_code(e))

    try:
        unversioned_arn = (function_arn.rsplit(":", 1)[0]
                           if ":$LATEST" in function_arn or function_arn.count(":") > 6
                           else function_arn)
        enrichment["tags"] = lambda_client.list_tags(Resource=unversioned_arn).get("Tags", {})
    except Exception as e:
        logger.error("ListTags failed for fn %s: %s", fn_ref, _error_code(e))
        enrichment["tags"] = {}

    try:
        log_resp = logs_client.describe_log_streams(
            logGroupName=f"/aws/lambda/{function_name}",
            orderBy="LastEventTime", descending=True, limit=1,
        )
        streams = log_resp.get("logStreams", [])
        if streams and streams[0].get("lastEventTimestamp"):
            enrichment["last_invocation_date"] = datetime.fromtimestamp(
                streams[0]["lastEventTimestamp"] / 1000, tz=timezone.utc).isoformat()
        else:
            enrichment["last_invocation_date"] = None
    except ClientError as e:
        code = _error_code(e)
        if code != "ResourceNotFoundException":
            logger.error("DescribeLogStreams failed for fn %s: %s", fn_ref, code)
        enrichment["last_invocation_date"] = None

    try:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=METRICS_LOOKBACK_DAYS)
        metrics_resp = cw_client.get_metric_statistics(
            Namespace="AWS/Lambda", MetricName="Invocations",
            Dimensions=[{"Name": "FunctionName", "Value": function_name}],
            StartTime=start_time, EndTime=end_time,
            Period=86400 * METRICS_LOOKBACK_DAYS, Statistics=["Sum"],
        )
        enrichment["invocation_count_14d"] = int(
            sum(dp.get("Sum", 0) for dp in metrics_resp.get("Datapoints", [])))
    except Exception as e:
        logger.error("GetMetricStatistics failed for fn %s: %s", fn_ref, _error_code(e))
        enrichment["invocation_count_14d"] = 0

    enrichment["migration_status"] = (
        "SKIPPED" if enrichment.get("package_type", "Zip") == "Image" else "DISCOVERED")
    return enrichment


# ---------------------------------------------------------------------------
# Prioritization (pure helper + Bedrock)
# ---------------------------------------------------------------------------

def _priority_label(score: int) -> str:
    """Human-readable priority label from the numeric score. Pure."""
    if score >= 80:
        return "CRITICAL"
    if score >= 60:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    if score >= 20:
        return "LOW"
    return "INACTIVE"


# Functions per Converse call: Nova's constrained decoding needs one fixed tool
# property per function, so an unbounded scope would blow the tool schema.
PRIORITIZE_BATCH = 50
# Nova may move the rule-based base score by at most this much, either way.
ADJUSTMENT_LIMIT = 15
# priority_reasoning when a function's score is the base score alone.
RULE_BASED_REASON = "rule-based score"


def base_priority_score(fn: dict) -> int:
    """Rule-based priority base score of one function, 0..100. Pure.

    The ONE implementation of the priority bands (the prompt no longer carries
    them; Nova only adjusts this score by at most ADJUSTMENT_LIMIT):
      * inactive (invocation_count_14d == 0 and avg_daily_invokes == 0): 15,
        nothing else applied (the INACTIVE band, a cleanup candidate);
      * otherwise start at 75 for alert_status Red, 50 for anything else (Yellow);
      * deadline bonus, Red: +10 if days_to_deprecation <= -180, +5 if < 0;
        Yellow: +10 if days_to_deprecation <= 30, +5 if <= 90;
      * usage: avg_daily_invokes >= 100 -> +10; >= 10 -> +5; < 1 -> -5;
      * clamped to 0..100.
    Examples: Red / -79 days / 42 per day -> 85; Red / -79 / 5 -> 80;
    Yellow / 0 days / 1 per day -> 60; Yellow / 150 days / 1 -> 50; inactive -> 15.
    """
    invokes_14d = int(fn.get("invocation_count_14d", 0) or 0)
    daily = float(fn.get("avg_daily_invokes", 0) or 0)
    if invokes_14d == 0 and daily == 0:
        return 15
    red = str(fn.get("alert_status", "")).lower() == "red"
    days = int(fn.get("days_to_deprecation", 0) or 0)
    score = 75 if red else 50
    if red:
        if days <= -180:
            score += 10
        elif days < 0:
            score += 5
    else:
        if days <= 30:
            score += 10
        elif days <= 90:
            score += 5
    if daily >= 100:
        score += 10
    elif daily >= 10:
        score += 5
    elif daily < 1:
        score -= 5
    return max(0, min(100, score))


def fallback_priorities(func_summaries: List[dict]) -> Dict[str, dict]:
    """Priorities from the base score alone (Bedrock unavailable, no toolUse, or a
    function missing from Nova's answer): adjustment 0, reasoning RULE_BASED_REASON.
    Summaries carry `base_score` when they come from prioritize_with_bedrock; any
    other dict is scored on the spot. Pure."""
    out = {}
    for fn in func_summaries:
        base = int(fn["base_score"]) if "base_score" in fn else base_priority_score(fn)
        out[fn["arn"]] = {"score": base, "base": base, "adjustment": 0, "reasoning": RULE_BASED_REASON}
    return out


def prioritize_with_bedrock(session: boto3.session.Session, region: str, functions: List[dict]) -> Dict[str, dict]:
    """Rule-based base score plus a bounded Nova adjustment, batched by PRIORITIZE_BATCH.

    The base comes from base_priority_score (code, deterministic). Nova sees the
    base and returns ONLY an adjustment in [-ADJUSTMENT_LIMIT, +ADJUSTMENT_LIMIT]
    per function, from what the rules cannot read (tags, names, runtime age),
    with one sentence of reasoning; the final score is clamp(base + adjustment,
    0, 100). One Converse call per batch, run sequentially over one client; a
    batch that fails or returns no toolUse falls back to fallback_priorities for
    THAT batch only (logged warning), so one bad call never degrades the others.

    Returns {arn: {"score": int, "base": int, "adjustment": int, "reasoning": str}}
    for every non-SKIPPED function.
    """

    func_summaries = []
    for fn in functions:
        if fn.get("migration_status") in ("SKIPPED",):
            continue
        func_summaries.append({
            "arn": fn.get("function_arn", ""),
            "runtime": fn.get("runtime", ""),
            "alert_status": fn.get("alert_status", ""),
            "days_to_deprecation": fn.get("days_to_deprecation", 0),
            "avg_daily_invokes": float(fn.get("avg_daily_invokes", 0)),
            "invocation_count_14d": int(fn.get("invocation_count_14d", 0)),
            "migration_status": fn.get("migration_status", "DISCOVERED"),
            "tags": fn.get("tags", {}),
            "layers": fn.get("layers", []),
            "package_type": fn.get("package_type", "Zip"),
            "last_invocation_date": fn.get("last_invocation_date"),
            "base_score": base_priority_score(fn),
        })
    if not func_summaries:
        return {}

    # Bedrock runs in the Lambda's OWN region with hub credentials, never the
    # scanned function's region or an assumed spoke session: _model_id is a
    # CRIS profile resolved for the deploy region, and a spoke role has no
    # Bedrock access. Prioritization only reasons over metadata, so the
    # scanned region is irrelevant here. One client for every batch.
    bedrock = boto3.client("bedrock-runtime", region_name=get_region())

    system_prompt = """You are ordering a Lambda runtime migration backlog for operators.
Each function already has a rule-based base score; you only fine-tune it with a small adjustment.
You MUST use the submit_priority_adjustments tool to return your analysis. DO NOT respond with plain text."""

    batches = [func_summaries[i:i + PRIORITIZE_BATCH]
               for i in range(0, len(func_summaries), PRIORITIZE_BATCH)]
    result: Dict[str, dict] = {}
    for batch_no, batch in enumerate(batches, start=1):
        adj_properties = {}
        for i, fn in enumerate(batch):
            adj_properties[f"adj_{i}"] = {
                "type": "object",
                "description": f"Adjustment for {fn['arn']} ({fn['runtime']}, {fn['alert_status']}, base {fn['base_score']})",
                "properties": {
                    "arn": {"type": "string", "description": "The function ARN"},
                    "adjustment": {"type": "integer", "minimum": -ADJUSTMENT_LIMIT, "maximum": ADJUSTMENT_LIMIT,
                                   "description": f"Adjustment to the base score, -{ADJUSTMENT_LIMIT} to +{ADJUSTMENT_LIMIT}; 0 when nothing justifies a change"},
                    "reasoning": {"type": "string", "description": "One concise sentence for the adjustment"},
                },
                "required": ["arn", "adjustment", "reasoning"],
            }
        tool_config = {
            "tools": [{
                "toolSpec": {
                    "name": "submit_priority_adjustments",
                    "description": "Submit a bounded priority adjustment for each Lambda function",
                    "inputSchema": {"json": {
                        "type": "object",
                        "properties": adj_properties,
                        "required": list(adj_properties.keys()),
                    }},
                }
            }]
        }

        prompt = f"""## Task Summary:
The functions below run deprecated Lambda runtimes and must be ordered into a migration backlog for operators.
Each function carries a base_score (0-100, higher = migrate sooner) that rules already computed from its
alert status, deprecation deadline and usage. Do NOT re-score it. Return ONLY an adjustment between
-{ADJUSTMENT_LIMIT} and +{ADJUSTMENT_LIMIT} per function, based on what the rules cannot read:
- tag semantics: environment / prod / stage, business, app or team names, customer-facing hints
- function-name hints (what the function seems to do and for whom)
- runtime major-version age: older major versions of a runtime first; days_to_deprecation already carries the deadline
Return 0 when nothing in the metadata justifies a change.

## Context Information:
- These functions were flagged by AWS Trusted Advisor check L4dfs2Q4C5 (Lambda Functions Using Deprecated Runtimes)
- alert_status "Red" means the runtime is already past its deprecation date
- alert_status "Yellow" means deprecation is upcoming within 180 days
- migration_status "DISCOVERED" means the function has been found and enriched
- migration_status "SKIPPED" means the function uses container images (not migratable via code)
- days_to_deprecation is negative when the runtime is already deprecated

## Functions to analyze:
{json.dumps(batch, indent=2, default=str)}

## Response style and format requirements:
- You MUST use the submit_priority_adjustments tool to return the adjustments
- DO NOT respond with plain text
- Each adjustment MUST be an integer between -{ADJUSTMENT_LIMIT} and +{ADJUSTMENT_LIMIT}
- Each reasoning MUST be one concise sentence"""

        try:
            response = bedrock.converse(
                modelId=_model_id(),
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"temperature": 0},
                toolConfig=tool_config,
            )
            content = response.get("output", {}).get("message", {}).get("content", [])
            tool_input = next((b["toolUse"].get("input", {}) for b in content if "toolUse" in b), None)
            if tool_input is None:
                raise ValueError("no toolUse block in the Bedrock response")
            base_by_arn = {fn["arn"]: fn["base_score"] for fn in batch}
            batch_result: Dict[str, dict] = {}
            for key, val in tool_input.items():
                if not (key.startswith("adj_") and isinstance(val, dict)):
                    continue
                arn = val.get("arn", "")
                if arn not in base_by_arn:
                    logger.warning("prioritize batch %d/%d: Nova returned unknown ARN %s, ignored",
                                   batch_no, len(batches), arn)
                    continue
                base = base_by_arn[arn]
                adj = max(-ADJUSTMENT_LIMIT, min(ADJUSTMENT_LIMIT, int(val.get("adjustment", 0))))
                batch_result[arn] = {
                    "score": max(0, min(100, base + adj)),
                    "base": base,
                    "adjustment": adj,
                    "reasoning": str(val.get("reasoning", "")),
                }
            missing = [fn for fn in batch if fn["arn"] not in batch_result]
            if missing:
                logger.warning("prioritize batch %d/%d: %d function(s) missing from Nova's answer, rule-based score kept",
                               batch_no, len(batches), len(missing))
                batch_result.update(fallback_priorities(missing))
            logger.info("prioritize batch %d/%d: Nova adjusted %d of %d functions",
                        batch_no, len(batches), len(batch) - len(missing), len(batch))
            result.update(batch_result)
        except Exception as e:
            logger.warning("prioritize batch %d/%d fell back to rule-based scores: %s",
                           batch_no, len(batches), e)
            result.update(fallback_priorities(batch))

    return result


# ---------------------------------------------------------------------------
# DynamoDB access (called inside steps)
# ---------------------------------------------------------------------------

def _table():
    """The inventory table ALWAYS lives in the hub account, in the deploy region.

    Discovery fans out across regions and (optionally) assumes spoke roles in
    other accounts, but DynamoDB must NOT follow the scan session: the table is
    single, in the hub account's deploy region, so every table access uses the
    Lambda's own credentials and own region, regardless of which (account,
    region) is being scanned.
    """
    return boto3.resource("dynamodb", region_name=get_region()).Table(TABLE_NAME)


def read_scope_inventory(session: boto3.session.Session, account_id: str, region: str) -> List[dict]:
    """Read existing inventory rows for one (account, region) scope.

    Rows are keyed item_key = fn#<account>#<region>#<arn>, so a scope's rows
    share the prefix fn#<account>#<region>#. begins_with on the partition key is
    not a Query predicate, so this scans with a filter — fine at demo volume.
    """
    table = _table()
    prefix = f"fn#{account_id}#{region}#"
    items, kwargs = [], {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(r for r in resp.get("Items", []) if str(r.get("item_key", "")).startswith(prefix))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def read_account_inventory(session: boto3.session.Session, account_id: str) -> List[dict]:
    """Read every inventory row for one account, across ALL regions.

    Trusted Advisor is account-wide, so a single discover_scope diff spans the
    whole account's inventory (keys share the prefix fn#<account>#). begins_with
    on the partition key is not a Query predicate, so this scans with a filter —
    fine at demo volume.
    """
    table = _table()
    prefix = f"fn#{account_id}#"
    items, kwargs = [], {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(r for r in resp.get("Items", []) if str(r.get("item_key", "")).startswith(prefix))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def discover_scope(session: boto3.session.Session, cell: dict, run_id: str, when: str) -> dict:
    """Run Discover for ONE account: Trusted Advisor query + enrich + diff + persist.

    Trusted Advisor is the SINGLE source of truth. Its deprecated-runtime check
    (L4dfs2Q4C5) is ACCOUNT-WIDE: one call returns the flagged functions across
    every region the account uses, so there is NO region enumeration — the cell
    carries only the account (keyed by the deploy region) and discovery keys each
    returned function under its OWN region (from the TA metadata / the ARN).

    TA needs a Business/Enterprise Support plan. That decision is made UPSTREAM,
    in its own durable step (backend/support.py / pipeline.support_tier_step):
    discover_scope is only ever called for accounts that step marked TA-eligible,
    so it no longer queries the Support API itself. The account's tier is carried
    on the cell (cell["support_tier"]) purely so the returned row/summary keep the
    same shape as before; non-eligible accounts are reported "not checked" by the
    summary from the tier step's output, not here.

    Writes rows to DynamoDB inside this step (the full function list never
    crosses a checkpoint) and returns only bounded facts. A scope that fails
    leaves its previous inventory intact — a row is marked RESOLVED only when
    its own scope succeeded.
    """
    account_id = cell.get("account_id", "")
    account_name = cell.get("account_name", "")
    region = cell["region"]
    if not account_id:
        # The hub carries its REAL id (org_accounts.resolve_accounts); an empty
        # id only means STS could not name the hub when the run started. Scanning
        # under it would key every row fn##<region>#... again, invisible to the
        # fn#<hub># rows, and re-create the twin rows the identity match exists
        # to prevent. Fail THIS scope (tolerated by the run, reported in the
        # summary) and leave the inventory intact; the next run retries STS.
        raise RuntimeError("discover_scope: account id unresolved (sts:GetCallerIdentity failed "
                           "at run start); refusing to scan under an empty account id")
    tags_filter = cell.get("tags") or []
    # Tier is decided upstream in the support-tier step and threaded on the cell;
    # discover only runs for TA-eligible accounts, so default to "enterprise".
    support_tier_name = cell.get("support_tier", "enterprise")

    # TA available by construction — the single discovery source. One call,
    # account-wide. (The Support-tier gate ran in its own step upstream.)
    ta_result = query_trusted_advisor(session, region)
    ta_message = ta_result["message"]
    functions = ta_result["functions"]

    # Each function lives in its OWN region (TA metadata col 1 / the ARN), not
    # the cell's deploy region. Normalize that region onto every function so the
    # key, the enrichment clients, and the row all land in the right place.
    for fn in functions:
        fn["region"] = fn.get("region") or extract_region(fn.get("function_arn", ""), region)

    if tags_filter:
        functions = [fn for fn in functions
                     if _matches_tags(fn, session, fn["region"], tags_filter)]

    # Regions actually seen across the account (Assess reads rows back by these).
    regions_seen = sorted({fn["region"] for fn in functions})

    table = _table()
    # TA is account-wide, so the diff is over the WHOLE account's inventory, not
    # one region: read every row this account has (any region) so a function that
    # dropped out of the TA result is correctly marked RESOLVED under its region.
    existing = read_account_inventory(session, account_id)
    # _caller_account returns "" when STS is unreachable, so hub rows can be
    # keyed under an EMPTY account id (fn##<region>#...). When this scope IS the
    # hub, read those rows too so the identity match below re-keys them onto the
    # real-id key instead of retiring them.
    if account_id and account_id == _caller_account(region):
        existing = existing + read_account_inventory(session, "")

    # Match on a STABLE IDENTITY, (region, canonical ARN), never on the raw
    # item_key string: a change of the key's shape (ARN qualifier, hub account
    # id) must re-key rows (below), never retire them as MIGRATED/Green while
    # inserting fresh twin rows. Only a function that genuinely vanished from TA
    # is resolved.
    existing_by_identity: Dict[tuple, dict] = {}
    stale_duplicates: List[dict] = []
    for row in existing:
        ident = _row_identity(row, region)
        prev = existing_by_identity.get(ident)
        if prev is None:
            existing_by_identity[ident] = row
            continue
        keep, drop = _prefer_row(prev, row)
        existing_by_identity[ident] = keep
        stale_duplicates.append(drop)
    for drop in stale_duplicates:
        # A duplicate of an identity that survives under another key shape: it
        # is the same function twice, not a second function, so delete rather
        # than retire it (retiring would count it as a migration). Typical twin
        # shape: the old row is resolved but carries the assessment / priority /
        # first_seen_at, the fresh twin is active and bare. The active twin wins,
        # so first merge the history the dropped row has and the kept row lacks
        # (kept row's values win) before deleting.
        ident = _row_identity(drop, region)
        existing_by_identity[ident] = _merge_dropped_duplicate(
            table, existing_by_identity[ident], drop, account_id)
        logger.info("discover_scope[%s]: deleting stale duplicate row %s",
                    account_id or "hub", drop.get("item_key"))
        table.delete_item(Key={"item_key": drop["item_key"]})
    active_before = sum(1 for r in existing_by_identity.values() if not r.get("resolved_at"))

    discovered_identities = set()
    new_count = rekeyed = 0
    all_active = []
    for fn in functions:
        arn = fn["function_arn"]
        fn_region = fn["region"]
        ident = (fn_region, canonical_arn(arn))
        discovered_identities.add(ident)
        key = item_key(account_id, fn_region, arn)
        existing_row = existing_by_identity.get(ident)
        if existing_row is not None and existing_row.get("item_key") != key:
            # Same function, different key shape: MIGRATE the row (every
            # attribute preserved: first_seen_at, assessment, priority_*,
            # migration_*) onto the key this scan composes, then drop the old key.
            existing_row = _rekey_row(table, existing_row, key, account_id, account_name,
                                      fn_region, arn)
            existing_by_identity[ident] = existing_row
            rekeyed += 1
        if existing_row is None:
            enrichment = enrich_function(session, arn, fn_region)
            merged = {**fn, **enrichment, "item_key": key, "account_id": account_id,
                      "account_name": account_name, "region": fn_region,
                      "first_seen_at": when, "migration_status": enrichment["migration_status"]}
            table.put_item(Item=convert_floats(merged))
            existing_by_identity[ident] = merged
            new_count += 1
            all_active.append(merged)
        else:  # existing — refresh TA fields only
            table.update_item(
                Key={"item_key": key},
                UpdateExpression="SET days_to_deprecation = :d, avg_daily_invokes = :a, alert_status = :s, ta_last_updated = :t, resolved_at = :r",
                ExpressionAttributeValues={
                    ":d": fn.get("days_to_deprecation", 0),
                    ":a": convert_floats(fn.get("avg_daily_invokes", 0)),
                    ":s": fn.get("alert_status", ""),
                    ":t": fn.get("ta_last_updated", ""),
                    ":r": None,
                },
            )
            all_active.append({**fn, "item_key": key, "account_id": account_id,
                               "account_name": account_name, "region": fn_region,
                               "migration_status": "DISCOVERED"})

    # Resolved: in our inventory but no longer flagged by TA this run (key carries
    # region). Trusted Advisor never reports a Green "OK" row — a fixed function
    # simply drops out of the flagged list — so a disappearance is inferred as
    # resolved: alert_status -> Green on the Deprecation axis, and migration_status
    # -> MIGRATED on the Migration axis. This MIGRATED write is the ONLY automatic
    # migration-status change in this phase. The own-account-success guardrail is
    # unchanged: resolved = existing ACTIVE rows whose IDENTITY is not among the
    # discovered identities, computed only for an account whose own TA scan
    # succeeded, so a failed scan never retires a function.
    resolved_keys = [r["item_key"] for ident, r in existing_by_identity.items()
                     if ident not in discovered_identities and not r.get("resolved_at")]
    if len(resolved_keys) >= 5 and len(resolved_keys) * 2 > active_before:
        # A genuine large migration is legitimate (still proceed), but a scope
        # retiring most of its backlog at once is also what a key-matching
        # defect would look like, so make it visible.
        logger.warning("discover_scope[%s]: retiring %d of %d active rows in one scan "
                       "(TA now flags %d functions); verify this is a real migration",
                       account_id or "hub", len(resolved_keys), active_before, len(functions))
    for key in resolved_keys:
        table.update_item(
            Key={"item_key": key},
            UpdateExpression="SET migration_status = :s, resolved_at = :t, alert_status = :a",
            ExpressionAttributeValues={":s": "MIGRATED", ":t": when, ":a": "Green"},
        )

    # Prioritization of this run's active functions (keyed by item_key so the
    # score lands on the right region's row): rule-based base + bounded Nova
    # adjustment, written field-scoped so the row's other history is untouched.
    if all_active:
        priorities = prioritize_with_bedrock(session, region, all_active)
        arn_to_key = {f["function_arn"]: f["item_key"] for f in all_active}
        for arn, p in priorities.items():
            key = arn_to_key.get(arn)
            if not key:
                continue
            table.update_item(
                Key={"item_key": key},
                UpdateExpression="SET priority_score = :s, priority_label = :l, priority_reasoning = :r, "
                                 "priority_base = :b, priority_adjustment = :a",
                ExpressionAttributeValues={
                    ":s": p["score"], ":l": _priority_label(p["score"]), ":r": p["reasoning"],
                    ":b": p["base"], ":a": p["adjustment"]},
            )

    result = {
        "account_id": account_id,
        "account_name": account_name,
        "region": region,
        "regions_seen": regions_seen,
        "discovered": len(all_active),
        "new": new_count,
        "resolved": len(resolved_keys),
        "rekeyed": rekeyed,
        "support_tier": support_tier_name,
        "ta_checked": True,
        "ta_message": ta_message,
        "ta_reason": None,
    }
    # Counts only: the account id and region list come back through the scan
    # session, so the summary line names neither (the run summary carries them).
    logger.info("discover_scope: tier=%s ta_checked=True regions=%d discovered=%d new=%d "
                "resolved=%d rekeyed=%d duplicates_deleted=%d",
                support_tier_name, len(regions_seen),
                len(all_active), new_count, len(resolved_keys), rekeyed, len(stale_duplicates))
    return result


def _row_identity(row: dict, default_region: str) -> tuple:
    """The stable identity of an inventory row: (region, canonical ARN).

    Independent of the item_key's shape (account id, ARN qualifier), so a row
    written under an older key convention still matches the function TA reports
    today. The region comes from the row attribute, else from the ARN. Pure.
    """
    arn = str(row.get("function_arn", "") or "")
    row_region = row.get("region") or extract_region(arn, default_region)
    return (row_region, canonical_arn(arn))


def _row_recency(row: dict) -> str:
    """ISO timestamp used to order two rows of one identity (newest wins). Pure."""
    return str(row.get("resolved_at") or row.get("first_seen_at") or "")


def _prefer_row(a: dict, b: dict) -> tuple:
    """(keep, drop) between two existing rows sharing one identity (a
    duplicate under another key shape). The ACTIVE row (no resolved_at) wins; when both are active or
    both resolved, the most recently updated wins. Pure."""
    a_active, b_active = not a.get("resolved_at"), not b.get("resolved_at")
    if a_active != b_active:
        return (a, b) if a_active else (b, a)
    return (a, b) if _row_recency(a) >= _row_recency(b) else (b, a)


def _merge_dropped_duplicate(table, keep: dict, drop: dict, account_id: str) -> dict:
    """Back-fill onto the kept row the history attributes only the dropped
    duplicate carries (kept row's values win; identity and axis attributes are
    never copied). Field-scoped SET of just the missing attributes on the kept
    key, never a whole-item put. Returns the merged in-memory row so a
    later re-key of the kept row carries the merged attributes too.
    """
    missing = {k: v for k, v in drop.items()
               if k not in _IDENTITY_ATTRS and k not in _AXIS_ATTRS
               and v is not None and keep.get(k) is None}
    if not missing:
        return keep
    names, values, parts = {}, {}, []
    for idx, (k, v) in enumerate(missing.items()):
        names[f"#m{idx}"], values[f":m{idx}"] = k, convert_floats(v)
        parts.append(f"#m{idx} = :m{idx}")
    table.update_item(
        Key={"item_key": keep["item_key"]},
        UpdateExpression="SET " + ", ".join(parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )
    logger.info("discover_scope[%s]: merged %d attribute(s) (%s) from duplicate %s into %s",
                account_id or "hub", len(missing), ", ".join(sorted(missing)),
                drop.get("item_key"), keep.get("item_key"))
    return {**missing, **keep}


def _rekey_row(table, old_row: dict, new_key: str, account_id: str, account_name: str,
               region: str, function_arn: str) -> dict:
    """Move one inventory row to a new item_key, preserving every attribute.

    Writes the full row under new_key (first_seen_at, assessment fields,
    priority_*, migration_* and anything else carried) with the identity
    attributes refreshed, then deletes the old key. The function's mig# sibling
    (migrate.py keys it on the fn# row's account_id / region) is moved along so
    the two keys keep lining up; see _rekey_mig_sibling. Returns the new row.
    """
    new_row = {**old_row, "item_key": new_key, "account_id": account_id,
               "account_name": account_name or old_row.get("account_name", ""),
               "region": region, "function_arn": function_arn}
    table.put_item(Item=convert_floats(new_row))
    table.delete_item(Key={"item_key": old_row["item_key"]})
    # A hashed function reference, never row contents or the keys read back from
    # the table: those travelled through the scan session and must stay out of logs.
    logger.info("discover_scope: re-keyed inventory row for fn %s into region %s",
                _log_ref(function_arn), region)
    old_mig_key = mig_item_key(old_row.get("account_id", "") or "",
                               old_row.get("region") or region, function_arn)
    new_mig_key = mig_item_key(account_id, region, function_arn)
    if old_mig_key != new_mig_key:
        _rekey_mig_sibling(table, old_mig_key, new_mig_key, account_id)
    return new_row


def _rekey_mig_sibling(table, old_mig_key: str, new_mig_key: str, account_id: str) -> bool:
    """Move a function's migration-run item (mig#) to the key its re-keyed fn#
    row now implies. Without this, migrate.resolve_and_verify composes the NEW
    mig# key from the fn# row and a re-migration creates a second mig# item for
    one function (stale status shown over the live run, a live item under the
    old key invisible to the ALREADY_IN_PROGRESS guard): twin rows on the mig# axis.

    Every attribute is preserved. The put is conditional on the new key being
    absent: a live item under the new key is never clobbered. When
    the item carries a build id, the buildidx#<build_id> row build_complete
    resolves the mig# key through is re-pointed, so an in-flight build still
    lands its outcome on the moved item. Returns True when an item was moved.
    """
    old_item = table.get_item(Key={"item_key": old_mig_key}).get("Item")
    if not old_item:
        return False
    new_item = {**old_item, "item_key": new_mig_key, "account_id": account_id}
    try:
        table.put_item(Item=convert_floats(new_item),
                       ConditionExpression="attribute_not_exists(item_key)")
    except ClientError as exc:
        if (exc.response.get("Error") or {}).get("Code") != "ConditionalCheckFailedException":
            raise
        # Something already lives under the new key. Only when it is our own
        # earlier copy (a retry after a crash between put and delete) is the old
        # item safe to drop; otherwise leave BOTH untouched and make it visible.
        current = table.get_item(Key={"item_key": new_mig_key}).get("Item") or {}
        same_copy = all(current.get(k) == old_item.get(k)
                        for k in ("migration_batch_id", "migration_updated_at", "migration_status"))
        if not same_copy:
            logger.warning("discover_scope: a migration item was not re-keyed because a different "
                           "item already exists under the new key; both left untouched "
                           "(manual cleanup: look for two mig# items for one function)")
            return False
    build_id = old_item.get("migration_build_id")
    if build_id:
        table.update_item(
            Key={"item_key": f"buildidx#{build_id}"},
            UpdateExpression="SET mig_item_key = :k",
            ExpressionAttributeValues={":k": new_mig_key},
        )
    table.delete_item(Key={"item_key": old_mig_key})
    logger.info("discover_scope: re-keyed one migration item alongside its inventory row")
    return True


def _matches_tags(fn: dict, session: boto3.session.Session, region: str, tags_filter: List[dict]) -> bool:
    """Does a flagged function carry all requested tag key/value filters?

    tags_filter is [{key, values}] (values empty => key-exists match). The TA
    row has no tags, so fetch them via ListTags for the filter.
    """
    try:
        arn = fn["function_arn"]
        unversioned = (arn.rsplit(":", 1)[0] if ":$LATEST" in arn or arn.count(":") > 6 else arn)
        tags = session.client("lambda", region_name=region).list_tags(Resource=unversioned).get("Tags", {})
    except Exception:
        return False
    for flt in tags_filter:
        key, values = flt.get("key"), flt.get("values") or []
        if key not in tags:
            return False
        if values and tags[key] not in values:
            return False
    return True


def list_discovered_arns(session: boto3.session.Session, scopes: List[dict]) -> List[dict]:
    """Read back the active (non-resolved, non-SKIPPED) rows for the scanned
    scopes so Assess can fan out over them. Returns bounded dicts
    {item_key, function_arn, account_id, region, runtime}."""
    out = []
    for scope in scopes:
        for row in read_scope_inventory(session, scope["account_id"], scope["region"]):
            if row.get("resolved_at") or row.get("migration_status") == "SKIPPED":
                continue
            out.append({
                "item_key": row["item_key"],
                "function_arn": row.get("function_arn", ""),
                "account_id": scope["account_id"],
                "region": scope["region"],
                "runtime": row.get("runtime", ""),
            })
    return out


# ---------------------------------------------------------------------------
# Per-scan rollup history (immutable measurement series)
# ---------------------------------------------------------------------------

# mig# enum value -> rollup run_* key. Any value not listed here, including one
# a newer writer may add, counts as run_unknown.
_RUN_BUCKETS = {
    "NOT_STARTED": "run_not_started",
    "QUEUED": "run_in_progress",
    "IN_PROGRESS": "run_in_progress",
    "REJECTED": "run_rejected",
    "FAILED": "run_failed",
    "NO_CHANGES": "run_no_changes",
    "BRANCH_READY": "run_branches_ready",
}


def _run_bucket(mig: Optional[dict]) -> str:
    """The rollup run_* key an active function's migration-run item falls in.
    No item at all means nobody has tried to migrate it: run_not_started."""
    if mig is None:
        return "run_not_started"
    return _RUN_BUCKETS.get(str(mig.get("migration_status", "")), "run_unknown")


def build_rollup(run: dict, disc_summary: dict,
                 support_summary: dict, inventory_rows: List[dict], when: str) -> dict:
    """Build ONE immutable per-scan rollup record from a fleet snapshot. Pure.

    Counts are derived from `inventory_rows` — the whole-fleet snapshot at the
    end of the run: the function rows ('fn#') and their migration-run items
    ('mig#', matched to a fn# row by canonical ARN via index_migration_runs /
    migration_run_for; a mig# item whose ARN matches no fn# row is ignored).
    Any other row (rollup#, settings#...) is skipped.
      - total_active: rows flagged Red/Yellow by TA with no resolved_at (the
        active deprecation backlog).
      - red / yellow: that active backlog split by alert_status.
      - migrated_total: rows with migration_status == 'MIGRATED' (inferred-green).
      - by_runtime / by_account: active backlog grouped by runtime / account.
      - run_not_started / run_in_progress / run_rejected / run_failed /
        run_no_changes / run_branches_ready / run_unknown: the ACTIVE backlog
        partitioned by the migration-run axis (the mig# enum). No item or
        NOT_STARTED -> run_not_started; QUEUED | IN_PROGRESS -> run_in_progress;
        BRANCH_READY -> run_branches_ready; anything this build does not know
        -> run_unknown. The seven keys always sum
        to total_active. Callers passing fn# rows only get run_* = 0 except
        run_not_started == total_active.

    `run`, `disc_summary` and `support_summary` supply the bounded run facts
    (run_id, accounts scanned / not checked). The returned record is keyed 'rollup#<rollup_at>' so the series sorts by
    time and never collides with a function row.
    """
    rollup_at = when
    red = yellow = total_active = migrated_total = 0
    by_runtime: Dict[str, int] = {}
    by_account: Dict[str, int] = {}
    run_counts = {"run_not_started": 0, "run_in_progress": 0, "run_rejected": 0,
                  "run_failed": 0, "run_no_changes": 0, "run_branches_ready": 0,
                  "run_unknown": 0}
    fn_rows = [r for r in inventory_rows if str(r.get("item_key", "")).startswith("fn#")]
    mig_by_arn = index_migration_runs(inventory_rows)
    for row in fn_rows:
        if row.get("migration_status") == "MIGRATED":
            migrated_total += 1
        alert = str(row.get("alert_status", "")).lower()
        if row.get("resolved_at"):
            continue
        if alert not in ("red", "yellow"):
            continue
        total_active += 1
        if alert == "red":
            red += 1
        else:
            yellow += 1
        run_counts[_run_bucket(migration_run_for(row, mig_by_arn))] += 1
        runtime = row.get("runtime", "unknown") or "unknown"
        by_runtime[runtime] = by_runtime.get(runtime, 0) + 1
        # Never an empty map key: DynamoDB rejects it (ValidationException:
        # Empty attribute name) and the whole rollup write would fail. Hub rows
        # scanned while STS was unreachable carry account_id == "" -> group them
        # under "unknown".
        account = row.get("account_id", "") or "unknown"
        by_account[account] = by_account.get(account, 0) + 1

    accounts_scanned = len(disc_summary.get("accounts_scanned", []) or [])
    accounts_not_checked = len(
        [t for t in support_summary.get("tiers", []) if not t.get("ta_eligible")])

    return {
        "item_key": f"rollup#{rollup_at}",
        "rollup_at": rollup_at,
        "run_id": run.get("run_id", ""),
        "accounts_scanned": accounts_scanned,
        "accounts_not_checked": accounts_not_checked,
        "total_active": total_active,
        "red": red,
        "yellow": yellow,
        "migrated_total": migrated_total,
        "by_runtime": by_runtime,
        "by_account": by_account,
        **run_counts,
    }


def read_fleet_inventory() -> List[dict]:
    """Scan the function rows ('fn#') and their migration-run items ('mig#')
    across the whole table.

    The rollup is a fleet-wide snapshot, not one scope, so this reads all
    accounts/regions. begins_with on the partition key is not a Query predicate,
    so this scans with a filter — fine at demo volume.
    """
    table = _table()
    items, kwargs = [], {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(r for r in resp.get("Items", [])
                     if str(r.get("item_key", "")).startswith(("fn#", "mig#")))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def put_rollup(record: dict) -> None:
    """Persist one immutable rollup row (floats -> Decimal for DynamoDB)."""
    _table().put_item(Item=convert_floats(record))
