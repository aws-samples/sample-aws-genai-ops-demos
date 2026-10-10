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
        describe_org_config_response=None,
        describe_org_config_error=None,
    ):
        self._describe_hub_response = describe_hub_response
        self._describe_hub_error = describe_hub_error
        self._products_response = products_response or {"ProductSubscriptions": []}
        self._products_error = products_error
        self._findings_response = findings_response or {"Findings": []}
        self._findings_error = findings_error
        self._describe_org_config_response = describe_org_config_response
        self._describe_org_config_error = describe_org_config_error
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

    def describe_organization_configuration(self):
        self.calls.append(("describe_organization_configuration", {}))
        if self._describe_org_config_error is not None:
            raise self._describe_org_config_error
        return self._describe_org_config_response or {
            "AutoEnable": True,
            "AutoEnableStandards": "DEFAULT",
            "OrganizationConfiguration": {"ConfigurationType": "LOCAL"},
        }


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


class _AWSOrganizationsNotInUseException(Exception):
    """Stand-in for ``organizations_client.exceptions.AWSOrganizationsNotInUseException``."""


class _OrganizationsExceptions:
    AWSOrganizationsNotInUseException = _AWSOrganizationsNotInUseException


class FakeOrganizationsClient:
    def __init__(self, describe_org_response=None, describe_org_error=None):
        self._describe_org_response = describe_org_response
        self._describe_org_error = describe_org_error
        self.calls = []
        self.exceptions = _OrganizationsExceptions()

    def describe_organization(self):
        self.calls.append(("describe_organization", {}))
        if self._describe_org_error is not None:
            raise self._describe_org_error
        return self._describe_org_response or {
            "Organization": {"Id": "o-example12345"}
        }


class _ProbeTestBase(unittest.TestCase):
    def setUp(self):
        self._orig_sh = capabilities.securityhub_client
        self._orig_aa = capabilities.accessanalyzer_client
        self._orig_ct = capabilities.cloudtrail_client
        self._orig_org = capabilities.organizations_client

    def tearDown(self):
        capabilities.securityhub_client = self._orig_sh
        capabilities.accessanalyzer_client = self._orig_aa
        capabilities.cloudtrail_client = self._orig_ct
        capabilities.organizations_client = self._orig_org

    def _install(self, sh=None, aa=None, ct=None, org=None):
        capabilities.securityhub_client = sh or FakeSecurityHubClient()
        capabilities.accessanalyzer_client = aa or FakeAccessAnalyzerClient()
        capabilities.cloudtrail_client = ct or FakeCloudTrailClient()
        capabilities.organizations_client = org or FakeOrganizationsClient(
            describe_org_error=_AWSOrganizationsNotInUseException("not in an org")
        )


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


class OrgDetectionNotInOrgTest(_ProbeTestBase):
    def test_not_in_org_surfaces_single_account_coverage(self):
        # Default _install() already simulates AWSOrganizationsNotInUseException.
        self._install()
        body = json.loads(capabilities.handler({}, None)["body"])
        org_entries = [c for c in body["coverage"] if c["source"] == "organizations"]
        self.assertEqual(1, len(org_entries))
        self.assertEqual("unavailable", org_entries[0]["state"])
        self.assertIn("not part of an aws organization", org_entries[0]["detail"].lower())
        # No delegated-admin sentence should appear when not in an org at all.
        self.assertNotIn("delegated administrator", body["welcome_message"].lower())
        self.assertIsNone(body["org_id"])
        self.assertIsNone(body["is_delegated_admin"])


class OrgDetectionSecurityHubDisabledTest(_ProbeTestBase):
    def test_in_org_but_sh_disabled_reports_org_without_admin_claim(self):
        sh = FakeSecurityHubClient(
            describe_hub_error=_InvalidAccessException("Hub not enabled"),
        )
        org = FakeOrganizationsClient()
        self._install(sh=sh, org=org)
        body = json.loads(capabilities.handler({}, None)["body"])
        org_entries = [c for c in body["coverage"] if c["source"] == "organizations"]
        self.assertEqual(1, len(org_entries))
        self.assertEqual("checked", org_entries[0]["state"])
        self.assertIn("o-example12345", org_entries[0]["detail"])
        self.assertIn("security hub is not enabled", org_entries[0]["detail"].lower())
        # describe_organization_configuration must NOT be called when SH is off.
        self.assertNotIn(
            "describe_organization_configuration",
            [c[0] for c in sh.calls],
        )
        self.assertNotIn("delegated administrator", body["welcome_message"].lower())


class OrgDetectionDelegatedAdminTest(_ProbeTestBase):
    def test_delegated_admin_account_confirmed_via_successful_call(self):
        sh = FakeSecurityHubClient(
            products_response={
                "ProductSubscriptions": [
                    "arn:aws:securityhub:us-east-1::product/aws/access-analyzer",
                ]
            },
        )
        org = FakeOrganizationsClient()
        self._install(sh=sh, org=org)
        body = json.loads(capabilities.handler({}, None)["body"])
        org_entries = [c for c in body["coverage"] if c["source"] == "organizations"]
        self.assertEqual(1, len(org_entries))
        self.assertEqual("checked", org_entries[0]["state"])
        self.assertIn("delegated administrator", org_entries[0]["detail"].lower())
        self.assertIn(
            "delegated administrator for its aws organization",
            body["welcome_message"].lower(),
        )
        self.assertIn(
            "aggregated across every member account",
            body["welcome_message"].lower(),
        )
        self.assertIn(("describe_organization_configuration", {}), sh.calls)
        # Structured top-level fields, threaded by the frontend into
        # /conversation as org_context so the chat model doesn't have to
        # guess (see agent.py's _get_system_prompt(mode, org_context)).
        self.assertEqual("o-example12345", body["org_id"])
        self.assertIs(True, body["is_delegated_admin"])


class OrgDetectionNotDelegatedAdminTest(_ProbeTestBase):
    def test_member_account_reports_local_only_via_invalid_access(self):
        sh = FakeSecurityHubClient(
            products_response={
                "ProductSubscriptions": [
                    "arn:aws:securityhub:us-east-1::product/aws/access-analyzer",
                ]
            },
            describe_org_config_error=_InvalidAccessException(
                "Only the Security Hub administrator account can invoke this operation."
            ),
        )
        org = FakeOrganizationsClient()
        self._install(sh=sh, org=org)
        body = json.loads(capabilities.handler({}, None)["body"])
        org_entries = [c for c in body["coverage"] if c["source"] == "organizations"]
        self.assertEqual(1, len(org_entries))
        self.assertEqual("checked", org_entries[0]["state"])
        self.assertIn("handled by a different account", org_entries[0]["detail"])
        self.assertIn("local to this account only", body["welcome_message"].lower())
        self.assertNotIn("aggregated across every member account", body["welcome_message"].lower())
        self.assertEqual("o-example12345", body["org_id"])
        self.assertIs(False, body["is_delegated_admin"])


class OrgDetectionAdminCheckFailsTest(_ProbeTestBase):
    def test_unexpected_error_still_reports_org_membership(self):
        sh = FakeSecurityHubClient(
            products_response={
                "ProductSubscriptions": [
                    "arn:aws:securityhub:us-east-1::product/aws/access-analyzer",
                ]
            },
            describe_org_config_error=RuntimeError("throttled"),
        )
        org = FakeOrganizationsClient()
        self._install(sh=sh, org=org)
        body = json.loads(capabilities.handler({}, None)["body"])
        org_entries = [c for c in body["coverage"] if c["source"] == "organizations"]
        self.assertEqual(1, len(org_entries))
        self.assertEqual("checked", org_entries[0]["state"])
        self.assertIn("could not", org_entries[0]["detail"].lower())
        self.assertIn("o-example12345", org_entries[0]["detail"])


class OrgDetectionGenericFailureTest(_ProbeTestBase):
    def test_describe_organization_unexpected_error_marks_unavailable(self):
        org = FakeOrganizationsClient(describe_org_error=RuntimeError("boom"))
        self._install(org=org)
        body = json.loads(capabilities.handler({}, None)["body"])
        org_entries = [c for c in body["coverage"] if c["source"] == "organizations"]
        self.assertEqual(1, len(org_entries))
        self.assertEqual("unavailable", org_entries[0]["state"])
        self.assertIn("check failed", org_entries[0]["detail"].lower())


if __name__ == "__main__":
    unittest.main()
