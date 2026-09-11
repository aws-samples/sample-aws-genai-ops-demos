"""
AWS Health cross-check for the account scan (issue #141).

The catalog + scan are the source of truth for "what of mine is affected".
AWS Health adds one thing they cannot: AWS's own notice naming a specific
resource. Planned lifecycle events (AWS_LAMBDA_PLANNED_LIFECYCLE_EVENT,
AWS_RDS_PLANNED_LIFECYCLE_EVENT, ...) carry affected entities with a per-entity
status (PENDING / RESOLVED). This module joins those entities with the scanned
inventory by ARN (or identifier) so each resource can say "AWS Health flags
this one too".

Two calls, no storage of its own:
  DescribeEvents           open/upcoming scheduledChange + accountNotification
  DescribeAffectedEntities entities of those events (10 event ARNs per call)

The Health API requires Business/Enterprise Support; without it the call fails
with SubscriptionRequiredException and the scan simply reports Health as
unavailable. Health is a global service: its endpoint lives in one region per
partition, which is a service fact, not a deployment choice.
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

# Health API endpoint region per partition (global service).
_HEALTH_ENDPOINT_REGION = {
    "aws": "us-east-1",
    "aws-cn": "cn-northwest-1",
    "aws-us-gov": "us-gov-west-1",
}

# Health console deep link for one event
HEALTH_CONSOLE_URL = "https://health.aws.amazon.com/health/home#/account/event-log?eventID={arn}"


def _endpoint_region(partition: str) -> str:
    return _HEALTH_ENDPOINT_REGION.get(partition, "us-east-1")


def _chunks(seq: List, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def match_health_events(region: str, partition: str = "aws", client=None) -> Dict:
    """Return open/upcoming planned-lifecycle notices keyed by affected entity.

    Result:
      {"available": bool, "reason": str|None, "checked_at": iso,
       "events": int, "entities": {entity_value_or_arn: {...flag}}}
    where a flag is {event_arn, event_type, event_status, entity_status,
    start_time, end_time, console_url}. Never raises: Health being unreachable
    must not fail a scan.
    """
    checked_at = datetime.now(timezone.utc).isoformat()
    health = client or boto3.client("health", region_name=_endpoint_region(partition))
    events: Dict[str, Dict] = {}
    try:
        paginator = health.get_paginator("describe_events")
        pages = paginator.paginate(filter={
            # Region-scoped events plus global ones (Health tags some account
            # notifications with region 'global').
            "regions": [region, "global"],
            "eventStatusCodes": ["open", "upcoming"],
            "eventTypeCategories": ["scheduledChange", "accountNotification"],
        })
        for page in pages:
            for ev in page.get("events", []):
                events[ev["arn"]] = ev
        entities: Dict[str, Dict] = {}
        for arns in _chunks(list(events), 10):
            ent_pages = health.get_paginator("describe_affected_entities").paginate(
                filter={"eventArns": arns})
            for page in ent_pages:
                for ent in page.get("entities", []):
                    ev = events.get(ent["eventArn"], {})
                    flag = {
                        "event_arn": ent["eventArn"],
                        "event_type": ev.get("eventTypeCode", ""),
                        "event_status": ev.get("statusCode", ""),
                        "entity_status": ent.get("statusCode", ""),
                        "start_time": _iso(ev.get("startTime")),
                        "end_time": _iso(ev.get("endTime")),
                        "console_url": HEALTH_CONSOLE_URL.format(arn=ent["eventArn"]),
                    }
                    value = ent.get("entityValue", "")
                    # Keep the most actionable flag per entity (PENDING beats RESOLVED)
                    prev = entities.get(value)
                    if prev is None or (prev["entity_status"] != "PENDING" and flag["entity_status"] == "PENDING"):
                        entities[value] = flag
        return {"available": True, "reason": None, "checked_at": checked_at,
                "events": len(events), "entities": entities}
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "ClientError")
        reason = {
            "SubscriptionRequiredException": "AWS Health API requires a Business or Enterprise Support plan",
            "AccessDeniedException": "Scanner role lacks health:DescribeEvents / health:DescribeAffectedEntities",
        }.get(code, f"{code}: {e.response.get('Error', {}).get('Message', '')[:160]}")
        return {"available": False, "reason": reason, "checked_at": checked_at, "events": 0, "entities": {}}
    except Exception as e:  # network, endpoint resolution...
        return {"available": False, "reason": f"{type(e).__name__}: {str(e)[:160]}", "checked_at": checked_at,
                "events": 0, "entities": {}}


def _iso(value) -> str:
    if value is None:
        return ""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def apply_health_flags(items: List[Dict], match: Dict) -> int:
    """Stamp inventory rows in place with Health flags; return flagged resources.

    Each affected_resource_details entry gains 'health' when its ARN or
    identifier is an affected entity; each row gains 'health_flagged' (count).
    """
    entities = match.get("entities") or {}
    flagged_total = 0
    for item in items:
        ss = item.get("service_specific", {})
        flagged = 0
        for res in ss.get("affected_resource_details", []):
            flag = entities.get(res.get("arn") or "") or entities.get(res.get("name") or "")
            if flag:
                res["health"] = flag
                flagged += 1
            else:
                res.pop("health", None)
        ss["health_flagged"] = flagged
        flagged_total += flagged
    return flagged_total


def health_status_summary(match: Dict, flagged: int) -> Dict:
    """Compact, storable status of the last Health cross-check."""
    return {
        "available": bool(match.get("available")),
        "reason": match.get("reason"),
        "checked_at": match.get("checked_at"),
        "events": int(match.get("events", 0)),
        "flagged_resources": int(flagged),
    }
