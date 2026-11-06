"""Regression test for the general EDUCATIONAL QUESTIONS system-prompt rule.

The rule tells the model to answer "explain / describe / walk me through /
worst-case" style prompts conceptually and briefly WITHOUT calling any
tool. It sits above every tool-specific "you MUST call the tool" rule and
overrides them when the user's intent is clearly educational rather than
actionable.

This is the general fallback for the timeout class that surfaced on
`triage_access_keys` in production — an educational prompt combined with
a heavy tool call and a long synthesis round exceeds the API Gateway 29s
ceiling. The deterministic short-circuit for triage handles the biggest
offender; this rule catches the same shape on other tools (list_findings,
generate_action_plan, etc.) at the prompt-level instead.

Pinning the key phrases keeps a future prompt rewrite from silently
dropping the rule.
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agent  # noqa: E402


class SystemPromptEducationalRuleTest(unittest.TestCase):
    def test_has_educational_questions_section(self):
        self.assertIn("EDUCATIONAL QUESTIONS", agent.SYSTEM_PROMPT)

    def test_names_the_common_signal_words(self):
        # The rule enumerates the phrasings that trigger it. If a rewrite
        # drops any of these, the rule loses coverage on that shape.
        for signal in (
            "explain",
            "describe",
            "walk me through",
            "tell me about",
            "what would you do",
            "how would you approach",
            "worst-case scenarios",
        ):
            self.assertIn(
                signal, agent.SYSTEM_PROMPT.lower(),
                msg=f"educational signal missing from prompt: {signal!r}",
            )

    def test_forbids_tool_call_for_conceptual_intent(self):
        # The core directive is "respond WITHOUT calling any tool" when
        # the intent is educational. Pin both ends.
        prompt = agent.SYSTEM_PROMPT
        self.assertIn("WITHOUT calling any tool", prompt)
        self.assertIn("conceptually", prompt.lower())

    def test_overrides_tool_specific_must_rules(self):
        # The general rule must explicitly override tool-specific MUST-call
        # rules; otherwise Bedrock will follow the strongest rule it sees
        # (usually the tool-specific one) and burn the tool call anyway.
        self.assertIn("OVERRIDES", agent.SYSTEM_PROMPT)
        self.assertIn("MUST call the tool", agent.SYSTEM_PROMPT)

    def test_specifies_short_response_and_cta(self):
        # Length matters: even without a tool call, a long conceptual
        # synthesis can still approach the 29s ceiling if the model
        # decides to be thorough. Pin both the length guidance and the
        # CTA convention.
        prompt = agent.SYSTEM_PROMPT
        self.assertIn("6", prompt)   # "6-10 lines" or "6–10 lines"
        self.assertIn("CTA", prompt)

    def test_rule_is_positioned_above_tool_specific_rules(self):
        # The rule must appear BEFORE the ACCESS KEY TRIAGE 'you MUST
        # call' rule so a reader (and, empirically, the model) encounter
        # the general override first.
        prompt = agent.SYSTEM_PROMPT
        edu_idx = prompt.find("EDUCATIONAL QUESTIONS")
        triage_idx = prompt.find("ACCESS KEY TRIAGE")
        self.assertGreater(edu_idx, 0, "rule missing")
        self.assertGreater(triage_idx, 0, "ACCESS KEY TRIAGE anchor missing")
        self.assertLess(
            edu_idx, triage_idx,
            "EDUCATIONAL QUESTIONS must sit above ACCESS KEY TRIAGE",
        )


if __name__ == "__main__":
    unittest.main()
