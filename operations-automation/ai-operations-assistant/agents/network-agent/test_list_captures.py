"""
Unit and property-based tests for ``handle_list_captures`` (Task 9, Reqs 3.9, 3.10, 3.11).

Exercises:

- Validates ``status`` against ``{all, active, historical}`` (Req 3.10),
  surfacing ``invalid_parameter`` for any other value or non-string input.
- Defaults missing/None ``status`` to ``"all"`` and returns both active
  and historical rows (Req 3.11).
- Delegates to ``state.query_captures`` so the GSI dispatch and the
  ``start_time`` desc sort (Req 3.9) are reused without duplication.
- Projects each row to the documented field set: ``capture_id``,
  ``eni_ids``, ``start_time``, ``deadline``, ``status``,
  ``stopped_reason``, ``mirror_session_ids``.
- Sets ``metadata.sourceApi = "dynamodb:Query"`` on every response.
- Surfaces ``configuration_missing`` when ``state.StateError`` is
  raised (e.g. table-name env var unset) and an ``aws_*`` category
  when DynamoDB raises a ``ClientError``.

Tests use an in-memory recorder to stand in for the ``state``
module's free functions, keeping the entire handler path runnable
without AWS or ``moto``.

Run from the ``network-agent`` directory:

    python -m pytest test_list_captures.py -v
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pytest
from botocore.exceptions import BotoCoreError, ClientError
from hypothesis import HealthCheck, given, settings, strategies as st

import main
import state


# ---------------------------------------------------------------------------
# Fake state-module surface
# ---------------------------------------------------------------------------


class _FakeState:
    """In-memory recorder for ``state.query_captures``.

    Tests preload either ``rows_by_filter`` (rows returned per status
    filter) or ``raise_on_filter`` (an exception raised when the
    filter matches), and inspect ``calls`` to confirm the handler
    issued exactly one query with the expected filter.
    """

    def __init__(self) -> None:
        # status_filter -> list of row dicts
        self.rows_by_filter: Dict[str, List[dict]] = {}
        # status_filter -> Exception to raise instead of returning rows
        self.raise_on_filter: Dict[str, Exception] = {}
        # status_filter values the handler actually queried (in order)
        self.calls: List[str] = []

    def query_captures(self, status_filter: str) -> List[dict]:
        self.calls.append(status_filter)
        if status_filter in self.raise_on_filter:
            raise self.raise_on_filter[status_filter]
        # Default to an empty list when the filter is unset so tests
        # that only care about the dispatch path don't have to seed
        # row data.
        return list(self.rows_by_filter.get(status_filter, []))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_state(monkeypatch) -> _FakeState:
    """Patch ``state.query_captures`` with a fresh recorder per test."""
    fake = _FakeState()
    monkeypatch.setattr(state, "query_captures", fake.query_captures)
    return fake


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(
    capture_id: str = "cap-001",
    *,
    status: str = "active",
    start_time: str = "2026-04-20T12:00:00+00:00",
    deadline: str = "2026-04-20T12:15:00+00:00",
    eni_ids: Optional[List[str]] = None,
    mirror_session_ids: Optional[List[str]] = None,
    stopped_reason: Optional[str] = None,
    extra: Optional[dict] = None,
) -> dict:
    """Build a Capture_State_Table row dict for tests."""
    row = {
        "capture_id": capture_id,
        "eni_ids": eni_ids if eni_ids is not None else ["eni-12345678"],
        "start_time": start_time,
        "deadline": deadline,
        "status": status,
        "duration_minutes": 15,
        "mirror_session_ids": mirror_session_ids
        if mirror_session_ids is not None
        else ["tms-1"],
        "created_at": start_time,
        "requested_by": "test-user",
    }
    if stopped_reason is not None:
        row["stopped_reason"] = stopped_reason
    if extra:
        row.update(extra)
    return row


# ---------------------------------------------------------------------------
# Caller-fault paths (validation, defaulting)
# ---------------------------------------------------------------------------


class TestStatusValidation:
    """Req 3.10: invalid ``status`` values are rejected before any query."""

    @pytest.mark.parametrize(
        "bad_value",
        [
            "ALL",  # case-sensitive: only lowercase is accepted
            "Active",
            "historic",  # near miss
            "stopped",  # internal status, not a public filter value
            "transformed",
            "queryable",
            "stopping_failed",
            "",  # empty string
            "  active  ",  # whitespace not stripped by validator
            "all,active",  # comma-separated forms not accepted
        ],
    )
    def test_rejects_invalid_string_values(self, fake_state, bad_value):
        result = main.handle_list_captures({"status": bad_value})

        assert result["success"] is False
        assert result["domain"] == "network"
        assert result["metadata"]["errorCategory"] == "invalid_parameter"
        # Source API is the primary AWS surface for the action even on
        # caller-fault rejections so the orchestration agent can
        # attribute the error correctly.
        assert result["metadata"]["sourceApi"] == "dynamodb:Query"
        # No DynamoDB query was issued.
        assert fake_state.calls == []

    @pytest.mark.parametrize(
        "bad_type", [123, 1.5, True, False, ["all"], {"value": "all"}]
    )
    def test_rejects_non_string_values(self, fake_state, bad_type):
        result = main.handle_list_captures({"status": bad_type})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_state.calls == []

    def test_error_message_lists_accepted_values(self, fake_state):
        result = main.handle_list_captures({"status": "bogus"})

        # The accepted set must be explicit in the error message so
        # the orchestration agent can echo it to the user.
        assert "all" in result["error"]
        assert "active" in result["error"]
        assert "historical" in result["error"]


class TestDefaulting:
    """Req 3.11: missing or None ``status`` defaults to ``"all"``."""

    def test_missing_status_defaults_to_all(self, fake_state):
        result = main.handle_list_captures({})

        assert result["success"] is True
        assert fake_state.calls == ["all"]
        assert result["data"]["status"] == "all"

    def test_explicit_none_status_defaults_to_all(self, fake_state):
        result = main.handle_list_captures({"status": None})

        assert result["success"] is True
        assert fake_state.calls == ["all"]
        assert result["data"]["status"] == "all"

    def test_non_dict_params_treated_as_empty(self, fake_state):
        # The handler tolerates non-dict ``params`` (treats as ``{}``)
        # and applies the default ``"all"`` filter.
        result = main.handle_list_captures(None)

        assert result["success"] is True
        assert fake_state.calls == ["all"]


# ---------------------------------------------------------------------------
# Filter dispatch (Reqs 3.9, 3.11)
# ---------------------------------------------------------------------------


class TestFilterDispatch:
    """Each accepted filter value is forwarded verbatim to ``state.query_captures``."""

    @pytest.mark.parametrize("filter_value", ["all", "active", "historical"])
    def test_filter_value_forwarded_to_state_helper(
        self, fake_state, filter_value
    ):
        result = main.handle_list_captures({"status": filter_value})

        assert result["success"] is True
        assert fake_state.calls == [filter_value]
        # The data envelope echoes the resolved filter so the
        # orchestration agent can mention it in chat.
        assert result["data"]["status"] == filter_value


# ---------------------------------------------------------------------------
# Response shape and field projection
# ---------------------------------------------------------------------------


class TestResponseShape:
    """The response envelope and row projection match the design schema."""

    _PROJECTED_FIELDS = {
        "capture_id",
        "eni_ids",
        "start_time",
        "deadline",
        "status",
        "stopped_reason",
        "mirror_session_ids",
    }

    def test_empty_result_returns_empty_list(self, fake_state):
        result = main.handle_list_captures({"status": "active"})

        assert result["success"] is True
        assert result["data"]["captures"] == []
        assert result["data"]["count"] == 0
        # Friendly summary even when nothing matches.
        assert "active" in result["formattedText"]

    def test_each_row_carries_only_documented_fields(self, fake_state):
        # The row carries extra attributes (idempotency_token,
        # requested_by) that the design does not surface via
        # list_captures. The handler must drop them.
        full_row = _row(
            extra={
                "idempotency_token": "tok-abcdef",
                "requested_by": "alice",
                "transform_execution_arn": "arn:aws:states:...",
                "auto_stop_schedule_armed": True,
            },
        )
        fake_state.rows_by_filter["all"] = [full_row]

        result = main.handle_list_captures({})

        assert result["success"] is True
        assert result["data"]["count"] == 1
        projected = result["data"]["captures"][0]
        assert set(projected.keys()) == self._PROJECTED_FIELDS

    def test_missing_optional_attributes_become_defaults(self, fake_state):
        # A row without ``stopped_reason`` (e.g. an active capture)
        # surfaces ``None`` so the orchestration agent's downstream
        # rendering sees a stable shape.
        row = _row(stopped_reason=None)
        fake_state.rows_by_filter["all"] = [row]

        result = main.handle_list_captures({})

        projected = result["data"]["captures"][0]
        assert projected["stopped_reason"] is None
        # eni_ids and mirror_session_ids are always surfaced as lists
        # even when the row carries them as ``None``.
        empty_row = _row(capture_id="cap-empty")
        empty_row["eni_ids"] = None
        empty_row["mirror_session_ids"] = None
        fake_state.rows_by_filter["all"] = [empty_row]

        result2 = main.handle_list_captures({})
        projected2 = result2["data"]["captures"][0]
        assert projected2["eni_ids"] == []
        assert projected2["mirror_session_ids"] == []

    def test_stopped_reason_passed_through(self, fake_state):
        # The Auto_Stop_Schedule path writes
        # ``stopped_reason="auto_stop_deadline"`` (Req 4.10). The
        # handler must surface it verbatim so the orchestration agent
        # can distinguish auto-stops from user-initiated stops.
        row = _row(
            capture_id="cap-auto",
            status="stopped",
            stopped_reason="auto_stop_deadline",
        )
        fake_state.rows_by_filter["historical"] = [row]

        result = main.handle_list_captures({"status": "historical"})

        projected = result["data"]["captures"][0]
        assert projected["status"] == "stopped"
        assert projected["stopped_reason"] == "auto_stop_deadline"

    def test_metadata_source_api_is_dynamodb_query(self, fake_state):
        """Task 9 mandates ``metadata.sourceApi = "dynamodb:Query"``."""
        result = main.handle_list_captures({})
        assert result["metadata"]["sourceApi"] == "dynamodb:Query"

    def test_response_envelope_shape(self, fake_state):
        """Property 10 of the design — uniform envelope."""
        result = main.handle_list_captures({})

        # Required envelope keys.
        for key in ("success", "domain", "data", "formattedText", "metadata"):
            assert key in result
        assert result["domain"] == "network"
        # Required metadata keys.
        for key in ("sourceApi", "queryTimestamp", "dataFreshness"):
            assert key in result["metadata"]
        assert result["metadata"]["dataFreshness"] == "real-time"


# ---------------------------------------------------------------------------
# Sort order (Req 3.9)
# ---------------------------------------------------------------------------


class TestSortOrder:
    """Req 3.9: rows are returned ``start_time`` descending.

    The actual sorting happens in ``state.query_captures``; these tests
    verify that the handler does not reorder the rows it receives. We
    feed pre-sorted rows from the fake helper and assert the projection
    preserves the order.
    """

    def test_handler_preserves_input_order(self, fake_state):
        rows = [
            _row(
                capture_id="cap-newest",
                start_time="2026-04-20T13:00:00+00:00",
            ),
            _row(
                capture_id="cap-mid",
                start_time="2026-04-20T12:30:00+00:00",
            ),
            _row(
                capture_id="cap-oldest",
                start_time="2026-04-20T12:00:00+00:00",
            ),
        ]
        # ``state.query_captures`` returns rows already sorted desc, so
        # the handler should not perturb them.
        fake_state.rows_by_filter["all"] = rows

        result = main.handle_list_captures({})
        ids = [r["capture_id"] for r in result["data"]["captures"]]
        assert ids == ["cap-newest", "cap-mid", "cap-oldest"]


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestStateErrors:
    """``state.StateError`` (configuration) is surfaced as ``configuration_missing``."""

    def test_state_error_returns_configuration_missing(self, fake_state):
        fake_state.raise_on_filter["all"] = state.StateError(
            "Required environment variable 'CAPTURE_STATE_TABLE' is not set."
        )

        result = main.handle_list_captures({})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "configuration_missing"
        # The status filter is echoed in ``data`` so an operator can
        # see what the handler attempted.
        assert result["data"]["status"] == "all"
        assert "CAPTURE_STATE_TABLE" in result["error"]


class TestAwsErrors:
    """Boto3 errors are classified via ``_classify_aws_error`` (EH-2)."""

    def test_throttling_error_classified_as_aws_throttled(self, fake_state):
        exc = ClientError(
            error_response={
                "Error": {
                    "Code": "ThrottlingException",
                    "Message": "Rate exceeded",
                },
            },
            operation_name="Query",
        )
        fake_state.raise_on_filter["active"] = exc

        result = main.handle_list_captures({"status": "active"})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "aws_throttled"
        assert "dynamodb:Query" in result["error"]

    def test_access_denied_classified_as_aws_access_denied(self, fake_state):
        exc = ClientError(
            error_response={
                "Error": {
                    "Code": "AccessDeniedException",
                    "Message": "Permission denied",
                },
            },
            operation_name="Query",
        )
        fake_state.raise_on_filter["all"] = exc

        result = main.handle_list_captures({})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "aws_access_denied"

    def test_botocore_error_classified_as_aws_other(self, fake_state):
        # BotoCoreError is the base class for non-API client failures
        # (DNS lookup, connection timeout, etc.). The handler must
        # still produce a structured envelope.
        fake_state.raise_on_filter["all"] = BotoCoreError()

        result = main.handle_list_captures({})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "aws_other"
        assert "list_captures" in result["error"]


# ---------------------------------------------------------------------------
# Property-based tests
#
# The task is not labelled as a PBT task in the spec, but the existing
# test suite includes hypothesis-based checks for adjacent handlers
# (test_validation, test_state, test_stop_capture). We add two
# lightweight properties that exercise the whole handler path under
# generated inputs to catch regressions cheaply.
# ---------------------------------------------------------------------------


# Strategy producing strings that are NOT in the accepted status set.
# We exclude the three valid lowercase strings to keep the property
# focussed on rejection.
_invalid_status_strings = st.text().filter(
    lambda s: s not in {"all", "active", "historical"}
)


class TestPropertyRejectsArbitraryStrings:
    """For any string outside the accepted set, the handler rejects."""

    @settings(
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        max_examples=50,
        deadline=None,
    )
    @given(bad=_invalid_status_strings)
    def test_rejects_any_string_outside_accepted_set(self, fake_state, bad):
        result = main.handle_list_captures({"status": bad})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_state.calls == []


class TestPropertyShapeInvariant:
    """Every response satisfies the envelope shape (Property 10)."""

    @settings(
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        max_examples=50,
        deadline=None,
    )
    @given(
        status=st.sampled_from(["all", "active", "historical"]),
        n_rows=st.integers(min_value=0, max_value=5),
    )
    def test_response_envelope_shape(self, fake_state, status, n_rows):
        rows = [
            _row(
                capture_id=f"cap-{i:03d}",
                start_time=f"2026-04-20T{12 + i:02d}:00:00+00:00",
            )
            for i in range(n_rows)
        ]
        fake_state.rows_by_filter[status] = rows

        result = main.handle_list_captures({"status": status})

        # Envelope keys.
        assert result["domain"] == "network"
        assert isinstance(result["success"], bool)
        assert isinstance(result["data"], dict)
        assert isinstance(result["formattedText"], str)
        assert isinstance(result["metadata"], dict)
        # Metadata keys.
        assert result["metadata"]["sourceApi"] == "dynamodb:Query"
        assert result["metadata"]["dataFreshness"] == "real-time"
        # Successful path: data carries the expected shape.
        assert result["success"] is True
        assert result["data"]["count"] == n_rows
        assert len(result["data"]["captures"]) == n_rows
        # Each projected row carries exactly the documented field set.
        for projected in result["data"]["captures"]:
            assert set(projected.keys()) == {
                "capture_id",
                "eni_ids",
                "start_time",
                "deadline",
                "status",
                "stopped_reason",
                "mirror_session_ids",
            }
