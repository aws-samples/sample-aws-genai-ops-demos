"""Offline tests for the cross-account IAM/CloudTrail read tool (#173 Level 2).

Covers the central guardrail (account_id is required, no implicit
all-accounts loop), the same-account vs. cross-account branch
(session_for_account returning None for the hub's own account id),
AccessDenied handling when a target account has no spoke role deployed,
and the coverage contract on both the IAM and CloudTrail sub-checks.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from botocore.exceptions import ClientError

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tools import cross_account_iam_context as tool  # noqa: E402

_HUB_ACCOUNT = "661487690263"
_SPOKE_ACCOUNT = "280072637828"


def _client_error(code: str, operation: str = "AssumeRole") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class FakeStsClient:
    """Stands in for boto3's sts client — only the two methods this tool
    calls: get_caller_identity (cached) and assume_role."""

    def __init__(self, account_id=_HUB_ACCOUNT, assume_role_error=None, partition="aws"):
        self._account_id = account_id
        self._assume_role_error = assume_role_error
        self.assume_role_calls = []

        class _Meta:
            def __init__(self, partition):
                self.partition = partition

        self.meta = _Meta(partition)

    def get_caller_identity(self):
        return {"Account": self._account_id}

    def assume_role(self, **kwargs):
        self.assume_role_calls.append(kwargs)
        if self._assume_role_error is not None:
            raise self._assume_role_error
        return {
            "Credentials": {
                "AccessKeyId": "FAKEKEY",
                "SecretAccessKey": "FAKESECRET",
                "SessionToken": "FAKETOKEN",
            }
        }


class FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kwargs):
        return iter(self._pages)


class FakeIamClient:
    def __init__(self, roles=None, attached_policies=None, list_roles_error=None):
        self._roles = roles or []
        self._attached_policies = attached_policies or {}
        self._list_roles_error = list_roles_error

    def get_paginator(self, name):
        assert name == "list_roles"
        if self._list_roles_error is not None:
            raise self._list_roles_error
        return FakePaginator([{"Roles": self._roles}])

    def list_attached_role_policies(self, RoleName):
        return {"AttachedPolicies": self._attached_policies.get(RoleName, [])}


class FakeCloudTrailClient:
    def __init__(self, error=None):
        self._error = error

    def lookup_events(self, **kwargs):
        if self._error is not None:
            raise self._error
        return {"Events": []}


class FakeSession:
    """Stands in for the boto3.session.Session returned by
    session_for_account() when it DOES assume a role — lets tests inject
    fake IAM/CloudTrail clients for the "cross-account" branch without a
    real STS call."""

    def __init__(self, iam_client, cloudtrail_client):
        self._clients = {"iam": iam_client, "cloudtrail": cloudtrail_client}

    def client(self, service_name, **kwargs):
        return self._clients[service_name]


class _ToolTestBase(unittest.TestCase):
    def setUp(self):
        self._orig_sts = tool.sts_client
        tool._HUB_ACCOUNT_ID = None  # reset the module-level cache each test

    def tearDown(self):
        tool.sts_client = self._orig_sts
        tool._HUB_ACCOUNT_ID = None


class MissingAccountIdTest(_ToolTestBase):
    def test_missing_account_id_returns_error_naming_the_requirement(self):
        tool.sts_client = FakeStsClient()
        result = tool.handler({})
        self.assertIn("error", result)
        self.assertIn("account_id is required", result["error"])
        # Must not silently default to the hub account or any other guess.
        self.assertNotIn("roles", result)

    def test_malformed_account_id_is_rejected(self):
        tool.sts_client = FakeStsClient()
        for bad in ("not-an-account", "12345", "1234567890123", ""):
            if bad == "":
                continue  # falsy -> covered by the missing-id test above
            result = tool.handler({"account_id": bad})
            self.assertIn("error", result, msg=f"expected error for {bad!r}")
            self.assertIn("12-digit", result["error"])


class SameAccountTest(_ToolTestBase):
    """account_id == hub's own account: session_for_account() must return
    None, and the tool must fall back to boto3.client(...) directly
    rather than attempting any assume_role call."""

    def test_hub_account_id_does_not_assume_any_role(self):
        sts = FakeStsClient(account_id=_HUB_ACCOUNT)
        tool.sts_client = sts

        fake_iam = FakeIamClient(roles=[{"RoleName": "AppRole", "Arn": "arn:aws:iam::661487690263:role/AppRole", "CreateDate": "2024-01-01"}])
        fake_ct = FakeCloudTrailClient()

        with patch("boto3.client") as mock_boto_client:
            mock_boto_client.side_effect = lambda service, **kw: {"iam": fake_iam, "cloudtrail": fake_ct}[service]
            result = tool.handler({"account_id": _HUB_ACCOUNT})

        self.assertEqual(0, len(sts.assume_role_calls))
        self.assertTrue(result["is_hub_account"])
        self.assertEqual(1, result["returned_count"])
        self.assertTrue(result["cloudtrail_reachable"])


class CrossAccountTest(_ToolTestBase):
    """account_id != hub's own account: session_for_account() must call
    assume_role with the fixed spoke-role ARN derived purely from the
    target account id, and the tool must use the resulting session's
    clients, not the hub's own boto3.client(...)."""

    def test_assumes_the_fixed_spoke_role_arn(self):
        sts = FakeStsClient(account_id=_HUB_ACCOUNT, partition="aws")
        tool.sts_client = sts

        fake_iam = FakeIamClient(
            roles=[{"RoleName": "AdminRole", "Arn": "arn:aws:iam::280072637828:role/AdminRole", "CreateDate": "2024-01-01"}],
            attached_policies={"AdminRole": [{"PolicyName": "AdministratorAccess"}]},
        )
        fake_ct = FakeCloudTrailClient()
        fake_session = FakeSession(fake_iam, fake_ct)

        with patch("boto3.session.Session", return_value=fake_session):
            result = tool.handler({"account_id": _SPOKE_ACCOUNT})

        self.assertEqual(1, len(sts.assume_role_calls))
        call = sts.assume_role_calls[0]
        self.assertEqual(
            f"arn:aws:iam::{_SPOKE_ACCOUNT}:role/{tool.SPOKE_ROLE_NAME}", call["RoleArn"]
        )
        self.assertEqual(3600, call["DurationSeconds"])

        self.assertFalse(result["is_hub_account"])
        self.assertEqual(1, result["returned_count"])
        self.assertTrue(result["roles"][0]["high_privilege"])
        self.assertTrue(result["cloudtrail_reachable"])

    def test_access_denied_on_assume_role_names_missing_spoke_stack(self):
        sts = FakeStsClient(
            account_id=_HUB_ACCOUNT,
            assume_role_error=_client_error("AccessDenied"),
        )
        tool.sts_client = sts

        result = tool.handler({"account_id": _SPOKE_ACCOUNT})

        self.assertIn("error", result)
        self.assertIn(tool.SPOKE_ROLE_NAME, result["error"])
        self.assertIn("not have the spoke role deployed", result["error"])
        coverage = result["coverage"]
        self.assertEqual(1, len(coverage))
        self.assertEqual("unavailable", coverage[0]["state"])

    def test_other_assume_role_failure_is_reported_generically(self):
        sts = FakeStsClient(
            account_id=_HUB_ACCOUNT,
            assume_role_error=_client_error("Throttling"),
        )
        tool.sts_client = sts

        result = tool.handler({"account_id": _SPOKE_ACCOUNT})

        self.assertIn("error", result)
        self.assertIn("Throttling", result["error"])


class CoverageContractTest(_ToolTestBase):
    def test_empty_roles_reports_empty_not_unavailable(self):
        sts = FakeStsClient(account_id=_HUB_ACCOUNT)
        tool.sts_client = sts
        fake_iam = FakeIamClient(roles=[])
        fake_ct = FakeCloudTrailClient()

        with patch("boto3.client") as mock_boto_client:
            mock_boto_client.side_effect = lambda service, **kw: {"iam": fake_iam, "cloudtrail": fake_ct}[service]
            result = tool.handler({"account_id": _HUB_ACCOUNT})

        iam_entries = [c for c in result["coverage"] if "roles" in c["detail"]]
        self.assertEqual(1, len(iam_entries))
        self.assertEqual("empty", iam_entries[0]["state"])

    def test_iam_failure_is_unavailable_not_silently_empty(self):
        sts = FakeStsClient(account_id=_HUB_ACCOUNT)
        tool.sts_client = sts
        fake_iam = FakeIamClient(list_roles_error=_client_error("AccessDenied", "ListRoles"))
        fake_ct = FakeCloudTrailClient()

        with patch("boto3.client") as mock_boto_client:
            mock_boto_client.side_effect = lambda service, **kw: {"iam": fake_iam, "cloudtrail": fake_ct}[service]
            result = tool.handler({"account_id": _HUB_ACCOUNT})

        self.assertEqual(0, result["returned_count"])
        iam_entries = [c for c in result["coverage"] if "IAM read failed" in c["detail"]]
        self.assertEqual(1, len(iam_entries))
        self.assertEqual("unavailable", iam_entries[0]["state"])

    def test_cloudtrail_failure_is_unavailable_and_independent_of_iam_result(self):
        sts = FakeStsClient(account_id=_HUB_ACCOUNT)
        tool.sts_client = sts
        fake_iam = FakeIamClient(roles=[{"RoleName": "R", "Arn": "arn:aws:iam::661487690263:role/R", "CreateDate": "2024-01-01"}])
        fake_ct = FakeCloudTrailClient(error=_client_error("AccessDenied", "LookupEvents"))

        with patch("boto3.client") as mock_boto_client:
            mock_boto_client.side_effect = lambda service, **kw: {"iam": fake_iam, "cloudtrail": fake_ct}[service]
            result = tool.handler({"account_id": _HUB_ACCOUNT})

        # IAM succeeded independently of CloudTrail failing.
        self.assertEqual(1, result["returned_count"])
        self.assertFalse(result["cloudtrail_reachable"])
        ct_entries = [c for c in result["coverage"] if "CloudTrail" in c["detail"]]
        self.assertEqual(1, len(ct_entries))
        self.assertEqual("unavailable", ct_entries[0]["state"])


class LimitHandlingTest(_ToolTestBase):
    def test_has_more_true_when_more_roles_exist_than_limit(self):
        sts = FakeStsClient(account_id=_HUB_ACCOUNT)
        tool.sts_client = sts
        roles = [
            {"RoleName": f"Role{i}", "Arn": f"arn:aws:iam::661487690263:role/Role{i}", "CreateDate": "2024-01-01"}
            for i in range(5)
        ]
        fake_iam = FakeIamClient(roles=roles)
        fake_ct = FakeCloudTrailClient()

        with patch("boto3.client") as mock_boto_client:
            mock_boto_client.side_effect = lambda service, **kw: {"iam": fake_iam, "cloudtrail": fake_ct}[service]
            result = tool.handler({"account_id": _HUB_ACCOUNT, "limit": 3})

        self.assertEqual(3, result["returned_count"])
        self.assertTrue(result["has_more"])

    def test_limit_is_clamped_to_100_max(self):
        sts = FakeStsClient(account_id=_HUB_ACCOUNT)
        tool.sts_client = sts
        fake_iam = FakeIamClient(roles=[])
        fake_ct = FakeCloudTrailClient()

        with patch("boto3.client") as mock_boto_client:
            mock_boto_client.side_effect = lambda service, **kw: {"iam": fake_iam, "cloudtrail": fake_ct}[service]
            # Must not raise even with an absurd requested limit.
            result = tool.handler({"account_id": _HUB_ACCOUNT, "limit": 99999})

        self.assertEqual(0, result["returned_count"])


if __name__ == "__main__":
    unittest.main()
