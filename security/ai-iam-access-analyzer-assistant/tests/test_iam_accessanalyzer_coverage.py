"""Regression tests for #171 coverage on the IAM + Access Analyzer tools.

Pins the per-source coverage contract for:
  * check_dependencies   (source: iam)
  * compare_roles        (source: iam + cloudtrail)
  * validate_policy      (source: accessanalyzer)

Each tool must emit a ``coverage`` array with the appropriate source(s),
with state ``checked`` on the happy path and ``unavailable`` when the
underlying AWS call errors out. For validate_policy specifically:
a ``ValidationException`` from Access Analyzer is ``checked`` (the API
did its job and rejected the input), while any other exception is
``unavailable`` (we could not check).
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

from tools import check_dependencies, compare_roles, validate_policy  # noqa: E402


def _cov(result: dict, source: str) -> dict:
    """Return the coverage entry for `source` (or fail loudly)."""
    entries = result.get("coverage") or []
    for entry in entries:
        if entry.get("source") == source:
            return entry
    raise AssertionError(
        f"No coverage entry for source={source!r} in result: {result!r}"
    )


# --------------------------------------------------------- check_dependencies


class CheckDependenciesCoverageTest(unittest.TestCase):
    def test_checked_on_happy_path(self):
        """Happy path returns coverage[iam].state == 'checked'."""
        # Mock all IAM calls the role dependencies path needs.
        role_arn = "arn:aws:iam::123456789012:role/my-role"

        with patch.object(check_dependencies, "iam_client") as mock_iam:
            mock_iam.get_role.return_value = {
                "Role": {
                    "RoleName": "my-role",
                    "Arn": role_arn,
                    "AssumeRolePolicyDocument": "{}",
                }
            }
            mock_iam.list_attached_role_policies.return_value = {"AttachedPolicies": []}
            mock_iam.list_role_policies.return_value = {"PolicyNames": []}
            mock_iam.exceptions = MagicMock()
            # Distinct exception class to avoid accidental catches.
            mock_iam.exceptions.NoSuchEntityException = type(
                "NoSuchEntityException", (Exception,), {}
            )

            result = check_dependencies.handler({"entity_arn": role_arn})

        cov = _cov(result, "iam")
        self.assertEqual(cov["state"], "checked")

    def test_unavailable_on_outer_exception(self):
        """A top-level failure returns coverage[iam].state == 'unavailable'."""
        # Force an exception the outer try/except will catch by patching a
        # pure helper on the module to raise.
        with patch.object(
            check_dependencies,
            "_build_graph_summary",
            side_effect=RuntimeError("boom"),
        ), patch.object(check_dependencies, "iam_client") as mock_iam:
            mock_iam.get_role.return_value = {
                "Role": {
                    "RoleName": "r",
                    "Arn": "arn:aws:iam::123456789012:role/r",
                    "AssumeRolePolicyDocument": "{}",
                }
            }
            mock_iam.list_attached_role_policies.return_value = {"AttachedPolicies": []}
            mock_iam.list_role_policies.return_value = {"PolicyNames": []}
            mock_iam.exceptions = MagicMock()
            mock_iam.exceptions.NoSuchEntityException = type(
                "NoSuchEntityException", (Exception,), {}
            )
            result = check_dependencies.handler(
                {"entity_arn": "arn:aws:iam::123456789012:role/r"}
            )

        self.assertIn("error", result)
        cov = _cov(result, "iam")
        self.assertEqual(cov["state"], "unavailable")
        self.assertIn("boom", cov["detail"])


# ------------------------------------------------------------- compare_roles


class CompareRolesCoverageTest(unittest.TestCase):
    def test_checked_on_happy_path_reports_both_sources(self):
        """Happy path emits both iam and cloudtrail as checked."""
        with patch.object(
            compare_roles,
            "_analyze_role",
            return_value={"role_name": "r", "exists": True, "risk_score": 0, "risk_factors": []},
        ):
            result = compare_roles.handler({"role_names": ["r1", "r2"]})

        self.assertNotIn("error", result)
        self.assertEqual(_cov(result, "iam")["state"], "checked")
        self.assertEqual(_cov(result, "cloudtrail")["state"], "checked")

    def test_unavailable_on_outer_exception_reports_both_unavailable(self):
        """Top-level failure — we can't attribute to a specific source, so
        report both as unavailable."""
        with patch.object(
            compare_roles, "_analyze_role", side_effect=RuntimeError("thread died")
        ):
            result = compare_roles.handler({"role_names": ["r1", "r2"]})

        self.assertIn("error", result)
        self.assertEqual(_cov(result, "iam")["state"], "unavailable")
        self.assertEqual(_cov(result, "cloudtrail")["state"], "unavailable")
        self.assertIn("thread died", _cov(result, "iam")["detail"])


# ---------------------------------------------------------- validate_policy


VALID_POLICY = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": "s3:GetObject", "Resource": "*"}
        ],
    }
)


class ValidatePolicyCoverageTest(unittest.TestCase):
    def test_checked_on_happy_validate_policy(self):
        with patch.object(validate_policy, "access_analyzer_client") as mock_aa:
            mock_aa.validate_policy.return_value = {"findings": []}
            mock_aa.exceptions = MagicMock()
            mock_aa.exceptions.ValidationException = type(
                "ValidationException", (Exception,), {}
            )

            result = validate_policy.handler({"policy_document": VALID_POLICY})

        cov = _cov(result, "accessanalyzer")
        self.assertEqual(cov["state"], "checked")

    def test_validation_exception_from_aa_is_still_checked(self):
        """AA rejecting a malformed policy is AA working correctly, not an
        outage. Coverage stays 'checked' — the API did its job.
        """
        class _VE(Exception):
            pass

        with patch.object(validate_policy, "access_analyzer_client") as mock_aa:
            mock_aa.validate_policy.side_effect = _VE("bad policy")
            mock_aa.exceptions = MagicMock()
            mock_aa.exceptions.ValidationException = _VE

            result = validate_policy.handler({"policy_document": VALID_POLICY})

        cov = _cov(result, "accessanalyzer")
        self.assertEqual(cov["state"], "checked")

    def test_generic_exception_from_aa_is_unavailable(self):
        """Any non-ValidationException from Access Analyzer means the tool
        could not reach the service. Coverage must be 'unavailable' so the
        caller doesn't act on partial results as if they were complete."""
        class _VE(Exception):
            pass

        with patch.object(validate_policy, "access_analyzer_client") as mock_aa:
            mock_aa.validate_policy.side_effect = RuntimeError("throttled")
            mock_aa.exceptions = MagicMock()
            mock_aa.exceptions.ValidationException = _VE

            result = validate_policy.handler({"policy_document": VALID_POLICY})

        cov = _cov(result, "accessanalyzer")
        self.assertEqual(cov["state"], "unavailable")

    def test_unavailable_when_check_access_not_granted_fails(self):
        """Even if ValidatePolicy succeeded, a non-ValidationException from
        CheckAccessNotGranted must flip coverage to unavailable."""
        class _VE(Exception):
            pass

        with patch.object(validate_policy, "access_analyzer_client") as mock_aa:
            mock_aa.validate_policy.return_value = {"findings": []}
            mock_aa.check_access_not_granted.side_effect = RuntimeError("throttled")
            mock_aa.exceptions = MagicMock()
            mock_aa.exceptions.ValidationException = _VE

            result = validate_policy.handler({
                "policy_document": VALID_POLICY,
                "check_actions_not_granted": ["iam:*"],
            })

        cov = _cov(result, "accessanalyzer")
        self.assertEqual(cov["state"], "unavailable")


if __name__ == "__main__":
    unittest.main()
