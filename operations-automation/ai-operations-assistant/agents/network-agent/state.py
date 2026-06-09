"""
DynamoDB capture-state and VNI-lookup helpers for the G.O.A.T. Network Agent.

Implements Task 5 of the goat-network-agent spec: the eight ``boto3``
DynamoDB-resource helper functions used by the capture-lifecycle handlers
in ``main.py``.

Tables (provisioned by ``NetworkInfraStack`` in CDK Task 23, names exposed
to the runtime container via the environment variables ``CAPTURE_STATE_TABLE``
and ``VNI_LOOKUP_TABLE``):

- **Capture_State_Table** (Req 6.11)
  - PK: ``capture_id`` (string)
  - GSI ``status-index`` on ``status`` (string)
  - Schema (design "Data Models" section): ``capture_id``, ``eni_ids``
    (list of strings), ``start_time`` (ISO 8601 UTC), ``deadline``
    (ISO 8601 UTC), ``duration_minutes`` (number), ``status``
    (``active`` | ``stopped`` | ``transformed`` | ``queryable`` |
    ``stopping_failed``), ``stopped_reason``, ``mirror_session_ids``
    (list of strings), optional ``idempotency_token``, ``requested_by``,
    optional ``transform_execution_arn``, ``created_at``,
    ``auto_stop_schedule_armed`` (boolean).

- **Vni_Lookup_Table** (Req 6.11)
  - PK: ``vni`` (number, VXLAN VNI 1..16777215)
  - GSI ``capture-id-index`` on ``capture_id`` (string)
  - Schema: ``vni``, ``capture_id``, ``mirror_session_id``, ``eni_id``,
    ``expires_at`` (Unix epoch seconds, DynamoDB TTL attribute).

The helpers exposed by this module:

- :func:`put_capture` — write one Capture_State_Table row (Req 3.1).
- :func:`get_capture` — read one row by ``capture_id`` (Reqs 3.7, 3.13).
- :func:`update_capture_status` — conditional status transition with
  optional ``stopped_reason`` (Reqs 3.7, 4.10).
- :func:`query_active_captures` — query the ``status-index`` GSI for
  ``active`` rows (Req 4.5 concurrency check).
- :func:`query_captures` — list rows for a status filter, sorted by
  ``start_time`` descending (Req 3.9).
- :func:`find_idempotent_capture` — return an existing row when an
  idempotency-token reuse is observed within the last 5 minutes
  (Req 3.15).
- :func:`put_vni_lookup_rows` — batch-write Vni_Lookup_Table rows
  (Req 6.11).
- :func:`delete_vni_lookup_for_capture` — delete every row in the
  Vni_Lookup_Table that references the supplied ``capture_id``, via the
  ``capture-id-index`` GSI (Reqs 3.7, 6.11).

Design notes:

* The ``boto3`` DynamoDB **resource** client is used everywhere (not the
  low-level client) so item dicts can be supplied verbatim and
  pagination is handled by ``Table.query``'s ``LastEvaluatedKey``.
* Resource clients are created lazily and cached as module-level
  singletons mirroring the ``_get_ec2_client`` pattern in ``main.py``.
  Tests can reset the cache by setting the module-level
  ``_dynamodb_resource``, ``_capture_state_table``, and
  ``_vni_lookup_table`` attributes to ``None``.
* Table names are read from the environment **at first use** (not at
  import time) so test fixtures can ``monkeypatch`` ``os.environ`` and
  the cache before invoking a helper.
* Every helper raises :class:`StateError` on a misconfiguration error
  (missing environment variable). AWS errors propagate from ``boto3``
  unchanged so the calling handler can run them through the existing
  ``_classify_aws_error`` pipeline in ``main.py``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, List, Optional

import boto3
from boto3.dynamodb.conditions import Attr, Key

from aws_utils import get_region

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level configuration
# ---------------------------------------------------------------------------


# Environment variable names exposed by the AgentCore runtime (CDK Task 28).
CAPTURE_STATE_TABLE_ENV = "CAPTURE_STATE_TABLE"
VNI_LOOKUP_TABLE_ENV = "VNI_LOOKUP_TABLE"

# GSI names provisioned in CDK Task 23.
STATUS_INDEX_NAME = "status-index"
CAPTURE_ID_INDEX_NAME = "capture-id-index"

# Idempotency window for Req 3.15. Re-using the same idempotency token
# within this window with matching ``eni_ids`` and ``duration_minutes``
# returns the existing capture row instead of creating a new one.
IDEMPOTENCY_WINDOW = timedelta(minutes=5)


# Lazy boto3 DynamoDB resource singleton. ``boto3.resource("dynamodb")``
# is heavyweight — the resource layer caches per-table metadata in
# memory — so callers should reuse the cached instance.
_dynamodb_resource = None

# Cached Table handles. The DynamoDB resource layer's ``.Table()`` call
# is essentially free, but we keep a cache so ``CAPTURE_STATE_TABLE``
# environment-variable reads happen exactly once per container.
_capture_state_table = None
_vni_lookup_table = None


class StateError(Exception):
    """Raised for misconfiguration errors in :mod:`state`.

    Currently emitted only when the table-name environment variable is
    missing or empty. AWS errors propagate from ``boto3`` unchanged so
    the calling handler can classify them via ``_classify_aws_error``
    in ``main.py``.
    """


# ---------------------------------------------------------------------------
# Lazy resource / table accessors
# ---------------------------------------------------------------------------


def _get_dynamodb_resource():
    """Return a cached boto3 DynamoDB resource bound to the agent's region."""
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb", region_name=get_region())
    return _dynamodb_resource


def _read_required_env(name: str) -> str:
    """Read a required environment variable, raising :class:`StateError` if unset."""
    value = os.environ.get(name)
    if not value:
        raise StateError(
            f"Required environment variable {name!r} is not set. "
            "The Network Agent runtime container must receive this "
            "value from the NetworkRuntimeStack (CDK Task 28)."
        )
    return value


def _capture_table():
    """Return the cached Capture_State_Table ``Table`` resource."""
    global _capture_state_table
    if _capture_state_table is None:
        name = _read_required_env(CAPTURE_STATE_TABLE_ENV)
        _capture_state_table = _get_dynamodb_resource().Table(name)
    return _capture_state_table


def _vni_table():
    """Return the cached Vni_Lookup_Table ``Table`` resource."""
    global _vni_lookup_table
    if _vni_lookup_table is None:
        name = _read_required_env(VNI_LOOKUP_TABLE_ENV)
        _vni_lookup_table = _get_dynamodb_resource().Table(name)
    return _vni_lookup_table


def _reset_cache_for_tests() -> None:
    """Clear the cached resource and table handles (test helper).

    Tests that ``monkeypatch`` the table-name environment variable or
    the DynamoDB resource singleton should call this between cases so
    the next helper call re-reads the environment.
    """
    global _dynamodb_resource, _capture_state_table, _vni_lookup_table
    _dynamodb_resource = None
    _capture_state_table = None
    _vni_lookup_table = None


# ---------------------------------------------------------------------------
# Capture_State_Table helpers
# ---------------------------------------------------------------------------


def put_capture(item: dict) -> dict:
    """Write one Capture_State_Table row.

    The caller is responsible for supplying every required attribute
    (``capture_id``, ``eni_ids``, ``start_time``, ``deadline``,
    ``duration_minutes``, ``status``, ``mirror_session_ids``,
    ``created_at``, ``requested_by``) and any optional attributes
    (``idempotency_token``, ``stopped_reason``,
    ``transform_execution_arn``, ``auto_stop_schedule_armed``) per the
    schema documented in design "Data Models — Capture_State_Table".

    Args:
        item: Mapping containing the row attributes. Must include
            ``capture_id`` so the put succeeds against the table's
            primary key.

    Returns:
        The boto3 ``put_item`` response (echoed for caller-side
        logging / metrics).

    Raises:
        StateError: If the ``CAPTURE_STATE_TABLE`` environment variable
            is unset or empty.
        botocore.exceptions.ClientError: Propagated from DynamoDB
            (handled by the caller's ``_classify_aws_error`` path).
    """
    if not isinstance(item, dict):
        raise StateError(
            f"put_capture requires a dict item, got {type(item).__name__}"
        )
    if "capture_id" not in item:
        raise StateError("put_capture item is missing the required 'capture_id' key")

    table = _capture_table()
    return table.put_item(Item=item)


def get_capture(capture_id: str) -> Optional[dict]:
    """Read one Capture_State_Table row by primary key.

    Args:
        capture_id: The capture identifier. Callers are expected to
            have already validated this against ``Capture_Id_Format``
            via :func:`validation.validate_capture_id`.

    Returns:
        The row as a dict if present, ``None`` if absent. The caller
        decides whether absence is an error (e.g. ``stop_capture``
        treats absence as ``not_found``).

    Raises:
        StateError: If the ``CAPTURE_STATE_TABLE`` environment variable
            is unset or empty, or ``capture_id`` is not a non-empty
            string.
        botocore.exceptions.ClientError: Propagated from DynamoDB.
    """
    if not isinstance(capture_id, str) or not capture_id:
        raise StateError(
            "get_capture requires a non-empty string capture_id"
        )

    table = _capture_table()
    response = table.get_item(Key={"capture_id": capture_id})
    return response.get("Item")


def update_capture_status(
    capture_id: str,
    status: str,
    stopped_reason: Optional[str] = None,
) -> dict:
    """Conditionally update a Capture_Session's ``status`` (Req 3.7).

    The update is conditional on the row already existing
    (``attribute_exists(capture_id)``); if no row exists, DynamoDB
    raises ``ConditionalCheckFailedException`` and the caller should
    surface a ``not_found`` envelope. The condition does **not** check
    the prior status value because ``stop_capture`` is documented as
    idempotent (Req 3.7) and ``start_capture``'s rollback path may
    walk through several intermediate statuses.

    When ``stopped_reason`` is supplied, the row's ``stopped_reason``
    attribute is set in the same update; this is used by Req 4.10
    (``auto_stop_deadline``) and the partial-cleanup path described
    in design's stop_capture commentary
    (``stopped_reason=partial_cleanup_<step>``).

    Args:
        capture_id: The capture identifier (validated upstream).
        status: The new status value. Documented values are
            ``active``, ``stopped``, ``transformed``, ``queryable``,
            ``stopping_failed``. The helper accepts any non-empty
            string so future statuses can be added without modifying
            this module.
        stopped_reason: Optional string written to ``stopped_reason``
            in the same update.

    Returns:
        The boto3 ``update_item`` response with ``ReturnValues="ALL_NEW"``,
        i.e. the updated row under ``response["Attributes"]``.

    Raises:
        StateError: If inputs are missing/empty, or
            ``CAPTURE_STATE_TABLE`` is unset.
        botocore.exceptions.ClientError: Propagated from DynamoDB. In
            particular, ``ConditionalCheckFailedException`` indicates
            no row exists for ``capture_id``.
    """
    if not isinstance(capture_id, str) or not capture_id:
        raise StateError(
            "update_capture_status requires a non-empty string capture_id"
        )
    if not isinstance(status, str) or not status:
        raise StateError(
            "update_capture_status requires a non-empty string status"
        )

    update_expression_parts = ["#s = :s"]
    expression_names: dict = {"#s": "status"}
    expression_values: dict = {":s": status}

    if stopped_reason is not None:
        if not isinstance(stopped_reason, str) or not stopped_reason:
            raise StateError(
                "update_capture_status stopped_reason must be a non-empty "
                "string when supplied"
            )
        update_expression_parts.append("#sr = :sr")
        expression_names["#sr"] = "stopped_reason"
        expression_values[":sr"] = stopped_reason

    table = _capture_table()
    return table.update_item(
        Key={"capture_id": capture_id},
        UpdateExpression="SET " + ", ".join(update_expression_parts),
        ConditionExpression="attribute_exists(capture_id)",
        ExpressionAttributeNames=expression_names,
        ExpressionAttributeValues=expression_values,
        ReturnValues="ALL_NEW",
    )


def update_capture_transform_execution_arn(
    capture_id: str,
    transform_execution_arn: str,
) -> dict:
    """Persist a Step Functions ``transform_execution_arn`` on a capture row.

    Used by ``handle_transform_capture`` (Task 10, Reqs 3.12, 3.13) to
    record the Step Functions execution ARN returned by
    ``stepfunctions:StartExecution``. The update is conditional on the
    row already existing (``attribute_exists(capture_id)``); if no row
    exists, DynamoDB raises ``ConditionalCheckFailedException`` and the
    caller should surface a ``not_found`` envelope. This is a defensive
    guard — the handler verifies the row exists via :func:`get_capture`
    before calling this helper, but the conditional update closes the
    race in which the row is deleted between the read and the update.

    The status value is **not** modified by this helper. Per the
    capture-lifecycle state machine, ``transform_capture`` runs against
    a row in ``status=stopped`` and the transition to ``transformed`` /
    ``queryable`` happens only after the Step Functions execution
    succeeds (which is observed asynchronously by the orchestration
    agent or a downstream reconciler — Req 17.7 in the design).

    Args:
        capture_id: The capture identifier (validated upstream).
        transform_execution_arn: The Step Functions execution ARN
            returned by ``stepfunctions:StartExecution``. Must be a
            non-empty string. Stored verbatim under the
            ``transform_execution_arn`` attribute on the row.

    Returns:
        The boto3 ``update_item`` response with
        ``ReturnValues="ALL_NEW"``.

    Raises:
        StateError: If inputs are missing/empty, or
            ``CAPTURE_STATE_TABLE`` is unset.
        botocore.exceptions.ClientError: Propagated from DynamoDB. In
            particular, ``ConditionalCheckFailedException`` indicates
            the row no longer exists for ``capture_id``.
    """
    if not isinstance(capture_id, str) or not capture_id:
        raise StateError(
            "update_capture_transform_execution_arn requires a non-empty "
            "string capture_id"
        )
    if not isinstance(transform_execution_arn, str) or not transform_execution_arn:
        raise StateError(
            "update_capture_transform_execution_arn requires a non-empty "
            "string transform_execution_arn"
        )

    table = _capture_table()
    return table.update_item(
        Key={"capture_id": capture_id},
        UpdateExpression="SET #tea = :tea",
        ConditionExpression="attribute_exists(capture_id)",
        ExpressionAttributeNames={"#tea": "transform_execution_arn"},
        ExpressionAttributeValues={":tea": transform_execution_arn},
        ReturnValues="ALL_NEW",
    )


def _query_status_index(status: str) -> List[dict]:
    """Helper: query the ``status-index`` GSI for one status value, fully paginated."""
    table = _capture_table()
    items: List[dict] = []
    last_key = None
    while True:
        kwargs = {
            "IndexName": STATUS_INDEX_NAME,
            "KeyConditionExpression": Key("status").eq(status),
        }
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items


def query_active_captures() -> List[dict]:
    """Return every Capture_Session row with ``status='active'`` (Req 4.5).

    Used by ``handle_start_capture`` to enforce the
    Capture_Concurrency_Limit (5 simultaneous active captures).

    Returns:
        List of row dicts. Order is the order DynamoDB returned them
        (no sort is applied because the only consumer counts the rows).

    Raises:
        StateError: If ``CAPTURE_STATE_TABLE`` is unset.
        botocore.exceptions.ClientError: Propagated from DynamoDB.
    """
    return _query_status_index("active")


# Status values the design considers "historical" per the list_captures
# action commentary in tasks.md task 9 ("when ``historical``, query for
# ``stopped`` and ``transformed`` and ``queryable`` and ``stopping_failed``").
_HISTORICAL_STATUSES = ("stopped", "transformed", "queryable", "stopping_failed")
_ALL_STATUSES = ("active",) + _HISTORICAL_STATUSES

# Accepted ``status`` filter values for :func:`query_captures` per Req 3.10.
ACCEPTED_LIST_CAPTURES_STATUSES = frozenset({"all", "active", "historical"})


def _start_time_sort_key(item: dict) -> str:
    """Sort key for ``start_time`` desc ordering.

    ``start_time`` is an ISO 8601 UTC string per the schema. Lexicographic
    ordering on ISO 8601 strings is identical to chronological ordering
    when every value carries the same offset, which the design mandates
    (UTC). Items missing ``start_time`` sort to the end of a desc list.
    """
    return item.get("start_time") or ""


def query_captures(status_filter: str) -> List[dict]:
    """List Capture_Sessions filtered by status, sorted by start_time desc (Req 3.9).

    Args:
        status_filter: One of ``"all"``, ``"active"``, ``"historical"``.

    Returns:
        List of row dicts ordered by ``start_time`` descending. A
        missing or null ``start_time`` sorts to the bottom of the list.
        Returns an empty list when no rows match.

    Raises:
        StateError: If ``status_filter`` is missing/empty/unknown, or
            ``CAPTURE_STATE_TABLE`` is unset.
        botocore.exceptions.ClientError: Propagated from DynamoDB.
    """
    if status_filter not in ACCEPTED_LIST_CAPTURES_STATUSES:
        accepted = ", ".join(sorted(ACCEPTED_LIST_CAPTURES_STATUSES))
        raise StateError(
            f"query_captures status_filter must be one of {accepted}, "
            f"got {status_filter!r}"
        )

    if status_filter == "all":
        statuses: Iterable[str] = _ALL_STATUSES
    elif status_filter == "active":
        statuses = ("active",)
    else:
        statuses = _HISTORICAL_STATUSES

    items: List[dict] = []
    for status in statuses:
        items.extend(_query_status_index(status))

    items.sort(key=_start_time_sort_key, reverse=True)
    return items


def find_idempotent_capture(
    token: str,
    eni_ids: List[str],
    duration_minutes: int,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    """Return an idempotent-match row, or ``None`` if no match exists (Req 3.15).

    Looks for a Capture_Session whose ``idempotency_token`` equals
    ``token``, whose ``duration_minutes`` equals ``duration_minutes``,
    whose ``eni_ids`` set equals the supplied set (order-insensitive),
    and whose ``created_at`` is within ``IDEMPOTENCY_WINDOW`` (5 min)
    of ``now``.

    Implementation: scans the Capture_State_Table with a
    ``FilterExpression`` on ``idempotency_token`` to keep the network
    cost bounded (the table holds at most a few hundred rows for a
    demo). The window check and ENI-set check are applied after the
    scan because DynamoDB cannot natively express either as a filter
    expression.

    The scan vs. query trade-off: a GSI on ``idempotency_token`` would
    avoid the scan, but every additional GSI costs provisioned
    throughput and the design (Req 6.11) only documents the
    ``status-index`` GSI on this table. Scanning is acceptable because
    Req 3.15 is a relatively rare path (only triggered when the
    orchestration agent retries a request inside the 5-minute window)
    and the table is bounded by ``Capture_Concurrency_Limit=5`` active
    rows plus a small history.

    Args:
        token: The ``idempotency_token`` to look up. Validated upstream
            via :func:`validation.validate_idempotency_token`.
        eni_ids: Validated ENI list. Order-insensitive comparison.
        duration_minutes: Validated duration value.
        now: Override for the current time, primarily for tests. The
            helper compares ``created_at`` against
            ``now - IDEMPOTENCY_WINDOW``. When ``None``, ``datetime.now(
            timezone.utc)`` is used.

    Returns:
        The matching row dict, or ``None`` if no row qualifies.

    Raises:
        StateError: If inputs are missing/empty, or
            ``CAPTURE_STATE_TABLE`` is unset.
        botocore.exceptions.ClientError: Propagated from DynamoDB.
    """
    if not isinstance(token, str) or not token:
        raise StateError(
            "find_idempotent_capture requires a non-empty string token"
        )
    if not isinstance(eni_ids, list) or not eni_ids:
        raise StateError(
            "find_idempotent_capture requires a non-empty list of eni_ids"
        )
    if not isinstance(duration_minutes, int) or isinstance(duration_minutes, bool):
        raise StateError(
            "find_idempotent_capture requires an integer duration_minutes"
        )

    cutoff = (now or datetime.now(timezone.utc)) - IDEMPOTENCY_WINDOW
    eni_set = set(eni_ids)

    table = _capture_table()

    items: List[dict] = []
    last_key = None
    while True:
        # Reduce scanned bytes by combining the cheap predicates that
        # DynamoDB can evaluate server-side: idempotency_token equality
        # and duration_minutes equality. The set-equal check on
        # eni_ids must run client-side because DynamoDB has no
        # set-equality filter.
        kwargs = {
            "FilterExpression": Attr("idempotency_token").eq(token)
            & Attr("duration_minutes").eq(duration_minutes),
        }
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break

    for row in items:
        # Compare ENI sets order-insensitively — Req 3.15 specifies
        # "the same eni_ids set" which we interpret as equal as a set.
        row_eni_ids = row.get("eni_ids")
        if not isinstance(row_eni_ids, list):
            continue
        if set(row_eni_ids) != eni_set:
            continue

        # Compare ``created_at`` to the cutoff. ``created_at`` is an
        # ISO 8601 UTC string per the schema. Rows that pre-date the
        # 5-minute window are skipped; rows missing ``created_at`` are
        # treated as too old to match (defensive).
        created_at_raw = row.get("created_at")
        if not isinstance(created_at_raw, str):
            continue
        try:
            created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
        except ValueError:
            logger.warning(
                "find_idempotent_capture skipping row with unparseable "
                "created_at: %r",
                created_at_raw,
            )
            continue
        if created_at.tzinfo is None:
            # Defensive: treat naive timestamps as UTC.
            created_at = created_at.replace(tzinfo=timezone.utc)
        if created_at < cutoff:
            continue

        return row

    return None


# ---------------------------------------------------------------------------
# Vni_Lookup_Table helpers
# ---------------------------------------------------------------------------


def put_vni_lookup_rows(rows: List[dict]) -> None:
    """Batch-write Vni_Lookup_Table rows (Req 6.11).

    Uses ``Table.batch_writer()`` so writes are flushed in chunks of
    25 items and unprocessed-item retries are handled by ``boto3``.
    Each row must contain ``vni`` (number), ``capture_id`` (string),
    ``mirror_session_id`` (string), ``eni_id`` (string), and
    ``expires_at`` (number — Unix epoch seconds).

    Per Req 3.6, callers wrap this helper in the ``start_capture``
    rollback path so partial Vni_Lookup_Table writes are walked back
    when a later step (Capture_State_Table write or schedule create)
    fails. DynamoDB TTL on ``expires_at`` provides a safety net if
    the rollback itself fails.

    Args:
        rows: List of row dicts. May be empty (no-op).

    Raises:
        StateError: If ``rows`` is not a list, contains non-dict
            elements, or any row is missing the ``vni`` primary-key
            attribute. Also raised if ``VNI_LOOKUP_TABLE`` is unset.
        botocore.exceptions.ClientError: Propagated from DynamoDB.
    """
    if not isinstance(rows, list):
        raise StateError(
            f"put_vni_lookup_rows requires a list, got {type(rows).__name__}"
        )
    if not rows:
        return

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise StateError(
                f"put_vni_lookup_rows rows[{index}] must be a dict, got "
                f"{type(row).__name__}"
            )
        if "vni" not in row:
            raise StateError(
                f"put_vni_lookup_rows rows[{index}] is missing the "
                "required 'vni' key"
            )

    table = _vni_table()
    with table.batch_writer() as batch:
        for row in rows:
            batch.put_item(Item=row)


def delete_vni_lookup_for_capture(capture_id: str) -> int:
    """Delete every Vni_Lookup_Table row referencing ``capture_id`` (Req 3.7).

    Queries the ``capture-id-index`` GSI to find every row for this
    capture, then issues a paged ``batch_writer`` delete. Returns the
    number of rows deleted so the caller can log a one-line summary.

    Args:
        capture_id: The capture identifier (validated upstream).

    Returns:
        The number of rows deleted. ``0`` when no rows reference the
        capture (e.g. ``stop_capture`` called for a capture whose
        Traffic Mirror sessions never produced any VNI rows).

    Raises:
        StateError: If ``capture_id`` is missing/empty, or
            ``VNI_LOOKUP_TABLE`` is unset.
        botocore.exceptions.ClientError: Propagated from DynamoDB.
    """
    if not isinstance(capture_id, str) or not capture_id:
        raise StateError(
            "delete_vni_lookup_for_capture requires a non-empty "
            "string capture_id"
        )

    table = _vni_table()
    rows_to_delete: List[dict] = []
    last_key = None
    while True:
        kwargs = {
            "IndexName": CAPTURE_ID_INDEX_NAME,
            "KeyConditionExpression": Key("capture_id").eq(capture_id),
            # Project only the partition key — that's all we need to
            # delete each row, and it keeps RCU consumption minimal.
            "ProjectionExpression": "vni",
        }
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        response = table.query(**kwargs)
        rows_to_delete.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break

    if not rows_to_delete:
        return 0

    deleted = 0
    with table.batch_writer() as batch:
        for row in rows_to_delete:
            vni = row.get("vni")
            if vni is None:
                # Defensive: every row should carry the primary key,
                # but skip silently rather than raise so a single
                # malformed row cannot block the rest of the cleanup.
                logger.warning(
                    "delete_vni_lookup_for_capture: skipping row with "
                    "missing 'vni' for capture_id=%r",
                    capture_id,
                )
                continue
            batch.delete_item(Key={"vni": vni})
            deleted += 1
    return deleted


__all__ = [
    "StateError",
    "CAPTURE_STATE_TABLE_ENV",
    "VNI_LOOKUP_TABLE_ENV",
    "STATUS_INDEX_NAME",
    "CAPTURE_ID_INDEX_NAME",
    "IDEMPOTENCY_WINDOW",
    "ACCEPTED_LIST_CAPTURES_STATUSES",
    "put_capture",
    "get_capture",
    "update_capture_status",
    "update_capture_transform_execution_arn",
    "query_active_captures",
    "query_captures",
    "find_idempotent_capture",
    "put_vni_lookup_rows",
    "delete_vni_lookup_for_capture",
]
