"""
G.O.A.T. Health Agent - AWS Health Dashboard event queries
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

# AWS Health API is a global service — always use us-east-1
HEALTH_API_REGION = "us-east-1"


def handle_describe_events(params: dict) -> dict:
    """Retrieve AWS Health events filtered by region, service, type, and time range.

    Returns both account-specific events (Personal Health Dashboard) and public
    service events (Service Health History) by default, then filters client-side
    if a specific eventScopeCode is requested.

    Optional params: region, service, event_type, startTime, endTime, eventScopeCode, maxResults
    eventScopeCode: "ACCOUNT_SPECIFIC", "PUBLIC", or omit for all events
    maxResults: limit number of events returned (default 25)
    """
    try:
        health_client = boto3.client("health", region_name=HEALTH_API_REGION)

        filters = {}
        if params.get("region"):
            filters["regions"] = [params["region"]]
        if params.get("service"):
            filters["services"] = [params["service"]]
        if params.get("event_type"):
            filters["eventTypeCategories"] = [params["event_type"]]

        # Use both startTimes and lastUpdatedTimes to maximize event coverage.
        # startTimes catches events that started in the window.
        # lastUpdatedTimes catches events active/updated during the window.
        if params.get("startTime") or params.get("endTime"):
            time_range = {}
            if params.get("startTime"):
                time_range["from"] = datetime.fromisoformat(params["startTime"])
            if params.get("endTime"):
                time_range["to"] = datetime.fromisoformat(params["endTime"])
            # Use startTimes as the primary filter — this is what the console uses
            filters["startTimes"] = [time_range]

        # NOTE: eventScopeCode is NOT a valid filter parameter for describe_events.
        # It's a field in the response. We query all events and filter client-side.
        requested_scope = params.get("eventScopeCode")
        max_results = int(params.get("maxResults") or params.get("max_results", 25))

        kwargs = {}
        if filters:
            kwargs["filter"] = filters

        # Paginate to collect matching events, but cap at max_results
        all_events = []
        while len(all_events) < max_results * 2:  # Fetch extra for client-side filtering
            response = health_client.describe_events(**kwargs)
            all_events.extend(response.get("events", []))
            next_token = response.get("nextToken")
            if not next_token:
                break
            kwargs["nextToken"] = next_token

        # Client-side filtering by eventScopeCode if requested
        if requested_scope:
            all_events = [
                e for e in all_events
                if e.get("eventScopeCode", "").upper() == requested_scope.upper()
            ]

        # Deduplicate by event ARN
        seen_arns = set()
        unique_events = []
        for event in all_events:
            arn = event.get("arn", "")
            if arn not in seen_arns:
                seen_arns.add(arn)
                unique_events.append(event)

        # Sort by start time descending (most recent first) and limit results
        unique_events.sort(
            key=lambda e: e.get("startTime", datetime.min) if isinstance(e.get("startTime"), datetime)
            else datetime.min,
            reverse=True,
        )
        unique_events = unique_events[:max_results]

        logger.info(f"Health agent returning {len(unique_events)} events (scope: {requested_scope or 'all'}, max: {max_results})")

        return {
            "success": True,
            "domain": "health",
            "data": {"events": _serialize_events(unique_events), "count": len(unique_events)},
            "formattedText": _format_health_events(unique_events),
            "metadata": {
                "sourceApi": "health:DescribeEvents",
                "queryTimestamp": datetime.now(timezone.utc).isoformat(),
                "dataFreshness": "real-time",
            },
        }

    except Exception as e:
        return {"success": False, "error": f"Describe events query failed: {str(e)}"}


def handle_affected_entities(params: dict) -> dict:
    """Get resources affected by a specific health event.

    Required params: event_arn
    """
    try:
        event_arn = params.get("event_arn")
        if not event_arn:
            return {
                "success": False,
                "error": "event_arn is required for affected entities queries.",
            }

        health_client = boto3.client("health", region_name=HEALTH_API_REGION)

        response = health_client.describe_affected_entities(
            filter={"eventArns": [event_arn]}
        )
        entities = response.get("entities", [])

        return {
            "success": True,
            "domain": "health",
            "data": {"entities": _serialize_entities(entities), "count": len(entities)},
            "formattedText": _format_affected_entities(entities, event_arn),
            "metadata": {
                "sourceApi": "health:DescribeAffectedEntities",
                "queryTimestamp": datetime.now(timezone.utc).isoformat(),
                "dataFreshness": "real-time",
            },
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Affected entities query failed: {str(e)}",
        }


def handle_event_details(params: dict) -> dict:
    """Get detailed description and remediation for a health event.

    Required params: event_arn
    """
    try:
        event_arn = params.get("event_arn")
        if not event_arn:
            return {
                "success": False,
                "error": "event_arn is required for event details queries.",
            }

        health_client = boto3.client("health", region_name=HEALTH_API_REGION)

        response = health_client.describe_event_details(eventArns=[event_arn])
        successful_set = response.get("successfulSet", [])
        failed_set = response.get("failedSet", [])

        if failed_set:
            error_msg = failed_set[0].get("errorName", "Unknown error")
            return {
                "success": False,
                "error": f"Failed to retrieve event details: {error_msg}",
            }

        return {
            "success": True,
            "domain": "health",
            "data": {"details": _serialize_event_details(successful_set)},
            "formattedText": _format_event_details(successful_set),
            "metadata": {
                "sourceApi": "health:DescribeEventDetails",
                "queryTimestamp": datetime.now(timezone.utc).isoformat(),
                "dataFreshness": "real-time",
            },
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Event details query failed: {str(e)}",
        }


def _serialize_events(events: list) -> list:
    """Serialize health event objects for JSON response (convert datetimes)."""
    serialized = []
    for event in events:
        item = {}
        for key, value in event.items():
            item[key] = value.isoformat() if isinstance(value, datetime) else value
        serialized.append(item)
    return serialized


def _serialize_entities(entities: list) -> list:
    """Serialize affected entity objects for JSON response."""
    serialized = []
    for entity in entities:
        item = {}
        for key, value in entity.items():
            item[key] = value.isoformat() if isinstance(value, datetime) else value
        serialized.append(item)
    return serialized


def _serialize_event_details(details: list) -> list:
    """Serialize event detail objects for JSON response."""
    serialized = []
    for detail in details:
        item = {}
        event = detail.get("event", {})
        description = detail.get("eventDescription", {})
        item["event"] = {
            k: v.isoformat() if isinstance(v, datetime) else v
            for k, v in event.items()
        }
        item["eventDescription"] = description
        serialized.append(item)
    return serialized


def _format_health_events(events: list) -> str:
    """Format health events with event type, affected services, regions, start time, status."""
    if not events:
        return "No matching health events found for the specified criteria."

    lines = [f"AWS Health Events ({len(events)} found)", ""]
    for event in events:
        event_type = event.get("eventTypeCategory", "N/A")
        service = event.get("service", "N/A")
        region = event.get("region", "N/A")
        start_time = event.get("startTime", "N/A")
        if isinstance(start_time, datetime):
            start_time = start_time.isoformat()
        status = event.get("statusCode", "N/A")
        event_code = event.get("eventTypeCode", "N/A")

        lines.append(
            f"  Type: {event_type} | Service: {service} | "
            f"Region: {region} | Start: {start_time} | "
            f"Status: {status} | Code: {event_code}"
        )

    return "\n".join(lines)


def _format_affected_entities(entities: list, event_arn: str) -> str:
    """Format affected entities for a health event."""
    if not entities:
        return f"No affected entities found for event: {event_arn}"

    lines = [f"Affected Entities ({len(entities)} found) for event: {event_arn}", ""]
    for entity in entities:
        entity_value = entity.get("entityValue", "N/A")
        status = entity.get("statusCode", "N/A")
        last_updated = entity.get("lastUpdatedTime", "N/A")
        if isinstance(last_updated, datetime):
            last_updated = last_updated.isoformat()

        lines.append(
            f"  Entity: {entity_value} | Status: {status} | "
            f"Last Updated: {last_updated}"
        )

    return "\n".join(lines)


def _format_event_details(details: list) -> str:
    """Format event details with description and remediation."""
    if not details:
        return "No event details available."

    lines = []
    for detail in details:
        event = detail.get("event", {})
        description = detail.get("eventDescription", {})

        event_type = event.get("eventTypeCategory", "N/A")
        service = event.get("service", "N/A")
        region = event.get("region", "N/A")
        start_time = event.get("startTime", "N/A")
        if isinstance(start_time, datetime):
            start_time = start_time.isoformat()
        status = event.get("statusCode", "N/A")
        desc_text = description.get("latestDescription", "No description available.")

        lines.append(f"Event Details:")
        lines.append(
            f"  Type: {event_type} | Service: {service} | "
            f"Region: {region} | Start: {start_time} | Status: {status}"
        )
        lines.append(f"  Description: {desc_text}")
        lines.append("")

    return "\n".join(lines)


def handle_action(action: str, params: dict) -> dict:
    """Route to the appropriate handler based on action."""
    handlers = {
        "describe_events": handle_describe_events,
        "describe_affected_entities": handle_affected_entities,
        "describe_event_details": handle_event_details,
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
    Main entry point for the Health Agent.
    Receives JSON payload, routes to handler based on action field.
    Synchronous — returns dict, not async generator.

    Payload format: {"action": "describe_events", "params": {...}}
    """
    try:
        if isinstance(payload, str):
            payload = json.loads(payload)

        logger.info(f"Health agent received payload: {json.dumps(payload)[:500]}")

        action = payload.get("action")
        if not action:
            # Auto-default: if no action provided, use describe_events as the
            # most common operation. This handles cases where the orchestration
            # LLM omits the action parameter.
            if payload.get("prompt"):
                logger.warning("Received raw prompt instead of structured action, defaulting to describe_events")
            else:
                logger.warning("No action in payload, defaulting to describe_events")
            action = "describe_events"

        params = payload.get("params", {})
        return handle_action(action, params)

    except Exception as e:
        return {"success": False, "error": f"Request failed: {str(e)}"}


if __name__ == "__main__":
    app.run()
