"""
Unit and property-based tests for the orchestration agent's
guardrail-violation chat handling and Pcap_Query_Action result
rendering introduced by Task 39 (Reqs 17.10, 17.11, 17.12).

Run from the ``orchestration-agent`` directory::

    python -m pytest test_guardrail_violation_chat.py -v

Scope:

- ``_classify_guardrail_violation`` — maps the Network Agent's
  ``errorCategory`` plus error-message text to one of the three
  Capture_*_Limit guardrail keys, or ``None`` when no guardrail
  matches.
- ``format_guardrail_violation_reply`` — renders the chat reply for
  a Capture_Action rejected by a guardrail. Names the limit using
  the value from the glossary, lists the user's options, and ends
  with a yes/no Clarification_Question (Req 17.11). Falls through
  to a generic refusal for unknown guardrail keys.
- ``format_pcap_query_action_reply`` — renders the chat reply for
  a successful Pcap_Query_Action result set. Includes the source
  ``capture_id`` enclosed in a markdown inline code span, the
  action name in a code span, and a one-sentence interpretation
  generated from a deterministic per-action hint (Req 17.10).
- Per-action interpretation hints — verify the hint produced for
  each of the action-specific helpers (e.g. ``check_tls_hello_size``
  flags potential TLS Client Hello fragmentation when a row's
  ``frame_size > 1400``).
- The ``query_network_pcap`` post-invocation hook — verifies the
  upstream envelope is enriched with ``metadata.uxFormattedText``
  and ``metadata.uxHint`` for both the guardrail-violation case
  and the Pcap_Query_Action result case so the LLM has a
  deterministic chat reply to surface (Req 17.12).
"""

from __future__ import annotations

import json
import os

import pytest
from hypothesis import given, settings, strategies as st

# AgentCore imports a region at module load. Set both env vars before
# importing main so the module loads cleanly outside the AgentCore
# runtime (mirrors the pattern in the other test files in this folder).
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import main  # noqa: E402


# ---------------------------------------------------------------------------
# _classify_guardrail_violation
# ---------------------------------------------------------------------------


class TestClassifyGuardrailViolation:
    """Validates: Requirement 17.11"""

    def test_concurrency_via_error_category(self):
        result = main._classify_guardrail_violation(
            "capture_concurrency_limit",
            "5 active captures already exist",
        )
        assert result == "Capture_Concurrency_Limit"

    def test_concurrency_via_error_message_substring(self):
        result = main._classify_guardrail_violation(
            "invalid_parameter",
            "rejected: Capture_Concurrency_Limit reached (5 active)",
        )
        assert result == "Capture_Concurrency_Limit"

    def test_eni_via_error_category(self):
        result = main._classify_guardrail_violation(
            "capture_eni_limit",
            "asked to mirror 7 ENIs",
        )
        assert result == "Capture_Eni_Limit"

    def test_eni_via_error_message_substring(self):
        result = main._classify_guardrail_violation(
            "invalid_parameter",
            "Capture_Eni_Limit is 3 ENIs per capture",
        )
        assert result == "Capture_Eni_Limit"

    def test_duration_via_error_category(self):
        result = main._classify_guardrail_violation(
            "capture_duration_limit",
            "duration_minutes 90 exceeds the limit",
        )
        assert result == "Capture_Duration_Limit"

    def test_duration_via_error_message_substring(self):
        result = main._classify_guardrail_violation(
            "invalid_parameter",
            "Capture_Duration_Limit is 60 minutes per capture",
        )
        assert result == "Capture_Duration_Limit"

    def test_returns_none_when_no_match(self):
        assert (
            main._classify_guardrail_violation(
                "unauthorized", "missing goat-network-capture-allowed tag"
            )
            is None
        )

    def test_returns_none_when_inputs_are_empty(self):
        assert main._classify_guardrail_violation("", "") is None
        assert main._classify_guardrail_violation(None, None) is None

    def test_concurrency_takes_priority_when_message_mentions_two_limits(self):
        # If the upstream message names both Concurrency and ENI, the
        # classifier should pick the first match it tests for. The
        # current implementation tests Concurrency first so that is
        # the documented stable behaviour.
        result = main._classify_guardrail_violation(
            "invalid_parameter",
            "Capture_Concurrency_Limit and Capture_Eni_Limit both apply",
        )
        assert result == "Capture_Concurrency_Limit"


# ---------------------------------------------------------------------------
# format_guardrail_violation_reply — Capture_Concurrency_Limit
# ---------------------------------------------------------------------------


class TestFormatGuardrailViolationConcurrency:
    """Validates: Requirement 17.11"""

    def test_names_the_limit_with_glossary_value(self):
        reply = main.format_guardrail_violation_reply(
            "Capture_Concurrency_Limit",
            error_text="too many active captures",
            active_capture_count=5,
        )
        assert "Capture_Concurrency_Limit" in reply
        # Glossary value must appear so the user knows the exact number
        # without consulting the docs.
        assert "5" in reply
        assert "simultaneous captures" in reply

    def test_includes_upstream_error_text_verbatim(self):
        reply = main.format_guardrail_violation_reply(
            "Capture_Concurrency_Limit",
            error_text="rejected: 5 active captures already exist",
            active_capture_count=5,
        )
        assert "rejected: 5 active captures already exist" in reply

    def test_lists_user_options(self):
        reply = main.format_guardrail_violation_reply(
            "Capture_Concurrency_Limit",
            error_text=None,
            active_capture_count=5,
        )
        # The reply should mention at least one concrete remediation.
        assert "Stop one of the active captures" in reply
        assert "list captures" in reply

    def test_ends_with_yes_no_clarification_question(self):
        reply = main.format_guardrail_violation_reply(
            "Capture_Concurrency_Limit",
            error_text=None,
            active_capture_count=5,
        )
        assert reply.rstrip().endswith("(yes / no)")

    def test_singular_active_capture_phrasing(self):
        reply = main.format_guardrail_violation_reply(
            "Capture_Concurrency_Limit",
            error_text=None,
            active_capture_count=1,
        )
        assert "1 active capture in" in reply

    def test_plural_active_capture_phrasing(self):
        reply = main.format_guardrail_violation_reply(
            "Capture_Concurrency_Limit",
            error_text=None,
            active_capture_count=3,
        )
        assert "3 active captures in" in reply


# ---------------------------------------------------------------------------
# format_guardrail_violation_reply — Capture_Eni_Limit
# ---------------------------------------------------------------------------


class TestFormatGuardrailViolationEni:
    """Validates: Requirement 17.11"""

    def test_names_the_limit_with_glossary_value(self):
        reply = main.format_guardrail_violation_reply(
            "Capture_Eni_Limit",
            error_text="asked to mirror 7 ENIs",
            eni_count=7,
        )
        assert "Capture_Eni_Limit" in reply
        # Glossary value must appear in the reply.
        assert "3" in reply
        assert "ENIs per capture" in reply

    def test_states_requested_eni_count(self):
        reply = main.format_guardrail_violation_reply(
            "Capture_Eni_Limit",
            error_text=None,
            eni_count=7,
        )
        assert "7 ENIs" in reply

    def test_lists_split_option_referencing_concurrency_limit(self):
        reply = main.format_guardrail_violation_reply(
            "Capture_Eni_Limit",
            error_text=None,
            eni_count=7,
        )
        # The split-into-multiple-captures option must mention the
        # Concurrency limit so the user understands the upper bound on
        # how many parallel captures they can spin up.
        assert "Split the request into multiple captures" in reply
        assert "Capture_Concurrency_Limit" in reply

    def test_ends_with_yes_no_clarification_question(self):
        reply = main.format_guardrail_violation_reply(
            "Capture_Eni_Limit",
            error_text=None,
            eni_count=7,
        )
        assert reply.rstrip().endswith("(yes / no)")


# ---------------------------------------------------------------------------
# format_guardrail_violation_reply — Capture_Duration_Limit
# ---------------------------------------------------------------------------


class TestFormatGuardrailViolationDuration:
    """Validates: Requirement 17.11"""

    def test_names_the_limit_with_glossary_value(self):
        reply = main.format_guardrail_violation_reply(
            "Capture_Duration_Limit",
            error_text="duration_minutes 120 exceeds the limit",
            duration_minutes=120,
        )
        assert "Capture_Duration_Limit" in reply
        # Glossary value must appear.
        assert "60" in reply
        assert "minutes per capture" in reply

    def test_states_requested_duration(self):
        reply = main.format_guardrail_violation_reply(
            "Capture_Duration_Limit",
            error_text=None,
            duration_minutes=120,
        )
        assert "120 minutes" in reply

    def test_lists_lower_duration_option(self):
        reply = main.format_guardrail_violation_reply(
            "Capture_Duration_Limit",
            error_text=None,
            duration_minutes=120,
        )
        assert "Lower the duration" in reply
        # Back-to-back captures option should appear so the user has
        # a way to cover a longer observation window.
        assert "back-to-back captures" in reply

    def test_ends_with_yes_no_clarification_question(self):
        reply = main.format_guardrail_violation_reply(
            "Capture_Duration_Limit",
            error_text=None,
            duration_minutes=120,
        )
        assert reply.rstrip().endswith("(yes / no)")


# ---------------------------------------------------------------------------
# format_guardrail_violation_reply — fall-through path
# ---------------------------------------------------------------------------


class TestFormatGuardrailViolationUnknown:
    """Validates: Requirement 17.11"""

    def test_unknown_guardrail_returns_generic_reply(self):
        reply = main.format_guardrail_violation_reply(
            "Capture_Future_Limit_That_Does_Not_Exist",
            error_text="some upstream error",
        )
        # Generic refusal should still surface the rejection AND the
        # upstream reason so the user is not left guessing.
        assert "rejected by a Network Agent guardrail" in reply
        assert "some upstream error" in reply

    def test_unknown_guardrail_without_error_text_still_returns_reply(self):
        reply = main.format_guardrail_violation_reply(
            "Capture_Bogus_Limit",
        )
        # Even without an upstream error message the reply must still
        # be a non-empty string so the LLM has something to surface.
        assert isinstance(reply, str)
        assert reply.strip() != ""


# ---------------------------------------------------------------------------
# format_pcap_query_action_reply — Req 17.10
# ---------------------------------------------------------------------------


class TestFormatPcapQueryActionReplyShape:
    """Validates: Requirement 17.10"""

    def test_includes_capture_id_in_markdown_inline_code_span(self):
        reply = main.format_pcap_query_action_reply(
            action="check_tls_hello_size",
            capture_id="cap-abc123",
            data={"rows": [{"frame_size": 2048, "fragment_count": 2}]},
        )
        assert "`cap-abc123`" in reply

    def test_includes_action_name_in_code_span(self):
        reply = main.format_pcap_query_action_reply(
            action="check_tls_hello_size",
            capture_id="cap-abc123",
            data={"rows": [{"frame_size": 1500}]},
        )
        assert "`check_tls_hello_size`" in reply

    def test_falls_back_to_placeholder_when_capture_id_missing(self):
        reply = main.format_pcap_query_action_reply(
            action="detect_retransmissions",
            capture_id=None,
            data={"rows": [{"dst_ip": "10.0.0.1", "count": 5}]},
        )
        # No literal None or empty-backtick ``` `` ``` should appear.
        assert "None" not in reply
        assert "`` " not in reply
        # Placeholder phrasing should keep the reply self-contained.
        assert "the source capture" in reply

    def test_includes_upstream_formatted_text_when_supplied(self):
        formatted = "| frame_size | fragment_count |\n|---|---|\n| 1500 | 2 |"
        reply = main.format_pcap_query_action_reply(
            action="check_tls_hello_size",
            capture_id="cap-xyz",
            data={"rows": [{"frame_size": 1500, "fragment_count": 2}]},
            formatted_text=formatted,
        )
        assert formatted in reply


class TestFormatPcapQueryActionReplyInterpretation:
    """Validates: Requirement 17.10 — one-sentence interpretation"""

    def test_check_tls_hello_size_flags_fragmentation_above_1400(self):
        # Req 17.10 example: when frame_size exceeds 1400 bytes, the
        # interpretation should note potential TLS Client Hello
        # fragmentation.
        reply = main.format_pcap_query_action_reply(
            action="check_tls_hello_size",
            capture_id="cap-frag",
            data={"rows": [{"frame_size": 1800, "fragment_count": 1}]},
        )
        # Use lowercase substring search so paraphrase changes don't
        # break the test, but the literal threshold + the word
        # "fragments" must appear together.
        lower = reply.lower()
        assert "1400" in lower
        assert "fragment" in lower
        # The hint should explicitly mention TLS Client Hello.
        assert "tls client hello" in lower

    def test_check_tls_hello_size_does_not_flag_under_threshold(self):
        # Frames at or below 1400 bytes should not trigger the
        # fragmentation interpretation; instead the formatter falls
        # back to a generic interpretation.
        reply = main.format_pcap_query_action_reply(
            action="check_tls_hello_size",
            capture_id="cap-ok",
            data={"rows": [{"frame_size": 1200, "fragment_count": 1}]},
        )
        assert "exceed 1400" not in reply

    def test_detect_retransmissions_highlights_top_destination(self):
        reply = main.format_pcap_query_action_reply(
            action="detect_retransmissions",
            capture_id="cap-rtx",
            data={
                "rows": [
                    {"dst_ip": "10.0.1.5", "dst_port": 443, "retransmission_count": 42},
                    {"dst_ip": "10.0.1.6", "dst_port": 443, "retransmission_count": 7},
                ]
            },
        )
        assert "10.0.1.5:443" in reply
        assert "42 retransmissions" in reply

    def test_classify_tcp_resets_summarises_origins(self):
        reply = main.format_pcap_query_action_reply(
            action="classify_tcp_resets",
            capture_id="cap-rst",
            data={
                "rows": [
                    {"reset_origin_side": "client"},
                    {"reset_origin_side": "client"},
                    {"reset_origin_side": "middlebox"},
                ]
            },
        )
        # The summary should count by origin side.
        assert "2 client" in reply
        assert "1 middlebox" in reply
        assert "TCP RST" in reply

    def test_get_conversation_stats_highlights_top_talker(self):
        reply = main.format_pcap_query_action_reply(
            action="get_conversation_stats",
            capture_id="cap-conv",
            data={
                "rows": [
                    {
                        "source": "10.0.0.1",
                        "destination": "10.0.0.2",
                        "total_bytes": 1024 * 1024,  # 1 MiB
                    }
                ]
            },
        )
        assert "10.0.0.1" in reply
        assert "10.0.0.2" in reply
        # The byte count must use binary units per format_bytes_binary.
        assert "MiB" in reply or "KiB" in reply

    def test_unmapped_action_falls_back_to_generic_row_count(self):
        reply = main.format_pcap_query_action_reply(
            action="reconstruct_tcp_handshake",
            capture_id="cap-hs",
            data={"rows": [{"x": 1}, {"x": 2}, {"x": 3}]},
        )
        # Generic interpretation states how many rows were returned.
        assert "3 rows" in reply

    def test_zero_rows_falls_back_to_safety_message(self):
        # Empty rows are usually handled by the empty-data offer, but
        # the formatter must still produce a non-empty reply when
        # called directly with zero rows (defensive path).
        reply = main.format_pcap_query_action_reply(
            action="reconstruct_tcp_handshake",
            capture_id="cap-empty",
            data={"rows": []},
        )
        assert "No rows returned" in reply

    def test_hint_failure_falls_back_to_generic(self, monkeypatch):
        """A hint that raises must not break the chat reply."""
        def boom(_data):
            raise RuntimeError("hint exploded")

        monkeypatch.setitem(
            main._PCAP_QUERY_INTERPRETATION_HINTS,
            "check_tls_hello_size",
            boom,
        )
        reply = main.format_pcap_query_action_reply(
            action="check_tls_hello_size",
            capture_id="cap-x",
            data={"rows": [{"frame_size": 1500}]},
        )
        # Generic interpretation kicks in.
        assert "1 row returned" in reply


# ---------------------------------------------------------------------------
# Per-action interpretation hints (called directly)
# ---------------------------------------------------------------------------


class TestHintCheckTlsHelloSize:
    """Validates: Requirement 17.10"""

    def test_returns_none_for_empty_rows(self):
        assert main._hint_check_tls_hello_size({"rows": []}) is None

    def test_returns_none_for_non_dict_data(self):
        assert main._hint_check_tls_hello_size(None) is None
        assert main._hint_check_tls_hello_size("not a dict") is None

    def test_flags_frame_size_above_1400(self):
        hint = main._hint_check_tls_hello_size(
            {"rows": [{"frame_size": 1500}, {"frame_size": 2000}]}
        )
        assert hint is not None
        assert "2 TLS Client Hello frames exceed 1400 bytes" in hint

    def test_flags_fragmented_client_hello(self):
        hint = main._hint_check_tls_hello_size(
            {"rows": [{"frame_size": 800, "fragment_count": 3}]}
        )
        assert hint is not None
        assert "fragment_count > 1" in hint

    def test_returns_none_when_only_under_threshold_and_unfragmented(self):
        hint = main._hint_check_tls_hello_size(
            {"rows": [{"frame_size": 800, "fragment_count": 1}]}
        )
        assert hint is None


class TestHintClassifyTcpResets:
    """Validates: Requirement 17.10"""

    def test_summarises_known_origins(self):
        hint = main._hint_classify_tcp_resets(
            {
                "rows": [
                    {"reset_origin_side": "client"},
                    {"reset_origin_side": "server"},
                    {"reset_origin_side": "middlebox"},
                ]
            }
        )
        assert hint is not None
        assert "1 client" in hint
        assert "1 middlebox" in hint
        assert "1 server" in hint

    def test_falls_back_to_count_when_no_origin_field(self):
        hint = main._hint_classify_tcp_resets(
            {"rows": [{"x": 1}, {"x": 2}]}
        )
        assert hint == "2 TCP RST packets observed."


# ---------------------------------------------------------------------------
# query_network_pcap post-invocation hook (Req 17.11 + 17.12)
# ---------------------------------------------------------------------------


class TestQueryNetworkPcapGuardrailHook:
    """Validates: Requirement 17.11 + 17.12"""

    @pytest.fixture(autouse=True)
    def _stub_invoke_and_auth(self, monkeypatch):
        # Bypass the Cognito group gate so the hook is reached for
        # ``start_capture``. The actual authorization tests live in
        # test_capture_authorization.py.
        monkeypatch.setattr(main, "_user_in_group", lambda *a, **kw: True)
        # Avoid touching DynamoDB. The actual state-persistence tests
        # live in test_capture_conversation_context.py.
        monkeypatch.setattr(
            main.state, "load_capture_context", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            main.state, "record_capture_context", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            main.state, "update_capture_context_status", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            main.state, "contains_capture_anaphor", lambda text: False
        )
        monkeypatch.setattr(
            main.state,
            "substitute_persisted_capture_id",
            lambda *a, **kw: (kw.get("params"), False),
        )

    def test_concurrency_violation_envelope_carries_pre_formatted_reply(
        self, monkeypatch
    ):
        upstream = {
            "success": False,
            "domain": "network",
            "error": (
                "rejected: Capture_Concurrency_Limit reached "
                "(5 active captures)"
            ),
            "metadata": {"errorCategory": "capture_concurrency_limit"},
            "data": {"active_capture_count": 5},
        }
        monkeypatch.setattr(
            main, "_invoke_network_agent",
            lambda action, params=None: json.dumps(upstream),
        )
        out = main.query_network_pcap(
            "start_capture",
            {"eni_ids": ["eni-1"], "duration_minutes": 15},
        )
        envelope = json.loads(out)
        meta = envelope["metadata"]
        assert meta.get("uxHint") == "guardrail_violation_offer_remediation"
        chat_reply = meta.get("uxFormattedText")
        assert isinstance(chat_reply, str)
        assert "Capture_Concurrency_Limit" in chat_reply
        # The Network Agent's verbatim error message should be quoted.
        assert "5 active captures" in chat_reply
        assert chat_reply.rstrip().endswith("(yes / no)")

    def test_eni_violation_envelope_carries_pre_formatted_reply(
        self, monkeypatch
    ):
        upstream = {
            "success": False,
            "domain": "network",
            "error": "Capture_Eni_Limit is 3 ENIs per capture; received 7",
            "metadata": {"errorCategory": "invalid_parameter"},
            "data": {},
        }
        monkeypatch.setattr(
            main, "_invoke_network_agent",
            lambda action, params=None: json.dumps(upstream),
        )
        out = main.query_network_pcap(
            "start_capture",
            {
                "eni_ids": [
                    "eni-1", "eni-2", "eni-3", "eni-4",
                    "eni-5", "eni-6", "eni-7",
                ],
                "duration_minutes": 15,
            },
        )
        envelope = json.loads(out)
        meta = envelope["metadata"]
        assert meta.get("uxHint") == "guardrail_violation_offer_remediation"
        chat_reply = meta["uxFormattedText"]
        assert "Capture_Eni_Limit" in chat_reply
        # The orchestration agent passed eni_count=7, so the reply
        # should surface that.
        assert "7 ENIs" in chat_reply

    def test_duration_violation_envelope_carries_pre_formatted_reply(
        self, monkeypatch
    ):
        upstream = {
            "success": False,
            "domain": "network",
            "error": (
                "Capture_Duration_Limit is 60 minutes per capture; "
                "received 120"
            ),
            "metadata": {"errorCategory": "invalid_parameter"},
            "data": {},
        }
        monkeypatch.setattr(
            main, "_invoke_network_agent",
            lambda action, params=None: json.dumps(upstream),
        )
        out = main.query_network_pcap(
            "start_capture",
            {"eni_ids": ["eni-1"], "duration_minutes": 120},
        )
        envelope = json.loads(out)
        meta = envelope["metadata"]
        assert meta.get("uxHint") == "guardrail_violation_offer_remediation"
        chat_reply = meta["uxFormattedText"]
        assert "Capture_Duration_Limit" in chat_reply
        # The user requested 120 minutes; that should appear.
        assert "120 minutes" in chat_reply

    def test_non_guardrail_failure_does_not_emit_uxHint(self, monkeypatch):
        """An unauthorized/opt-in failure must not be misclassified."""
        upstream = {
            "success": False,
            "domain": "network",
            "error": "ENI eni-1 missing goat-network-capture-allowed=true tag",
            "metadata": {"errorCategory": "unauthorized"},
            "data": {},
        }
        monkeypatch.setattr(
            main, "_invoke_network_agent",
            lambda action, params=None: json.dumps(upstream),
        )
        out = main.query_network_pcap(
            "start_capture",
            {"eni_ids": ["eni-1"], "duration_minutes": 15},
        )
        envelope = json.loads(out)
        meta = envelope.get("metadata", {})
        assert meta.get("uxHint") != "guardrail_violation_offer_remediation"


class TestQueryNetworkPcapPcapQueryHook:
    """Validates: Requirement 17.10 + 17.12"""

    @pytest.fixture(autouse=True)
    def _stub_invoke_and_auth(self, monkeypatch):
        monkeypatch.setattr(main, "_user_in_group", lambda *a, **kw: True)
        monkeypatch.setattr(
            main.state, "load_capture_context", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            main.state, "record_capture_context", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            main.state, "update_capture_context_status", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            main.state, "contains_capture_anaphor", lambda text: False
        )
        monkeypatch.setattr(
            main.state,
            "substitute_persisted_capture_id",
            lambda *a, **kw: (kw.get("params"), False),
        )

    def test_pcap_query_action_envelope_carries_pre_formatted_reply(
        self, monkeypatch
    ):
        upstream = {
            "success": True,
            "domain": "network",
            "data": {
                "rows": [
                    {"frame_size": 1800, "fragment_count": 2},
                    {"frame_size": 1500, "fragment_count": 1},
                ]
            },
            "formattedText": "| frame_size | fragment_count |\n|---|---|\n| 1800 | 2 |\n| 1500 | 1 |",
            "metadata": {
                "sourceApi": "athena:StartQueryExecution",
                "queryTimestamp": "2026-04-20T12:34:56Z",
                "dataFreshness": "near-real-time",
            },
        }
        monkeypatch.setattr(
            main, "_invoke_network_agent",
            lambda action, params=None: json.dumps(upstream),
        )
        out = main.query_network_pcap(
            "check_tls_hello_size",
            {"capture_id": "cap-abc123"},
        )
        envelope = json.loads(out)
        meta = envelope["metadata"]
        assert meta.get("uxHint") == "pcap_query_action_result"
        chat_reply = meta.get("uxFormattedText")
        assert isinstance(chat_reply, str)
        # Capture id must appear in a markdown inline code span.
        assert "`cap-abc123`" in chat_reply
        # Action name must appear in a code span.
        assert "`check_tls_hello_size`" in chat_reply
        # The TLS Client Hello fragmentation interpretation should fire.
        assert "1400" in chat_reply

    def test_empty_rows_use_empty_data_path_not_result_path(
        self, monkeypatch
    ):
        """Empty results should offer transform_capture, not the row hint."""
        upstream = {
            "success": True,
            "domain": "network",
            "data": {"rows": []},
            "formattedText": "",
            "metadata": {
                "sourceApi": "athena:StartQueryExecution",
                "queryTimestamp": "2026-04-20T12:34:56Z",
                "dataFreshness": "near-real-time",
            },
        }
        monkeypatch.setattr(
            main, "_invoke_network_agent",
            lambda action, params=None: json.dumps(upstream),
        )
        out = main.query_network_pcap(
            "check_tls_hello_size",
            {"capture_id": "cap-empty"},
        )
        envelope = json.loads(out)
        meta = envelope["metadata"]
        assert meta.get("uxHint") == "pcap_empty_offer_transform_then_retry"
        chat_reply = meta["uxFormattedText"]
        # The empty-data offer ends with a yes/no question to run
        # transform_capture.
        assert "transform_capture" in chat_reply
        assert chat_reply.rstrip().endswith("(yes / no)")


# ---------------------------------------------------------------------------
# System prompt — Req 17.13 (chat-driven workflow descriptions)
# ---------------------------------------------------------------------------


class TestSystemPromptDocumentsGuardrailHandling:
    """Validates: Requirement 17.13"""

    @pytest.fixture(scope="class")
    def prompt(self) -> str:
        return main._build_system_prompt()

    def test_documents_guardrail_violation_handling(self, prompt):
        assert "GUARDRAIL-VIOLATION HANDLING" in prompt
        assert "Capture_Concurrency_Limit" in prompt
        assert "Capture_Eni_Limit" in prompt
        assert "Capture_Duration_Limit" in prompt

    def test_documents_glossary_values_for_each_guardrail(self, prompt):
        # Glossary numeric values must be embedded in the prompt so
        # the LLM has them at hand when paraphrasing the chat reply.
        assert "5 simultaneous captures" in prompt
        assert "3 ENIs per capture" in prompt
        assert "60 minutes per capture" in prompt

    def test_documents_pcap_query_action_result_rendering(self, prompt):
        assert "PCAP_QUERY_ACTION RESULT RENDERING" in prompt
        # The prompt must call out the markdown inline code span
        # requirement so the LLM keeps the capture id and action name
        # rendering stable.
        assert "markdown inline code span" in prompt or "code span" in prompt
        # The TLS Client Hello fragmentation example from Req 17.10
        # should appear.
        assert "TLS Client Hello fragmentation" in prompt or (
            "frame_size" in prompt and "1400" in prompt
        )


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


class TestGuardrailReplyProperties:
    """Universal properties that must hold across all reasonable inputs."""

    @given(
        guardrail=st.sampled_from([
            "Capture_Concurrency_Limit",
            "Capture_Eni_Limit",
            "Capture_Duration_Limit",
        ]),
        eni_count=st.integers(min_value=4, max_value=20),
        duration_minutes=st.integers(min_value=61, max_value=600),
        active_capture_count=st.integers(min_value=5, max_value=50),
    )
    @settings(max_examples=50, deadline=None)
    def test_reply_always_names_the_guardrail_and_ends_with_question(
        self, guardrail, eni_count, duration_minutes, active_capture_count
    ):
        """Validates: Requirement 17.11

        For every guardrail variant and every reasonable user input
        size, the reply must:
        - name the guardrail by its glossary key, and
        - end with a yes/no Clarification_Question.
        """
        reply = main.format_guardrail_violation_reply(
            guardrail,
            error_text="upstream rejection",
            active_capture_count=active_capture_count,
            eni_count=eni_count,
            duration_minutes=duration_minutes,
        )
        assert guardrail in reply
        assert reply.rstrip().endswith("(yes / no)")
        # The reply must not be empty and must include the upstream
        # error text verbatim.
        assert "upstream rejection" in reply

    @given(
        capture_id=st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N"),
                whitelist_characters="-_",
            ),
            min_size=1,
            max_size=64,
        ),
        action=st.sampled_from(list(main.NETWORK_AGENT_ACTIONS)),
        row_count=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=50, deadline=None)
    def test_pcap_query_reply_always_includes_capture_id_and_action_in_code_spans(
        self, capture_id, action, row_count
    ):
        """Validates: Requirement 17.10

        For every Pcap_Query_Action and every well-formed capture id,
        the formatter must:
        - render the capture id inside a markdown inline code span, AND
        - render the action name inside a markdown inline code span.
        """
        rows = [{"frame_size": 1500, "fragment_count": 1, "x": i} for i in range(row_count)]
        reply = main.format_pcap_query_action_reply(
            action=action,
            capture_id=capture_id,
            data={"rows": rows},
        )
        assert f"`{capture_id}`" in reply
        assert f"`{action}`" in reply

    @given(
        guardrail_key=st.text(min_size=1, max_size=64).filter(
            lambda s: s not in {
                "Capture_Concurrency_Limit",
                "Capture_Eni_Limit",
                "Capture_Duration_Limit",
            }
        ),
    )
    @settings(max_examples=50, deadline=None)
    def test_unknown_guardrail_key_never_raises(self, guardrail_key):
        """Validates: Requirement 17.11 — fall-through path

        Even with an arbitrary unknown guardrail key, the formatter
        must produce a non-empty string and must not raise.
        """
        reply = main.format_guardrail_violation_reply(
            guardrail_key,
            error_text="some upstream error",
        )
        assert isinstance(reply, str)
        assert reply.strip() != ""
