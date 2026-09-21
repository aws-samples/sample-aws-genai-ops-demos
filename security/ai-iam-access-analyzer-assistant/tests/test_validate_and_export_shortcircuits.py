"""Offline tests for the validate_policy and action_plan+export short-circuits."""

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


VALID_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:ListBucket"],
            "Resource": [
                "arn:aws:s3:::my-bucket",
                "arn:aws:s3:::my-bucket/*",
            ],
        }
    ],
}


def _event(message):
    return {
        "httpMethod": "POST",
        "body": json.dumps(
            {"message": message, "history": [], "mode": "guided"}
        ),
    }


class ValidatePolicyShortCircuitTest(unittest.TestCase):
    def test_extracts_fenced_json(self):
        prompt = (
            "validate this policy\n\n```json\n" + json.dumps(VALID_POLICY) + "\n```"
        )
        parsed = agent._extract_policy_json(prompt)
        self.assertIsNotNone(parsed)
        self.assertIn("Statement", parsed)

    def test_extracts_inline_json(self):
        prompt = "check this policy " + json.dumps(VALID_POLICY)
        parsed = agent._extract_policy_json(prompt)
        self.assertIsNotNone(parsed)

    def test_ignores_non_policy_json(self):
        parsed = agent._extract_policy_json("something like {\"unrelated\": 1}")
        self.assertIsNone(parsed)

    def test_short_circuit_returns_validation_without_bedrock(self):
        tool_output = {
            "is_valid": True,
            "findings": [],
            "summary": {"errors": 0, "warnings": 0, "suggestions": 0, "total_findings": 0, "verdict": "clean"},
            "security_analysis": {"wildcards": [], "dangerous_patterns": [], "missing_conditions": []},
        }
        prompt = "validate this policy " + json.dumps(VALID_POLICY)
        with patch.object(agent, "invoke_tool", return_value=tool_output) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(_event(prompt), None)

        converse.assert_not_called()
        invoke.assert_called_once()
        tool_name, tool_input = invoke.call_args.args
        self.assertEqual(tool_name, "validate_policy")
        self.assertIn("policy_document", tool_input)

        body = json.loads(response["body"])
        self.assertEqual(body["usage"], {"inputTokens": 0, "outputTokens": 0})
        self.assertEqual(len(body["tools_used"]), 1)
        self.assertEqual(body["tools_used"][0]["tool"], "validate_policy")
        self.assertIn("Policy is valid", body["response"])

    def test_short_circuit_skipped_without_json(self):
        fake_bedrock = {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "usage": {"inputTokens": 1, "outputTokens": 2},
        }
        with patch.object(agent, "invoke_tool") as invoke, \
                patch.object(agent, "converse_with_tools", return_value=(fake_bedrock, [], None, [])) as converse:
            agent.handler(_event("validate a policy for me"), None)

        invoke.assert_not_called()
        converse.assert_called_once()


class ActionPlanAndExportShortCircuitTest(unittest.TestCase):
    def _plan_result(self):
        return {
            "action_plan": [
                {
                    "priority": 1,
                    "action": "Delete unused role",
                    "role_name": "role-a",
                    "severity": "MEDIUM",
                    "priority_score": 60,
                    "effort": "trivial",
                    "risk_if_ignored": "Attack surface",
                    "rationale": "unused",
                }
            ],
            "showing": 1,
            "summary": {"total_findings": 1, "quick_wins_count": 1, "high_priority_count": 0},
            "quick_wins": [{"action": "Delete unused role", "role_name": "role-a", "why": "quick"}],
            "risk_distribution": {"medium": 1},
        }

    def test_calls_plan_then_export_no_bedrock(self):
        export_result = {
            "success": True,
            "s3_path": "s3://bucket/reports/action_plan_2026.md",
            "filename": "action_plan_2026.md",
            "download_url": "https://example.com/download",
            "valid_for": "1 hour",
        }
        # Ensure the response uses whatever "valid_for" the tool reports.
        expected_valid_for = "1 hour"
        results = iter([self._plan_result(), export_result])

        def fake_invoke(tool_name, _):
            return next(results)

        with patch.object(agent, "invoke_tool", side_effect=fake_invoke) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(_event("generate an action plan and export it"), None)

        converse.assert_not_called()
        self.assertEqual(invoke.call_count, 2)
        first_call = invoke.call_args_list[0].args
        second_call = invoke.call_args_list[1].args
        self.assertEqual(first_call[0], "generate_action_plan")
        self.assertEqual(second_call[0], "export_report")

        body = json.loads(response["body"])
        self.assertEqual(len(body["tools_used"]), 2)
        self.assertEqual(body["tools_used"][0]["tool"], "generate_action_plan")
        self.assertEqual(body["tools_used"][1]["tool"], "export_report")
        self.assertIn("Download here", body["response"])
        self.assertIn("action_plan_2026.md", body["response"])
        self.assertIn(expected_valid_for, body["response"])
        # The standalone "Say `export that`" invitation must not appear in the
        # compound response — we already exported, so it's contradictory.
        self.assertNotIn("Say `export that`", body["response"])

    def test_falls_back_to_plain_action_plan_without_export_intent(self):
        with patch.object(agent, "invoke_tool", return_value=self._plan_result()) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(_event("generate an action plan"), None)

        converse.assert_not_called()
        invoke.assert_called_once()
        tool_name, _ = invoke.call_args.args
        self.assertEqual(tool_name, "generate_action_plan")
        body = json.loads(response["body"])
        self.assertEqual(len(body["tools_used"]), 1)


if __name__ == "__main__":
    unittest.main()
