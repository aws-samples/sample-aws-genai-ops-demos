"""
Unit tests for the AWS Health cross-check (health_match.py, issue #141).

Offline: the Health client is replaced by a fake that serves canned pages.
"""
import os
import sys
from datetime import datetime, timezone

from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from health_match import match_health_events, apply_health_flags, health_status_summary  # noqa: E402

EVENT_ARN = "arn:aws:health:eu-central-1::event/LAMBDA/AWS_LAMBDA_PLANNED_LIFECYCLE_EVENT/abc"
FN_ARN = "arn:aws:lambda:eu-central-1:123456789012:function:old-fn"


class _Paginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **_):
        return iter(self._pages)


class FakeHealth:
    def __init__(self, events, entities):
        self._events, self._entities = events, entities

    def get_paginator(self, name):
        if name == "describe_events":
            return _Paginator([{"events": self._events}])
        if name == "describe_affected_entities":
            return _Paginator([{"entities": self._entities}])
        raise AssertionError(name)


class FailingHealth:
    def __init__(self, code):
        self._code = code

    def get_paginator(self, name):
        raise ClientError({"Error": {"Code": self._code, "Message": "nope"}}, name)


def _event():
    return {"arn": EVENT_ARN, "eventTypeCode": "AWS_LAMBDA_PLANNED_LIFECYCLE_EVENT", "statusCode": "open",
            "startTime": datetime(2026, 4, 30, tzinfo=timezone.utc), "region": "eu-central-1"}


def test_match_keys_entities_by_value_and_prefers_pending():
    client = FakeHealth([_event()], [
        {"eventArn": EVENT_ARN, "entityValue": FN_ARN, "statusCode": "RESOLVED"},
        {"eventArn": EVENT_ARN, "entityValue": FN_ARN, "statusCode": "PENDING"},
        {"eventArn": EVENT_ARN, "entityValue": "other-id", "statusCode": "PENDING"},
    ])
    result = match_health_events("eu-central-1", client=client)
    assert result["available"] is True and result["events"] == 1
    flag = result["entities"][FN_ARN]
    assert flag["entity_status"] == "PENDING"
    assert flag["event_type"] == "AWS_LAMBDA_PLANNED_LIFECYCLE_EVENT"
    assert flag["start_time"].startswith("2026-04-30")
    assert EVENT_ARN in flag["console_url"]


def test_apply_flags_by_arn_or_name_and_counts():
    match = {"entities": {FN_ARN: {"entity_status": "PENDING"}, "db-1": {"entity_status": "PENDING"}}}
    items = [{"service_specific": {"affected_resource_details": [
        {"name": "old-fn", "arn": FN_ARN},
        {"name": "db-1", "arn": ""},
        {"name": "fine", "arn": "arn:aws:lambda:eu-central-1:123456789012:function:fine", "health": {"stale": True}},
    ]}}]
    assert apply_health_flags(items, match) == 2
    details = items[0]["service_specific"]["affected_resource_details"]
    assert details[0]["health"]["entity_status"] == "PENDING"
    assert details[1]["health"]["entity_status"] == "PENDING"
    assert "health" not in details[2]  # stale flag from a previous scan is cleared
    assert items[0]["service_specific"]["health_flagged"] == 2


def test_subscription_required_is_reported_not_raised():
    result = match_health_events("eu-central-1", client=FailingHealth("SubscriptionRequiredException"))
    assert result["available"] is False
    assert "Business or Enterprise" in result["reason"]
    assert result["entities"] == {}
    summary = health_status_summary(result, 0)
    assert summary == {"available": False, "reason": result["reason"], "checked_at": result["checked_at"],
                       "events": 0, "flagged_resources": 0}


def test_access_denied_names_the_missing_permissions():
    result = match_health_events("eu-central-1", client=FailingHealth("AccessDeniedException"))
    assert result["available"] is False and "health:DescribeEvents" in result["reason"]
