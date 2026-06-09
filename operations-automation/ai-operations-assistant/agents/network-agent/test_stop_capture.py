"""
Unit and property-based tests for ``handle_stop_capture`` (Task 8, Reqs 3.7, 3.8).

Exercises the best-effort cleanup path documented in the design's
"Capture Lifecycle Handlers > stop_capture" section and the EH-3
partial-failure rules:

- Validates ``capture_id`` and surfaces ``invalid_parameter`` when it
  fails the Capture_Id_Format regex.
- Returns ``not_found`` when the Capture_State_Table has no row for
  the supplied ``capture_id``.
- Returns ``state_conflict`` when the row already exists but its
  ``status`` is ``stopped``.
- Sequentially deletes every Traffic Mirror session, the VNI lookup
  rows, and the Auto_Stop_Schedule, treating "already deleted"
  AWS errors as benign.
- Updates the row to ``status=stopped`` only when every step
  succeeded; otherwise to ``status=stopping_failed`` with
  ``stopped_reason=partial_cleanup_<step>`` where ``<step>`` is the
  *first* step that failed.
- Sets ``metadata.sourceApi = "ec2:DeleteTrafficMirrorSession"``.

Tests use small in-memory fakes for EC2 + Scheduler + the ``state``
module so the full handler path runs without AWS or ``moto``.

Run from the ``network-agent`` directory:

    python -m pytest test_stop_capture.py -v
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import pytest
from botocore.exceptions import ClientError, BotoCoreError
from hypothesis import HealthCheck, given, settings, strategies as st

import main
import state
from validation import ValidationError


# ---------------------------------------------------------------------------
# Fake EC2 client
# ---------------------------------------------------------------------------


class _FakeEC2:
    """In-memory EC2 stand-in supporting only ``delete_traffic_mirror_session``.

    Tests can pre-load ``raise_on_session_id`` to make a specific
    session ID raise a supplied exception (typically a
    ``ClientError`` whose code is either benign or non-benign per the
    design's table). All other session IDs are simply marked deleted.
    """

    def __init__(self) -> None:
        self.deleted: List[str] = []
        # Map session_id -> Exception to raise on attempted delete.
        self.raise_on_session_id: Dict[str, Exception] = {}

    def delete_traffic_mirror_session(
        self, TrafficMirrorSessionId: str
    ) -> dict:
        if TrafficMirrorSessionId in self.raise_on_session_id:
            raise self.raise_on_session_id[TrafficMirrorSessionId]
        self.deleted.append(TrafficMirrorSessionId)
        return {"TrafficMirrorSessionId": TrafficMirrorSessionId}


# ---------------------------------------------------------------------------
# Fake Scheduler client
# ---------------------------------------------------------------------------


class _FakeScheduler:
    """In-memory EventBridge Scheduler stand-in supporting ``delete_schedule``."""

    def __init__(self) -> None:
        self.deleted: List[Dict[str, str]] = []
        self.raise_on_next: Optional[Exception] = None

    def delete_schedule(self, Name: str, GroupName: str) -> dict:
        if self.raise_on_next is not None:
            exc = self.raise_on_next
            self.raise_on_next = None
            raise exc
        self.deleted.append({"Name": Name, "GroupName": GroupName})
        return {}


# ---------------------------------------------------------------------------
# Fake state-module surface
#
# The ``state`` module exposes free functions backed by DynamoDB. We
# patch ``state.get_capture``, ``state.update_capture_status``, and
# ``state.delete_vni_lookup_for_capture`` per-test using the fixture
# below so the handler exercises real code without DynamoDB.
# ---------------------------------------------------------------------------


class _FakeState:
    """In-memory recorder for the three ``state`` functions stop_capture calls.

    The fixture installs lambdas that delegate to this object's
    methods so tests can pre-load row data, register exceptions, and
    inspect the post-call state.
    """

    def __init__(self) -> None:
        # capture_id -> row dict (None means "absent")
        self.rows: Dict[str, Optional[dict]] = {}
        # capture_id -> exception to raise when get_capture is called
        self.get_raises: Dict[str, Exception] = {}
        # capture_id -> exception to raise on delete_vni_lookup_for_capture
        self.vni_delete_raises: Dict[str, Exception] = {}
        # capture_id -> rows-deleted count
        self.vni_delete_counts: Dict[str, int] = {}
        # capture_id -> exception to raise on update_capture_status
        self.update_raises: Dict[str, Exception] = {}
        # Recorded calls
        self.update_calls: List[Dict[str, Optional[str]]] = []
        self.vni_delete_calls: List[str] = []

    def get_capture(self, capture_id: str) -> Optional[dict]:
        if capture_id in self.get_raises:
            raise self.get_raises[capture_id]
        return self.rows.get(capture_id)

    def update_capture_status(
        self,
        capture_id: str,
        status: str,
        stopped_reason: Optional[str] = None,
    ) -> dict:
        if capture_id in self.update_raises:
            raise self.update_raises[capture_id]
        self.update_calls.append(
            {
                "capture_id": capture_id,
                "status": status,
                "stopped_reason": stopped_reason,
            }
        )
        # Reflect the change in our recorded row so a follow-up
        # get_capture call would see it (used by idempotency tests).
        existing = self.rows.get(capture_id) or {}
        new_row = dict(existing)
        new_row["status"] = status
        if stopped_reason is not None:
            new_row["stopped_reason"] = stopped_reason
        self.rows[capture_id] = new_row
        return {"Attributes": new_row}

    def delete_vni_lookup_for_capture(self, capture_id: str) -> int:
        self.vni_delete_calls.append(capture_id)
        if capture_id in self.vni_delete_raises:
            raise self.vni_delete_raises[capture_id]
        return self.vni_delete_counts.get(capture_id, 0)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_main_singletons(monkeypatch):
    """Reset main.py module-level boto3 client caches between tests."""
    monkeypatch.setattr(main, "_ec2_client", None)
    monkeypatch.setattr(main, "_scheduler_client", None)
    yield
    monkeypatch.setattr(main, "_ec2_client", None)
    monkeypatch.setattr(main, "_scheduler_client", None)


@pytest.fixture
def fake_ec2(monkeypatch) -> _FakeEC2:
    """Install a fresh ``_FakeEC2`` as ``main._ec2_client``."""
    fake = _FakeEC2()
    monkeypatch.setattr(main, "_ec2_client", fake)
    return fake


@pytest.fixture
def fake_scheduler(monkeypatch) -> _FakeScheduler:
    """Install a fresh ``_FakeScheduler`` as ``main._scheduler_client``."""
    fake = _FakeScheduler()
    monkeypatch.setattr(main, "_scheduler_client", fake)
    return fake


@pytest.fixture
def fake_state(monkeypatch) -> _FakeState:
    """Patch the three ``state`` functions stop_capture calls."""
    fake = _FakeState()
    monkeypatch.setattr(state, "get_capture", fake.get_capture)
    monkeypatch.setattr(
        state, "update_capture_status", fake.update_capture_status
    )
    monkeypatch.setattr(
        state, "delete_vni_lookup_for_capture", fake.delete_vni_lookup_for_capture
    )
    return fake


@pytest.fixture
def schedule_env(monkeypatch):
    """Set the SCHEDULE_GROUP_NAME env var so the schedule-delete path runs."""
    monkeypatch.setenv("SCHEDULE_GROUP_NAME", "goat-network-test-group")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    capture_id: str = "cap-test-001",
    *,
    status: str = "active",
    mirror_session_ids: Optional[List[str]] = None,
    eni_ids: Optional[List[str]] = None,
) -> dict:
    """Build a minimal Capture_State_Table row dict for tests."""
    return {
        "capture_id": capture_id,
        "status": status,
        "mirror_session_ids": mirror_session_ids
        if mirror_session_ids is not None
        else ["tms-1111", "tms-2222"],
        "eni_ids": eni_ids if eni_ids is not None else ["eni-aaaaaaaa"],
        "duration_minutes": 15,
        "start_time": "2026-01-01T12:00:00+00:00",
        "deadline": "2026-01-01T12:15:00+00:00",
    }


def _benign_session_not_found(session_id: str) -> ClientError:
    return ClientError(
        error_response={
            "Error": {
                "Code": "InvalidTrafficMirrorSessionId.NotFound",
                "Message": f"The Traffic Mirror Session '{session_id}' does not exist",
            }
        },
        operation_name="DeleteTrafficMirrorSession",
    )


def _benign_schedule_not_found() -> ClientError:
    return ClientError(
        error_response={
            "Error": {
                "Code": "ResourceNotFoundException",
                "Message": "Schedule not found",
            }
        },
        operation_name="DeleteSchedule",
    )


def _non_benign_aws_error(code: str = "AccessDeniedException") -> ClientError:
    return ClientError(
        error_response={
            "Error": {"Code": code, "Message": f"{code} on operation"},
        },
        operation_name="DeleteTrafficMirrorSession",
    )


# ---------------------------------------------------------------------------
# Caller-fault paths (validation, lookup)
# ---------------------------------------------------------------------------


class TestValidation:
    """Caller-fault response envelopes for invalid parameters."""

    def test_missing_capture_id_returns_invalid_parameter(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        result = main.handle_stop_capture({})

        assert result["success"] is False
        assert result["domain"] == "network"
        assert result["metadata"]["errorCategory"] == "invalid_parameter"
        # Source API is the primary AWS surface for the action even on
        # caller-fault rejections so the orchestration agent can
        # attribute the error correctly.
        assert (
            result["metadata"]["sourceApi"] == "ec2:DeleteTrafficMirrorSession"
        )
        # Nothing was deleted because validation failed before any AWS call.
        assert fake_ec2.deleted == []
        assert fake_scheduler.deleted == []
        assert fake_state.update_calls == []

    def test_empty_capture_id_returns_invalid_parameter(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        result = main.handle_stop_capture({"capture_id": ""})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "invalid_parameter"

    def test_capture_id_with_invalid_chars_returns_invalid_parameter(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        result = main.handle_stop_capture({"capture_id": "cap with space"})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_ec2.deleted == []

    def test_capture_id_too_long_returns_invalid_parameter(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        result = main.handle_stop_capture({"capture_id": "a" * 129})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "invalid_parameter"

    def test_non_dict_params_treated_as_empty(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        # The handler tolerates non-dict params (treats them as {}),
        # then surfaces the missing-capture_id validation error.
        result = main.handle_stop_capture(None)

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "invalid_parameter"


class TestNotFound:
    """Req 3.8: unknown capture_id returns ``not_found``."""

    def test_returns_not_found_envelope_when_row_missing(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        # No row is stored in fake_state.
        result = main.handle_stop_capture({"capture_id": "cap-missing"})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "not_found"
        assert "cap-missing" in result["error"]
        # Nothing was deleted.
        assert fake_ec2.deleted == []
        assert fake_scheduler.deleted == []
        assert fake_state.update_calls == []
        assert fake_state.vni_delete_calls == []


class TestStateConflict:
    """Req 3.8: an already-stopped row returns ``state_conflict``."""

    def test_already_stopped_returns_state_conflict(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        fake_state.rows["cap-already-stopped"] = _make_row(
            "cap-already-stopped", status="stopped"
        )

        result = main.handle_stop_capture({"capture_id": "cap-already-stopped"})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "state_conflict"
        assert "already stopped" in result["formattedText"].lower()
        # Idempotency: no AWS calls, no DynamoDB writes happen for a
        # row that is already stopped.
        assert fake_ec2.deleted == []
        assert fake_scheduler.deleted == []
        assert fake_state.update_calls == []
        assert fake_state.vni_delete_calls == []


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    """All cleanup steps succeed; row updated to ``status=stopped``."""

    def test_full_cleanup_marks_row_stopped(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        fake_state.rows["cap-001"] = _make_row(
            "cap-001",
            mirror_session_ids=["tms-1111", "tms-2222", "tms-3333"],
        )
        fake_state.vni_delete_counts["cap-001"] = 3

        result = main.handle_stop_capture({"capture_id": "cap-001"})

        assert result["success"] is True
        assert result["domain"] == "network"
        assert result["data"]["status"] == "stopped"
        assert result["data"]["capture_id"] == "cap-001"
        assert result["data"]["mirror_session_ids"] == [
            "tms-1111",
            "tms-2222",
            "tms-3333",
        ]
        assert (
            result["metadata"]["sourceApi"] == "ec2:DeleteTrafficMirrorSession"
        )
        # Every Traffic Mirror session was deleted.
        assert fake_ec2.deleted == ["tms-1111", "tms-2222", "tms-3333"]
        # The VNI lookup rows were deleted exactly once.
        assert fake_state.vni_delete_calls == ["cap-001"]
        # The Auto_Stop_Schedule was deleted.
        assert fake_scheduler.deleted == [
            {"Name": "cap-001", "GroupName": "goat-network-test-group"}
        ]
        # The status was updated to ``stopped`` with no stopped_reason.
        assert fake_state.update_calls == [
            {
                "capture_id": "cap-001",
                "status": "stopped",
                "stopped_reason": None,
            }
        ]

    def test_cleanup_with_no_mirror_sessions(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        # An empty mirror_session_ids list still updates the row to
        # ``stopped`` and deletes the schedule + VNI rows.
        fake_state.rows["cap-empty"] = _make_row(
            "cap-empty", mirror_session_ids=[]
        )

        result = main.handle_stop_capture({"capture_id": "cap-empty"})

        assert result["success"] is True
        assert result["data"]["status"] == "stopped"
        assert fake_ec2.deleted == []
        assert fake_state.update_calls[0]["status"] == "stopped"

    def test_cleanup_when_schedule_group_unset(
        self, fake_ec2, fake_scheduler, fake_state, monkeypatch
    ):
        # When SCHEDULE_GROUP_NAME is unset, ``start_capture`` could
        # not arm the schedule and there is nothing to delete. The
        # handler should still mark the row stopped without raising.
        monkeypatch.delenv("SCHEDULE_GROUP_NAME", raising=False)
        fake_state.rows["cap-no-sched"] = _make_row("cap-no-sched")

        result = main.handle_stop_capture({"capture_id": "cap-no-sched"})

        assert result["success"] is True
        assert result["data"]["status"] == "stopped"
        # The scheduler client was never called.
        assert fake_scheduler.deleted == []


# ---------------------------------------------------------------------------
# Benign "already deleted" handling
# ---------------------------------------------------------------------------


class TestBenignAlreadyDeleted:
    """AWS "already deleted" errors are treated as benign per the design."""

    def test_benign_traffic_mirror_session_not_found_treated_as_success(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        fake_state.rows["cap-tm-gone"] = _make_row(
            "cap-tm-gone",
            mirror_session_ids=["tms-stale", "tms-real"],
        )
        # tms-stale reports already-deleted; tms-real deletes normally.
        fake_ec2.raise_on_session_id["tms-stale"] = (
            _benign_session_not_found("tms-stale")
        )

        result = main.handle_stop_capture({"capture_id": "cap-tm-gone"})

        assert result["success"] is True
        assert result["data"]["status"] == "stopped"
        # tms-real was deleted; tms-stale's benign error did not block
        # the rest of the cleanup, so no failure was recorded.
        assert fake_ec2.deleted == ["tms-real"]
        assert fake_state.update_calls[0] == {
            "capture_id": "cap-tm-gone",
            "status": "stopped",
            "stopped_reason": None,
        }

    def test_benign_schedule_not_found_treated_as_success(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        # The schedule self-deleted via ActionAfterCompletion=DELETE
        # immediately before the user-initiated stop. The handler
        # should treat the ResourceNotFoundException as benign.
        fake_state.rows["cap-sched-gone"] = _make_row("cap-sched-gone")
        fake_scheduler.raise_on_next = _benign_schedule_not_found()

        result = main.handle_stop_capture({"capture_id": "cap-sched-gone"})

        assert result["success"] is True
        assert result["data"]["status"] == "stopped"
        # The status was still updated to stopped because the benign
        # error did not count as a failure.
        assert fake_state.update_calls[0]["status"] == "stopped"


# ---------------------------------------------------------------------------
# Partial-cleanup paths
# ---------------------------------------------------------------------------


class TestPartialCleanup:
    """Non-benign deletion errors continue past failures and mark the row."""

    def test_mirror_session_delete_failure_marks_partial_cleanup(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        fake_state.rows["cap-tm-fail"] = _make_row(
            "cap-tm-fail",
            mirror_session_ids=["tms-good", "tms-bad"],
        )
        fake_ec2.raise_on_session_id["tms-bad"] = _non_benign_aws_error()
        fake_state.vni_delete_counts["cap-tm-fail"] = 2

        result = main.handle_stop_capture({"capture_id": "cap-tm-fail"})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "partial_cleanup"
        assert result["data"]["status"] == "stopping_failed"
        assert result["data"]["failed_step"] == "mirror_sessions"
        assert (
            result["data"]["stopped_reason"] == "partial_cleanup_mirror_sessions"
        )
        # The handler continued past the failure: tms-good was still
        # deleted, the VNI rows were still cleaned, and the schedule
        # was still deleted.
        assert fake_ec2.deleted == ["tms-good"]
        assert fake_state.vni_delete_calls == ["cap-tm-fail"]
        assert fake_scheduler.deleted == [
            {"Name": "cap-tm-fail", "GroupName": "goat-network-test-group"}
        ]
        # The row was marked stopping_failed with the step-specific
        # stopped_reason.
        assert fake_state.update_calls == [
            {
                "capture_id": "cap-tm-fail",
                "status": "stopping_failed",
                "stopped_reason": "partial_cleanup_mirror_sessions",
            }
        ]

    def test_vni_lookup_delete_failure_marks_partial_cleanup(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        fake_state.rows["cap-vni-fail"] = _make_row("cap-vni-fail")
        fake_state.vni_delete_raises["cap-vni-fail"] = ClientError(
            error_response={
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "rate-limited",
                }
            },
            operation_name="Query",
        )

        result = main.handle_stop_capture({"capture_id": "cap-vni-fail"})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "partial_cleanup"
        assert result["data"]["failed_step"] == "vni_lookup"
        assert result["data"]["stopped_reason"] == "partial_cleanup_vni_lookup"
        # The schedule was still deleted (cleanup continues).
        assert fake_scheduler.deleted == [
            {"Name": "cap-vni-fail", "GroupName": "goat-network-test-group"}
        ]

    def test_schedule_delete_failure_marks_partial_cleanup(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        fake_state.rows["cap-sched-fail"] = _make_row("cap-sched-fail")
        # A non-benign AWS error from DeleteSchedule (e.g. throttling).
        fake_scheduler.raise_on_next = ClientError(
            error_response={
                "Error": {
                    "Code": "ThrottlingException",
                    "Message": "rate-limited",
                }
            },
            operation_name="DeleteSchedule",
        )

        result = main.handle_stop_capture({"capture_id": "cap-sched-fail"})

        assert result["success"] is False
        assert result["data"]["failed_step"] == "auto_stop_schedule"
        assert (
            result["data"]["stopped_reason"]
            == "partial_cleanup_auto_stop_schedule"
        )

    def test_first_failing_step_wins_in_stopped_reason(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        # Both mirror_sessions and vni_lookup fail. The row's
        # stopped_reason should reference the *first* failing step
        # (mirror_sessions), matching the "first failing step wins"
        # convention used by start_capture's rollback path.
        fake_state.rows["cap-multi-fail"] = _make_row(
            "cap-multi-fail",
            mirror_session_ids=["tms-bad"],
        )
        fake_ec2.raise_on_session_id["tms-bad"] = _non_benign_aws_error()
        fake_state.vni_delete_raises["cap-multi-fail"] = ClientError(
            error_response={
                "Error": {"Code": "ThrottlingException", "Message": "rate"}
            },
            operation_name="Query",
        )

        result = main.handle_stop_capture({"capture_id": "cap-multi-fail"})

        assert result["success"] is False
        assert result["data"]["failed_step"] == "mirror_sessions"
        assert (
            result["data"]["stopped_reason"]
            == "partial_cleanup_mirror_sessions"
        )

    def test_botocore_error_treated_as_non_benign(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        # ``BotoCoreError`` (not a ``ClientError``) is never benign —
        # it always counts as a failed step.
        fake_state.rows["cap-botocore"] = _make_row(
            "cap-botocore", mirror_session_ids=["tms-x"]
        )
        fake_ec2.raise_on_session_id["tms-x"] = BotoCoreError()

        result = main.handle_stop_capture({"capture_id": "cap-botocore"})

        assert result["success"] is False
        assert result["data"]["failed_step"] == "mirror_sessions"


# ---------------------------------------------------------------------------
# Idempotency: running stop_capture twice is safe
# ---------------------------------------------------------------------------


class TestIdempotency:
    """A second stop_capture call returns the documented state_conflict."""

    def test_second_call_returns_state_conflict(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        fake_state.rows["cap-idemp"] = _make_row("cap-idemp")

        first = main.handle_stop_capture({"capture_id": "cap-idemp"})
        assert first["success"] is True
        assert first["data"]["status"] == "stopped"

        # The fake state's update_capture_status updated the row
        # in-place, so the next get_capture sees status=stopped and
        # the handler short-circuits with state_conflict.
        second = main.handle_stop_capture({"capture_id": "cap-idemp"})
        assert second["success"] is False
        assert second["metadata"]["errorCategory"] == "state_conflict"
        # No additional EC2 / scheduler calls on the second invocation.
        assert fake_ec2.deleted == ["tms-1111", "tms-2222"]
        assert len(fake_scheduler.deleted) == 1


# ---------------------------------------------------------------------------
# Infrastructure-error paths (DynamoDB GetItem / UpdateItem failures)
# ---------------------------------------------------------------------------


class TestInfrastructureErrors:
    """Errors from the lookup or final update surface as AWS-error envelopes."""

    def test_get_capture_aws_error_returns_aws_error_envelope(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        fake_state.get_raises["cap-getfail"] = ClientError(
            error_response={
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "throttled",
                }
            },
            operation_name="GetItem",
        )

        result = main.handle_stop_capture({"capture_id": "cap-getfail"})

        assert result["success"] is False
        # The error category should be one of the AWS-error codes
        # produced by ``_classify_aws_error`` (throttling -> aws_throttled).
        assert result["metadata"]["errorCategory"] in (
            "aws_throttled",
            "aws_other",
        )
        # No cleanup happened because we never read the row.
        assert fake_ec2.deleted == []
        assert fake_scheduler.deleted == []

    def test_update_capture_status_aws_error_surfaces(
        self, fake_ec2, fake_scheduler, fake_state, schedule_env
    ):
        fake_state.rows["cap-updatefail"] = _make_row("cap-updatefail")
        fake_state.update_raises["cap-updatefail"] = ClientError(
            error_response={
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "throttled",
                }
            },
            operation_name="UpdateItem",
        )

        result = main.handle_stop_capture({"capture_id": "cap-updatefail"})

        assert result["success"] is False
        assert "dynamodb:UpdateItem" in result["error"]


# ---------------------------------------------------------------------------
# Property-based tests
#
# Property: For any non-empty list of distinct mirror-session IDs whose
# delete operations all succeed (or fail benignly), the handler always
# returns success=True with status=stopped, exactly one update_capture
# call to ``stopped``, and at most one VNI-delete call.
# ---------------------------------------------------------------------------


_session_id_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="-",
    ),
    min_size=4,
    max_size=20,
)


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    session_ids=st.lists(
        _session_id_strategy, min_size=0, max_size=5, unique=True
    ),
    benign_count=st.integers(min_value=0, max_value=5),
)
def test_property_full_or_benign_cleanup_always_succeeds(
    monkeypatch, session_ids, benign_count
):
    """**Validates: Requirements 3.7, 3.8**

    For any combination of (a) zero or more session IDs and (b) zero
    or more of those sessions reporting benign "already deleted"
    errors, the handler returns ``success=True`` with
    ``status=stopped`` and updates the row exactly once.

    This property captures the design's "best-effort sequential
    deletion ... benign 'already deleted' errors are treated as
    success" rule from the EH-3 commentary.
    """
    # Reset the per-test fakes manually because ``@given`` cannot
    # share pytest fixtures cleanly with module-level test functions.
    monkeypatch.setattr(main, "_ec2_client", None)
    monkeypatch.setattr(main, "_scheduler_client", None)
    monkeypatch.setenv("SCHEDULE_GROUP_NAME", "goat-network-test-group")

    fake_ec2 = _FakeEC2()
    monkeypatch.setattr(main, "_ec2_client", fake_ec2)
    fake_scheduler = _FakeScheduler()
    monkeypatch.setattr(main, "_scheduler_client", fake_scheduler)
    fake_state = _FakeState()
    monkeypatch.setattr(state, "get_capture", fake_state.get_capture)
    monkeypatch.setattr(
        state, "update_capture_status", fake_state.update_capture_status
    )
    monkeypatch.setattr(
        state,
        "delete_vni_lookup_for_capture",
        fake_state.delete_vni_lookup_for_capture,
    )

    capture_id = "cap-prop"
    fake_state.rows[capture_id] = _make_row(
        capture_id, mirror_session_ids=list(session_ids)
    )
    # Mark the first ``benign_count`` sessions as already deleted.
    for sid in session_ids[: min(benign_count, len(session_ids))]:
        fake_ec2.raise_on_session_id[sid] = _benign_session_not_found(sid)

    result = main.handle_stop_capture({"capture_id": capture_id})

    # The handler always succeeds because every error path is benign.
    assert result["success"] is True
    assert result["data"]["status"] == "stopped"
    assert result["metadata"]["sourceApi"] == "ec2:DeleteTrafficMirrorSession"

    # The status update happened exactly once with no stopped_reason.
    assert len(fake_state.update_calls) == 1
    assert fake_state.update_calls[0] == {
        "capture_id": capture_id,
        "status": "stopped",
        "stopped_reason": None,
    }

    # The VNI lookup delete was attempted exactly once (idempotent).
    assert fake_state.vni_delete_calls == [capture_id]


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(invalid_id=st.text(min_size=0, max_size=200))
def test_property_invalid_capture_id_never_calls_aws(monkeypatch, invalid_id):
    """**Validates: Requirements 3.7**

    For any string that fails ``validate_capture_id``, the handler
    must never call any AWS API. This protects against accidentally
    leaking caller-supplied input into AWS calls.
    """
    # Use the deployed validator itself to decide whether the input
    # is "invalid" — matching whatever shape rules the production
    # ``validate_capture_id`` enforces. The property under test is
    # exclusively about invalid inputs; valid ones are skipped via
    # hypothesis.assume so the property statement stays clean.
    from hypothesis import assume
    from validation import validate_capture_id

    try:
        validate_capture_id(invalid_id)
    except ValidationError:
        pass  # Confirmed invalid — proceed.
    else:
        assume(False)  # Valid input — outside the scope of this property.

    monkeypatch.setattr(main, "_ec2_client", None)
    monkeypatch.setattr(main, "_scheduler_client", None)
    monkeypatch.setenv("SCHEDULE_GROUP_NAME", "goat-network-test-group")
    fake_ec2 = _FakeEC2()
    monkeypatch.setattr(main, "_ec2_client", fake_ec2)
    fake_scheduler = _FakeScheduler()
    monkeypatch.setattr(main, "_scheduler_client", fake_scheduler)
    fake_state = _FakeState()
    monkeypatch.setattr(state, "get_capture", fake_state.get_capture)
    monkeypatch.setattr(
        state, "update_capture_status", fake_state.update_capture_status
    )
    monkeypatch.setattr(
        state,
        "delete_vni_lookup_for_capture",
        fake_state.delete_vni_lookup_for_capture,
    )

    result = main.handle_stop_capture({"capture_id": invalid_id})

    assert result["success"] is False
    assert result["metadata"]["errorCategory"] == "invalid_parameter"
    # No AWS calls were made.
    assert fake_ec2.deleted == []
    assert fake_scheduler.deleted == []
    assert fake_state.update_calls == []
    assert fake_state.vni_delete_calls == []
