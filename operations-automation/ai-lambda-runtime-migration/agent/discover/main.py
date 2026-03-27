"""
Lambda Runtime Migration Assistant — Phase 1: Discover Agent

Discovers Lambda functions running deprecated runtimes via Trusted Advisor,
enriches each with Lambda API + CloudWatch data, persists to DynamoDB,
and uses Bedrock Nova 2 Lite for AI-powered prioritization.

Deployed to AgentCore as lambdaruntime_discover.
Invoked via AgentCore HTTP protocol (POST /invocations, GET /ping).
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError
from bedrock_agentcore.runtime import BedrockAgentCoreApp

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Create the AgentCore app
app = BedrockAgentCoreApp()

# Environment configuration
TABLE_NAME = os.environ.get("TABLE_NAME", "lambda-runtime-migration")
BUCKET_NAME = os.environ.get("BUCKET_NAME", "")
REGION = os.environ["AWS_DEFAULT_REGION"]  # Set by AgentCore environment variables — no fallback

# Trusted Advisor check ID for deprecated Lambda runtimes
DEPRECATED_RUNTIMES_CHECK_ID = "L4dfs2Q4C5"

# Thresholds
METRICS_LOOKBACK_DAYS = 14

# Bedrock model for prioritization
PRIORITIZATION_MODEL_ID = "global.amazon.nova-2-lite-v1:0"  # Global inference profile

# DynamoDB resource (lazy init)
_dynamodb_resource = None


def _get_table():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb")
    return _dynamodb_resource.Table(TABLE_NAME)


def _convert_floats(obj: Any) -> Any:
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _convert_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_floats(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# Step 1: Discovery — Trusted Advisor
# ---------------------------------------------------------------------------

def query_trusted_advisor() -> dict[str, Any]:
    """Query Trusted Advisor check L4dfs2Q4C5 for deprecated-runtime Lambda functions."""
    try:
        support_client = boto3.client("support", region_name="us-east-1")  # AWS Support API is only available in us-east-1
    except Exception as e:
        logger.error("Failed to create Support API client: %s", e)
        return {"functions": [], "message": str(e)}

    try:
        response = support_client.describe_trusted_advisor_check_result(
            checkId=DEPRECATED_RUNTIMES_CHECK_ID, language="en"
        )
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "SubscriptionRequiredException":
            msg = "AWS Business/Enterprise Support plan required for Trusted Advisor access."
            logger.warning(msg)
            return {"functions": [], "message": msg}
        logger.error("Trusted Advisor ClientError: %s", e)
        return {"functions": [], "message": f"Trusted Advisor error: {e}"}
    except Exception as e:
        logger.error("Trusted Advisor unexpected error: %s", e)
        return {"functions": [], "message": f"Unexpected error querying Trusted Advisor: {e}"}

    flagged = response.get("result", {}).get("flaggedResources", [])
    logger.info("Trusted Advisor raw response: status=%s, flagged=%d resources",
                response.get("result", {}).get("status", "unknown"), len(flagged))
    if not flagged:
        return {"functions": [], "message": "No deprecated-runtime Lambda functions found."}

    functions = []
    parse_errors = []
    for resource in flagged:
        md = resource.get("metadata", [])
        try:
            # Official column order per AWS docs:
            # 0: Status, 1: Region, 2: Function ARN, 3: Runtime,
            # 4: Days to Deprecation, 5: Deprecation Date,
            # 6: Average Daily Invokes, 7: Last Updated Time
            if len(md) < 6:
                parse_errors.append(f"Too few columns ({len(md)}): {md}")
                continue

            # Parse days_to_deprecation safely
            days_raw = md[4] if len(md) > 4 else "0"
            try:
                days_val = int(days_raw)
            except (ValueError, TypeError):
                days_val = 0

            # Parse avg_daily_invokes safely
            invokes_raw = md[6] if len(md) > 6 else "0"
            try:
                invokes_val = float(invokes_raw)
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
        except Exception as e:
            parse_errors.append(f"metadata={md}, error={e}")
            logger.warning("Skipping malformed resource: %s (metadata: %s)", e, md)

    logger.info("Discovered %d function(s), %d parse errors", len(functions), len(parse_errors))
    msg = f"Found {len(functions)} function(s)."
    if parse_errors:
        msg += f" ({len(parse_errors)} parse errors: {parse_errors[0]})"
    return {"functions": functions, "message": msg}


# ---------------------------------------------------------------------------
# Step 2: Enrichment — Lambda API + CloudWatch
# ---------------------------------------------------------------------------

def _extract_region(arn: str) -> str:
    parts = arn.split(":")
    return parts[3] if len(parts) >= 4 else REGION


def _extract_function_name(arn: str) -> str:
    parts = arn.split(":")
    return parts[6] if len(parts) >= 7 else arn


def enrich_function(function_arn: str) -> dict[str, Any]:
    """Enrich a function with Lambda config, tags, and CloudWatch metrics."""
    region = _extract_region(function_arn)
    function_name = _extract_function_name(function_arn)

    lambda_client = boto3.client("lambda", region_name=region)
    logs_client = boto3.client("logs", region_name=region)
    cw_client = boto3.client("cloudwatch", region_name=region)

    enrichment: dict[str, Any] = {"function_arn": function_arn}

    # GetFunctionConfiguration
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
        logger.error("GetFunctionConfiguration failed for %s: %s", function_name, e)

    # ListTags — requires unversioned ARN (strip :$LATEST or :version suffix)
    try:
        unversioned_arn = function_arn.rsplit(":", 1)[0] if ":$LATEST" in function_arn or function_arn.count(":") > 6 else function_arn
        tags_resp = lambda_client.list_tags(Resource=unversioned_arn)
        enrichment["tags"] = tags_resp.get("Tags", {})
    except Exception as e:
        logger.error("ListTags failed for %s: %s", function_name, e)
        enrichment["tags"] = {}

    # CloudWatch Logs — last invocation
    try:
        log_group = f"/aws/lambda/{function_name}"
        log_resp = logs_client.describe_log_streams(
            logGroupName=log_group, orderBy="LastEventTime", descending=True, limit=1
        )
        streams = log_resp.get("logStreams", [])
        if streams and streams[0].get("lastEventTimestamp"):
            ts = streams[0]["lastEventTimestamp"]
            enrichment["last_invocation_date"] = datetime.fromtimestamp(
                ts / 1000, tz=timezone.utc
            ).isoformat()
        else:
            enrichment["last_invocation_date"] = None
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            enrichment["last_invocation_date"] = None
        else:
            logger.error("DescribeLogStreams failed for %s: %s", function_name, e)
            enrichment["last_invocation_date"] = None

    # CloudWatch Metrics — 14-day invocation count
    try:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=METRICS_LOOKBACK_DAYS)
        metrics_resp = cw_client.get_metric_statistics(
            Namespace="AWS/Lambda",
            MetricName="Invocations",
            Dimensions=[{"Name": "FunctionName", "Value": function_name}],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400 * METRICS_LOOKBACK_DAYS,
            Statistics=["Sum"],
        )
        dps = metrics_resp.get("Datapoints", [])
        enrichment["invocation_count_14d"] = int(sum(dp.get("Sum", 0) for dp in dps))
    except Exception as e:
        logger.error("GetMetricStatistics failed for %s: %s", function_name, e)
        enrichment["invocation_count_14d"] = 0

    # Determine migration status (workflow state only)
    pkg = enrichment.get("package_type", "Zip")
    if pkg == "Image":
        enrichment["migration_status"] = "SKIPPED"
    else:
        enrichment["migration_status"] = "DISCOVERED"

    return enrichment


def _priority_label(score: int) -> str:
    """Derive a human-readable priority label from the AI score."""
    if score >= 80:
        return "CRITICAL"
    if score >= 60:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    if score >= 20:
        return "LOW"
    return "INACTIVE"


# ---------------------------------------------------------------------------
# Step 3: Save to DynamoDB
# ---------------------------------------------------------------------------

def save_to_dynamodb(function_data: dict) -> None:
    """Save a function record to DynamoDB."""
    table = _get_table()
    item = _convert_floats(function_data)
    table.put_item(Item=item)
    logger.info("Saved record for %s", function_data.get("function_arn", "unknown"))


def update_priority_scores(scores: dict[str, int], reasonings: dict[str, str] | None = None) -> None:
    """Update priority_score, priority_label, and reasoning for each function in DynamoDB."""
    table = _get_table()
    for arn, score in scores.items():
        try:
            update_expr = "SET priority_score = :s, priority_label = :l"
            expr_values: dict = {":s": score, ":l": _priority_label(score)}
            if reasonings and arn in reasonings:
                update_expr += ", priority_reasoning = :r"
                expr_values[":r"] = reasonings[arn]
            table.update_item(
                Key={"function_arn": arn},
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expr_values,
            )
        except Exception as e:
            logger.error("Failed to update priority_score for %s: %s", arn, e)


def read_inventory() -> list[dict]:
    """Read all records from DynamoDB with pagination."""
    table = _get_table()
    items: list[dict] = []
    response = table.scan()
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    return items


# ---------------------------------------------------------------------------
# Step 4: AI Prioritization — Bedrock Nova 2 Lite
# ---------------------------------------------------------------------------

def prioritize_with_bedrock(functions: list[dict]) -> dict[str, int]:
    """Call Bedrock Nova 2 Lite with constrained decoding to generate priority scores."""
    if not functions:
        return {}

    bedrock = boto3.client("bedrock-runtime", region_name=REGION)

    # Build concise summaries, skip SKIPPED functions
    func_summaries = []
    for fn in functions:
        status = fn.get("migration_status", "DISCOVERED")
        if status in ("SKIPPED",):
            continue
        func_summaries.append({
            "arn": fn.get("function_arn", ""),
            "runtime": fn.get("runtime", ""),
            "alert_status": fn.get("alert_status", ""),
            "days_to_deprecation": fn.get("days_to_deprecation", 0),
            "avg_daily_invokes": float(fn.get("avg_daily_invokes", 0)),
            "invocation_count_14d": int(fn.get("invocation_count_14d", 0)),
            "migration_status": status,
            "tags": fn.get("tags", {}),
            "layers": fn.get("layers", []),
            "package_type": fn.get("package_type", "Zip"),
            "last_invocation_date": fn.get("last_invocation_date"),
        })

    if not func_summaries:
        return {}

    # Build tool config with constrained decoding schema
    # Each function gets a score property keyed by index (ARN can't be a JSON key in toolSpec)
    score_properties = {}
    for i, fn in enumerate(func_summaries):
        score_properties[f"score_{i}"] = {
            "type": "object",
            "description": f"Score for {fn['arn']} ({fn['runtime']}, {fn['alert_status']}, {fn['migration_status']})",
            "properties": {
                "arn": {"type": "string", "description": "The function ARN"},
                "priority_score": {"type": "integer", "description": "Priority score 0-100, higher = migrate sooner"},
                "reasoning": {"type": "string", "description": "Brief reasoning for the score"},
            },
            "required": ["arn", "priority_score", "reasoning"],
        }

    tool_config = {
        "tools": [{
            "toolSpec": {
                "name": "submit_priority_scores",
                "description": "Submit migration priority scores for Lambda functions",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": score_properties,
                        "required": list(score_properties.keys()),
                    }
                },
            }
        }]
    }

    system_prompt = """You are a Lambda Runtime Migration Prioritization Engine.
Your job is to analyze AWS Lambda functions running deprecated runtimes and assign migration priority scores.
You MUST use the submit_priority_scores tool to return your analysis. DO NOT respond with plain text."""

    prompt = f"""## Task Summary:
Analyze the following AWS Lambda functions and assign each a migration priority_score from 0 to 100.
A higher score means the function should be migrated sooner.

## Context Information:
- These functions were flagged by AWS Trusted Advisor check L4dfs2Q4C5 (Lambda Functions Using Deprecated Runtimes)
- alert_status "Red" means the runtime is already past its deprecation date
- alert_status "Yellow" means deprecation is upcoming within 180 days
- migration_status "DISCOVERED" means the function has been found and enriched
- migration_status "SKIPPED" means the function uses container images (not migratable via code)
- days_to_deprecation is negative when the runtime is already deprecated

## Model Instructions:
- Score 80-95: Red alert, actively invoked functions with negative days_to_deprecation
- Score 60-79: Red alert, low invocation or Yellow alert with high invocation
- Score 40-59: Yellow alert, moderate invocation
- Score 10-25: Inactive functions with no recent invocations (candidates for cleanup)
- Consider production tags (environment=prod, app=*) as higher priority than dev/staging
- Consider runtime age: nodejs10.x and python3.6 are more urgent than nodejs20.x
- You MUST provide a brief reasoning for each score

## Functions to analyze:
{json.dumps(func_summaries, indent=2, default=str)}

## Response style and format requirements:
- You MUST use the submit_priority_scores tool to return scores
- DO NOT respond with plain text
- Each score MUST be an integer between 0 and 100
- Each reasoning MUST be one concise sentence"""

    try:
        response = bedrock.converse(
            modelId=PRIORITIZATION_MODEL_ID,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"temperature": 0},
            toolConfig=tool_config,
        )

        # Extract tool use result from response
        content = response.get("output", {}).get("message", {}).get("content", [])
        for block in content:
            if "toolUse" in block:
                tool_input = block["toolUse"].get("input", {})
                scores = {}
                reasonings = {}
                for key, val in tool_input.items():
                    if key.startswith("score_") and isinstance(val, dict):
                        arn = val.get("arn", "")
                        score = int(val.get("priority_score", 0))
                        reasoning = val.get("reasoning", "")
                        scores[arn] = score
                        reasonings[arn] = reasoning
                        logger.info("Priority %s → %d: %s", arn.split(":")[-1], score, reasoning)
                logger.info("Bedrock returned priority scores for %d functions", len(scores))
                return scores, reasonings

        logger.warning("No toolUse block found in Bedrock response")

    except Exception as e:
        logger.error("Bedrock prioritization failed: %s", e)

    # Fallback: deterministic scores based on alert_status
    fallback = {}
    fallback_reasons = {}
    for fn in func_summaries:
        if fn.get("invocation_count_14d", 0) == 0 and fn.get("avg_daily_invokes", 0) == 0:
            fallback[fn["arn"]] = 15
            fallback_reasons[fn["arn"]] = "Inactive function — no recent invocations, candidate for cleanup"
        elif fn["alert_status"].lower() == "red":
            fallback[fn["arn"]] = 75
            fallback_reasons[fn["arn"]] = "Red alert — runtime already past deprecation date"
        else:
            fallback[fn["arn"]] = 50
            fallback_reasons[fn["arn"]] = "Yellow alert — deprecation upcoming"
    return fallback, fallback_reasons


# ---------------------------------------------------------------------------
# Main handler — orchestrates the full Phase 1 workflow
# ---------------------------------------------------------------------------

def handle_discover() -> dict:
    """Run the full Phase 1: Discovery + Enrichment + Prioritization pipeline.
    
    Implements a diff-based approach:
    1. Query Trusted Advisor for currently flagged functions
    2. Compare against existing DynamoDB inventory
    3. NEW functions (in TA, not in DDB) → full enrichment + save
    4. EXISTING functions (in TA and DDB) → refresh TA fields only (no API calls)
    5. RESOLVED functions (in DDB but not in TA) → mark as RESOLVED
    6. RE-OPENED functions (resolved in DDB but back in TA) → reactivate
    7. AI prioritization on all active (non-resolved) functions
    """
    logger.info("=== Phase 1: Discovery + Enrichment + Prioritization ===")
    now = datetime.now(timezone.utc).isoformat()

    # -----------------------------------------------------------------------
    # Step 1: Query Trusted Advisor for currently flagged functions
    # (Retry once if TA returns 0 results — API can be flaky)
    # -----------------------------------------------------------------------
    logger.info("Step 1: Querying Trusted Advisor...")
    ta_result = query_trusted_advisor()
    ta_functions = ta_result["functions"]

    # Retry once if TA returned 0 functions but we have active records in DDB
    # (TA API can occasionally return empty results due to caching/timing)
    if not ta_functions:
        import time
        logger.warning("TA returned 0 functions — retrying in 3 seconds...")
        time.sleep(3)  # nosemgrep: arbitrary-sleep — intentional retry delay for flaky TA API
        ta_result = query_trusted_advisor()
        ta_functions = ta_result["functions"]
        if ta_functions:
            logger.info("TA retry succeeded: %d functions", len(ta_functions))
        else:
            logger.warning("TA retry also returned 0 functions")

    ta_arns = {fn["function_arn"] for fn in ta_functions}
    ta_by_arn = {fn["function_arn"]: fn for fn in ta_functions}

    # -----------------------------------------------------------------------
    # Step 2: Read existing inventory from DynamoDB
    # -----------------------------------------------------------------------
    logger.info("Step 2: Reading existing inventory from DynamoDB...")
    ddb_records = read_inventory()
    ddb_arns = {r["function_arn"] for r in ddb_records}
    ddb_by_arn = {r["function_arn"]: r for r in ddb_records}
    active_ddb_arns = {r["function_arn"] for r in ddb_records if not r.get("resolved_at")}

    # -----------------------------------------------------------------------
    # Step 3: Compute diff — classify each function
    # -----------------------------------------------------------------------
    new_arns = ta_arns - ddb_arns                          # Never seen before
    existing_arns = ta_arns & ddb_arns                     # Still flagged
    resolved_arns = active_ddb_arns - ta_arns              # Gone from TA → resolved
    reopened_arns = (ta_arns & ddb_arns) & {               # Was resolved, now back in TA
        r["function_arn"] for r in ddb_records if r.get("resolved_at")
    }

    logger.info("Diff: %d new, %d existing, %d resolved, %d re-opened",
                len(new_arns), len(existing_arns), len(resolved_arns), len(reopened_arns))

    all_active = []  # Collect all active functions for prioritization

    # -----------------------------------------------------------------------
    # Step 4: NEW functions — full enrichment (Lambda API + CloudWatch)
    # -----------------------------------------------------------------------
    if new_arns:
        logger.info("Step 4a: Enriching %d NEW functions...", len(new_arns))
    for arn in new_arns:
        ta_data = ta_by_arn[arn]
        enrichment = enrich_function(arn)
        merged = {**ta_data, **enrichment, "first_seen_at": now, "migration_status": "DISCOVERED"}
        save_to_dynamodb(merged)
        all_active.append(merged)

    # -----------------------------------------------------------------------
    # Step 5: EXISTING functions — refresh TA fields only (no API calls)
    # -----------------------------------------------------------------------
    if existing_arns:
        logger.info("Step 4b: Refreshing TA fields for %d EXISTING functions...", len(existing_arns))
    table = _get_table()
    for arn in existing_arns:
        ta_data = ta_by_arn[arn]
        existing_record = ddb_by_arn[arn]

        # Only update Trusted Advisor fields (these change between scans)
        try:
            table.update_item(
                Key={"function_arn": arn},
                UpdateExpression="SET days_to_deprecation = :d, avg_daily_invokes = :a, alert_status = :s, ta_last_updated = :t",
                ExpressionAttributeValues={
                    ":d": ta_data.get("days_to_deprecation", 0),
                    ":a": _convert_floats(ta_data.get("avg_daily_invokes", 0)),
                    ":s": ta_data.get("alert_status", ""),
                    ":t": ta_data.get("ta_last_updated", ""),
                },
            )
        except Exception as e:
            logger.error("Failed to refresh TA fields for %s: %s", arn, e)

        # Merge for response: existing DDB data + refreshed TA fields
        merged = {**existing_record, **ta_data}
        all_active.append(merged)

    # -----------------------------------------------------------------------
    # Step 6: RE-OPENED functions — was resolved, now back in TA
    # -----------------------------------------------------------------------
    if reopened_arns:
        logger.info("Step 4c: Re-opening %d previously resolved functions...", len(reopened_arns))
    for arn in reopened_arns:
        try:
            table.update_item(
                Key={"function_arn": arn},
                UpdateExpression="SET migration_status = :s, resolved_at = :r",
                ExpressionAttributeValues={":s": "DISCOVERED", ":r": None},
            )
        except Exception as e:
            logger.error("Failed to re-open %s: %s", arn, e)

    # -----------------------------------------------------------------------
    # Step 7: RESOLVED functions — no longer in TA scan
    # Before resolving, retry TA once to confirm they're truly gone
    # (TA API can occasionally miss functions due to caching/timing)
    # -----------------------------------------------------------------------
    if resolved_arns:
        logger.info("Step 4d: %d functions missing from TA — retrying to confirm...", len(resolved_arns))
        import time
        time.sleep(3)  # nosemgrep: arbitrary-sleep — intentional retry delay to confirm TA resolution
        ta_retry = query_trusted_advisor()
        ta_retry_arns = {fn["function_arn"] for fn in ta_retry.get("functions", [])}
        
        # Functions that reappeared on retry — NOT resolved, keep active
        false_positives = resolved_arns & ta_retry_arns
        if false_positives:
            logger.warning("TA retry recovered %d functions — NOT resolving: %s",
                           len(false_positives),
                           [a.split(":")[-2] for a in false_positives])
            # Refresh their TA fields from the retry data
            ta_retry_by_arn = {fn["function_arn"]: fn for fn in ta_retry.get("functions", [])}
            for arn in false_positives:
                existing_record = ddb_by_arn.get(arn, {})
                ta_data = ta_retry_by_arn.get(arn, {})
                merged = {**existing_record, **ta_data}
                all_active.append(merged)
        
        # Functions confirmed missing on both calls — truly resolved
        confirmed_resolved = resolved_arns - ta_retry_arns
        if confirmed_resolved:
            logger.info("Confirmed %d functions resolved (missing from both TA calls)", len(confirmed_resolved))
        for arn in confirmed_resolved:
            try:
                table.update_item(
                    Key={"function_arn": arn},
                    UpdateExpression="SET migration_status = :s, resolved_at = :t, alert_status = :a",
                    ExpressionAttributeValues={":s": "RESOLVED", ":t": now, ":a": "Green"},
                )
            except Exception as e:
                logger.error("Failed to resolve %s: %s", arn, e)

    # -----------------------------------------------------------------------
    # Step 8: AI Prioritization on all active functions
    # -----------------------------------------------------------------------
    if all_active:
        logger.info("Step 5: Running AI prioritization on %d active functions...", len(all_active))
        scores, reasonings = prioritize_with_bedrock(all_active)

        logger.info("Step 6: Updating DynamoDB with priority scores...")
        update_priority_scores(scores, reasonings)

        # Merge scores into response
        for fn in all_active:
            arn = fn.get("function_arn", "")
            score = scores.get(arn, 0)
            fn["priority_score"] = score
            fn["priority_label"] = _priority_label(score)
            fn["priority_reasoning"] = reasonings.get(arn, "")

    # Sort by priority score descending
    all_active.sort(key=lambda f: f.get("priority_score", 0), reverse=True)

    # Build summary message
    parts = [f"{len(all_active)} active"]
    if new_arns:
        parts.append(f"{len(new_arns)} new")
    if resolved_arns:
        parts.append(f"{len(resolved_arns)} resolved")
    if reopened_arns:
        parts.append(f"{len(reopened_arns)} re-opened")
    message = f"Scan complete: {', '.join(parts)}."

    # Sort by priority score descending
    # Convert Decimals to int/float for JSON serialization
    def decimal_default(obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj == int(obj) else float(obj)
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    return json.loads(json.dumps({
        "phase": "discover",
        "status": "complete",
        "message": message,
        "functions": all_active,
        "total": len(all_active),
        "new_count": len(new_arns),
        "resolved_count": len(resolved_arns),
        "reopened_count": len(reopened_arns),
    }, default=decimal_default))


@app.entrypoint
def invoke(payload):
    """Main entry point for the discover agent.
    
    Supports two actions:
      - "discover" (default): Full Phase 1 pipeline (TA query + enrich + prioritize)
      - "read_inventory": Read existing records from DynamoDB (fast, no API calls)
    """
    try:
        if isinstance(payload, str):
            payload = json.loads(payload)

        action = payload.get("action", "discover") if isinstance(payload, dict) else "discover"

        if action == "read_inventory":
            items = read_inventory()
            # Convert Decimals for JSON serialization
            def decimal_default(obj):
                if isinstance(obj, Decimal):
                    return int(obj) if obj == int(obj) else float(obj)
                raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
            items = json.loads(json.dumps(items, default=decimal_default))
            return {
                "phase": "discover",
                "status": "complete",
                "message": f"Loaded {len(items)} functions from inventory.",
                "functions": items,
                "total": len(items),
            }

        result = handle_discover()
        return result
    except Exception as e:
        logger.exception("Error processing discover request")
        return {"error": str(e)}


if __name__ == "__main__":
    app.run()
