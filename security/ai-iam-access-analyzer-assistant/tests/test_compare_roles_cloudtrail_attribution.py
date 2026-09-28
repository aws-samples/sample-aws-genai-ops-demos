"""Regression tests for compare_roles' CloudTrail attribution bug.

Same root cause and fix as tests/test_generate_policy_cloudtrail_safety.py's
GeneratePolicyRoleAttributionTest: CloudTrail's LookupEvents "Username"
attribute does NOT resolve to a role name for assumed-role activity -- it
resolves to the SESSION name (RoleSessionName, or a service-generated name
for AWS-service callers like Lambda). compare_roles' _get_usage_summary
filtered LookupAttributes on Username=role_name, so it silently returned
event_count=0 for every role ever exercised via AssumeRole or a Lambda/ECS
execution role -- almost all real-world usage -- while the SAME tool's
separate RoleLastUsed field (a different IAM-native tracker) correctly
showed recent activity. That mismatch (real Last Used timestamp next to
"0 CloudTrail events") is exactly what surfaced this bug during the demo
guided-tour re-test.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tools import compare_roles  # noqa: E402


class GetUsageSummaryAttributionTest(unittest.TestCase):
    def _paginator_yielding(self, pages):
        p = MagicMock()
        p.paginate.return_value = iter(pages)
        return p

    def _event(self, event_name, event_source, actor_arn):
        return {
            "EventName": event_name,
            "EventSource": event_source,
            "CloudTrailEvent": json.dumps({"userIdentity": {"arn": actor_arn}}),
        }

    def test_assumed_role_session_name_does_not_match_username_filter(self):
        """The exact failure mode surfaced in the demo re-test: an exerciser
        assumes demo-alphaapp-dev-role with a session name that is NOT the
        role name. The old Username=role_name filter matched zero events;
        matching userIdentity.arn instead must find it."""
        page = {
            "Events": [
                self._event(
                    "ListBuckets",
                    "s3.amazonaws.com",
                    "arn:aws:sts::123456789012:assumed-role/demo-alphaapp-dev-role/demo-exerciser",
                )
            ]
        }
        with patch.object(
            compare_roles.cloudtrail_client,
            "get_paginator",
            return_value=self._paginator_yielding([page]),
        ):
            result = compare_roles._get_usage_summary("demo-alphaapp-dev-role", 90)

        self.assertNotIn("error", result)
        self.assertEqual(result["event_count"], 1)
        self.assertTrue(result["is_active"])
        self.assertIn("s3", result["services_used"])

    def test_lambda_execution_role_activity_matches_by_role_arn(self):
        """Lambda-service-generated session names must also attribute
        correctly -- same rule, same boundary-safe match as generate_policy."""
        page = {
            "Events": [
                self._event(
                    "GetItem",
                    "dynamodb.amazonaws.com",
                    "arn:aws:sts::123456789012:assumed-role/demo-payment-svc-v2-role/aws-lambda-abc123",
                )
            ]
        }
        with patch.object(
            compare_roles.cloudtrail_client,
            "get_paginator",
            return_value=self._paginator_yielding([page]),
        ):
            result = compare_roles._get_usage_summary("demo-payment-svc-v2-role", 90)

        self.assertNotIn("error", result)
        self.assertEqual(result["event_count"], 1)
        self.assertIn("dynamodb", result["services_used"])

    def test_events_from_a_prefix_named_role_are_excluded(self):
        """Boundary safety: a role whose name is a PREFIX of the actual
        actor's role name must not be credited with that activity."""
        page = {
            "Events": [
                self._event(
                    "ListBuckets",
                    "s3.amazonaws.com",
                    "arn:aws:sts::123456789012:assumed-role/demo-alphaapp-dev-role-v2/some-session",
                )
            ]
        }
        with patch.object(
            compare_roles.cloudtrail_client,
            "get_paginator",
            return_value=self._paginator_yielding([page]),
        ):
            result = compare_roles._get_usage_summary("demo-alphaapp-dev-role", 90)

        self.assertNotIn("error", result)
        self.assertEqual(result["event_count"], 0)
        self.assertEqual(result["activity_level"], "inactive")

    def test_malformed_cloudtrail_event_json_is_skipped_not_fatal(self):
        page = {
            "Events": [
                {
                    "EventName": "ListBuckets",
                    "EventSource": "s3.amazonaws.com",
                    "CloudTrailEvent": "not valid json{{{",
                },
                {
                    "EventName": "ListBuckets",
                    "EventSource": "s3.amazonaws.com",
                    # No CloudTrailEvent key at all.
                },
            ]
        }
        with patch.object(
            compare_roles.cloudtrail_client,
            "get_paginator",
            return_value=self._paginator_yielding([page]),
        ):
            result = compare_roles._get_usage_summary("demo-alphaapp-dev-role", 90)

        self.assertNotIn("error", result)
        self.assertEqual(result["event_count"], 0)

    def test_no_matching_events_returns_inactive_zero_count(self):
        with patch.object(
            compare_roles.cloudtrail_client,
            "get_paginator",
            return_value=self._paginator_yielding([{"Events": []}]),
        ):
            result = compare_roles._get_usage_summary("demo-orphan-role", 90)

        self.assertEqual(result["event_count"], 0)
        self.assertFalse(result["is_active"])
        self.assertEqual(result["activity_level"], "inactive")

    def test_cloudtrail_exception_returns_error_with_zero_count(self):
        paginator = MagicMock()
        paginator.paginate.side_effect = RuntimeError("throttled")
        with patch.object(
            compare_roles.cloudtrail_client, "get_paginator", return_value=paginator
        ):
            result = compare_roles._get_usage_summary("demo-alphaapp-dev-role", 90)

        self.assertIn("error", result)
        self.assertEqual(result["event_count"], 0)


if __name__ == "__main__":
    unittest.main()
