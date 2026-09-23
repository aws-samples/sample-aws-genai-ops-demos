"""Regression tests pinning the export_report / list_exports response shape.

The tools no longer sign S3 presigned URLs (see src/download.py — the
Cognito-authed /downloads/{proxy+} API GW route replaced them after
boto3 1.42.97 began producing role-chained STS-signed URLs that S3
rejected as InvalidToken). These tests pin the invariants of the new
design:

  1. put_object runs on the module-level s3_client (the Lambda role) —
     no assume_role, no dedicated presigner role.
  2. download_url in the response points at the API GW /downloads/ path
     when API_ENDPOINT is configured.
  3. When API_ENDPOINT is missing, download_url is empty — the customer
     sees the s3_path and can retrieve via CLI. No fabricated URL.
  4. valid_for is NOT present in the response — session-lifetime auth,
     not a fixed TTL.
"""

import base64
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


_TEST_API_ENDPOINT = "https://api.example.com/prod/"


class ExportReportShapeTest(unittest.TestCase):
    """Pin export_report's new response shape and behavior."""

    def setUp(self):
        self._patches = [
            patch.object(export_report, "REPORTS_BUCKET", "test-bucket"),
            patch.object(export_report, "API_ENDPOINT", _TEST_API_ENDPOINT),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_upload_runs_on_module_s3_client(self):
        s3 = MagicMock(name="lambda_role_s3")
        with patch.object(export_report, "s3_client", s3):
            result = export_report.handler(
                {"content": "# action plan", "content_type": "action_plan"}
            )
        self.assertTrue(result.get("success"), msg=f"handler failed: {result}")
        s3.put_object.assert_called_once()

    def test_no_assume_role_or_presigning(self):
        """The tool must never call sts.assume_role or generate_presigned_url
        — the API GW route handles auth and content serving.
        """
        s3 = MagicMock(name="lambda_role_s3")
        with patch.object(export_report, "s3_client", s3), \
                patch.object(export_report, "boto3") as boto3_mod:
            export_report.handler(
                {"content": "# action plan", "content_type": "action_plan"}
            )
        # boto3.client should NOT have been called at all — the module-level
        # s3_client is what gets used, and no STS client is ever built.
        boto3_mod.client.assert_not_called()
        # And the S3 client itself never runs generate_presigned_url.
        s3.generate_presigned_url.assert_not_called()

    def test_download_url_points_at_api_gw(self):
        s3 = MagicMock(name="lambda_role_s3")
        with patch.object(export_report, "s3_client", s3):
            result = export_report.handler(
                {
                    "content": '{"Version": "2012-10-17", "Statement": []}',
                    "content_type": "policy",
                    "role_name": "ApolloRole",
                }
            )
        self.assertTrue(result["success"])
        url = result["download_url"]
        # URL host is our API GW endpoint, NOT S3.
        self.assertTrue(url.startswith(_TEST_API_ENDPOINT + "downloads/"), msg=url)
        # The S3 key encoded in the URL matches the s3_path.
        self.assertIn("policies/policy-ApolloRole-", url)

    def test_download_url_empty_when_api_endpoint_missing(self):
        """If CDK wiring hasn't set API_ENDPOINT yet, don't fabricate a URL."""
        s3 = MagicMock(name="lambda_role_s3")
        with patch.object(export_report, "API_ENDPOINT", ""), \
                patch.object(export_report, "s3_client", s3):
            result = export_report.handler(
                {"content": "# report", "content_type": "report"}
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["download_url"], "")
        # But the s3_path is still populated so a customer can retrieve
        # via aws s3 cp.
        self.assertTrue(result["s3_path"].startswith("s3://test-bucket/"))

    def test_response_shape_omits_valid_for(self):
        s3 = MagicMock(name="lambda_role_s3")
        with patch.object(export_report, "s3_client", s3):
            result = export_report.handler(
                {"content": "# plan", "content_type": "action_plan"}
            )
        # valid_for / expires_in / expires_at — none should be present.
        # The URL is session-lifetime; promising a fixed TTL is dishonest.
        self.assertNotIn("valid_for", result)
        self.assertNotIn("expires_in", result)
        self.assertNotIn("expires_at", result)


class ListExportsShapeTest(unittest.TestCase):
    """Pin list_exports's new response shape and behavior."""

    def setUp(self):
        self._patches = [
            patch.object(list_exports, "REPORTS_BUCKET", "test-bucket"),
            patch.object(list_exports, "API_ENDPOINT", _TEST_API_ENDPOINT),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_get_link_returns_api_gw_url(self):
        s3 = MagicMock(name="lambda_role_s3")
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {"Contents": [{"Key": "action-plans/plan-1.md"}]}
        ]
        s3.get_paginator.return_value = paginator
        with patch.object(list_exports, "s3_client", s3):
            result = list_exports.handler(
                {"action": "get_link", "filename": "plan-1.md"}
            )

        self.assertNotIn("error", result, msg=result)
        url = result["download_url"]
        self.assertTrue(
            url.startswith(_TEST_API_ENDPOINT + "downloads/action-plans/plan-1.md"),
            msg=url,
        )
        # No presign anywhere.
        s3.generate_presigned_url.assert_not_called()
        # valid_for dropped.
        self.assertNotIn("valid_for", result)

    def test_list_action_no_urls_no_presign(self):
        """The `list` action returns filename metadata only — no URLs."""
        s3 = MagicMock(name="lambda_role_s3")
        s3.list_objects_v2.return_value = {
            "Contents": [
                {
                    "Key": "policies/policy-a-2026-09-23_000000.json",
                    "Size": 1024,
                    "LastModified": _dt("2026-09-23T00:00:00+00:00"),
                }
            ]
        }
        with patch.object(list_exports, "s3_client", s3):
            result = list_exports.handler({"action": "list"})

        self.assertEqual(result["total_count"], 1)
        # No download URLs in the list response.
        for entry in result["files"]:
            self.assertNotIn("download_url", entry)
        # No presign call anywhere on the S3 client.
        s3.generate_presigned_url.assert_not_called()

    def test_get_link_missing_filename_returns_error(self):
        with patch.object(list_exports, "s3_client", MagicMock()):
            result = list_exports.handler({"action": "get_link"})
        self.assertIn("error", result)
        self.assertIn("filename is required", result["error"])


def _dt(iso: str):
    """Build a datetime for LastModified. datetime.fromisoformat is fine here."""
    from datetime import datetime
    return datetime.fromisoformat(iso)


if __name__ == "__main__":
    unittest.main()
