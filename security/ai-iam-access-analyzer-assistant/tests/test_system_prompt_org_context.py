"""Regression tests for the #173 Level 1 org-context system-prompt block.

Live testing on 2026-10-09 against the real deployed stack found that,
without this fix, the chat model would confidently guess the WRONG account
as the Security Hub delegated administrator when asked directly -- it had
no access to the org/delegated-admin fact the session-start /capabilities
probe had already computed. ``_get_org_context_block`` renders that fact
into a short, deterministic block appended to the system prompt so the
model answers from a known fact instead of inferring one from unrelated
tool-result data (e.g. "whichever account_id appears most often in
findings").

These tests pin: (a) each of the four org_context shapes produces the
expected directive, (b) the block is appended for BOTH modes (quick and
guided), and (c) the block never fabricates an org ID that wasn't actually
provided.
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agent  # noqa: E402


class NoOrgContextTest(unittest.TestCase):
    """org_context absent, None, or found no Organization at all."""

    def test_missing_org_context_produces_not_confirmed_block(self):
        block = agent._get_org_context_block(None)
        self.assertIn("not confirmed to be part of an aws organization", block.lower())
        self.assertIn("do not claim or guess", block.lower())

    def test_org_context_with_null_org_id_is_treated_the_same_as_missing(self):
        block = agent._get_org_context_block({"org_id": None, "is_delegated_admin": None})
        self.assertIn("not confirmed to be part of an aws organization", block.lower())

    def test_empty_dict_is_treated_the_same_as_missing(self):
        block = agent._get_org_context_block({})
        self.assertIn("not confirmed to be part of an aws organization", block.lower())


class DelegatedAdminTrueTest(unittest.TestCase):
    def setUp(self):
        self.block = agent._get_org_context_block(
            {"org_id": "o-jv46kla31h", "is_delegated_admin": True}
        )

    def test_names_the_real_org_id(self):
        self.assertIn("o-jv46kla31h", self.block)

    def test_states_this_account_is_the_delegated_admin(self):
        self.assertRegex(self.block, r"IS the Security Hub\s+delegated administrator")

    def test_states_findings_are_aggregated(self):
        self.assertIn("aggregated across every member account", self.block)

    def test_forbids_guessing_from_findings_data(self):
        # This is the exact failure mode live testing found: the model
        # guessed the admin account from which account_id appeared most in
        # findings. The block must explicitly forbid that shortcut.
        self.assertIn("do not guess", self.block.lower())
        self.assertIn("account_id appears most often", self.block)


class DelegatedAdminFalseTest(unittest.TestCase):
    def setUp(self):
        self.block = agent._get_org_context_block(
            {"org_id": "o-jv46kla31h", "is_delegated_admin": False}
        )

    def test_names_the_real_org_id(self):
        self.assertIn("o-jv46kla31h", self.block)

    def test_states_this_account_is_not_the_admin(self):
        self.assertRegex(self.block, r"is NOT the Security\s+Hub delegated administrator")

    def test_does_not_claim_aggregation_for_this_account(self):
        self.assertNotIn("aggregated across every member account", self.block)

    def test_instructs_not_to_guess_the_real_admin_account(self):
        self.assertIn("don't guess one from", self.block.lower())


class DelegatedAdminUnknownTest(unittest.TestCase):
    def test_in_org_but_admin_status_unconfirmed(self):
        block = agent._get_org_context_block(
            {"org_id": "o-jv46kla31h", "is_delegated_admin": None}
        )
        self.assertIn("o-jv46kla31h", block)
        self.assertIn("could not be confirmed", block)
        self.assertRegex(block.lower(), r"say delegated-admin\s+status is unconfirmed")


class SystemPromptIntegrationTest(unittest.TestCase):
    """Confirm _get_system_prompt actually appends the block for both modes."""

    def test_quick_mode_includes_org_block(self):
        prompt = agent._get_system_prompt(
            "quick", {"org_id": "o-jv46kla31h", "is_delegated_admin": True}
        )
        self.assertIn("o-jv46kla31h", prompt)
        self.assertIn("MODE: QUICK", prompt)
        # Org block must precede the mode suffix in the concatenation.
        self.assertLess(prompt.index("ORG CONTEXT"), prompt.index("MODE: QUICK"))

    def test_guided_mode_includes_org_block(self):
        prompt = agent._get_system_prompt(
            "guided", {"org_id": "o-jv46kla31h", "is_delegated_admin": True}
        )
        self.assertIn("o-jv46kla31h", prompt)
        self.assertIn("MODE: GUIDED", prompt)
        self.assertLess(prompt.index("ORG CONTEXT"), prompt.index("MODE: GUIDED"))

    def test_default_mode_argument_still_works_without_org_context(self):
        # Backward-compat: callers (including any older cached code path)
        # that only pass `mode` must not raise.
        prompt = agent._get_system_prompt("guided")
        self.assertIn("not confirmed to be part of an aws organization", prompt.lower())

    def test_org_block_is_deterministic_for_the_same_input(self):
        # Required for Bedrock prompt caching within a session: the same
        # org_context must always render the exact same string so the
        # cache checkpoint isn't invalidated turn-to-turn.
        ctx = {"org_id": "o-jv46kla31h", "is_delegated_admin": True}
        self.assertEqual(
            agent._get_system_prompt("guided", ctx),
            agent._get_system_prompt("guided", dict(ctx)),
        )


if __name__ == "__main__":
    unittest.main()
