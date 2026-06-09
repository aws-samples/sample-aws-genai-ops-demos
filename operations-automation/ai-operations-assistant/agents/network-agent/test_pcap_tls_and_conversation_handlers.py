"""
Unit and property-based tests for Task 15 Pcap_Query_Action handlers:
``handle_check_tls_hello_size`` and ``handle_get_conversation_stats``.

Run from the ``network-agent`` directory:

    python -m pytest test_pcap_tls_and_conversation_handlers.py -v

These tests stub :func:`main.run_athena_query` with a hand-rolled fake
so we can deterministically verify:

- Validation errors (missing/invalid ``capture_id``, invalid ``top_n``)
  never call Athena (Reqs 5.7, 5.10).
- The Capture_Id_Predicate is inlined into every executed query
  (Reqs 5.7, 5.9, 5.10 — partition pruning).
- ``check_tls_hello_size`` SQL filters on ``tls_handshake_type = 1``
  and projects the response columns mandated by Req 5.9
  (``frame_size``, ``fragment_count``, ``source_ip``, ``source_port``,
  ``destination_ip``, ``destination_port``).
- ``get_conversation_stats`` SQL groups by the conversation 5-tuple,
  orders by total bytes descending, and uses a ``LIMIT`` matching the
  resolved ``top_n`` (Reqs 5.10, 5.11).
- ``top_n`` defaults to 20 when omitted (Req 5.11).
- Athena failures (``AthenaQueryFailedError``,
  ``AthenaQueryTimeoutError``, ``AthenaConfigurationError``) produce
  ``success=false`` envelopes with the correct ``errorCategory`` and
  no partial results (Req 5.12).
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
    """Every accepted query must inline the Capture_Id_Predicate (Req 5.7)."""
    assert f"capture_id = '{capture_id}'" in executed_sql, (
        f"Capture_Id_Predicate missing from executed SQL: {executed_sql!r}"
    )


# Capture_Id_Format alphabet for Hypothesis strategies.
_CAPTURE_ID_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789_-"
)


# ---------------------------------------------------------------------------
# check_tls_hello_size tests
# ---------------------------------------------------------------------------


class TestCheckTlsHelloSizeValidation:
    """Reqs 5.7: capture_id required and validated before Athena call."""

    def test_rejects_missing_capture_id(self, fake_athena):
        response = main.handle_check_tls_hello_size({})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    def test_rejects_empty_capture_id(self, fake_athena):
        response = main.handle_check_tls_hello_size({"capture_id": ""})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    def test_rejects_invalid_capture_id(self, fake_athena):
        response = main.handle_check_tls_hello_size(
            {"capture_id": "bad space"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    def test_rejects_capture_id_too_long(self, fake_athena):
        response = main.handle_check_tls_hello_size(
            {"capture_id": "a" * 129}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    def test_rejects_non_string_capture_id(self, fake_athena):
        response = main.handle_check_tls_hello_size({"capture_id": 12345})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    def test_accepts_non_dict_params(self, fake_athena):
        # Non-dict params should be coerced to {} which then fails validation
        # for the missing capture_id.
        response = main.handle_check_tls_hello_size("not a dict")  # type: ignore[arg-type]
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []


class TestCheckTlsHelloSizeHappyPath:
    """Reqs 5.9: SQL filters on TLS Client Hello and projects the documented columns."""

    def test_predicate_and_handshake_filter_inlined(self, fake_athena):
        main.handle_check_tls_hello_size({"capture_id": "cap-001"})
        assert len(fake_athena.calls) == 1
        sql = fake_athena.calls[0]
        _assert_capture_id_predicate(sql, "cap-001")
        # Req 5.9: filter on TLS Client Hello (tls_handshake_type = 1).
        assert "tls_handshake_type = 1" in sql

    def test_response_columns_match_req_5_9(self, fake_athena):
        """Req 5.9: response columns are frame_size, fragment_count,
        source_ip, source_port, destination_ip, destination_port."""
        main.handle_check_tls_hello_size({"capture_id": "cap-001"})
        sql = fake_athena.calls[0]
        # Aliased projections produce the required column names in the
        # Athena response.
        assert "AS fragment_count" in sql
        assert "AS source_ip" in sql
        assert "AS source_port" in sql
        assert "AS destination_ip" in sql
        assert "AS destination_port" in sql
        # frame_size is selected directly without an alias.
        assert "SELECT frame_size" in sql

    def test_returns_rows_in_data(self, fake_athena):
        # Athena would return the columns named by the SELECT aliases.
        fake_athena.rows = [
            {
                "frame_size": "3520",
                "fragment_count": "3",
                "source_ip": "10.0.0.10",
                "source_port": "55432",
                "destination_ip": "203.0.113.5",
                "destination_port": "443",
            }
        ]
        response = main.handle_check_tls_hello_size(
            {"capture_id": "cap-001"}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["row_count"] == 1
        assert response["data"]["rows"] == fake_athena.rows
        assert response["data"]["capture_id"] == "cap-001"

    def test_empty_partition_returns_friendly_message(self, fake_athena):
        """Req 5.23: empty result set returns success=true with friendly text."""
        response = main.handle_check_tls_hello_size(
            {"capture_id": "cap-001"}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["row_count"] == 0
        assert response["data"]["rows"] == []
        assert "no matching" in response["formattedText"].lower()


class TestCheckTlsHelloSizeAthenaFailures:
    """Req 5.12: Athena failures produce success=false with no partial results."""

    def test_athena_failure_is_surfaced(self, fake_athena):
        fake_athena.raise_exception = AthenaQueryFailedError(
            "Athena query qid-001 ended in state FAILED.",
            query_execution_id="qid-001",
            athena_state="FAILED",
            state_change_reason="invalid column",
        )
        response = main.handle_check_tls_hello_size(
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
        response = main.handle_check_tls_hello_size(
            {"capture_id": "cap-001"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "athena_timeout"
        assert response["data"] == {}

    def test_configuration_error_is_surfaced(self, fake_athena):
        fake_athena.raise_exception = AthenaConfigurationError(
            "Required environment variable 'GLUE_DATABASE' is not set."
        )
        response = main.handle_check_tls_hello_size(
            {"capture_id": "cap-001"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "configuration_missing"


# ---------------------------------------------------------------------------
# get_conversation_stats tests
# ---------------------------------------------------------------------------


class TestGetConversationStatsValidation:
    """Reqs 5.7, 5.10: capture_id and top_n validated before Athena call."""

    def test_rejects_missing_capture_id(self, fake_athena):
        response = main.handle_get_conversation_stats({})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    def test_rejects_invalid_capture_id(self, fake_athena):
        response = main.handle_get_conversation_stats(
            {"capture_id": "bad/slash"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []

    @pytest.mark.parametrize(
        "bad_top_n",
        [
            0,                # below range (Req 5.10)
            -1,
            1001,             # above range (Req 5.10)
            10000,
            "20",             # not an int (string)
            5.5,              # not an int (float)
            True,             # bool is rejected explicitly
        ],
    )
    def test_rejects_invalid_top_n(self, fake_athena, bad_top_n):
        response = main.handle_get_conversation_stats(
            {"capture_id": "cap-001", "top_n": bad_top_n}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_athena.calls == []


class TestGetConversationStatsHappyPath:
    """Reqs 5.10, 5.11: SQL groups conversations and applies the configured top_n."""

    def test_default_top_n_is_20(self, fake_athena):
        """Req 5.11: when top_n is omitted, the default is 20."""
        response = main.handle_get_conversation_stats(
            {"capture_id": "cap-001"}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["top_n"] == 20
        assert len(fake_athena.calls) == 1
        sql = fake_athena.calls[0]
        _assert_capture_id_predicate(sql, "cap-001")
        assert "LIMIT 20" in sql

    def test_custom_top_n_used_in_limit(self, fake_athena):
        """Req 5.10: supplied top_n is interpolated into the LIMIT clause."""
        response = main.handle_get_conversation_stats(
            {"capture_id": "cap-001", "top_n": 50}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["top_n"] == 50
        sql = fake_athena.calls[0]
        assert "LIMIT 50" in sql

    def test_top_n_boundary_1_accepted(self, fake_athena):
        """Req 5.10: lower bound 1 is accepted."""
        response = main.handle_get_conversation_stats(
            {"capture_id": "cap-001", "top_n": 1}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["top_n"] == 1
        assert "LIMIT 1" in fake_athena.calls[0]

    def test_top_n_boundary_1000_accepted(self, fake_athena):
        """Req 5.10: upper bound 1000 is accepted."""
        response = main.handle_get_conversation_stats(
            {"capture_id": "cap-001", "top_n": 1000}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["top_n"] == 1000
        assert "LIMIT 1000" in fake_athena.calls[0]

    def test_groupby_and_orderby_present(self, fake_athena):
        """Req 5.10: group by conversation tuple, order by total bytes desc."""
        main.handle_get_conversation_stats({"capture_id": "cap-001"})
        sql = fake_athena.calls[0]
        # Conversation 5-tuple grouping.
        assert (
            "GROUP BY src_ip, src_port, dst_ip, dst_port, protocol"
            in sql
        )
        # Total bytes descending.
        assert "ORDER BY total_bytes DESC" in sql
        # Aggregates: total bytes (SUM(frame_size)) and packet count (COUNT(*)).
        assert "SUM(frame_size)" in sql
        assert "COUNT(*)" in sql

    def test_returns_grouped_rows(self, fake_athena):
        fake_athena.rows = [
            {
                "src_ip": "10.0.0.10",
                "src_port": "55432",
                "dst_ip": "10.0.0.20",
                "dst_port": "443",
                "protocol": "tcp",
                "total_bytes": "1234567",
                "packet_count": "1500",
            },
            {
                "src_ip": "10.0.0.20",
                "src_port": "443",
                "dst_ip": "10.0.0.10",
                "dst_port": "55432",
                "protocol": "tcp",
                "total_bytes": "987654",
                "packet_count": "1200",
            },
        ]
        response = main.handle_get_conversation_stats(
            {"capture_id": "cap-001", "top_n": 10}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["row_count"] == 2
        assert response["data"]["capture_id"] == "cap-001"
        assert response["data"]["top_n"] == 10
        assert response["data"]["rows"] == fake_athena.rows

    def test_empty_partition_returns_friendly_message(self, fake_athena):
        """Req 5.23: empty result set returns success=true with friendly text."""
        response = main.handle_get_conversation_stats(
            {"capture_id": "cap-001"}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["row_count"] == 0
        assert response["data"]["rows"] == []
        assert "no matching" in response["formattedText"].lower()


class TestGetConversationStatsAthenaFailures:
    """Req 5.12: Athena failures produce success=false with no partial results."""

    def test_athena_failure_is_surfaced(self, fake_athena):
        fake_athena.raise_exception = AthenaQueryFailedError(
            "Athena query qid-003 ended in state FAILED.",
            query_execution_id="qid-003",
            athena_state="FAILED",
            state_change_reason="invalid column",
        )
        response = main.handle_get_conversation_stats(
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
        response = main.handle_get_conversation_stats(
            {"capture_id": "cap-001"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "athena_timeout"


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


class TestProperties:
    """Property tests covering the two Task 15 handlers.

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
    def test_property_check_tls_hello_size_capture_id_predicate_present(
        self, capture_id: str, monkeypatch
    ):
        """Validates: Requirements 5.7, 5.9.

        For every valid ``capture_id``, the SQL forwarded to Athena
        contains the Capture_Id_Predicate and the TLS Client Hello
        filter.
        """
        fake = FakeAthena(rows=[])
        monkeypatch.setattr(main, "run_athena_query", fake)
        response = main.handle_check_tls_hello_size(
            {"capture_id": capture_id}
        )
        assert response["success"] is True
        assert len(fake.calls) == 1
        sql = fake.calls[0]
        _assert_capture_id_predicate(sql, capture_id)
        assert "tls_handshake_type = 1" in sql

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
    def test_property_get_conversation_stats_default_top_n(
        self, capture_id: str, monkeypatch
    ):
        """Validates: Requirements 5.7, 5.10, 5.11.

        For every valid ``capture_id`` with no ``top_n`` parameter,
        the default ``top_n`` of 20 is interpolated into the LIMIT
        clause and the Capture_Id_Predicate is present.
        """
        fake = FakeAthena(rows=[])
        monkeypatch.setattr(main, "run_athena_query", fake)
        response = main.handle_get_conversation_stats(
            {"capture_id": capture_id}
        )
        assert response["success"] is True
        assert response["data"]["top_n"] == 20
        sql = fake.calls[0]
        _assert_capture_id_predicate(sql, capture_id)
        assert "LIMIT 20" in sql

    @given(
        capture_id=st.text(
            alphabet=_CAPTURE_ID_ALPHABET, min_size=1, max_size=128
        ),
        top_n=st.integers(min_value=1, max_value=1000),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.function_scoped_fixture,
        ],
    )
    def test_property_get_conversation_stats_top_n_accepted(
        self, capture_id: str, top_n: int, monkeypatch
    ):
        """Validates: Requirement 5.10.

        Every integer in ``[1, 1000]`` is accepted as ``top_n`` and
        inlined into the SQL ``LIMIT`` clause.
        """
        fake = FakeAthena(rows=[])
        monkeypatch.setattr(main, "run_athena_query", fake)
        response = main.handle_get_conversation_stats(
            {"capture_id": capture_id, "top_n": top_n}
        )
        assert response["success"] is True
        assert response["data"]["top_n"] == top_n
        assert f"LIMIT {top_n}" in fake.calls[0]

    @given(
        top_n=st.one_of(
            st.integers(max_value=0),
            st.integers(min_value=1001),
        ),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.function_scoped_fixture,
        ],
    )
    def test_property_get_conversation_stats_top_n_out_of_range_rejected(
        self, top_n: int, monkeypatch
    ):
        """Validates: Requirement 5.10 (range rejection).

        Every integer outside ``[1, 1000]`` is rejected without an
        Athena call.
        """
        fake = FakeAthena(rows=[])
        monkeypatch.setattr(main, "run_athena_query", fake)
        response = main.handle_get_conversation_stats(
            {"capture_id": "valid-id", "top_n": top_n}
        )
        assert response["success"] is False
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake.calls == []

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
            (
                {"capture_id": "valid-id"},
                main.handle_check_tls_hello_size,
            ),
            (
                {"capture_id": "valid-id"},
                main.handle_get_conversation_stats,
            ),
        ]:
            response = action(params)
            _assert_envelope_shape(response, success=True)
            assert response["data"]["row_count"] == len(sql_returns_rows)
