"""Offline tests for the /downloads/{proxy+} API Gateway handler.

This is customer-facing code — path-traversal defenses and the prefix
allowlist are release-gating. Every "attacker-shaped" input must return
the uniform 404, not leak existence, and never touch the S3 client.
"""

import base64
import json
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ["REPORTS_BUCKET"] = "test-bucket"

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import download  # noqa: E402


# ---------- Fake S3 client + ClientError ---------------------------------


class FakeClientError(download.ClientError):
    """Minimal ClientError matching what botocore raises."""

    def __init__(self, code: str, message: str = ""):
        self.response = {"Error": {"Code": code, "Message": message or code}}
        self.operation_name = code
        super().__init__(self.response, self.operation_name)


class _BodyStream:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3Client:
    """In-memory S3. Records every call for later assertions.

    Objects are keyed by S3 key and store {ContentLength, ContentType,
    Body} — the fields download.py actually reads.
    """

    def __init__(self):
        self.objects: dict = {}
        self.head_calls: list = []
        self.get_calls: list = []
        # Optional overrides for raising errors from head_object /
        # get_object on a specific key.
        self.head_errors: dict = {}
        self.get_errors: dict = {}

    def put(self, key: str, body: bytes, content_type: str = "application/octet-stream"):
        self.objects[key] = {
            "Body": _BodyStream(body),
            "ContentLength": len(body),
            "ContentType": content_type,
        }

    def head_object(self, Bucket: str, Key: str):
        self.head_calls.append({"Bucket": Bucket, "Key": Key})
        if Key in self.head_errors:
            raise self.head_errors[Key]
        obj = self.objects.get(Key)
        if obj is None:
            raise FakeClientError("NoSuchKey", "The specified key does not exist.")
        return {
            "ContentLength": obj["ContentLength"],
            "ContentType": obj["ContentType"],
        }

    def get_object(self, Bucket: str, Key: str):
        self.get_calls.append({"Bucket": Bucket, "Key": Key})
        if Key in self.get_errors:
            raise self.get_errors[Key]
        obj = self.objects.get(Key)
        if obj is None:
            raise FakeClientError("NoSuchKey", "The specified key does not exist.")
        # Rewrap body so consecutive reads work independently.
        return {
            "Body": _BodyStream(obj["Body"].read()),
            "ContentLength": obj["ContentLength"],
            "ContentType": obj["ContentType"],
        }


# ---------- Test helpers -------------------------------------------------


def _event(proxy: str | None, email: str = "customer@example.com") -> dict:
    """Build a minimal API GW proxy event with a Cognito authorizer claim."""
    return {
        "httpMethod": "GET",
        "pathParameters": {"proxy": proxy} if proxy is not None else None,
        "requestContext": {
            "authorizer": {"claims": {"email": email, "sub": "abc-123"}},
        },
    }


class _DownloadTestBase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeS3Client()
        self._orig_client = download.s3_client
        download.s3_client = self.fake

    def tearDown(self):
        download.s3_client = self._orig_client


# ---------- Happy path ---------------------------------------------------


class HappyPathTest(_DownloadTestBase):
    def test_serves_a_policy_json_as_base64(self):
        body = b'{"Version":"2012-10-17","Statement":[]}'
        self.fake.put(
            "policies/policy-ApolloRole-2026-09-23_004422.json",
            body,
            content_type="application/json",
        )
        result = download.handler(
            _event("policies/policy-ApolloRole-2026-09-23_004422.json"), None
        )

        self.assertEqual(result["statusCode"], 200)
        body_json = json.loads(result["body"])
        self.assertEqual(body_json["filename"], "policy-ApolloRole-2026-09-23_004422.json")
        self.assertEqual(body_json["content_type"], "application/json")
        self.assertEqual(body_json["size_bytes"], len(body))
        self.assertEqual(base64.b64decode(body_json["content_b64"]), body)
        self.assertEqual(body_json["s3_key"], "policies/policy-ApolloRole-2026-09-23_004422.json")
        # CORS headers on 200.
        self.assertIn("Access-Control-Allow-Origin", result["headers"])

    def test_serves_a_markdown_report(self):
        body = b"# Findings\n\n- HIGH: cross-account access\n"
        self.fake.put("reports/report-2026-09-23_002313.md", body, content_type="text/markdown")
        result = download.handler(_event("reports/report-2026-09-23_002313.md"), None)

        self.assertEqual(result["statusCode"], 200)
        body_json = json.loads(result["body"])
        self.assertEqual(body_json["content_type"], "text/markdown")
        self.assertEqual(base64.b64decode(body_json["content_b64"]), body)


# ---------- Path-validation blocks ---------------------------------------


class PathValidationTest(_DownloadTestBase):
    def _assert_uniform_404(self, event, msg=""):
        result = download.handler(event, None)
        self.assertEqual(result["statusCode"], 404, msg=msg)
        self.assertEqual(json.loads(result["body"]), {"error": "Not found."}, msg=msg)
        # CORS headers on 4xx too.
        self.assertIn("Access-Control-Allow-Origin", result["headers"], msg=msg)
        # And crucially: S3 was NEVER touched.
        self.assertEqual(self.fake.head_calls, [], msg=f"{msg}: head_object was called")
        self.assertEqual(self.fake.get_calls, [], msg=f"{msg}: get_object was called")

    def test_missing_proxy_path(self):
        self._assert_uniform_404(_event(None), msg="no proxy at all")
        self._assert_uniform_404(_event(""), msg="empty proxy")

    def test_disallowed_prefix(self):
        for key in (
            "secrets/foo.json",
            "customer-tokens/bar.txt",
            "billing/invoice.pdf",
            "aws-config-history/2026/09/23/config.json",
        ):
            self._assert_uniform_404(_event(key), msg=f"prefix {key!r}")

    def test_case_sensitive_prefix(self):
        # Prefix allowlist is case-sensitive; "Policies" != "policies".
        self._assert_uniform_404(_event("Policies/foo.json"), msg="Policies (capital)")
        self._assert_uniform_404(_event("REPORTS/foo.md"), msg="REPORTS (uppercase)")

    def test_path_traversal(self):
        for key in (
            "policies/../secrets/prod-keys.txt",
            "reports/../../etc/passwd",
            "policies/..",
            "..",
            "policies/foo/../../../etc/hosts",
        ):
            self._assert_uniform_404(_event(key), msg=f"traversal {key!r}")

    def test_absolute_path(self):
        self._assert_uniform_404(_event("/policies/foo.json"), msg="leading /")
        self._assert_uniform_404(_event("/etc/passwd"), msg="/etc/passwd")

    def test_double_slash(self):
        self._assert_uniform_404(_event("policies//foo.json"))

    def test_forbidden_characters(self):
        for key in (
            "policies/foo\r\ninjected.json",
            "policies/foo\x00bar.json",
            "policies/foo\\bar.json",
            # Percent-encoded percent-sign (double-encoding) — even if
            # urllib.parse.unquote silently decodes some of it, a residual
            # % after decode is a signal that someone is trying to smuggle
            # something through.
            "policies/foo%2E%2E.json",
        ):
            self._assert_uniform_404(_event(key), msg=f"forbidden char in {key!r}")

    def test_missing_basename(self):
        self._assert_uniform_404(_event("policies/"), msg="trailing slash, no basename")

    def test_prefix_only_is_not_an_object(self):
        self._assert_uniform_404(_event("policies"), msg="prefix without /basename")

    def test_key_too_long(self):
        key = "policies/" + ("a" * 1024) + ".json"  # ~1032 chars, over the 512 cap
        self._assert_uniform_404(_event(key), msg="key over max length")


# ---------- Size cap -----------------------------------------------------


class SizeCapTest(_DownloadTestBase):
    def test_oversize_object_returns_413_with_cli_fallback(self):
        # Object smaller than actual — we mock ContentLength to exceed the
        # cap without actually allocating 6 MB of test data.
        self.fake.put("reports/big-report.md", b"placeholder")
        self.fake.objects["reports/big-report.md"]["ContentLength"] = (
            download._MAX_OBJECT_BYTES + 1
        )

        result = download.handler(_event("reports/big-report.md"), None)
        self.assertEqual(result["statusCode"], 413)
        body = json.loads(result["body"])
        self.assertIn("aws s3 cp", body["error"])
        self.assertIn("s3://test-bucket/reports/big-report.md", body["error"])
        # get_object must NOT have run — we caught the size at head time.
        self.assertEqual(self.fake.get_calls, [])

    def test_size_at_cap_is_allowed(self):
        # Exactly at the cap: allowed (the cap already has headroom).
        body = b"x" * download._MAX_OBJECT_BYTES
        self.fake.put("reports/edge.md", body, content_type="text/markdown")
        result = download.handler(_event("reports/edge.md"), None)
        self.assertEqual(result["statusCode"], 200)


# ---------- Error paths --------------------------------------------------


class ErrorPathTest(_DownloadTestBase):
    def test_missing_object_returns_uniform_404(self):
        # Valid path, but the object doesn't exist. Must return the SAME
        # 404 shape as a validation failure — no info leak.
        result = download.handler(_event("policies/does-not-exist.json"), None)
        self.assertEqual(result["statusCode"], 404)
        self.assertEqual(json.loads(result["body"]), {"error": "Not found."})

    def test_s3_client_error_returns_500(self):
        # Non-404 S3 error (permission denied, throttle, etc.) — 500, not
        # 404, so the caller can distinguish "temporary problem" from "no
        # such file".
        self.fake.head_errors["policies/x.json"] = FakeClientError(
            "AccessDenied", "You don't have permission."
        )
        result = download.handler(_event("policies/x.json"), None)
        self.assertEqual(result["statusCode"], 500)
        self.assertIn("Failed to look up", json.loads(result["body"])["error"])

    def test_get_object_race_returns_404(self):
        # head_object succeeded, but the object was deleted before we
        # could read the body. Return 404, not 500.
        body = b"content"
        self.fake.put("policies/race.json", body, content_type="application/json")
        self.fake.get_errors["policies/race.json"] = FakeClientError(
            "NoSuchKey", "deleted between head and get"
        )
        result = download.handler(_event("policies/race.json"), None)
        self.assertEqual(result["statusCode"], 404)

    def test_get_object_5xx_returns_500(self):
        body = b"content"
        self.fake.put("policies/broken.json", body, content_type="application/json")
        self.fake.get_errors["policies/broken.json"] = FakeClientError(
            "InternalError", "S3 hiccup"
        )
        result = download.handler(_event("policies/broken.json"), None)
        self.assertEqual(result["statusCode"], 500)

    def test_missing_bucket_env_returns_500(self):
        orig = download.REPORTS_BUCKET
        download.REPORTS_BUCKET = ""
        try:
            result = download.handler(_event("policies/x.json"), None)
            self.assertEqual(result["statusCode"], 500)
        finally:
            download.REPORTS_BUCKET = orig


# ---------- Content-type preservation ------------------------------------


class ContentTypeTest(_DownloadTestBase):
    def test_content_type_from_s3_metadata_is_returned(self):
        for ct in ("application/json", "text/markdown", "text/plain", "application/octet-stream"):
            key = f"reports/x-{ct.replace('/', '-')}.bin"
            self.fake.put(key, b"data", content_type=ct)
            result = download.handler(_event(key), None)
            self.assertEqual(result["statusCode"], 200)
            self.assertEqual(json.loads(result["body"])["content_type"], ct)

    def test_missing_content_type_defaults_to_octet_stream(self):
        self.fake.put("reports/nofmt.bin", b"data")
        self.fake.objects["reports/nofmt.bin"]["ContentType"] = None
        result = download.handler(_event("reports/nofmt.bin"), None)
        body = json.loads(result["body"])
        self.assertEqual(body["content_type"], "application/octet-stream")


# ---------- CloudWatch logging -------------------------------------------
#
# Not verifying log message content (that's fragile) — just ensuring the
# handler paths don't crash when auth claims are missing. In production,
# the Cognito authorizer will always populate claims, but a
# misconfiguration shouldn't 500 the download endpoint.


class CallerIdentityTest(_DownloadTestBase):
    def test_missing_claims_still_serves(self):
        event = {
            "httpMethod": "GET",
            "pathParameters": {"proxy": "policies/foo.json"},
            "requestContext": {},  # no authorizer at all
        }
        self.fake.put("policies/foo.json", b"body", content_type="application/json")
        result = download.handler(event, None)
        # The API GW Cognito authorizer will reject unauthenticated calls
        # before they reach the Lambda, but if it doesn't, we still serve.
        self.assertEqual(result["statusCode"], 200)


if __name__ == "__main__":
    unittest.main()
