"""
ValidateAthenaLambda — fourth and final task in the G.O.A.T. Network
Agent Transformation_Workflow Step Functions state machine (Task 25,
Reqs 6.8, 6.9, 6.12).

Purpose
-------
Run a tiny existence-check query against the ``goat_network.pcap_logs``
Athena table to confirm the partition we just transformed is queryable
end-to-end. The query is the literal:

.. code-block:: sql

    SELECT 1 FROM pcap_logs WHERE capture_id = '<id>' LIMIT 1

Per Req 6.8, the workflow must "execute a validation Athena query
against the Pcap_Athena_Table". A row count of >= 1 confirms:

1. The Glue Crawler created/updated the ``capture_id=<id>`` partition.
2. Athena can plan a query against the partition.
3. The Parquet objects produced by ``ConvertPcapToParquetLambda`` are
   readable by Athena's Parquet engine.
4. At least one frame survived the transformation pipeline.

Workflow position::

    ListRawObjects ──► Map(ConvertPcapToParquet) ──► RunCrawler ──► [ValidateAthena]

Failure semantics (Req 6.9)
---------------------------
The handler raises (which Step Functions captures via ``Catch`` →
``Fail`` state) on:

- empty result set (the partition exists but is empty — equivalent to
  the entire pipeline silently dropping data),
- ``QueryExecution.Status.State`` of ``FAILED`` or ``CANCELLED``,
- polling timeout,
- unexpected client error.

The downstream ``Fail`` state emits ``failed_task = "ValidateAthena"``
and ``error_reason`` so the Network Agent's ``transform_capture``
handler can surface a useful diagnostic to the user.

Input contract
--------------
.. code-block:: json

    { "capture_id": "<id>" }

Output contract
---------------
On success::

    {
        "capture_id":         "<id>",
        "validation_query_id": "<athena execution id>",
        "rows_returned":       1
    }

Environment variables
---------------------
``GLUE_DATABASE``
    Glue database name (``goat_network``). Sourced from the
    ``GOATNetworkAgentGlueDatabaseName`` CFN export by the InfraStack.
``DATA_BUCKET_NAME``
    Bucket used as the Athena query result location (we write to
    ``s3://{bucket}/athena-results/`` to keep validation artifacts
    outside the ``raw/`` and ``parquet/`` reserved prefixes).
``ATHENA_POLL_INTERVAL_SECONDS``
    (Optional) Polling cadence. Defaults to 1 second.
``ATHENA_TIMEOUT_SECONDS``
    (Optional) Polling timeout. Defaults to 60 seconds — the validation
    query touches a single partition and a single row, so it should
    complete in single-digit seconds even with cold-start Glue Catalog
    cache misses.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict

import boto3
from botocore.exceptions import ClientError


_ATHENA = None


def _get_athena_client():
    """Lazy-init Athena client (one per Lambda container)."""
    global _ATHENA
    if _ATHENA is None:
        _ATHENA = boto3.client("athena")
    return _ATHENA


def _validate_capture_id(value: Any) -> str:
    """Capture_Id_Format check, identical to the agent's ``validation.py``.

    Critical: the value is interpolated directly into the validation
    SQL via single-quote escaping. The Capture_Id_Format restricts the
    character set to ``[A-Za-z0-9_-]`` which excludes single quotes,
    backslashes, and every other SQL escape vector. Even so, we
    explicitly escape in the SQL builder below to keep this Lambda
    safe under any future relaxation of the format.
    """
    if not isinstance(value, str):
        raise ValueError(
            f"capture_id must be a string, got {type(value).__name__}"
        )
    if not (1 <= len(value) <= 128):
        raise ValueError(
            f"capture_id length {len(value)} outside allowed range 1..128"
        )
    for ch in value:
        if not (ch.isalnum() or ch in ("_", "-")):
            raise ValueError(
                f"capture_id contains disallowed character {ch!r}"
            )
    return value


def _build_validation_sql(capture_id: str) -> str:
    """Build the literal validation query mandated by Task 25.

    The Capture_Id_Format constraint forbids quote characters so a
    direct interpolation is safe. We additionally escape any single
    quote (defensive) so that this builder remains correct even if the
    upstream validator were relaxed.
    """
    safe_id = capture_id.replace("'", "''")
    return f"SELECT 1 FROM pcap_logs WHERE capture_id = '{safe_id}' LIMIT 1"


def _wait_for_query(
    execution_id: str,
    poll_interval_s: float,
    timeout_s: float,
) -> Dict[str, Any]:
    """Poll ``athena:GetQueryExecution`` until the query finishes.

    Returns:
        The full ``QueryExecution`` object once the state is
        ``SUCCEEDED``. Raises on any other terminal state.
    """
    athena = _get_athena_client()
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        try:
            response = athena.get_query_execution(
                QueryExecutionId=execution_id
            )
        except ClientError as exc:
            raise RuntimeError(
                f"ValidateAthena: athena:GetQueryExecution failed "
                f"for execution_id={execution_id!r}: {exc}"
            ) from exc

        execution = response.get("QueryExecution", {})
        state = (execution.get("Status") or {}).get("State")

        if state == "SUCCEEDED":
            return execution
        if state in ("FAILED", "CANCELLED"):
            reason = (execution.get("Status") or {}).get(
                "StateChangeReason", "<no reason reported>"
            )
            raise RuntimeError(
                f"ValidateAthena: validation query terminated with "
                f"state={state} reason={reason!r}"
            )

        time.sleep(poll_interval_s)  # nosemgrep: arbitrary-sleep

    raise TimeoutError(
        f"ValidateAthena: validation query did not finish within "
        f"{timeout_s:.0f} seconds (execution_id={execution_id!r})"
    )


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """Run the validation Athena query and assert at least one row.

    Args:
        event: Step Functions task input. Must contain ``capture_id``.
        _context: Lambda context (unused).

    Returns:
        Dict with ``capture_id``, ``validation_query_id``, and
        ``rows_returned``.

    Raises:
        Various: Any exception causes Step Functions ``Catch`` to
            transition the workflow to the ``Fail`` state.
    """
    capture_id = _validate_capture_id(event.get("capture_id"))

    glue_database = os.environ.get("GLUE_DATABASE")
    bucket = os.environ.get("DATA_BUCKET_NAME")
    if not glue_database:
        raise RuntimeError(
            "ValidateAthena: GLUE_DATABASE environment variable is unset"
        )
    if not bucket:
        raise RuntimeError(
            "ValidateAthena: DATA_BUCKET_NAME environment variable is unset"
        )

    poll_interval_s = float(
        os.environ.get("ATHENA_POLL_INTERVAL_SECONDS", "1")
    )
    timeout_s = float(os.environ.get("ATHENA_TIMEOUT_SECONDS", "60"))

    sql = _build_validation_sql(capture_id)
    athena = _get_athena_client()

    # Athena query results are written to the bucket under a reserved
    # ``athena-results/`` prefix so they do not collide with ``raw/``
    # or ``parquet/`` (Req 7.5). The lifecycle policy on this bucket
    # already deletes ``raw/`` and ``parquet/`` after their respective
    # windows; ``athena-results/`` carries no special policy because
    # validation queries are tiny.
    output_location = f"s3://{bucket}/athena-results/"

    try:
        start_response = athena.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": glue_database},
            ResultConfiguration={"OutputLocation": output_location},
        )
    except ClientError as exc:
        raise RuntimeError(
            f"ValidateAthena: athena:StartQueryExecution failed: {exc}"
        ) from exc

    execution_id = start_response.get("QueryExecutionId")
    if not execution_id:
        raise RuntimeError(
            "ValidateAthena: athena:StartQueryExecution returned no "
            "QueryExecutionId"
        )

    _wait_for_query(execution_id, poll_interval_s, timeout_s)

    # Fetch the result rows. ``GetQueryResults`` returns the header
    # row as the first ResultSet row, so a populated partition with a
    # single matching row produces 2 rows in the response. Anything
    # less than 2 means the partition is empty / unqueryable, which
    # is a validation failure per the Task 25 contract.
    try:
        results = athena.get_query_results(QueryExecutionId=execution_id)
    except ClientError as exc:
        raise RuntimeError(
            f"ValidateAthena: athena:GetQueryResults failed for "
            f"execution_id={execution_id!r}: {exc}"
        ) from exc

    rows = (results.get("ResultSet") or {}).get("Rows") or []
    # Athena's GetQueryResults always returns the header row as the
    # first element. ``LIMIT 1`` therefore produces 2 rows on success
    # (header + 1 data row) or 1 row on empty (header only).
    data_row_count = max(0, len(rows) - 1)

    if data_row_count < 1:
        raise RuntimeError(
            f"ValidateAthena: validation query returned zero rows for "
            f"capture_id={capture_id!r}; the partition exists but contains "
            "no data, indicating an upstream transformation failure"
        )

    return {
        "capture_id": capture_id,
        "validation_query_id": execution_id,
        "rows_returned": data_row_count,
    }
