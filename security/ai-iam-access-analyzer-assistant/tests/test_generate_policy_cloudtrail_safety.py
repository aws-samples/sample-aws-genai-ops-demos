"""Regression tests for generate_policy's CloudTrail failure mode.

Pins the safety invariant from #171 phase A: when the CloudTrail query
raises, generate_policy MUST return an error and MUST NOT propose a
least-privilege policy. The pre-fix behavior swallowed the exception and
continued with an empty `used_actions` accumulator, producing a
"strip 100% of permissions" recommendation driven by absence of data
rather than observed non-use — the most dangerous silent-failure in the
demo.
"""

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
        page = {
            "Events": [
                {
                    "EventName": "GetObject",
                    "EventSource": "s3.amazonaws.com",
                    "Resources": [
                        {"ResourceName": "arn:aws:s3:::my-bucket/key"}
                    ],
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


if __name__ == "__main__":
    unittest.main()
