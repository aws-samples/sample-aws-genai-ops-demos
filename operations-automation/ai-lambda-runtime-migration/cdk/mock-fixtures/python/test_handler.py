"""Tests for the partner webhook receiver.

Written in the unittest style of the python3.9 era, ON PURPOSE: the
assertEquals alias was removed in Python 3.12, so AWS Transform has to upgrade
the tests as well as the code. Run with `python -m pytest`.
"""

import json
import unittest

from lambda_function import client_supported, handler, load_settings, parse_content_type


def _event(content_type="application/json", version="2.10.1", body=None):
    return {
        "headers": {"Content-Type": content_type, "X-Client-Version": version},
        "body": json.dumps(body if body is not None else {"orderId": "ORD-1001"}),
    }


class WebhookTest(unittest.TestCase):
    def test_settings_are_read(self):
        settings = load_settings()
        self.assertEquals(settings["min_client_version"], "2.4.0")
        self.assertEquals(settings["plugin"], "enrich_plugin.py")

    def test_content_type_with_charset(self):
        self.assertEquals(parse_content_type("application/json; charset=latin-1"),
                          ("application/json", "latin-1"))

    def test_version_comparison_is_numeric(self):
        # 2.10.1 is newer than 2.4.0: a plain string comparison would get this wrong.
        self.assertTrue(client_supported("2.10.1", "2.4.0"))
        self.assertFalse(client_supported("2.3.9", "2.4.0"))

    def test_accepts_and_enriches_a_valid_event(self):
        result = handler(_event(), None)
        self.assertEquals(result["statusCode"], 200)
        self.assertEquals(json.loads(result["body"])["accepted"],
                          {"orderId": "ORD-1001", "source": "partner-webhook"})

    def test_rejects_a_non_json_payload(self):
        self.assertEquals(handler(_event(content_type="text/plain"), None)["statusCode"], 415)

    def test_rejects_an_old_client(self):
        self.assertEquals(handler(_event(version="2.3.9"), None)["statusCode"], 426)
