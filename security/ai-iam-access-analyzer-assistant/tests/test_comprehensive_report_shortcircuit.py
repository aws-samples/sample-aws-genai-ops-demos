"""Tests for the comprehensive-report short-circuit (P1 + P3).

A "full report of all my findings" request previously went through the
model (told to call generate_action_plan then export), which free-wrote a
short summary instead of the full data, emitted a hallucinated
"${new Date().toISOString()}" line, and ran ~29s. The short-circuit runs
generate_action_plan(max_items=100) + export_report deterministically — no
Bedrock — rendering the real structured output.

Pins the intent regex (incl. the negatives that must stay the fast
interactive batch), the max_items=100 call, the deterministic render (no
model, no template-literal), and the zero-Bedrock handler path.
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


def _sample_plan_result(total=42):
    return {
        "action_plan": [
            {
                "rank": 1,
                "action": "Remove unused permissions",
                "role_name": "ApolloRole",
                "priority_score": 90,
                "severity": "HIGH",
            }
        ],
        "total_findings": total,
        "total_items_analyzed": 1,
        "quick_wins": [],
        "coverage": [{"source": "securityhub", "state": "checked", "detail": "x"}],
    }


class ComprehensiveReportIntentTest(unittest.TestCase):
    def test_matches_comprehensive_phrasings(self):
        for msg in (
            "I want a full report of all my findings exported to S3",
            "generate a comprehensive report",
            "give me a complete findings report",
            "report all findings",
            "export all my findings",
            "full audit report",
        ):
            self.assertIsNotNone(
                agent._COMPREHENSIVE_REPORT_INTENT.search(msg),
                msg=f"expected match: {msg!r}",
            )

    def test_does_not_match_normal_interactive_requests(self):
        """A normal "show me findings" must stay the fast interactive batch
        and NOT trigger a full 100-item report build."""
        for msg in (
            "show me my findings",
            "what are my active findings",
            "list findings",
            "show me critical findings",
            "show me the next 10 findings",
            "generate an action plan",
        ):
            self.assertIsNone(
                agent._COMPREHENSIVE_REPORT_INTENT.search(msg),
                msg=f"expected NO match: {msg!r}",
            )


class ComprehensiveReportHandlerTest(unittest.TestCase):
    def _event(self, message):
        return {
            "httpMethod": "POST",
            "body": json.dumps({"message": message, "history": [], "mode": "guided"}),
        }

    def test_bypasses_bedrock_calls_plan_100_and_exports(self):
        plan = _sample_plan_result(total=42)
        export = {
            "filename": "report-2026-10-10.md",
            "download_url": "https://api.example.com/prod/downloads/reports/report-2026-10-10.md",
        }

        def fake_invoke(name, inp):
            if name == "generate_action_plan":
                return plan
            if name == "export_report":
                return export
            raise AssertionError(f"unexpected tool {name}")

        with patch.object(agent, "invoke_tool", side_effect=fake_invoke) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(
                self._event("I want a full report of all my findings exported to S3"),
                None,
            )

        converse.assert_not_called()
        # generate_action_plan called with max_items=100.
        plan_calls = [c for c in invoke.call_args_list if c.args[0] == "generate_action_plan"]
        self.assertEqual(len(plan_calls), 1)
        self.assertEqual(plan_calls[0].args[1].get("max_items"), 100)
        # export_report called with content_type report.
        export_calls = [c for c in invoke.call_args_list if c.args[0] == "export_report"]
        self.assertEqual(len(export_calls), 1)
        self.assertEqual(export_calls[0].args[1]["content_type"], "report")

        body = json.loads(response["body"])
        self.assertEqual(body["usage"], {"inputTokens": 0, "outputTokens": 0})
        text = body["response"]
        # Comprehensive framing + real count, a clickable export link.
        self.assertIn("Comprehensive IAM findings report", text)
        self.assertIn("42", text)
        self.assertIn("[Download here]", text)
        # NO hallucinated template literal (P3).
        self.assertNotIn("toISOString", text)
        self.assertNotIn("${", text)

    def test_plan_tool_error_is_honest(self):
        with patch.object(agent, "invoke_tool", return_value={"error": "SecurityHub not enabled"}), \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(self._event("generate a comprehensive report"), None)
        converse.assert_not_called()
        body = json.loads(response["body"])
        self.assertIn("couldn't build the comprehensive report", body["response"])
        self.assertIn("SecurityHub not enabled", body["response"])

    def test_normal_findings_request_does_not_short_circuit(self):
        fake = {
            "output": {"message": {"content": [{"text": "..."}]}},
            "usage": {"inputTokens": 1, "outputTokens": 1},
        }
        with patch.object(agent, "invoke_tool") as invoke, \
                patch.object(agent, "converse_with_tools", return_value=(fake, [], None, [])) as converse:
            agent.handler(self._event("show me my findings"), None)
        converse.assert_called_once()
        for call in invoke.call_args_list:
            self.assertNotEqual(
                call.args[0] if call.args else None, "generate_action_plan"
            )


if __name__ == "__main__":
    unittest.main()
