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
                "suggested_remediation": "SSO_Federation",
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

    def _tour_event(self, message, history):
        return {
            "httpMethod": "POST",
            "body": json.dumps({"message": message, "history": history, "mode": "guided"}),
        }

    def test_bare_ready_after_tour_step6_announcement_still_short_circuits(self):
        """The GUIDED TOUR's Step 6 announces the access-key audit itself
        ("One more surface worth auditing — long-lived IAM access keys...");
        the user's actual next message is just "ready", which never
        contains the words "access keys" on its own. This is the exact
        failure mode from the demo re-test: Step 6 fell through to a full,
        slow Bedrock synthesis + tool call on every tour run because the
        short-circuit only ever inspected the current user message."""
        history = [
            {"role": "user", "content": "take me on a guided tour"},
            {
                "role": "assistant",
                "content": (
                    "Now let's validate that policy... looks good, 0 syntax errors. "
                    "One more surface worth auditing — long-lived IAM access keys. "
                    "These are the top credential exposure vector in AWS incident "
                    "reports, so we always cover this before wrapping up. Ready for "
                    "the next step?"
                ),
            },
        ]
        with patch.object(agent, "invoke_tool", return_value=_sample_triage_result()) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(self._tour_event("ready", history), None)

        converse.assert_not_called()
        invoke.assert_called_once()
        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(body["tools_used"][0]["tool"], "triage_access_keys")

    def test_bare_ready_after_an_unrelated_announcement_does_not_short_circuit(self):
        """A bare "ready" is only a green light for THIS short-circuit when
        the prior assistant turn was actually setting up the access-key
        audit. If the prior turn was about something else entirely (e.g.
        offering to compare roles), "ready" must fall through to Bedrock —
        it might mean "ready to compare roles", not "ready to audit keys"."""
        history = [
            {"role": "user", "content": "compare my top 3 roles"},
            {
                "role": "assistant",
                "content": "Want me to run that comparison now? Ready when you are.",
            },
        ]
        fake_response = {
            "output": {"message": {"content": [{"text": "Sure, comparing now..."}]}},
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

    def test_bare_ready_with_no_history_does_not_short_circuit(self):
        """A cold-start "ready" with no prior assistant turn at all must not
        short-circuit -- there is nothing to confirm the intent against."""
        fake_response = {
            "output": {"message": {"content": [{"text": "Ready for what?"}]}},
            "usage": {"inputTokens": 5, "outputTokens": 6},
        }
        with patch.object(agent, "invoke_tool") as invoke, \
                patch.object(
                    agent, "converse_with_tools",
                    return_value=(fake_response, [], None, []),
                ) as converse:
            agent.handler(self._tour_event("ready", []), None)

        invoke.assert_not_called()
        converse.assert_called_once()

    def test_bare_ready_with_trailing_politeness_still_short_circuits(self):
        """"yes please", "sure, go ahead", "continue please" -- natural
        phrasings a real tester types instead of a bare "ready" -- must
        still short-circuit. The original _BARE_AFFIRMATIVE pattern was an
        exact enumerated list with no tolerance for these, which is a
        plausible reason a live tour re-test kept timing out even after
        the bare-"ready" case was fixed and verified in isolation."""
        history = [
            {
                "role": "assistant",
                "content": "One more surface worth auditing — long-lived IAM access keys. Ready?",
            },
        ]
        for phrasing in ("yes please", "sure, go ahead", "continue please", "I'm ready"):
            with patch.object(agent, "invoke_tool", return_value=_sample_triage_result()) as invoke, \
                    patch.object(agent, "converse_with_tools") as converse:
                agent.handler(self._tour_event(phrasing, history), None)
            converse.assert_not_called()
            self.assertEqual(invoke.call_count, 1, f"expected short-circuit for {phrasing!r}")

    def test_exact_ready_for_the_next_step_short_circuits(self):
        """Pins the CONFIRMED root cause of the live guided-tour re-test
        that stayed blocked at Step 6 through three redeploys: the test
        harness's fixed advance string was the literal 24-character
        "ready for the next step" (verified verbatim against the actual
        test driver, not a paraphrase) — which the prior _BARE_AFFIRMATIVE
        pattern did NOT match (it only tolerated a short affirmative word
        plus an optional politeness tail, not "for the next step"). This
        explains the isolated-1.6s / in-tour-29s+ split precisely: the
        isolated test used "audit my access keys" (matches
        _TRIAGE_ACCESS_KEYS_INTENT directly), while every real tour advance
        used a phrase this short-circuit never recognized at all — no
        context-growth theory required."""
        history = [
            {
                "role": "assistant",
                "content": (
                    "Validated — PASS, 0 syntax errors. One more surface worth "
                    "auditing — long-lived IAM access keys. These are the top "
                    "credential exposure vector in AWS incident reports, so we "
                    "always cover this before wrapping up. Ready for the next step?"
                ),
            },
        ]
        with patch.object(agent, "invoke_tool", return_value=_sample_triage_result()) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(
                self._tour_event("ready for the next step", history), None
            )

        converse.assert_not_called()
        invoke.assert_called_once()
        self.assertEqual(response["statusCode"], 200)

    def test_longer_message_starting_with_yes_is_not_treated_as_bare_affirmative(self):
        """"yes, but explain X first" has more content than a bare
        affirmative and must reach the model even with a matching prior
        turn -- the user has something else to say."""
        history = [
            {
                "role": "assistant",
                "content": "One more surface worth auditing — long-lived IAM access keys. Ready?",
            },
        ]
        fake_response = {
            "output": {"message": {"content": [{"text": "Sure, here's how it works..."}]}},
            "usage": {"inputTokens": 5, "outputTokens": 6},
        }
        with patch.object(agent, "invoke_tool") as invoke, \
                patch.object(
                    agent, "converse_with_tools",
                    return_value=(fake_response, [], None, []),
                ) as converse:
            agent.handler(
                self._tour_event("yes, but explain what it checks first", history), None
            )

        invoke.assert_not_called()
        converse.assert_called_once()

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
                             return_value=(fake_response, [], None, [])) as converse:
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


class RemediationLinkTest(unittest.TestCase):
    """Pins the prose renderer's link contract: when a row supplies a
    suggested_remediation_url, the top-priority line renders the label as
    a plain Markdown link ``[Label](url)``; when the URL is missing, it
    falls back to a code-span form (backticks around the label). Same
    contract for the root-row branch.

    The form was previously ``[`Label`](url)`` (backticks nested inside
    link brackets). That confused react-markdown + remark-gfm in the
    assistant frontend — the opening left-bracket was sometimes elided
    and the label rendered with a dangling closing bracket — so the code
    span is dropped when we also have a URL to link to.
    """

    def test_top_priority_row_renders_remediation_as_markdown_link(self):
        result = _sample_triage_result()
        # Force a known top row with a real URL.
        result["keys"][0]["suggested_remediation"] = "SSO_Federation"
        result["keys"][0]["suggested_remediation_url"] = (
            "https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers.html"
        )
        rendered = agent._render_triage_access_keys(result)
        self.assertIn(
            "[SSO_Federation](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers.html)",
            rendered,
        )
        # No backticks-in-link syntax anywhere — that's the regression
        # this contract prevents.
        self.assertNotIn("[`SSO_Federation`]", rendered)

    def test_top_priority_falls_back_to_plain_backticks_when_url_empty(self):
        result = _sample_triage_result()
        result["keys"][0]["suggested_remediation"] = "Mystery_Label"
        result["keys"][0]["suggested_remediation_url"] = ""
        rendered = agent._render_triage_access_keys(result)
        self.assertIn("`Mystery_Label`", rendered)
        self.assertNotIn("](", rendered)  # no markdown link syntax at all

    def test_root_row_renders_remediation_as_markdown_link(self):
        result = _sample_triage_result()
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
            "suggested_remediation_url": (
                "https://docs.aws.amazon.com/accounts/latest/reference/root-user-access-key.html"
            ),
        })
        result["summary"]["Critical"] = 2
        result["summary"]["total_keys"] = 3
        rendered = agent._render_triage_access_keys(result)
        self.assertIn(
            "[Remove_Root_Access_Keys](https://docs.aws.amazon.com/accounts/latest/reference/root-user-access-key.html)",
            rendered,
        )
        self.assertNotIn("[`Remove_Root_Access_Keys`]", rendered)


# --- Educational intent short-circuit -----------------------------------------


class TriageEducationalIntentTest(unittest.TestCase):
    """Pins the _TRIAGE_EDUCATIONAL_INTENT regex — the second-order deterministic
    dispatch that catches "explain / describe / worst case" phrasings around
    access-key auditing so they don't fall through to Bedrock and blow past the
    API Gateway 29s ceiling."""

    def test_matches_explain_phrasings(self):
        # The exact prompt that surfaced the bug in production plus close
        # variants.
        for msg in (
            "Can you explain on what you would do exactly regarding Audit keys, "
            "worst case scenarios regarding them, what you could propose to fix etc.?",
            "explain what the access-key audit does",
            "describe the triage capability",
            "walk me through the access key audit",
            "tell me about key hygiene",
            "help me understand what audit access keys does",
        ):
            self.assertIsNotNone(
                agent._TRIAGE_EDUCATIONAL_INTENT.search(msg),
                msg=f"expected MATCH: {msg!r}",
            )

    def test_matches_hypothetical_phrasings(self):
        for msg in (
            "what would you do about my access keys",
            "how would you audit access keys",
            "how would you approach key hygiene here",
        ):
            self.assertIsNotNone(
                agent._TRIAGE_EDUCATIONAL_INTENT.search(msg),
                msg=f"expected MATCH: {msg!r}",
            )

    def test_matches_worst_case_phrasings(self):
        for msg in (
            "worst-case scenario for access keys",
            "worst case with an admin access key",
            "worst-case audit findings",
        ):
            self.assertIsNotNone(
                agent._TRIAGE_EDUCATIONAL_INTENT.search(msg),
                msg=f"expected MATCH: {msg!r}",
            )

    def test_rejects_direct_inventory_prompts(self):
        # Inventory prompts must NOT match the educational regex; they
        # belong to _TRIAGE_ACCESS_KEYS_INTENT.
        for msg in (
            "audit my access keys",
            "audit my iam access keys",
            "which of my keys are stale",
            "show me my access keys",
            "list all my access keys",
            "iam key hygiene",
            "stale access keys",
        ):
            self.assertIsNone(
                agent._TRIAGE_EDUCATIONAL_INTENT.search(msg),
                msg=f"expected NO educational match: {msg!r}",
            )

    def test_rejects_unrelated_prompts(self):
        for msg in (
            "explain what a role is",
            "describe the finding",
            "what would you recommend for this policy",
            "walk me through blast radius",
            "hello",
            "",
        ):
            self.assertIsNone(
                agent._TRIAGE_EDUCATIONAL_INTENT.search(msg),
                msg=f"expected NO educational match: {msg!r}",
            )


class TriageEducationalHandlerTest(unittest.TestCase):
    """End-to-end: user asks an educational question about the audit → handler
    returns the canned envelope, does NOT invoke the tool, does NOT reach
    Bedrock. Guards against timeout regression on the class of prompt that
    surfaced the bug in production."""

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

    def test_educational_prompt_bypasses_both_tool_and_bedrock(self):
        with patch.object(agent, "invoke_tool") as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(
                self._event(
                    "Can you explain on what you would do exactly regarding "
                    "Audit keys, worst case scenarios regarding them, what "
                    "you could propose to fix etc.?"
                ),
                None,
            )

        invoke.assert_not_called()
        converse.assert_not_called()

        body = json.loads(response["body"])
        self.assertIn("Audit access keys", body["response"])
        self.assertIn("Root user has access keys", body["response"])
        self.assertIn("Want me to audit my IAM access keys", body["response"])
        # tools_used is empty — no tool ran on this turn.
        self.assertEqual([], body["tools_used"])

    def test_educational_beats_inventory_on_mixed_intent(self):
        # A prompt that mentions BOTH "explain" and "audit my keys" prefers
        # the educational path so the customer gets an overview + CTA
        # rather than silently running the tool. Documented ordering.
        with patch.object(agent, "invoke_tool") as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(
                self._event("explain the audit and then audit my access keys"),
                None,
            )
        invoke.assert_not_called()
        converse.assert_not_called()
        body = json.loads(response["body"])
        self.assertIn("Audit access keys", body["response"])

    def test_inventory_prompt_still_reaches_tool_shortcircuit(self):
        # Regression guard: the plain inventory prompt does NOT get caught
        # by the educational regex — it goes to the tool short-circuit,
        # invokes the tool, and returns the fenced JSON payload.
        with patch.object(agent, "invoke_tool", return_value=_sample_triage_result()) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(self._event("audit my access keys"), None)
        invoke.assert_called_once()
        converse.assert_not_called()
        body = json.loads(response["body"])
        self.assertIn("```json", body["response"])


if __name__ == "__main__":
    unittest.main()
