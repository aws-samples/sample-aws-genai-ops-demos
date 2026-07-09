"""CloudTrail log analyzer - identifies actually-used permissions for an IAM identity."""

import json
from datetime import datetime, timedelta, timezone

import boto3


def analyze_cloudtrail(identity_name: str, identity_type: str, days: int, region: str) -> dict:
    """Analyze CloudTrail logs to find actually-used AWS actions for an identity.

    Args:
        identity_name: IAM role or user name
        identity_type: 'role' or 'user'
        days: Number of days to look back
        region: AWS region

    Returns:
        Dict with used_actions, used_services, event_count
    """
    client = boto3.client("cloudtrail", region_name=region)

    start_time = datetime.now(timezone.utc) - timedelta(days=days)
    end_time = datetime.now(timezone.utc)

    # Build lookup attribute based on identity type
    lookup_attribute = {
        "AttributeKey": "Username",
        "AttributeValue": identity_name,
    }

    used_actions = set()
    event_count = 0
    next_token = None

    print(f"  Querying CloudTrail for {identity_type} '{identity_name}' over last {days} days...")

    while True:
        kwargs = {
            "LookupAttributes": [lookup_attribute],
            "StartTime": start_time,
            "EndTime": end_time,
            "MaxResults": 50,
        }
        if next_token:
            kwargs["NextToken"] = next_token

        response = client.lookup_events(**kwargs)

        for event in response.get("Events", []):
            event_count += 1
            cloud_trail_event = json.loads(event.get("CloudTrailEvent", "{}"))
            event_source = cloud_trail_event.get("eventSource", "")
            event_name = cloud_trail_event.get("eventName", "")

            if event_source and event_name:
                # Convert eventSource (e.g. s3.amazonaws.com) to service prefix
                service = event_source.replace(".amazonaws.com", "")
                action = f"{service}:{event_name}"
                used_actions.add(action)

        next_token = response.get("NextToken")
        if not next_token:
            break

    used_services = sorted(set(a.split(":")[0] for a in used_actions))

    print(f"  Found {event_count} events, {len(used_actions)} unique actions across {len(used_services)} services")

    return {
        "identity": identity_name,
        "identity_type": identity_type,
        "period_days": days,
        "used_actions": sorted(used_actions),
        "used_services": used_services,
        "event_count": event_count,
    }
