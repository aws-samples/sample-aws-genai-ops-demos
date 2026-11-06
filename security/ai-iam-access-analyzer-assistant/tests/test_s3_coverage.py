"""Coverage-contract tests for export_report and list_exports.

Pins the per-source coverage contract (from #171): every response has a
``coverage`` array with entries shaped as ``{source, state, detail, count?}``.
``state`` is one of ``checked`` (call succeeded, data returned), ``empty``
(call succeeded, no data), ``unavailable`` (call failed or config missing).

These tests focus on the coverage envelope specifically — they do NOT pin
implementation details of the S3 client wiring (which is separately
covered in test_export_report_clients.py). The setup swaps the module-
level ``s3_client`` to a MagicMock so no live AWS traffic is generated.
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


def _cov(result: dict, source: str = "s3") -> dict:
    for entry in result.get("coverage") or []:
        if entry.get("source") == source:
            return entry
    raise AssertionError(f"No coverage for source={source!r} in: {result!r}")


class ExportReportCoverageTest(unittest.TestCase):
    def setUp(self):
        self._patches = [
            patch.object(export_report, "REPORTS_BUCKET", "test-bucket"),
            patch.object(export_report, "API_ENDPOINT", "https://api.example.com/prod/"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_checked_on_happy_path(self):
        s3 = MagicMock(name="lambda_role_s3")
        with patch.object(export_report, "s3_client", s3):
            result = export_report.handler(
                {"content": "hello", "content_type": "report"}
            )
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
        s3 = MagicMock(name="lambda_role_s3")
        s3.put_object.side_effect = RuntimeError("bucket denied")
        with patch.object(export_report, "s3_client", s3):
            result = export_report.handler({"content": "hello"})
        self.assertFalse(result.get("success", False))
        cov = _cov(result)
        self.assertEqual(cov["state"], "unavailable")
        self.assertIn("bucket denied", cov["detail"])


class ListExportsCoverageTest(unittest.TestCase):
    def setUp(self):
        self._patches = [
            patch.object(list_exports, "REPORTS_BUCKET", "test-bucket"),
            patch.object(list_exports, "API_ENDPOINT", "https://api.example.com/prod/"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_list_with_files_is_checked_with_count(self):
        s3 = MagicMock(name="lambda_role_s3")
        s3.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "policies/one.md", "Size": 100, "LastModified": _fake_datetime()},
                {"Key": "policies/two.md", "Size": 200, "LastModified": _fake_datetime()},
            ]
        }
        with patch.object(list_exports, "s3_client", s3):
            result = list_exports.handler({"action": "list"})
        cov = _cov(result)
        self.assertEqual(cov["state"], "checked")
        self.assertEqual(cov["count"], 2)

    def test_list_empty_bucket_is_empty(self):
        s3 = MagicMock(name="lambda_role_s3")
        s3.list_objects_v2.return_value = {"Contents": []}
        with patch.object(list_exports, "s3_client", s3):
            result = list_exports.handler({"action": "list"})
        cov = _cov(result)
        self.assertEqual(cov["state"], "empty")
        self.assertEqual(cov["count"], 0)

    def test_get_link_success_is_checked(self):
        s3 = MagicMock(name="lambda_role_s3")
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {"Contents": [{"Key": "policies/report-1.md"}]}
        ]
        s3.get_paginator.return_value = paginator
        with patch.object(list_exports, "s3_client", s3):
            result = list_exports.handler(
                {"action": "get_link", "filename": "report-1.md"}
            )
        cov = _cov(result)
        self.assertEqual(cov["state"], "checked")

    def test_get_link_not_found_is_empty(self):
        s3 = MagicMock(name="lambda_role_s3")
        paginator = MagicMock()
        paginator.paginate.return_value = [{"Contents": []}]
        s3.get_paginator.return_value = paginator
        with patch.object(list_exports, "s3_client", s3):
            result = list_exports.handler(
                {"action": "get_link", "filename": "missing.md"}
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
    from datetime import datetime, timezone
    return datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()
