"""Regression test for the #171 phase D system-prompt rule.

Phase D is the "belt on top of the mechanism" — the coverage contract
(#171 phases A, B) is what actually stops the model from being handed
misleading empty data, and this rule is the final guardrail: when a
tool reports a source as ``unavailable``, the prose must name that
source rather than describe the posture as "clean".

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


class SystemPromptCoverageRuleTest(unittest.TestCase):
    def test_has_coverage_handling_section(self):
        self.assertIn("COVERAGE HANDLING", agent.SYSTEM_PROMPT)

    def test_names_all_five_sources(self):
        for source in ("securityhub", "iam", "accessanalyzer", "cloudtrail", "s3"):
            self.assertIn(source, agent.SYSTEM_PROMPT, msg=f"missing source: {source}")

    def test_names_all_three_states(self):
        for state in ("checked", "empty", "unavailable"):
            self.assertIn(state, agent.SYSTEM_PROMPT)

    def test_forbids_calling_posture_clean_when_unavailable(self):
        """Pin the actual rule text — this is the safety guardrail Phase D
        exists to add."""
        # The rule must forbid describing posture as clean when any source
        # is unavailable. We assert the two ends of that rule are present.
        prompt = agent.SYSTEM_PROMPT
        self.assertIn('unavailable', prompt)
        self.assertIn('MUST NOT', prompt)
        # Words the rule explicitly forbids the model from using in this
        # situation. If a rewrite drops them, this test flags it.
        for word in ('clean', 'healthy', 'safe'):
            self.assertIn(word, prompt.lower())

    def test_distinguishes_empty_from_unavailable(self):
        """The rule must explicitly call out that empty != unavailable, so
        a legitimate zero-findings result isn't upgraded to an outage
        (and vice versa).
        """
        # A single sentence anywhere in the prompt that says 'empty' is
        # DIFFERENT from 'unavailable'.
        self.assertRegex(
            agent.SYSTEM_PROMPT.lower(),
            r'"?empty"?\s+is\s+different\s+from\s+"?unavailable"?',
        )


if __name__ == "__main__":
    unittest.main()
