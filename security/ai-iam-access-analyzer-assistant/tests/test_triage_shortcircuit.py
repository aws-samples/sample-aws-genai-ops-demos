"""Offline tests for the triage_access_keys short-circuit (#175).

Pins the intent regex, the render output shape (fenced JSON payload + prose
that encodes the ACCESS KEY TRIAGE prompt rules), and the tool-error path.
Mirrors the test structure of test_action_plan_shortcircuit.py.
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


def _sample_triage_result(**overrides):
    """A minimal but realistic tool payload: one Critical admin row, one
    Cleanup idle row, both under a single user-facing account id."""
    base = {
        "keys": [
            {
                "account_id": "111111111111",
                "user": "admin-user",
                "is_root": False,
                "key_id": "AKIAADMIN00001",
                "status": "Active",
                "created": "2024-01-01T00:00:00+00:00",
                "key_age_days": 630,
                "last_used": "NEVER",
                "last_used_service": "",
                "actions": "* [*]",
                "policies": "AdministratorAccess",
                "resource_scope": "WILDCARD",
                "has_condition": False,
                "risk_flags": ["ADMIN", "NEVER_USED", "KEY_AGE_630d(>1yr)"],
                "priority_class": "Critical",
                "suggested_remediation": "IAM_Identity_Center",
            },
            {
                "account_id": "111111111111",
                "user": "svc-loader",
                "is_root": False,
                "key_id": "AKIASVCLOAD001",
                "status": "Active",
                "created": "2025-01-01T00:00:00+00:00",
                "key_age_days": 200,
                "last_used": "2025-06-01T00:00:00+00:00",
                "last_used_service": "s3",
                "actions": "s3:GetObject [arn:aws:s3:::b/*]",
                "policies": "inline:ReadOnly",
                "resource_scope": "SCOPED",
                "has_condition": False,
                "risk_flags": ["IDLE_113d"],
                "priority_class": "Cleanup",
                "suggested_remediation": "IAM_Role",
            },
        ],
        "summary": {
            "Critical": 1, "High": 0, "Cleanup": 1, "Rotation": 0,
            "total_keys": 2, "users_with_keys": 2,
        },
        "coverage": [
            {"source": "iam", "state": "checked", "detail": "2 users; 2 with keys", "count": 2},
            {"source": "iam-policy-resolution", "state": "checked", "detail": "OK", "count": 2},
        ],
        "usage_lag_caveat": (
            "Last-used data can lag by hours, and a low-frequency workload "
            "may back a rare-but-critical job that appears idle. Verify with "
            "the resource owner before recommending any removal."
        ),
    }
    base.update(overrides)
    return base


# --- Intent regex ---------------------------------------------------------


class TriageIntentTest(unittest.TestCase):
    def test_matches_direct_audit_phrasings(self):
        for msg in (
            "audit my access keys",
            "audit the access keys",
            "triage access keys",
            "inventory all iam access keys",
            "check my access keys",
            "list my access keys",
            "show me my access keys",
            "find access keys",
        ):
            self.assertIsNotNone(
                agent._TRIAGE_ACCESS_KEYS_INTENT.search(msg),
                msg=f"expected match: {msg!r}",
            )

    def test_matches_stale_and_over_permissioned(self):
        for msg in (
            "which of my keys are stale",
            "which access keys are risky",
            "which iam keys are over-permissioned",
            "stale access keys",
            "unrotated iam keys",
            "long-lived keys in this account",
            "over-permissioned users",
            "over permissioned iam users",
        ):
            self.assertIsNotNone(
                agent._TRIAGE_ACCESS_KEYS_INTENT.search(msg),
                msg=f"expected match: {msg!r}",
            )

    def test_matches_compact_intent_nouns(self):
        for msg in (
            "iam key hygiene",
            "access key audit",
            "key triage report",
            "iam access key posture",
        ):
            self.assertIsNotNone(
                agent._TRIAGE_ACCESS_KEYS_INTENT.search(msg),
                msg=f"expected match: {msg!r}",
            )

    def test_rejects_unrelated_prompts(self):
        for msg in (
            "generate an action plan",
            "compare roles a and b",
            "list findings",
            "show me critical findings",
            "check dependencies for this role",
            "validate this policy",
            "what is blast radius",
            "hello",
            "",
        ):
            self.assertIsNone(
                agent._TRIAGE_ACCESS_KEYS_INTENT.search(msg),
                msg=f"expected NO match: {msg!r}",
            )


# --- Handler end-to-end short-circuit -----------------------------------------


class HandlerShortCircuitTest(unittest.TestCase):
    def _event(self, message):
        return {
            "httpMethod": "POST",
            "body": json.dumps(
                {
                    "message": message,
                    "history": [],
                    "mode": "guided",
                }
            ),
        }

    def test_audit_prompt_bypasses_bedrock_and_returns_fenced_json(self):
        with patch.object(agent, "invoke_tool", return_value=_sample_triage_result()) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(self._event("audit my access keys"), None)

        # Bedrock was not called; the tool was invoked exactly once.
        converse.assert_not_called()
        invoke.assert_called_once()
        tool_name, tool_input = invoke.call_args.args
        self.assertEqual(tool_name, "triage_access_keys")
        self.assertEqual(tool_input, {})

        body = json.loads(response["body"])
        self.assertEqual(200, response["statusCode"])
        self.assertEqual(body["usage"], {"inputTokens": 0, "outputTokens": 0})
        self.assertEqual(len(body["tools_used"]), 1)
        self.assertEqual(body["tools_used"][0]["tool"], "triage_access_keys")
        self.assertIsNone(body["pagination"])

        text = body["response"]
        # Prose intro names the totals + priority mix.
        self.assertIn("Reviewed 2 access keys", text)
        self.assertIn("1 Critical", text)
        self.assertIn("1 Cleanup", text)
        # Deactivate → monitor → delete safety framing is always emitted.
        self.assertIn("deactivate", text)
        self.assertIn("monitor", text)
        # The usage_lag_caveat is quoted in prose.
        self.assertIn("Last-used data can lag", text)
        # The frontend routing hinges on this fenced JSON block.
        self.assertIn("```json", text)
        self.assertIn('"_type": "access_keys_report"', text)
        self.assertIn('"keys":', text)

    def test_intent_that_hits_pagination_first_does_not_reach_triage(self):
        # A pagination follow-up ("next 20") that arrives with a pagination
        # context should short-circuit at the pagination path, not at the
        # triage path — even if a paginate-flavored word overlaps.
        pagination_ctx = {
            "tool": "list_findings",
            "next_token": "cursor-1",
            "has_more": True,
            "last_input": {"severity": "CRITICAL", "limit": 20},
        }
        list_findings_result = {
            "findings": [], "next_token": "", "has_more": False, "total_matching": 0,
        }
        event = {
            "httpMethod": "POST",
            "body": json.dumps(
                {"message": "next 20", "history": [], "mode": "guided",
                 "pagination": pagination_ctx}
            ),
        }
        with patch.object(agent, "invoke_tool", return_value=list_findings_result) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            agent.handler(event, None)

        converse.assert_not_called()
        invoke.assert_called_once()
        tool_name, _ = invoke.call_args.args
        self.assertEqual(tool_name, "list_findings")

    def test_no_short_circuit_for_unrelated_prompts(self):
        # A plain question that doesn't match any intent must fall through
        # to Bedrock — the tool must NOT be invoked.
        fake_response = {
            "output": {"message": {"content": [{"text": "answer"}]}},
            "usage": {"inputTokens": 1, "outputTokens": 1},
        }
        event = self._event("what is blast radius")
        with patch.object(agent, "invoke_tool") as invoke, \
                patch.object(agent, "converse_with_tools",
                             return_value=(fake_response, [], None)) as converse:
            agent.handler(event, None)

        invoke.assert_not_called()
        converse.assert_called_once()

    def test_tool_error_returns_apology_without_fenced_json(self):
        with patch.object(agent, "invoke_tool", return_value={"error": "AccessDenied"}) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(self._event("audit my access keys"), None)

        converse.assert_not_called()
        invoke.assert_called_once()
        body = json.loads(response["body"])
        self.assertIn("couldn't run", body["response"].lower())
        self.assertIn("AccessDenied", body["response"])
        # A tool-error apology has NO fenced JSON — the frontend must not
        # try to render an empty table.
        self.assertNotIn("```json", body["response"])
        self.assertIsNone(body["pagination"])


# --- Render function ---------------------------------------------------------


class RenderingTest(unittest.TestCase):
    def test_iam_unavailable_coverage_returns_prose_only(self):
        # When iam:ListUsers failed at the top, the tool returns coverage
        # unavailable + keys=[]. Render must NOT emit a fenced JSON block
        # (there is no data to render), and must name the failure detail.
        rendered = agent._render_triage_access_keys({
            "keys": [],
            "summary": {"total_keys": 0, "users_with_keys": 0},
            "coverage": [
                {"source": "iam", "state": "unavailable",
                 "detail": "iam:ListUsers failed: AccessDenied"},
            ],
            "usage_lag_caveat": "…",
        })
        self.assertIn("couldn't inventory", rendered)
        self.assertIn("AccessDenied", rendered)
        self.assertNotIn("```json", rendered)

    def test_empty_result_encourages_the_recommended_posture(self):
        rendered = agent._render_triage_access_keys({
            "keys": [],
            "summary": {"total_keys": 0, "users_with_keys": 0},
            "coverage": [
                {"source": "iam", "state": "checked", "detail": "0 users", "count": 0},
                {"source": "iam-policy-resolution", "state": "checked", "count": 0},
            ],
            "usage_lag_caveat": "…",
        })
        self.assertIn("No IAM users", rendered)
        self.assertIn("recommended posture", rendered)
        # Empty result: no fenced JSON — the table has nothing to render.
        self.assertNotIn("```json", rendered)

    def test_root_row_is_surfaced_first_in_prose(self):
        result = _sample_triage_result()
        # Insert a synthetic root row at the head of the list.
        result["keys"].insert(0, {
            "account_id": "111111111111",
            "user": "<root>",
            "is_root": True,
            "key_id": "(root)",
            "status": "Active",
            "created": "",
            "key_age_days": None,
            "last_used": "UNKNOWN",
            "last_used_service": "",
            "actions": "(root user — full account control)",
            "policies": "(root)",
            "resource_scope": "WILDCARD",
            "has_condition": False,
            "risk_flags": ["ADMIN"],
            "priority_class": "Critical",
            "suggested_remediation": "Remove_Root_Access_Keys",
        })
        result["summary"]["Critical"] = 2
        result["summary"]["total_keys"] = 3
        rendered = agent._render_triage_access_keys(result)
        # Root note surfaces WITH the exact suggested_remediation label.
        self.assertIn("Root user", rendered)
        self.assertIn("Remove_Root_Access_Keys", rendered)

    def test_partial_coverage_appears_as_warning(self):
        result = _sample_triage_result()
        result["coverage"] = [
            {"source": "iam", "state": "checked", "detail": "OK", "count": 2},
            {"source": "iam-policy-resolution", "state": "unavailable",
             "detail": "walk failed for: bob(AccessDenied)", "count": 1},
        ]
        rendered = agent._render_triage_access_keys(result)
        # Fenced JSON present — the iam source itself was checked, so the
        # table can still render whatever data we DID collect.
        self.assertIn("```json", rendered)
        # But partial-data warning surfaces in prose.
        self.assertIn("Partial data", rendered)
        self.assertIn("iam-policy-resolution", rendered)


if __name__ == "__main__":
    unittest.main()
