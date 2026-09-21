"""Regression tests for #171 coverage on the S3 tools.

Pins the per-source coverage contract for:
  * export_report    (writes + presigns)
  * list_exports     (lists / mints get-link URLs)

Same three states as elsewhere: ``checked`` (call succeeded, data
returned), ``empty`` (call succeeded, no data), ``unavailable`` (call
failed or was never attempted because config was missing).
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tools import export_report, list_exports  # noqa: E402


_TEST_ARN = "arn:aws:iam::280072637828:role/test-presigner"


def _cov(result: dict, source: str = "s3") -> dict:
    for entry in result.get("coverage") or []:
        if entry.get("source") == source:
            return entry
    raise AssertionError(f"No coverage for source={source!r} in: {result!r}")


class ExportReportCoverageTest(unittest.TestCase):
    def setUp(self):
        # Force the assume-role code path so the test doesn't rely on live STS.
        self._patches = [
            patch.object(export_report, "PRESIGNER_ROLE_ARN", _TEST_ARN),
            patch.object(export_report, "REPORTS_BUCKET", "test-bucket"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _run_with_mocks(self, write_client, sign_client, event=None):
        with patch.object(
            export_report, "_build_lambda_role_s3", return_value=write_client
        ), patch.object(export_report, "boto3") as mock_boto3:
            def _client(service, *_a, **_kw):
                if service == "sts":
                    sts = MagicMock()
                    sts.assume_role.return_value = {
                        "Credentials": {
                            "AccessKeyId": "AKIAFAKE",
                            "SecretAccessKey": "secret",
                            "SessionToken": "token",
                        }
                    }
                    return sts
                if service == "s3":
                    return sign_client
                raise AssertionError(f"unexpected service {service}")

            mock_boto3.client.side_effect = _client

            return export_report.handler(event or {"content": "hello"})

    def test_checked_on_happy_path(self):
        write_client = MagicMock(name="lambda_role_s3")
        write_client.generate_presigned_url.return_value = "https://example/dl"
        sign_client = MagicMock(name="presigner_role_s3")
        sign_client.generate_presigned_url.return_value = "https://example/dl"

        result = self._run_with_mocks(write_client, sign_client)
        self.assertTrue(result.get("success"), msg=result)
        cov = _cov(result)
        self.assertEqual(cov["state"], "checked")

    def test_missing_content_is_unavailable(self):
        result = export_report.handler({"content": ""})
        self.assertIn("error", result)
        cov = _cov(result)
        self.assertEqual(cov["state"], "unavailable")
        self.assertIn("content", cov["detail"])

    def test_missing_bucket_is_unavailable(self):
        with patch.object(export_report, "REPORTS_BUCKET", ""):
            result = export_report.handler({"content": "hello"})
        self.assertIn("error", result)
        cov = _cov(result)
        self.assertEqual(cov["state"], "unavailable")
        self.assertIn("REPORTS_BUCKET", cov["detail"])

    def test_put_object_failure_is_unavailable(self):
        write_client = MagicMock(name="lambda_role_s3")
        write_client.put_object.side_effect = RuntimeError("bucket denied")
        sign_client = MagicMock(name="presigner_role_s3")

        result = self._run_with_mocks(write_client, sign_client)
        self.assertFalse(result.get("success", False))
        cov = _cov(result)
        self.assertEqual(cov["state"], "unavailable")
        self.assertIn("bucket denied", cov["detail"])


class ListExportsCoverageTest(unittest.TestCase):
    def setUp(self):
        self._patches = [
            patch.object(list_exports, "PRESIGNER_ROLE_ARN", _TEST_ARN),
            patch.object(list_exports, "REPORTS_BUCKET", "test-bucket"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _run(self, read_client, sign_client, event):
        with patch.object(
            list_exports, "_build_lambda_role_s3", return_value=read_client
        ), patch.object(list_exports, "boto3") as mock_boto3:
            def _client(service, *_a, **_kw):
                if service == "sts":
                    sts = MagicMock()
                    sts.assume_role.return_value = {
                        "Credentials": {
                            "AccessKeyId": "AKIAFAKE",
                            "SecretAccessKey": "secret",
                            "SessionToken": "token",
                        }
                    }
                    return sts
                if service == "s3":
                    return sign_client
                raise AssertionError(f"unexpected service {service}")

            mock_boto3.client.side_effect = _client

            return list_exports.handler(event)

    def test_list_with_files_is_checked_with_count(self):
        read_client = MagicMock(name="lambda_role_s3")
        read_client.list_objects_v2.return_value = {
            "Contents": [
                {
                    "Key": "policies/one.md",
                    "Size": 100,
                    "LastModified": _fake_datetime(),
                },
                {
                    "Key": "policies/two.md",
                    "Size": 200,
                    "LastModified": _fake_datetime(),
                },
            ]
        }
        sign_client = MagicMock(name="presigner_role_s3")

        result = self._run(read_client, sign_client, {"action": "list"})
        cov = _cov(result)
        self.assertEqual(cov["state"], "checked")
        self.assertEqual(cov["count"], 2)

    def test_list_empty_bucket_is_empty(self):
        read_client = MagicMock(name="lambda_role_s3")
        read_client.list_objects_v2.return_value = {"Contents": []}
        sign_client = MagicMock(name="presigner_role_s3")

        result = self._run(read_client, sign_client, {"action": "list"})
        cov = _cov(result)
        self.assertEqual(cov["state"], "empty")
        self.assertEqual(cov["count"], 0)

    def test_get_link_success_is_checked(self):
        read_client = MagicMock(name="lambda_role_s3")
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {"Contents": [{"Key": "policies/report-1.md"}]}
        ]
        read_client.get_paginator.return_value = paginator

        sign_client = MagicMock(name="presigner_role_s3")
        sign_client.generate_presigned_url.return_value = "https://example/dl"

        result = self._run(
            read_client, sign_client, {"action": "get_link", "filename": "report-1.md"}
        )
        cov = _cov(result)
        self.assertEqual(cov["state"], "checked")

    def test_get_link_not_found_is_empty(self):
        read_client = MagicMock(name="lambda_role_s3")
        paginator = MagicMock()
        paginator.paginate.return_value = [{"Contents": []}]
        read_client.get_paginator.return_value = paginator

        sign_client = MagicMock(name="presigner_role_s3")

        result = self._run(
            read_client, sign_client, {"action": "get_link", "filename": "missing.md"}
        )
        cov = _cov(result)
        self.assertEqual(cov["state"], "empty")

    def test_missing_bucket_is_unavailable(self):
        with patch.object(list_exports, "REPORTS_BUCKET", ""):
            result = list_exports.handler({"action": "list"})
        cov = _cov(result)
        self.assertEqual(cov["state"], "unavailable")
        self.assertIn("REPORTS_BUCKET", cov["detail"])


def _fake_datetime():
    """Return a minimal datetime shim that supports strftime + comparison."""
    from datetime import datetime, timezone

    return datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()
