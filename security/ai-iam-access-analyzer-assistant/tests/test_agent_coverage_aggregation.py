"""Tests for the top-level `coverage` aggregation on the conversation response.

Pins the aggregation contract (agent.py `_aggregate_coverage`) and the
end-to-end wiring: every response envelope (short-circuits AND the main
Bedrock loop) surfaces a `coverage` array built from the per-tool
`coverage` entries.

Dedup rule: same (source, state) collapses; different states for the
same source are preserved so the caller can see, e.g., that Security Hub
was reached by one tool but failed for another in the same turn.
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


def _cov(source: str, state: str, count: int | None = None, detail: str = "") -> dict:
    entry = {"source": source, "state": state, "detail": detail}
    if count is not None:
        entry["count"] = count
    return entry


class AggregateCoverageHelperTest(unittest.TestCase):
    def test_returns_empty_when_no_tool_emitted_coverage(self):
        results = [{"findings": []}, {"anything": "else"}]
        self.assertEqual(agent._aggregate_coverage(results), [])

    def test_deduplicates_by_source_and_state(self):
        """Two tools reporting `securityhub: checked` collapse to one entry."""
        results = [
            {"coverage": [_cov("securityhub", "checked", count=10)]},
            {"coverage": [_cov("securityhub", "checked", count=3)]},
        ]
        agg = agent._aggregate_coverage(results)
        self.assertEqual(len(agg), 1)
        self.assertEqual(agg[0]["state"], "checked")

    def test_preserves_different_states_for_same_source(self):
        """`securityhub: checked` + `securityhub: unavailable` -> both kept."""
        results = [
            {"coverage": [_cov("securityhub", "checked")]},
            {"coverage": [_cov("securityhub", "unavailable", detail="denied")]},
        ]
        agg = agent._aggregate_coverage(results)
        self.assertEqual(len(agg), 2)
        states = sorted(entry["state"] for entry in agg)
        self.assertEqual(states, ["checked", "unavailable"])

    def test_preserves_different_sources(self):
        """iam + cloudtrail + s3 all keep their own entries."""
        results = [
            {"coverage": [
                _cov("iam", "checked"),
                _cov("cloudtrail", "checked"),
            ]},
            {"coverage": [_cov("s3", "empty", count=0)]},
        ]
        agg = agent._aggregate_coverage(results)
        sources = sorted(entry["source"] for entry in agg)
        self.assertEqual(sources, ["cloudtrail", "iam", "s3"])

    def test_ignores_malformed_entries(self):
        results = [
            {"coverage": "not a list"},
            {"coverage": [None, "not a dict", {"source": "iam", "state": "checked"}]},
        ]
        agg = agent._aggregate_coverage(results)
        self.assertEqual(len(agg), 1)
        self.assertEqual(agg[0]["source"], "iam")

    def test_handles_non_dict_results(self):
        results = ["string result", None, 42, {"coverage": [_cov("s3", "checked")]}]
        agg = agent._aggregate_coverage(results)
        self.assertEqual(len(agg), 1)


class ShortCircuitCoverageWiredTest(unittest.TestCase):
    """Every short-circuit path must include `coverage` in its response."""

    def _event(self, message: str, pagination=None) -> dict:
        body = {"message": message}
        if pagination is not None:
            body["pagination"] = pagination
        return {"httpMethod": "POST", "body": json.dumps(body)}

    def _body(self, response) -> dict:
        return json.loads(response["body"])

    def test_action_plan_short_circuit_emits_coverage(self):
        tool_result = {
            "action_plan": [],
            "summary": {"total_findings": 0, "message": "clean"},
            "quick_wins": [],
            "risk_distribution": {},
            "coverage": [_cov("securityhub", "empty", count=0)],
        }
        with patch.object(agent, "invoke_tool", return_value=tool_result):
            response = agent.handler(self._event("generate an action plan"), None)

        body = self._body(response)
        self.assertIn("coverage", body)
        self.assertEqual(body["coverage"][0]["source"], "securityhub")
        self.assertEqual(body["coverage"][0]["state"], "empty")

    def test_pagination_short_circuit_emits_coverage(self):
        pagination = {
            "tool": "list_findings",
            "last_input": {"status": "ACTIVE", "limit": 10},
            "next_token": "next-page",
        }
        tool_result = {
            "findings": [],
            "returned_count": 0,
            "total_matching": 0,
            "has_more": False,
            "coverage": [_cov("securityhub", "empty", count=0)],
        }
        with patch.object(agent, "invoke_tool", return_value=tool_result):
            response = agent.handler(
                self._event("next 10", pagination=pagination), None
            )

        body = self._body(response)
        self.assertIn("coverage", body)
        self.assertEqual(body["coverage"][0]["source"], "securityhub")

    def test_action_plan_error_still_emits_coverage(self):
        tool_result = {
            "error": "Security Hub not enabled",
            "coverage": [_cov("securityhub", "unavailable", detail="denied")],
        }
        with patch.object(agent, "invoke_tool", return_value=tool_result):
            response = agent.handler(self._event("draft an action plan"), None)

        body = self._body(response)
        self.assertIn("coverage", body)
        self.assertEqual(body["coverage"][0]["state"], "unavailable")


class MainBedrockLoopCoverageTest(unittest.TestCase):
    """The main Bedrock path also surfaces aggregated coverage."""

    def test_returns_coverage_from_converse_with_tools(self):
        fake_bedrock = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "Sure."}],
                }
            },
            "usage": {"inputTokens": 10, "outputTokens": 20},
        }
        aggregated_coverage = [
            _cov("securityhub", "checked", count=5),
            _cov("iam", "checked"),
        ]
        # 4-tuple: (response, tool_calls_made, pagination, coverage)
        with patch.object(
            agent,
            "converse_with_tools",
            return_value=(fake_bedrock, [], None, aggregated_coverage),
        ):
            response = agent.handler(
                {
                    "httpMethod": "POST",
                    "body": json.dumps({"message": "list my findings"}),
                },
                None,
            )

        body = json.loads(response["body"])
        self.assertEqual(body["coverage"], aggregated_coverage)


class AnsweredFromPriorContextTest(unittest.TestCase):
    """#183: an empty `coverage` is ambiguous — "nothing was ever checked"
    (dangerous, possibly fabricated) vs. "I reused data a tool already
    checked in an earlier turn of this same conversation" (safe, but the
    #171 signal is otherwise lost). `answered_from_prior_context` makes the
    second case structurally explicit instead of relying solely on the
    model's prose to say so.
    """

    def _fake_bedrock_response(self, text: str) -> dict:
        return {
            "output": {
                "message": {"role": "assistant", "content": [{"text": text}]},
            },
            "usage": {"inputTokens": 10, "outputTokens": 20},
        }

    def _handler_with_mocked_turn(self, message: str, history: list, tool_calls_made, coverage):
        with patch.object(
            agent,
            "converse_with_tools",
            return_value=(
                self._fake_bedrock_response("Sure, from the list I already fetched: ..."),
                tool_calls_made,
                None,
                coverage,
            ),
        ):
            event = {
                "httpMethod": "POST",
                "body": json.dumps({"message": message, "history": history}),
            }
            return json.loads(agent.handler(event, None)["body"])

    def test_flagged_true_when_no_tools_called_but_prior_history_exists(self):
        """Recitation: this turn called no tools, coverage is empty, but
        there IS prior conversation history — the classic #183 scenario
        ("which of those roles are unused?" after an earlier list_findings
        turn)."""
        history = [
            {"role": "user", "content": "what are my active IAM findings?"},
            {"role": "assistant", "content": "Here are 12 unused roles: ..."},
        ]
        body = self._handler_with_mocked_turn(
            "which of those are unused?", history, tool_calls_made=[], coverage=[]
        )
        self.assertTrue(body["answered_from_prior_context"])

    def test_not_flagged_on_the_very_first_turn(self):
        """No prior history at all -- an empty coverage here is NOT a
        recitation (there is nothing to recite from); it is just a model
        response with no tool call, e.g. a greeting or a scope question."""
        body = self._handler_with_mocked_turn(
            "what can you help me with?", history=[], tool_calls_made=[], coverage=[]
        )
        self.assertFalse(body["answered_from_prior_context"])

    def test_not_flagged_when_a_tool_was_actually_called(self):
        """Tools were called this turn (even if their coverage happens to be
        empty, e.g. an `empty` state entry got filtered) -- this is a fresh
        check, not a recitation, so the flag must stay False."""
        history = [
            {"role": "user", "content": "what are my active IAM findings?"},
            {"role": "assistant", "content": "Here are 12 unused roles: ..."},
        ]
        body = self._handler_with_mocked_turn(
            "check again",
            history,
            tool_calls_made=[{"tool": "list_findings", "input_summary": "status: ACTIVE"}],
            coverage=[],
        )
        self.assertFalse(body["answered_from_prior_context"])

    def test_not_flagged_when_coverage_is_genuinely_populated(self):
        """Tools ran and returned real coverage -- the normal, non-ambiguous
        case. Flag must be False; the populated `coverage` array is itself
        the signal."""
        history = [
            {"role": "user", "content": "what are my active IAM findings?"},
        ]
        body = self._handler_with_mocked_turn(
            "what are my active IAM findings?",
            history,
            tool_calls_made=[{"tool": "list_findings", "input_summary": "status: ACTIVE"}],
            coverage=[_cov("securityhub", "checked", count=12)],
        )
        self.assertFalse(body["answered_from_prior_context"])


if __name__ == "__main__":
    unittest.main()
