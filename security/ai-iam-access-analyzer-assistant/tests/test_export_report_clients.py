"""Regression tests for export_report / list_exports client-splitting.

These pin the invariant that broke on the first PresignerRole deploy: the
S3 upload must run as the Lambda role (which has s3:PutObject), and only
generate_presigned_url may ride on the assumed presigner role (which is
read-only). Wiring both operations through a single "signing client" landed
uploads on the read-only role and produced an AccessDenied on PutObject.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("REPORTS_BUCKET", "test-bucket")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tools import export_report, list_exports  # noqa: E402

# NOTE: PRESIGNER_ROLE_ARN is read at module import time in export_report/list_exports.
# Other test files (e.g. test_conversation_local.py) can import those modules
# before this test's module-level code runs, freezing PRESIGNER_ROLE_ARN=""
# regardless of what we set in os.environ here. Patch the module-level constant
# directly so this test's expectations hold under `unittest discover`.
_TEST_PRESIGNER_ARN = "arn:aws:iam::280072637828:role/test-presigner-role"


def _fake_creds():
    return {
        "Credentials": {
            "AccessKeyId": "AKIAFAKE",
            "SecretAccessKey": "secret",
            "SessionToken": "token",
        }
    }


class ExportReportClientSplitTest(unittest.TestCase):
    """put_object must NOT run on the assumed presigner-role client."""

    def setUp(self):
        # Force the assume-role code path regardless of prior import order.
        # REPORTS_BUCKET and PRESIGNER_ROLE_ARN are read at module import time,
        # so we must patch the module-level constants directly.
        self._patches = [
            patch.object(export_report, "PRESIGNER_ROLE_ARN", _TEST_PRESIGNER_ARN),
            patch.object(export_report, "REPORTS_BUCKET", "test-bucket"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_upload_uses_lambda_role_client_not_presigner(self):
        write_client = MagicMock(name="lambda_role_s3")
        write_client.generate_presigned_url.return_value = (
            "https://s3.example.com/download"
        )
        sign_client = MagicMock(name="presigner_role_s3")
        sign_client.generate_presigned_url.return_value = (
            "https://s3.example.com/download"
        )

        with patch.object(
            export_report, "_build_lambda_role_s3", return_value=write_client
        ), patch.object(export_report, "boto3") as mock_boto3:
            mock_boto3.client.return_value.assume_role.return_value = (
                _fake_creds()
            )
            mock_boto3.client.side_effect = None
            # boto3.client("sts", ...) -> STS mock; boto3.client("s3", ...) ->
            # sign_client. Route calls by service name so the test doesn't
            # depend on call order.
            def _client(service, *_a, **_kw):
                if service == "sts":
                    sts = MagicMock()
                    sts.assume_role.return_value = _fake_creds()
                    return sts
                if service == "s3":
                    return sign_client
                raise AssertionError(f"unexpected service {service}")

            mock_boto3.client.side_effect = _client

            result = export_report.handler(
                {"content": "# hello", "content_type": "action_plan"}
            )

        self.assertTrue(
            result.get("success"),
            msg=f"handler failed: {result}",
        )
        # The upload must have happened on the Lambda role client, NOT the
        # presigner-role sign client.
        write_client.put_object.assert_called_once()
        sign_client.put_object.assert_not_called()
        # And the presigned URL must have been produced by the sign client.
        sign_client.generate_presigned_url.assert_called_once()
        # 1-hour expiry when signing with the assumed role.
        _, kwargs = sign_client.generate_presigned_url.call_args
        self.assertEqual(kwargs.get("ExpiresIn"), 3600)
        self.assertEqual(result["valid_for"], "1 hour")

    def test_falls_back_to_lambda_role_signing_when_assume_fails(self):
        # setUp already patched PRESIGNER_ROLE_ARN; keep it here too.
        write_client = MagicMock(name="lambda_role_s3")
        write_client.generate_presigned_url.return_value = (
            "https://s3.example.com/download"
        )

        with patch.object(
            export_report, "_build_lambda_role_s3", return_value=write_client
        ), patch.object(export_report, "boto3") as mock_boto3:
            def _client(service, *_a, **_kw):
                if service == "sts":
                    sts = MagicMock()
                    sts.assume_role.side_effect = RuntimeError("denied")
                    return sts
                raise AssertionError(
                    f"no S3 client should be built on assume failure, got {service}"
                )

            mock_boto3.client.side_effect = _client

            result = export_report.handler(
                {"content": "# hello", "content_type": "action_plan"}
            )

        self.assertTrue(result.get("success"), msg=f"handler failed: {result}")
        write_client.put_object.assert_called_once()
        write_client.generate_presigned_url.assert_called_once()
        _, kwargs = write_client.generate_presigned_url.call_args
        # Falls back to 5 minutes when we could not assume the presigner role.
        self.assertEqual(kwargs.get("ExpiresIn"), 300)
        self.assertEqual(result["valid_for"], "5 minutes")


class ListExportsClientSplitTest(unittest.TestCase):
    """list_objects_v2 must run on the Lambda role; only presign uses the
    assumed presigner role."""

    def setUp(self):
        self._patches = [
            patch.object(list_exports, "PRESIGNER_ROLE_ARN", _TEST_PRESIGNER_ARN),
            patch.object(list_exports, "REPORTS_BUCKET", "test-bucket"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_get_link_reads_on_lambda_role_and_signs_on_presigner(self):
        read_client = MagicMock(name="lambda_role_s3")
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {"Contents": [{"Key": "action-plans/plan-1.md"}]}
        ]
        read_client.get_paginator.return_value = paginator

        sign_client = MagicMock(name="presigner_role_s3")
        sign_client.generate_presigned_url.return_value = (
            "https://s3.example.com/dl"
        )

        with patch.object(
            list_exports, "_build_lambda_role_s3", return_value=read_client
        ), patch.object(list_exports, "boto3") as mock_boto3:
            def _client(service, *_a, **_kw):
                if service == "sts":
                    sts = MagicMock()
                    sts.assume_role.return_value = _fake_creds()
                    return sts
                if service == "s3":
                    return sign_client
                raise AssertionError(f"unexpected service {service}")

            mock_boto3.client.side_effect = _client

            result = list_exports.handler(
                {"action": "get_link", "filename": "plan-1.md"}
            )

        self.assertNotIn("error", result, msg=result)
        # Listing rides on the Lambda role.
        read_client.get_paginator.assert_called_once_with("list_objects_v2")
        # Presigning rides on the presigner role.
        sign_client.generate_presigned_url.assert_called_once()
        self.assertEqual(result["valid_for"], "1 hour")


if __name__ == "__main__":
    unittest.main()
