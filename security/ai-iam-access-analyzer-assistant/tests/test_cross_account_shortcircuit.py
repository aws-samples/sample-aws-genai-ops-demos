"""Tests for the cross-account read short-circuit (#173 Level 2).

The cross_account_iam_context tool returns an already-structured role list;
the dominant latency was a SECOND Bedrock round re-formatting it into a
markdown table (measured 10-18s). This short-circuit skips that by calling
the tool directly and rendering a fenced `_type: "cross_account_roles"`
block the frontend's CrossAccountRolesTable consumes.

Pins:
  - the intent regex (unambiguous "roles in account <12 digits>" only),
  - the account-id extraction,
  - the CRITICAL safety behavior: ambiguous messages (zero or 2+ distinct
    account ids) must FALL THROUGH to the model, never guess,
  - the zero-Bedrock handler path and the fenced-JSON payload shape,
  - the tool-error (spoke-not-deployed) path.

Mirrors the structure of test_triage_shortcircuit.py.
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

_ACCT = "686821851625"


def _sample_cross_account_result(**overrides):
    base = {
        "account_id": _ACCT,
        "is_hub_account": False,
        "roles": [
            {
                "name": "Admin",
                "arn": f"arn:aws:iam::{_ACCT}:role/Admin",
                "high_privilege": True,
                "create_date": "2025-12-10 21:27:27+00:00",
            },
            {
                "name": "AppRole",
                "arn": f"arn:aws:iam::{_ACCT}:role/AppRole",
                "high_privilege": False,
                "create_date": "2025-12-10 20:11:19+00:00",
            },
        ],
        "returned_count": 2,
        "has_more": True,
        "cloudtrail_reachable": True,
        "coverage": [
            {"source": "iam", "state": "checked", "detail": "2 returned", "count": 2},
        ],
    }
    base.update(overrides)
    return base


# --- Detection + extraction (the risky part) ------------------------------


class CrossAccountDetectionTest(unittest.TestCase):
    def _extract(self, msg):
        """Replicates the handler's extraction decision for unit testing:
        returns the account_id a short-circuit WOULD fire on, or None."""
        m = agent._CROSS_ACCOUNT_ROLES_INTENT.search(msg)
        if not m:
            return None
        acct = next((g for g in m.groups() if g), None)
        if not acct:
            return None
        distinct = {x.group(1) for x in agent._TWELVE_DIGIT.finditer(msg)}
        if len(distinct) != 1:
            return None
        return acct

    def test_extracts_account_from_clear_phrasings(self):
        cases = {
            "Show me the IAM roles in account 686821851625": "686821851625",
            "what roles exist in account 848837613892": "848837613892",
            "show roles in 661487690263": "661487690263",
            "list the iam roles in account 123456789012 please": "123456789012",
        }
        for msg, expected in cases.items():
            self.assertEqual(expected, self._extract(msg), msg=f"for: {msg!r}")

    def test_falls_through_when_two_accounts_named(self):
        """SAFETY: naming two accounts is ambiguous — the short-circuit must
        NOT guess which one; it returns None so the model asks."""
        self.assertIsNone(
            self._extract(
                "compare roles in account 111111111111 and account 222222222222"
            )
        )

    def test_falls_through_without_an_account_id(self):
        for msg in (
            "what is the blast radius of my admin role",
            "show me my iam roles",  # no account id
            "which roles are over-privileged",
        ):
            self.assertIsNone(self._extract(msg), msg=f"for: {msg!r}")

    def test_falls_through_without_roles_intent(self):
        for msg in (
            "show me account 686821851625",  # no 'roles'
            "what findings are in account 686821851625",  # findings, not roles
            "audit my access keys",  # different intent entirely
        ):
            self.assertIsNone(self._extract(msg), msg=f"for: {msg!r}")

    def test_does_not_match_non_12_digit_numbers(self):
        # 11 and 13 digit numbers are not account ids.
        self.assertIsNone(self._extract("roles in account 12345678901"))
        self.assertIsNone(self._extract("roles in account 1234567890123"))


# --- Handler end-to-end short-circuit -------------------------------------


class CrossAccountHandlerShortCircuitTest(unittest.TestCase):
    def _event(self, message):
        return {
            "httpMethod": "POST",
            "body": json.dumps({"message": message, "history": [], "mode": "guided"}),
        }

    def test_clear_prompt_bypasses_bedrock_and_returns_fenced_json(self):
        with patch.object(
            agent, "invoke_tool", return_value=_sample_cross_account_result()
        ) as invoke, patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(
                self._event(f"show me the IAM roles in account {_ACCT}"), None
            )

        # Bedrock not called; tool invoked once with the extracted account.
        converse.assert_not_called()
        invoke.assert_called_once()
        tool_name, tool_input = invoke.call_args.args
        self.assertEqual(tool_name, "cross_account_iam_context")
        self.assertEqual(tool_input, {"account_id": _ACCT})

        body = json.loads(response["body"])
        self.assertEqual(200, response["statusCode"])
        self.assertEqual(body["usage"], {"inputTokens": 0, "outputTokens": 0})
        self.assertEqual(body["tools_used"][0]["tool"], "cross_account_iam_context")
        self.assertIsNone(body["pagination"])

        text = body["response"]
        self.assertIn(f"account {_ACCT}", text)
        self.assertIn("2 returned", text)
        self.assertIn("high-privilege", text)  # Admin flagged
        # The frontend routing hinges on this fenced JSON block.
        self.assertIn("```json", text)
        self.assertIn('"_type": "cross_account_roles"', text)
        self.assertIn('"roles":', text)

    def test_two_accounts_does_not_short_circuit_falls_to_model(self):
        """SAFETY end-to-end: a message naming two accounts must reach the
        model path (converse_with_tools), NOT silently read one of them."""
        fake_response = {
            "output": {"message": {"content": [{"text": "Which account?"}]}},
            "usage": {"inputTokens": 5, "outputTokens": 6},
        }
        with patch.object(agent, "invoke_tool") as invoke, patch.object(
            agent,
            "converse_with_tools",
            return_value=(fake_response, [], None, []),
        ) as converse:
            response = agent.handler(
                self._event(
                    "show roles in account 111111111111 and account 222222222222"
                ),
                None,
            )

        # The cross-account tool was NOT invoked by the short-circuit; the
        # model path handled it (and would ask which account).
        converse.assert_called_once()
        for call in invoke.call_args_list:
            self.assertNotEqual(
                call.args[0] if call.args else None, "cross_account_iam_context"
            )
        self.assertEqual(200, response["statusCode"])

    def test_spoke_not_deployed_error_surfaces_verbatim_zero_bedrock(self):
        err = {
            "account_id": _ACCT,
            "is_hub_account": False,
            "error": (
                f"could not assume IamAnalyzerAssistantSpokeRole in account {_ACCT} "
                "— that account likely does not have the spoke role deployed"
            ),
            "coverage": [{"source": "iam", "state": "unavailable", "detail": "x"}],
        }
        with patch.object(agent, "invoke_tool", return_value=err) as invoke, patch.object(
            agent, "converse_with_tools"
        ) as converse:
            response = agent.handler(
                self._event(f"show the iam roles in account {_ACCT}"), None
            )

        converse.assert_not_called()
        invoke.assert_called_once()
        body = json.loads(response["body"])
        self.assertEqual(200, response["statusCode"])
        self.assertIn("does not have the spoke role deployed", body["response"])
        # No fabricated table on an error.
        self.assertNotIn('"_type": "cross_account_roles"', body["response"])


if __name__ == "__main__":
    unittest.main()
