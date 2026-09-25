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

    def _tour_event(self, message, history):
        return {
            "httpMethod": "POST",
            "body": json.dumps({"message": message, "history": history, "mode": "guided"}),
        }

    def test_bare_ready_after_tour_step8_announcement_still_short_circuits(self):
        """The GUIDED TOUR's Step 8 says 'pull everything we've found into
        a prioritized backlog' — 'backlog' alone doesn't match
        _ACTION_PLAN_INTENT (wants 'action plan' or 'remediation backlog'),
        so a bare 'ready' after that announcement must still short-circuit
        via the fallback check, not fall through to a full Bedrock round
        trip on the tour's own closing step."""
        history = [
            {"role": "user", "content": "ready"},
            {
                "role": "assistant",
                "content": (
                    "Reviewed 7 access keys, 2 Critical. Now let's pull everything "
                    "we've found into a prioritized backlog — what to fix first, "
                    "what's a quick win. Ready?"
                ),
            },
        ]
        with patch.object(agent, "invoke_tool", return_value=_sample_action_plan()) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(self._tour_event("ready", history), None)

        converse.assert_not_called()
        invoke.assert_called_once()
        tool_name, _ = invoke.call_args.args
        self.assertEqual(tool_name, "generate_action_plan")
        self.assertEqual(response["statusCode"], 200)

    def test_exact_ready_for_the_next_step_short_circuits(self):
        """Same confirmed root cause as Step 6's pinned test in
        test_triage_shortcircuit.py: a live tour re-test's fixed advance
        string was the literal "ready for the next step", which the
        original _BARE_AFFIRMATIVE pattern did not match at all."""
        history = [
            {
                "role": "assistant",
                "content": (
                    "Reviewed 7 access keys, 2 Critical. Now let's pull everything "
                    "we've found into a prioritized backlog — what to fix first, "
                    "what's a quick win. Ready for the next step?"
                ),
            },
        ]
        with patch.object(agent, "invoke_tool", return_value=_sample_action_plan()) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(
                self._tour_event("ready for the next step", history), None
            )

        converse.assert_not_called()
        invoke.assert_called_once()
        self.assertEqual(response["statusCode"], 200)

    def test_ready_after_delivered_plan_does_not_re_run_it(self):
        """Pins the Step 8 self-close gap Quick's clean-paced tour walk
        surfaced: after the action plan is delivered (the prior assistant
        turn carries _ACTION_PLAN_FOOTER_MARKER, "Say `export that`..."),
        a trailing "ready for the next step" must NOT re-run
        generate_action_plan — the tour is over, this is a stray
        continuation attempt, not a new request for the same step. Falls
        through to Bedrock, which the new TOUR COMPLETION prompt rule
        instructs to close out gracefully instead of re-running the tool."""
        delivered_plan = (
            "Prioritized action plan — showing top 10 of 27 findings.\n\n"
            "| # | Action | Role | Priority score |\n|---|---|---|---|\n"
            "| 1 | Delete unused role | demo-orphan-role | 90 |\n\n"
            "---\n"
            "Say `export that` to save this plan to S3, or ask for details on a specific role."
        )
        history = [{"role": "assistant", "content": delivered_plan}]
        fake_response = {
            "output": {"message": {"content": [{"text": "That completes the tour!"}]}},
            "usage": {"inputTokens": 5, "outputTokens": 6},
        }
        with patch.object(agent, "invoke_tool") as invoke, \
                patch.object(
                    agent, "converse_with_tools",
                    return_value=(fake_response, [], None, []),
                ) as converse:
            agent.handler(self._tour_event("ready for the next step", history), None)

        invoke.assert_not_called()
        converse.assert_called_once()

    def test_bare_ready_after_unrelated_announcement_does_not_short_circuit(self):
        history = [
            {"role": "assistant", "content": "Want me to check the blast radius next? Ready when you are."},
        ]
        fake_response = {
            "output": {"message": {"content": [{"text": "Checking..."}]}},
            "usage": {"inputTokens": 5, "outputTokens": 6},
        }
        with patch.object(agent, "invoke_tool") as invoke, \
                patch.object(
                    agent, "converse_with_tools",
                    return_value=(fake_response, [], None, []),
                ) as converse:
            agent.handler(self._tour_event("ready", history), None)

        invoke.assert_not_called()
        converse.assert_called_once()

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
