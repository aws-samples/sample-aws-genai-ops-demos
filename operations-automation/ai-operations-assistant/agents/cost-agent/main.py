"""
G.O.A.T. Cost Agent - AWS Cost Explorer and Cost Optimization Hub queries
Plain Python handler with BedrockAgentCoreApp (sync entrypoint)

Receives structured JSON payloads from the orchestration agent's @tool functions,
routes to domain-specific handler functions, and calls AWS APIs directly via boto3.
NO Strands Agent SDK, NO Agent class, NO @tool decorators.
"""
import json
import time
import logging
import hashlib
import boto3
from datetime import datetime, timezone, timedelta
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from aws_utils import get_region

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = BedrockAgentCoreApp()
AWS_REGION = get_region()

# Maximum supported time range for cost queries (12 months)
MAX_TIME_RANGE_DAYS = 366

# Retry configuration for Cost Explorer rate limiting
MAX_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 2.0

# In-memory cache for Cost Explorer responses (TTL: 5 minutes)
_cost_cache = {}
CACHE_TTL_SECONDS = 300


def _cache_key(kwargs: dict) -> str:
    """Generate a cache key from the API call parameters."""
    return hashlib.md5(json.dumps(kwargs, sort_keys=True, default=str).encode(), usedforsecurity=False).hexdigest()


def _get_cached(key: str):
    """Return cached result if still valid, else None."""
    entry = _cost_cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL_SECONDS:
        logger.info(f"Cache hit for cost query (age: {time.time() - entry['ts']:.0f}s)")
        return entry["data"]
    return None


def _set_cache(key: str, data):
    """Store result in cache."""
    _cost_cache[key] = {"data": data, "ts": time.time()}


def _retry_with_backoff(func, *args, **kwargs):
    """Execute a function with exponential backoff retry for rate limiting.

    Retries on LimitExceededException and Throttling errors.
    Backoff: 2s, 4s, 8s, 16s, 32s
    """
    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_str = str(e)
            is_throttle = (
                "LimitExceededException" in error_str
                or "Throttling" in error_str
                or "Rate exceeded" in error_str
                or "TooManyRequestsException" in error_str
            )
            if is_throttle and attempt < MAX_RETRIES - 1:
                wait_time = INITIAL_BACKOFF_SECONDS * (2 ** attempt)
                logger.warning(f"Rate limited (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise


def _validate_time_range(start_date: str, end_date: str) -> None:
    """Validate that the time range does not exceed 12 months.

    Raises ValueError with a descriptive message when the range is too large.
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    delta = (end - start).days
    if delta > MAX_TIME_RANGE_DAYS:
        raise ValueError(
            f"Time range of {delta} days exceeds the maximum supported range of 12 months "
            f"({MAX_TIME_RANGE_DAYS} days). Please narrow your query to a 12-month window."
        )
    if delta < 0:
        raise ValueError(
            f"Invalid time range: start date ({start_date}) is after end date ({end_date})."
        )


def _format_currency(amount: float, unit: str = "USD") -> str:
    """Format a numeric amount as a currency string."""
    return f"${amount:,.2f} {unit}"


def _format_percentage_change(current: float, previous: float) -> str:
    """Calculate and format percentage change between two values."""
    if previous == 0:
        return "N/A (no previous data)"
    change = ((current - previous) / previous) * 100
    direction = "increase" if change > 0 else "decrease"
    return f"{abs(change):.1f}% {direction}"


def handle_cost_and_usage(params: dict) -> dict:
    """Retrieve cost and usage data from AWS Cost Explorer.

    Optional params: startDate, endDate, granularity, groupBy, filter, metric
    If startDate/endDate are not provided, defaults to the last 30 days with DAILY
    granularity for a useful overview.
    """
    try:
        start_date = params.get("startDate") or params.get("start_date")
        end_date = params.get("endDate") or params.get("end_date")
        granularity = params.get("granularity", "MONTHLY")
        group_by = params.get("groupBy") or params.get("group_by")
        metric = params.get("metric", "UNBLENDED_COST")

        # Normalize metric — BLENDED_COST often fails if not explicitly enabled.
        # Fall back to UNBLENDED_COST which is always available.
        VALID_METRICS = ["UNBLENDED_COST", "BLENDED_COST", "AMORTIZED_COST", "NET_UNBLENDED_COST", "NET_AMORTIZED_COST", "USAGE_QUANTITY", "NORMALIZED_USAGE_AMOUNT"]
        if metric not in VALID_METRICS:
            metric = "UNBLENDED_COST"

        today = datetime.now(timezone.utc).date()

        # Smart defaults: if no dates provided, use last 30 days
        if not start_date or not end_date:
            if not end_date:
                end_date = today.strftime("%Y-%m-%d")
            if not start_date:
                start_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")

        # When grouping by SERVICE, use MONTHLY to avoid massive responses
        # (DAILY + SERVICE grouping = 20+ services × 30 days = 600+ rows)
        if group_by and granularity == "DAILY":
            start_dt_check = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt_check = datetime.strptime(end_date, "%Y-%m-%d")
            if (end_dt_check - start_dt_check).days > 7:
                granularity = "MONTHLY"
                logger.info(f"Switched to MONTHLY granularity for grouped query over {(end_dt_check - start_dt_check).days} days")

        # Auto-adjust granularity if range is too large for DAILY (max ~14 months)
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        # Sanity check: if end_date is in the future, cap it to today
        if end_dt.date() > today:
            end_date = today.strftime("%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        # Sanity check: if dates are unreasonably old (LLM hallucinated wrong year),
        # interpret "past 3 months" relative to today instead
        earliest_allowed = today - timedelta(days=MAX_TIME_RANGE_DAYS)
        if start_dt.date() < earliest_allowed:
            logger.warning(f"Start date {start_date} is too far back, adjusting to {earliest_allowed}")
            start_date = earliest_allowed.strftime("%Y-%m-%d")
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")

        range_days = (end_dt - start_dt).days

        if range_days < 0:
            return {
                "success": False,
                "error": f"Invalid time range: start date ({start_date}) is after end date ({end_date}).",
            }

        # Cost Explorer DAILY granularity supports ~14 months max
        if granularity == "DAILY" and range_days > 395:
            granularity = "MONTHLY"

        # Cap at 12 months for safety
        if range_days > MAX_TIME_RANGE_DAYS:
            start_date = (end_dt - timedelta(days=MAX_TIME_RANGE_DAYS)).strftime("%Y-%m-%d")

        _validate_time_range(start_date, end_date)

        ce_client = boto3.client("ce", region_name=AWS_REGION)

        kwargs = {
            "TimePeriod": {"Start": start_date, "End": end_date},
            "Granularity": granularity,
            "Metrics": [metric],
        }

        if group_by:
            kwargs["GroupBy"] = [
                {"Type": "DIMENSION", "Key": key} for key in group_by
            ]

        # Check cache first to avoid rate limiting
        cache_k = _cache_key(kwargs)
        cached = _get_cached(cache_k)
        if cached is not None:
            results = cached
        else:
            response = _retry_with_backoff(ce_client.get_cost_and_usage, **kwargs)
            results = response.get("ResultsByTime", [])
            _set_cache(cache_k, results)

        # If the requested metric returned no data and it wasn't UNBLENDED_COST,
        # retry with UNBLENDED_COST as fallback
        if not results and metric != "UNBLENDED_COST":
            logger.warning(f"Metric {metric} returned no results, falling back to UNBLENDED_COST")
            metric = "UNBLENDED_COST"
            kwargs["Metrics"] = [metric]
            cache_k2 = _cache_key(kwargs)
            cached2 = _get_cached(cache_k2)
            if cached2 is not None:
                results = cached2
            else:
                response = _retry_with_backoff(ce_client.get_cost_and_usage, **kwargs)
                results = response.get("ResultsByTime", [])
                _set_cache(cache_k2, results)

        return {
            "success": True,
            "domain": "cost",
            "data": {
                "resultsByTime": results,
                "granularity": granularity,
                "metric": metric,
            },
            "formattedText": _format_cost_results(results, metric, start_date, end_date),
            "metadata": {
                "sourceApi": "ce:GetCostAndUsage",
                "queryTimestamp": datetime.now(timezone.utc).isoformat(),
                "dataFreshness": "up to 24 hours",
            },
        }

    except ValueError as ve:
        return {"success": False, "error": str(ve)}
    except Exception as e:
        return {"success": False, "error": f"Cost and usage query failed: {str(e)}"}


def handle_cost_forecast(params: dict) -> dict:
    """Retrieve cost forecast from AWS Cost Explorer.

    Required params: startDate, endDate, granularity, metric
    """
    try:
        start_date = params.get("startDate") or params.get("start_date")
        end_date = params.get("endDate") or params.get("end_date")
        granularity = params.get("granularity", "MONTHLY")
        metric = params.get("metric", "UNBLENDED_COST")

        if not start_date or not end_date:
            return {
                "success": False,
                "error": "Both startDate and endDate are required for cost forecast queries.",
            }

        ce_client = boto3.client("ce", region_name=AWS_REGION)

        cache_k = _cache_key({"forecast": start_date, "end": end_date, "g": granularity, "m": metric})
        cached = _get_cached(cache_k)
        if cached is not None:
            total, forecast_results = cached["total"], cached["forecast"]
        else:
            response = _retry_with_backoff(ce_client.get_cost_forecast,
                TimePeriod={"Start": start_date, "End": end_date},
                Granularity=granularity,
                Metric=metric,
            )
            total = response.get("Total", {})
            forecast_results = response.get("ForecastResultsByTime", [])
            _set_cache(cache_k, {"total": total, "forecast": forecast_results})

        total_amount = float(total.get("Amount", 0))
        total_unit = total.get("Unit", "USD")

        formatted_lines = [
            f"Cost Forecast ({start_date} to {end_date})",
            f"Total Forecast: {_format_currency(total_amount, total_unit)}",
            f"Granularity: {granularity} | Metric: {metric}",
            "",
        ]
        for period in forecast_results:
            period_start = period["TimePeriod"]["Start"]
            period_end = period["TimePeriod"]["End"]
            mean = float(period.get("MeanValue", 0))
            formatted_lines.append(
                f"  {period_start} to {period_end}: {_format_currency(mean, total_unit)}"
            )

        return {
            "success": True,
            "domain": "cost",
            "data": {
                "total": total,
                "forecastResultsByTime": forecast_results,
                "granularity": granularity,
                "metric": metric,
            },
            "formattedText": "\n".join(formatted_lines),
            "metadata": {
                "sourceApi": "ce:GetCostForecast",
                "queryTimestamp": datetime.now(timezone.utc).isoformat(),
                "dataFreshness": "real-time forecast",
            },
        }

    except Exception as e:
        return {"success": False, "error": f"Cost forecast query failed: {str(e)}"}


def handle_recommendations(params: dict) -> dict:
    """Retrieve cost optimization recommendations from Cost Optimization Hub.

    Optional params: category, maxResults
    """
    try:
        coh_client = boto3.client("cost-optimization-hub", region_name="us-east-1")

        kwargs = {}
        max_results = params.get("maxResults") or params.get("max_results", 20)
        kwargs["maxResults"] = min(int(max_results), 100)

        category = params.get("category")
        if category:
            kwargs["filter"] = {"actionTypes": [category]}

        cache_k = _cache_key({"recs": category or "all", "max": max_results})
        cached = _get_cached(cache_k)
        if cached is not None:
            items = cached
        else:
            response = _retry_with_backoff(coh_client.list_recommendations, **kwargs)
            items = response.get("items", [])
            _set_cache(cache_k, items)

        return {
            "success": True,
            "domain": "cost",
            "data": {"recommendations": items, "count": len(items)},
            "formattedText": _format_recommendations(items),
            "metadata": {
                "sourceApi": "cost-optimization-hub:ListRecommendations",
                "queryTimestamp": datetime.now(timezone.utc).isoformat(),
                "dataFreshness": "up to 24 hours",
            },
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Recommendations query failed: {str(e)}",
        }


def _format_cost_results(results: list, metric: str, start_date: str, end_date: str) -> str:
    """Format cost results with currency values, time ranges, and percentage changes."""
    if not results:
        return f"No cost data found for the period {start_date} to {end_date}."

    lines = [f"Cost and Usage ({start_date} to {end_date}) — Metric: {metric}", ""]

    previous_amount = None
    for period in results:
        period_start = period["TimePeriod"]["Start"]
        period_end = period["TimePeriod"]["End"]

        # Handle grouped results
        if period.get("Groups"):
            for group in period["Groups"]:
                keys = ", ".join(group.get("Keys", []))
                # Try both metric key formats (API returns PascalCase like "UnblendedCost")
                metrics = group.get("Metrics", {})
                metric_data = metrics.get(metric) or metrics.get(metric.replace("_", "").title().replace(" ", "")) or next(iter(metrics.values()), {})
                amount = float(metric_data.get("Amount", 0))
                unit = metric_data.get("Unit", "USD")
                lines.append(f"  {period_start} to {period_end} | {keys}: {_format_currency(amount, unit)}")
        else:
            # Ungrouped total — try multiple key formats
            total = period.get("Total", {})
            total_data = total.get(metric) or total.get(metric.replace("_", "").title().replace(" ", "")) or next(iter(total.values()), {})
            amount = float(total_data.get("Amount", 0))
            unit = total_data.get("Unit", "USD")
            change_str = ""
            if previous_amount is not None:
                change_str = f" ({_format_percentage_change(amount, previous_amount)})"
            lines.append(
                f"  {period_start} to {period_end}: {_format_currency(amount, unit)}{change_str}"
            )
            previous_amount = amount

    return "\n".join(lines)


def _format_recommendations(items: list) -> str:
    """Format cost optimization recommendations."""
    if not items:
        return "No cost optimization recommendations found."

    lines = [f"Cost Optimization Recommendations ({len(items)} found)", ""]
    for item in items:
        rec_id = item.get("recommendationId", "N/A")
        action_type = item.get("actionType", "N/A")
        estimated_savings = item.get("estimatedMonthlySavings", 0)
        currency = item.get("currencyCode", "USD")
        resource_id = item.get("resourceId", "N/A")

        savings_str = _format_currency(float(estimated_savings), currency) if estimated_savings else "N/A"
        lines.append(
            f"  [{action_type}] Resource: {resource_id} | "
            f"Est. Monthly Savings: {savings_str} | ID: {rec_id}"
        )

    return "\n".join(lines)


def handle_action(action: str, params: dict) -> dict:
    """Route to the appropriate handler based on action."""
    handlers = {
        "get_cost_and_usage": handle_cost_and_usage,
        "get_cost_forecast": handle_cost_forecast,
        "list_recommendations": handle_recommendations,
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
    Main entry point for the Cost Agent.
    Receives JSON payload, routes to handler based on action field.
    Synchronous — returns dict, not async generator.

    Payload format: {"action": "get_cost_and_usage", "params": {...}}
    """
    try:
        if isinstance(payload, str):
            payload = json.loads(payload)

        logger.info(f"Cost agent received payload: {json.dumps(payload)[:500]}")

        action = payload.get("action")
        if not action:
            if payload.get("prompt"):
                logger.warning("Received raw prompt instead of structured action, defaulting to get_cost_and_usage")
            else:
                logger.warning("No action in payload, defaulting to get_cost_and_usage")
            action = "get_cost_and_usage"

        params = payload.get("params", {})
        return handle_action(action, params)

    except Exception as e:
        return {"success": False, "error": f"Request failed: {str(e)}"}


if __name__ == "__main__":
    app.run()
