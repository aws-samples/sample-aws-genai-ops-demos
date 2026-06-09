"""
Unit and property-based tests for Task 14 Pcap_Query_Action handlers:
``handle_search_fragmented_packets``, ``handle_correlate_tcp_streams``,
``handle_detect_retransmissions``.

Run from the ``network-agent`` directory:

    python -m pytest test_pcap_query_handlers.py -v

These tests stub :func:`main.run_athena_query` with a hand-rolled fake
so we can deterministically verify:

- Validation errors (missing/invalid ``capture_id``,
  missing/invalid ``stream_id``, invalid ``min_size``) never call
  Athena (Reqs 5.4, 5.5, 5.6, 5.7, 5.21).
- The Capture_Id_Predicate is inlined into every executed query
  (Reqs 5.4, 5.6, 5.7, 5.8 — partition pruning).
- ``min_size`` defaults to 1400 when omitted (Req 5.5).
- ``correlate_tcp_streams`` SQL filters on the supplied
  ``stream_id`` and orders by ``frame_time`` ASC (Req 5.6).
- ``detect_retransmissions`` SQL groups by ``(dst_ip, dst_port)``
  and orders by retransmission count DESC (Req 5.8).
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
# Fake run_athena_query (mirrors the helper used in test_query_pcap.py)
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
    """Every accepted query must inline the Capture_Id_Predicate (Req 5.1, 5.7)."""
    assert f"capture_id = '{capture_id}'" in executed_sql, (
        f"Capture_Id_Predicate missing from executed SQL: {executed_sql!r}"
    )


# ---------------------------------------------------------------------------
# search_fragmented_packets tests
# ---------------------------------------------------------------------------


class TestSearchFragmentedPacketsValidation:
    """Reqs 5.4, 5.5, 5.7: parameter validation rejects bad inputs without calling Athena."""

    def test_rejects_missing_capture_id(self, fake_athena):
        response = main.handle_search_fragmented_packets({})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    def test_rejects_empty_capture_id(self, fake_athena):
        response = main.handle_search_fragmented_packets({"capture_id": ""})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    def test_rejects_invalid_capture_id(self, fake_athena):
        response = main.handle_search_fragmented_packets(
            {"capture_id": "bad space"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    def test_rejects_capture_id_too_long(self, fake_athena):
        response = main.handle_search_fragmented_packets(
            {"capture_id": "a" * 129}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    @pytest.mark.parametrize(
        "bad_min_size",
        [
            63,             # below range (Req 5.4)
            65536,          # above range (Req 5.4)
            -1,
            0,
            "1500",         # not an int (string)
            14.5,           # not an int (float)
            True,           # bool is rejected explicitly
        ],
    )
    def test_rejects_invalid_min_size(self, fake_athena, bad_min_size):
        response = main.handle_search_fragmented_packets(
            {"capture_id": "abc-123", "min_size": bad_min_size}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []


class TestSearchFragmentedPacketsHappyPath:
    """Reqs 5.4, 5.5: successful queries inline predicate, default min_size, return rows."""

    def test_default_min_size_is_1400(self, fake_athena):
        """Req 5.5: when min_size is omitted, the default is 1400 bytes."""
        response = main.handle_search_fragmented_packets(
            {"capture_id": "cap-001"}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["min_size"] == 1400
        assert len(fake_athena.calls) == 1
        sql = fake_athena.calls[0]
        _assert_capture_id_predicate(sql, "cap-001")
        assert "frame_size >= 1400" in sql

    def test_custom_min_size_used_in_predicate(self, fake_athena):
        """Req 5.4: supplied min_size is interpolated into the SQL."""
        response = main.handle_search_fragmented_packets(
            {"capture_id": "cap-001", "min_size": 2000}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["min_size"] == 2000
        sql = fake_athena.calls[0]
        assert "frame_size >= 2000" in sql

    def test_min_size_boundary_64_accepted(self, fake_athena):
        """Req 5.4: lower bound 64 is accepted."""
        response = main.handle_search_fragmented_packets(
            {"capture_id": "cap-001", "min_size": 64}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["min_size"] == 64

    def test_min_size_boundary_65535_accepted(self, fake_athena):
        """Req 5.4: upper bound 65535 is accepted."""
        response = main.handle_search_fragmented_packets(
            {"capture_id": "cap-001", "min_size": 65535}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["min_size"] == 65535

    def test_returns_rows_in_data(self, fake_athena):
        fake_athena.rows = [
            {
                "frame_time": "2026-04-20T12:00:00",
                "frame_size": "1500",
                "src_ip": "10.0.0.1",
                "src_port": "443",
                "dst_ip": "10.0.0.2",
                "dst_port": "55432",
                "protocol": "TCP",
                "tcp_stream": "5",
            }
        ]
        response = main.handle_search_fragmented_packets(
            {"capture_id": "cap-001"}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["row_count"] == 1
        assert response["data"]["rows"] == fake_athena.rows
        assert response["data"]["capture_id"] == "cap-001"

    def test_empty_partition_returns_friendly_message(self, fake_athena):
        """Req 5.23: empty result set returns success=true with friendly text."""
        fake_athena.rows = []
        response = main.handle_search_fragmented_packets(
            {"capture_id": "cap-001"}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["row_count"] == 0
        assert response["data"]["rows"] == []
        assert "no matching" in response["formattedText"].lower()


class TestSearchFragmentedPacketsAthenaFailures:
    """Req 5.12: Athena failures produce success=false with no partial results."""

    def test_athena_failure_is_surfaced(self, fake_athena):
        fake_athena.raise_exception = AthenaQueryFailedError(
            "Athena query qid-001 ended in state FAILED.",
            query_execution_id="qid-001",
            athena_state="FAILED",
            state_change_reason="invalid column",
        )
        response = main.handle_search_fragmented_packets(
            {"capture_id": "cap-001"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "athena_query_failed"
        assert response["data"] == {}

    def test_athena_timeout_is_surfaced(self, fake_athena):
        fake_athena.raise_exception = AthenaQueryTimeoutError(
            "Athena query qid-002 did not reach a terminal state within 60s.",
            query_execution_id="qid-002",
            athena_state="RUNNING",
        )
        response = main.handle_search_fragmented_packets(
            {"capture_id": "cap-001"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "athena_timeout"
        assert response["data"] == {}

    def test_configuration_error_is_surfaced(self, fake_athena):
        fake_athena.raise_exception = AthenaConfigurationError(
            "Required environment variable 'GLUE_DATABASE' is not set."
        )
        response = main.handle_search_fragmented_packets(
            {"capture_id": "cap-001"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "configuration_missing"


# ---------------------------------------------------------------------------
# correlate_tcp_streams tests
# ---------------------------------------------------------------------------


class TestCorrelateTcpStreamsValidation:
    """Reqs 5.6, 5.7, 5.21: capture_id and stream_id validated before Athena call."""

    def test_rejects_missing_capture_id(self, fake_athena):
        response = main.handle_correlate_tcp_streams({"stream_id": "5"})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    def test_rejects_invalid_capture_id(self, fake_athena):
        response = main.handle_correlate_tcp_streams(
            {"capture_id": "bad space", "stream_id": "5"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    def test_rejects_missing_stream_id(self, fake_athena):
        response = main.handle_correlate_tcp_streams({"capture_id": "abc"})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    def test_rejects_empty_stream_id(self, fake_athena):
        response = main.handle_correlate_tcp_streams(
            {"capture_id": "abc", "stream_id": ""}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    def test_rejects_stream_id_with_invalid_chars(self, fake_athena):
        response = main.handle_correlate_tcp_streams(
            {"capture_id": "abc", "stream_id": "bad space"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    def test_rejects_stream_id_too_long(self, fake_athena):
        response = main.handle_correlate_tcp_streams(
            {"capture_id": "abc", "stream_id": "a" * 65}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []


class TestCorrelateTcpStreamsHappyPath:
    """Reqs 5.6: SQL includes the capture predicate, stream filter, and ascending order."""

    def test_predicate_and_stream_filter_inlined(self, fake_athena):
        main.handle_correlate_tcp_streams(
            {"capture_id": "cap-001", "stream_id": "42"}
        )
        assert len(fake_athena.calls) == 1
        sql = fake_athena.calls[0]
        _assert_capture_id_predicate(sql, "cap-001")
        assert "tcp_stream = '42'" in sql
        assert "ORDER BY frame_time ASC" in sql

    def test_returns_rows_in_data(self, fake_athena):
        fake_athena.rows = [
            {
                "frame_time": "2026-04-20T12:00:00.001",
                "frame_size": "60",
                "src_ip": "10.0.0.1",
                "src_port": "55432",
                "dst_ip": "10.0.0.2",
                "dst_port": "443",
                "protocol": "TCP",
                "tcp_seq": "1",
                "tcp_ack": "0",
                "tcp_flags": "0x002",
                "tcp_window": "65535",
                "tcp_stream": "42",
                "frame_payload_summary": "SYN",
            },
            {
                "frame_time": "2026-04-20T12:00:00.002",
                "frame_size": "60",
                "src_ip": "10.0.0.2",
                "src_port": "443",
                "dst_ip": "10.0.0.1",
                "dst_port": "55432",
                "protocol": "TCP",
                "tcp_seq": "1",
                "tcp_ack": "2",
                "tcp_flags": "0x012",
                "tcp_window": "65535",
                "tcp_stream": "42",
                "frame_payload_summary": "SYN-ACK",
            },
        ]
        response = main.handle_correlate_tcp_streams(
            {"capture_id": "cap-001", "stream_id": "42"}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["row_count"] == 2
        assert response["data"]["stream_id"] == "42"
        assert response["data"]["capture_id"] == "cap-001"
        assert response["data"]["rows"] == fake_athena.rows

    def test_empty_partition_returns_friendly_message(self, fake_athena):
        """Req 5.23: empty result set returns success=true with friendly text."""
        response = main.handle_correlate_tcp_streams(
            {"capture_id": "cap-001", "stream_id": "999"}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["row_count"] == 0
        assert response["data"]["rows"] == []
        assert "no matching" in response["formattedText"].lower()


class TestCorrelateTcpStreamsAthenaFailures:
    """Req 5.12: Athena failures produce success=false with no partial results."""

    def test_athena_failure_is_surfaced(self, fake_athena):
        fake_athena.raise_exception = AthenaQueryFailedError(
            "Athena query qid-001 ended in state FAILED.",
            query_execution_id="qid-001",
            athena_state="FAILED",
            state_change_reason="invalid column",
        )
        response = main.handle_correlate_tcp_streams(
            {"capture_id": "cap-001", "stream_id": "5"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "athena_query_failed"
        assert response["data"] == {}


# ---------------------------------------------------------------------------
# detect_retransmissions tests
# ---------------------------------------------------------------------------


class TestDetectRetransmissionsValidation:
    """Reqs 5.7: capture_id required and validated before Athena call."""

    def test_rejects_missing_capture_id(self, fake_athena):
        response = main.handle_detect_retransmissions({})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    def test_rejects_invalid_capture_id(self, fake_athena):
        response = main.handle_detect_retransmissions(
            {"capture_id": "bad/slash"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []


class TestDetectRetransmissionsHappyPath:
    """Reqs 5.7, 5.8: SQL groups by destination and orders by retransmission count desc."""

    def test_predicate_and_groupby_inlined(self, fake_athena):
        main.handle_detect_retransmissions({"capture_id": "cap-001"})
        assert len(fake_athena.calls) == 1
        sql = fake_athena.calls[0]
        _assert_capture_id_predicate(sql, "cap-001")
        # Req 5.8: group by destination IP and destination port.
        assert "GROUP BY dst_ip, dst_port" in sql
        # Req 5.8: ordered by retransmission count descending.
        assert "ORDER BY retransmission_count DESC" in sql
        # Uses tshark-derived retransmission column per the design.
        assert "tcp_analysis_retransmission" in sql

    def test_returns_grouped_rows(self, fake_athena):
        fake_athena.rows = [
            {
                "dst_ip": "10.0.0.5",
                "dst_port": "443",
                "retransmission_count": "127",
                "affected_stream_count": "3",
                "first_retransmission_time": "2026-04-20T12:00:00",
                "last_retransmission_time": "2026-04-20T12:14:30",
            },
            {
                "dst_ip": "10.0.0.6",
                "dst_port": "443",
                "retransmission_count": "42",
                "affected_stream_count": "1",
                "first_retransmission_time": "2026-04-20T12:01:00",
                "last_retransmission_time": "2026-04-20T12:10:00",
            },
        ]
        response = main.handle_detect_retransmissions(
            {"capture_id": "cap-001"}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["row_count"] == 2
        assert response["data"]["capture_id"] == "cap-001"
        assert response["data"]["rows"] == fake_athena.rows

    def test_empty_partition_returns_friendly_message(self, fake_athena):
        """Req 5.23: empty result set returns success=true with friendly text."""
        response = main.handle_detect_retransmissions(
            {"capture_id": "cap-001"}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["row_count"] == 0
        assert response["data"]["rows"] == []
        assert "no matching" in response["formattedText"].lower()


class TestDetectRetransmissionsAthenaFailures:
    """Req 5.12: Athena failures produce success=false with no partial results."""

    def test_athena_failure_is_surfaced(self, fake_athena):
        fake_athena.raise_exception = AthenaQueryFailedError(
            "Athena query qid-003 ended in state FAILED.",
            query_execution_id="qid-003",
            athena_state="FAILED",
            state_change_reason="invalid column",
        )
        response = main.handle_detect_retransmissions(
            {"capture_id": "cap-001"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "athena_query_failed"
        assert response["data"] == {}

    def test_athena_timeout_is_surfaced(self, fake_athena):
        fake_athena.raise_exception = AthenaQueryTimeoutError(
            "Athena query qid-004 did not reach a terminal state.",
            query_execution_id="qid-004",
            athena_state="RUNNING",
        )
        response = main.handle_detect_retransmissions(
            {"capture_id": "cap-001"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "athena_timeout"


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


# Capture_Id_Format alphabet for Hypothesis strategies.
_CAPTURE_ID_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789_-"
)


class TestProperties:
    """Property tests covering the three Task 14 handlers.

    These tests assert universal invariants over Hypothesis-generated
    inputs. Each test is annotated with the requirement(s) it
    validates.
    """

    @given(
        capture_id=st.text(
            alphabet=_CAPTURE_ID_ALPHABET, min_size=1, max_size=128
        ),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.function_scoped_fixture,
        ],
    )
    def test_property_search_fragmented_capture_id_predicate_present(
        self, capture_id: str, monkeypatch
    ):
        """Validates: Requirements 5.4, 5.7.

        For every valid ``capture_id``, the SQL forwarded to Athena
        contains the Capture_Id_Predicate.
        """
        fake = FakeAthena(rows=[])
        monkeypatch.setattr(main, "run_athena_query", fake)
        response = main.handle_search_fragmented_packets(
            {"capture_id": capture_id}
        )
        assert response["success"] is True
        assert len(fake.calls) == 1
        _assert_capture_id_predicate(fake.calls[0], capture_id)

    @given(
        capture_id=st.text(
            alphabet=_CAPTURE_ID_ALPHABET, min_size=1, max_size=128
        ),
        min_size=st.integers(min_value=64, max_value=65535),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.function_scoped_fixture,
        ],
    )
    def test_property_search_fragmented_min_size_accepted(
        self, capture_id: str, min_size: int, monkeypatch
    ):
        """Validates: Requirement 5.4.

        Every integer in ``[64, 65535]`` is accepted as ``min_size``
        and inlined into the SQL.
        """
        fake = FakeAthena(rows=[])
        monkeypatch.setattr(main, "run_athena_query", fake)
        response = main.handle_search_fragmented_packets(
            {"capture_id": capture_id, "min_size": min_size}
        )
        assert response["success"] is True
        assert response["data"]["min_size"] == min_size
        assert f"frame_size >= {min_size}" in fake.calls[0]

    @given(
        min_size=st.one_of(
            st.integers(max_value=63),
            st.integers(min_value=65536),
        ),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.function_scoped_fixture,
        ],
    )
    def test_property_search_fragmented_min_size_out_of_range_rejected(
        self, min_size: int, monkeypatch
    ):
        """Validates: Requirement 5.4 (range rejection).

        Every integer outside ``[64, 65535]`` is rejected without an
        Athena call.
        """
        fake = FakeAthena(rows=[])
        monkeypatch.setattr(main, "run_athena_query", fake)
        response = main.handle_search_fragmented_packets(
            {"capture_id": "valid-id", "min_size": min_size}
        )
        assert response["success"] is False
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake.calls == []

    @given(
        capture_id=st.text(
            alphabet=_CAPTURE_ID_ALPHABET, min_size=1, max_size=128
        ),
        stream_id=st.text(
            alphabet=_CAPTURE_ID_ALPHABET, min_size=1, max_size=64
        ),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.function_scoped_fixture,
        ],
    )
    def test_property_correlate_tcp_streams_predicates_present(
        self, capture_id: str, stream_id: str, monkeypatch
    ):
        """Validates: Requirements 5.6, 5.7.

        For every valid ``(capture_id, stream_id)`` pair, the
        executed SQL contains both the Capture_Id_Predicate and the
        ``tcp_stream`` filter.
        """
        fake = FakeAthena(rows=[])
        monkeypatch.setattr(main, "run_athena_query", fake)
        response = main.handle_correlate_tcp_streams(
            {"capture_id": capture_id, "stream_id": stream_id}
        )
        assert response["success"] is True
        sql = fake.calls[0]
        _assert_capture_id_predicate(sql, capture_id)
        assert f"tcp_stream = '{stream_id}'" in sql
        assert "ORDER BY frame_time ASC" in sql

    @given(
        capture_id=st.text(
            alphabet=_CAPTURE_ID_ALPHABET, min_size=1, max_size=128
        ),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.function_scoped_fixture,
        ],
    )
    def test_property_detect_retransmissions_groupby_present(
        self, capture_id: str, monkeypatch
    ):
        """Validates: Requirements 5.7, 5.8.

        For every valid ``capture_id``, the executed SQL groups by
        ``(dst_ip, dst_port)`` and orders by retransmission count
        descending.
        """
        fake = FakeAthena(rows=[])
        monkeypatch.setattr(main, "run_athena_query", fake)
        response = main.handle_detect_retransmissions(
            {"capture_id": capture_id}
        )
        assert response["success"] is True
        sql = fake.calls[0]
        _assert_capture_id_predicate(sql, capture_id)
        assert "GROUP BY dst_ip, dst_port" in sql
        assert "ORDER BY retransmission_count DESC" in sql

    @given(
        sql_returns_rows=st.lists(
            st.dictionaries(
                keys=st.sampled_from(["a", "b", "c"]),
                values=st.text(min_size=0, max_size=20),
                min_size=1,
                max_size=3,
            ),
            min_size=0,
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
    def test_property_uniform_envelope_shape(
        self, sql_returns_rows: List[Dict[str, str]], monkeypatch
    ):
        """Validates: Requirements 5.22, 5.23 (Correctness Property 10).

        Every successful response — regardless of how many rows
        Athena returns — satisfies the universal envelope schema
        with the fixed sourceApi and dataFreshness values.
        """
        fake = FakeAthena(rows=sql_returns_rows)
        monkeypatch.setattr(main, "run_athena_query", fake)
        for params, action in [
            ({"capture_id": "valid-id"}, main.handle_search_fragmented_packets),
            (
                {"capture_id": "valid-id", "stream_id": "5"},
                main.handle_correlate_tcp_streams,
            ),
            ({"capture_id": "valid-id"}, main.handle_detect_retransmissions),
        ]:
            response = action(params)
            _assert_envelope_shape(response, success=True)
            assert response["data"]["row_count"] == len(sql_returns_rows)
