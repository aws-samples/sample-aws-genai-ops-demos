"""Offline tests for the standard assistant's pagination short-circuit."""

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


def _finding(identifier, severity="HIGH"):
    return {
        "id": f"finding-{identifier}",
        "title": f"Finding {identifier}",
        "severity": severity,
        "resource_id": f"role/test-{identifier}",
        "resource_type": "AwsIamRole",
        "status": "NEW",
    }


class PaginationIntentTest(unittest.TestCase):
    def test_matches_common_intents(self):
        self.assertIsNotNone(agent._pagination_intent("next"))
        self.assertIsNotNone(agent._pagination_intent("more"))
        self.assertIsNotNone(agent._pagination_intent("continue"))
        self.assertIsNotNone(agent._pagination_intent("keep going"))
        self.assertIsNotNone(agent._pagination_intent("page 2"))

    def test_captures_explicit_limit(self):
        self.assertEqual(agent._pagination_intent("next 20"), {"limit": 20})
        self.assertEqual(agent._pagination_intent("show 15 more"), {"limit": 15})
        self.assertEqual(agent._pagination_intent("show me another 25"), {"limit": 25})
        self.assertEqual(agent._pagination_intent("next 10 findings"), {"limit": 10})

    def test_rejects_unrelated_text(self):
        self.assertIsNone(agent._pagination_intent("show critical findings"))
        self.assertIsNone(agent._pagination_intent("hello"))
        self.assertIsNone(agent._pagination_intent(""))
        self.assertIsNone(agent._pagination_intent("what is IAM"))


class HandlerShortCircuitTest(unittest.TestCase):
    def _handler_event(self, message, pagination):
        return {
            "httpMethod": "POST",
            "body": json.dumps(
                {
                    "message": message,
                    "history": [],
                    "mode": "guided",
                    "pagination": pagination,
                }
            ),
        }

    def test_short_circuit_returns_table_without_bedrock(self):
        pagination = {
            "tool": "list_findings",
            "next_token": "cursor-1",
            "has_more": True,
            "last_input": {"limit": 20, "severity": "HIGH", "status": "ACTIVE"},
        }
        expected_result = {
            "findings": [_finding("first"), _finding("second")],
            "returned_count": 2,
            "total_matching": 40,
            "total_count": 40,
            "next_token": "cursor-2",
            "has_more": True,
        }

        with patch.object(agent, "invoke_tool", return_value=expected_result) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(self._handler_event("next 20", pagination), None)

        converse.assert_not_called()
        invoke.assert_called_once()
        tool_name, tool_input = invoke.call_args.args
        self.assertEqual(tool_name, "list_findings")
        self.assertEqual(tool_input["next_token"], "cursor-1")
        self.assertEqual(tool_input["limit"], 20)
        self.assertEqual(tool_input["severity"], "HIGH")
        self.assertEqual(tool_input["status"], "ACTIVE")

        body = json.loads(response["body"])
        self.assertIn("Finding first", body["response"])
        self.assertIn("40 total", body["response"])
        self.assertEqual(body["usage"], {"inputTokens": 0, "outputTokens": 0})
        self.assertEqual(body["pagination"]["next_token"], "cursor-2")

    def test_short_circuit_respects_explicit_intent_limit_over_prior_limit(self):
        pagination = {
            "tool": "list_findings",
            "next_token": "cursor-1",
            "has_more": True,
            "last_input": {"limit": 50, "status": "ACTIVE"},
        }
        with patch.object(agent, "invoke_tool", return_value={"findings": [], "next_token": "", "has_more": False, "total_matching": 0}) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            agent.handler(self._handler_event("next 5", pagination), None)

        converse.assert_not_called()
        _, tool_input = invoke.call_args.args
        self.assertEqual(tool_input["limit"], 5)

    def test_no_short_circuit_when_intent_missing(self):
        pagination = {
            "tool": "list_findings",
            "next_token": "cursor-1",
            "has_more": True,
            "last_input": {"limit": 20},
        }
        fake_response = {
            "output": {"message": {"content": [{"text": "answer"}]}},
            "usage": {"inputTokens": 3, "outputTokens": 4},
        }
        with patch.object(agent, "invoke_tool") as invoke, \
                patch.object(agent, "converse_with_tools", return_value=(fake_response, [], None, [])) as converse:
            agent.handler(self._handler_event("show critical findings", pagination), None)

        invoke.assert_not_called()
        converse.assert_called_once()

    def test_no_short_circuit_without_pagination_context(self):
        fake_response = {
            "output": {"message": {"content": [{"text": "answer"}]}},
            "usage": {"inputTokens": 1, "outputTokens": 2},
        }
        with patch.object(agent, "invoke_tool") as invoke, \
                patch.object(agent, "converse_with_tools", return_value=(fake_response, [], None, [])) as converse:
            agent.handler(self._handler_event("next 20", None), None)

        invoke.assert_not_called()
        converse.assert_called_once()

    def test_short_circuit_reports_tool_error_gracefully(self):
        pagination = {
            "tool": "list_findings",
            "next_token": "cursor-1",
            "has_more": True,
            "last_input": {"limit": 10},
        }
        with patch.object(agent, "invoke_tool", return_value={"error": "boom"}), \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(self._handler_event("next", pagination), None)

        converse.assert_not_called()
        body = json.loads(response["body"])
        self.assertIn("couldn't fetch", body["response"].lower())
        self.assertIsNone(body["pagination"])


class PaginationExtractionTest(unittest.TestCase):
    def test_returns_context_when_has_more(self):
        context = agent._extract_pagination_from_findings(
            "list_findings",
            {"status": "ACTIVE", "limit": 10, "severity": "HIGH"},
            {"next_token": "cursor", "has_more": True, "findings": [_finding("only")]},
        )
        self.assertEqual(context["tool"], "list_findings")
        self.assertEqual(context["next_token"], "cursor")
        self.assertTrue(context["has_more"])
        self.assertEqual(
            context["last_input"], {"status": "ACTIVE", "limit": 10, "severity": "HIGH"}
        )

    def test_drops_unrecognized_fields(self):
        context = agent._extract_pagination_from_findings(
            "list_findings",
            {"status": "ACTIVE", "next_token": "prior", "bogus": "value"},
            {"next_token": "cursor", "has_more": True},
        )
        self.assertNotIn("bogus", context["last_input"])
        self.assertNotIn("next_token", context["last_input"])

    def test_returns_none_for_terminal_page(self):
        self.assertIsNone(
            agent._extract_pagination_from_findings(
                "list_findings",
                {"status": "ACTIVE"},
                {"findings": [], "has_more": False},
            )
        )

    def test_returns_none_for_error_result(self):
        self.assertIsNone(
            agent._extract_pagination_from_findings(
                "list_findings",
                {"status": "ACTIVE"},
                {"error": "boom"},
            )
        )


if __name__ == "__main__":
    unittest.main()
