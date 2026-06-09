"""
Unit and property-based tests for Task 17 — Flow_Selector resolution.

Run from the ``network-agent`` directory::

    python -m pytest test_flow_selector.py -v

These tests cover :mod:`flow_selector` and its integration with the
Pcap_Query_Action handlers in :mod:`main`. They stub
:func:`flow_selector.run_athena_query` (for the in-capture strategies)
and patch :data:`flow_selector._ACTIVE_DNS_RESOLVER` (for the active
DNS step) with deterministic fakes so the tests do not touch the
network and have no AWS dependencies.

What is verified
----------------

- :func:`flow_selector.validate_flow_selector` enforces the shape of
  the seven Flow_Selector fields (Req 19.4): IPs are syntactically
  valid IPv4/IPv6, ports are integers in 0..65535, hostnames are
  non-empty strings, ``stream_id`` matches ``[A-Za-z0-9_-]{1,64}``,
  unknown fields are rejected, and the empty selector is rejected.
- :func:`flow_selector.resolve_flow_selector` runs the
  ``combined`` Hostname_Resolution_Strategy in the documented order
  and unions all returned IPs (Req 19.2), and rejects with
  ``hostname_unresolved`` when a supplied hostname returns zero IPs
  across all three strategies (Req 19.3).
- :func:`flow_selector.build_flow_predicate` produces the correct
  AND-combined Athena predicate fragment for the three direction
  cases (both sides supplied — Req 19.1, source-only — Req 19.6,
  destination-only — Req 19.7), and AND-combines a top-level
  ``stream_id`` (Req 5.25).
- :func:`flow_selector.build_resolved_flow_set_metadata` populates
  ``metadata.resolved_flow_set`` with the (ip, port, strategy)
  tuples actually used (Reqs 5.27, 19.9).
- :func:`flow_selector.query_matched_streams` runs a secondary
  aggregate and returns ``(count, streams)`` (Req 19.5), and
  degrades gracefully on Athena error.
- The active-DNS budgets (per-hostname 5s, overall 15s) are
  honoured: when the overall budget is exhausted the module
  surfaces a ``timeout_note`` and still returns whatever IPs were
  collected (Req 19.8).
- The 11 affected handlers integrate with Flow_Selector:
  ``correlate_tcp_streams``, ``reconstruct_tcp_handshake``,
  ``analyze_tcp_options`` and ``get_request_response_latency``
  reject when neither ``stream_id`` nor ``flow_selector`` is
  supplied (Req 5.26); a successful flow-selector invocation
  surfaces ``metadata.resolved_flow_set``,
  ``metadata.matched_stream_count``, and
  ``metadata.matched_streams`` (Reqs 19.5, 19.9).
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

import flow_selector as fs_module
import main
from flow_selector import (
    ACTIVE_DNS_OVERALL_BUDGET_SECONDS,
    FlowSelectorError,
    ResolvedFlowSelector,
    ResolvedSide,
    ResolvedTuple,
    STRATEGY_ACTIVE_DNS_LOOKUP,
    STRATEGY_DNS_IN_CAPTURE,
    STRATEGY_TLS_SNI_IN_CAPTURE,
    build_flow_predicate,
    build_resolved_flow_set_metadata,
    query_matched_streams,
    resolve_flow_selector,
    validate_flow_selector,
)
from athena_helper import AthenaQueryError


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeAthena:
    """Records SQL strings and replays canned responses by query order.

    The two in-capture strategies (``dns_in_capture`` and
    ``tls_sni_in_capture``) and the matched-streams aggregate each
    issue exactly one query, so the fake replays the canned list in
    order. Tests that exercise multiple resolutions populate the
    list accordingly.
    """

    def __init__(
        self,
        responses: Optional[Sequence[Sequence[Dict[str, Any]]]] = None,
        raise_on_call: Optional[Dict[int, Exception]] = None,
    ) -> None:
        self.responses: List[List[Dict[str, Any]]] = [
            list(r) for r in (responses or [])
        ]
        self.raise_on_call: Dict[int, Exception] = raise_on_call or {}
        self.calls: List[str] = []

    def __call__(
        self,
        sql: str,
        work_group: Optional[str] = None,
        output_location: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        idx = len(self.calls)
        self.calls.append(sql)
        if idx in self.raise_on_call:
            raise self.raise_on_call[idx]
        if idx >= len(self.responses):
            # Default to "no rows" so tests do not have to enumerate
            # every secondary query.
            return []
        return [dict(r) for r in self.responses[idx]]


@pytest.fixture
def fake_athena_in_capture(monkeypatch):
    """Stub ``flow_selector.run_athena_query`` for in-capture strategies."""
    fake = FakeAthena()
    monkeypatch.setattr(fs_module, "run_athena_query", fake)
    return fake


@pytest.fixture
def fake_athena_main(monkeypatch):
    """Stub ``main.run_athena_query`` so handlers do not hit AWS."""
    fake = FakeAthena()
    monkeypatch.setattr(main, "run_athena_query", fake)
    return fake


@pytest.fixture
def deterministic_dns(monkeypatch):
    """Replace the active-DNS resolver with a deterministic mapping.

    Returns a dict the test populates: ``{hostname: [ip, ...]}``.
    Hostnames not in the dict resolve to ``[]``.
    """
    table: Dict[str, List[str]] = {}

    def fake_resolver(hostname: str) -> List[str]:
        return list(table.get(hostname, []))

    monkeypatch.setattr(fs_module, "_ACTIVE_DNS_RESOLVER", fake_resolver)
    return table


# ---------------------------------------------------------------------------
# 1. validate_flow_selector — shape enforcement (Req 19.4, 19.1)
# ---------------------------------------------------------------------------


class TestValidateFlowSelector:
    def test_accepts_full_selector(self):
        result = validate_flow_selector(
            {
                "source_ip": "10.0.1.5",
                "source_port": 443,
                "destination_ip": "10.0.2.7",
                "destination_port": 80,
                "stream_id": "abc-123_XYZ",
            }
        )
        assert result.source_ip == "10.0.1.5"
        assert result.source_port == 443
        assert result.destination_ip == "10.0.2.7"
        assert result.destination_port == 80
        assert result.stream_id == "abc-123_XYZ"

    def test_accepts_hostname_only_source(self):
        result = validate_flow_selector(
            {"source_hostname": "ecr.eu-west-3.amazonaws.com"}
        )
        assert result.source_hostname == "ecr.eu-west-3.amazonaws.com"
        assert result.has_source
        assert not result.has_destination

    def test_accepts_destination_only(self):
        result = validate_flow_selector(
            {"destination_ip": "::1", "destination_port": 0}
        )
        assert result.destination_ip == "::1"
        assert result.destination_port == 0
        assert not result.has_source
        assert result.has_destination

    def test_rejects_empty_dict(self):
        with pytest.raises(FlowSelectorError) as exc_info:
            validate_flow_selector({})
        assert "must contain at least one" in str(exc_info.value)
        assert exc_info.value.error_category == "invalid_parameter"

    def test_rejects_non_dict(self):
        with pytest.raises(FlowSelectorError):
            validate_flow_selector("10.0.0.1")
        with pytest.raises(FlowSelectorError):
            validate_flow_selector(["src", "dst"])

    def test_rejects_unknown_field(self):
        with pytest.raises(FlowSelectorError) as exc_info:
            validate_flow_selector({"src_ip": "10.0.0.1"})
        assert "unknown field" in str(exc_info.value).lower()

    @pytest.mark.parametrize(
        "field,value",
        [
            ("source_ip", "not-an-ip"),
            ("source_ip", "10.0.0.300"),
            ("destination_ip", ""),
            ("destination_ip", 42),
        ],
    )
    def test_rejects_invalid_ip(self, field, value):
        with pytest.raises(FlowSelectorError) as exc_info:
            validate_flow_selector({field: value})
        assert exc_info.value.error_category == "invalid_parameter"

    @pytest.mark.parametrize(
        "field,value",
        [
            ("source_port", -1),
            ("source_port", 65536),
            ("destination_port", "443"),
            ("destination_port", 1.5),
            ("destination_port", True),  # bool is not int
        ],
    )
    def test_rejects_invalid_port(self, field, value):
        with pytest.raises(FlowSelectorError) as exc_info:
            validate_flow_selector({field: value})
        assert exc_info.value.error_category == "invalid_parameter"

    @pytest.mark.parametrize("port", [0, 1, 80, 443, 65535])
    def test_accepts_port_boundaries(self, port):
        result = validate_flow_selector({"source_port": port})
        assert result.source_port == port

    def test_rejects_empty_hostname(self):
        with pytest.raises(FlowSelectorError):
            validate_flow_selector({"source_hostname": ""})
        with pytest.raises(FlowSelectorError):
            validate_flow_selector({"source_hostname": "   "})

    def test_strips_hostname_whitespace(self):
        result = validate_flow_selector(
            {"source_hostname": "  example.com  "}
        )
        assert result.source_hostname == "example.com"

    def test_rejects_invalid_stream_id(self):
        with pytest.raises(FlowSelectorError):
            validate_flow_selector({"stream_id": "with space"})
        with pytest.raises(FlowSelectorError):
            validate_flow_selector({"stream_id": "x" * 65})
        with pytest.raises(FlowSelectorError):
            validate_flow_selector({"stream_id": ""})

    @given(
        st.text(
            alphabet=st.sampled_from(
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            ),
            min_size=1,
            max_size=64,
        )
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_property_valid_stream_ids_round_trip(self, stream_id):
        """Validates: Requirements 19.1, 19.4

        Every stream_id that matches ``[A-Za-z0-9_-]{1,64}`` is
        accepted by the validator and round-trips unchanged.
        """
        result = validate_flow_selector({"stream_id": stream_id})
        assert result.stream_id == stream_id


# ---------------------------------------------------------------------------
# 2. resolve_flow_selector — combined strategy (Reqs 19.2, 19.3)
# ---------------------------------------------------------------------------


class TestResolveFlowSelectorCombined:
    def test_literal_ip_skips_resolution(
        self, fake_athena_in_capture, deterministic_dns
    ):
        resolved = resolve_flow_selector(
            "cap-1",
            {"source_ip": "10.0.1.5"},
        )
        assert resolved.source.ips == ("10.0.1.5",)
        assert resolved.source.tuples == (
            ResolvedTuple(ip="10.0.1.5", port=None, strategy="literal"),
        )
        # No Athena calls should have happened — literal IPs skip the
        # combined strategy entirely.
        assert fake_athena_in_capture.calls == []

    def test_hostname_uses_dns_in_capture_first(
        self, monkeypatch, deterministic_dns
    ):
        # First call (dns_in_capture) returns one IP, subsequent
        # strategies still run (combined unions all). We only seed
        # the first response; others default to empty.
        fake = FakeAthena(responses=[[{"ip": "10.0.0.1"}]])
        monkeypatch.setattr(fs_module, "run_athena_query", fake)
        deterministic_dns["app.example.com"] = ["203.0.113.5"]

        resolved = resolve_flow_selector(
            "cap-1", {"destination_hostname": "app.example.com"}
        )
        # The dns_in_capture IP appears first (preserves resolution
        # order), the active DNS IP appears at the end.
        assert resolved.destination.ips == ("10.0.0.1", "203.0.113.5")
        strategies = [t.strategy for t in resolved.destination.tuples]
        assert strategies == [
            STRATEGY_DNS_IN_CAPTURE,
            STRATEGY_ACTIVE_DNS_LOOKUP,
        ]

    def test_combined_unions_all_three_strategies(
        self, monkeypatch, deterministic_dns
    ):
        fake = FakeAthena(
            responses=[
                # dns_in_capture → 1 IP
                [{"ip": "10.0.0.1"}],
                # tls_sni_in_capture → 1 IP (different)
                [{"ip": "10.0.0.2"}],
            ]
        )
        monkeypatch.setattr(fs_module, "run_athena_query", fake)
        deterministic_dns["multi.example.com"] = ["10.0.0.3", "10.0.0.4"]

        resolved = resolve_flow_selector(
            "cap-1", {"source_hostname": "multi.example.com"}
        )
        assert resolved.source.ips == (
            "10.0.0.1",
            "10.0.0.2",
            "10.0.0.3",
            "10.0.0.4",
        )
        # Each strategy contributed at least one IP, so all three
        # appear in strategies_used in resolution order.
        assert resolved.source.strategies_used == (
            STRATEGY_DNS_IN_CAPTURE,
            STRATEGY_TLS_SNI_IN_CAPTURE,
            STRATEGY_ACTIVE_DNS_LOOKUP,
        )

    def test_combined_dedupes_overlapping_ips(
        self, monkeypatch, deterministic_dns
    ):
        # All three strategies return the same IP. The union should
        # contain it exactly once, attributed to the *first* strategy
        # that produced it.
        fake = FakeAthena(
            responses=[
                [{"ip": "10.0.0.1"}],
                [{"ip": "10.0.0.1"}],
            ]
        )
        monkeypatch.setattr(fs_module, "run_athena_query", fake)
        deterministic_dns["dup.example.com"] = ["10.0.0.1"]

        resolved = resolve_flow_selector(
            "cap-1", {"destination_hostname": "dup.example.com"}
        )
        assert resolved.destination.ips == ("10.0.0.1",)
        assert resolved.destination.tuples[0].strategy == STRATEGY_DNS_IN_CAPTURE

    def test_unresolved_hostname_raises(
        self, monkeypatch, deterministic_dns
    ):
        # All three strategies return zero IPs.
        fake = FakeAthena(responses=[[], []])
        monkeypatch.setattr(fs_module, "run_athena_query", fake)
        # deterministic_dns is empty by default.

        with pytest.raises(FlowSelectorError) as exc_info:
            resolve_flow_selector(
                "cap-1", {"source_hostname": "missing.example.com"}
            )
        assert exc_info.value.error_category == "hostname_unresolved"
        # Error message must list the strategies attempted.
        msg = str(exc_info.value)
        assert STRATEGY_DNS_IN_CAPTURE in msg
        assert STRATEGY_TLS_SNI_IN_CAPTURE in msg
        assert STRATEGY_ACTIVE_DNS_LOOKUP in msg

    def test_athena_failure_in_capture_strategies_falls_through(
        self, monkeypatch, deterministic_dns
    ):
        """Athena failures in dns_in_capture must not block the request.

        Per Req 19.3 the rejection only triggers when *all three*
        strategies return zero IPs. An Athena outage that breaks
        dns_in_capture and tls_sni_in_capture should still allow the
        request to succeed via active_dns_lookup.
        """

        class FailingAthena:
            def __init__(self):
                self.calls = 0

            def __call__(self, *args, **kwargs):
                self.calls += 1
                raise AthenaQueryError("athena down")

        monkeypatch.setattr(fs_module, "run_athena_query", FailingAthena())
        deterministic_dns["always.example.com"] = ["10.0.0.42"]

        resolved = resolve_flow_selector(
            "cap-1", {"destination_hostname": "always.example.com"}
        )
        assert resolved.destination.ips == ("10.0.0.42",)


# ---------------------------------------------------------------------------
# 3. Active DNS budget (Req 19.8)
# ---------------------------------------------------------------------------


class TestActiveDnsBudget:
    def test_overall_budget_exceeded_sets_timeout_note(
        self, monkeypatch, fake_athena_in_capture
    ):
        # Replace ``time.monotonic`` so the overall budget is already
        # exhausted before the active-DNS step runs.
        deadline_anchor = [100.0]

        def fake_monotonic():
            t = deadline_anchor[0]
            # Each subsequent call advances time by 20s — beyond the
            # 15s overall budget — so the active_dns_lookup step
            # short-circuits with budget_exhausted=True.
            deadline_anchor[0] += 20.0
            return t

        monkeypatch.setattr(fs_module.time, "monotonic", fake_monotonic)

        # The fake resolver is never called because the budget is
        # already gone, but we still need to populate dns_in_capture
        # so the request does not fail with hostname_unresolved.
        monkeypatch.setattr(
            fs_module,
            "run_athena_query",
            FakeAthena(responses=[[{"ip": "10.0.0.99"}]]),
        )

        resolved = resolve_flow_selector(
            "cap-1", {"destination_hostname": "slow.example.com"}
        )
        assert resolved.timeout_note is not None
        assert "active_dns_lookup" in resolved.timeout_note
        # IPs from the in-capture strategy are preserved.
        assert "10.0.0.99" in resolved.destination.ips


# ---------------------------------------------------------------------------
# 4. build_flow_predicate (Reqs 19.1, 19.6, 19.7, 5.25)
# ---------------------------------------------------------------------------


class TestBuildFlowPredicate:
    @staticmethod
    def _resolved(
        *,
        src_ips: Tuple[str, ...] = (),
        src_port: Optional[int] = None,
        dst_ips: Tuple[str, ...] = (),
        dst_port: Optional[int] = None,
        stream_id: Optional[str] = None,
    ) -> ResolvedFlowSelector:
        src = ResolvedSide(
            ips=src_ips,
            port=src_port,
            tuples=tuple(
                ResolvedTuple(ip=ip, port=src_port, strategy="literal")
                for ip in src_ips
            ),
        )
        dst = ResolvedSide(
            ips=dst_ips,
            port=dst_port,
            tuples=tuple(
                ResolvedTuple(ip=ip, port=dst_port, strategy="literal")
                for ip in dst_ips
            ),
        )
        return ResolvedFlowSelector(
            source=src, destination=dst, stream_id=stream_id,
        )

    def test_both_sides_strict_match(self):
        """Req 19.1: both source_* and destination_* AND-combined."""
        resolved = self._resolved(
            src_ips=("10.0.0.1",),
            src_port=12345,
            dst_ips=("10.0.0.2",),
            dst_port=443,
        )
        predicate = build_flow_predicate(resolved)
        assert "src_ip = '10.0.0.1'" in predicate
        assert "src_port = 12345" in predicate
        assert "dst_ip = '10.0.0.2'" in predicate
        assert "dst_port = 443" in predicate

    def test_source_only_matches_either_direction(self):
        """Req 19.6: source-only constraints apply to either direction."""
        resolved = self._resolved(src_ips=("10.0.0.1",), src_port=12345)
        predicate = build_flow_predicate(resolved)
        # The predicate must include both src_ip and dst_ip checks
        # OR-combined so a flow where 10.0.0.1 is the responder
        # also matches.
        assert "src_ip = '10.0.0.1'" in predicate
        assert "dst_ip = '10.0.0.1'" in predicate
        assert " OR " in predicate

    def test_destination_only_responder_side_only(self):
        """Req 19.7: destination-only constraints restrict to responder."""
        resolved = self._resolved(dst_ips=("10.0.0.2",), dst_port=443)
        predicate = build_flow_predicate(resolved)
        # Only dst_* references — never src_*.
        assert "dst_ip = '10.0.0.2'" in predicate
        assert "dst_port = 443" in predicate
        assert "src_ip = '10.0.0.2'" not in predicate
        # No OR — strict responder match.
        assert " OR " not in predicate

    def test_multi_ip_uses_in_clause(self):
        resolved = self._resolved(dst_ips=("10.0.0.2", "10.0.0.3"))
        predicate = build_flow_predicate(resolved)
        assert "dst_ip IN ('10.0.0.2', '10.0.0.3')" in predicate

    def test_stream_id_and_combined_with_other_constraints(self):
        """Req 5.25: stream_id AND-combines with the rest of the selector."""
        resolved = self._resolved(
            dst_ips=("10.0.0.2",),
            dst_port=443,
            stream_id="abc-123",
        )
        predicate = build_flow_predicate(resolved)
        assert "tcp_stream = 'abc-123'" in predicate
        assert "dst_ip = '10.0.0.2'" in predicate
        assert " AND " in predicate

    def test_empty_resolved_returns_empty_string(self):
        # Defensive: if every field collapses to no constraint, we
        # produce an empty fragment so the caller can detect it.
        resolved = ResolvedFlowSelector()
        assert build_flow_predicate(resolved) == ""


# ---------------------------------------------------------------------------
# 5. build_resolved_flow_set_metadata (Reqs 5.27, 19.9)
# ---------------------------------------------------------------------------


class TestResolvedFlowSetMetadata:
    def test_includes_source_destination_hostname_strategies(self):
        resolved = ResolvedFlowSelector(
            source=ResolvedSide(
                ips=("10.0.0.1",),
                port=80,
                tuples=(
                    ResolvedTuple(ip="10.0.0.1", port=80, strategy=STRATEGY_DNS_IN_CAPTURE),
                ),
            ),
            destination=ResolvedSide(
                ips=("203.0.113.5",),
                port=443,
                tuples=(
                    ResolvedTuple(
                        ip="203.0.113.5",
                        port=443,
                        strategy=STRATEGY_ACTIVE_DNS_LOOKUP,
                    ),
                ),
            ),
            stream_id="abc-123",
            source_hostname="src.example.com",
            destination_hostname="dst.example.com",
        )
        meta = build_resolved_flow_set_metadata(resolved)
        assert meta["source"] == [
            {"ip": "10.0.0.1", "port": 80, "strategy": STRATEGY_DNS_IN_CAPTURE}
        ]
        assert meta["destination"] == [
            {
                "ip": "203.0.113.5",
                "port": 443,
                "strategy": STRATEGY_ACTIVE_DNS_LOOKUP,
            }
        ]
        assert meta["source_hostname"] == "src.example.com"
        assert meta["destination_hostname"] == "dst.example.com"
        assert meta["stream_id"] == "abc-123"

    def test_omits_optional_fields_when_absent(self):
        resolved = ResolvedFlowSelector(
            source=ResolvedSide(
                ips=("10.0.0.1",),
                tuples=(
                    ResolvedTuple(ip="10.0.0.1", strategy="literal"),
                ),
            ),
        )
        meta = build_resolved_flow_set_metadata(resolved)
        assert meta["source"] == [
            {"ip": "10.0.0.1", "port": None, "strategy": "literal"}
        ]
        assert meta["destination"] == []
        assert "stream_id" not in meta
        assert "source_hostname" not in meta
        assert "destination_hostname" not in meta
        assert "timeout_note" not in meta

    def test_includes_timeout_note_when_present(self):
        resolved = ResolvedFlowSelector(
            source=ResolvedSide(
                ips=("10.0.0.1",),
                tuples=(ResolvedTuple(ip="10.0.0.1"),),
            ),
            timeout_note="active_dns_lookup overall budget exhausted",
        )
        meta = build_resolved_flow_set_metadata(resolved)
        assert meta["timeout_note"] == (
            "active_dns_lookup overall budget exhausted"
        )


# ---------------------------------------------------------------------------
# 6. query_matched_streams (Req 19.5)
# ---------------------------------------------------------------------------


class TestQueryMatchedStreams:
    def test_returns_streams_from_athena_rows(self, monkeypatch):
        fake = FakeAthena(
            responses=[
                [
                    {
                        "stream_id": "1",
                        "client_ip": "10.0.0.1",
                        "client_port": "12345",
                        "server_ip": "10.0.0.2",
                        "server_port": "443",
                        "packet_count": "120",
                    },
                    {
                        "stream_id": "2",
                        "client_ip": "10.0.0.1",
                        "client_port": "12346",
                        "server_ip": "10.0.0.2",
                        "server_port": "443",
                        "packet_count": "30",
                    },
                ]
            ]
        )
        monkeypatch.setattr(fs_module, "run_athena_query", fake)

        count, streams = query_matched_streams("cap-1", "dst_ip = '10.0.0.2'")
        assert count == 2
        assert streams[0]["stream_id"] == "1"
        assert streams[0]["client_port"] == 12345
        assert streams[0]["packet_count"] == 120
        assert streams[1]["stream_id"] == "2"
        # The query must scope to the supplied capture_id and include
        # the predicate.
        assert "cap-1" in fake.calls[0]
        assert "10.0.0.2" in fake.calls[0]

    def test_empty_predicate_short_circuits(self, monkeypatch):
        fake = FakeAthena()
        monkeypatch.setattr(fs_module, "run_athena_query", fake)
        count, streams = query_matched_streams("cap-1", "")
        assert count == 0
        assert streams == []
        # No Athena call when the predicate is empty.
        assert fake.calls == []

    def test_athena_failure_returns_zero_count(self, monkeypatch):
        fake = FakeAthena(raise_on_call={0: AthenaQueryError("boom")})
        monkeypatch.setattr(fs_module, "run_athena_query", fake)
        count, streams = query_matched_streams("cap-1", "dst_ip = '1.1.1.1'")
        assert count == 0
        assert streams == []


# ---------------------------------------------------------------------------
# 7. Handler integration — Pcap_Query_Action handlers + flow_selector
# ---------------------------------------------------------------------------


VALID_CAPTURE_ID = "cap-flow-123"


def _patch_handler_athena(
    monkeypatch,
    *,
    rows: Optional[List[Dict[str, Any]]] = None,
    matched_streams_rows: Optional[List[Dict[str, Any]]] = None,
    in_capture_responses: Optional[Sequence[Sequence[Dict[str, Any]]]] = None,
):
    """Stub Athena across both ``main`` and ``flow_selector``.

    The order of Athena calls depends on whether the flow_selector
    contains a hostname:

    - **Literal IP only**: the in-capture strategies (dns_in_capture,
      tls_sni_in_capture) and the active-DNS step are all skipped.
      The call order is: handler main query → matched_streams.
    - **Hostname**: dns_in_capture → tls_sni_in_capture → handler
      main query → matched_streams. The active-DNS step is also
      called but does not go through ``run_athena_query`` (it goes
      through :data:`flow_selector._ACTIVE_DNS_RESOLVER`).

    The test helper inspects the SQL it receives to decide which
    canned response to return: queries that target ``pcap_logs`` and
    contain ``capture_id = '<VALID_CAPTURE_ID>'`` are routed by their
    inner predicates:

    - ``dns_response_ips`` / ``CROSS JOIN UNNEST`` → in-capture DNS
    - ``tls_sni`` / ``tls_handshake_type = 1`` → in-capture TLS SNI
    - ``WITH matched AS`` / ``GROUP BY tcp_stream`` and surrounding
      structure → matched_streams aggregate
    - everything else → the handler's main query

    This makes the helper resilient to handler ordering differences
    across the eleven affected handlers.
    """

    in_capture_iter: List[List[Dict[str, Any]]] = [
        list(r) for r in (in_capture_responses or [])
    ]

    class HandlerFake:
        def __init__(self):
            self.calls: List[str] = []
            self.main_query_calls: List[str] = []

        def __call__(self, sql, work_group=None, output_location=None):
            self.calls.append(sql)
            sql_lower = sql.lower()
            # Route by SQL shape rather than call ordinal so the
            # helper remains correct regardless of which handler
            # invoked it.
            if "cross join unnest" in sql_lower and "dns_response_ips" in sql_lower:
                # dns_in_capture
                if in_capture_iter:
                    return in_capture_iter.pop(0)
                return []
            if "tls_sni" in sql_lower and "tls_handshake_type = 1" in sql_lower:
                # tls_sni_in_capture
                if in_capture_iter:
                    return in_capture_iter.pop(0)
                return []
            if "with matched as" in sql_lower and "min_by" in sql_lower:
                # matched_streams aggregate
                return list(matched_streams_rows or [])
            # Default: handler's main query.
            self.main_query_calls.append(sql)
            return list(rows or [])

    shared = HandlerFake()
    monkeypatch.setattr(main, "run_athena_query", shared)
    monkeypatch.setattr(fs_module, "run_athena_query", shared)
    return shared


class FakeActiveDns:
    """Always returns the configured IP list (no network access)."""

    def __init__(self, ips: List[str]):
        self.ips = list(ips)
        self.calls = 0

    def __call__(self, hostname: str) -> List[str]:
        self.calls += 1
        return list(self.ips)


@pytest.fixture
def fake_dns(monkeypatch):
    """Install a deterministic active DNS resolver."""
    resolver = FakeActiveDns(ips=["203.0.113.5"])
    monkeypatch.setattr(fs_module, "_ACTIVE_DNS_RESOLVER", resolver)
    return resolver


class TestHandlerIntegration:
    def test_correlate_tcp_streams_rejects_when_neither_supplied(
        self, fake_athena_main, fake_dns
    ):
        """Req 5.26: correlate_tcp_streams requires stream_id OR flow_selector."""
        response = main.handle_correlate_tcp_streams(
            {"capture_id": VALID_CAPTURE_ID}
        )
        assert response["success"] is False
        assert "stream_id" in response["error"]
        assert "flow_selector" in response["error"]
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        # No Athena call must have happened.
        assert fake_athena_main.calls == []

    def test_reconstruct_handshake_rejects_when_neither_supplied(
        self, fake_athena_main, fake_dns
    ):
        response = main.handle_reconstruct_tcp_handshake(
            {"capture_id": VALID_CAPTURE_ID}
        )
        assert response["success"] is False
        assert "stream_id" in response["error"]
        assert "flow_selector" in response["error"]

    def test_analyze_tcp_options_rejects_when_neither_supplied(
        self, fake_athena_main, fake_dns
    ):
        response = main.handle_analyze_tcp_options(
            {"capture_id": VALID_CAPTURE_ID}
        )
        assert response["success"] is False
        assert "stream_id" in response["error"]
        assert "flow_selector" in response["error"]

    def test_get_request_response_latency_rejects_when_neither_supplied(
        self, fake_athena_main, fake_dns
    ):
        response = main.handle_get_request_response_latency(
            {"capture_id": VALID_CAPTURE_ID}
        )
        assert response["success"] is False
        assert "stream_id" in response["error"]
        assert "flow_selector" in response["error"]

    def test_correlate_tcp_streams_with_flow_selector_only(
        self, monkeypatch, fake_dns
    ):
        """Flow_Selector instead of stream_id should drive a successful query.

        Verifies Reqs 5.24, 5.27, 19.5, 19.9: the handler accepts a
        Flow_Selector, surfaces ``metadata.resolved_flow_set``,
        ``metadata.matched_stream_count``, and
        ``metadata.matched_streams``.
        """
        shared = _patch_handler_athena(
            monkeypatch,
            rows=[
                {
                    "frame_time": "2026-04-20 12:00:00.000",
                    "frame_size": 1500,
                    "src_ip": "10.0.1.5",
                    "src_port": 12345,
                    "dst_ip": "203.0.113.5",
                    "dst_port": 443,
                    "tcp_stream": "5",
                }
            ],
            matched_streams_rows=[
                {
                    "stream_id": "5",
                    "client_ip": "10.0.1.5",
                    "client_port": "12345",
                    "server_ip": "203.0.113.5",
                    "server_port": "443",
                    "packet_count": "42",
                }
            ],
        )

        response = main.handle_correlate_tcp_streams(
            {
                "capture_id": VALID_CAPTURE_ID,
                "flow_selector": {
                    "destination_ip": "203.0.113.5",
                    "destination_port": 443,
                },
            }
        )

        assert response["success"] is True
        # Capture_Id_Predicate is still present.
        sql = response["data"]["executed_sql"]
        assert f"capture_id = '{VALID_CAPTURE_ID}'" in sql
        # The destination_ip / destination_port appear in the SQL.
        assert "dst_ip = '203.0.113.5'" in sql
        assert "dst_port = 443" in sql
        # Source predicate must be absent (destination-only ⇒ responder side).
        assert "src_ip = '203.0.113.5'" not in sql

        # metadata.resolved_flow_set surfaces the resolved tuples.
        meta = response["metadata"]
        rfs = meta["resolved_flow_set"]
        assert rfs["destination"] == [
            {"ip": "203.0.113.5", "port": 443, "strategy": "literal"}
        ]
        assert rfs["source"] == []

        # matched_stream_count / matched_streams
        assert meta["matched_stream_count"] == 1
        assert meta["matched_streams"][0]["stream_id"] == "5"
        assert meta["matched_streams"][0]["packet_count"] == 42

    def test_correlate_with_stream_id_and_flow_selector_combined(
        self, monkeypatch, fake_dns
    ):
        """Req 5.25: stream_id + flow_selector AND-combined."""
        _patch_handler_athena(monkeypatch)

        response = main.handle_correlate_tcp_streams(
            {
                "capture_id": VALID_CAPTURE_ID,
                "stream_id": "abc-123",
                "flow_selector": {"destination_ip": "10.0.0.2"},
            }
        )
        assert response["success"] is True
        sql = response["data"]["executed_sql"]
        # Both constraints must be present.
        assert "tcp_stream = 'abc-123'" in sql
        assert "dst_ip = '10.0.0.2'" in sql

    def test_invalid_flow_selector_rejected_before_athena(
        self, fake_athena_main, fake_dns
    ):
        response = main.handle_correlate_tcp_streams(
            {
                "capture_id": VALID_CAPTURE_ID,
                "flow_selector": {"source_ip": "not-an-ip"},
            }
        )
        assert response["success"] is False
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        # No Athena query must have run.
        assert fake_athena_main.calls == []

    def test_unresolved_hostname_rejects_request(
        self, monkeypatch, deterministic_dns
    ):
        """Req 19.3: zero IPs across all three strategies → reject."""
        # Stub both Athena (returns no rows for any in-capture query)
        # and active DNS (returns []).
        empty_fake = FakeAthena()
        monkeypatch.setattr(main, "run_athena_query", empty_fake)
        monkeypatch.setattr(fs_module, "run_athena_query", empty_fake)

        response = main.handle_correlate_tcp_streams(
            {
                "capture_id": VALID_CAPTURE_ID,
                "flow_selector": {
                    "destination_hostname": "missing.example.com",
                },
            }
        )
        assert response["success"] is False
        assert response["metadata"]["errorCategory"] == "hostname_unresolved"

    def test_destination_only_flow_selector_responder_side_only(
        self, monkeypatch, fake_dns
    ):
        """Req 19.7: destination-only matches the responder side only."""
        _patch_handler_athena(monkeypatch)

        response = main.handle_check_tls_hello_size(
            {
                "capture_id": VALID_CAPTURE_ID,
                "flow_selector": {
                    "destination_ip": "10.0.0.2",
                    "destination_port": 443,
                },
            }
        )
        assert response["success"] is True
        sql = response["data"]["executed_sql"]
        assert "dst_ip = '10.0.0.2'" in sql
        assert "dst_port = 443" in sql
        # Must NOT include src_ip = '10.0.0.2' (that would be the
        # source-only either-direction logic from Req 19.6).
        assert "src_ip = '10.0.0.2'" not in sql

    def test_source_only_flow_selector_either_direction(
        self, monkeypatch, fake_dns
    ):
        """Req 19.6: source-only applies to either direction."""
        _patch_handler_athena(monkeypatch)

        response = main.handle_detect_retransmissions(
            {
                "capture_id": VALID_CAPTURE_ID,
                "flow_selector": {"source_ip": "10.0.1.5"},
            }
        )
        assert response["success"] is True
        sql = response["data"]["executed_sql"]
        # The either-direction logic produces both src_ip and dst_ip
        # references with an OR.
        assert "src_ip = '10.0.1.5'" in sql
        assert "dst_ip = '10.0.1.5'" in sql

    def test_stream_id_mismatch_between_top_level_and_selector(
        self, fake_athena_main, fake_dns
    ):
        """Conflicting stream_id values must surface a clear error."""
        response = main.handle_correlate_tcp_streams(
            {
                "capture_id": VALID_CAPTURE_ID,
                "stream_id": "stream-A",
                "flow_selector": {"stream_id": "stream-B"},
            }
        )
        assert response["success"] is False
        assert "stream_id" in response["error"]
        assert response["metadata"]["errorCategory"] == "invalid_parameter"

    def test_existing_stream_id_only_path_unchanged(
        self, fake_athena_main, fake_dns
    ):
        """Backward compat: stream_id-only invocations keep their old SQL shape."""
        response = main.handle_correlate_tcp_streams(
            {"capture_id": VALID_CAPTURE_ID, "stream_id": "abc-123"}
        )
        # Athena should have been called exactly once (no
        # flow_selector → no in-capture queries, no matched_streams
        # query).
        assert len(fake_athena_main.calls) == 1
        sql = fake_athena_main.calls[0]
        assert "tcp_stream = 'abc-123'" in sql
        # No resolved_flow_set in metadata for stream-id-only invocations.
        assert "resolved_flow_set" not in response["metadata"]

    def test_handlers_without_targeting_accept_optional_flow_selector(
        self, monkeypatch, fake_dns
    ):
        """detect_zero_window: no targeting required, but flow_selector accepted."""
        # Without flow_selector: works as before with one Athena call.
        fake = FakeAthena()
        monkeypatch.setattr(main, "run_athena_query", fake)
        monkeypatch.setattr(fs_module, "run_athena_query", fake)
        response = main.handle_detect_zero_window(
            {"capture_id": VALID_CAPTURE_ID}
        )
        assert response["success"] is True
        assert len(fake.calls) == 1
        assert "resolved_flow_set" not in response["metadata"]

        # With flow_selector: gets the additional in-capture and
        # matched_streams queries.
        fake_with_selector = FakeAthena()
        monkeypatch.setattr(main, "run_athena_query", fake_with_selector)
        monkeypatch.setattr(fs_module, "run_athena_query", fake_with_selector)
        response = main.handle_detect_zero_window(
            {
                "capture_id": VALID_CAPTURE_ID,
                "flow_selector": {"destination_ip": "10.0.0.5"},
            }
        )
        assert response["success"] is True
        assert "resolved_flow_set" in response["metadata"]
        assert "matched_stream_count" in response["metadata"]


# ---------------------------------------------------------------------------
# 8. Property-based round trip — every valid Flow_Selector is shape-validated
# ---------------------------------------------------------------------------


class TestFlowSelectorProperty:
    @given(
        st.fixed_dictionaries(
            {},
            optional={
                "source_port": st.integers(min_value=0, max_value=65535),
                "destination_port": st.integers(min_value=0, max_value=65535),
                "stream_id": st.text(
                    alphabet=st.sampled_from(
                        "abcdefghijklmnopqrstuvwxyz"
                        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                        "0123456789_-"
                    ),
                    min_size=1,
                    max_size=64,
                ),
            },
        )
    )
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_property_well_formed_selectors_validate(self, payload):
        """Validates: Requirements 19.1, 19.4

        For any subset of (source_port, destination_port, stream_id)
        that obeys the documented shape, the validator either accepts
        the selector or rejects with the empty-selector error when
        the dict is empty.
        """
        if not payload:
            with pytest.raises(FlowSelectorError) as exc_info:
                validate_flow_selector(payload)
            assert exc_info.value.error_category == "invalid_parameter"
            return
        result = validate_flow_selector(payload)
        if "source_port" in payload:
            assert result.source_port == payload["source_port"]
        if "destination_port" in payload:
            assert result.destination_port == payload["destination_port"]
        if "stream_id" in payload:
            assert result.stream_id == payload["stream_id"]
