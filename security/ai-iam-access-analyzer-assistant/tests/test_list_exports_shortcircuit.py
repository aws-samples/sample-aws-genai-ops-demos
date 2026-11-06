"""Tests for the list-exports / get-link short-circuit (P5).

The list_exports tool reads the WHOLE S3 reports bucket, but when "list my
exports" / "get me a link for X" went through the model, the model
sometimes answered from its own conversation memory — so a file exported
in an earlier conversation looked "missing" even though the S3 object
existed. The short-circuit calls list_exports directly, guaranteeing the
real bucket is always consulted.

Pins the two intent regexes (incl. the conservative negatives that must
fall through to the model), the filename extraction for get_link, and the
zero-Bedrock handler path for both actions.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agent  # noqa: E402


# --- Intent regexes -------------------------------------------------------


class ListExportsIntentTest(unittest.TestCase):
    def test_list_matches_common_phrasings(self):
        for msg in (
            "list my exports",
            "show me my exports",
            "what exports do i have",
            "show my saved reports",
            "list exported files",
            "which saved artifacts are there",
        ):
            self.assertIsNotNone(
                agent._LIST_EXPORTS_INTENT.search(msg), msg=f"expected match: {msg!r}"
            )

    def test_list_rejects_unrelated(self):
        for msg in (
            "list my findings",
            "show me critical findings",
            "export that",
            "audit my access keys",
            "generate an action plan",
        ):
            self.assertIsNone(
                agent._LIST_EXPORTS_INTENT.search(msg), msg=f"expected NO match: {msg!r}"
            )

    def test_get_link_extracts_filename(self):
        cases = {
            "get me a link for policy-ApolloRole-2026.json": "policy-ApolloRole-2026.json",
            "give me a link for change-request-2026-10-10.md": "change-request-2026-10-10.md",
            "regenerate a link for report-x.txt": "report-x.txt",
        }
        for msg, expected in cases.items():
            m = agent._GET_LINK_INTENT.search(msg)
            self.assertIsNotNone(m, msg=f"expected match: {msg!r}")
            self.assertEqual(expected, m.group(1))

    def test_get_link_falls_through_without_a_real_filename(self):
        """A vague link request with no concrete filename must NOT
        short-circuit — the model should ask which file / offer to create
        one, rather than the short-circuit guessing."""
        for msg in (
            "get me a link",
            "give me the download link for my findings report",
            "show me a link",
        ):
            self.assertIsNone(
                agent._GET_LINK_INTENT.search(msg), msg=f"expected NO match: {msg!r}"
            )


# --- Handler end-to-end ---------------------------------------------------


class ListExportsHandlerTest(unittest.TestCase):
    def _event(self, message):
        return {
            "httpMethod": "POST",
            "body": json.dumps({"message": message, "history": [], "mode": "guided"}),
        }

    def test_list_bypasses_bedrock_and_calls_tool(self):
        tool_result = {
            "files": [
                {
                    "filename": "policy-ApolloRole-2026.json",
                    "folder": "policies",
                    "last_modified": "2026-10-10 20:30 UTC",
                },
                {
                    "filename": "change-request-2026-10-10.md",
                    "folder": "change-requests",
                    "last_modified": "2026-10-10 20:46 UTC",
                },
            ],
            "total_count": 2,
            "coverage": [{"source": "s3", "state": "checked", "detail": "x"}],
        }
        with patch.object(agent, "invoke_tool", return_value=tool_result) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(self._event("list my exports"), None)

        converse.assert_not_called()
        invoke.assert_called_once()
        name, inp = invoke.call_args.args
        self.assertEqual(name, "list_exports")
        self.assertEqual(inp, {"action": "list"})

        body = json.loads(response["body"])
        self.assertEqual(200, response["statusCode"])
        self.assertEqual(body["usage"], {"inputTokens": 0, "outputTokens": 0})
        text = body["response"]
        self.assertIn("policy-ApolloRole-2026.json", text)
        self.assertIn("change-request-2026-10-10.md", text)
        # No download URLs in the list (by design).
        self.assertNotIn("http", text)
        self.assertIn("get me a link", text)

    def test_get_link_bypasses_bedrock_and_returns_clickable_link(self):
        tool_result = {
            "filename": "policy-ApolloRole-2026.json",
            "download_url": "https://api.example.com/prod/downloads/policies/policy-ApolloRole-2026.json",
            "coverage": [{"source": "s3", "state": "checked", "detail": "x"}],
        }
        with patch.object(agent, "invoke_tool", return_value=tool_result) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(
                self._event("get me a link for policy-ApolloRole-2026.json"), None
            )

        converse.assert_not_called()
        invoke.assert_called_once()
        name, inp = invoke.call_args.args
        self.assertEqual(name, "list_exports")
        self.assertEqual(inp, {"action": "get_link", "filename": "policy-ApolloRole-2026.json"})

        body = json.loads(response["body"])
        text = body["response"]
        # Clickable markdown link, not a raw URL.
        self.assertIn("[Download", text)
        self.assertIn("(https://api.example.com/prod/downloads/", text)
        # No TTL language.
        self.assertNotIn("expire", text.lower())

    def test_get_link_file_not_found_is_honest_no_fake_link(self):
        tool_result = {
            "error": "File 'nope.md' not found in exports bucket.",
            "coverage": [{"source": "s3", "state": "empty", "detail": "x"}],
        }
        with patch.object(agent, "invoke_tool", return_value=tool_result), \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(self._event("get me a link for nope.md"), None)

        converse.assert_not_called()
        body = json.loads(response["body"])
        text = body["response"]
        self.assertIn("couldn't find", text.lower())
        self.assertNotIn("http", text)  # no fabricated URL

    def test_empty_list_is_graceful(self):
        tool_result = {"files": [], "total_count": 0, "coverage": []}
        with patch.object(agent, "invoke_tool", return_value=tool_result), \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(self._event("show me my exports"), None)

        converse.assert_not_called()
        body = json.loads(response["body"])
        self.assertIn("No exported reports", body["response"])

    def test_unrelated_message_does_not_short_circuit(self):
        fake = {
            "output": {"message": {"content": [{"text": "..."}]}},
            "usage": {"inputTokens": 1, "outputTokens": 1},
        }
        with patch.object(agent, "invoke_tool") as invoke, \
                patch.object(agent, "converse_with_tools", return_value=(fake, [], None, [])) as converse:
            agent.handler(self._event("what are my active findings"), None)

        converse.assert_called_once()
        for call in invoke.call_args_list:
            self.assertNotEqual(call.args[0] if call.args else None, "list_exports")


if __name__ == "__main__":
    unittest.main()
