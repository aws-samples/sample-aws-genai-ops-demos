"""Offline tests for the export-followup short-circuit — Bug 1 fix.

Bug 1: on prompts like `export that` following a generate_policy turn, the
model synthesizes a plausible-looking presigned URL instead of invoking
export_report. Result: tools_used=[] and the link 404s. The short-circuit
detects the follow-up intent, pulls the last assistant artifact from
conversation history, and runs export_report deterministically.
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


# --- Intent regex --------------------------------------------------------


class ExportFollowupIntentTest(unittest.TestCase):
    def test_matches_common_phrasings(self):
        for msg in (
            "export that",
            "save that",
            "export it",
            "save it",
            "keep that",
            "store this",
            "export the policy",
            "save the plan",
            "export the report",
            "download that",
            "please export that",
            "can you save that",
            "could you export it",
            "go ahead and save it",
            "export that to s3",
            "save that please",
            "export.",
            "SAVE THAT",
        ):
            self.assertIsNotNone(
                agent._EXPORT_FOLLOWUP_INTENT.match(msg),
                msg=f"expected match: {msg!r}",
            )

    def test_rejects_unrelated_prompts(self):
        for msg in (
            "export access keys to a CSV file",  # not a follow-up; needs to be handled by Bedrock
            "generate an action plan and export it",  # compound intent handled elsewhere
            "list findings",
            "audit my access keys",
            "compare roles a and b",
            "what does the export tool do",
            "why did that export fail",
            "hello",
            "",
        ):
            self.assertIsNone(
                agent._EXPORT_FOLLOWUP_INTENT.match(msg),
                msg=f"expected NO match: {msg!r}",
            )


# --- Content-type inference ---------------------------------------------


class ContentTypeInferenceTest(unittest.TestCase):
    def test_json_policy_block_maps_to_policy_json(self):
        artifact = (
            "Here is a least-privilege policy for ApolloRole based on the "
            "last 90 days of CloudTrail activity:\n\n"
            '```json\n'
            '{\n'
            '  "Version": "2012-10-17",\n'
            '  "Statement": [{"Effect": "Allow", "Action": "s3:GetObject", '
            '"Resource": "arn:aws:s3:::my-bucket/*"}]\n'
            '}\n'
            '```'
        )
        ct, fmt = agent._infer_export_content_type(artifact)
        self.assertEqual(ct, "policy")
        self.assertEqual(fmt, "json")

    def test_action_plan_text_maps_to_action_plan_md(self):
        artifact = "Prioritized action plan — showing top 10 of 42 findings.\n\n| # | Action | Role | Priority score |\n"
        ct, fmt = agent._infer_export_content_type(artifact)
        self.assertEqual(ct, "action_plan")
        self.assertEqual(fmt, "md")

    def test_blast_radius_maps_correctly(self):
        artifact = "# Blast radius analysis for ApolloRole\n\nRisk score: 70 (HIGH). Impact radius: 12 dependents."
        ct, fmt = agent._infer_export_content_type(artifact)
        self.assertEqual(ct, "blast_radius")

    def test_change_request_maps_correctly(self):
        artifact = "# Change Request: Delete ApolloRole\n\n**Rollback plan**: recreate the role from CDK…"
        ct, fmt = agent._infer_export_content_type(artifact)
        self.assertEqual(ct, "change_request")

    def test_generic_content_defaults_to_report_md(self):
        artifact = "# IAM Access Analyzer Findings Report\n\n| # | Title | Severity | Resource | Status |\n| 1 | Public access | HIGH | s3:xyz | ACTIVE |"
        ct, fmt = agent._infer_export_content_type(artifact)
        self.assertEqual(ct, "report")
        self.assertEqual(fmt, "md")


# --- Artifact body extraction --------------------------------------------


class ArtifactBodyExtractionTest(unittest.TestCase):
    def test_fenced_policy_json_is_extracted(self):
        artifact = (
            "Prose framing before the policy…\n\n"
            '```json\n'
            '{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}]}\n'
            '```\n\n'
            "Prose framing after."
        )
        body = agent._extract_artifact_body(artifact)
        # Body must be the pure JSON, no prose.
        parsed = json.loads(body)
        self.assertEqual(parsed["Version"], "2012-10-17")

    def test_non_policy_content_returned_as_is(self):
        artifact = "# Report\n\nSome content that isn't a policy."
        body = agent._extract_artifact_body(artifact)
        self.assertIn("Report", body)


# --- Last-assistant-artifact walk ----------------------------------------


class LastAssistantArtifactTest(unittest.TestCase):
    def test_walks_back_past_short_or_saved_messages(self):
        long_artifact = "# Real report\n\n" + "line " * 40  # > 100 chars
        history = [
            {"role": "user", "content": "list findings"},
            {"role": "assistant", "content": long_artifact},
            {"role": "user", "content": "export that"},
            {"role": "assistant", "content": "Saved: `report.md` — [Download here](https://…)"},
            {"role": "user", "content": "export that"},  # a redo
        ]
        result = agent._last_assistant_artifact(history)
        # _last_assistant_artifact strips trailing whitespace; assert on
        # the stripped comparison so the test doesn't accidentally pin the
        # whitespace behavior.
        self.assertEqual(result, long_artifact.strip())

    def test_returns_empty_when_no_substantial_artifact(self):
        history = [
            {"role": "assistant", "content": "ok"},
            {"role": "assistant", "content": "sure"},
        ]
        self.assertEqual(agent._last_assistant_artifact(history), "")

    def test_returns_empty_when_no_history(self):
        self.assertEqual(agent._last_assistant_artifact([]), "")
        self.assertEqual(agent._last_assistant_artifact(None), "")


# --- Handler end-to-end short-circuit ------------------------------------


class HandlerShortCircuitTest(unittest.TestCase):
    def _event(self, message, history=None):
        return {
            "httpMethod": "POST",
            "body": json.dumps(
                {
                    "message": message,
                    "history": history or [],
                    "mode": "guided",
                }
            ),
        }

    def _policy_artifact(self):
        return (
            "Least-privilege policy for ApolloRole:\n\n"
            '```json\n'
            '{\n'
            '  "Version": "2012-10-17",\n'
            '  "Statement": [{"Effect": "Allow", "Action": "s3:GetObject", '
            '"Resource": "arn:aws:s3:::my-bucket/*"}]\n'
            '}\n'
            '```\n\n'
            "Reduction: 92% (from 340 actions to 1)."
        )

    def _export_success(self, filename="policy.json"):
        # Response envelope produced by the current export_report tool —
        # points at the Cognito-authed /downloads/ API GW route, no
        # fixed TTL field.
        return {
            "success": True,
            "filename": filename,
            "s3_path": f"s3://iam-analyzer-reports/policies/{filename}",
            "download_url": (
                f"https://api.example.com/prod/downloads/policies/{filename}"
            ),
            "exported_at": "2026-09-22 23:59 UTC",
        }

    def test_export_after_generate_policy_actually_calls_export_report(self):
        history = [
            {"role": "user", "content": "Generate a least-privilege policy for ApolloRole"},
            {"role": "assistant", "content": self._policy_artifact()},
        ]
        expected_export = self._export_success("policy-ApolloRole.json")

        with patch.object(agent, "invoke_tool", return_value=expected_export) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(self._event("export that", history), None)

        # Bedrock is NOT called (short-circuit fired) and export_report IS
        # called with a policy content_type + json format.
        converse.assert_not_called()
        invoke.assert_called_once()
        tool_name, tool_input = invoke.call_args.args
        self.assertEqual(tool_name, "export_report")
        self.assertEqual(tool_input["content_type"], "policy")
        self.assertEqual(tool_input["format"], "json")
        # The content passed is the extracted JSON policy, not the prose framing.
        parsed = json.loads(tool_input["content"])
        self.assertEqual(parsed["Version"], "2012-10-17")

        body = json.loads(response["body"])
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["usage"], {"inputTokens": 0, "outputTokens": 0})
        self.assertEqual(len(body["tools_used"]), 1)
        self.assertEqual(body["tools_used"][0]["tool"], "export_report")
        self.assertIsNone(body["pagination"])
        # The response text includes the REAL download URL from the tool,
        # never a fabricated one.
        self.assertIn(expected_export["download_url"], body["response"])
        self.assertIn("policy-ApolloRole.json", body["response"])
        self.assertIn("[Download here]", body["response"])

    def test_export_after_findings_maps_to_report_type(self):
        history = [
            {"role": "user", "content": "list findings"},
            {"role": "assistant", "content": "# IAM Access Analyzer Findings Report\n\n" + ("| 1 | x | HIGH | y | ACTIVE |\n" * 10)},
        ]
        with patch.object(agent, "invoke_tool", return_value=self._export_success("report.md")) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            agent.handler(self._event("export that", history), None)

        converse.assert_not_called()
        tool_name, tool_input = invoke.call_args.args
        self.assertEqual(tool_name, "export_report")
        self.assertEqual(tool_input["content_type"], "report")
        self.assertEqual(tool_input["format"], "md")

    def test_export_with_no_prior_artifact_falls_through_to_bedrock(self):
        # First-turn export request — no artifact to export. Must NOT call
        # export_report; must fall through so Bedrock can ask for clarification.
        fake_response = {
            "output": {"message": {"content": [{"text": "What would you like to export?"}]}},
            "usage": {"inputTokens": 5, "outputTokens": 6},
        }
        with patch.object(agent, "invoke_tool") as invoke, \
                patch.object(agent, "converse_with_tools",
                             return_value=(fake_response, [], None, [])) as converse:
            agent.handler(self._event("export that", []), None)

        invoke.assert_not_called()
        converse.assert_called_once()

    def test_export_after_a_prior_export_does_not_recurse(self):
        # After a Saved: line, another "export that" must NOT re-export the
        # Saved: message. Must fall through to Bedrock instead.
        history = [
            {"role": "user", "content": "generate a policy"},
            {"role": "assistant", "content": self._policy_artifact()},
            {"role": "user", "content": "export that"},
            {"role": "assistant", "content": "Saved: `policy.json` — [Download here](https://...) *(valid for 1 hour)*"},
        ]
        # Even though the LAST assistant message starts with "Saved:", we
        # look BACK PAST it to the prior artifact and export that instead —
        # so a redo is supported without recursion.
        with patch.object(agent, "invoke_tool", return_value=self._export_success()) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            agent.handler(self._event("export that", history), None)

        converse.assert_not_called()
        invoke.assert_called_once()
        tool_name, tool_input = invoke.call_args.args
        self.assertEqual(tool_name, "export_report")
        # The redo should still export the ORIGINAL policy, not the Saved: line.
        parsed = json.loads(tool_input["content"])
        self.assertEqual(parsed["Version"], "2012-10-17")

    def test_export_tool_error_surfaces_apology_no_fake_url(self):
        history = [
            {"role": "user", "content": "generate a policy"},
            {"role": "assistant", "content": self._policy_artifact()},
        ]
        with patch.object(agent, "invoke_tool", return_value={"error": "AccessDenied", "success": False}) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(self._event("export that", history), None)

        converse.assert_not_called()
        invoke.assert_called_once()
        body = json.loads(response["body"])
        # The apology quotes the tool error verbatim.
        self.assertIn("AccessDenied", body["response"])
        # It does NOT include a "[Download here]" link — no fake URL.
        self.assertNotIn("[Download here]", body["response"])
        self.assertNotIn("https://", body["response"])

    def test_export_intent_with_short_reply_falls_through(self):
        # If the prior assistant message is too short to be a real artifact
        # ("ok", "sure"), we don't try to export it — fall through to Bedrock.
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hi! I can help you audit IAM findings."},
        ]
        fake_response = {
            "output": {"message": {"content": [{"text": "Sure — what would you like to save?"}]}},
            "usage": {"inputTokens": 5, "outputTokens": 6},
        }
        with patch.object(agent, "invoke_tool") as invoke, \
                patch.object(agent, "converse_with_tools",
                             return_value=(fake_response, [], None, [])) as converse:
            agent.handler(self._event("export that", history), None)

        invoke.assert_not_called()
        converse.assert_called_once()

    def test_compound_generate_and_export_still_handled_upstream(self):
        # A single-message compound intent ("generate an action plan and
        # export it") should hit _shortcircuit_action_plan_and_export FIRST,
        # not this export follow-up. Verify by checking the tool called is
        # generate_action_plan (which the compound short-circuit fires first).
        action_plan_result = {
            "action_plan": [{"priority": 1, "action": "x", "role_name": "r", "severity": "HIGH", "effort": "S", "priority_score": 90}],
            "summary": {"total_findings": 1, "quick_wins_count": 0, "high_priority_count": 1},
            "quick_wins": [],
            "risk_distribution": {"high": 1},
        }
        export_result = self._export_success("plan.md")
        # Two-call side effect: first generate_action_plan, then export_report.
        with patch.object(agent, "invoke_tool", side_effect=[action_plan_result, export_result]) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            agent.handler(self._event("generate an action plan and export it", []), None)

        converse.assert_not_called()
        self.assertEqual(invoke.call_count, 2)
        first_tool = invoke.call_args_list[0].args[0]
        second_tool = invoke.call_args_list[1].args[0]
        self.assertEqual(first_tool, "generate_action_plan")
        self.assertEqual(second_tool, "export_report")


if __name__ == "__main__":
    unittest.main()
