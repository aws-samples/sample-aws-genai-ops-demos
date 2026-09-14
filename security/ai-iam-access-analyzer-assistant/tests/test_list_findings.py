"""Offline regression tests for the standard findings query handler."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tools import list_findings


class FakeSecurityHubClient:
    """Captures Security Hub calls and returns predefined responses."""

    def __init__(self, responses):
        self._responses = iter(responses)
        self.calls = []

    def get_findings(self, **kwargs):
        self.calls.append(kwargs)
        return next(self._responses)


class ListFindingsTest(unittest.TestCase):
    def setUp(self):
        self._original_client = list_findings.securityhub_client

    def tearDown(self):
        list_findings.securityhub_client = self._original_client

    def test_returns_unknown_total_for_paginated_results_without_count_scan(self):
        client = FakeSecurityHubClient([
            {
                "Findings": [_finding("first")],
                "NextToken": "second-page-token",
            }
        ])
        list_findings.securityhub_client = client

        with patch.dict(os.environ, {"FULL_TOTAL_SCAN": "false"}):
            result = list_findings.handler({"status": "ACTIVE", "limit": 1})

        self.assertEqual(1, len(client.calls))
        self.assertEqual(1, result["returned_count"])
        self.assertEqual(-1, result["total_matching"])
        self.assertEqual(-1, result["total_count"])
        self.assertTrue(result["has_more"])
        self.assertEqual("second-page-token", result["next_token"])

    def test_returns_exact_total_for_terminal_page_at_requested_limit(self):
        client = FakeSecurityHubClient([{"Findings": [_finding("only")] }])
        list_findings.securityhub_client = client

        with patch.dict(os.environ, {"FULL_TOTAL_SCAN": "false"}):
            result = list_findings.handler({"status": "ACTIVE", "limit": 1})

        self.assertEqual(1, len(client.calls))
        self.assertEqual(1, result["returned_count"])
        self.assertEqual(1, result["total_matching"])
        self.assertEqual(1, result["total_count"])
        self.assertFalse(result["has_more"])
        self.assertNotIn("next_token", result)

    def test_preserves_exact_count_when_full_total_scan_is_enabled(self):
        client = FakeSecurityHubClient([
            {
                "Findings": [_finding("first")],
                "NextToken": "second-page-token",
            },
            {
                "Findings": [_finding("first"), _finding("second")],
                "NextToken": "count-page-token",
            },
            {"Findings": [_finding("third")]},
        ])
        list_findings.securityhub_client = client

        with patch.dict(os.environ, {"FULL_TOTAL_SCAN": "true"}):
            result = list_findings.handler({"status": "ACTIVE", "limit": 1})

        self.assertEqual(3, len(client.calls))
        self.assertEqual(1, result["returned_count"])
        self.assertEqual(3, result["total_matching"])
        self.assertEqual(3, result["total_count"])
        self.assertTrue(result["has_more"])
        self.assertEqual("second-page-token", result["next_token"])

    def test_returns_unknown_total_for_terminal_continuation_page(self):
        client = FakeSecurityHubClient([{"Findings": [_finding("final")]}])
        list_findings.securityhub_client = client

        with patch.dict(os.environ, {"FULL_TOTAL_SCAN": "false"}):
            result = list_findings.handler(
                {"status": "ACTIVE", "limit": 1, "next_token": "prior-page-token"}
            )

        self.assertEqual(1, len(client.calls))
        self.assertEqual(-1, result["total_matching"])
        self.assertEqual(-1, result["total_count"])
        self.assertFalse(result["has_more"])

    def test_defaults_to_exact_total_scans_when_flag_is_unset(self):
        client = FakeSecurityHubClient([
            {
                "Findings": [_finding("first")],
                "NextToken": "second-page-token",
            },
            {
                "Findings": [_finding("first"), _finding("second")],
                "NextToken": "count-page-token",
            },
            {"Findings": [_finding("third")]},
        ])
        list_findings.securityhub_client = client

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FULL_TOTAL_SCAN", None)
            result = list_findings.handler({"status": "ACTIVE", "limit": 1})

        self.assertEqual(3, len(client.calls))
        self.assertEqual(3, result["total_matching"])
        self.assertEqual(3, result["total_count"])


def _finding(identifier):
    return {
        "Id": f"finding-{identifier}",
        "Title": f"Finding {identifier}",
        "Description": "A test finding",
        "Severity": {"Label": "HIGH", "Normalized": 70},
        "Resources": [{"Type": "AwsIamRole", "Id": "role/test", "Region": "us-east-1"}],
        "Workflow": {"Status": "NEW"},
        "ProductFields": {"type": "POLICY"},
        "CreatedAt": "2026-01-01T00:00:00Z",
        "UpdatedAt": "2026-01-01T00:00:00Z",
        "AwsAccountId": "123456789012",
    }


if __name__ == "__main__":
    unittest.main()
