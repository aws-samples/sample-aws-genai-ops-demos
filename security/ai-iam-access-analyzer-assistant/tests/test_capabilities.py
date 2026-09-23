"""Offline tests for the session-start capability probe (#171 phase C)."""

import json
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import capabilities


class _InvalidAccessException(Exception):
    """Stand-in for ``securityhub_client.exceptions.InvalidAccessException``."""


class _SecurityHubExceptions:
    InvalidAccessException = _InvalidAccessException


class FakeSecurityHubClient:
    """Records calls and returns predefined responses (or raises)."""

    def __init__(
        self,
        describe_hub_response=None,
        describe_hub_error=None,
        products_response=None,
        products_error=None,
        findings_response=None,
        findings_error=None,
    ):
        self._describe_hub_response = describe_hub_response
        self._describe_hub_error = describe_hub_error
        self._products_response = products_response or {"ProductSubscriptions": []}
        self._products_error = products_error
        self._findings_response = findings_response or {"Findings": []}
        self._findings_error = findings_error
        self.calls = []
        self.exceptions = _SecurityHubExceptions()

    def describe_hub(self):
        self.calls.append(("describe_hub", {}))
        if self._describe_hub_error is not None:
            raise self._describe_hub_error
        return self._describe_hub_response or {"SubscribedAt": "2024-01-01T00:00:00Z"}

    def list_enabled_products_for_import(self):
        self.calls.append(("list_enabled_products_for_import", {}))
        if self._products_error is not None:
            raise self._products_error
        return self._products_response

    def get_findings(self, **kwargs):
        self.calls.append(("get_findings", kwargs))
        if self._findings_error is not None:
            raise self._findings_error
        return self._findings_response


class FakeAccessAnalyzerClient:
    def __init__(self, response=None, error=None):
        self._response = response or {"analyzers": []}
        self._error = error
        self.calls = []

    def list_analyzers(self):
        self.calls.append("list_analyzers")
        if self._error is not None:
            raise self._error
        return self._response


class FakeCloudTrailClient:
    def __init__(self, error=None):
        self._error = error
        self.calls = []

    def lookup_events(self, **kwargs):
        self.calls.append(("lookup_events", kwargs))
        if self._error is not None:
            raise self._error
        return {"Events": []}


class _ProbeTestBase(unittest.TestCase):
    def setUp(self):
        self._orig_sh = capabilities.securityhub_client
        self._orig_aa = capabilities.accessanalyzer_client
        self._orig_ct = capabilities.cloudtrail_client

    def tearDown(self):
        capabilities.securityhub_client = self._orig_sh
        capabilities.accessanalyzer_client = self._orig_aa
        capabilities.cloudtrail_client = self._orig_ct

    def _install(self, sh=None, aa=None, ct=None):
        capabilities.securityhub_client = sh or FakeSecurityHubClient()
        capabilities.accessanalyzer_client = aa or FakeAccessAnalyzerClient()
        capabilities.cloudtrail_client = ct or FakeCloudTrailClient()


class HandlerShapeTest(_ProbeTestBase):
    def test_handler_returns_200_json_with_region_coverage_welcome(self):
        self._install(
            sh=FakeSecurityHubClient(
                products_response={
                    "ProductSubscriptions": [
                        "arn:aws:securityhub:us-east-1::product/aws/access-analyzer",
                    ]
                },
            ),
            aa=FakeAccessAnalyzerClient(
                response={
                    "analyzers": [
                        {"name": "external", "type": "ACCOUNT", "status": "ACTIVE"},
                        {"name": "unused", "type": "ACCOUNT_UNUSED_ACCESS", "status": "ACTIVE"},
                    ]
                }
            ),
        )

        result = capabilities.handler({}, None)

        self.assertEqual(200, result["statusCode"])
        body = json.loads(result["body"])
        self.assertIn("region", body)
        self.assertIn("coverage", body)
        self.assertIn("welcome_message", body)
        self.assertIsInstance(body["coverage"], list)
        self.assertGreater(len(body["coverage"]), 0)
        for entry in body["coverage"]:
            self.assertIn("source", entry)
            self.assertIn("state", entry)
            self.assertIn("detail", entry)
            self.assertIn(entry["state"], {"checked", "unavailable"})

    def test_response_has_cors_headers(self):
        self._install()
        result = capabilities.handler({}, None)
        self.assertIn("Access-Control-Allow-Origin", result["headers"])


class HappyPathTest(_ProbeTestBase):
    def test_all_sources_reachable_welcome_mentions_findings_and_ct(self):
        self._install(
            sh=FakeSecurityHubClient(
                products_response={
                    "ProductSubscriptions": [
                        "arn:aws:securityhub:us-east-1::product/aws/access-analyzer",
                    ]
                },
                findings_response={"Findings": [{"Id": "f1"}], "NextToken": "more"},
            ),
            aa=FakeAccessAnalyzerClient(
                response={
                    "analyzers": [
                        {"name": "external", "type": "ACCOUNT", "status": "ACTIVE"},
                        {"name": "unused", "type": "ACCOUNT_UNUSED_ACCESS", "status": "ACTIVE"},
                    ]
                }
            ),
        )
        body = json.loads(capabilities.handler({}, None)["body"])
        welcome = body["welcome_message"]
        self.assertIn("Security Hub", welcome)
        self.assertIn("CloudTrail is reachable", welcome)
        # No negative-coverage sentence for missing analyzers when both exist.
        self.assertNotIn("NO unused-access analyzer", welcome)
        self.assertNotIn("NO external-access analyzer", welcome)


class SecurityHubDisabledTest(_ProbeTestBase):
    def test_disabled_security_hub_surfaces_gap_in_welcome_and_coverage(self):
        sh = FakeSecurityHubClient(
            describe_hub_error=_InvalidAccessException("Hub not enabled"),
        )
        self._install(sh=sh)
        body = json.loads(capabilities.handler({}, None)["body"])
        # The unavailable entry says SH is not enabled in this region.
        sh_entries = [c for c in body["coverage"] if c["source"] == "securityhub"]
        self.assertEqual(1, len(sh_entries))
        self.assertEqual("unavailable", sh_entries[0]["state"])
        self.assertIn("not enabled", sh_entries[0]["detail"].lower())
        # The welcome message names the gap explicitly.
        self.assertIn("Security Hub is NOT enabled", body["welcome_message"])


class SecurityHubIntegrationOffTest(_ProbeTestBase):
    def test_integration_off_appears_in_coverage_and_welcome(self):
        sh = FakeSecurityHubClient(
            products_response={
                "ProductSubscriptions": [
                    "arn:aws:securityhub:us-east-1::product/aws/guardduty",
                ]
            },
        )
        self._install(sh=sh)
        body = json.loads(capabilities.handler({}, None)["body"])
        # An unavailable SH coverage entry names the integration.
        integ_entries = [
            c
            for c in body["coverage"]
            if c["source"] == "securityhub"
            and c["state"] == "unavailable"
            and "integration" in c["detail"].lower()
        ]
        self.assertEqual(1, len(integ_entries))
        self.assertIn("integration is switched off", body["welcome_message"])


class AnalyzerCoverageTest(_ProbeTestBase):
    def test_only_external_analyzer_present(self):
        aa = FakeAccessAnalyzerClient(
            response={
                "analyzers": [
                    {"name": "ext", "type": "ACCOUNT", "status": "ACTIVE"},
                ]
            }
        )
        self._install(aa=aa)
        body = json.loads(capabilities.handler({}, None)["body"])
        aa_entries = [c for c in body["coverage"] if c["source"] == "accessanalyzer"]
        # One checked (external) + one unavailable (unused missing).
        states = sorted(c["state"] for c in aa_entries)
        self.assertEqual(["checked", "unavailable"], states)
        self.assertIn("NO unused-access analyzer", body["welcome_message"])
        self.assertNotIn("NO external-access analyzer", body["welcome_message"])

    def test_only_unused_analyzer_present(self):
        aa = FakeAccessAnalyzerClient(
            response={
                "analyzers": [
                    {"name": "un", "type": "ACCOUNT_UNUSED_ACCESS", "status": "ACTIVE"},
                ]
            }
        )
        self._install(aa=aa)
        body = json.loads(capabilities.handler({}, None)["body"])
        self.assertIn("NO external-access analyzer", body["welcome_message"])
        self.assertNotIn("NO unused-access analyzer", body["welcome_message"])

    def test_no_analyzers(self):
        self._install(aa=FakeAccessAnalyzerClient(response={"analyzers": []}))
        body = json.loads(capabilities.handler({}, None)["body"])
        aa_entries = [c for c in body["coverage"] if c["source"] == "accessanalyzer"]
        # Two unavailable entries — one per missing analyzer kind.
        self.assertEqual(2, len(aa_entries))
        for e in aa_entries:
            self.assertEqual("unavailable", e["state"])
        self.assertIn("NO unused-access analyzer", body["welcome_message"])
        self.assertIn("NO external-access analyzer", body["welcome_message"])

    def test_only_active_analyzers_count(self):
        aa = FakeAccessAnalyzerClient(
            response={
                "analyzers": [
                    {"name": "ext-old", "type": "ACCOUNT", "status": "DISABLED"},
                    {"name": "unused-ok", "type": "ACCOUNT_UNUSED_ACCESS", "status": "ACTIVE"},
                ]
            }
        )
        self._install(aa=aa)
        body = json.loads(capabilities.handler({}, None)["body"])
        # DISABLED analyzers must not count as coverage.
        self.assertIn("NO external-access analyzer", body["welcome_message"])
        self.assertNotIn("NO unused-access analyzer", body["welcome_message"])


class CloudTrailUnreachableTest(_ProbeTestBase):
    def test_cloudtrail_permission_error_surfaces_gap(self):
        ct = FakeCloudTrailClient(error=RuntimeError("AccessDenied"))
        self._install(ct=ct)
        body = json.loads(capabilities.handler({}, None)["body"])
        ct_entries = [c for c in body["coverage"] if c["source"] == "cloudtrail"]
        self.assertEqual(1, len(ct_entries))
        self.assertEqual("unavailable", ct_entries[0]["state"])
        # Welcome names the unsafe policy generation consequence.
        self.assertIn("CloudTrail LookupEvents is NOT reachable", body["welcome_message"])
        self.assertIn("least-privilege", body["welcome_message"].lower())


class AccessAnalyzerErrorTest(_ProbeTestBase):
    def test_list_analyzers_failure_becomes_single_unavailable_entry(self):
        aa = FakeAccessAnalyzerClient(error=RuntimeError("boom"))
        self._install(aa=aa)
        body = json.loads(capabilities.handler({}, None)["body"])
        aa_entries = [c for c in body["coverage"] if c["source"] == "accessanalyzer"]
        # The probe short-circuits into ONE unavailable row when it can't list.
        self.assertEqual(1, len(aa_entries))
        self.assertEqual("unavailable", aa_entries[0]["state"])
        # Both "missing" welcome lines still surface because neither kind was seen.
        self.assertIn("NO unused-access analyzer", body["welcome_message"])
        self.assertIn("NO external-access analyzer", body["welcome_message"])


class WelcomeFallbackTest(_ProbeTestBase):
    def test_welcome_never_empty(self):
        self._install()
        body = json.loads(capabilities.handler({}, None)["body"])
        self.assertTrue(body["welcome_message"].strip())


if __name__ == "__main__":
    unittest.main()
