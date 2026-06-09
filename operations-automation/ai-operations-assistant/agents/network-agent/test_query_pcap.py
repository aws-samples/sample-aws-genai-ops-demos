"""
Unit and property-based tests for ``main.handle_query_pcap``.

Run from the ``network-agent`` directory:

    python -m pytest test_query_pcap.py -v

These tests exercise the full ``query_pcap`` action handler against
hand-rolled fakes of :func:`athena_helper.run_athena_query` so we can
deterministically verify:

- Validation errors (missing/invalid ``capture_id``, missing/invalid
  ``sql``) never call Athena (Reqs 5.2, 5.3).
- Forbidden SQL constructs (non-SELECT, semicolons, comments,
  forbidden keywords, subqueries) never call Athena (Req 5.3,
  Correctness Property 6).
- The Capture_Id_Predicate is injected into the SQL passed to
  ``run_athena_query`` for every accepted input (Reqs 5.1, 5.7,
  Correctness Property 5).
- Athena failures (``AthenaQueryFailedError``,
  ``AthenaQueryTimeoutError``) produce ``success=false`` envelopes
  with the failure reason and no partial results (Req 5.12).
- Success envelopes set ``metadata.sourceApi =
  "athena:StartQueryExecution"`` and ``metadata.dataFreshness =
  "near-real-time"`` (Req 5.22).
- The full envelope shape conforms to the design's response
  envelope schema (Correctness Property 10).

The tests stub ``main.run_athena_query`` so no real Athena client
or boto3 is needed.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

import main
from athena_helper import (
    AthenaConfigurationError,
    AthenaQueryFailedError,
    AthenaQueryTimeoutError,
)


# ---------------------------------------------------------------------------
# Fake run_athena_query
# ---------------------------------------------------------------------------


class FakeAthena:
    """Records the SQL passed to ``run_athena_query`` and returns a canned response.

    The fake supports two modes:
      * **Returning rows** (default): each call records the SQL and
        returns a fresh shallow copy of ``rows``.
      * **Raising**: when ``raise_exception`` is set, every call
        raises that exception. Useful for exercising the
        AthenaQueryFailed / Timeout / Configuration paths.

    The recorded SQL is exposed as ``calls`` so tests can assert on
    the rewritten SQL the handler produced.
    """

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


def _invoke_query_pcap(params: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke the handler and return the response envelope."""
    return main.handle_query_pcap(params)


def _assert_envelope_shape(response: Dict[str, Any], *, success: bool) -> None:
    """Check the response satisfies the universal envelope schema."""
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


# ---------------------------------------------------------------------------
# Validation: capture_id
# ---------------------------------------------------------------------------


class TestCaptureIdValidation:
    """Req 5.2: capture_id is required and must match Capture_Id_Format."""

    def test_rejects_missing_capture_id(self, fake_athena):
        response = _invoke_query_pcap({"sql": "SELECT * FROM pcap_logs"})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_parameter"
        # Athena is never called.
        assert fake_athena.calls == []

    def test_rejects_empty_capture_id(self, fake_athena):
        response = _invoke_query_pcap(
            {"sql": "SELECT * FROM pcap_logs", "capture_id": ""}
        )
        _assert_envelope_shape(response, success=False)
        assert fake_athena.calls == []

    def test_rejects_non_string_capture_id(self, fake_athena):
        response = _invoke_query_pcap(
            {"sql": "SELECT * FROM pcap_logs", "capture_id": 12345}
        )
        _assert_envelope_shape(response, success=False)
        assert fake_athena.calls == []

    def test_rejects_capture_id_with_invalid_chars(self, fake_athena):
        response = _invoke_query_pcap(
            {"sql": "SELECT * FROM pcap_logs", "capture_id": "abc; drop"}
        )
        _assert_envelope_shape(response, success=False)
        assert fake_athena.calls == []

    def test_rejects_capture_id_too_long(self, fake_athena):
        response = _invoke_query_pcap(
            {"sql": "SELECT * FROM pcap_logs", "capture_id": "a" * 129}
        )
        _assert_envelope_shape(response, success=False)
        assert fake_athena.calls == []


# ---------------------------------------------------------------------------
# Validation: sql shape
# ---------------------------------------------------------------------------


class TestSqlShapeRejections:
    """Req 5.3: forbidden SQL constructs are rejected before any Athena call."""

    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM pcap_logs",
            "INSERT INTO pcap_logs VALUES (1)",
            "UPDATE pcap_logs SET x = 1",
            "DROP TABLE pcap_logs",
            "CREATE TABLE foo AS SELECT * FROM pcap_logs",
            "ALTER TABLE pcap_logs ADD COLUMN x INT",
            "TRUNCATE TABLE pcap_logs",
            "MSCK REPAIR TABLE pcap_logs",
        ],
    )
    def test_rejects_non_select_keywords(self, sql: str, fake_athena):
        response = _invoke_query_pcap({"capture_id": "abc", "sql": sql})
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "invalid_sql"
        assert fake_athena.calls == []

    def test_rejects_semicolon(self, fake_athena):
        response = _invoke_query_pcap(
            {"capture_id": "abc", "sql": "SELECT * FROM pcap_logs;"}
        )
        _assert_envelope_shape(response, success=False)
        assert fake_athena.calls == []

    def test_rejects_line_comment(self, fake_athena):
        response = _invoke_query_pcap(
            {
                "capture_id": "abc",
                "sql": "SELECT * FROM pcap_logs -- comment",
            }
        )
        _assert_envelope_shape(response, success=False)
        assert fake_athena.calls == []

    def test_rejects_block_comment(self, fake_athena):
        response = _invoke_query_pcap(
            {
                "capture_id": "abc",
                "sql": "SELECT * /* comment */ FROM pcap_logs",
            }
        )
        _assert_envelope_shape(response, success=False)
        assert fake_athena.calls == []

    def test_rejects_subquery(self, fake_athena):
        response = _invoke_query_pcap(
            {
                "capture_id": "abc",
                "sql": "SELECT * FROM pcap_logs WHERE x IN (SELECT 1)",
            }
        )
        _assert_envelope_shape(response, success=False)
        assert fake_athena.calls == []

    def test_rejects_union(self, fake_athena):
        response = _invoke_query_pcap(
            {
                "capture_id": "abc",
                "sql": "SELECT * FROM pcap_logs UNION SELECT * FROM pcap_logs",
            }
        )
        _assert_envelope_shape(response, success=False)
        assert fake_athena.calls == []

    def test_rejects_join(self, fake_athena):
        response = _invoke_query_pcap(
            {
                "capture_id": "abc",
                "sql": "SELECT * FROM pcap_logs JOIN x ON 1=1",
            }
        )
        _assert_envelope_shape(response, success=False)
        assert fake_athena.calls == []

    def test_rejects_with_cte(self, fake_athena):
        response = _invoke_query_pcap(
            {
                "capture_id": "abc",
                "sql": "WITH x AS (SELECT 1) SELECT * FROM pcap_logs",
            }
        )
        _assert_envelope_shape(response, success=False)
        assert fake_athena.calls == []

    def test_rejects_other_table(self, fake_athena):
        response = _invoke_query_pcap(
            {
                "capture_id": "abc",
                "sql": "SELECT * FROM other_table",
            }
        )
        _assert_envelope_shape(response, success=False)
        assert fake_athena.calls == []

    def test_rejects_missing_sql(self, fake_athena):
        response = _invoke_query_pcap({"capture_id": "abc"})
        _assert_envelope_shape(response, success=False)
        assert fake_athena.calls == []

    def test_rejects_empty_sql(self, fake_athena):
        response = _invoke_query_pcap({"capture_id": "abc", "sql": ""})
        _assert_envelope_shape(response, success=False)
        assert fake_athena.calls == []

    def test_rejects_oversized_sql(self, fake_athena):
        # 16385 chars — one over MAX_SQL_LENGTH.
        oversized = "SELECT * FROM pcap_logs WHERE x = '" + ("a" * 16385) + "'"
        assert len(oversized) > 16384
        response = _invoke_query_pcap(
            {"capture_id": "abc", "sql": oversized}
        )
        _assert_envelope_shape(response, success=False)
        assert fake_athena.calls == []

    def test_rejects_stacked_query_attack(self, fake_athena):
        """Classic SQL injection: terminate first query and append malicious one."""
        response = _invoke_query_pcap(
            {
                "capture_id": "abc",
                "sql": "SELECT * FROM pcap_logs; DROP TABLE pcap_logs",
            }
        )
        _assert_envelope_shape(response, success=False)
        assert fake_athena.calls == []

    def test_rejects_comment_injection_attack(self, fake_athena):
        """Attacker tries to comment out the predicate injector's AND clause."""
        response = _invoke_query_pcap(
            {
                "capture_id": "abc",
                "sql": "SELECT * FROM pcap_logs WHERE 1=1 -- AND",
            }
        )
        _assert_envelope_shape(response, success=False)
        assert fake_athena.calls == []


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------


class TestSuccessfulQueries:
    """Successful queries inject the predicate, set metadata, and return rows."""

    def test_success_with_simple_select(self, fake_athena):
        fake_athena.rows = [{"frame_size": "1500"}]
        response = _invoke_query_pcap(
            {"capture_id": "cap-001", "sql": "SELECT * FROM pcap_logs"}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["row_count"] == 1
        assert response["data"]["rows"] == [{"frame_size": "1500"}]
        assert response["data"]["capture_id"] == "cap-001"
        # The injected predicate appears in executed_sql.
        assert "capture_id = 'cap-001'" in response["data"]["executed_sql"]

    def test_predicate_injected_into_athena_call(self, fake_athena):
        _invoke_query_pcap(
            {"capture_id": "cap-001", "sql": "SELECT * FROM pcap_logs"}
        )
        assert len(fake_athena.calls) == 1
        assert "capture_id = 'cap-001'" in fake_athena.calls[0]

    def test_predicate_injected_with_existing_where(self, fake_athena):
        _invoke_query_pcap(
            {
                "capture_id": "cap-001",
                "sql": "SELECT * FROM pcap_logs WHERE frame_size > 1500",
            }
        )
        assert len(fake_athena.calls) == 1
        assert "frame_size > 1500" in fake_athena.calls[0]
        assert "AND capture_id = 'cap-001'" in fake_athena.calls[0]

    def test_predicate_injected_before_order_by(self, fake_athena):
        _invoke_query_pcap(
            {
                "capture_id": "cap-001",
                "sql": "SELECT * FROM pcap_logs ORDER BY frame_time",
            }
        )
        executed_sql = fake_athena.calls[0]
        assert executed_sql.find("WHERE capture_id = 'cap-001'") < executed_sql.find(
            "ORDER BY frame_time"
        )

    def test_empty_result_returns_success(self, fake_athena):
        fake_athena.rows = []
        response = _invoke_query_pcap(
            {"capture_id": "cap-001", "sql": "SELECT * FROM pcap_logs"}
        )
        _assert_envelope_shape(response, success=True)
        assert response["data"]["row_count"] == 0
        assert response["data"]["rows"] == []
        assert "no rows" in response["formattedText"].lower()


# ---------------------------------------------------------------------------
# Athena failure paths
# ---------------------------------------------------------------------------


class TestAthenaFailures:
    """Req 5.12: Athena failures produce success=false with no partial results."""

    def test_athena_failure_returns_error(self, fake_athena):
        fake_athena.raise_exception = AthenaQueryFailedError(
            "Athena query qid-001 ended in state FAILED (table not found).",
            query_execution_id="qid-001",
            athena_state="FAILED",
            state_change_reason="table not found",
        )
        response = _invoke_query_pcap(
            {"capture_id": "cap-001", "sql": "SELECT * FROM pcap_logs"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "athena_query_failed"
        assert "FAILED" in response["error"] or "failed" in response["error"].lower()
        assert response["data"] == {}

    def test_athena_timeout_returns_error(self, fake_athena):
        fake_athena.raise_exception = AthenaQueryTimeoutError(
            "Athena query qid-002 did not reach a terminal state within 60s.",
            query_execution_id="qid-002",
            athena_state="RUNNING",
        )
        response = _invoke_query_pcap(
            {"capture_id": "cap-001", "sql": "SELECT * FROM pcap_logs"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "athena_timeout"
        assert response["data"] == {}

    def test_configuration_missing_returns_error(self, fake_athena):
        fake_athena.raise_exception = AthenaConfigurationError(
            "Required environment variable 'GLUE_DATABASE' is not set."
        )
        response = _invoke_query_pcap(
            {"capture_id": "cap-001", "sql": "SELECT * FROM pcap_logs"}
        )
        _assert_envelope_shape(response, success=False)
        assert response["metadata"]["errorCategory"] == "configuration_missing"


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


# Capture_Id_Format alphabet for valid IDs.
_CAPTURE_ID_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789_-"
)


# Forbidden top-level keywords drawn from Req 5.3 plus the design
# constraint, used to assemble adversarial SQL strings.
_ADVERSARIAL_PREFIXES = [
    "INSERT INTO pcap_logs VALUES (1)",
    "UPDATE pcap_logs SET x = 1",
    "DELETE FROM pcap_logs",
    "DROP TABLE pcap_logs",
    "CREATE TABLE foo AS SELECT * FROM pcap_logs",
    "ALTER TABLE pcap_logs ADD COLUMN x INT",
    "TRUNCATE TABLE pcap_logs",
    "MSCK REPAIR TABLE pcap_logs",
]


_ADVERSARIAL_INJECTIONS = [
    "SELECT * FROM pcap_logs;",
    "SELECT * FROM pcap_logs; DROP TABLE pcap_logs",
    "SELECT * FROM pcap_logs -- evil",
    "SELECT * /* evil */ FROM pcap_logs",
    "SELECT * FROM pcap_logs UNION SELECT * FROM other",
    "SELECT * FROM pcap_logs JOIN other ON 1=1",
    "SELECT * FROM pcap_logs WHERE x IN (SELECT 1)",
    "WITH x AS (SELECT 1) SELECT * FROM pcap_logs",
    "SELECT * FROM (SELECT 1) p",
]


class TestProperties:
    """Property tests lifted from Correctness Properties 5, 6, and 10."""

    @given(
        capture_id=st.text(
            alphabet=_CAPTURE_ID_ALPHABET, min_size=1, max_size=128
        ),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    def test_property_predicate_present_on_every_accepted_query(
        self, capture_id: str, monkeypatch
    ):
        """Validates: Requirements 5.1, 5.7 (Correctness Property 5).

        For every valid ``capture_id`` and every accepted SQL shape,
        the SQL forwarded to Athena contains the
        Capture_Id_Predicate.
        """
        fake = FakeAthena(rows=[])
        monkeypatch.setattr(main, "run_athena_query", fake)

        sqls = [
            "SELECT * FROM pcap_logs",
            "SELECT * FROM pcap_logs WHERE frame_size > 1500",
            "SELECT * FROM pcap_logs ORDER BY frame_time",
            "SELECT COUNT(*) FROM pcap_logs",
        ]
        for sql in sqls:
            response = _invoke_query_pcap(
                {"capture_id": capture_id, "sql": sql}
            )
            assert response["success"] is True, (
                f"Expected success for capture_id={capture_id!r} sql={sql!r}, "
                f"got {response.get('error')!r}"
            )
            executed = fake.calls[-1]
            assert f"capture_id = '{capture_id}'" in executed

    @given(sql=st.sampled_from(_ADVERSARIAL_PREFIXES + _ADVERSARIAL_INJECTIONS))
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_property_adversarial_sql_never_reaches_athena(
        self, sql: str, monkeypatch
    ):
        """Validates: Requirements 5.3 (Correctness Property 6).

        For every adversarial SQL input, Athena is never called.
        """
        fake = FakeAthena(rows=[])
        monkeypatch.setattr(main, "run_athena_query", fake)

        response = _invoke_query_pcap(
            {"capture_id": "valid-id", "sql": sql}
        )
        assert response["success"] is False
        assert response["metadata"]["errorCategory"] == "invalid_sql"
        assert fake.calls == []

    @given(
        capture_id=st.text(
            alphabet=_CAPTURE_ID_ALPHABET, min_size=1, max_size=128
        ),
        sql=st.sampled_from(
            [
                "SELECT * FROM pcap_logs",
                "SELECT * FROM pcap_logs WHERE frame_size > 1500",
                "SELECT * FROM pcap_logs ORDER BY frame_time",
                "SELECT * FROM pcap_logs LIMIT 10",
            ]
        ),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_property_response_envelope_shape_invariant(
        self, capture_id: str, sql: str, monkeypatch
    ):
        """Validates: Requirements 1.7, 5.22 (Correctness Property 10).

        Every successful response satisfies the universal envelope
        schema with the fixed sourceApi and dataFreshness values.
        """
        fake = FakeAthena(rows=[{"a": "1"}])
        monkeypatch.setattr(main, "run_athena_query", fake)

        response = _invoke_query_pcap(
            {"capture_id": capture_id, "sql": sql}
        )
        _assert_envelope_shape(response, success=True)
