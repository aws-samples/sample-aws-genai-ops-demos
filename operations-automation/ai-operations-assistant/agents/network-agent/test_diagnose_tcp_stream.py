"""
Unit and property-based tests for Task 18 Pcap_Query_Action:
``handle_diagnose_tcp_stream``.

Run from the ``network-agent`` directory:

    python -m pytest test_diagnose_tcp_stream.py -v

These tests stub :func:`main.run_athena_query` with a routing fake
that returns canned rows based on SQL substrings, so we can
deterministically verify:

- Validation errors (missing ``capture_id``, missing both
  ``stream_id`` and ``flow_selector``, invalid ``capture_id``,
  invalid ``stream_id``) never call Athena (Reqs 18.5, 18.14).
- The Tcp_Stream_Health_Report contains exactly the keys mandated
  by Req 18.2 — no missing keys, no extra keys.
- Anomaly classification (Req 18.3) fires correctly for each
  triggering condition (handshake failure, slow handshake,
  reset by client/server/middlebox, excessive retransmissions,
  spurious retransmissions, out-of-order packets, duplicate ACKs,
  zero-window stalls, MSS clamping mismatch, TLS Client Hello
  fragmentation), and a single ``none`` entry when no rule fires.
- ``mss_clamping_mismatch`` is ``True`` when
  ``mss_effective_min < 0.8 * mss_advertised`` (Req 18.2).
- Empty partition (Req 18.6): all numerics zero, single ``none``
  anomaly, ``success=True``, formattedText says no traffic observed.
- Partial Athena failure (Req 18.7): affected sub-objects ``null``,
  ``none`` anomaly listing unavailable sections, ``success=True``.
- Multi-stream flow_selector resolution returns up to 20 reports
  (Req 18.13).
- formattedText sections appear in the order mandated by Req 18.4
  (handshake, connection close, RTT, retransmissions, out-of-order,
  zero-window, TCP options, MSS clamping, anomalies).
- Every Tcp_Anomaly_Category in ``anomalies[].category`` belongs
  to the closed enumeration (property-based; Req 18.2/18.3).
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
# Tcp_Anomaly_Category closed enumeration (requirements glossary, Req 18.3).
# ---------------------------------------------------------------------------

_VALID_ANOMALY_CATEGORIES = frozenset(
    {
        "handshake_failed",
        "handshake_slow",
        "connection_reset_by_client",
        "connection_reset_by_server",
        "connection_reset_by_middlebox",
        "idle_timeout_close",
        "excessive_retransmissions",
        "spurious_retransmissions",
        "out_of_order_packets",
        "duplicate_acks",
        "zero_window_stall",
        "mss_clamping_mismatch",
        "tls_client_hello_fragmented",
        "none",
    }
)

_REPORT_KEYS = {
    "stream_id",
    "client_endpoint",
    "server_endpoint",
    "handshake",
    "connection_close",
    "rtt",
    "retransmissions",
    "out_of_order",
    "zero_window",
    "tcp_options",
    "mss_clamping_mismatch",
    "anomalies",
}


# ---------------------------------------------------------------------------
# Routing FakeAthena — dispatches canned rows based on SQL substrings.
# ---------------------------------------------------------------------------


class RoutingFakeAthena:
    """Routes ``run_athena_query`` calls to canned responses by SQL substring.

    The diagnose handler invokes seven sub-handlers, plus side queries
    for total packet count, TLS fragmentation probe, and the
    flow_selector ranking aggregate. Each query has a distinctive
    SQL fragment that identifies it. The routing dictionary maps a
    substring to either a list of rows or an exception to raise.
    """

    def __init__(
        self,
        routes: Optional[List] = None,
    ) -> None:
        # ``routes`` is a list of ``(substring, response)`` tuples
        # where ``response`` is either a list of dicts or an
        # exception instance to raise. Order matters: the first
        # matching substring wins.
        self.routes = list(routes or [])
        self.calls: List[str] = []

    def add(self, substring: str, response):
        self.routes.append((substring, response))

    def __call__(
        self,
        sql: str,
        work_group: Optional[str] = None,
        output_location: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        self.calls.append(sql)
        for substring, response in self.routes:
            if substring in sql:
                if isinstance(response, Exception):
                    raise response
                return [dict(r) for r in response]
        # Default: empty.
        return []


@pytest.fixture
def fake_athena(monkeypatch):
    """Install a RoutingFakeAthena with no routes (every query empty)."""
    fake = RoutingFakeAthena()
    monkeypatch.setattr(main, "run_athena_query", fake)
    return fake


# ---------------------------------------------------------------------------
# Shared envelope and shape assertions
# ---------------------------------------------------------------------------


def _assert_envelope_shape(response: Dict[str, Any], *, success: bool) -> None:
    """Check the response satisfies the universal envelope schema (Req 5.22)."""
    assert response["success"] is success
    assert response["domain"] == "network"
    assert isinstance(response["data"], dict)
    assert isinstance(response["formattedText"], str)
    metadata = response["metadata"]
    assert metadata["sourceApi"] == "athena:StartQueryExecution"
    assert metadata["dataFreshness"] == "near-real-time"
    assert isinstance(metadata["queryTimestamp"], str)
    if not success:
        assert "error" in response
        assert isinstance(response["error"], str)
        assert response["error"]
        assert "errorCategory" in metadata


def _assert_report_keys(report: Dict[str, Any]) -> None:
    """Every Tcp_Stream_Health_Report contains exactly the Req 18.2 keys."""
    assert set(report.keys()) == _REPORT_KEYS, (
        f"Report keys differ from Req 18.2: "
        f"missing={_REPORT_KEYS - set(report.keys())}, "
        f"extra={set(report.keys()) - _REPORT_KEYS}"
    )


def _assert_anomaly_categories_valid(report: Dict[str, Any]) -> None:
    """Every category in ``anomalies[].category`` is in the closed enum."""
    for entry in report["anomalies"]:
        assert entry["category"] in _VALID_ANOMALY_CATEGORIES, (
            f"Invalid anomaly category: {entry['category']!r}"
        )
        assert isinstance(entry["description"], str)
        assert entry["description"]


def _extract_report(data: Dict[str, Any]) -> Dict[str, Any]:
    """Pick the report dict out of the response ``data`` field.

    Single-stream responses inline the report keys directly into
    ``data`` (alongside ``capture_id``). Multi-stream responses surface
    a list under ``data.reports``.
    """
    if "reports" in data:
        return data["reports"][0]
    return {key: data[key] for key in _REPORT_KEYS}


# ---------------------------------------------------------------------------
# Validation tests (Reqs 18.5, 18.14)
# ---------------------------------------------------------------------------


class TestValidation:
    """Reqs 18.5, 18.14: reject before any Athena call."""

    def test_rejects_missing_capture_id(self, fake_athena):
        response = main.handle_diagnose_tcp_stream({"stream_id": "42"})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    def test_rejects_invalid_capture_id(self, fake_athena):
        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "bad space", "stream_id": "42"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    def test_rejects_invalid_stream_id(self, fake_athena):
        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "cap1", "stream_id": "bad space"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    def test_rejects_missing_stream_id_and_flow_selector(self, fake_athena):
        # Req 18.14: either stream_id or flow_selector required.
        response = main.handle_diagnose_tcp_stream({"capture_id": "cap1"})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert "stream_id" in response["error"]
        assert "flow_selector" in response["error"]
        assert fake_athena.calls == []


# ---------------------------------------------------------------------------
# Empty-partition path (Req 18.6)
# ---------------------------------------------------------------------------


class TestEmptyPartition:
    """Req 18.6: empty partition -> success=True, zeros, single none anomaly."""

    def test_empty_partition_zero_packets(self, fake_athena):
        # The total-packet probe returns 0 -> empty-partition path.
        fake_athena.add(
            "COUNT(*) AS packet_count",
            [{"packet_count": "0", "byte_count": "0"}],
        )
        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "cap1", "stream_id": "42"}
        )
        _assert_envelope_shape(response, success=True)
        report = _extract_report(response["data"])
        _assert_report_keys(report)
        _assert_anomaly_categories_valid(report)

        # All numerics zero
        assert report["rtt"]["sample_count"] == 0
        assert report["retransmissions"]["total_count"] == 0
        assert report["out_of_order"]["out_of_order_count"] == 0
        assert report["zero_window"]["event_count"] == 0
        assert report["mss_clamping_mismatch"] is False

        # Exactly one anomaly, category "none", description mentions traffic.
        assert len(report["anomalies"]) == 1
        assert report["anomalies"][0]["category"] == "none"
        assert "traffic" in report["anomalies"][0]["description"].lower()

        # formattedText should mention "no traffic"
        assert "no traffic" in response["formattedText"].lower()


# ---------------------------------------------------------------------------
# Anomaly classification rules (Req 18.3)
# ---------------------------------------------------------------------------


class TestAnomalyClassification:
    """Req 18.3: each rule fires under the documented condition."""

    def _baseline_routes(self, fake_athena, packet_total: int = 100):
        """Add a 'normal' baseline that produces no anomalies.

        The total-packet probe returns ``packet_total`` (non-zero so
        we don't hit the empty-partition path). Each sub-handler
        returns a row that produces zero anomalies.
        """
        # Ranking query (optional — not used in stream_id-only mode).
        fake_athena.add(
            "byte_count "
            "FROM pcap_logs",
            [{"packet_count": str(packet_total), "byte_count": "10000"}],
        )

    def test_handshake_failed(self, fake_athena):
        self._baseline_routes(fake_athena)
        # reconstruct_tcp_handshake projects SYN/SYN-ACK/ACK rows.
        # An empty result -> _classify_handshake returns
        # failure_reason="not_observed" with complete=False; that
        # specific case is handled differently — handshake_failed
        # only fires when reason != not_observed. Provide a SYN-only
        # row so reason becomes "syn_ack_missing".
        fake_athena.add(
            "ORDER BY frame_time ASC",
            [
                {
                    "frame_time": "2026-05-21 18:00:00.000000",
                    "direction": "client_to_server",
                    "seq_number": "1",
                    "ack_number": "0",
                    "tcp_flags": "0x02",
                    "tcp_options_summary": "",
                    "tcp_stream": "42",
                }
            ],
        )

        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "cap1", "stream_id": "42"}
        )
        _assert_envelope_shape(response, success=True)
        report = _extract_report(response["data"])
        categories = {a["category"] for a in report["anomalies"]}
        assert "handshake_failed" in categories

    def test_handshake_slow(self, fake_athena):
        self._baseline_routes(fake_athena)
        # SYN at t0, SYN-ACK at t0+0.6s, plain ACK at t0+0.7s.
        # Duration = 700 ms > 500 ms threshold.
        fake_athena.add(
            "ORDER BY frame_time ASC",
            [
                {
                    "frame_time": "2026-05-21 18:00:00.000000",
                    "direction": "client_to_server",
                    "seq_number": "1",
                    "ack_number": "0",
                    "tcp_flags": "0x02",
                    "tcp_options_summary": "",
                    "tcp_stream": "42",
                },
                {
                    "frame_time": "2026-05-21 18:00:00.600000",
                    "direction": "server_to_client",
                    "seq_number": "100",
                    "ack_number": "2",
                    "tcp_flags": "0x12",
                    "tcp_options_summary": "",
                    "tcp_stream": "42",
                },
                {
                    "frame_time": "2026-05-21 18:00:00.700000",
                    "direction": "client_to_server",
                    "seq_number": "2",
                    "ack_number": "101",
                    "tcp_flags": "0x10",
                    "tcp_options_summary": "",
                    "tcp_stream": "42",
                },
            ],
        )

        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "cap1", "stream_id": "42"}
        )
        _assert_envelope_shape(response, success=True)
        report = _extract_report(response["data"])
        categories = {a["category"] for a in report["anomalies"]}
        assert "handshake_slow" in categories

    def test_reset_by_client(self, fake_athena):
        self._baseline_routes(fake_athena)
        fake_athena.add(
            "AS reset_origin_side",
            [
                {
                    "frame_time": "2026-05-21 18:00:01.000000",
                    "stream_id": "42",
                    "source_ip": "10.0.0.1",
                    "source_port": "12345",
                    "destination_ip": "10.0.0.2",
                    "destination_port": "443",
                    "seq_number": "1",
                    "reset_origin_side": "client",
                    "preceded_by_fin": False,
                }
            ],
        )

        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "cap1", "stream_id": "42"}
        )
        _assert_envelope_shape(response, success=True)
        report = _extract_report(response["data"])
        categories = {a["category"] for a in report["anomalies"]}
        assert "connection_reset_by_client" in categories
        assert report["connection_close"]["state"] == "rst_observed"
        assert report["connection_close"]["reset_origin_side"] == "client"

    def test_reset_by_server(self, fake_athena):
        self._baseline_routes(fake_athena)
        fake_athena.add(
            "AS reset_origin_side",
            [
                {
                    "frame_time": "2026-05-21 18:00:01.000000",
                    "stream_id": "42",
                    "source_ip": "10.0.0.2",
                    "source_port": "443",
                    "destination_ip": "10.0.0.1",
                    "destination_port": "12345",
                    "seq_number": "1",
                    "reset_origin_side": "server",
                    "preceded_by_fin": False,
                }
            ],
        )
        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "cap1", "stream_id": "42"}
        )
        report = _extract_report(response["data"])
        categories = {a["category"] for a in report["anomalies"]}
        assert "connection_reset_by_server" in categories

    def test_reset_by_middlebox(self, fake_athena):
        self._baseline_routes(fake_athena)
        fake_athena.add(
            "AS reset_origin_side",
            [
                {
                    "frame_time": "2026-05-21 18:00:01.000000",
                    "stream_id": "42",
                    "source_ip": "10.99.99.99",
                    "source_port": "443",
                    "destination_ip": "10.0.0.1",
                    "destination_port": "12345",
                    "seq_number": "1",
                    "reset_origin_side": "middlebox",
                    "preceded_by_fin": False,
                }
            ],
        )
        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "cap1", "stream_id": "42"}
        )
        report = _extract_report(response["data"])
        categories = {a["category"] for a in report["anomalies"]}
        assert "connection_reset_by_middlebox" in categories

    def test_excessive_retransmissions(self, fake_athena):
        # 100 total packets, 10 fast retransmits -> 10% > 5% threshold.
        self._baseline_routes(fake_athena, packet_total=100)
        fake_athena.add(
            "fast_retransmit_count",
            [
                {
                    "stream_id": "42",
                    "out_of_order_count": "0",
                    "duplicate_ack_count": "0",
                    "dsack_count": "0",
                    "fast_retransmit_count": "10",
                }
            ],
        )
        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "cap1", "stream_id": "42"}
        )
        report = _extract_report(response["data"])
        categories = {a["category"] for a in report["anomalies"]}
        assert "excessive_retransmissions" in categories

    def test_spurious_retransmissions(self, fake_athena):
        # DSACK > 0 -> spurious_retransmissions fires.
        self._baseline_routes(fake_athena, packet_total=100)
        fake_athena.add(
            "fast_retransmit_count",
            [
                {
                    "stream_id": "42",
                    "out_of_order_count": "0",
                    "duplicate_ack_count": "0",
                    "dsack_count": "3",
                    "fast_retransmit_count": "0",
                }
            ],
        )
        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "cap1", "stream_id": "42"}
        )
        report = _extract_report(response["data"])
        categories = {a["category"] for a in report["anomalies"]}
        assert "spurious_retransmissions" in categories

    def test_out_of_order_packets(self, fake_athena):
        # 100 packets, 5 OOO -> 5% > 1% threshold.
        self._baseline_routes(fake_athena, packet_total=100)
        fake_athena.add(
            "fast_retransmit_count",
            [
                {
                    "stream_id": "42",
                    "out_of_order_count": "5",
                    "duplicate_ack_count": "0",
                    "dsack_count": "0",
                    "fast_retransmit_count": "0",
                }
            ],
        )
        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "cap1", "stream_id": "42"}
        )
        report = _extract_report(response["data"])
        categories = {a["category"] for a in report["anomalies"]}
        assert "out_of_order_packets" in categories

    def test_duplicate_acks(self, fake_athena):
        # > 5 duplicate ACKs.
        self._baseline_routes(fake_athena, packet_total=100)
        fake_athena.add(
            "fast_retransmit_count",
            [
                {
                    "stream_id": "42",
                    "out_of_order_count": "0",
                    "duplicate_ack_count": "10",
                    "dsack_count": "0",
                    "fast_retransmit_count": "0",
                }
            ],
        )
        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "cap1", "stream_id": "42"}
        )
        report = _extract_report(response["data"])
        categories = {a["category"] for a in report["anomalies"]}
        assert "duplicate_acks" in categories

    def test_zero_window_stall(self, fake_athena):
        # > 100 ms total stall.
        self._baseline_routes(fake_athena, packet_total=100)
        fake_athena.add(
            "zero_window_total_duration_ms",
            [
                {
                    "stream_id": "42",
                    "zero_window_event_count": "3",
                    "zero_window_total_duration_ms": "250.0",
                    "window_full_event_count": "0",
                    "window_update_event_count": "0",
                }
            ],
        )
        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "cap1", "stream_id": "42"}
        )
        report = _extract_report(response["data"])
        categories = {a["category"] for a in report["anomalies"]}
        assert "zero_window_stall" in categories

    def test_mss_clamping_mismatch(self, fake_athena):
        # mss_effective_min (1000) < 0.8 * mss_advertised (1460) = 1168.
        self._baseline_routes(fake_athena, packet_total=100)
        fake_athena.add(
            "BOOL_OR(sack_permitted)",
            [
                {
                    "direction": "client_to_server",
                    "mss_advertised": "1460",
                    "window_scale": "7",
                    "sack_permitted": "true",
                    "timestamps_enabled": "true",
                    "mss_effective_min": "1000",
                }
            ],
        )
        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "cap1", "stream_id": "42"}
        )
        report = _extract_report(response["data"])
        categories = {a["category"] for a in report["anomalies"]}
        assert "mss_clamping_mismatch" in categories
        assert report["mss_clamping_mismatch"] is True

    def test_mss_clamping_no_mismatch(self, fake_athena):
        # mss_effective_min (1400) >= 0.8 * mss_advertised (1460) = 1168.
        self._baseline_routes(fake_athena, packet_total=100)
        fake_athena.add(
            "BOOL_OR(sack_permitted)",
            [
                {
                    "direction": "client_to_server",
                    "mss_advertised": "1460",
                    "window_scale": "7",
                    "sack_permitted": "true",
                    "timestamps_enabled": "true",
                    "mss_effective_min": "1400",
                }
            ],
        )
        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "cap1", "stream_id": "42"}
        )
        report = _extract_report(response["data"])
        assert report["mss_clamping_mismatch"] is False
        categories = {a["category"] for a in report["anomalies"]}
        assert "mss_clamping_mismatch" not in categories

    def test_tls_client_hello_fragmented(self, fake_athena):
        # max_fragments > 1 -> tls_client_hello_fragmented fires.
        self._baseline_routes(fake_athena, packet_total=100)
        fake_athena.add(
            "MAX(tls_fragment_count) AS max_fragments",
            [{"max_fragments": "3"}],
        )
        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "cap1", "stream_id": "42"}
        )
        report = _extract_report(response["data"])
        categories = {a["category"] for a in report["anomalies"]}
        assert "tls_client_hello_fragmented" in categories

    def test_no_anomalies_emits_single_none(self, fake_athena):
        # All sub-handlers return baseline rows that trigger no rules.
        self._baseline_routes(fake_athena, packet_total=100)
        # Complete handshake within 50ms (well under 500ms threshold).
        fake_athena.add(
            "ORDER BY frame_time ASC",
            [
                {
                    "frame_time": "2026-05-21 18:00:00.000000",
                    "direction": "client_to_server",
                    "seq_number": "1",
                    "ack_number": "0",
                    "tcp_flags": "0x02",
                    "tcp_options_summary": "",
                    "tcp_stream": "42",
                },
                {
                    "frame_time": "2026-05-21 18:00:00.020000",
                    "direction": "server_to_client",
                    "seq_number": "100",
                    "ack_number": "2",
                    "tcp_flags": "0x12",
                    "tcp_options_summary": "",
                    "tcp_stream": "42",
                },
                {
                    "frame_time": "2026-05-21 18:00:00.040000",
                    "direction": "client_to_server",
                    "seq_number": "2",
                    "ack_number": "101",
                    "tcp_flags": "0x10",
                    "tcp_options_summary": "",
                    "tcp_stream": "42",
                },
            ],
        )

        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "cap1", "stream_id": "42"}
        )
        _assert_envelope_shape(response, success=True)
        report = _extract_report(response["data"])
        # No anomalies -> exactly one ``none`` entry.
        assert len(report["anomalies"]) == 1
        assert report["anomalies"][0]["category"] == "none"


# ---------------------------------------------------------------------------
# Partial Athena failure (Req 18.7)
# ---------------------------------------------------------------------------


class TestPartialFailure:
    """Req 18.7: a single sub-query failure leaves the rest of the report intact."""

    def test_handshake_query_fails_but_rest_succeed(self, fake_athena):
        # Total-packet probe returns 100 (avoid empty-partition path).
        fake_athena.add(
            "COUNT(*) AS packet_count",
            [{"packet_count": "100", "byte_count": "10000"}],
        )
        # Make the handshake query fail.
        fake_athena.add(
            "ORDER BY frame_time ASC",
            AthenaQueryFailedError("simulated handshake query failure"),
        )

        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "cap1", "stream_id": "42"}
        )
        # Req 18.7: success=True even when a section is unavailable.
        _assert_envelope_shape(response, success=True)
        report = _extract_report(response["data"])
        _assert_report_keys(report)

        # Affected sub-object is None.
        assert report["handshake"] is None

        # Single none anomaly listing handshake.
        none_entries = [
            a for a in report["anomalies"] if a["category"] == "none"
        ]
        assert len(none_entries) == 1
        assert "handshake" in none_entries[0]["description"]


# ---------------------------------------------------------------------------
# Multi-stream flow_selector (Req 18.13)
# ---------------------------------------------------------------------------


class TestMultiStream:
    """Req 18.13: flow_selector resolving to multiple streams returns up to 20 reports."""

    def test_flow_selector_multi_stream_returns_array(self, fake_athena, monkeypatch):
        # Stub the flow_selector resolution path so the helper
        # produces a non-empty predicate without contacting the
        # actual flow_selector module.
        from flow_selector import (
            ResolvedFlowSelector,
            ResolvedSide,
            ResolvedTuple,
        )

        resolved = ResolvedFlowSelector(
            source=ResolvedSide(
                ips=("10.0.0.1",),
                port=None,
                tuples=(ResolvedTuple(ip="10.0.0.1", port=None, strategy="literal"),),
                strategies_used=("literal",),
            ),
            destination=ResolvedSide(
                ips=("10.0.0.2",),
                port=443,
                tuples=(
                    ResolvedTuple(ip="10.0.0.2", port=443, strategy="literal"),
                ),
                strategies_used=("literal",),
            ),
            source_hostname=None,
            destination_hostname=None,
            stream_id=None,
            timeout_note=None,
        )

        def fake_resolve(capture_id, raw):
            return resolved

        monkeypatch.setattr(main, "resolve_flow_selector", fake_resolve)

        # Three matched streams from the matched_streams aggregate.
        fake_athena.add(
            "AS packet_count, "
            "COUNT(*)",
            [
                {
                    "stream_id": "1",
                    "client_ip": "10.0.0.1",
                    "client_port": "12345",
                    "server_ip": "10.0.0.2",
                    "server_port": "443",
                    "packet_count": "100",
                },
                {
                    "stream_id": "2",
                    "client_ip": "10.0.0.1",
                    "client_port": "12346",
                    "server_ip": "10.0.0.2",
                    "server_port": "443",
                    "packet_count": "50",
                },
                {
                    "stream_id": "3",
                    "client_ip": "10.0.0.1",
                    "client_port": "12347",
                    "server_ip": "10.0.0.2",
                    "server_port": "443",
                    "packet_count": "30",
                },
            ],
        )
        # Diagnose ranking query (replaces matched_streams when
        # available). Returns three streams.
        fake_athena.add(
            "byte_count "
            "FROM pcap_logs",
            [
                {"stream_id": "1", "packet_count": "100", "byte_count": "10000"},
                {"stream_id": "2", "packet_count": "50", "byte_count": "5000"},
                {"stream_id": "3", "packet_count": "30", "byte_count": "3000"},
            ],
        )
        # Per-stream total packet probe (returns 0 -> empty partition
        # for each stream, which is fine for shape verification).
        fake_athena.add(
            "COUNT(*) AS packet_count",
            [{"packet_count": "0", "byte_count": "0"}],
        )

        response = main.handle_diagnose_tcp_stream(
            {
                "capture_id": "cap1",
                "flow_selector": {
                    "source_ip": "10.0.0.1",
                    "destination_ip": "10.0.0.2",
                    "destination_port": 443,
                },
            }
        )
        _assert_envelope_shape(response, success=True)
        # Multi-stream -> reports array.
        assert "reports" in response["data"]
        reports = response["data"]["reports"]
        assert len(reports) == 3
        # First report corresponds to the highest-ranked stream.
        assert reports[0]["stream_id"] == "1"
        for r in reports:
            _assert_report_keys(r)
            _assert_anomaly_categories_valid(r)


# ---------------------------------------------------------------------------
# formattedText section ordering (Req 18.4)
# ---------------------------------------------------------------------------


class TestFormattedText:
    """Req 18.4: section headers appear in the mandated order."""

    def test_section_order(self, fake_athena):
        # Use the empty-partition path for simplicity — every section
        # is rendered with zero values, but the section *headers* are
        # still emitted in the Req 18.4 order.
        fake_athena.add(
            "COUNT(*) AS packet_count",
            [{"packet_count": "0", "byte_count": "0"}],
        )
        response = main.handle_diagnose_tcp_stream(
            {"capture_id": "cap1", "stream_id": "42"}
        )
        text = response["formattedText"]

        # Expected headers in order (Req 18.4).
        expected_order = [
            "Handshake:",
            "Connection close:",
            "RTT:",
            "Retransmissions:",
            "Out-of-order:",
            "Zero-window:",
            "TCP options:",
            "MSS clamping:",
            "Anomalies:",
        ]
        positions = [text.find(header) for header in expected_order]
        # Each header must be present and appear in increasing order.
        assert all(p > -1 for p in positions), (
            f"Missing section header(s); positions={positions}"
        )
        assert positions == sorted(positions), (
            f"Section headers out of order: {expected_order} -> {positions}"
        )


# ---------------------------------------------------------------------------
# Property-based tests (Req 18.2 shape, Req 18.3 closed enum)
# ---------------------------------------------------------------------------


_CAPTURE_ID_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789_-"
)


class TestPropertyBased:
    """Validates: Requirements 18.2, 18.3"""

    @given(
        capture_id=st.text(alphabet=_CAPTURE_ID_ALPHABET, min_size=1, max_size=64),
        stream_id=st.text(alphabet=_CAPTURE_ID_ALPHABET, min_size=1, max_size=32),
        # Synthetic counts to drive the rule firing space.
        out_of_order=st.integers(min_value=0, max_value=50),
        duplicate_acks=st.integers(min_value=0, max_value=50),
        dsacks=st.integers(min_value=0, max_value=50),
        fast_retx=st.integers(min_value=0, max_value=50),
        zero_window_ms=st.floats(
            min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False
        ),
        mss_advertised=st.integers(min_value=0, max_value=1500),
        mss_effective=st.integers(min_value=0, max_value=1500),
    )
    @settings(
        max_examples=30,
        deadline=None,
        suppress_health_check=[
            HealthCheck.function_scoped_fixture,
            HealthCheck.too_slow,
        ],
    )
    def test_report_shape_invariant(
        self,
        monkeypatch,
        capture_id,
        stream_id,
        out_of_order,
        duplicate_acks,
        dsacks,
        fast_retx,
        zero_window_ms,
        mss_advertised,
        mss_effective,
    ):
        """Every diagnose response satisfies Req 18.2 shape and Req 18.3 closed enum.

        Validates: Requirements 18.2, 18.3
        """
        fake = RoutingFakeAthena()
        monkeypatch.setattr(main, "run_athena_query", fake)

        # Total-packet probe — non-zero so we don't hit the empty
        # partition path.
        fake.add(
            "COUNT(*) AS packet_count",
            [{"packet_count": "100", "byte_count": "10000"}],
        )
        # Out-of-order aggregate
        fake.add(
            "fast_retransmit_count",
            [
                {
                    "stream_id": stream_id,
                    "out_of_order_count": str(out_of_order),
                    "duplicate_ack_count": str(duplicate_acks),
                    "dsack_count": str(dsacks),
                    "fast_retransmit_count": str(fast_retx),
                }
            ],
        )
        # Zero-window aggregate
        fake.add(
            "zero_window_total_duration_ms",
            [
                {
                    "stream_id": stream_id,
                    "zero_window_event_count": "1",
                    "zero_window_total_duration_ms": str(zero_window_ms),
                    "window_full_event_count": "0",
                    "window_update_event_count": "0",
                }
            ],
        )
        # TCP options aggregate
        fake.add(
            "BOOL_OR(sack_permitted)",
            [
                {
                    "direction": "client_to_server",
                    "mss_advertised": str(mss_advertised),
                    "window_scale": "7",
                    "sack_permitted": "true",
                    "timestamps_enabled": "true",
                    "mss_effective_min": str(mss_effective),
                }
            ],
        )

        response = main.handle_diagnose_tcp_stream(
            {"capture_id": capture_id, "stream_id": stream_id}
        )
        _assert_envelope_shape(response, success=True)
        report = _extract_report(response["data"])
        # Req 18.2: keys exactly match.
        _assert_report_keys(report)
        # Req 18.3: every category is from the closed enum.
        _assert_anomaly_categories_valid(report)
        # Anomalies array is never empty (none-entry fills when no
        # other rule fires).
        assert len(report["anomalies"]) >= 1
