"""
G.O.A.T. Trusted Advisor Agent - AWS Trusted Advisor check and recommendation queries
Plain Python handler with BedrockAgentCoreApp (sync entrypoint)

Receives structured JSON payloads from the orchestration agent's @tool functions,
routes to domain-specific handler functions, and calls AWS APIs directly via boto3.
NO Strands Agent SDK, NO Agent class, NO @tool decorators.
"""
import json
import logging
import boto3
from datetime import datetime, timezone
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from aws_utils import get_region

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = BedrockAgentCoreApp()
AWS_REGION = get_region()

# AWS Trusted Advisor API is a global service — always use us-east-1
TA_API_REGION = "us-east-1"

# Valid Trusted Advisor pillars for categorization
TA_PILLARS = [
    "cost_optimizing",
    "security",
    "performance",
    "fault_tolerance",
    "service_limits",
]

# Error message for missing Support plan (TA requires Business+ plan)
SUPPORT_PLAN_ERROR = (
    "AWS Trusted Advisor API access requires a Business, Enterprise On-Ramp, or "
    "Enterprise Support plan. Your account does not appear to have the required "
    "support plan. Please upgrade your support plan at "
    "https://console.aws.amazon.com/support/plans to use this feature."
)


def _is_subscription_error(error: Exception) -> bool:
    """Check if the error is a SubscriptionRequiredException (missing Support plan)."""
    error_str = str(error)
    return (
        "SubscriptionRequiredException" in error_str
        or "subscription" in error_str.lower()
    )


def _categorize_by_pillar(checks: list) -> dict:
    """Categorize Trusted Advisor checks by pillar.

    Returns a dict mapping each pillar to its list of checks.
    """
    categorized = {pillar: [] for pillar in TA_PILLARS}
    uncategorized = []

    for check in checks:
        category = check.get("category", "").lower().replace(" ", "_")
        if category in categorized:
            categorized[category].append(check)
        else:
            uncategorized.append(check)

    if uncategorized:
        categorized["other"] = uncategorized

    return categorized


def handle_describe_checks(params: dict) -> dict:
    """Retrieve available Trusted Advisor checks, optionally filtered by pillar.

    Optional params: pillar, language
    """
    try:
        support_client = boto3.client("support", region_name=TA_API_REGION)

        language = params.get("language", "en")
        response = support_client.describe_trusted_advisor_checks(language=language)
        checks = response.get("checks", [])

        # Filter by pillar if specified
        pillar = params.get("pillar")
        if pillar:
            pillar_normalized = pillar.lower().replace(" ", "_")
            checks = [
                c for c in checks
                if c.get("category", "").lower().replace(" ", "_") == pillar_normalized
            ]

        categorized = _categorize_by_pillar(checks)

        return {
            "success": True,
            "domain": "trusted_advisor",
            "data": {
                "checks": checks,
                "categorized": {k: len(v) for k, v in categorized.items()},
                "count": len(checks),
            },
            "formattedText": _format_checks(checks, categorized),
            "metadata": {
                "sourceApi": "support:DescribeTrustedAdvisorChecks",
                "queryTimestamp": datetime.now(timezone.utc).isoformat(),
                "dataFreshness": "real-time",
            },
        }

    except Exception as e:
        if _is_subscription_error(e):
            return {"success": False, "error": SUPPORT_PLAN_ERROR}
        return {"success": False, "error": f"Describe checks query failed: {str(e)}"}


def handle_check_result(params: dict) -> dict:
    """Retrieve the result of a specific Trusted Advisor check.

    Required params: checkId
    """
    try:
        check_id = params.get("checkId") or params.get("check_id")
        if not check_id:
            return {
                "success": False,
                "error": "checkId is required for check result queries.",
            }

        support_client = boto3.client("support", region_name=TA_API_REGION)

        response = support_client.describe_trusted_advisor_check_result(
            checkId=check_id, language=params.get("language", "en")
        )
        result = response.get("result", {})

        return {
            "success": True,
            "domain": "trusted_advisor",
            "data": {"result": _serialize_check_result(result), "checkId": check_id},
            "formattedText": _format_check_result(result, check_id),
            "metadata": {
                "sourceApi": "support:DescribeTrustedAdvisorCheckResult",
                "queryTimestamp": datetime.now(timezone.utc).isoformat(),
                "dataFreshness": "up to 24 hours",
            },
        }

    except Exception as e:
        if _is_subscription_error(e):
            return {"success": False, "error": SUPPORT_PLAN_ERROR}
        return {"success": False, "error": f"Check result query failed: {str(e)}"}


def handle_list_recommendations(params: dict) -> dict:
    """List Trusted Advisor recommendations by retrieving checks and their summaries.

    Retrieves all checks, then fetches summaries to identify checks with warnings/errors.
    Optional params: pillar, maxResults
    """
    try:
        support_client = boto3.client("support", region_name=TA_API_REGION)

        language = params.get("language", "en")
        response = support_client.describe_trusted_advisor_checks(language=language)
        checks = response.get("checks", [])

        # Filter by pillar if specified
        pillar = params.get("pillar")
        if pillar:
            pillar_normalized = pillar.lower().replace(" ", "_")
            checks = [
                c for c in checks
                if c.get("category", "").lower().replace(" ", "_") == pillar_normalized
            ]

        # Get summaries for all checks to find those with action items.
        # The API accepts a maximum of 100 checkIds per call, so we batch.
        check_ids = [c["id"] for c in checks]
        summaries = []
        BATCH_SIZE = 100
        for i in range(0, len(check_ids), BATCH_SIZE):
            batch = check_ids[i : i + BATCH_SIZE]
            summary_response = support_client.describe_trusted_advisor_check_summaries(
                checkIds=batch
            )
            summaries.extend(summary_response.get("summaries", []))

        # Build a lookup from check ID to check metadata
        check_lookup = {c["id"]: c for c in checks}

        # Refresh stale checks (not_available) before collecting results.
        # This is fire-and-forget — results may not be immediate but subsequent
        # calls will have fresh data.
        for summary in summaries:
            if summary.get("status") == "not_available":
                try:
                    support_client.refresh_trusted_advisor_check(
                        checkId=summary.get("checkId", "")
                    )
                except Exception:
                    pass  # Best-effort refresh; some checks can't be refreshed

        # Include checks with warnings, errors, AND not_available (stale data
        # that likely has findings but hasn't been refreshed recently)
        recommendations = []
        for summary in summaries:
            status = summary.get("status", "ok")
            if status in ("warning", "error", "not_available"):
                check_id = summary.get("checkId", "")
                check_meta = check_lookup.get(check_id, {})
                flagged = summary.get("resourcesSummary", {}).get("resourcesFlagged", 0)
                # Skip not_available checks that have zero flagged resources
                if status == "not_available" and flagged == 0:
                    continue
                recommendations.append({
                    "checkId": check_id,
                    "name": check_meta.get("name", "N/A"),
                    "category": check_meta.get("category", "N/A"),
                    "status": status,
                    "resourcesSummary": summary.get("resourcesSummary", {}),
                    "timestamp": summary.get("timestamp", "N/A"),
                })

        max_results = int(params.get("maxResults") or params.get("max_results", 50))
        recommendations = recommendations[:max_results]

        categorized = _categorize_recommendations_by_pillar(recommendations)

        return {
            "success": True,
            "domain": "trusted_advisor",
            "data": {
                "recommendations": recommendations,
                "categorized": {k: len(v) for k, v in categorized.items()},
                "count": len(recommendations),
            },
            "formattedText": _format_recommendations(recommendations, categorized),
            "metadata": {
                "sourceApi": "support:DescribeTrustedAdvisorCheckSummaries",
                "queryTimestamp": datetime.now(timezone.utc).isoformat(),
                "dataFreshness": "up to 24 hours",
            },
        }

    except Exception as e:
        if _is_subscription_error(e):
            return {"success": False, "error": SUPPORT_PLAN_ERROR}
        return {
            "success": False,
            "error": f"List recommendations query failed: {str(e)}",
        }


def _categorize_recommendations_by_pillar(recommendations: list) -> dict:
    """Categorize recommendations by pillar."""
    categorized = {pillar: [] for pillar in TA_PILLARS}
    uncategorized = []

    for rec in recommendations:
        category = rec.get("category", "").lower().replace(" ", "_")
        if category in categorized:
            categorized[category].append(rec)
        else:
            uncategorized.append(rec)

    if uncategorized:
        categorized["other"] = uncategorized

    # Remove empty pillars for cleaner output
    return {k: v for k, v in categorized.items() if v}


def _serialize_check_result(result: dict) -> dict:
    """Serialize check result for JSON response (convert datetimes)."""
    serialized = {}
    for key, value in result.items():
        if isinstance(value, datetime):
            serialized[key] = value.isoformat()
        elif isinstance(value, list):
            serialized[key] = [
                {k: v.isoformat() if isinstance(v, datetime) else v for k, v in item.items()}
                if isinstance(item, dict) else item
                for item in value
            ]
        else:
            serialized[key] = value
    return serialized


def _format_checks(checks: list, categorized: dict) -> str:
    """Format Trusted Advisor checks with pillar categorization."""
    if not checks:
        return "No Trusted Advisor checks found for the specified criteria."

    lines = [f"Trusted Advisor Checks ({len(checks)} found)", ""]

    # Show pillar summary
    lines.append("By Pillar:")
    for pillar, pillar_checks in categorized.items():
        if pillar_checks:
            display_pillar = pillar.replace("_", " ").title()
            lines.append(f"  {display_pillar}: {len(pillar_checks)} checks")
    lines.append("")

    # Show individual checks
    for check in checks:
        name = check.get("name", "N/A")
        category = check.get("category", "N/A")
        description = check.get("description", "N/A")
        # Truncate long descriptions
        if len(description) > 120:
            description = description[:120] + "..."

        lines.append(f"  [{category}] {name}")
        lines.append(f"    {description}")

    return "\n".join(lines)


def _format_check_result(result: dict, check_id: str) -> str:
    """Format a single check result with status and flagged resources."""
    if not result:
        return f"No result available for check: {check_id}"

    status = result.get("status", "N/A")
    timestamp = result.get("timestamp", "N/A")
    resources_summary = result.get("resourcesSummary", {})
    flagged_resources = result.get("flaggedResources", [])

    lines = [
        f"Trusted Advisor Check Result (ID: {check_id})",
        f"  Status: {status} | Last Refreshed: {timestamp}",
        "",
    ]

    # Resource summary
    processed = resources_summary.get("resourcesProcessed", 0)
    flagged = resources_summary.get("resourcesFlagged", 0)
    ignored = resources_summary.get("resourcesIgnored", 0)
    suppressed = resources_summary.get("resourcesSuppressed", 0)
    lines.append(
        f"  Resources: {processed} processed, {flagged} flagged, "
        f"{ignored} ignored, {suppressed} suppressed"
    )
    lines.append("")

    # Flagged resources (show up to 10)
    if flagged_resources:
        lines.append(f"  Flagged Resources ({len(flagged_resources)} total):")
        for resource in flagged_resources[:10]:
            status_val = resource.get("status", "N/A")
            metadata = resource.get("metadata", [])
            metadata_str = " | ".join(str(m) for m in metadata[:5]) if metadata else "N/A"
            lines.append(f"    Status: {status_val} | {metadata_str}")
        if len(flagged_resources) > 10:
            lines.append(f"    ... and {len(flagged_resources) - 10} more")

    return "\n".join(lines)


def _format_recommendations(recommendations: list, categorized: dict) -> str:
    """Format actionable recommendations with pillar categorization."""
    if not recommendations:
        return "No actionable Trusted Advisor recommendations found. All checks are passing."

    lines = [
        f"Trusted Advisor Recommendations ({len(recommendations)} actionable)",
        "",
    ]

    # Show by pillar
    for pillar, recs in categorized.items():
        display_pillar = pillar.replace("_", " ").title()
        lines.append(f"  {display_pillar} ({len(recs)}):")
        for rec in recs:
            name = rec.get("name", "N/A")
            status = rec.get("status", "N/A")
            resources = rec.get("resourcesSummary", {})
            flagged = resources.get("resourcesFlagged", 0)
            status_icon = "⚠️" if status == "warning" else "🔴" if status == "error" else "🔄" if status == "not_available" else "ℹ️"
            lines.append(
                f"    {status_icon} {name} | Status: {status} | "
                f"Flagged Resources: {flagged}"
            )
        lines.append("")

    return "\n".join(lines)


def handle_action(action: str, params: dict) -> dict:
    """Route to the appropriate handler based on action."""
    handlers = {
        "describe_checks": handle_describe_checks,
        "describe_check_result": handle_check_result,
        "list_recommendations": handle_list_recommendations,
    }
    handler = handlers.get(action)
    if not handler:
        return {
            "success": False,
            "error": f"Unknown action: {action}. Supported actions: {', '.join(handlers.keys())}",
        }
    return handler(params)


@app.entrypoint
def main_handler(payload):
    """
    Main entry point for the Trusted Advisor Agent.
    Receives JSON payload, routes to handler based on action field.
    Synchronous — returns dict, not async generator.

    Payload format: {"action": "describe_checks", "params": {...}}
    """
    try:
        if isinstance(payload, str):
            payload = json.loads(payload)

        logger.info(f"TA agent received payload: {json.dumps(payload)[:500]}")

        action = payload.get("action")
        if not action:
            if payload.get("prompt"):
                logger.warning("Received raw prompt instead of structured action, defaulting to list_recommendations")
            else:
                logger.warning("No action in payload, defaulting to list_recommendations")
            action = "list_recommendations"

        params = payload.get("params", {})
        return handle_action(action, params)

    except Exception as e:
        return {"success": False, "error": f"Request failed: {str(e)}"}


if __name__ == "__main__":
    app.run()
