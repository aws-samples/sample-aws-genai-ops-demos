"""Offline tests for the generate_action_plan short-circuit."""

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


def _sample_action_plan():
    return {
        "action_plan": [
            {
                "priority": 1,
                "action": "Delete unused role",
                "role_name": "test-role-a",
                "severity": "MEDIUM",
                "priority_score": 65,
                "effort": "trivial",
                "risk_if_ignored": "Attack surface",
                "rationale": "unused",
            },
            {
                "priority": 2,
                "action": "Remove unused IAM entity",
                "role_name": "test-role-b",
                "severity": "MEDIUM",
                "priority_score": 60,
                "effort": "trivial",
                "risk_if_ignored": "Attack surface",
                "rationale": "unused",
            },
        ],
        "total_items_analyzed": 12,
        "showing": 2,
        "summary": {
            "total_findings": 12,
            "quick_wins_count": 8,
            "high_priority_count": 2,
            "estimated_total_time_minutes": 60,
            "estimated_total_time_human": "1 hour",
            "focus_area": "all",
        },
        "quick_wins": [
            {"action": "Delete unused role", "role_name": "test-role-a", "why": "quick"},
        ],
        "risk_distribution": {"medium": 12},
    }


class ActionPlanIntentTest(unittest.TestCase):
    def test_matches_common_phrasings(self):
        self.assertIsNotNone(agent._ACTION_PLAN_INTENT.search("generate an action plan"))
        self.assertIsNotNone(agent._ACTION_PLAN_INTENT.search("Draft an IAM action plan"))
        self.assertIsNotNone(
            agent._ACTION_PLAN_INTENT.search("Give me a prioritized action plan for my findings")
        )
        self.assertIsNotNone(
            agent._ACTION_PLAN_INTENT.search("Create the remediation backlog")
        )
        self.assertIsNotNone(
            agent._ACTION_PLAN_INTENT.search("show me an action plan for findings")
        )

    def test_rejects_unrelated_prompts(self):
        self.assertIsNone(agent._ACTION_PLAN_INTENT.search("show my active findings"))
        self.assertIsNone(agent._ACTION_PLAN_INTENT.search("compare roles a, b, c"))
        self.assertIsNone(agent._ACTION_PLAN_INTENT.search("what is blast radius"))


class HandlerActionPlanShortCircuitTest(unittest.TestCase):
    def _event(self, message):
        return {
            "httpMethod": "POST",
            "body": json.dumps(
                {"message": message, "history": [], "mode": "guided"}
            ),
        }

    def test_short_circuit_returns_rendered_plan_without_bedrock(self):
        with patch.object(agent, "invoke_tool", return_value=_sample_action_plan()) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(self._event("generate an action plan"), None)

        converse.assert_not_called()
        invoke.assert_called_once()
        tool_name, tool_input = invoke.call_args.args
        self.assertEqual(tool_name, "generate_action_plan")
        self.assertEqual(tool_input.get("max_items"), 50)

        body = json.loads(response["body"])
        self.assertEqual(body["usage"], {"inputTokens": 0, "outputTokens": 0})
        self.assertEqual(len(body["tools_used"]), 1)
        self.assertIsNone(body["pagination"])
        self.assertIn("Prioritized action plan", body["response"])
        self.assertIn("test-role-a", body["response"])
        self.assertIn("test-role-b", body["response"])

    def test_falls_through_to_bedrock_on_unrelated_prompt(self):
        fake_bedrock = {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "usage": {"inputTokens": 1, "outputTokens": 2},
        }
        with patch.object(agent, "invoke_tool") as invoke, \
                patch.object(agent, "converse_with_tools", return_value=(fake_bedrock, [], None, [])) as converse:
            agent.handler(self._event("show my active findings"), None)

        invoke.assert_not_called()
        converse.assert_called_once()

    def test_short_circuit_reports_tool_error(self):
        with patch.object(agent, "invoke_tool", return_value={"error": "boom"}), \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(self._event("draft an action plan"), None)

        converse.assert_not_called()
        body = json.loads(response["body"])
        self.assertIn("couldn't generate", body["response"].lower())


class ActionPlanRenderingTest(unittest.TestCase):
    def test_zero_findings_message(self):
        rendered = agent._render_action_plan(
            {
                "action_plan": [],
                "summary": {"total_findings": 0, "message": "No active IAM findings — clean!"},
                "quick_wins": [],
                "risk_distribution": {},
            }
        )
        self.assertIn("No active IAM findings", rendered)

    def test_pipe_characters_are_escaped(self):
        result = _sample_action_plan()
        result["action_plan"][0]["role_name"] = "role|with|pipes"
        rendered = agent._render_action_plan(result)
        self.assertNotIn("role|with|pipes |", rendered)
        self.assertIn("role\\|with\\|pipes", rendered)


if __name__ == "__main__":
    unittest.main()
