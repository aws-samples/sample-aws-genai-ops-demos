"""
Unit and property-based tests for ``athena_helper.py``.

Run from the ``network-agent`` directory:

    python -m pytest test_athena_helper.py -v

The tests exercise :func:`athena_helper.run_athena_query` against a
hand-rolled fake Athena client so we can deterministically:

- Drive the query through ``RUNNING`` → ``SUCCEEDED`` and verify
  the returned rows match the boto3 ``GetQueryResults`` shape we
  expect (column-keyed dicts, NULL → ``None``).
- Drive the query through ``RUNNING`` → ``FAILED`` and verify the
  helper raises :class:`AthenaQueryFailedError` with the
  ``StateChangeReason`` propagated verbatim — and that
  ``GetQueryResults`` is **never** called (Req 5.12 forbids partial
  results).
- Stub :func:`time.monotonic` and :func:`time.sleep` so the
  60-second wall-clock budget is enforced in milliseconds, and
  verify :class:`AthenaQueryTimeoutError` is raised plus
  ``StopQueryExecution`` is called best-effort.
- Verify ``GetQueryResults`` pagination: rows from every page are
  merged in order, and the synthetic header row from the *first*
  page is dropped while subsequent pages keep their full row sets.
- Verify environment-variable handling: ``GLUE_DATABASE`` is
  required; ``DATA_BUCKET_NAME`` is required only when the caller
  does not pass an explicit ``output_location``.

The fake Athena client implements only the four boto3 methods our
helper actually calls (``start_query_execution``,
``get_query_execution``, ``get_query_results``,
``stop_query_execution``) plus the ``get_paginator`` interface used
by the helper. A small ``ScriptedQuery`` test helper sequences each
``GetQueryExecution`` poll's response so a single test reads
naturally as "first poll: RUNNING; second poll: SUCCEEDED".
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence
from unittest import mock

import pytest
from botocore.exceptions import ClientError
from hypothesis import HealthCheck, given, settings, strategies as st

import athena_helper
from athena_helper import (
    ATHENA_POLL_INTERVAL_SECONDS,
    ATHENA_QUERY_BUDGET_SECONDS,
    AthenaConfigurationError,
    AthenaQueryFailedError,
    AthenaQueryTimeoutError,
    DATA_BUCKET_NAME_ENV,
    GLUE_DATABASE_ENV,
    run_athena_query,
)


# ---------------------------------------------------------------------------
# Fake Athena client
#
# The helper makes exactly four kinds of calls:
#   1. start_query_execution(**kwargs) → {"QueryExecutionId": "..."}
#   2. get_query_execution(QueryExecutionId=...) → status dict
#   3. get_paginator("get_query_results").paginate(QueryExecutionId=...)
#       → iterable of page dicts
#   4. stop_query_execution(QueryExecutionId=...) → {} (best-effort)
#
# The fake records each call so tests can assert on call shape (e.g.
# "WorkGroup not in start_params when work_group is None") and
# sequences scripted responses through a small queue per query id.
# ---------------------------------------------------------------------------


class ScriptedQuery:
    """Sequenced ``GetQueryExecution`` responses for one query id.

    Each :meth:`pop` call returns the next status dict in the sequence
    and stays on the final entry once exhausted (so a "stuck RUNNING"
    test can poll indefinitely without IndexError).

    Args:
        states: Each entry is either a state string (e.g. ``"RUNNING"``,
            ``"SUCCEEDED"``) or a tuple ``(state, reason)``. Tuples
            populate ``StateChangeReason``.
    """

    def __init__(self, states: Sequence[Any]) -> None:
        self.states = [s if isinstance(s, tuple) else (s, None) for s in states]
        self._index = 0

    def pop(self) -> Dict[str, Any]:
        state, reason = self.states[min(self._index, len(self.states) - 1)]
        self._index += 1
        body: Dict[str, Any] = {"State": state}
        if reason is not None:
            body["StateChangeReason"] = reason
        return {"QueryExecution": {"Status": body}}


class FakePaginator:
    """Minimal stand-in for boto3's ``Paginator``."""

    def __init__(self, pages: Sequence[Dict[str, Any]]) -> None:
        self._pages = list(pages)

    def paginate(self, **_kwargs):  # noqa: D401 - matches boto3 signature
        return iter(self._pages)


class FakeAthenaClient:
    """Hand-rolled fake matching the four methods the helper calls."""

    def __init__(
        self,
        scripted: ScriptedQuery,
        result_pages: Optional[Sequence[Dict[str, Any]]] = None,
        start_failure: Optional[Exception] = None,
    ) -> None:
        self.scripted = scripted
        self.result_pages = list(result_pages or [])
        self.start_failure = start_failure
        self.start_calls: List[Dict[str, Any]] = []
        self.get_calls: List[Dict[str, Any]] = []
        self.results_calls: List[Dict[str, Any]] = []
        self.stop_calls: List[Dict[str, Any]] = []
        self.next_query_id = "qid-0001"

    # boto3 interface ------------------------------------------------

    def start_query_execution(self, **kwargs: Any) -> Dict[str, Any]:
        self.start_calls.append(kwargs)
        if self.start_failure is not None:
            raise self.start_failure
        return {"QueryExecutionId": self.next_query_id}

    def get_query_execution(self, QueryExecutionId: str) -> Dict[str, Any]:
        self.get_calls.append({"QueryExecutionId": QueryExecutionId})
        return self.scripted.pop()

    def get_paginator(self, operation_name: str) -> FakePaginator:
        assert operation_name == "get_query_results", (
            f"unexpected paginator request: {operation_name}"
        )
        return FakePaginator(self.result_pages)

    def stop_query_execution(self, QueryExecutionId: str) -> Dict[str, Any]:
        self.stop_calls.append({"QueryExecutionId": QueryExecutionId})
        return {}


# ---------------------------------------------------------------------------
# Result-page builders
#
# Athena GetQueryResults responses have a nested shape that's noisy to
# spell out per-test. These helpers build a single page dict from a
# friendly column / row representation.
# ---------------------------------------------------------------------------


def _make_row(values: Sequence[Optional[str]]) -> Dict[str, Any]:
    """Build one ``Rows[]`` entry with NULL-aware cell shapes."""
    data: List[Dict[str, Any]] = []
    for value in values:
        if value is None:
            # boto3 omits ``VarCharValue`` entirely on SQL NULL.
            data.append({})
        else:
            data.append({"VarCharValue": value})
    return {"Data": data}


def _make_page(
    columns: Sequence[str],
    rows: Sequence[Sequence[Optional[str]]],
    *,
    include_header: bool = True,
) -> Dict[str, Any]:
    """Build a full ``ResultSet`` page.

    Args:
        columns: Column names exposed via ``ColumnInfo``.
        rows: Each entry is a sequence of cell values (or ``None`` for SQL NULL).
        include_header: Whether to prepend the synthetic header row that
            Athena emits on the first page. Set to ``False`` for
            subsequent pages.
    """
    raw_rows: List[Dict[str, Any]] = []
    if include_header:
        raw_rows.append(_make_row(list(columns)))
    raw_rows.extend(_make_row(r) for r in rows)
    return {
        "ResultSet": {
            "ResultSetMetadata": {
                "ColumnInfo": [{"Name": name} for name in columns],
            },
            "Rows": raw_rows,
        }
    }


# ---------------------------------------------------------------------------
# Fixture: pristine env + client
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Ensure each test starts with a blank env and no cached client."""
    # Clear the helper's lazy client so each test installs its own fake.
    monkeypatch.setattr(athena_helper, "_athena_client", None, raising=False)

    # Wipe the helper's two env vars; tests opt back in with
    # ``monkeypatch.setenv`` as needed.
    monkeypatch.delenv(GLUE_DATABASE_ENV, raising=False)
    monkeypatch.delenv(DATA_BUCKET_NAME_ENV, raising=False)
    yield


def _install_fake(monkeypatch, fake: FakeAthenaClient) -> None:
    """Wire ``athena_helper`` to use ``fake`` as its Athena client."""
    # Bypass ``boto3.client("athena", ...)`` entirely by pre-populating
    # the lazy singleton. Any subsequent ``_get_athena_client()`` call
    # returns the fake.
    monkeypatch.setattr(athena_helper, "_athena_client", fake, raising=False)


def _install_zero_sleep(monkeypatch) -> List[float]:
    """Replace ``time.sleep`` in the helper with a no-op; record durations."""
    durations: List[float] = []

    def fake_sleep(seconds: float) -> None:
        durations.append(seconds)

    monkeypatch.setattr(athena_helper.time, "sleep", fake_sleep)
    return durations


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestSucceededQuery:
    """``RUNNING`` → ``SUCCEEDED`` returns rows as column-keyed dicts."""

    def test_returns_rows_when_query_succeeds(self, monkeypatch):
        """Single-page result with one row should map to one dict."""
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")

        scripted = ScriptedQuery(["RUNNING", "SUCCEEDED"])
        page = _make_page(
            columns=["frame_size", "src_ip"],
            rows=[("1500", "10.0.0.1")],
        )
        fake = FakeAthenaClient(scripted, result_pages=[page])
        _install_fake(monkeypatch, fake)
        _install_zero_sleep(monkeypatch)

        rows = run_athena_query("SELECT * FROM pcap_logs WHERE capture_id='c1'")

        assert rows == [{"frame_size": "1500", "src_ip": "10.0.0.1"}]
        assert len(fake.start_calls) == 1
        assert len(fake.get_calls) == 2  # poll until SUCCEEDED
        assert len(fake.stop_calls) == 0  # no cancel on success

    def test_default_output_location_uses_data_bucket_env(self, monkeypatch):
        """Without explicit ``output_location``, the helper builds an s3:// URI."""
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")

        scripted = ScriptedQuery(["SUCCEEDED"])
        page = _make_page(columns=["a"], rows=[])
        fake = FakeAthenaClient(scripted, result_pages=[page])
        _install_fake(monkeypatch, fake)
        _install_zero_sleep(monkeypatch)

        run_athena_query("SELECT 1")

        start_kwargs = fake.start_calls[0]
        assert (
            start_kwargs["ResultConfiguration"]["OutputLocation"]
            == "s3://goat-net-data-bucket/athena-results/"
        )
        assert start_kwargs["QueryExecutionContext"] == {"Database": "goat_network"}
        # WorkGroup is omitted when caller passes None — matches Athena's
        # "fall back to the caller's default workgroup" behavior.
        assert "WorkGroup" not in start_kwargs

    def test_explicit_output_location_overrides_env(self, monkeypatch):
        """Caller-supplied ``output_location`` wins over ``DATA_BUCKET_NAME``."""
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")

        scripted = ScriptedQuery(["SUCCEEDED"])
        fake = FakeAthenaClient(scripted, result_pages=[_make_page(["a"], [])])
        _install_fake(monkeypatch, fake)
        _install_zero_sleep(monkeypatch)

        run_athena_query("SELECT 1", output_location="s3://override/path/")

        assert (
            fake.start_calls[0]["ResultConfiguration"]["OutputLocation"]
            == "s3://override/path/"
        )

    def test_explicit_output_location_does_not_require_data_bucket_env(self, monkeypatch):
        """When caller passes ``output_location``, ``DATA_BUCKET_NAME`` is unused."""
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        # DATA_BUCKET_NAME deliberately unset.

        scripted = ScriptedQuery(["SUCCEEDED"])
        fake = FakeAthenaClient(scripted, result_pages=[_make_page(["a"], [])])
        _install_fake(monkeypatch, fake)
        _install_zero_sleep(monkeypatch)

        # Should not raise even though DATA_BUCKET_NAME is missing.
        run_athena_query("SELECT 1", output_location="s3://override/path/")

    def test_work_group_passed_when_supplied(self, monkeypatch):
        """``work_group`` is forwarded verbatim to ``StartQueryExecution``."""
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")

        scripted = ScriptedQuery(["SUCCEEDED"])
        fake = FakeAthenaClient(scripted, result_pages=[_make_page(["a"], [])])
        _install_fake(monkeypatch, fake)
        _install_zero_sleep(monkeypatch)

        run_athena_query("SELECT 1", work_group="goat-network-wg")

        assert fake.start_calls[0]["WorkGroup"] == "goat-network-wg"

    def test_null_cells_become_python_none(self, monkeypatch):
        """SQL NULL (no ``VarCharValue``) maps to Python ``None``, not ``""``."""
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")

        scripted = ScriptedQuery(["SUCCEEDED"])
        page = _make_page(
            columns=["a", "b"],
            rows=[("alpha", None), (None, "beta")],
        )
        fake = FakeAthenaClient(scripted, result_pages=[page])
        _install_fake(monkeypatch, fake)
        _install_zero_sleep(monkeypatch)

        rows = run_athena_query("SELECT a, b FROM pcap_logs")

        assert rows == [
            {"a": "alpha", "b": None},
            {"a": None, "b": "beta"},
        ]

    def test_empty_result_set_returns_empty_list(self, monkeypatch):
        """Successful query with zero data rows returns ``[]``.

        Validates Req 5.23: empty partitions must surface as an empty
        ``data`` list with ``success=true`` upstream.
        """
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")

        scripted = ScriptedQuery(["SUCCEEDED"])
        page = _make_page(columns=["frame_size"], rows=[])
        fake = FakeAthenaClient(scripted, result_pages=[page])
        _install_fake(monkeypatch, fake)
        _install_zero_sleep(monkeypatch)

        rows = run_athena_query("SELECT 1")

        assert rows == []


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestPagination:
    """Multi-page ``GetQueryResults`` walks every page in order."""

    def test_drops_header_only_on_first_page(self, monkeypatch):
        """The synthetic header appears on page 1 only and must be dropped there.

        Subsequent pages are pure data; we must NOT skip a real row by
        pretending each page has a header.
        """
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")

        page_one = _make_page(
            columns=["x"],
            rows=[("p1-row1",), ("p1-row2",)],
            include_header=True,
        )
        page_two = _make_page(
            columns=["x"],
            rows=[("p2-row1",), ("p2-row2",), ("p2-row3",)],
            include_header=False,
        )
        page_three = _make_page(
            columns=["x"],
            rows=[("p3-row1",)],
            include_header=False,
        )

        scripted = ScriptedQuery(["SUCCEEDED"])
        fake = FakeAthenaClient(
            scripted, result_pages=[page_one, page_two, page_three]
        )
        _install_fake(monkeypatch, fake)
        _install_zero_sleep(monkeypatch)

        rows = run_athena_query("SELECT x FROM pcap_logs")

        assert [r["x"] for r in rows] == [
            "p1-row1",
            "p1-row2",
            "p2-row1",
            "p2-row2",
            "p2-row3",
            "p3-row1",
        ]


# ---------------------------------------------------------------------------
# Failure path (Req 5.12)
# ---------------------------------------------------------------------------


class TestFailedQuery:
    """Terminal ``FAILED``/``CANCELLED`` raises typed exception, no partial results.

    Validates Req 5.12.
    """

    def test_failed_state_raises_typed_exception(self, monkeypatch):
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")

        scripted = ScriptedQuery([
            "RUNNING",
            ("FAILED", "Column 'capture_id' cannot be resolved"),
        ])
        fake = FakeAthenaClient(scripted, result_pages=[])
        _install_fake(monkeypatch, fake)
        _install_zero_sleep(monkeypatch)

        with pytest.raises(AthenaQueryFailedError) as exc:
            run_athena_query("SELECT * FROM bad_table")

        assert exc.value.athena_state == "FAILED"
        assert "cannot be resolved" in exc.value.state_change_reason
        assert exc.value.query_execution_id == "qid-0001"

    def test_cancelled_state_raises_typed_exception(self, monkeypatch):
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")

        scripted = ScriptedQuery([("CANCELLED", "User cancelled")])
        fake = FakeAthenaClient(scripted, result_pages=[])
        _install_fake(monkeypatch, fake)
        _install_zero_sleep(monkeypatch)

        with pytest.raises(AthenaQueryFailedError) as exc:
            run_athena_query("SELECT 1")

        assert exc.value.athena_state == "CANCELLED"

    def test_failed_query_does_not_call_get_query_results(self, monkeypatch):
        """Req 5.12: partial results SHALL NOT be returned on Athena failure."""
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")

        # If the helper accidentally tried to fetch results, accessing
        # the empty paginator's first page would still yield an
        # iterable; we instead replace the paginator factory with a
        # spy that records the call.
        scripted = ScriptedQuery([("FAILED", "boom")])
        fake = FakeAthenaClient(scripted, result_pages=[])
        get_paginator_calls: List[str] = []
        original_get_paginator = fake.get_paginator

        def spy_get_paginator(name: str) -> FakePaginator:
            get_paginator_calls.append(name)
            return original_get_paginator(name)

        fake.get_paginator = spy_get_paginator  # type: ignore[assignment]
        _install_fake(monkeypatch, fake)
        _install_zero_sleep(monkeypatch)

        with pytest.raises(AthenaQueryFailedError):
            run_athena_query("SELECT 1")

        assert get_paginator_calls == [], (
            "Athena failure path must not request GetQueryResults"
        )

    def test_failed_query_with_no_state_reason_uses_default_text(self, monkeypatch):
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")

        scripted = ScriptedQuery([("FAILED", None)])  # no reason supplied
        fake = FakeAthenaClient(scripted, result_pages=[])
        _install_fake(monkeypatch, fake)
        _install_zero_sleep(monkeypatch)

        with pytest.raises(AthenaQueryFailedError) as exc:
            run_athena_query("SELECT 1")

        assert "Unknown error" in exc.value.state_change_reason


# ---------------------------------------------------------------------------
# Timeout path (Req 5.22)
# ---------------------------------------------------------------------------


class TestTimeoutBudget:
    """Wall-clock budget enforcement.

    Tests stub :func:`time.monotonic` so the 60 s budget elapses in
    deterministic ticks, and stub :func:`time.sleep` to a no-op so
    the loop spins instantly.
    """

    def _install_clock(self, monkeypatch, ticks: List[float]) -> None:
        """Replace ``time.monotonic`` with a sequence of fixed values."""
        index = {"i": 0}

        def fake_monotonic() -> float:
            i = index["i"]
            index["i"] = i + 1
            return ticks[min(i, len(ticks) - 1)]

        monkeypatch.setattr(athena_helper.time, "monotonic", fake_monotonic)

    def test_stuck_running_raises_timeout(self, monkeypatch):
        """A query that never reaches a terminal state hits the budget cap."""
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")

        # Tick sequence:
        #   1. deadline = monotonic() + 60   → t=0   so deadline = 60
        #   2. budget check (start of loop)  → t=10  (under deadline)
        #   3. remaining computation         → t=11
        #   4. budget check (next loop)      → t=70  → triggers timeout
        #   5. timeout raise / stop call     → t=71
        ticks = [0, 10, 11, 70, 71]
        self._install_clock(monkeypatch, ticks)

        scripted = ScriptedQuery(["RUNNING"])  # stays RUNNING forever
        fake = FakeAthenaClient(scripted, result_pages=[])
        _install_fake(monkeypatch, fake)
        _install_zero_sleep(monkeypatch)

        with pytest.raises(AthenaQueryTimeoutError) as exc:
            run_athena_query("SELECT 1")

        assert exc.value.query_execution_id == "qid-0001"
        # Best-effort cancellation must run on timeout.
        assert fake.stop_calls == [{"QueryExecutionId": "qid-0001"}]
        # The helper should report the last observed state.
        assert exc.value.athena_state == "RUNNING"

    def test_stop_query_failure_is_swallowed(self, monkeypatch):
        """If ``StopQueryExecution`` raises, the timeout still propagates."""
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")

        ticks = [0, 70, 71]  # budget exhausted on first loop check
        self._install_clock(monkeypatch, ticks)

        scripted = ScriptedQuery(["RUNNING"])
        fake = FakeAthenaClient(scripted, result_pages=[])

        def boom(QueryExecutionId: str) -> None:
            raise ClientError(
                {"Error": {"Code": "InvalidRequestException", "Message": "no-op"}},
                "StopQueryExecution",
            )

        fake.stop_query_execution = boom  # type: ignore[assignment]
        _install_fake(monkeypatch, fake)
        _install_zero_sleep(monkeypatch)

        with pytest.raises(AthenaQueryTimeoutError):
            run_athena_query("SELECT 1")


# ---------------------------------------------------------------------------
# Configuration errors
# ---------------------------------------------------------------------------


class TestConfiguration:
    """Missing env vars surface as :class:`AthenaConfigurationError`."""

    def test_missing_glue_database_raises(self, monkeypatch):
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")
        # GLUE_DATABASE deliberately unset (auto-fixture wiped it).

        scripted = ScriptedQuery(["SUCCEEDED"])
        fake = FakeAthenaClient(scripted, result_pages=[])
        _install_fake(monkeypatch, fake)

        with pytest.raises(AthenaConfigurationError) as exc:
            run_athena_query("SELECT 1")

        assert GLUE_DATABASE_ENV in str(exc.value)

    def test_missing_data_bucket_name_raises_when_no_explicit_output(self, monkeypatch):
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        # DATA_BUCKET_NAME deliberately unset.

        scripted = ScriptedQuery(["SUCCEEDED"])
        fake = FakeAthenaClient(scripted, result_pages=[])
        _install_fake(monkeypatch, fake)

        with pytest.raises(AthenaConfigurationError) as exc:
            run_athena_query("SELECT 1")

        assert DATA_BUCKET_NAME_ENV in str(exc.value)

    def test_empty_sql_raises(self, monkeypatch):
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")

        with pytest.raises(AthenaConfigurationError):
            run_athena_query("")

    def test_whitespace_only_sql_raises(self, monkeypatch):
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")

        with pytest.raises(AthenaConfigurationError):
            run_athena_query("   \n\t")


# ---------------------------------------------------------------------------
# Polling cadence sanity check
# ---------------------------------------------------------------------------


class TestPollingCadence:
    """``time.sleep`` is called with the documented 1-second interval."""

    def test_sleep_uses_one_second_default(self, monkeypatch):
        """Each in-budget poll iteration sleeps for ATHENA_POLL_INTERVAL_SECONDS."""
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")

        scripted = ScriptedQuery(["RUNNING", "RUNNING", "SUCCEEDED"])
        fake = FakeAthenaClient(
            scripted, result_pages=[_make_page(columns=["a"], rows=[])]
        )
        _install_fake(monkeypatch, fake)
        durations = _install_zero_sleep(monkeypatch)

        run_athena_query("SELECT 1")

        # 2 RUNNING iterations → 2 sleeps; the SUCCEEDED iteration
        # exits before sleeping. Each sleep is bounded by the
        # 1-second cadence (may be capped by remaining budget when
        # fast-forwarded clocks are used in other tests, but here the
        # real clock is in play and the budget is fresh).
        assert all(
            0 < d <= ATHENA_POLL_INTERVAL_SECONDS for d in durations
        ), durations
        assert len(durations) == 2


# ---------------------------------------------------------------------------
# Hypothesis property tests
# ---------------------------------------------------------------------------


# Strategy that produces a column-keyed row spec (column names + row values).
#
# We use ``st.lists`` (instead of ``@st.composite`` with nested ``draw``)
# because flat list strategies have a much smaller per-input entropy
# footprint, which keeps Hypothesis happy when the test is run as part
# of the full network-agent suite (rather than in isolation). The
# trade-off is a slightly less natural-reading generator; the resulting
# property is identical.
_NULLABLE_CELL = st.one_of(
    st.none(),
    st.text(alphabet="abcdef0123456789-_", min_size=0, max_size=4),
)


def _result_set_strategy():
    """Generate a (column_names, list_of_row_value_tuples) pair.

    The generator picks 1-3 columns and 0-4 rows, keeping the search
    space small so the test stays inside Hypothesis's entropy budget
    even when run alongside the rest of the agent suite.
    """
    return st.tuples(
        st.integers(min_value=1, max_value=3),
        st.lists(
            st.lists(_NULLABLE_CELL, min_size=1, max_size=3),
            min_size=0,
            max_size=4,
        ),
    ).map(
        lambda pair: (
            [f"c{i}" for i in range(pair[0])],
            # Truncate or pad each row so its width matches the column count.
            [
                tuple(
                    (row[i] if i < len(row) else None)
                    for i in range(pair[0])
                )
                for row in pair[1]
            ],
        )
    )


class TestParseProperties:
    """Hypothesis-driven properties of the helper's row parser.

    Validates: Requirements 5.12, 5.22 (helper behavior contract)
    """

    @given(_result_set_strategy())
    @settings(
        max_examples=40,
        suppress_health_check=[
            HealthCheck.function_scoped_fixture,
            HealthCheck.too_slow,
        ],
    )
    def test_result_round_trip_preserves_values_and_count(
        self, monkeypatch, columns_and_rows
    ):
        """Validates: Requirement 5.12 (faithful, complete result reporting).

        Property: every row returned by ``GetQueryResults`` appears
        once in the helper's output, with values mapped exactly:
        cells with a ``VarCharValue`` survive verbatim; cells without
        one become ``None``.
        """
        columns, rows = columns_and_rows

        monkeypatch.setattr(athena_helper, "_athena_client", None, raising=False)
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")

        scripted = ScriptedQuery(["SUCCEEDED"])
        page = _make_page(columns=columns, rows=rows)
        fake = FakeAthenaClient(scripted, result_pages=[page])
        monkeypatch.setattr(athena_helper, "_athena_client", fake, raising=False)
        # Bypass real ``time.sleep`` for property-test speed.
        monkeypatch.setattr(athena_helper.time, "sleep", lambda _s: None)

        result = run_athena_query("SELECT 1")

        # Row count matches.
        assert len(result) == len(rows)

        # Each row's cells preserve order and values, with NULL → None.
        for actual, expected in zip(result, rows):
            assert list(actual.keys()) == list(columns)
            for col, value in zip(columns, expected):
                assert actual[col] == value

    @given(_result_set_strategy(), _result_set_strategy())
    @settings(
        max_examples=20,
        suppress_health_check=[
            HealthCheck.function_scoped_fixture,
            HealthCheck.too_slow,
        ],
    )
    def test_two_page_concatenation_preserves_row_order(
        self, monkeypatch, page_a, page_b
    ):
        """Validates: Requirement 5.12.

        Property: rows from page 2 follow rows from page 1 unchanged
        (no row dropped, no row reordered, header row dropped only on
        page 1). Both pages must declare the same columns.
        """
        columns, rows_a = page_a
        _columns_b, rows_b = page_b

        # Force same column schema on both pages by reusing page A's columns.
        rows_b_aligned = [
            tuple((value if i < len(columns) else None) for i, value in enumerate(row))
            for row in rows_b
        ]

        monkeypatch.setattr(athena_helper, "_athena_client", None, raising=False)
        monkeypatch.setenv(GLUE_DATABASE_ENV, "goat_network")
        monkeypatch.setenv(DATA_BUCKET_NAME_ENV, "goat-net-data-bucket")

        page_one = _make_page(columns, rows_a, include_header=True)
        page_two = _make_page(columns, rows_b_aligned, include_header=False)
        scripted = ScriptedQuery(["SUCCEEDED"])
        fake = FakeAthenaClient(scripted, result_pages=[page_one, page_two])
        monkeypatch.setattr(athena_helper, "_athena_client", fake, raising=False)
        monkeypatch.setattr(athena_helper.time, "sleep", lambda _s: None)

        result = run_athena_query("SELECT 1")

        # Page 1 rows come first (header dropped), page 2 rows follow.
        assert len(result) == len(rows_a) + len(rows_b_aligned)
        for i, expected_row in enumerate(rows_a):
            for col, value in zip(columns, expected_row):
                assert result[i][col] == value
        for j, expected_row in enumerate(rows_b_aligned):
            for col, value in zip(columns, expected_row):
                assert result[len(rows_a) + j][col] == value
