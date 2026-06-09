"""
Athena query execution helper for the G.O.A.T. Network Agent.

Implements Task 12 of the goat-network-agent spec: a single
``run_athena_query`` entry point shared by every Pcap_Query_Action
handler in :mod:`main` (Tasks 13-18).

Contract (verbatim from ``tasks.md`` Task 12 and Reqs 5.12, 5.22):

* ``run_athena_query(sql, work_group=None, output_location=None)``
  calls ``athena:StartQueryExecution``, polls
  ``athena:GetQueryExecution`` at 1-second intervals, fails fast on
  ``FAILED`` or ``CANCELLED``, retrieves results via
  ``athena:GetQueryResults`` with pagination, and returns rows as a
  list of dicts.
* The helper enforces a 60-second wall-clock budget covering the
  entire start-poll-results pipeline. On budget exhaustion the helper
  raises :class:`AthenaQueryTimeoutError` (a typed timeout exception)
  so handlers can convert the failure to ``success=false`` with
  ``metadata.errorCategory="athena_timeout"`` per design Error
  Handling section EH-2.
* Database name is read from the ``GLUE_DATABASE`` environment
  variable. The S3 output location is derived from the
  ``DATA_BUCKET_NAME`` environment variable as
  ``s3://<DATA_BUCKET_NAME>/athena-results/``; callers may override
  this by passing ``output_location`` explicitly.

Why a separate module instead of a section in ``main.py``:

* Task 12 description allows either; we prefer a separate module
  because Tasks 13-18 will add 14 pcap-query handlers in ``main.py``
  and the resulting file would exceed comfortable size.
* The helper has no agent-specific state and is straightforward to
  unit-test in isolation against ``moto`` or hand-rolled fakes.

Returned row shape:

The boto3 ``athena:GetQueryResults`` response is a list of
``Rows[].Data[].VarCharValue`` cells where every cell is already a
string (Athena returns the SQL-type-formatted value). The helper
preserves that shape: each row is a ``dict`` keyed by the result-set
column name, with string values taken directly from ``VarCharValue``.
``NULL`` cells (where ``VarCharValue`` is absent) are mapped to
``None`` so downstream handlers can distinguish them from empty
strings. Downstream handlers cast values to numeric / boolean types
as their per-action schemas require — see Tasks 13-18 for details.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from aws_utils import get_region

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level configuration
# ---------------------------------------------------------------------------

# Environment variable names exposed by ``NetworkRuntimeStack`` (CDK
# Task 28). Read at call time (not import time) so test fixtures can
# ``monkeypatch`` ``os.environ`` between cases.
GLUE_DATABASE_ENV = "GLUE_DATABASE"
DATA_BUCKET_NAME_ENV = "DATA_BUCKET_NAME"

# Polling cadence and overall wall-clock budget. The polling cadence
# is mandated verbatim by Task 12 ("polls ``athena:GetQueryExecution``
# at 1s intervals"); the budget is mandated by Reqs 5.1, 5.4, 5.6,
# 5.8, 5.9, 5.10, and 5.22 ("respond within 60 seconds").
ATHENA_POLL_INTERVAL_SECONDS = 1.0
ATHENA_QUERY_BUDGET_SECONDS = 60.0

# Athena query states (from boto3 docs). The terminal states are
# documented as ``SUCCEEDED``, ``FAILED``, and ``CANCELLED``; any other
# state means the query is still running and the helper continues
# polling.
_ATHENA_STATE_SUCCEEDED = "SUCCEEDED"
_ATHENA_STATE_FAILED = "FAILED"
_ATHENA_STATE_CANCELLED = "CANCELLED"
_TERMINAL_STATES = frozenset(
    {_ATHENA_STATE_SUCCEEDED, _ATHENA_STATE_FAILED, _ATHENA_STATE_CANCELLED}
)

# Default S3 prefix under the Network_Data_Bucket where Athena writes
# query result files. The trailing slash is required by Athena.
_DEFAULT_OUTPUT_LOCATION_PREFIX = "athena-results/"


# Lazy boto3 client singleton — same pattern as the EC2/scheduler/SFN
# clients in :mod:`main`. Tests reset by setting
# ``athena_helper._athena_client = None``.
_athena_client = None


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class AthenaConfigurationError(Exception):
    """Raised when a required environment variable is unset or empty.

    The Network Agent runtime container must receive ``GLUE_DATABASE``
    and ``DATA_BUCKET_NAME`` from the ``NetworkRuntimeStack`` (CDK
    Task 28). Calling ``run_athena_query`` before those values are
    available is a deployment misconfiguration; we surface it as a
    distinct exception so handlers can label the response envelope
    with ``metadata.errorCategory="configuration_missing"``.
    """


class AthenaQueryError(Exception):
    """Base class for ``run_athena_query`` runtime failures.

    Two concrete subclasses are emitted by the helper:
    :class:`AthenaQueryFailedError` when Athena reports the query
    transitioned to ``FAILED`` or ``CANCELLED``, and
    :class:`AthenaQueryTimeoutError` when the helper's 60-second
    wall-clock budget elapses before Athena reaches a terminal state.

    Both carry the offending ``query_execution_id`` (when known) so
    operators can correlate with Athena's CloudWatch / Athena history
    page. Catch :class:`AthenaQueryError` in handlers to convert all
    helper failures to ``success=false`` envelopes uniformly.
    """

    def __init__(
        self,
        message: str,
        *,
        query_execution_id: Optional[str] = None,
        athena_state: Optional[str] = None,
        state_change_reason: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.query_execution_id = query_execution_id
        self.athena_state = athena_state
        self.state_change_reason = state_change_reason


class AthenaQueryFailedError(AthenaQueryError):
    """Athena reported the query in ``FAILED`` or ``CANCELLED`` state (Req 5.12).

    The helper raises this exception immediately on observing a
    terminal non-``SUCCEEDED`` state, without attempting to fetch
    partial results. This satisfies Req 5.12 ("SHALL NOT return
    partial results") for every Pcap_Query_Action that calls
    ``run_athena_query``.
    """


class AthenaQueryTimeoutError(AthenaQueryError):
    """The helper's 60-second wall-clock budget elapsed (Req 5.22).

    The helper attempts a best-effort ``athena:StopQueryExecution``
    before raising so the in-flight query does not continue burning
    Athena scan-bytes. On budget exhaustion the helper sets
    ``athena_state`` to whatever non-terminal state Athena last
    reported (typically ``RUNNING``).
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_athena_client():
    """Return a cached boto3 Athena client bound to the agent's region.

    Lazy construction matches the singleton pattern used elsewhere in
    the agent (see ``_get_ec2_client`` in :mod:`main`). Tests reset
    the cache by setting ``athena_helper._athena_client = None``.
    """
    global _athena_client
    if _athena_client is None:
        _athena_client = boto3.client("athena", region_name=get_region())
    return _athena_client


def _read_required_env(name: str) -> str:
    """Read a required environment variable, raising :class:`AthenaConfigurationError` if unset."""
    value = os.environ.get(name)
    if not value:
        raise AthenaConfigurationError(
            f"Required environment variable {name!r} is not set. "
            "The Network Agent runtime container must receive this "
            "value from the NetworkRuntimeStack (CDK Task 28)."
        )
    return value


def _resolve_output_location(explicit: Optional[str]) -> str:
    """Resolve the Athena ``OutputLocation`` from caller arg or env.

    Precedence:
      1. ``explicit`` argument when supplied (caller override).
      2. ``s3://<DATA_BUCKET_NAME>/athena-results/`` derived from the
         environment variable.

    Args:
        explicit: Caller-supplied ``output_location`` from
            :func:`run_athena_query`. ``None`` or empty string falls
            back to the env-derived default.

    Returns:
        A non-empty ``s3://...`` URI. Athena requires the URI to end
        with ``/`` (it represents a prefix, not a single key); the
        env-derived default already includes the trailing slash, and
        any ``explicit`` URI without one is left as-is so the caller
        sees the same behaviour they would get talking to Athena
        directly.

    Raises:
        AthenaConfigurationError: If neither ``explicit`` nor the
            ``DATA_BUCKET_NAME`` env var are set.
    """
    if explicit:
        return explicit

    bucket_name = _read_required_env(DATA_BUCKET_NAME_ENV)
    return f"s3://{bucket_name}/{_DEFAULT_OUTPUT_LOCATION_PREFIX}"


def _parse_results_pages(athena_client, query_execution_id: str) -> List[Dict[str, Optional[str]]]:
    """Fetch every page of query results and merge into a list of dicts.

    Athena ``GetQueryResults`` paginates with ``NextToken``; the helper
    walks every page so the returned list is exhaustive regardless of
    result-set size. The first row of the **first** page contains the
    column header (Athena documents this explicitly); we drop it on
    the first page only and never on subsequent pages.

    Args:
        athena_client: A boto3 Athena client (already bound to the
            agent's region).
        query_execution_id: The id returned by ``StartQueryExecution``.

    Returns:
        List of row dicts in the order Athena returned them. Each
        dict is keyed by column name with string values taken from
        ``VarCharValue``; ``NULL`` cells (no ``VarCharValue``) are
        mapped to ``None``.

    Raises:
        botocore.exceptions.ClientError: Propagated from
            ``GetQueryResults``. Callers convert this into the
            response envelope via the existing
            ``_classify_aws_error`` pipeline in :mod:`main`.
    """
    paginator = athena_client.get_paginator("get_query_results")
    rows: List[Dict[str, Optional[str]]] = []
    columns: Optional[List[str]] = None
    is_first_page = True

    for page in paginator.paginate(QueryExecutionId=query_execution_id):
        result_set = page.get("ResultSet", {})

        # The column metadata is identical on every page; cache it on
        # the first page so we don't have to re-extract it repeatedly.
        if columns is None:
            column_info = result_set.get("ResultSetMetadata", {}).get(
                "ColumnInfo", []
            )
            columns = [col.get("Name", f"col_{i}") for i, col in enumerate(column_info)]

        raw_rows = result_set.get("Rows", [])

        # Athena's first page has a synthetic header row. The header
        # values match the column names but the row shape is
        # otherwise indistinguishable from a data row, so we have to
        # drop it positionally.
        page_data_rows = raw_rows[1:] if is_first_page else raw_rows
        is_first_page = False

        for raw_row in page_data_rows:
            data = raw_row.get("Data", [])
            row: Dict[str, Optional[str]] = {}
            for index, cell in enumerate(data):
                # Cells representing SQL ``NULL`` arrive without
                # ``VarCharValue`` at all (boto3 docs:
                # "Indicates that a value is null."). Map those to
                # Python ``None`` so callers can distinguish an empty
                # string from a NULL.
                value = cell.get("VarCharValue") if "VarCharValue" in cell else None
                if columns is not None and index < len(columns):
                    column_name = columns[index]
                else:
                    column_name = f"col_{index}"
                row[column_name] = value
            rows.append(row)

    return rows


def _stop_query_best_effort(athena_client, query_execution_id: str) -> None:
    """Cancel a still-running query, swallowing any cancellation error.

    Called from the timeout path to keep Athena scan-bytes bounded.
    Failures here are logged and swallowed because the caller is
    already raising :class:`AthenaQueryTimeoutError` and the original
    error-category label takes precedence.
    """
    try:
        athena_client.stop_query_execution(QueryExecutionId=query_execution_id)
    except (ClientError, Exception) as exc:  # noqa: BLE001 - intentional swallow
        logger.warning(
            "Failed to cancel Athena query %s after wall-clock timeout: %s",
            query_execution_id,
            exc,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_athena_query(
    sql: str,
    work_group: Optional[str] = None,
    output_location: Optional[str] = None,
) -> List[Dict[str, Optional[str]]]:
    """Execute an Athena query and return its rows as a list of dicts.

    This is the single Athena entry point shared by every
    Pcap_Query_Action handler in :mod:`main` (Tasks 13-18). The
    helper performs the full start-poll-results pipeline:

    1. Calls ``athena:StartQueryExecution`` against the database
       named by ``GLUE_DATABASE`` and the supplied ``work_group`` (or
       the workgroup default when ``work_group`` is ``None``).
    2. Polls ``athena:GetQueryExecution`` at 1-second intervals until
       the query reaches a terminal state.
    3. On terminal ``FAILED`` or ``CANCELLED`` (Req 5.12), raises
       :class:`AthenaQueryFailedError` with the Athena
       ``StateChangeReason`` so the caller can include it in the
       response envelope's ``error`` field. **Partial results are
       never returned** — the failure path bypasses
       ``GetQueryResults`` entirely.
    4. On terminal ``SUCCEEDED``, calls ``athena:GetQueryResults``
       (paginated) and returns rows as a list of column-keyed dicts.

    The entire pipeline is bounded by a 60-second wall-clock budget
    (Req 5.22). On budget exhaustion the helper attempts a best-effort
    ``athena:StopQueryExecution`` and raises
    :class:`AthenaQueryTimeoutError`. The 60-second budget is
    *inclusive* of every ``StartQueryExecution`` /
    ``GetQueryExecution`` / ``GetQueryResults`` call so handlers can
    return their response envelope inside the matching response-time
    SLA (Reqs 5.1, 5.4, 5.6, 5.8, 5.9, 5.10, 5.22).

    Args:
        sql: The SQL query string. The helper does **not** rewrite or
            validate the SQL — that is the responsibility of the
            calling handler (Tasks 13-18 inject the
            Capture_Id_Predicate before calling this helper).
        work_group: Optional Athena workgroup name. When ``None``,
            ``StartQueryExecution`` is called without ``WorkGroup``
            and Athena uses the caller's default workgroup. A
            ``WorkGroup`` value is supplied verbatim.
        output_location: Optional ``s3://...`` URI for query results.
            When ``None``, the helper uses
            ``s3://<DATA_BUCKET_NAME>/athena-results/`` derived from
            the env variable. Always passed in the
            ``ResultConfiguration.OutputLocation`` field so the
            workgroup default never silently overrides our intent.

    Returns:
        List of row dicts. Each dict is keyed by the result-set
        column name; values are strings as returned by Athena under
        ``VarCharValue``, with SQL ``NULL`` mapped to Python
        ``None``. Returns an empty list when the query succeeds but
        the result set is empty (this is the documented Req 5.23
        path; handlers convert it to ``success=true`` with empty
        ``data``).

    Raises:
        AthenaConfigurationError: ``GLUE_DATABASE`` is unset, or
            ``output_location`` is ``None`` and ``DATA_BUCKET_NAME``
            is unset. Handlers should surface this as
            ``metadata.errorCategory="configuration_missing"``.
        AthenaQueryFailedError: Athena reports ``FAILED`` or
            ``CANCELLED`` (Req 5.12). The exception's
            ``state_change_reason`` carries the Athena failure
            reason verbatim for inclusion in the response envelope.
        AthenaQueryTimeoutError: The 60-second wall-clock budget
            elapsed before the query reached a terminal state
            (Req 5.22). Handlers convert this to ``success=false``
            with ``metadata.errorCategory="athena_timeout"``.
        botocore.exceptions.ClientError: Propagated from any boto3
            call (e.g. ``AccessDeniedException`` on
            ``StartQueryExecution``). Handlers convert this to
            ``success=false`` via the existing
            ``_classify_aws_error`` pipeline in :mod:`main`.
    """
    if not isinstance(sql, str) or not sql.strip():
        # Defensive guard — every existing caller validates SQL
        # upstream, but a misuse here would otherwise fail inside
        # boto3 with a less actionable error.
        raise AthenaConfigurationError(
            "run_athena_query requires a non-empty SQL string"
        )

    database = _read_required_env(GLUE_DATABASE_ENV)
    resolved_output_location = _resolve_output_location(output_location)

    athena_client = _get_athena_client()

    start_params = {
        "QueryString": sql,
        "QueryExecutionContext": {"Database": database},
        "ResultConfiguration": {"OutputLocation": resolved_output_location},
    }
    if work_group:
        # ``WorkGroup`` is omitted (rather than passed empty) when no
        # value is supplied so Athena falls back to the caller's
        # default workgroup.
        start_params["WorkGroup"] = work_group

    deadline = time.monotonic() + ATHENA_QUERY_BUDGET_SECONDS

    response = athena_client.start_query_execution(**start_params)
    query_execution_id = response["QueryExecutionId"]
    logger.info(
        "Started Athena query %s against database %s",
        query_execution_id,
        database,
    )

    last_state = None
    last_state_change_reason: Optional[str] = None

    while True:
        # Check the wall-clock budget *before* every poll so we never
        # issue an additional GetQueryExecution call past the deadline.
        # The first iteration's budget check follows the
        # StartQueryExecution call; if Athena was very slow to start,
        # we would already know the budget was exhausted here.
        if time.monotonic() >= deadline:
            _stop_query_best_effort(athena_client, query_execution_id)
            raise AthenaQueryTimeoutError(
                f"Athena query {query_execution_id} did not reach a terminal "
                f"state within {ATHENA_QUERY_BUDGET_SECONDS:.0f} seconds. "
                f"Last observed state: {last_state!r}.",
                query_execution_id=query_execution_id,
                athena_state=last_state,
            )

        get_response = athena_client.get_query_execution(
            QueryExecutionId=query_execution_id,
        )
        status = get_response.get("QueryExecution", {}).get("Status", {})
        last_state = status.get("State")
        last_state_change_reason = status.get("StateChangeReason")

        if last_state in _TERMINAL_STATES:
            break

        # Sleep until the next poll, but never past the deadline so we
        # exit the loop promptly when the budget is about to expire.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # Re-check the deadline at the top of the next iteration so
            # the timeout path emits ``last_state`` rather than the
            # value from the last terminal-state check.
            continue
        time.sleep(min(ATHENA_POLL_INTERVAL_SECONDS, remaining))

    if last_state == _ATHENA_STATE_SUCCEEDED:
        logger.info(
            "Athena query %s SUCCEEDED; fetching results",
            query_execution_id,
        )
        return _parse_results_pages(athena_client, query_execution_id)

    # Terminal non-success: FAILED or CANCELLED. Per Req 5.12, do not
    # attempt to fetch partial results; surface the failure reason
    # verbatim.
    reason = last_state_change_reason or "Unknown error"
    raise AthenaQueryFailedError(
        f"Athena query {query_execution_id} ended in state "
        f"{last_state} ({reason}).",
        query_execution_id=query_execution_id,
        athena_state=last_state,
        state_change_reason=reason,
    )


__all__ = [
    "GLUE_DATABASE_ENV",
    "DATA_BUCKET_NAME_ENV",
    "ATHENA_POLL_INTERVAL_SECONDS",
    "ATHENA_QUERY_BUDGET_SECONDS",
    "AthenaConfigurationError",
    "AthenaQueryError",
    "AthenaQueryFailedError",
    "AthenaQueryTimeoutError",
    "run_athena_query",
]
