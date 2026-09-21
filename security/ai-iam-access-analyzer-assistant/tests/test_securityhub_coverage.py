"""Regression tests for the Security Hub coverage contract from #171.

Every tool that reads Security Hub now returns a ``coverage`` entry with
one of three states — ``checked`` (call succeeded with data), ``empty``
(call succeeded, no data), ``unavailable`` (call failed). The tests
below pin two invariants for each tool:

  1. An empty Security Hub response reads as ``empty``, not ``unavailable``.
     A legitimately clean account should NOT masquerade as an outage.
  2. A Security Hub exception (InvalidAccessException / generic) reads as
     ``unavailable``, with a useful ``detail``. An outage / missing service
     / permission error must NOT masquerade as ``no findings``.

The second half is where the pre-fix behaviour was actually dangerous:
``generate_action_plan`` returned *"Your IAM posture looks clean!"* on any
empty response, including empty-because-service-unavailable.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tools import list_findings, get_finding_details, generate_action_plan  # noqa: E402


class _FakeExceptions:
    """Boto3 exception namespace surrogate for the fake client."""

    class InvalidAccessException(Exception):
        pass

    class InvalidInputException(Exception):
        pass


class _FakeSecurityHubClient:
    """Minimal shim: enough surface for the tools' happy + error paths."""

    exceptions = _FakeExceptions

    def __init__(self, response=None, raise_exc=None):
        self._response = response or {"Findings": []}
        self._raise = raise_exc
        self.calls = []
        # Mimic boto3's client.meta.region_name so _coverage() can report
        # the region without special-casing the fake.
        self.meta = MagicMock()
        self.meta.region_name = "us-east-1"

    def get_findings(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise is not None:
            raise self._raise
        return self._response


def _install(module, client):
    """Swap module-level securityhub_client with the fake; return restore fn."""
    original = module.securityhub_client
    module.securityhub_client = client

    def _restore():
        module.securityhub_client = original

    return _restore


def _finding(fid="f-1"):
    return {
        "Id": fid,
        "Title": "Unused role",
        "Description": "Role has not been used in 90 days.",
        "Severity": {"Label": "MEDIUM", "Normalized": 40},
        "Resources": [{"Id": f"arn:aws:iam::123456789012:role/{fid}", "Type": "AwsIamRole"}],
        "Workflow": {"Status": "NEW"},
        "RecordState": "ACTIVE",
        "ProductName": "IAM Access Analyzer",
        "ProductFields": {"type": "UNUSED_ACCESS"},
        "AwsAccountId": "123456789012",
    }


# ---------------------------------------------------------------- list_findings


class ListFindingsCoverageTest(unittest.TestCase):
    def test_checked_on_happy_path_with_findings(self):
        client = _FakeSecurityHubClient({"Findings": [_finding("f-1"), _finding("f-2")]})
        restore = _install(list_findings, client)
        try:
            result = list_findings.handler({"status": "ACTIVE", "limit": 10})
        finally:
            restore()

        self.assertIn("coverage", result)
        cov = result["coverage"][0]
        self.assertEqual(cov["source"], "securityhub")
        self.assertEqual(cov["state"], "checked")
        self.assertEqual(cov["count"], 2)

    def test_empty_when_call_succeeds_but_no_findings(self):
        """Empty is NOT unavailable — pins the honest 'clean' case."""
        client = _FakeSecurityHubClient({"Findings": []})
        restore = _install(list_findings, client)
        try:
            result = list_findings.handler({"status": "ACTIVE", "limit": 10})
        finally:
            restore()

        cov = result["coverage"][0]
        self.assertEqual(cov["state"], "empty")
        self.assertEqual(cov["count"], 0)

    def test_unavailable_when_security_hub_access_denied(self):
        client = _FakeSecurityHubClient(
            raise_exc=_FakeExceptions.InvalidAccessException("not enabled")
        )
        restore = _install(list_findings, client)
        try:
            result = list_findings.handler({"status": "ACTIVE"})
        finally:
            restore()

        self.assertIn("error", result)
        cov = result["coverage"][0]
        self.assertEqual(cov["state"], "unavailable")
        self.assertIn("Security Hub", cov["detail"])

    def test_unavailable_on_generic_exception(self):
        client = _FakeSecurityHubClient(raise_exc=RuntimeError("throttled"))
        restore = _install(list_findings, client)
        try:
            result = list_findings.handler({"status": "ACTIVE"})
        finally:
            restore()

        cov = result["coverage"][0]
        self.assertEqual(cov["state"], "unavailable")
        self.assertIn("throttled", cov["detail"])


# ---------------------------------------------------------- get_finding_details


class GetFindingDetailsCoverageTest(unittest.TestCase):
    def test_unavailable_no_longer_masquerades_as_not_found(self):
        """Pre-fix bug: _resolve_finding_by_resource_name swallowed the
        exception and returned None, so an unreachable Security Hub read
        as 'no matching finding'. Fix: exceptions propagate to the outer
        handler which returns unavailable coverage.
        """
        client = _FakeSecurityHubClient(
            raise_exc=_FakeExceptions.InvalidAccessException("not enabled")
        )
        restore = _install(get_finding_details, client)
        try:
            result = get_finding_details.handler({"role_name": "some-role"})
        finally:
            restore()

        self.assertIn("error", result)
        cov = result["coverage"][0]
        self.assertEqual(cov["state"], "unavailable")
        # Must not look like a lookup miss.
        self.assertNotIn("No active finding found", result["error"])

    def test_empty_when_lookup_succeeds_but_no_match(self):
        """Successful call, no match → empty (legitimate lookup miss)."""
        client = _FakeSecurityHubClient({"Findings": []})
        restore = _install(get_finding_details, client)
        try:
            result = get_finding_details.handler({"role_name": "unknown-role"})
        finally:
            restore()

        self.assertIn("error", result)
        cov = result["coverage"][0]
        self.assertEqual(cov["state"], "empty")
        self.assertIn("No active finding", result["error"])


# --------------------------------------------------------- generate_action_plan


class GenerateActionPlanCoverageTest(unittest.TestCase):
    def test_no_more_posture_looks_clean(self):
        """Ben's citation: 'Your IAM posture looks clean!' has to go. Empty
        findings should name the coverage rather than imply the environment
        is clean.
        """
        client = _FakeSecurityHubClient({"Findings": []})
        restore = _install(generate_action_plan, client)
        try:
            result = generate_action_plan.handler({"max_items": 50})
        finally:
            restore()

        self.assertEqual(result["summary"]["total_findings"], 0)
        message = result["summary"]["message"]
        self.assertNotIn("posture looks clean", message.lower())
        self.assertIn("Security Hub", message)

        cov = result["coverage"][0]
        self.assertEqual(cov["state"], "empty")
        self.assertEqual(cov["count"], 0)

    def test_unavailable_returns_error_not_empty_plan(self):
        """When Security Hub is unreachable, the tool must NOT return an
        empty action plan and imply the environment is clean.
        """
        client = _FakeSecurityHubClient(
            raise_exc=_FakeExceptions.InvalidAccessException("not enabled")
        )
        restore = _install(generate_action_plan, client)
        try:
            result = generate_action_plan.handler({"max_items": 50})
        finally:
            restore()

        self.assertIn("error", result)
        self.assertNotIn("action_plan", result)
        self.assertNotIn("summary", result)
        cov = result["coverage"][0]
        self.assertEqual(cov["state"], "unavailable")
        self.assertIn("Security Hub", result["error"])

    def test_generic_exception_also_returns_unavailable(self):
        client = _FakeSecurityHubClient(raise_exc=RuntimeError("throttled"))
        restore = _install(generate_action_plan, client)
        try:
            result = generate_action_plan.handler({"max_items": 50})
        finally:
            restore()

        self.assertIn("error", result)
        self.assertNotIn("action_plan", result)
        cov = result["coverage"][0]
        self.assertEqual(cov["state"], "unavailable")
        self.assertIn("throttled", cov["detail"])


if __name__ == "__main__":
    unittest.main()
