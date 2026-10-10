"""Regression tests for the #173 Level 2 multi-account system-prompt rule.

Pins the guardrail against the exact failure mode this feature is
designed to avoid: looping cross_account_iam_context over "every account"
in one turn, and guessing an account_id from prior tool-result data
rather than asking the user (the same category of mistake that caused
the assistant to name the wrong Security Hub delegated-admin account in
#173 Level 1 testing, before that was fixed by threading org_context
into the prompt).
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agent  # noqa: E402


class MultiAccountPromptRuleTest(unittest.TestCase):
    def test_has_multi_account_section(self):
        self.assertIn("MULTI-ACCOUNT / CROSS-ACCOUNT QUESTIONS", agent.SYSTEM_PROMPT)

    def test_forbids_looping_over_all_accounts(self):
        prompt = agent.SYSTEM_PROMPT
        self.assertIn("MUST NOT", prompt)
        self.assertIn("in a loop", prompt)
        self.assertIn("one named account", prompt.lower())

    def test_prefers_list_findings_for_org_wide_questions(self):
        prompt = agent.SYSTEM_PROMPT
        self.assertIn("ORG-WIDE", prompt)
        self.assertIn("prefer list_findings instead", prompt)

    def test_forbids_guessing_account_id_from_prior_results(self):
        prompt = agent.SYSTEM_PROMPT
        self.assertIn("ASK them", prompt)
        self.assertIn("Do NOT guess an account_id", prompt)

    def test_names_the_specific_prior_failure_mode(self):
        """The rule should reference the exact prior mistake (naming the
        wrong account) so a future editor understands why the rule
        exists, not just that it exists."""
        self.assertIn(
            "name the WRONG account",
            agent.SYSTEM_PROMPT,
        )

    def test_handles_unavailable_coverage_without_implying_clean(self):
        self.assertIn("doesn't have the multi-account feature set up", agent.SYSTEM_PROMPT)


class CrossAccountToolSchemaTest(unittest.TestCase):
    def _tool_spec(self):
        for entry in agent.TOOL_CONFIG["tools"]:
            spec = entry.get("toolSpec")
            if spec and spec.get("name") == "cross_account_iam_context":
                return spec
        self.fail("cross_account_iam_context tool not found in TOOL_CONFIG")

    def test_account_id_is_required(self):
        spec = self._tool_spec()
        self.assertIn("account_id", spec["inputSchema"]["json"]["required"])

    def test_description_forbids_looping_at_the_tool_level_too(self):
        """Defense in depth: the rule exists in both the system prompt
        AND the tool's own description, since the model reads tool
        descriptions when deciding how to call a tool, independent of
        prose rules elsewhere."""
        spec = self._tool_spec()
        self.assertIn("never call this tool in a loop", spec["description"].lower())

    def test_registered_in_tool_functions_dispatch(self):
        self.assertIn("cross_account_iam_context", agent.TOOL_FUNCTIONS)


if __name__ == "__main__":
    unittest.main()
