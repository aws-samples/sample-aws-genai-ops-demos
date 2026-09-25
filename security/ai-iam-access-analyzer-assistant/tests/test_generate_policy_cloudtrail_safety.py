"""Regression tests for generate_policy's CloudTrail failure mode.

Pins the safety invariant from #171 phase A: when the CloudTrail query
raises, generate_policy MUST return an error and MUST NOT propose a
least-privilege policy. The pre-fix behavior swallowed the exception and
continued with an empty `used_actions` accumulator, producing a
"strip 100% of permissions" recommendation driven by absence of data
rather than observed non-use — the most dangerous silent-failure in the
demo.
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

from tools import generate_policy  # noqa: E402


def _stub_role_info(role_name: str = "my-role") -> dict:
    """Shape returned by _get_role_info for a valid role."""
    return {
        "arn": f"arn:aws:iam::123456789012:role/{role_name}",
        "attached_policies": [
            {"name": "AmazonS3ReadOnlyAccess", "type": "managed"},
        ],
    }


class GeneratePolicyCloudTrailSafetyTest(unittest.TestCase):
    """When CloudTrail is unreachable, the tool must refuse to propose a policy."""

    def test_cloudtrail_access_denied_returns_error_not_empty_policy(self):
        """AccessDenied on cloudtrail:LookupEvents must not become "role unused"."""
        access_denied = Exception(
            "An error occurred (AccessDeniedException) when calling the "
            "LookupEvents operation: User is not authorized"
        )

        # Force the paginator into the exception path used by
        # _analyze_cloudtrail_usage.
        mock_paginator = MagicMock()
        mock_paginator.paginate.side_effect = access_denied

        with patch.object(
            generate_policy, "_get_role_info", return_value=_stub_role_info()
        ), patch.object(
            generate_policy, "_get_current_granted_actions", return_value={"s3:getobject"}
        ), patch.object(
            generate_policy.cloudtrail_client,
            "get_paginator",
            return_value=mock_paginator,
        ):
            result = generate_policy.handler({"role_name": "my-role"})

        # 1. Must be an error result.
        self.assertIn("error", result)
        self.assertIn("CloudTrail", result["error"])

        # 2. Must NOT propose a policy or reduction metrics — those are the
        # dangerous outputs the pre-fix version produced.
        self.assertNotIn("proposed_policy", result)
        self.assertNotIn("reduction_metrics", result)
        self.assertNotIn("formatted_policy", result)

        # 3. Coverage must flag the source as unavailable with a useful
        # detail so callers (and the user) can act.
        self.assertIn("coverage", result)
        ct_cov = next(
            c for c in result["coverage"] if c.get("source") == "cloudtrail"
        )
        self.assertEqual(ct_cov["state"], "unavailable")
        self.assertIn("AccessDenied", ct_cov["detail"])

    def test_cloudtrail_generic_exception_still_refuses_policy(self):
        """Any exception (not just AccessDenied) must refuse to propose."""
        mock_paginator = MagicMock()
        mock_paginator.paginate.side_effect = RuntimeError("boto3 throttling")

        with patch.object(
            generate_policy, "_get_role_info", return_value=_stub_role_info()
        ), patch.object(
            generate_policy, "_get_current_granted_actions", return_value=set()
        ), patch.object(
            generate_policy.cloudtrail_client,
            "get_paginator",
            return_value=mock_paginator,
        ):
            result = generate_policy.handler({"role_name": "my-role"})

        self.assertIn("error", result)
        self.assertNotIn("proposed_policy", result)
        ct_cov = next(
            c for c in result["coverage"] if c.get("source") == "cloudtrail"
        )
        self.assertEqual(ct_cov["state"], "unavailable")
        self.assertIn("boto3 throttling", ct_cov["detail"])


class GeneratePolicyCoverageOnHappyPathTest(unittest.TestCase):
    """The success paths still return coverage so callers can inspect it."""

    def _paginator_yielding(self, pages):
        p = MagicMock()
        p.paginate.return_value = iter(pages)
        return p

    def test_cloudtrail_with_events_returns_checked_coverage(self):
        # Real CloudTrail LookupEvents responses always carry the raw event
        # JSON in CloudTrailEvent -- that's where userIdentity.arn lives, and
        # matching against it (not the Username summary field) is what makes
        # role-based CloudTrail attribution work for assumed-role sessions.
        raw_event = json.dumps(
            {
                "userIdentity": {
                    "type": "AssumedRole",
                    "arn": "arn:aws:sts::123456789012:assumed-role/my-role/some-session",
                }
            }
        )
        page = {
            "Events": [
                {
                    "EventName": "GetObject",
                    "EventSource": "s3.amazonaws.com",
                    "Resources": [
                        {"ResourceName": "arn:aws:s3:::my-bucket/key"}
                    ],
                    "CloudTrailEvent": raw_event,
                }
            ]
        }
        with patch.object(
            generate_policy, "_get_role_info", return_value=_stub_role_info()
        ), patch.object(
            generate_policy,
            "_get_current_granted_actions",
            return_value={"s3:getobject"},
        ), patch.object(
            generate_policy.cloudtrail_client,
            "get_paginator",
            return_value=self._paginator_yielding([page]),
        ):
            result = generate_policy.handler({"role_name": "my-role"})

        self.assertNotIn("error", result)
        self.assertIn("proposed_policy", result)
        ct_cov = next(
            c for c in result["coverage"] if c.get("source") == "cloudtrail"
        )
        self.assertEqual(ct_cov["state"], "checked")
        self.assertGreaterEqual(ct_cov["count"], 1)

    def test_cloudtrail_with_zero_events_returns_empty_coverage(self):
        """Zero events on a successful call is `empty`, not `unavailable`.

        The current behavior (still proposes a policy + adds a warning that
        the role may be unused) is preserved for now — Ben's #171 phase B
        will tighten this further. We only guard the truly dangerous case in
        phase A.
        """
        with patch.object(
            generate_policy, "_get_role_info", return_value=_stub_role_info()
        ), patch.object(
            generate_policy,
            "_get_current_granted_actions",
            return_value={"s3:getobject"},
        ), patch.object(
            generate_policy.cloudtrail_client,
            "get_paginator",
            return_value=self._paginator_yielding([{"Events": []}]),
        ):
            result = generate_policy.handler({"role_name": "my-role"})

        self.assertNotIn("error", result)
        ct_cov = next(
            c for c in result["coverage"] if c.get("source") == "cloudtrail"
        )
        self.assertEqual(ct_cov["state"], "empty")
        self.assertEqual(ct_cov["count"], 0)


class NormalizeEventNameTest(unittest.TestCase):
    """Pins the fix for Finding #1 from Quick's guided-tour re-test: the
    generated policy contained the literal action `lambda:ListFunctions20150331`
    -- CloudTrail's real eventName for Lambda's ListFunctions API, which
    bakes in Lambda's stable API version (2015-03-31) as a trailing digit
    suffix. AWS's own Lambda troubleshooting docs confirm this is a known,
    documented quirk scoped to Lambda specifically -- not a general
    CloudTrail behavior, so the fix must not blindly strip trailing digits
    from every service's event names.
    """

    def test_lambda_event_name_strips_version_suffix(self):
        self.assertEqual(
            generate_policy._normalize_event_name("lambda", "ListFunctions20150331"),
            "ListFunctions",
        )
        self.assertEqual(
            generate_policy._normalize_event_name("lambda", "GetFunction20150331"),
            "GetFunction",
        )

    def test_lambda_event_name_without_suffix_is_unchanged(self):
        # Not every Lambda eventName carries the suffix.
        self.assertEqual(
            generate_policy._normalize_event_name("lambda", "InvokeFunction"),
            "InvokeFunction",
        )

    def test_non_lambda_service_is_never_touched(self):
        # The rule is deliberately scoped to lambda only -- an action that
        # happens to end in 8 digits for some other service must not be
        # mangled by an overzealous generic strip.
        self.assertEqual(
            generate_policy._normalize_event_name("s3", "ListBuckets"),
            "ListBuckets",
        )
        self.assertEqual(
            generate_policy._normalize_event_name("dynamodb", "SomeAction12345678"),
            "SomeAction12345678",
        )

    def test_end_to_end_generated_policy_uses_clean_action_name(self):
        """The full handler path: a Lambda ListFunctions call observed via
        CloudTrail must produce `lambda:ListFunctions` in the proposed
        policy, never the raw eventName with its version suffix."""
        raw_event = json.dumps(
            {
                "userIdentity": {
                    "arn": "arn:aws:sts::123456789012:assumed-role/demo-alphaapp-dev-role/demo-exerciser"
                }
            }
        )
        page = {
            "Events": [
                {
                    "EventName": "ListFunctions20150331",
                    "EventSource": "lambda.amazonaws.com",
                    "CloudTrailEvent": raw_event,
                }
            ]
        }
        paginator = MagicMock()
        paginator.paginate.return_value = iter([page])

        with patch.object(
            generate_policy,
            "_get_role_info",
            return_value=_stub_role_info("demo-alphaapp-dev-role"),
        ), patch.object(
            generate_policy,
            "_get_current_granted_actions",
            return_value={"lambda:listfunctions"},
        ), patch.object(
            generate_policy.cloudtrail_client, "get_paginator", return_value=paginator
        ):
            result = generate_policy.handler({"role_name": "demo-alphaapp-dev-role"})

        self.assertNotIn("error", result)
        proposed_actions = [
            a
            for stmt in result["proposed_policy"]["Statement"]
            for a in stmt["Action"]
        ]
        self.assertIn("lambda:ListFunctions", proposed_actions)
        self.assertNotIn("lambda:ListFunctions20150331", proposed_actions)


class GeneratePolicyRoleAttributionTest(unittest.TestCase):
    """Pins the fix for the demo-fixture bug: CloudTrail's Username
    LookupAttribute resolves to the SESSION name for assumed-role activity
    (the RoleSessionName, or a service-generated name for AWS-service
    callers like Lambda) — never the role name. Filtering LookupEvents on
    Username=role_name therefore silently matched zero events for every
    role that was ever exercised via AssumeRole or a Lambda execution role,
    regardless of real activity. The fix queries unfiltered and matches
    client-side against userIdentity.arn from the raw CloudTrailEvent JSON,
    which correctly contains the role name in both forms CloudTrail uses.
    """

    def _paginator_yielding(self, pages):
        p = MagicMock()
        p.paginate.return_value = iter(pages)
        return p

    def _event(self, event_name, event_source, actor_arn, resource_arn=None):
        raw = json.dumps({"userIdentity": {"arn": actor_arn}})
        evt = {
            "EventName": event_name,
            "EventSource": event_source,
            "CloudTrailEvent": raw,
        }
        if resource_arn:
            evt["Resources"] = [{"ResourceName": resource_arn}]
        return evt

    def test_assumed_role_session_name_does_not_match_username_filter(self):
        """The exact failure mode Quick's demo-fixture test surfaced: an
        exerciser assumes `demo-alphaapp-dev-role` with RoleSessionName=
        'demo-exerciser'. CloudTrail's Username for that event is the
        session name, not the role name — but userIdentity.arn still names
        the role via the assumed-role ARN pattern, and the fix must catch
        it there."""
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
            generate_policy,
            "_get_role_info",
            return_value=_stub_role_info("demo-alphaapp-dev-role"),
        ), patch.object(
            generate_policy,
            "_get_current_granted_actions",
            return_value={"s3:*"},
        ), patch.object(
            generate_policy.cloudtrail_client,
            "get_paginator",
            return_value=self._paginator_yielding([page]),
        ):
            result = generate_policy.handler({"role_name": "demo-alphaapp-dev-role"})

        self.assertNotIn("error", result)
        self.assertEqual(result["events_analyzed"], 1)
        ct_cov = next(c for c in result["coverage"] if c.get("source") == "cloudtrail")
        self.assertEqual(ct_cov["state"], "checked")
        self.assertEqual(result["reduction_metrics"]["proposed_actions"], 1)

    def test_lambda_execution_role_activity_matches_by_role_arn(self):
        """A Lambda's own execution-role activity: CloudTrail records
        userIdentity.arn with the assumed-role/<role>/<lambda-session>
        pattern (the session name is Lambda-service-generated, not the
        role name) -- same attribution rule applies."""
        page = {
            "Events": [
                self._event(
                    "GetItem",
                    "dynamodb.amazonaws.com",
                    "arn:aws:sts::123456789012:assumed-role/demo-payment-svc-v2-role/aws-lambda-abc123",
                    resource_arn="arn:aws:dynamodb:us-east-1:123456789012:table/payments-prod",
                )
            ]
        }
        with patch.object(
            generate_policy,
            "_get_role_info",
            return_value=_stub_role_info("demo-payment-svc-v2-role"),
        ), patch.object(
            generate_policy,
            "_get_current_granted_actions",
            return_value={"dynamodb:getitem", "dynamodb:putitem"},
        ), patch.object(
            generate_policy.cloudtrail_client,
            "get_paginator",
            return_value=self._paginator_yielding([page]),
        ):
            result = generate_policy.handler({"role_name": "demo-payment-svc-v2-role"})

        self.assertNotIn("error", result)
        self.assertEqual(result["events_analyzed"], 1)
        self.assertIn("dynamodb", result["used_permissions"]["by_service"])

    def test_events_from_a_prefix_named_role_are_excluded(self):
        """A same-window event actuated by a DIFFERENT role whose name
        happens to be a PREFIX of this role's name (e.g. this role is
        "demo-alphaapp-dev-role" and the event's actor is
        "demo-alphaapp-dev-role-v2") must not count toward this role's
        usage. The match is boundary-safe: role/<name>/ or an exact
        end-of-string role/<name>, never a bare substring."""
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
            generate_policy,
            "_get_role_info",
            return_value=_stub_role_info("demo-alphaapp-dev-role"),
        ), patch.object(
            generate_policy,
            "_get_current_granted_actions",
            return_value={"s3:*"},
        ), patch.object(
            generate_policy.cloudtrail_client,
            "get_paginator",
            return_value=self._paginator_yielding([page]),
        ):
            result = generate_policy.handler({"role_name": "demo-alphaapp-dev-role"})

        self.assertNotIn("error", result)
        self.assertEqual(result["events_analyzed"], 0)

    def test_malformed_cloudtrail_event_json_is_skipped_not_fatal(self):
        """A page whose CloudTrailEvent field isn't valid JSON (or is
        missing) must not crash the analysis -- just contributes no
        attributed event."""
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
            generate_policy,
            "_get_role_info",
            return_value=_stub_role_info("demo-alphaapp-dev-role"),
        ), patch.object(
            generate_policy,
            "_get_current_granted_actions",
            return_value={"s3:*"},
        ), patch.object(
            generate_policy.cloudtrail_client,
            "get_paginator",
            return_value=self._paginator_yielding([page]),
        ):
            result = generate_policy.handler({"role_name": "demo-alphaapp-dev-role"})

        self.assertNotIn("error", result)
        self.assertEqual(result["events_analyzed"], 0)


if __name__ == "__main__":
    unittest.main()
