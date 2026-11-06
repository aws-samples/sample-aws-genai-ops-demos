"""Tests for the guided-tour STATE MACHINE (P2 root-cause fix).

The tour used to be model-driven with no server-side state, so capability
short-circuits (access-key audit at Step 6, action plan at Step 8) hijacked
the advance "ready" and looped the tour. This replaces that with an
explicit step pointer that round-trips like pagination: the pointer — not
the prior turn's text — decides what runs next, so the tour advances
deterministically and cannot loop.

Pins: start → Step 1; each "ready" with a tour pointer advances exactly one
step; the pointer increments and is returned for round-trip; Steps 6 and 8
run WITHOUT re-firing (the loop bug); Step 8 is terminal (active=False) and
lands plan + recap; the capability short-circuits never intercept while a
tour is active; Steps 2 and 7 bind to REAL roles from history (P4).
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


# Fake tool results keyed by tool name.
def _fake_tool(name, inp):
    if name == "list_findings":
        return {
            "findings": [
                {"title": "Admin role over-privileged", "severity": "HIGH",
                 "resource_id": "CloudSecAuditRole", "status": "ACTIVE"},
                {"title": "Unused EKS role", "severity": "MEDIUM",
                 "resource_id": "RvSaas-EksKubectlHandlerRole", "status": "ACTIVE"},
            ],
        }
    if name == "get_finding_details":
        return {"finding": {"title": "Over-privileged", "description": "Has *:* on resources.",
                            "severity": {"Label": "HIGH"}}}
    if name == "check_dependencies":
        return {"risk_score": {"level": "HIGH", "score": 70, "recommendation": "Review before changes."}}
    if name == "generate_policy":
        return {"proposed_policy": {"Version": "2012-10-17", "Statement": []},
                "reduction_metrics": {"reduction_percentage": 62.5}}
    if name == "triage_access_keys":
        return {"keys": [], "summary": {"total_keys": 0, "users_with_keys": 0},
                "coverage": [], "usage_lag_caveat": "..."}
    if name == "compare_roles":
        return {"comparison": [
            {"role_name": "CloudSecAuditRole", "exists": True, "risk_score": 70},
            {"role_name": "RvSaas-EksKubectlHandlerRole", "exists": True, "risk_score": 30},
        ], "recommendation": "CloudSecAuditRole is riskier."}
    if name == "generate_action_plan":
        return {"action_plan": [{"rank": 1, "action": "Remove unused perms",
                                 "role_name": "CloudSecAuditRole", "priority_score": 90}],
                "total_findings": 2, "quick_wins": []}
    raise AssertionError(f"unexpected tool {name}")


def _event(message, tour=None):
    body = {"message": message, "history": [], "mode": "guided"}
    if tour is not None:
        body["tour"] = tour
    return {"httpMethod": "POST", "body": json.dumps(body)}


def _event_with_history(message, history, tour=None):
    body = {"message": message, "history": history, "mode": "guided"}
    if tour is not None:
        body["tour"] = tour
    return {"httpMethod": "POST", "body": json.dumps(body)}


class TourStartTest(unittest.TestCase):
    def test_tour_start_runs_step_1_and_returns_pointer(self):
        with patch.object(agent, "invoke_tool", side_effect=_fake_tool), \
                patch.object(agent, "converse_with_tools") as converse:
            resp = agent.handler(_event("give me a guided tour"), None)
        converse.assert_not_called()
        body = json.loads(resp["body"])
        self.assertIn("Step 1 of 8", body["response"])
        self.assertEqual(body["tour"], {"step": 1, "active": True})

    def test_various_start_phrasings(self):
        for msg in ("Give me a guided tour", "walk me through my IAM",
                    "take me on a tour", "tour of my security posture"):
            with patch.object(agent, "invoke_tool", side_effect=_fake_tool), \
                    patch.object(agent, "converse_with_tools"):
                resp = agent.handler(_event(msg), None)
            body = json.loads(resp["body"])
            self.assertEqual(body["tour"]["step"], 1, msg=f"for {msg!r}")


class TourAdvanceTest(unittest.TestCase):
    def test_each_ready_advances_exactly_one_step(self):
        """The core fix: with a tour pointer, each 'ready' advances the
        pointer by exactly one — no loop, no re-fire."""
        for current in range(1, 8):
            with patch.object(agent, "invoke_tool", side_effect=_fake_tool), \
                    patch.object(agent, "converse_with_tools") as converse:
                resp = agent.handler(
                    _event("ready", tour={"step": current, "active": True}), None
                )
            converse.assert_not_called()
            body = json.loads(resp["body"])
            self.assertIn(f"Step {current + 1} of 8", body["response"],
                          msg=f"advancing from step {current}")
            # Pointer incremented (active until step 8).
            self.assertEqual(body["tour"]["step"], current + 1)

    def test_step_6_does_not_refire_on_next_ready(self):
        """The exact loop bug: at the Step 6 pointer, a 'ready' advances to
        Step 7 (role comparison) — it does NOT re-run the access-key audit."""
        history = [
            {"role": "assistant",
             "content": "| 1 | Admin | HIGH | CloudSecAuditRole | ACTIVE |\n"
                        "| 2 | EKS | MEDIUM | RvSaas-EksKubectlHandlerRole | ACTIVE |"},
        ]
        calls = []

        def tracking(name, inp):
            calls.append(name)
            return _fake_tool(name, inp)

        with patch.object(agent, "invoke_tool", side_effect=tracking), \
                patch.object(agent, "converse_with_tools") as converse:
            resp = agent.handler(
                _event_with_history("ready", history, tour={"step": 6, "active": True}), None
            )
        converse.assert_not_called()
        body = json.loads(resp["body"])
        self.assertIn("Step 7 of 8", body["response"])          # advanced
        self.assertNotIn("triage_access_keys", calls)           # did NOT re-run audit
        self.assertIn("compare_roles", calls)                   # ran step 7's tool

    def test_step_8_is_terminal_lands_plan_and_recap(self):
        """Advancing from step 7 runs Step 8, which returns active=False and
        shows the plan + the completion recap in ONE message (P6)."""
        with patch.object(agent, "invoke_tool", side_effect=_fake_tool), \
                patch.object(agent, "converse_with_tools") as converse:
            resp = agent.handler(
                _event("ready", tour={"step": 7, "active": True}), None
            )
        converse.assert_not_called()
        body = json.loads(resp["body"])
        self.assertIn("Step 8 of 8", body["response"])
        self.assertIn("completes the guided tour", body["response"])  # recap in same msg
        self.assertNotIn("Ready for the next step?", body["response"])  # no step 9
        self.assertEqual(body["tour"]["active"], False)               # terminal

    def test_ready_after_tour_finished_falls_through(self):
        """A 'ready' when the pointer is already at/after step 8 (or
        inactive) does NOT re-enter the tour — it falls through to the
        model."""
        fake = {"output": {"message": {"content": [{"text": "..."}]}},
                "usage": {"inputTokens": 1, "outputTokens": 1}}
        with patch.object(agent, "invoke_tool") as invoke, \
                patch.object(agent, "converse_with_tools", return_value=(fake, [], None, [])) as converse:
            agent.handler(_event("ready", tour={"step": 8, "active": False}), None)
        converse.assert_called_once()
        invoke.assert_not_called()


class TourRealRoleBindingTest(unittest.TestCase):
    def test_step_7_compares_real_roles_from_history_not_invented(self):
        """P4: Step 7 compares roles that actually appeared in the tour's
        findings (parsed from history), never invented names."""
        history = [
            {"role": "assistant",
             "content": "| 1 | Admin | HIGH | CloudSecAuditRole | ACTIVE |\n"
                        "| 2 | EKS | MEDIUM | RvSaas-EksKubectlHandlerRole | ACTIVE |"},
        ]
        captured = {}

        def capturing(name, inp):
            if name == "compare_roles":
                captured["roles"] = inp.get("role_names")
            return _fake_tool(name, inp)

        with patch.object(agent, "invoke_tool", side_effect=capturing), \
                patch.object(agent, "converse_with_tools"):
            agent.handler(
                _event_with_history("ready", history, tour={"step": 6, "active": True}), None
            )
        self.assertEqual(captured["roles"],
                         ["CloudSecAuditRole", "RvSaas-EksKubectlHandlerRole"])

    def test_step_2_drills_highest_severity_real_role(self):
        history = [
            {"role": "assistant",
             "content": "findings:\n| 1 | Admin | HIGH | CloudSecAuditRole | ACTIVE |\n"
                        "| 2 | EKS | MEDIUM | RvSaas-EksKubectlHandlerRole | ACTIVE |"},
        ]
        captured = {}

        def capturing(name, inp):
            if name == "get_finding_details":
                captured["role"] = inp.get("role_name")
            return _fake_tool(name, inp)

        with patch.object(agent, "invoke_tool", side_effect=capturing), \
                patch.object(agent, "converse_with_tools"):
            agent.handler(
                _event_with_history("ready", history, tour={"step": 1, "active": True}), None
            )
        self.assertEqual(captured["role"], "CloudSecAuditRole")  # HIGH beats MEDIUM


class TourVsShortCircuitPrecedenceTest(unittest.TestCase):
    def test_active_tour_ready_never_reaches_access_key_shortcircuit(self):
        """With an active tour pointer at step 5, 'ready' advances to Step 6
        via the tour state machine — it must NOT be intercepted by the
        access-key short-circuit (which would run bare, no framing)."""
        with patch.object(agent, "invoke_tool", side_effect=_fake_tool), \
                patch.object(agent, "converse_with_tools") as converse:
            resp = agent.handler(
                _event("ready", tour={"step": 5, "active": True}), None
            )
        converse.assert_not_called()
        body = json.loads(resp["body"])
        # Framed Step 6 from the state machine (not a bare audit).
        self.assertIn("Step 6 of 8", body["response"])
        self.assertEqual(body["tour"]["step"], 6)


if __name__ == "__main__":
    unittest.main()
