"""
Unit and property-based tests for Task 16 Pcap_Query_Action handlers:
``handle_reconstruct_tcp_handshake``, ``handle_classify_tcp_resets``,
``handle_detect_out_of_order_packets``, ``handle_detect_zero_window``,
``handle_analyze_tcp_options``, ``handle_get_rtt_distribution``,
``handle_get_request_response_latency``.

Run from the ``network-agent`` directory:

    python -m pytest test_tcp_analysis_handlers.py -v

These tests stub :func:`main.run_athena_query` with a hand-rolled fake
so we can deterministically verify:

- Validation errors (missing/invalid ``capture_id``,
  missing/invalid ``stream_id`` for actions that require it) never
  call Athena (Reqs 5.20, 5.21, 5.26).
- The Capture_Id_Predicate is inlined into every executed query
  (Reqs 5.20, 5.22 — partition pruning).
- ``stream_id`` is enforced for ``reconstruct_tcp_handshake``,
  ``analyze_tcp_options``, ``get_request_response_latency`` per Req 5.26.
- ``stream_id`` is *optional* for ``classify_tcp_resets`` and
  ``get_rtt_distribution`` per Reqs 5.14, 5.18.
- ``reconstruct_tcp_handshake`` post-processing returns the closed
  enumeration values for ``handshake_failure_reason`` per Req 5.13.
- Athena failures (``AthenaQueryFailedError``,
  ``AthenaQueryTimeoutError``, ``AthenaConfigurationError``)
  produce ``success=false`` envelopes with the correct
  ``errorCategory`` and no partial results (Req 5.12).
- Empty result sets produce ``success=true`` with a friendly
  ``formattedText`` (Req 5.23).
- Every response carries
  ``metadata.sourceApi="athena:StartQueryExecution"`` and
  ``metadata.dataFreshness="near-real-time"`` (Req 5.22).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

import main
from athena_helper import (
    AthenaConfigurationError,
    AthenaQueryFailedError,
    AthenaQueryTimeoutError,
)


# ---------------------------------------------------------------------------
# Fake run_athena_query (mirrors the helper used in test_pcap_query_handlers.py)
# ---------------------------------------------------------------------------


class FakeAthena:
    """Records the SQL passed to ``run_athena_query`` and returns a canned response."""

    def __init__(
        self,
        rows: Optional[List[Dict[str, Any]]] = None,
        raise_exception: Optional[Exception] = None,
    ) -> None:
        self.rows = list(rows or [])
        self.raise_exception = raise_exception
        self.calls: List[str] = []

    def __call__(
        self,
        sql: str,
        work_group: Optional[str] = None,
        output_location: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        self.calls.append(sql)
        if self.raise_exception is not None:
            raise self.raise_exception
        return [dict(r) for r in self.rows]


@pytest.fixture
def fake_athena(monkeypatch):
    """Install a FakeAthena that returns no rows by default."""
    fake = FakeAthena(rows=[])
    monkeypatch.setattr(main, "run_athena_query", fake)
    return fake


# ---------------------------------------------------------------------------
# Shared envelope assertions
# ---------------------------------------------------------------------------


def _assert_envelope_shape(response: Dict[str, Any], *, success: bool) -> None:
    """Check the response satisfies the universal envelope schema (Req 5.22)."""
    assert response["success"] is success
    assert response["domain"] == "network"
    assert isinstance(response["data"], dict)
    assert isinstance(response["formattedText"], str)
    metadata = response["metadata"]
    # Req 5.22 — every Pcap_Query_Action sets these literal values.
    assert metadata["sourceApi"] == "athena:StartQueryExecution"
    assert metadata["dataFreshness"] == "near-real-time"
    assert isinstance(metadata["queryTimestamp"], str)
    if not success:
        assert "error" in response
        assert isinstance(response["error"], str)
        assert response["error"]
        assert "errorCategory" in metadata


def _assert_capture_id_predicate(executed_sql: str, capture_id: str) -> None:
    """Every accepted query must inline the Capture_Id_Predicate (Req 5.20)."""
    assert f"capture_id = '{capture_id}'" in executed_sql, (
        f"Capture_Id_Predicate missing from executed SQL: {executed_sql!r}"
    )


def _assert_stream_id_predicate(executed_sql: str, stream_id: str) -> None:
    """Stream-scoped queries must inline ``tcp_stream = '<stream_id>'``."""
    assert f"tcp_stream = '{stream_id}'" in executed_sql, (
        f"Stream_Id predicate missing from executed SQL: {executed_sql!r}"
    )


# Capture_Id_Format / Stream_Id alphabet for Hypothesis strategies.
_CAPTURE_ID_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789_-"
)


# Handlers grouped by whether ``stream_id`` is required, optional, or
# absent from their parameter contract. Driven by Reqs 5.13-5.21 and
# 5.26.
_HANDLERS_REQUIRING_STREAM_ID = [
    main.handle_reconstruct_tcp_handshake,   # Req 5.13
    main.handle_analyze_tcp_options,         # Req 5.17
    main.handle_get_request_response_latency,  # Req 5.19
]

_HANDLERS_WITH_OPTIONAL_STREAM_ID = [
    main.handle_classify_tcp_resets,         # Req 5.14
    main.handle_get_rtt_distribution,        # Req 5.18
]

_HANDLERS_WITHOUT_STREAM_ID = [
    main.handle_detect_out_of_order_packets,  # Req 5.15
    main.handle_detect_zero_window,           # Req 5.16
]

_ALL_TASK_16_HANDLERS = (
    _HANDLERS_REQUIRING_STREAM_ID
    + _HANDLERS_WITH_OPTIONAL_STREAM_ID
    + _HANDLERS_WITHOUT_STREAM_ID
)


# ---------------------------------------------------------------------------
# Validation tests (Req 5.20, 5.21, 5.26)
# ---------------------------------------------------------------------------


class TestCaptureIdValidation:
    """Req 5.20: every Task 16 handler enforces capture_id before any Athena call."""

    @pytest.mark.parametrize("handler", _ALL_TASK_16_HANDLERS)
    def test_rejects_missing_capture_id(self, fake_athena, handler):
        # Stream_id-required handlers also need stream_id; supply a
        # valid one so the failure mode under test is the missing
        # capture_id.
        params = {"stream_id": "42"}
        response = handler(params)
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    @pytest.mark.parametrize("handler", _ALL_TASK_16_HANDLERS)
    def test_rejects_empty_capture_id(self, fake_athena, handler):
        response = handler({"capture_id": "", "stream_id": "42"})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    @pytest.mark.parametrize("handler", _ALL_TASK_16_HANDLERS)
    def test_rejects_invalid_capture_id_chars(self, fake_athena, handler):
        # Space is outside the Capture_Id_Format alphabet.
        response = handler({"capture_id": "bad space", "stream_id": "42"})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    @pytest.mark.parametrize("handler", _ALL_TASK_16_HANDLERS)
    def test_rejects_capture_id_too_long(self, fake_athena, handler):
        response = handler({"capture_id": "a" * 129, "stream_id": "42"})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    @pytest.mark.parametrize("handler", _ALL_TASK_16_HANDLERS)
    def test_rejects_non_string_capture_id(self, fake_athena, handler):
        response = handler({"capture_id": 12345, "stream_id": "42"})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    @pytest.mark.parametrize("handler", _ALL_TASK_16_HANDLERS)
    def test_accepts_non_dict_params(self, fake_athena, handler):
        # Non-dict params should be coerced to {} which then fails
        # validation for the missing capture_id.
        response = handler("not a dict")  # type: ignore[arg-type]
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []


class TestStreamIdValidation:
    """Reqs 5.21, 5.26: stream_id required for some handlers, validated for all."""

    @pytest.mark.parametrize("handler", _HANDLERS_REQUIRING_STREAM_ID)
    def test_required_stream_id_missing_rejected(self, fake_athena, handler):
        """Req 5.26: handlers requiring stream_id reject when it's missing."""
        response = handler({"capture_id": "valid-id"})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    @pytest.mark.parametrize("handler", _HANDLERS_REQUIRING_STREAM_ID)
    def test_required_stream_id_empty_rejected(self, fake_athena, handler):
        response = handler({"capture_id": "valid-id", "stream_id": ""})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    @pytest.mark.parametrize("handler", _HANDLERS_REQUIRING_STREAM_ID)
    def test_required_stream_id_invalid_chars_rejected(self, fake_athena, handler):
        response = handler(
            {"capture_id": "valid-id", "stream_id": "bad space"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    @pytest.mark.parametrize("handler", _HANDLERS_REQUIRING_STREAM_ID)
    def test_required_stream_id_too_long_rejected(self, fake_athena, handler):
        response = handler(
            {"capture_id": "valid-id", "stream_id": "a" * 65}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    @pytest.mark.parametrize("handler", _HANDLERS_WITH_OPTIONAL_STREAM_ID)
    def test_optional_stream_id_omitted_accepted(self, fake_athena, handler):
        """Reqs 5.14, 5.18: stream_id is optional for these handlers."""
        response = handler({"capture_id": "valid-id"})
        _assert_envelope_shape(response, success=True)
        assert len(fake_athena.calls) == 1

    @pytest.mark.parametrize("handler", _HANDLERS_WITH_OPTIONAL_STREAM_ID)
    def test_optional_stream_id_invalid_rejected(self, fake_athena, handler):
        """Req 5.21: when stream_id is supplied, it must be valid."""
        response = handler(
            {"capture_id": "valid-id", "stream_id": "bad space"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []


# ---------------------------------------------------------------------------
# reconstruct_tcp_handshake-specific tests
# ---------------------------------------------------------------------------


class TestReconstructTcpHandshakeSql:
    """Req 5.13: SQL filters to handshake frames with required projections."""

    def test_predicate_and_stream_filter_inlined(self, fake_athena):
        main.handle_reconstruct_tcp_handshake(
            {"capture_id": "cap-001", "stream_id": "42"}
        )
        assert len(fake_athena.calls) == 1
        sql = fake_athena.calls[0]
        _assert_capture_id_predicate(sql, "cap-001")
        _assert_stream_id_predicate(sql, "42")

    def test_required_projection_columns_present(self, fake_athena):
        """Req 5.13: SELECT projects frame_time, direction, seq_number,
        ack_number, tcp_flags, tcp_options_summary."""
        main.handle_reconstruct_tcp_handshake(
            {"capture_id": "cap-001", "stream_id": "42"}
        )
        sql = fake_athena.calls[0]
        assert "frame_time" in sql
        assert "AS direction" in sql
        assert "AS seq_number" in sql
        assert "AS ack_number" in sql
        assert "tcp_flags" in sql
        assert "AS tcp_options_summary" in sql


class TestReconstructTcpHandshakeClassification:
    """Req 5.13: handshake_failure_reason in {syn_ack_missing,
    final_ack_missing, syn_retransmitted, complete, not_observed}."""

    def test_empty_partition_returns_not_observed(self, fake_athena):
        """Req 5.23 + Req 5.13: empty result set => not_observed."""
        response = main.handle_reconstruct_tcp_handshake(
            {"capture_id": "cap-001", "stream_id": "42"}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["handshake_complete"] is False
        assert response["data"]["handshake_duration_ms"] is None
        assert response["data"]["handshake_failure_reason"] == "not_observed"
        assert response["data"]["row_count"] == 0

    def test_complete_handshake_classified(self, fake_athena):
        """3 frames: SYN, SYN+ACK, ACK -> complete, duration computed."""
        fake_athena.rows = [
            {
                "frame_time": "2026-05-21 18:00:00.000000",
                "direction": "client_to_server",
                "seq_number": "1000",
                "ack_number": "0",
                "tcp_flags": "0x002",  # SYN
                "tcp_options_summary": "MSS=1460,WS=7",
            },
            {
                "frame_time": "2026-05-21 18:00:00.050000",
                "direction": "server_to_client",
                "seq_number": "2000",
                "ack_number": "1001",
                "tcp_flags": "0x012",  # SYN+ACK
                "tcp_options_summary": "MSS=1460,WS=7",
            },
            {
                "frame_time": "2026-05-21 18:00:00.100000",
                "direction": "client_to_server",
                "seq_number": "1001",
                "ack_number": "2001",
                "tcp_flags": "0x010",  # ACK
                "tcp_options_summary": "",
            },
        ]
        response = main.handle_reconstruct_tcp_handshake(
            {"capture_id": "cap-001", "stream_id": "42"}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["handshake_complete"] is True
        assert response["data"]["handshake_failure_reason"] == "complete"
        # 100 ms between SYN and final ACK.
        assert abs(response["data"]["handshake_duration_ms"] - 100.0) < 0.1

    def test_syn_ack_missing_classified(self, fake_athena):
        """SYN seen but no SYN+ACK -> syn_ack_missing."""
        fake_athena.rows = [
            {
                "frame_time": "2026-05-21 18:00:00.000000",
                "direction": "client_to_server",
                "seq_number": "1000",
                "ack_number": "0",
                "tcp_flags": "0x002",  # SYN
                "tcp_options_summary": "MSS=1460",
            },
        ]
        response = main.handle_reconstruct_tcp_handshake(
            {"capture_id": "cap-001", "stream_id": "42"}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["handshake_complete"] is False
        assert response["data"]["handshake_duration_ms"] is None
        assert (
            response["data"]["handshake_failure_reason"] == "syn_ack_missing"
        )

    def test_final_ack_missing_classified(self, fake_athena):
        """SYN + SYN+ACK seen but no final ACK -> final_ack_missing."""
        fake_athena.rows = [
            {
                "frame_time": "2026-05-21 18:00:00.000000",
                "direction": "client_to_server",
                "seq_number": "1000",
                "ack_number": "0",
                "tcp_flags": "0x002",  # SYN
                "tcp_options_summary": "MSS=1460",
            },
            {
                "frame_time": "2026-05-21 18:00:00.050000",
                "direction": "server_to_client",
                "seq_number": "2000",
                "ack_number": "1001",
                "tcp_flags": "0x012",  # SYN+ACK
                "tcp_options_summary": "MSS=1460",
            },
        ]
        response = main.handle_reconstruct_tcp_handshake(
            {"capture_id": "cap-001", "stream_id": "42"}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["handshake_complete"] is False
        assert response["data"]["handshake_duration_ms"] is None
        assert (
            response["data"]["handshake_failure_reason"]
            == "final_ack_missing"
        )

    def test_syn_retransmitted_classified(self, fake_athena):
        """Two SYNs followed by a successful handshake -> syn_retransmitted."""
        fake_athena.rows = [
            {
                "frame_time": "2026-05-21 18:00:00.000000",
                "direction": "client_to_server",
                "seq_number": "1000",
                "ack_number": "0",
                "tcp_flags": "0x002",  # SYN #1
                "tcp_options_summary": "MSS=1460",
            },
            {
                "frame_time": "2026-05-21 18:00:01.000000",
                "direction": "client_to_server",
                "seq_number": "1000",
                "ack_number": "0",
                "tcp_flags": "0x002",  # SYN retransmit
                "tcp_options_summary": "MSS=1460",
            },
            {
                "frame_time": "2026-05-21 18:00:01.050000",
                "direction": "server_to_client",
                "seq_number": "2000",
                "ack_number": "1001",
                "tcp_flags": "0x012",  # SYN+ACK
                "tcp_options_summary": "MSS=1460",
            },
            {
                "frame_time": "2026-05-21 18:00:01.100000",
                "direction": "client_to_server",
                "seq_number": "1001",
                "ack_number": "2001",
                "tcp_flags": "0x010",  # ACK
                "tcp_options_summary": "",
            },
        ]
        response = main.handle_reconstruct_tcp_handshake(
            {"capture_id": "cap-001", "stream_id": "42"}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["handshake_complete"] is True
        assert (
            response["data"]["handshake_failure_reason"]
            == "syn_retransmitted"
        )
        # Duration computed from the *first* SYN.
        assert response["data"]["handshake_duration_ms"] is not None
        assert (
            abs(response["data"]["handshake_duration_ms"] - 1100.0) < 0.1
        )


# ---------------------------------------------------------------------------
# classify_tcp_resets-specific tests (Req 5.14)
# ---------------------------------------------------------------------------


class TestClassifyTcpResetsSql:
    """Req 5.14: SQL projects per-RST columns with reset_origin_side classification."""

    def test_predicate_inlined_no_stream_filter(self, fake_athena):
        main.handle_classify_tcp_resets({"capture_id": "cap-001"})
        sql = fake_athena.calls[0]
        _assert_capture_id_predicate(sql, "cap-001")
        # No tcp_stream filter when stream_id omitted.
        assert "tcp_stream = '" not in sql

    def test_stream_filter_inlined_when_supplied(self, fake_athena):
        main.handle_classify_tcp_resets(
            {"capture_id": "cap-001", "stream_id": "42"}
        )
        sql = fake_athena.calls[0]
        _assert_capture_id_predicate(sql, "cap-001")
        _assert_stream_id_predicate(sql, "42")

    def test_required_projection_columns_present(self, fake_athena):
        """Req 5.14: SELECT projects required columns."""
        main.handle_classify_tcp_resets({"capture_id": "cap-001"})
        sql = fake_athena.calls[0]
        # All Req 5.14 column aliases.
        assert "AS stream_id" in sql
        assert "AS source_ip" in sql
        assert "AS source_port" in sql
        assert "AS destination_ip" in sql
        assert "AS destination_port" in sql
        assert "AS reset_origin_side" in sql
        assert "AS seq_number" in sql
        assert "preceded_by_fin" in sql

    def test_reset_origin_side_classification_in_sql(self, fake_athena):
        """Req 5.14: reset_origin_side must produce values from
        the Reset_Origin_Side enumeration {client, server, middlebox, unknown}."""
        main.handle_classify_tcp_resets({"capture_id": "cap-001"})
        sql = fake_athena.calls[0]
        # Each enum value must appear as a literal in the CASE
        # expression so Athena can produce it.
        assert "'client'" in sql
        assert "'server'" in sql
        assert "'middlebox'" in sql
        assert "'unknown'" in sql


# ---------------------------------------------------------------------------
# detect_out_of_order_packets-specific tests (Req 5.15)
# ---------------------------------------------------------------------------


class TestDetectOutOfOrderPacketsSql:
    """Req 5.15: per-stream out-of-order/duplicate-ACK aggregates."""

    def test_predicate_inlined(self, fake_athena):
        main.handle_detect_out_of_order_packets({"capture_id": "cap-001"})
        sql = fake_athena.calls[0]
        _assert_capture_id_predicate(sql, "cap-001")

    def test_required_aggregates_projected(self, fake_athena):
        """Req 5.15: stream_id, out_of_order_count, duplicate_ack_count,
        dsack_count, fast_retransmit_count."""
        main.handle_detect_out_of_order_packets({"capture_id": "cap-001"})
        sql = fake_athena.calls[0]
        assert "AS stream_id" in sql
        assert "AS out_of_order_count" in sql
        assert "AS duplicate_ack_count" in sql
        assert "AS dsack_count" in sql
        assert "AS fast_retransmit_count" in sql

    def test_grouped_by_stream(self, fake_athena):
        main.handle_detect_out_of_order_packets({"capture_id": "cap-001"})
        sql = fake_athena.calls[0]
        assert "GROUP BY tcp_stream" in sql

    def test_ordered_by_sum_descending(self, fake_athena):
        """Req 5.15: ordered by ``out_of_order_count + duplicate_ack_count`` descending."""
        main.handle_detect_out_of_order_packets({"capture_id": "cap-001"})
        sql = fake_athena.calls[0]
        # Trino ORDER BY of an arithmetic expression with DESC.
        assert "DESC" in sql
        assert "tcp_analysis_out_of_order" in sql
        assert "tcp_analysis_duplicate_ack" in sql


# ---------------------------------------------------------------------------
# detect_zero_window-specific tests (Req 5.16)
# ---------------------------------------------------------------------------


class TestDetectZeroWindowSql:
    """Req 5.16: per-stream zero-window aggregates with stall duration."""

    def test_predicate_inlined(self, fake_athena):
        main.handle_detect_zero_window({"capture_id": "cap-001"})
        sql = fake_athena.calls[0]
        _assert_capture_id_predicate(sql, "cap-001")

    def test_required_aggregates_projected(self, fake_athena):
        """Req 5.16: stream_id, zero_window_event_count,
        zero_window_total_duration_ms, window_full_event_count,
        window_update_event_count."""
        main.handle_detect_zero_window({"capture_id": "cap-001"})
        sql = fake_athena.calls[0]
        assert "AS stream_id" in sql
        assert "AS zero_window_event_count" in sql
        assert "AS zero_window_total_duration_ms" in sql
        assert "AS window_full_event_count" in sql
        assert "AS window_update_event_count" in sql

    def test_grouped_by_stream(self, fake_athena):
        main.handle_detect_zero_window({"capture_id": "cap-001"})
        sql = fake_athena.calls[0]
        assert "GROUP BY tcp_stream" in sql

    def test_ordered_by_total_duration_desc(self, fake_athena):
        """Req 5.16: ordered by zero_window_total_duration_ms desc."""
        main.handle_detect_zero_window({"capture_id": "cap-001"})
        sql = fake_athena.calls[0]
        assert "ORDER BY zero_window_total_duration_ms DESC" in sql


# ---------------------------------------------------------------------------
# analyze_tcp_options-specific tests (Req 5.17)
# ---------------------------------------------------------------------------


class TestAnalyzeTcpOptionsSql:
    """Req 5.17: per-direction MSS/WS/SACK/timestamps + mss_effective_min."""

    def test_predicate_and_stream_filter_inlined(self, fake_athena):
        main.handle_analyze_tcp_options(
            {"capture_id": "cap-001", "stream_id": "42"}
        )
        sql = fake_athena.calls[0]
        _assert_capture_id_predicate(sql, "cap-001")
        _assert_stream_id_predicate(sql, "42")

    def test_required_projections_present(self, fake_athena):
        """Req 5.17: direction, mss_advertised, window_scale,
        sack_permitted, timestamps_enabled, mss_effective_min."""
        main.handle_analyze_tcp_options(
            {"capture_id": "cap-001", "stream_id": "42"}
        )
        sql = fake_athena.calls[0]
        assert "direction" in sql
        assert "mss_advertised" in sql
        assert "window_scale" in sql
        assert "sack_permitted" in sql
        assert "timestamps_enabled" in sql
        assert "mss_effective_min" in sql

    def test_grouped_by_direction(self, fake_athena):
        """Req 5.17: 'per direction' aggregation."""
        main.handle_analyze_tcp_options(
            {"capture_id": "cap-001", "stream_id": "42"}
        )
        sql = fake_athena.calls[0]
        assert "GROUP BY direction" in sql

    def test_mss_parsed_from_options_array(self, fake_athena):
        """``MSS=`` entries must be parsed from the tcp_options array."""
        main.handle_analyze_tcp_options(
            {"capture_id": "cap-001", "stream_id": "42"}
        )
        sql = fake_athena.calls[0]
        # We should be parsing 'MSS=...' tokens.
        assert "MSS=" in sql


# ---------------------------------------------------------------------------
# get_rtt_distribution-specific tests (Req 5.18)
# ---------------------------------------------------------------------------


class TestGetRttDistributionSql:
    """Req 5.18: per-stream RTT min/p50/p95/max/sample_count."""

    def test_predicate_inlined_no_stream_filter(self, fake_athena):
        main.handle_get_rtt_distribution({"capture_id": "cap-001"})
        sql = fake_athena.calls[0]
        _assert_capture_id_predicate(sql, "cap-001")
        assert "tcp_stream = '" not in sql

    def test_stream_filter_inlined_when_supplied(self, fake_athena):
        main.handle_get_rtt_distribution(
            {"capture_id": "cap-001", "stream_id": "42"}
        )
        sql = fake_athena.calls[0]
        _assert_capture_id_predicate(sql, "cap-001")
        _assert_stream_id_predicate(sql, "42")

    def test_required_aggregates_projected(self, fake_athena):
        """Req 5.18: stream_id, rtt_min_ms, rtt_p50_ms, rtt_p95_ms,
        rtt_max_ms, sample_count."""
        main.handle_get_rtt_distribution({"capture_id": "cap-001"})
        sql = fake_athena.calls[0]
        assert "AS stream_id" in sql
        assert "AS rtt_min_ms" in sql
        assert "AS rtt_p50_ms" in sql
        assert "AS rtt_p95_ms" in sql
        assert "AS rtt_max_ms" in sql
        assert "AS sample_count" in sql

    def test_uses_approx_percentile(self, fake_athena):
        """Trino's ``approx_percentile`` is used for p50/p95."""
        main.handle_get_rtt_distribution({"capture_id": "cap-001"})
        sql = fake_athena.calls[0]
        assert "approx_percentile" in sql
        assert "0.50" in sql
        assert "0.95" in sql


# ---------------------------------------------------------------------------
# get_request_response_latency-specific tests (Req 5.19)
# ---------------------------------------------------------------------------


class TestGetRequestResponseLatencySql:
    """Req 5.19: per-pair request/response latency metrics."""

    def test_predicate_and_stream_filter_inlined(self, fake_athena):
        main.handle_get_request_response_latency(
            {"capture_id": "cap-001", "stream_id": "42"}
        )
        sql = fake_athena.calls[0]
        _assert_capture_id_predicate(sql, "cap-001")
        _assert_stream_id_predicate(sql, "42")

    def test_required_projections_present(self, fake_athena):
        """Req 5.19: request_frame_time, request_size_bytes,
        time_to_first_response_byte_ms, time_to_full_response_ms,
        response_size_bytes."""
        main.handle_get_request_response_latency(
            {"capture_id": "cap-001", "stream_id": "42"}
        )
        sql = fake_athena.calls[0]
        assert "request_frame_time" in sql
        assert "request_size_bytes" in sql
        assert "time_to_first_response_byte_ms" in sql
        assert "time_to_full_response_ms" in sql
        assert "response_size_bytes" in sql


# ---------------------------------------------------------------------------
# Athena failure path tests (Req 5.12)
# ---------------------------------------------------------------------------


class TestAthenaFailurePropagation:
    """Req 5.12: Athena failures produce success=false with no partial results."""

    @pytest.mark.parametrize("handler", _ALL_TASK_16_HANDLERS)
    def test_athena_failure_is_surfaced(self, monkeypatch, handler):
        fake = FakeAthena(
            raise_exception=AthenaQueryFailedError(
                "Athena query qid-T16 ended in state FAILED.",
                query_execution_id="qid-T16",
                athena_state="FAILED",
                state_change_reason="invalid column",
            )
        )
        monkeypatch.setattr(main, "run_athena_query", fake)
        params = {"capture_id": "cap-001", "stream_id": "42"}
        response = handler(params)
        _assert_envelope_shape(response, success=False)
        assert (
            response["metadata"]["errorCategory"] == "athena_query_failed"
        )
        # Req 5.12: no partial results.
        assert response["data"] == {}

    @pytest.mark.parametrize("handler", _ALL_TASK_16_HANDLERS)
    def test_athena_timeout_is_surfaced(self, monkeypatch, handler):
        fake = FakeAthena(
            raise_exception=AthenaQueryTimeoutError(
                "Athena query qid-T16 did not reach a terminal state.",
                query_execution_id="qid-T16",
                athena_state="RUNNING",
            )
        )
        monkeypatch.setattr(main, "run_athena_query", fake)
        params = {"capture_id": "cap-001", "stream_id": "42"}
        response = handler(params)
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "athena_timeout"
        assert response["data"] == {}

    @pytest.mark.parametrize("handler", _ALL_TASK_16_HANDLERS)
    def test_configuration_error_is_surfaced(self, monkeypatch, handler):
        fake = FakeAthena(
            raise_exception=AthenaConfigurationError(
                "Required environment variable 'GLUE_DATABASE' is not set."
            )
        )
        monkeypatch.setattr(main, "run_athena_query", fake)
        params = {"capture_id": "cap-001", "stream_id": "42"}
        response = handler(params)
        _assert_envelope_shape(response, success=False)
        assert (
            response["metadata"]["errorCategory"] == "configuration_missing"
        )


# ---------------------------------------------------------------------------
# Empty partition tests (Req 5.23)
# ---------------------------------------------------------------------------


class TestEmptyPartition:
    """Req 5.23: empty partition returns success=true with friendly text."""

    @pytest.mark.parametrize(
        "handler",
        # reconstruct_tcp_handshake handles empty separately via the
        # not_observed path tested above, but it still meets the
        # contract.
        _ALL_TASK_16_HANDLERS,
    )
    def test_empty_partition_is_success(self, fake_athena, handler):
        params = {"capture_id": "cap-001", "stream_id": "42"}
        response = handler(params)
        _assert_envelope_shape(response, success=True)
        # The friendly text varies between handlers but should
        # always indicate "no matching" or be a handshake-specific
        # status message.
        formatted = response["formattedText"].lower()
        # Either a generic "no matching" message (for handlers using
        # _execute_pcap_query) or a not_observed handshake message
        # (for reconstruct_tcp_handshake).
        assert (
            "no matching" in formatted
            or "not_observed" in formatted
            or "no traffic" in formatted
        )


# ---------------------------------------------------------------------------
# Property-based tests (Reqs 5.20, 5.21, 5.22, 5.23)
# ---------------------------------------------------------------------------


class TestProperties:
    """Property tests covering the seven Task 16 handlers."""

    @given(
        capture_id=st.text(
            alphabet=_CAPTURE_ID_ALPHABET, min_size=1, max_size=128
        ),
    )
    @settings(
        max_examples=50,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.function_scoped_fixture,
        ],
    )
    @pytest.mark.parametrize(
        "handler",
        _HANDLERS_WITHOUT_STREAM_ID,
    )
    def test_property_capture_id_predicate_present_no_stream(
        self, capture_id: str, handler, monkeypatch
    ):
        """Validates: Requirements 5.20, 5.22.

        For every valid ``capture_id`` (with no ``stream_id`` since
        these handlers do not accept one), the SQL forwarded to
        Athena contains the Capture_Id_Predicate and the response
        envelope satisfies the universal schema.
        """
        fake = FakeAthena(rows=[])
        monkeypatch.setattr(main, "run_athena_query", fake)
        response = handler({"capture_id": capture_id})
        _assert_envelope_shape(response, success=True)
        assert len(fake.calls) == 1
        _assert_capture_id_predicate(fake.calls[0], capture_id)

    @given(
        capture_id=st.text(
            alphabet=_CAPTURE_ID_ALPHABET, min_size=1, max_size=128
        ),
        stream_id=st.text(
            alphabet=_CAPTURE_ID_ALPHABET, min_size=1, max_size=64
        ),
    )
    @settings(
        max_examples=50,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.function_scoped_fixture,
        ],
    )
    @pytest.mark.parametrize(
        "handler",
        _HANDLERS_REQUIRING_STREAM_ID,
    )
    def test_property_predicates_present_required_stream(
        self, capture_id: str, stream_id: str, handler, monkeypatch
    ):
        """Validates: Requirements 5.20, 5.21, 5.22, 5.26.

        For every valid ``(capture_id, stream_id)`` pair, the SQL
        forwarded to Athena contains both predicates and the
        response envelope satisfies the universal schema.
        """
        fake = FakeAthena(rows=[])
        monkeypatch.setattr(main, "run_athena_query", fake)
        response = handler(
            {"capture_id": capture_id, "stream_id": stream_id}
        )
        _assert_envelope_shape(response, success=True)
        assert len(fake.calls) == 1
        _assert_capture_id_predicate(fake.calls[0], capture_id)
        _assert_stream_id_predicate(fake.calls[0], stream_id)

    @given(
        bad_chars=st.text(
            alphabet=" /;'\"\t\n\\<>",
            min_size=1,
            max_size=10,
        ),
    )
    @settings(
        max_examples=50,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.function_scoped_fixture,
        ],
    )
    @pytest.mark.parametrize(
        "handler",
        _ALL_TASK_16_HANDLERS,
    )
    def test_property_invalid_capture_id_never_calls_athena(
        self, bad_chars: str, handler, monkeypatch
    ):
        """Validates: Requirement 5.20.

        Any capture_id containing characters outside Capture_Id_Format
        is rejected before Athena is called.
        """
        fake = FakeAthena(rows=[])
        monkeypatch.setattr(main, "run_athena_query", fake)
        # Embed bad_chars somewhere in the capture_id so it's a string
        # but not Capture_Id_Format-conformant.
        bad_capture_id = f"valid{bad_chars}id"
        response = handler(
            {"capture_id": bad_capture_id, "stream_id": "42"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake.calls == []

    @given(
        capture_id=st.text(
            alphabet=_CAPTURE_ID_ALPHABET, min_size=1, max_size=128
        ),
        n_rows=st.integers(min_value=0, max_value=5),
    )
    @settings(
        max_examples=30,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.function_scoped_fixture,
        ],
    )
    @pytest.mark.parametrize(
        "handler",
        _HANDLERS_WITHOUT_STREAM_ID,
    )
    def test_property_uniform_envelope_shape(
        self, capture_id: str, n_rows: int, handler, monkeypatch
    ):
        """Validates: Requirements 5.22, 5.23 (Correctness Property 10).

        Every successful response — regardless of how many rows
        Athena returns — satisfies the universal envelope schema
        with the fixed sourceApi and dataFreshness values.
        """
        rows = [
            {"stream_id": str(i), "out_of_order_count": str(i * 2)}
            for i in range(n_rows)
        ]
        fake = FakeAthena(rows=rows)
        monkeypatch.setattr(main, "run_athena_query", fake)
        response = handler({"capture_id": capture_id})
        _assert_envelope_shape(response, success=True)
        assert response["data"]["row_count"] == n_rows
