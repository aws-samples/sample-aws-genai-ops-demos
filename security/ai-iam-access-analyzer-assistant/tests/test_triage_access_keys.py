"""Offline unit tests for the triage_access_keys tool (#175).

Pins the risk-flag taxonomy, prioritization ladder, remediation mapping,
and coverage-contract behavior defined in
``src/tools/references/access_key_analysis_criteria.md``. The tests use a
fake IAM client that records every call and returns pre-canned responses,
so we cover both the happy path and every failure branch without an AWS
account.
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import tools.triage_access_keys as triage  # noqa: E402


# --- Fake ClientError (mirrors botocore.exceptions.ClientError shape) -------


class FakeClientError(triage.ClientError):
    """Minimal ClientError we can raise from a fake client.

    Constructing a real ``botocore.exceptions.ClientError`` requires an
    operation model; this subclass short-circuits that by populating just
    the fields the tool reads (``response.Error.Code`` + ``str(e)``).
    """

    def __init__(self, code: str, message: str = ""):
        self.response = {"Error": {"Code": code, "Message": message or code}}
        self.operation_name = code
        super().__init__(self.response, self.operation_name)


# --- Fake IAM client --------------------------------------------------------


class FakeIamClient:
    """In-memory IAM client the tool can walk.

    Populated with a small, hand-authored inventory in each test. Every
    ``iam:*`` call the tool makes has a corresponding method here that
    returns a canned response or raises a pre-configured error.
    """

    class _Paginator:
        def __init__(self, client, op):
            self._client = client
            self._op = op

        def paginate(self, **_kwargs):
            if self._op == "list_users":
                # Honor the pre-configured error so tests can simulate an
                # AccessDenied on ListUsers via the paginator path the tool
                # actually uses.
                if "list_users" in self._client.raise_on:
                    raise self._client.raise_on["list_users"]
                yield {"Users": [{"UserName": u} for u in self._client.users.keys()]}

    def __init__(self):
        # user_name -> {
        #   keys: [{"AccessKeyId","Status","CreateDate"}],
        #   last_used: {key_id -> {"LastUsedDate","ServiceName"} or Exception},
        #   attached: [{"PolicyName","PolicyArn"}],
        #   inline:   {policy_name -> policy_doc dict},
        #   groups:   ["group_name", ...],
        # }
        self.users: dict = {}
        # group_name -> {"attached": [...], "inline": {name -> doc}}
        self.groups: dict = {}
        # policy_arn -> {"default_version_id","versions": {vid -> doc}}
        self.managed_policies: dict = {}
        # Root user
        self.root_access_keys_present = 0
        # Pre-configured errors keyed by op name (e.g. "list_users")
        self.raise_on: dict = {}
        # Recorded calls for assertions
        self.calls: list = []

    # -- paginator -----
    def get_paginator(self, op):
        return self._Paginator(self, op)

    # -- top-level -----
    def get_account_summary(self):
        self.calls.append(("get_account_summary", {}))
        if "get_account_summary" in self.raise_on:
            raise self.raise_on["get_account_summary"]
        return {"SummaryMap": {"AccountAccessKeysPresent": self.root_access_keys_present}}

    def list_users(self, **kwargs):
        self.calls.append(("list_users", kwargs))
        if "list_users" in self.raise_on:
            raise self.raise_on["list_users"]
        return {"Users": [{"UserName": u} for u in self.users.keys()]}

    # -- per-user -----
    def list_access_keys(self, UserName):
        self.calls.append(("list_access_keys", {"UserName": UserName}))
        u = self.users.get(UserName, {})
        return {"AccessKeyMetadata": [
            {
                "AccessKeyId": k["AccessKeyId"],
                "UserName": UserName,
                "Status": k["Status"],
                "CreateDate": k["CreateDate"],
            }
            for k in u.get("keys", [])
        ]}

    def get_access_key_last_used(self, AccessKeyId):
        self.calls.append(("get_access_key_last_used", {"AccessKeyId": AccessKeyId}))
        for u in self.users.values():
            last_used_map = u.get("last_used", {})
            if AccessKeyId not in last_used_map:
                # Not this user's key — skip. Only the owning user's entry
                # decides between "never used" (None) and "used at T".
                continue
            lookup = last_used_map[AccessKeyId]
            if isinstance(lookup, Exception):
                raise lookup
            if lookup is None:
                return {"AccessKeyLastUsed": {}}  # never used
            return {"AccessKeyLastUsed": lookup}
        return {"AccessKeyLastUsed": {}}

    def list_attached_user_policies(self, UserName):
        self.calls.append(("list_attached_user_policies", {"UserName": UserName}))
        return {"AttachedPolicies": self.users.get(UserName, {}).get("attached", [])}

    def list_user_policies(self, UserName):
        self.calls.append(("list_user_policies", {"UserName": UserName}))
        return {"PolicyNames": list(self.users.get(UserName, {}).get("inline", {}).keys())}

    def get_user_policy(self, UserName, PolicyName):
        self.calls.append(("get_user_policy", {"UserName": UserName, "PolicyName": PolicyName}))
        doc = self.users.get(UserName, {}).get("inline", {}).get(PolicyName)
        return {"PolicyDocument": doc}

    def list_groups_for_user(self, UserName):
        self.calls.append(("list_groups_for_user", {"UserName": UserName}))
        return {"Groups": [
            {"GroupName": g} for g in self.users.get(UserName, {}).get("groups", [])
        ]}

    # -- group -----
    def list_attached_group_policies(self, GroupName):
        self.calls.append(("list_attached_group_policies", {"GroupName": GroupName}))
        return {"AttachedPolicies": self.groups.get(GroupName, {}).get("attached", [])}

    def list_group_policies(self, GroupName):
        self.calls.append(("list_group_policies", {"GroupName": GroupName}))
        return {"PolicyNames": list(self.groups.get(GroupName, {}).get("inline", {}).keys())}

    def get_group_policy(self, GroupName, PolicyName):
        self.calls.append(("get_group_policy", {"GroupName": GroupName, "PolicyName": PolicyName}))
        return {"PolicyDocument": self.groups.get(GroupName, {}).get("inline", {}).get(PolicyName)}

    # -- managed policy resolution -----
    def get_policy(self, PolicyArn):
        self.calls.append(("get_policy", {"PolicyArn": PolicyArn}))
        p = self.managed_policies.get(PolicyArn, {})
        return {"Policy": {
            "PolicyName": PolicyArn.rsplit("/", 1)[-1],
            "DefaultVersionId": p.get("default_version_id", "v1"),
        }}

    def get_policy_version(self, PolicyArn, VersionId):
        self.calls.append(("get_policy_version", {"PolicyArn": PolicyArn, "VersionId": VersionId}))
        p = self.managed_policies.get(PolicyArn, {})
        return {"PolicyVersion": {"Document": p.get("versions", {}).get(VersionId, {})}}


# --- Test helpers -----------------------------------------------------------


NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)


def _dt(days_ago: int) -> datetime:
    return NOW - timedelta(days=days_ago)


def _admin_arn() -> str:
    return "arn:aws:iam::aws:policy/AdministratorAccess"


class _TriageTestBase(unittest.TestCase):
    def setUp(self):
        self._orig_client = triage.iam_client
        self.fake = FakeIamClient()
        triage.iam_client = self.fake

        # Pin ``now`` so KEY_AGE_/IDLE_ counts are deterministic. We swap
        # the ``_now`` helper — NOT the ``datetime`` class — because
        # replacing the class with a subclass would make ``isinstance(x,
        # datetime)`` inside ``_parse_dt`` fail for regular test-suite
        # datetimes.
        self._orig_now = triage._now
        triage._now = lambda: NOW

        # Reset the account-id cache so each test starts fresh.
        triage._CACHED_ACCOUNT_ID = ""

    def tearDown(self):
        triage.iam_client = self._orig_client
        triage._now = self._orig_now
        triage._CACHED_ACCOUNT_ID = ""


# --- Scenario 1: happy path -------------------------------------------------


class HappyPathTest(_TriageTestBase):
    """Three users with keys of varied age, usage, and policy scope. Verifies
    the risk_flags, priority_class, ordering, and summary counts all line up."""

    def test_three_users_ordered_by_priority_then_age(self):
        # User A — admin, stale (old key, never used) → Critical + oldest.
        self.fake.managed_policies[_admin_arn()] = {
            "default_version_id": "v1",
            "versions": {"v1": {
                "Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}]
            }},
        }
        self.fake.users["alice"] = {
            "keys": [
                {"AccessKeyId": "AKIAALICE0001", "Status": "Active", "CreateDate": _dt(500)},
            ],
            "last_used": {"AKIAALICE0001": None},
            "attached": [{"PolicyName": "AdministratorAccess", "PolicyArn": _admin_arn()}],
        }
        # User B — scoped read-only inline, recently used, single key → Rotation-ish.
        self.fake.users["bob"] = {
            "keys": [
                {"AccessKeyId": "AKIABOB000001", "Status": "Active", "CreateDate": _dt(30)},
            ],
            "last_used": {"AKIABOB000001": {
                "LastUsedDate": _dt(5), "ServiceName": "s3",
            }},
            "inline": {"ReadOnly": {"Statement": [{
                "Effect": "Allow", "Action": ["s3:GetObject"],
                "Resource": "arn:aws:s3:::my-bucket/*",
            }]}},
        }
        # User C — idle for 200 days on a scoped policy → Cleanup.
        self.fake.users["carol"] = {
            "keys": [
                {"AccessKeyId": "AKIACAROL0001", "Status": "Active", "CreateDate": _dt(200)},
            ],
            "last_used": {"AKIACAROL0001": {
                "LastUsedDate": _dt(200), "ServiceName": "dynamodb",
            }},
            "inline": {"App": {"Statement": [{
                "Effect": "Allow", "Action": ["dynamodb:GetItem"],
                "Resource": "arn:aws:dynamodb:us-east-1:*:table/orders",
            }]}},
        }

        result = triage.handler({})

        # Alice is Critical (ADMIN + stale) and comes first.
        self.assertEqual(3, result["summary"]["total_keys"])
        self.assertEqual("Critical", result["keys"][0]["priority_class"])
        self.assertEqual("alice", result["keys"][0]["user"])
        self.assertIn("ADMIN", result["keys"][0]["risk_flags"])

        # Carol is Cleanup (IDLE, non-admin, non-broad).
        carol_row = next(r for r in result["keys"] if r["user"] == "carol")
        self.assertEqual("Cleanup", carol_row["priority_class"])
        self.assertTrue(any(f.startswith("IDLE_") for f in carol_row["risk_flags"]))

        # Bob has no age/usage flags — falls to Rotation as the default.
        bob_row = next(r for r in result["keys"] if r["user"] == "bob")
        self.assertEqual("Rotation", bob_row["priority_class"])

        # Sort invariant: priority ascending (Critical→Rotation), then age desc.
        ordering = [triage._priority_order(r["priority_class"]) for r in result["keys"]]
        self.assertEqual(ordering, sorted(ordering))

        # Coverage: both sources checked, no unavailable states.
        states = {c["source"]: c["state"] for c in result["coverage"]}
        self.assertEqual({"iam": "checked", "iam-policy-resolution": "checked"}, states)

        # The usage-lag caveat is always emitted.
        self.assertIn("Last-used data can lag", result["usage_lag_caveat"])


# --- Scenario 2: ADMIN detection is age-independent -------------------------


class AdminDetectionTest(_TriageTestBase):
    """A user with AdministratorAccess is Critical regardless of key age."""

    def test_admin_on_fresh_key_still_critical(self):
        self.fake.managed_policies[_admin_arn()] = {
            "default_version_id": "v1",
            "versions": {"v1": {
                "Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}]
            }},
        }
        self.fake.users["fresh-admin"] = {
            "keys": [{"AccessKeyId": "AKIAFRESH0001", "Status": "Active", "CreateDate": _dt(1)}],
            "last_used": {"AKIAFRESH0001": {
                "LastUsedDate": _dt(0), "ServiceName": "iam",
            }},
            "attached": [{"PolicyName": "AdministratorAccess", "PolicyArn": _admin_arn()}],
        }

        result = triage.handler({})
        self.assertEqual(1, len(result["keys"]))
        row = result["keys"][0]
        self.assertEqual("Critical", row["priority_class"])
        self.assertIn("ADMIN", row["risk_flags"])
        # No IDLE / KEY_AGE / NEVER_USED — key is fresh & used.
        self.assertFalse(any(f.startswith("IDLE_") for f in row["risk_flags"]))
        self.assertFalse(any(f.startswith("KEY_AGE_") for f in row["risk_flags"]))
        self.assertNotIn("NEVER_USED", row["risk_flags"])


# --- Scenario 3: root user special case -------------------------------------


class RootUserTest(_TriageTestBase):
    """A root-key inventory row is always Critical + Remove_Root_Access_Keys."""

    def test_root_keys_present_synthesizes_first_row(self):
        self.fake.root_access_keys_present = 1
        self.fake.users["someuser"] = {
            "keys": [{"AccessKeyId": "AKIA0000USER1", "Status": "Active", "CreateDate": _dt(10)}],
            "last_used": {"AKIA0000USER1": None},
        }

        result = triage.handler({})
        # Root row exists.
        root_rows = [r for r in result["keys"] if r.get("is_root")]
        self.assertEqual(1, len(root_rows))
        root = root_rows[0]
        self.assertEqual("<root>", root["user"])
        self.assertEqual("Critical", root["priority_class"])
        self.assertEqual("Remove_Root_Access_Keys", root["suggested_remediation"])
        # Total counts include the root row.
        self.assertEqual(2, result["summary"]["total_keys"])


# --- Scenario 4: SERVICE_WILDCARD detection ---------------------------------


class ServiceWildcardTest(_TriageTestBase):
    """An inline policy granting bedrock:* flips SERVICE_WILDCARD:bedrock."""

    def test_bedrock_wildcard_flags_service_wildcard(self):
        self.fake.users["ml-user"] = {
            "keys": [{"AccessKeyId": "AKIAMLUSER0001", "Status": "Active", "CreateDate": _dt(10)}],
            "last_used": {"AKIAMLUSER0001": {
                "LastUsedDate": _dt(2), "ServiceName": "bedrock",
            }},
            "inline": {"BedrockAll": {"Statement": [{
                "Effect": "Allow", "Action": "bedrock:*", "Resource": "*",
            }]}},
        }
        result = triage.handler({})
        row = result["keys"][0]
        self.assertIn("SERVICE_WILDCARD:bedrock", row["risk_flags"])
        # Priority is High because of the SERVICE_WILDCARD flag.
        self.assertEqual("High", row["priority_class"])
        # RESOURCE_WILDCARD also flagged because Resource:*.
        self.assertIn("RESOURCE_WILDCARD", row["risk_flags"])


# --- Scenario 5: LASTUSED_UNKNOWN preserves the "could not check" state -----


class LastUsedUnknownTest(_TriageTestBase):
    """When GetAccessKeyLastUsed raises, the key is LASTUSED_UNKNOWN — never
    NEVER_USED or IDLE_* — so a coverage gap is not silently reported as
    'confirmed unused'."""

    def test_throttling_lookup_sets_only_lastused_unknown(self):
        throttle = FakeClientError("Throttling", "Rate exceeded")
        self.fake.users["throttled"] = {
            "keys": [{"AccessKeyId": "AKIATHRO000001", "Status": "Active", "CreateDate": _dt(60)}],
            "last_used": {"AKIATHRO000001": throttle},
            "inline": {"ReadOnly": {"Statement": [{
                "Effect": "Allow", "Action": "s3:GetObject",
                "Resource": "arn:aws:s3:::b/*",
            }]}},
        }
        result = triage.handler({})
        row = result["keys"][0]
        self.assertIn("LASTUSED_UNKNOWN", row["risk_flags"])
        self.assertNotIn("NEVER_USED", row["risk_flags"])
        self.assertFalse(any(f.startswith("IDLE_") for f in row["risk_flags"]))
        # Coverage detail names the per-key throttle so the operator sees it.
        iam_cov = next(c for c in result["coverage"] if c["source"] == "iam")
        self.assertIn("GetAccessKeyLastUsed", iam_cov["detail"])


# --- Scenario 6: top-level coverage unavailable -----------------------------


class ListUsersDeniedTest(_TriageTestBase):
    """An iam:ListUsers AccessDenied returns keys=[] and coverage=unavailable."""

    def test_access_denied_returns_no_partial_data(self):
        self.fake.raise_on["list_users"] = FakeClientError("AccessDenied", "not allowed")
        result = triage.handler({})
        self.assertEqual([], result["keys"])
        iam_cov = [c for c in result["coverage"] if c["source"] == "iam"]
        self.assertEqual(1, len(iam_cov))
        self.assertEqual("unavailable", iam_cov[0]["state"])
        self.assertIn("AccessDenied", iam_cov[0]["detail"])
        # Handler bailed before the policy walk, so there's no
        # iam-policy-resolution entry — that source was never consulted.
        self.assertFalse(any(c["source"] == "iam-policy-resolution" for c in result["coverage"]))


# --- Scenario 7: MULTI_ACTIVE_KEYS on all active keys -----------------------


class MultiActiveKeysTest(_TriageTestBase):
    """Two Active keys on one user tag both rows with MULTI_ACTIVE_KEYS."""

    def test_two_active_keys_flag_both(self):
        self.fake.users["dual"] = {
            "keys": [
                {"AccessKeyId": "AKIADUAL000001", "Status": "Active", "CreateDate": _dt(10)},
                {"AccessKeyId": "AKIADUAL000002", "Status": "Active", "CreateDate": _dt(20)},
            ],
            "last_used": {
                "AKIADUAL000001": {"LastUsedDate": _dt(1), "ServiceName": "s3"},
                "AKIADUAL000002": {"LastUsedDate": _dt(2), "ServiceName": "s3"},
            },
            "inline": {"ReadOnly": {"Statement": [{
                "Effect": "Allow", "Action": "s3:GetObject",
                "Resource": "arn:aws:s3:::b/*",
            }]}},
        }
        result = triage.handler({})
        self.assertEqual(2, len(result["keys"]))
        for row in result["keys"]:
            self.assertIn("MULTI_ACTIVE_KEYS", row["risk_flags"])


# --- Scenario 8: effective-policy walk merges all three sources -------------


class EffectivePolicyWalkTest(_TriageTestBase):
    """A user with attached-managed + inline + group-attached policies has
    all three sources represented in the ``policies`` field, and the merged
    ``actions`` string names statements from each."""

    def test_all_three_policy_sources_merged(self):
        broad_arn = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
        team_arn = "arn:aws:iam::123456789012:policy/TeamPolicy"
        self.fake.managed_policies[broad_arn] = {
            "default_version_id": "v1",
            "versions": {"v1": {"Statement": [{
                "Effect": "Allow", "Action": "s3:*", "Resource": "*",
            }]}},
        }
        self.fake.managed_policies[team_arn] = {
            "default_version_id": "v1",
            "versions": {"v1": {"Statement": [{
                "Effect": "Allow", "Action": ["ec2:DescribeInstances"],
                "Resource": "*",
            }]}},
        }
        self.fake.groups["eng"] = {
            "attached": [{"PolicyName": "TeamPolicy", "PolicyArn": team_arn}],
        }
        self.fake.users["multi-source"] = {
            "keys": [{"AccessKeyId": "AKIAMULTI00001", "Status": "Active", "CreateDate": _dt(10)}],
            "last_used": {"AKIAMULTI00001": {"LastUsedDate": _dt(1), "ServiceName": "s3"}},
            "attached": [{"PolicyName": "AmazonS3FullAccess", "PolicyArn": broad_arn}],
            "inline": {"LocalAdmin": {"Statement": [{
                "Effect": "Allow", "Action": ["logs:CreateLogGroup"], "Resource": "*",
            }]}},
            "groups": ["eng"],
        }

        result = triage.handler({})
        row = result["keys"][0]

        # All three policy sources present in the policies field.
        self.assertIn("AmazonS3FullAccess", row["policies"])
        self.assertIn("inline:LocalAdmin", row["policies"])
        self.assertIn("group:eng/TeamPolicy", row["policies"])

        # BROAD flag captured the attached AmazonS3FullAccess.
        self.assertTrue(any(f.startswith("BROAD:") and "AmazonS3FullAccess" in f
                            for f in row["risk_flags"]))
        # SERVICE_WILDCARD:s3 also present (bracket over the s3:* action set).
        self.assertIn("SERVICE_WILDCARD:s3", row["risk_flags"])
        # Priority is High from BROAD/SERVICE_WILDCARD.
        self.assertEqual("High", row["priority_class"])


# --- Bonus scenario: remediation mapping regex ------------------------------
#
# Cheap to add and it pins the identity-pattern mapping from DD-7 against
# regressions. Not strictly required by Req 10 but every row's
# suggested_remediation depends on it.


class RemediationMappingTest(unittest.TestCase):
    def test_email_maps_to_sso_federation(self):
        self.assertEqual(
            "SSO_Federation",
            triage._suggested_remediation("alice@example.com"),
        )

    def test_dotted_name_maps_to_sso_federation(self):
        self.assertEqual(
            "SSO_Federation",
            triage._suggested_remediation("alice.smith"),
        )

    def test_service_prefix_maps_to_iam_role(self):
        self.assertEqual("IAM_Role", triage._suggested_remediation("svc-data-loader"))

    def test_cicd_pattern_maps_to_oidc(self):
        self.assertEqual(
            "OIDC_Federation",
            triage._suggested_remediation("prod-github-actions-deployer"),
        )

    def test_cicd_beats_service_when_both_match(self):
        # `-lambda-deployer-ci` matches BOTH the service pattern (`-lambda-`)
        # and the CI/CD pattern (`-ci` suffix). DD-7 orders CI/CD first.
        self.assertEqual(
            "OIDC_Federation",
            triage._suggested_remediation("my-lambda-deployer-ci"),
        )

    def test_default_is_cross_account_role(self):
        self.assertEqual(
            "Cross_Account_Role_With_External_Id",
            triage._suggested_remediation("mystery-account"),
        )

    def test_root_forces_remove_root_access_keys(self):
        self.assertEqual(
            "Remove_Root_Access_Keys",
            triage._suggested_remediation("alice", is_root=True),
        )

    # ----- Prefix / suffix tolerance -----
    # Real customer naming often adds environment or ownership prefixes
    # (e.g. `prod-`, `team-alpha-`, `test-`). The classifier must match
    # the identity signal wherever it appears, not just at start of name.

    def test_prefixed_dotted_name_still_maps_to_sso_federation(self):
        for name in (
            "triage-test-alice.admin",   # fixture prefix
            "prod-bob.developer",        # env prefix
            "team-alpha-jane.doe",       # ownership prefix
        ):
            self.assertEqual(
                "SSO_Federation",
                triage._suggested_remediation(name),
                msg=f"{name!r} should map to SSO_Federation",
            )

    def test_prefixed_service_name_still_maps_to_iam_role(self):
        for name in (
            "triage-test-svc-loader",
            "prod-svc-data-loader",
            "team-alpha-service-runner",
        ):
            self.assertEqual(
                "IAM_Role",
                triage._suggested_remediation(name),
                msg=f"{name!r} should map to IAM_Role",
            )

    def test_prefixed_cicd_name_still_maps_to_oidc(self):
        for name in (
            "triage-test-bedrock-ci-worker",
            "prod-github-actions-deployer",
            "team-jenkins-runner",
        ):
            self.assertEqual(
                "OIDC_Federation",
                triage._suggested_remediation(name),
                msg=f"{name!r} should map to OIDC_Federation",
            )


# --- Remediation doc URLs ---------------------------------------------------


class RemediationUrlTest(unittest.TestCase):
    """Pins the _REMEDIATION_DOCS mapping and the emit-URL contract:
    every canonical remediation label maps to a real AWS docs URL, and
    an unknown label yields an empty string rather than a fabricated URL.
    """

    def test_every_canonical_label_has_a_url(self):
        for label in (
            "SSO_Federation",
            "IAM_Role",
            "OIDC_Federation",
            "IAM_Roles_Anywhere",
            "Cross_Account_Role_With_External_Id",
            "Remove_Root_Access_Keys",
        ):
            url = triage._remediation_url(label)
            self.assertTrue(
                url.startswith("https://docs.aws.amazon.com/"),
                f"{label} → {url!r} is not an AWS docs URL",
            )

    def test_unknown_label_returns_empty_string(self):
        # Frontend and the model both branch on empty vs. non-empty. Never
        # fabricate a URL for a label the tool doesn't recognize.
        self.assertEqual("", triage._remediation_url("Nope_Not_A_Label"))
        self.assertEqual("", triage._remediation_url(""))


# --- Migration step lists ---------------------------------------------------


class RemediationStepsTest(unittest.TestCase):
    """Pins the _REMEDIATION_STEPS mapping — every canonical remediation
    label has a non-empty ordered walk-through, and unknown labels yield
    an empty list rather than a fabricated one."""

    def test_every_canonical_label_has_steps(self):
        for label in (
            "SSO_Federation",
            "IAM_Role",
            "OIDC_Federation",
            "IAM_Roles_Anywhere",
            "Cross_Account_Role_With_External_Id",
            "Remove_Root_Access_Keys",
        ):
            steps = triage._remediation_steps(label)
            self.assertIsInstance(steps, list, msg=f"{label} must return a list")
            self.assertGreaterEqual(
                len(steps), 5,
                msg=f"{label} has too few steps ({len(steps)}) — expected 5+",
            )
            # Every step is a non-empty string.
            for i, step in enumerate(steps):
                self.assertIsInstance(step, str, msg=f"{label} step {i} not a string")
                self.assertGreater(len(step), 0, msg=f"{label} step {i} is empty")

    def test_every_label_ends_with_deactivate_monitor_delete_pattern(self):
        # Every migration must end in the safety-first three-step pattern,
        # except Remove_Root_Access_Keys which handles it slightly
        # differently (keys are deactivated first, then monitored, then
        # deleted, but the pattern is present).
        for label in (
            "SSO_Federation",
            "IAM_Role",
            "OIDC_Federation",
            "IAM_Roles_Anywhere",
            "Cross_Account_Role_With_External_Id",
        ):
            steps = triage._remediation_steps(label)
            joined = " ".join(steps).lower()
            self.assertIn("deactivate", joined, msg=f"{label} missing 'deactivate'")
            self.assertIn("monitor", joined, msg=f"{label} missing 'monitor'")
            self.assertIn("delete", joined, msg=f"{label} missing 'delete'")

    def test_unknown_label_returns_empty_list(self):
        # Frontend omits the Migration steps section on empty. Never
        # fabricate a step list for an unrecognized label.
        self.assertEqual([], triage._remediation_steps("Nope_Not_A_Label"))
        self.assertEqual([], triage._remediation_steps(""))

    def test_returned_list_is_defensive_copy(self):
        # A caller mutating the returned list must not corrupt the
        # module-level source of truth.
        got = triage._remediation_steps("SSO_Federation")
        got.append("mutation")
        again = triage._remediation_steps("SSO_Federation")
        self.assertNotIn("mutation", again)


class AccountIdResolutionTest(unittest.TestCase):
    """Pins _account_id resolution: env var wins over STS; STS is only
    called as a fallback; result is cached across calls; STS failure
    yields an empty string without raising."""

    def setUp(self):
        # Reset cache and env for each test.
        triage._CACHED_ACCOUNT_ID = ""
        self._saved_env = os.environ.pop("AWS_ACCOUNT_ID", None)

    def tearDown(self):
        triage._CACHED_ACCOUNT_ID = ""
        if self._saved_env is not None:
            os.environ["AWS_ACCOUNT_ID"] = self._saved_env
        else:
            os.environ.pop("AWS_ACCOUNT_ID", None)

    def test_env_var_wins_when_set(self):
        os.environ["AWS_ACCOUNT_ID"] = "111122223333"
        self.assertEqual("111122223333", triage._account_id())

    def test_falls_back_to_sts_when_no_env(self):
        from unittest.mock import patch, MagicMock
        fake_sts = MagicMock()
        fake_sts.get_caller_identity.return_value = {"Account": "555566667777"}
        with patch.object(triage.boto3, "client", return_value=fake_sts):
            self.assertEqual("555566667777", triage._account_id())
            fake_sts.get_caller_identity.assert_called_once()

    def test_result_is_cached(self):
        from unittest.mock import patch, MagicMock
        fake_sts = MagicMock()
        fake_sts.get_caller_identity.return_value = {"Account": "555566667777"}
        with patch.object(triage.boto3, "client", return_value=fake_sts):
            triage._account_id()
            triage._account_id()
            triage._account_id()
            # Only called once — subsequent invocations hit the cache.
            fake_sts.get_caller_identity.assert_called_once()

    def test_sts_failure_returns_empty_string(self):
        from unittest.mock import patch
        with patch.object(
            triage.boto3, "client", side_effect=Exception("network down")
        ):
            # Must not raise; must return empty string.
            self.assertEqual("", triage._account_id())


if __name__ == "__main__":
    unittest.main()
