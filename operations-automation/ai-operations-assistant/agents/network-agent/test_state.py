"""
Unit and property-based tests for ``state.py``.

Run from the ``network-agent`` directory:

    python -m pytest test_state.py -v

These tests exercise the eight DynamoDB helpers without contacting
real AWS. Two in-memory fake tables (``FakeCaptureStateTable`` and
``FakeVniLookupTable``) implement only the boto3 ``Table`` resource
methods our helpers actually call (``put_item``, ``get_item``,
``update_item``, ``query`` against the documented GSIs, ``scan`` with
the ``Attr("idempotency_token").eq(...) & Attr("duration_minutes").eq(...)``
filter, and ``batch_writer``). The fakes deliberately mirror the real
DynamoDB semantics our code depends on:

- Item dicts stored verbatim, no Decimal coercion (matches resource
  layer behavior on read-back).
- ``ConditionalCheckFailedException`` raised when an
  ``attribute_exists(capture_id)`` precondition fails on
  ``update_item``.
- Paginated ``query`` and ``scan`` driven by an injectable
  ``page_size`` so the helpers' ``LastEvaluatedKey`` handling is
  exercised at small sizes.

This avoids any external dependency on ``moto`` while still validating
each helper's real logic — caller-fault checks, GSI usage, set-equal
ENI matching, the 5-minute idempotency window, and ``start_time desc``
sorting. Parser of the boto3 condition objects is intentionally narrow:
we only support the exact shapes our helpers produce, and any other
shape raises ``NotImplementedError`` so accidental drift fails fast.
"""

from __future__ import annotations

import string
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

import pytest
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import And, Attr, ConditionBase, Equals, Key
from hypothesis import HealthCheck, given, settings, strategies as st

import state
from state import (
    ACCEPTED_LIST_CAPTURES_STATUSES,
    IDEMPOTENCY_WINDOW,
    StateError,
    delete_vni_lookup_for_capture,
    find_idempotent_capture,
    get_capture,
    put_capture,
    put_vni_lookup_rows,
    query_active_captures,
    query_captures,
    update_capture_status,
)


# ---------------------------------------------------------------------------
# Boto3 condition introspection
#
# The helpers always call DynamoDB through boto3 condition objects (Key,
# Attr, And). We don't want to reimplement DynamoDB's expression engine
# in the fakes; we just want to extract the field/value pairs our
# helpers produce. The shapes we support are:
#
#   Key("status").eq(value)
#   Key("capture_id").eq(value)
#   Attr("idempotency_token").eq(t) & Attr("duration_minutes").eq(d)
#
# Anything else raises NotImplementedError so a code change that
# introduces a new shape is loud, not silent.
# ---------------------------------------------------------------------------


def _equals_to_dict(condition: Equals) -> dict:
    """Convert a single ``Equals`` condition to {field_name: value}."""
    if not isinstance(condition, Equals):
        raise NotImplementedError(
            f"Test fake only supports Equals conditions, got "
            f"{type(condition).__name__}"
        )
    field, value = condition.get_expression()["values"]
    # boto3 represents the field as ``AttributeBase`` (Key/Attr) and the
    # value as the raw Python value.
    return {field.name: value}


def _and_to_dict(condition: ConditionBase) -> dict:
    """Convert an ``And`` of ``Equals`` conditions to a {field: value} dict."""
    if isinstance(condition, And):
        result: dict = {}
        for child in condition.get_expression()["values"]:
            result.update(_equals_or_and_to_dict(child))
        return result
    return _equals_to_dict(condition)


def _equals_or_and_to_dict(condition: ConditionBase) -> dict:
    """Recursive helper for arbitrarily nested ``And`` of ``Equals``."""
    if isinstance(condition, And):
        return _and_to_dict(condition)
    return _equals_to_dict(condition)


# ---------------------------------------------------------------------------
# Fake DynamoDB tables
# ---------------------------------------------------------------------------


def _client_error(code: str, message: str) -> ClientError:
    """Construct a ``ClientError`` mirroring the real DynamoDB shape."""
    return ClientError(
        error_response={"Error": {"Code": code, "Message": message}},
        operation_name="UpdateItem",
    )


class _BatchWriterContext:
    """Minimal stand-in for ``Table.batch_writer()`` returned context manager."""

    def __init__(self, table: "_FakeTableBase") -> None:
        self._table = table

    def __enter__(self) -> "_BatchWriterContext":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Real boto3 flushes on exit. The fake performs writes
        # synchronously, so there's nothing to flush here.
        return None

    def put_item(self, Item: dict) -> None:
        self._table.put_item(Item=Item)

    def delete_item(self, Key: dict) -> None:
        self._table.delete_item(Key=Key)


class _FakeTableBase:
    """Common scaffolding shared by both fake tables."""

    def __init__(self, page_size: int = 100) -> None:
        # Items keyed by their primary-key tuple (or single value).
        self._items: dict = {}
        self.page_size = page_size

    def put_item(self, Item: dict) -> dict:
        key = self._primary_key(Item)
        self._items[key] = dict(Item)
        return {}

    def delete_item(self, Key: dict) -> dict:
        key = self._primary_key(Key)
        self._items.pop(key, None)
        return {}

    def batch_writer(self) -> _BatchWriterContext:
        return _BatchWriterContext(self)

    # Subclasses must implement these.
    def _primary_key(self, item_or_key: dict):
        raise NotImplementedError


class FakeCaptureStateTable(_FakeTableBase):
    """In-memory stand-in for the Capture_State_Table.

    Supports the exact subset of operations ``state.py`` invokes:
    ``put_item``, ``get_item``, ``update_item`` with the helpers'
    ``UpdateExpression`` / ``ConditionExpression`` shape, ``query``
    against the ``status-index`` GSI, and ``scan`` with the
    documented ``FilterExpression``.
    """

    def _primary_key(self, item_or_key: dict):
        return item_or_key["capture_id"]

    def get_item(self, Key: dict) -> dict:
        item = self._items.get(self._primary_key(Key))
        if item is None:
            return {}
        return {"Item": dict(item)}

    def update_item(
        self,
        Key: dict,
        UpdateExpression: str,
        ConditionExpression: str = "",
        ExpressionAttributeNames: Optional[dict] = None,
        ExpressionAttributeValues: Optional[dict] = None,
        ReturnValues: str = "NONE",
    ) -> dict:
        primary = self._primary_key(Key)
        existing = self._items.get(primary)

        if (
            ConditionExpression
            and "attribute_exists(capture_id)" in ConditionExpression
            and existing is None
        ):
            raise _client_error(
                "ConditionalCheckFailedException",
                "The conditional request failed",
            )

        if not UpdateExpression.startswith("SET "):
            raise NotImplementedError(
                f"Fake only supports 'SET ...' updates, got: {UpdateExpression!r}"
            )

        # Parse "SET #a = :a, #b = :b" into a list of (name_token, value_token).
        assignments = [
            piece.strip().split("=") for piece in UpdateExpression[4:].split(",")
        ]
        assignments = [(left.strip(), right.strip()) for left, right in assignments]

        names = ExpressionAttributeNames or {}
        values = ExpressionAttributeValues or {}

        updated = dict(existing or Key)
        for name_token, value_token in assignments:
            attr = names[name_token]
            updated[attr] = values[value_token]

        self._items[primary] = updated

        if ReturnValues == "ALL_NEW":
            return {"Attributes": dict(updated)}
        return {}

    def query(
        self,
        IndexName: Optional[str] = None,
        KeyConditionExpression: ConditionBase = None,
        ExclusiveStartKey: Optional[dict] = None,
        ProjectionExpression: Optional[str] = None,
    ) -> dict:
        if IndexName != state.STATUS_INDEX_NAME:
            raise NotImplementedError(
                f"FakeCaptureStateTable only supports IndexName="
                f"{state.STATUS_INDEX_NAME!r}, got {IndexName!r}"
            )
        condition = _equals_to_dict(KeyConditionExpression)
        if "status" not in condition:
            raise NotImplementedError(
                f"FakeCaptureStateTable.query expects a 'status' equality, got "
                f"{condition!r}"
            )
        target = condition["status"]
        matches = sorted(
            (
                dict(it)
                for it in self._items.values()
                if it.get("status") == target
            ),
            key=lambda it: it.get("capture_id", ""),
        )
        return _paginate(matches, self.page_size, ExclusiveStartKey, "capture_id")

    def scan(
        self,
        FilterExpression: ConditionBase = None,
        ExclusiveStartKey: Optional[dict] = None,
    ) -> dict:
        # Helpers always supply a FilterExpression.
        condition = _equals_or_and_to_dict(FilterExpression)
        # Apply every key/value pair as an equality filter.
        matches = []
        for it in self._items.values():
            if all(it.get(k) == v for k, v in condition.items()):
                matches.append(dict(it))
        matches.sort(key=lambda it: it.get("capture_id", ""))
        return _paginate(matches, self.page_size, ExclusiveStartKey, "capture_id")


class FakeVniLookupTable(_FakeTableBase):
    """In-memory stand-in for the Vni_Lookup_Table.

    Supports ``put_item`` (via ``batch_writer``), ``delete_item`` (via
    ``batch_writer``), and ``query`` against the
    ``capture-id-index`` GSI.
    """

    def _primary_key(self, item_or_key: dict):
        return item_or_key["vni"]

    def query(
        self,
        IndexName: Optional[str] = None,
        KeyConditionExpression: ConditionBase = None,
        ExclusiveStartKey: Optional[dict] = None,
        ProjectionExpression: Optional[str] = None,
    ) -> dict:
        if IndexName != state.CAPTURE_ID_INDEX_NAME:
            raise NotImplementedError(
                f"FakeVniLookupTable only supports IndexName="
                f"{state.CAPTURE_ID_INDEX_NAME!r}, got {IndexName!r}"
            )
        condition = _equals_to_dict(KeyConditionExpression)
        target = condition["capture_id"]
        # ProjectionExpression="vni" mirrored as a real GSI projection
        # would: only return the partition key.
        matches = sorted(
            (
                {"vni": it["vni"]}
                if ProjectionExpression == "vni"
                else dict(it)
                for it in self._items.values()
                if it.get("capture_id") == target
            ),
            key=lambda it: it["vni"],
        )
        return _paginate(matches, self.page_size, ExclusiveStartKey, "vni")


def _paginate(
    matches: List[dict],
    page_size: int,
    exclusive_start_key: Optional[dict],
    pk_name: str,
) -> dict:
    """Page through ``matches`` honoring DynamoDB's exclusive-start semantics."""
    start_index = 0
    if exclusive_start_key is not None:
        start_value = exclusive_start_key.get(pk_name)
        for i, item in enumerate(matches):
            if item.get(pk_name) == start_value:
                start_index = i + 1
                break

    end_index = start_index + page_size
    page = matches[start_index:end_index]
    response = {"Items": page}
    if end_index < len(matches):
        last = page[-1] if page else None
        if last is not None:
            response["LastEvaluatedKey"] = {pk_name: last[pk_name]}
    return response


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_table_env_vars(monkeypatch):
    """Set the table-name env vars and reset the module cache for each test."""
    monkeypatch.setenv(state.CAPTURE_STATE_TABLE_ENV, "Capture_State_Table_Test")
    monkeypatch.setenv(state.VNI_LOOKUP_TABLE_ENV, "Vni_Lookup_Table_Test")
    state._reset_cache_for_tests()
    yield
    state._reset_cache_for_tests()


@pytest.fixture
def capture_table(monkeypatch):
    """Inject a fresh ``FakeCaptureStateTable`` for each test."""
    table = FakeCaptureStateTable()
    monkeypatch.setattr(state, "_capture_state_table", table)
    return table


@pytest.fixture
def vni_table(monkeypatch):
    """Inject a fresh ``FakeVniLookupTable`` for each test."""
    table = FakeVniLookupTable()
    monkeypatch.setattr(state, "_vni_lookup_table", table)
    return table


# Used by tests that need a row to operate on.
def _capture_row(
    capture_id: str = "cap_001",
    eni_ids: Optional[List[str]] = None,
    duration_minutes: int = 15,
    status: str = "active",
    start_time: str = "2026-04-20T12:00:00+00:00",
    deadline: str = "2026-04-20T12:15:00+00:00",
    idempotency_token: Optional[str] = None,
    created_at: Optional[str] = None,
    extra: Optional[dict] = None,
) -> dict:
    row = {
        "capture_id": capture_id,
        "eni_ids": eni_ids or ["eni-12345678"],
        "duration_minutes": duration_minutes,
        "status": status,
        "start_time": start_time,
        "deadline": deadline,
        "mirror_session_ids": ["tms-1"],
        "created_at": created_at or "2026-04-20T12:00:00+00:00",
        "requested_by": "test-user",
    }
    if idempotency_token is not None:
        row["idempotency_token"] = idempotency_token
    if extra:
        row.update(extra)
    return row


# ---------------------------------------------------------------------------
# Misconfiguration / caller-fault tests
# ---------------------------------------------------------------------------


class TestEnvironmentVariables:
    def test_missing_capture_state_table_env_raises_state_error(self, monkeypatch):
        monkeypatch.delenv(state.CAPTURE_STATE_TABLE_ENV, raising=False)
        state._reset_cache_for_tests()
        with pytest.raises(StateError) as exc_info:
            state._capture_table()
        assert "CAPTURE_STATE_TABLE" in str(exc_info.value)

    def test_missing_vni_lookup_table_env_raises_state_error(self, monkeypatch):
        monkeypatch.delenv(state.VNI_LOOKUP_TABLE_ENV, raising=False)
        state._reset_cache_for_tests()
        with pytest.raises(StateError) as exc_info:
            state._vni_table()
        assert "VNI_LOOKUP_TABLE" in str(exc_info.value)

    def test_empty_capture_state_table_env_raises_state_error(self, monkeypatch):
        monkeypatch.setenv(state.CAPTURE_STATE_TABLE_ENV, "")
        state._reset_cache_for_tests()
        with pytest.raises(StateError):
            state._capture_table()


# ---------------------------------------------------------------------------
# put_capture
# ---------------------------------------------------------------------------


class TestPutCapture:
    def test_writes_row(self, capture_table):
        row = _capture_row()
        put_capture(row)
        assert capture_table._items["cap_001"] == row

    def test_rejects_non_dict(self, capture_table):
        with pytest.raises(StateError):
            put_capture("not a dict")  # type: ignore[arg-type]

    def test_rejects_missing_capture_id(self, capture_table):
        with pytest.raises(StateError) as exc_info:
            put_capture({"eni_ids": ["eni-12345678"]})
        assert "capture_id" in str(exc_info.value)

    def test_overwrites_existing_row_with_same_pk(self, capture_table):
        first = _capture_row()
        put_capture(first)
        second = _capture_row(extra={"status": "stopped"})
        put_capture(second)
        # status column should now be 'stopped'
        assert capture_table._items["cap_001"]["status"] == "stopped"


# ---------------------------------------------------------------------------
# get_capture
# ---------------------------------------------------------------------------


class TestGetCapture:
    def test_returns_row_when_present(self, capture_table):
        row = _capture_row()
        capture_table.put_item(Item=row)
        assert get_capture("cap_001") == row

    def test_returns_none_when_absent(self, capture_table):
        assert get_capture("missing") is None

    @pytest.mark.parametrize("bad", [None, "", 123, [], {}])
    def test_rejects_bad_capture_id(self, capture_table, bad):
        with pytest.raises(StateError):
            get_capture(bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# update_capture_status
# ---------------------------------------------------------------------------


class TestUpdateCaptureStatus:
    def test_updates_status(self, capture_table):
        row = _capture_row()
        capture_table.put_item(Item=row)
        result = update_capture_status("cap_001", "stopped")
        assert capture_table._items["cap_001"]["status"] == "stopped"
        # status preserved in returned attributes
        assert result["Attributes"]["status"] == "stopped"

    def test_updates_status_and_stopped_reason(self, capture_table):
        capture_table.put_item(Item=_capture_row())
        update_capture_status(
            "cap_001",
            "stopped",
            stopped_reason="auto_stop_deadline",
        )
        stored = capture_table._items["cap_001"]
        assert stored["status"] == "stopped"
        assert stored["stopped_reason"] == "auto_stop_deadline"

    def test_raises_conditional_check_on_missing_row(self, capture_table):
        with pytest.raises(ClientError) as exc_info:
            update_capture_status("missing", "stopped")
        assert (
            exc_info.value.response["Error"]["Code"]
            == "ConditionalCheckFailedException"
        )

    @pytest.mark.parametrize(
        "args",
        [
            (None, "stopped"),  # missing capture_id
            ("", "stopped"),  # empty capture_id
            ("cap", None),  # missing status
            ("cap", ""),  # empty status
        ],
    )
    def test_rejects_bad_inputs(self, capture_table, args):
        capture_id, status = args
        with pytest.raises(StateError):
            update_capture_status(capture_id, status)  # type: ignore[arg-type]

    def test_rejects_empty_stopped_reason_when_supplied(self, capture_table):
        capture_table.put_item(Item=_capture_row())
        with pytest.raises(StateError):
            update_capture_status("cap_001", "stopped", stopped_reason="")


# ---------------------------------------------------------------------------
# query_active_captures
# ---------------------------------------------------------------------------


class TestQueryActiveCaptures:
    def test_returns_only_active_rows(self, capture_table):
        capture_table.put_item(Item=_capture_row("a", status="active"))
        capture_table.put_item(Item=_capture_row("b", status="stopped"))
        capture_table.put_item(Item=_capture_row("c", status="active"))
        rows = query_active_captures()
        assert {r["capture_id"] for r in rows} == {"a", "c"}

    def test_returns_empty_when_no_active_rows(self, capture_table):
        capture_table.put_item(Item=_capture_row("a", status="stopped"))
        assert query_active_captures() == []

    def test_paginates_through_status_index(self, capture_table):
        capture_table.page_size = 2
        for i in range(7):
            capture_table.put_item(Item=_capture_row(f"a{i}", status="active"))
        rows = query_active_captures()
        assert len(rows) == 7


# ---------------------------------------------------------------------------
# query_captures
# ---------------------------------------------------------------------------


class TestQueryCaptures:
    def test_all_returns_every_row_sorted_by_start_time_desc(self, capture_table):
        capture_table.put_item(
            Item=_capture_row("a", status="active", start_time="2026-04-20T10:00:00+00:00")
        )
        capture_table.put_item(
            Item=_capture_row("b", status="stopped", start_time="2026-04-20T11:00:00+00:00")
        )
        capture_table.put_item(
            Item=_capture_row("c", status="transformed", start_time="2026-04-20T12:00:00+00:00")
        )
        rows = query_captures("all")
        assert [r["capture_id"] for r in rows] == ["c", "b", "a"]

    def test_active_only(self, capture_table):
        capture_table.put_item(Item=_capture_row("a", status="active"))
        capture_table.put_item(Item=_capture_row("b", status="stopped"))
        rows = query_captures("active")
        assert [r["capture_id"] for r in rows] == ["a"]

    def test_historical_includes_all_terminal_statuses(self, capture_table):
        capture_table.put_item(Item=_capture_row("a", status="active"))
        for i, status in enumerate(
            ("stopped", "transformed", "queryable", "stopping_failed"),
            start=1,
        ):
            capture_table.put_item(
                Item=_capture_row(
                    f"h{i}",
                    status=status,
                    start_time=f"2026-04-20T1{i}:00:00+00:00",
                )
            )
        rows = query_captures("historical")
        assert {r["capture_id"] for r in rows} == {"h1", "h2", "h3", "h4"}
        # active row excluded
        assert all(r["status"] != "active" for r in rows)

    def test_invalid_status_filter_raises(self, capture_table):
        with pytest.raises(StateError) as exc_info:
            query_captures("running")
        assert "active" in str(exc_info.value)
        assert "historical" in str(exc_info.value)
        assert "all" in str(exc_info.value)

    def test_missing_start_time_sorts_to_bottom_of_desc_list(self, capture_table):
        capture_table.put_item(
            Item=_capture_row("a", start_time="2026-04-20T10:00:00+00:00")
        )
        bad = _capture_row("b", start_time="2026-04-20T11:00:00+00:00")
        bad.pop("start_time")
        capture_table.put_item(Item=bad)
        rows = query_captures("all")
        assert [r["capture_id"] for r in rows] == ["a", "b"]


# ---------------------------------------------------------------------------
# find_idempotent_capture
# ---------------------------------------------------------------------------


class TestFindIdempotentCapture:
    def test_returns_match_within_window(self, capture_table):
        now = datetime(2026, 4, 20, 12, 5, 0, tzinfo=timezone.utc)
        # Created 2 minutes ago — well inside the 5-minute window.
        created = (now - timedelta(minutes=2)).isoformat()
        row = _capture_row(
            "cap_idem",
            idempotency_token="tok-1",
            duration_minutes=15,
            eni_ids=["eni-aaaaaaaa", "eni-bbbbbbbb"],
            created_at=created,
        )
        capture_table.put_item(Item=row)
        match = find_idempotent_capture(
            "tok-1",
            ["eni-aaaaaaaa", "eni-bbbbbbbb"],
            15,
            now=now,
        )
        assert match is not None
        assert match["capture_id"] == "cap_idem"

    def test_returns_match_when_eni_order_differs(self, capture_table):
        now = datetime(2026, 4, 20, 12, 5, 0, tzinfo=timezone.utc)
        capture_table.put_item(
            Item=_capture_row(
                "cap_idem",
                idempotency_token="tok-1",
                duration_minutes=15,
                eni_ids=["eni-aaaaaaaa", "eni-bbbbbbbb"],
                created_at=(now - timedelta(minutes=1)).isoformat(),
            )
        )
        # Reversed order — ENI sets are equal.
        match = find_idempotent_capture(
            "tok-1",
            ["eni-bbbbbbbb", "eni-aaaaaaaa"],
            15,
            now=now,
        )
        assert match is not None

    def test_no_match_outside_window(self, capture_table):
        now = datetime(2026, 4, 20, 12, 5, 0, tzinfo=timezone.utc)
        # Created 6 minutes ago — outside the 5-minute window.
        created = (now - timedelta(minutes=6)).isoformat()
        capture_table.put_item(
            Item=_capture_row(
                "cap_old",
                idempotency_token="tok-1",
                duration_minutes=15,
                created_at=created,
            )
        )
        assert find_idempotent_capture("tok-1", ["eni-12345678"], 15, now=now) is None

    def test_no_match_when_eni_set_differs(self, capture_table):
        now = datetime(2026, 4, 20, 12, 5, 0, tzinfo=timezone.utc)
        capture_table.put_item(
            Item=_capture_row(
                "cap_idem",
                idempotency_token="tok-1",
                eni_ids=["eni-aaaaaaaa"],
                created_at=(now - timedelta(minutes=1)).isoformat(),
            )
        )
        # Different ENI set — must NOT match.
        assert find_idempotent_capture("tok-1", ["eni-99999999"], 15, now=now) is None

    def test_no_match_when_duration_differs(self, capture_table):
        now = datetime(2026, 4, 20, 12, 5, 0, tzinfo=timezone.utc)
        capture_table.put_item(
            Item=_capture_row(
                "cap_idem",
                idempotency_token="tok-1",
                duration_minutes=15,
                created_at=(now - timedelta(minutes=1)).isoformat(),
            )
        )
        assert find_idempotent_capture("tok-1", ["eni-12345678"], 30, now=now) is None

    def test_no_match_for_different_token(self, capture_table):
        now = datetime(2026, 4, 20, 12, 5, 0, tzinfo=timezone.utc)
        capture_table.put_item(
            Item=_capture_row(
                "cap_idem",
                idempotency_token="tok-1",
                created_at=(now - timedelta(minutes=1)).isoformat(),
            )
        )
        assert find_idempotent_capture("tok-2", ["eni-12345678"], 15, now=now) is None

    def test_returns_none_when_table_empty(self, capture_table):
        assert find_idempotent_capture("tok-1", ["eni-12345678"], 15) is None

    @pytest.mark.parametrize(
        "token,eni_ids,duration",
        [
            (None, ["eni-12345678"], 15),
            ("", ["eni-12345678"], 15),
            ("tok-1", None, 15),
            ("tok-1", [], 15),
            ("tok-1", ["eni-12345678"], None),
            ("tok-1", ["eni-12345678"], "15"),
            ("tok-1", ["eni-12345678"], True),  # bool rejected
        ],
    )
    def test_rejects_bad_inputs(self, capture_table, token, eni_ids, duration):
        with pytest.raises(StateError):
            find_idempotent_capture(token, eni_ids, duration)  # type: ignore[arg-type]

    def test_skips_rows_with_unparseable_created_at(self, capture_table):
        now = datetime(2026, 4, 20, 12, 5, 0, tzinfo=timezone.utc)
        capture_table.put_item(
            Item=_capture_row(
                "cap_idem",
                idempotency_token="tok-1",
                created_at="not-a-timestamp",
            )
        )
        assert find_idempotent_capture("tok-1", ["eni-12345678"], 15, now=now) is None

    def test_idempotency_window_constant_matches_design(self):
        """Validates: Requirement 3.15 (5-minute window)."""
        assert IDEMPOTENCY_WINDOW == timedelta(minutes=5)


# ---------------------------------------------------------------------------
# put_vni_lookup_rows
# ---------------------------------------------------------------------------


class TestPutVniLookupRows:
    def test_writes_rows(self, vni_table):
        rows = [
            {
                "vni": 1001,
                "capture_id": "cap_001",
                "mirror_session_id": "tms-1",
                "eni_id": "eni-12345678",
                "expires_at": 1_730_000_000,
            },
            {
                "vni": 1002,
                "capture_id": "cap_001",
                "mirror_session_id": "tms-2",
                "eni_id": "eni-aaaaaaaa",
                "expires_at": 1_730_000_000,
            },
        ]
        put_vni_lookup_rows(rows)
        assert vni_table._items[1001]["mirror_session_id"] == "tms-1"
        assert vni_table._items[1002]["mirror_session_id"] == "tms-2"

    def test_empty_list_is_noop(self, vni_table):
        put_vni_lookup_rows([])
        assert vni_table._items == {}

    def test_rejects_non_list(self, vni_table):
        with pytest.raises(StateError):
            put_vni_lookup_rows({"vni": 1})  # type: ignore[arg-type]

    def test_rejects_non_dict_element(self, vni_table):
        with pytest.raises(StateError):
            put_vni_lookup_rows([{"vni": 1}, "not a dict"])  # type: ignore[list-item]

    def test_rejects_row_missing_vni_pk(self, vni_table):
        with pytest.raises(StateError) as exc_info:
            put_vni_lookup_rows([{"capture_id": "cap_001"}])
        assert "vni" in str(exc_info.value)


# ---------------------------------------------------------------------------
# delete_vni_lookup_for_capture
# ---------------------------------------------------------------------------


class TestDeleteVniLookupForCapture:
    def test_deletes_all_rows_for_capture(self, vni_table):
        # 3 rows for cap_001, 1 for cap_002.
        for vni, cap in [
            (1001, "cap_001"),
            (1002, "cap_001"),
            (1003, "cap_001"),
            (2001, "cap_002"),
        ]:
            vni_table.put_item(
                Item={
                    "vni": vni,
                    "capture_id": cap,
                    "mirror_session_id": f"tms-{vni}",
                    "eni_id": "eni-12345678",
                    "expires_at": 0,
                }
            )

        deleted = delete_vni_lookup_for_capture("cap_001")
        assert deleted == 3
        # cap_002 row preserved
        assert 2001 in vni_table._items
        # cap_001 rows gone
        assert 1001 not in vni_table._items
        assert 1002 not in vni_table._items
        assert 1003 not in vni_table._items

    def test_no_rows_for_capture_returns_zero(self, vni_table):
        assert delete_vni_lookup_for_capture("cap_missing") == 0

    def test_paginates_through_capture_id_index(self, vni_table):
        vni_table.page_size = 2
        for i in range(7):
            vni_table.put_item(
                Item={
                    "vni": 1000 + i,
                    "capture_id": "cap_paged",
                    "mirror_session_id": f"tms-{i}",
                    "eni_id": "eni-12345678",
                    "expires_at": 0,
                }
            )
        deleted = delete_vni_lookup_for_capture("cap_paged")
        assert deleted == 7
        assert vni_table._items == {}

    @pytest.mark.parametrize("bad", [None, "", 123, []])
    def test_rejects_bad_capture_id(self, vni_table, bad):
        with pytest.raises(StateError):
            delete_vni_lookup_for_capture(bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


_CAPTURE_ID_ALPHABET = string.ascii_letters + string.digits + "_-"


def _capture_id_st():
    return st.text(alphabet=_CAPTURE_ID_ALPHABET, min_size=1, max_size=64)


class TestPropertyPutGetRoundTrip:
    """Property: every put_capture row is recoverable by get_capture (Reqs 3.7, 6.11)."""

    # ``monkeypatch`` is a function-scoped fixture; Hypothesis flags this
    # by default. We suppress the health check because each example
    # explicitly resets the cached singletons via
    # ``state._reset_cache_for_tests()`` and re-injects a fresh
    # ``FakeCaptureStateTable`` before exercising any helper, so no
    # state from a prior example can leak into the current one.
    # ``too_slow`` is also suppressed because the per-example cache
    # reset legitimately takes a few milliseconds and is not under
    # the input-generation budget Hypothesis tracks by default.
    @given(
        capture_id=_capture_id_st(),
        eni_count=st.integers(min_value=1, max_value=3),
        duration=st.integers(min_value=1, max_value=60),
        status=st.sampled_from(
            ["active", "stopped", "transformed", "queryable", "stopping_failed"]
        ),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[
            HealthCheck.function_scoped_fixture,
            HealthCheck.too_slow,
        ],
    )
    def test_round_trip(self, capture_id, eni_count, duration, status, monkeypatch):
        # Recreate fixtures inside the property test because Hypothesis
        # runs each example as a separate function call.
        monkeypatch.setenv(state.CAPTURE_STATE_TABLE_ENV, "T")
        monkeypatch.setenv(state.VNI_LOOKUP_TABLE_ENV, "V")
        state._reset_cache_for_tests()
        table = FakeCaptureStateTable()
        monkeypatch.setattr(state, "_capture_state_table", table)

        eni_ids = [f"eni-{i:08x}" for i in range(eni_count)]
        row = _capture_row(
            capture_id,
            eni_ids=eni_ids,
            duration_minutes=duration,
            status=status,
        )
        put_capture(row)
        retrieved = get_capture(capture_id)
        assert retrieved == row


class TestPropertyQueryCapturesSortOrder:
    """Property: query_captures returns rows in start_time-desc order (Req 3.9)."""

    @given(
        n=st.integers(min_value=2, max_value=20),
        seed=st.integers(min_value=0, max_value=10**9),
    )
    @settings(
        max_examples=50,
        suppress_health_check=[
            HealthCheck.function_scoped_fixture,
            HealthCheck.too_slow,
        ],
    )
    def test_descending_order(self, n, seed, monkeypatch):
        monkeypatch.setenv(state.CAPTURE_STATE_TABLE_ENV, "T")
        monkeypatch.setenv(state.VNI_LOOKUP_TABLE_ENV, "V")
        state._reset_cache_for_tests()
        table = FakeCaptureStateTable()
        monkeypatch.setattr(state, "_capture_state_table", table)

        # Generate n unique capture_ids and ascending start_times spaced
        # by 1 minute starting from a deterministic base.
        base = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
        for i in range(n):
            t = (base + timedelta(minutes=i)).isoformat()
            cap_id = f"cap_{seed:09d}_{i:03d}"
            table.put_item(
                Item=_capture_row(
                    cap_id,
                    status="stopped",
                    start_time=t,
                )
            )

        rows = query_captures("historical")
        times = [r["start_time"] for r in rows]
        assert times == sorted(times, reverse=True)
        assert len(times) == n


class TestPropertyConcurrencyCount:
    """Property: query_active_captures count equals the number of active rows.

    Validates the Capture_Concurrency_Limit-supporting helper (Req 4.5):
    no matter how active and non-active rows are interleaved, the
    helper's result count equals the true active-row count.
    """

    @given(
        n_active=st.integers(min_value=0, max_value=10),
        n_other=st.integers(min_value=0, max_value=10),
    )
    @settings(
        max_examples=50,
        suppress_health_check=[
            HealthCheck.function_scoped_fixture,
            HealthCheck.too_slow,
        ],
    )
    def test_count_equals_active(self, n_active, n_other, monkeypatch):
        monkeypatch.setenv(state.CAPTURE_STATE_TABLE_ENV, "T")
        monkeypatch.setenv(state.VNI_LOOKUP_TABLE_ENV, "V")
        state._reset_cache_for_tests()
        table = FakeCaptureStateTable()
        monkeypatch.setattr(state, "_capture_state_table", table)

        for i in range(n_active):
            table.put_item(Item=_capture_row(f"a{i}", status="active"))
        for i in range(n_other):
            table.put_item(Item=_capture_row(f"o{i}", status="stopped"))

        assert len(query_active_captures()) == n_active


class TestPropertyDeleteVniLookupReturnsZero:
    """Property: deleting an unknown capture is a no-op returning 0."""

    @given(capture_id=_capture_id_st())
    @settings(
        max_examples=50,
        suppress_health_check=[
            HealthCheck.function_scoped_fixture,
            HealthCheck.too_slow,
        ],
    )
    def test_unknown_capture_returns_zero(self, capture_id, monkeypatch):
        monkeypatch.setenv(state.CAPTURE_STATE_TABLE_ENV, "T")
        monkeypatch.setenv(state.VNI_LOOKUP_TABLE_ENV, "V")
        state._reset_cache_for_tests()
        table = FakeVniLookupTable()
        monkeypatch.setattr(state, "_vni_lookup_table", table)
        assert delete_vni_lookup_for_capture(capture_id) == 0
