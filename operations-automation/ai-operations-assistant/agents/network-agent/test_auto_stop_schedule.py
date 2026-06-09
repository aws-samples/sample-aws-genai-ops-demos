"""
Unit and property-based tests for the Auto_Stop_Schedule integration
(Task 11, Reqs 3.5, 4.6, 4.7, 4.10).

Exercises the EventBridge Scheduler integration documented in the
design's "Capture Lifecycle Handlers > start_capture" step 10 and the
"StopCaptureInvokerLambda" component section:

- ``_create_auto_stop_schedule`` builds the correct ``CreateSchedule``
  arguments: ``Name=capture_id``, ``GroupName=SCHEDULE_GROUP_NAME``,
  ``ScheduleExpression="at(<deadline>)"`` in UTC,
  ``FlexibleTimeWindow={"Mode": "OFF"}``, ``ActionAfterCompletion="DELETE"``,
  ``Target.Arn=STOP_CAPTURE_INVOKER_LAMBDA_ARN``,
  ``Target.RoleArn=SCHEDULER_TARGET_ROLE_ARN``,
  ``Target.Input='{"capture_id": "<id>"}'`` (raw JSON, no agent envelope).
- Missing environment variables result in ``False`` and *no* AWS call.
- A non-benign AWS error during ``CreateSchedule`` results in ``False``.
- ``handle_start_capture`` integrates with the helper: a successful
  schedule create produces ``data.auto_stop_schedule_armed=True``;
  a failure produces ``data.auto_stop_schedule_armed=False`` and
  the persisted Capture_State_Table row is patched accordingly per
  the design's EH-3 step 10 commentary.
- ``handle_stop_capture`` deletes the named schedule and treats the
  benign ``ResourceNotFoundException`` as a success — covered by
  ``test_stop_capture.py`` already; this file does not duplicate.

Tests use small in-memory fakes for EC2 + Scheduler + the ``state``
module so the full handler path runs without AWS or ``moto``. The
property-based tests validate the design's "Property 8: Auto_Stop_Schedule
fires within 60 seconds of deadline" claim by asserting that the
``at()`` expression encodes the exact deadline at second precision —
EventBridge Scheduler's documented delivery latency closes the gap.

Run from the ``network-agent`` directory:

    python -m pytest test_auto_stop_schedule.py -v
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest
from botocore.exceptions import BotoCoreError, ClientError
from hypothesis import HealthCheck, given, settings, strategies as st

import main
import state


# ---------------------------------------------------------------------------
# Test environment values
# ---------------------------------------------------------------------------

_TEST_INVOKER_LAMBDA_ARN = (
    "arn:aws:lambda:us-east-1:123456789012:function:goat-network-stop-capture-invoker"
)
_TEST_SCHEDULE_GROUP_NAME = "goat-network-test-group"
_TEST_SCHEDULER_TARGET_ROLE_ARN = (
    "arn:aws:iam::123456789012:role/goat-network-scheduler-target-role"
)
_TEST_FILTER_ID = "tmf-test-1234"
_TEST_TARGET_ID = "tmt-test-5678"
_TEST_COLLECTOR_INSTANCE_ID = "i-test-collector"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeScheduler:
    """In-memory EventBridge Scheduler stand-in.

    Records every ``create_schedule`` and ``delete_schedule`` call with
    the full kwargs set so tests can assert on the wire arguments.
    A pre-loaded ``raise_on_create`` exception is raised on the next
    ``create_schedule`` call (one-shot).
    """

    def __init__(self) -> None:
        self.created: List[Dict[str, Any]] = []
        self.deleted: List[Dict[str, str]] = []
        self.raise_on_create: Optional[Exception] = None

    def create_schedule(self, **kwargs: Any) -> dict:
        if self.raise_on_create is not None:
            exc = self.raise_on_create
            self.raise_on_create = None
            raise exc
        self.created.append(kwargs)
        return {
            "ScheduleArn": (
                f"arn:aws:scheduler:us-east-1:123456789012:schedule/"
                f"{kwargs.get('GroupName', 'default')}/{kwargs.get('Name', '')}"
            )
        }

    def delete_schedule(self, Name: str, GroupName: str) -> dict:
        self.deleted.append({"Name": Name, "GroupName": GroupName})
        return {}


class _FakeEC2:
    """In-memory EC2 stand-in for the full ``handle_start_capture`` path.

    Implements the four EC2 methods ``handle_start_capture`` calls:
    ``describe_network_interfaces`` (opt-in tag), ``describe_instances``
    (parent instance tag + collector readiness), ``describe_instance_status``
    (collector status checks), and ``create_traffic_mirror_session``.
    """

    def __init__(self) -> None:
        self.eni_records: Dict[str, dict] = {}
        self.instance_records: Dict[str, dict] = {}
        self.collector_instance_id: Optional[str] = None
        self.created_mirror_sessions: List[Dict[str, Any]] = []
        self.deleted_mirror_sessions: List[str] = []
        self._next_vni = 100

    # -- Setup helpers (used by tests) -----------------------------------

    def add_eni_with_opt_in(self, eni_id: str, instance_id: Optional[str] = None) -> None:
        record: Dict[str, Any] = {
            "NetworkInterfaceId": eni_id,
            "TagSet": [
                {"Key": "goat-network-capture-allowed", "Value": "true"},
            ],
        }
        if instance_id is not None:
            record["Attachment"] = {
                "InstanceId": instance_id,
                "Status": "attached",
            }
        self.eni_records[eni_id] = record

    def set_collector(self, instance_id: str) -> None:
        self.collector_instance_id = instance_id

    # -- AWS API surface -------------------------------------------------

    def describe_network_interfaces(self, NetworkInterfaceIds=None, **_kw):  # noqa: N803
        if NetworkInterfaceIds is None:
            return {"NetworkInterfaces": list(self.eni_records.values())}
        return {
            "NetworkInterfaces": [
                self.eni_records[eid]
                for eid in NetworkInterfaceIds
                if eid in self.eni_records
            ]
        }

    def describe_instances(self, InstanceIds=None, **_kw):  # noqa: N803
        # ``handle_start_capture`` calls describe_instances both for
        # the opt-in tag check (ENI's parent instance) and for the
        # collector readiness check. Covering both with a single fake
        # keeps the test simple.
        ids = InstanceIds or []
        instances = []
        for iid in ids:
            if iid == self.collector_instance_id:
                instances.append(
                    {
                        "InstanceId": iid,
                        "State": {"Name": "running"},
                        "Tags": [],
                    }
                )
            else:
                instances.append(
                    self.instance_records.get(
                        iid, {"InstanceId": iid, "Tags": []}
                    )
                )
        return {"Reservations": [{"Instances": instances}]}

    def describe_instance_status(self, **_kw):
        # Mark the collector as healthy for readiness checks.
        return {
            "InstanceStatuses": [
                {
                    "InstanceId": self.collector_instance_id or "i-unknown",
                    "InstanceState": {"Name": "running"},
                    "SystemStatus": {"Status": "ok"},
                    "InstanceStatus": {"Status": "ok"},
                }
            ]
        }

    def create_traffic_mirror_session(self, **kwargs: Any) -> dict:
        vni = self._next_vni
        self._next_vni += 1
        session_id = f"tms-{kwargs['NetworkInterfaceId']}-{vni}"
        record = {
            "NetworkInterfaceId": kwargs["NetworkInterfaceId"],
            "TrafficMirrorSessionId": session_id,
            "VirtualNetworkId": vni,
            "TrafficMirrorTargetId": kwargs["TrafficMirrorTargetId"],
            "TrafficMirrorFilterId": kwargs["TrafficMirrorFilterId"],
            "SessionNumber": kwargs.get("SessionNumber"),
            "Description": kwargs.get("Description", ""),
        }
        self.created_mirror_sessions.append(record)
        return {"TrafficMirrorSession": record}

    def delete_traffic_mirror_session(
        self, TrafficMirrorSessionId: str  # noqa: N803
    ) -> dict:
        self.deleted_mirror_sessions.append(TrafficMirrorSessionId)
        return {}


class _FakeStateModule:
    """Patch surface for the ``state`` module functions ``handle_start_capture`` calls.

    We only need a small subset for the schedule path:

    * ``query_active_captures`` — returns ``[]`` so the concurrency
      check passes.
    * ``find_idempotent_capture`` — returns ``None`` so we never take
      the cached path (idempotency is exercised in dedicated tests).
    * ``put_vni_lookup_rows`` — records the rows.
    * ``put_capture`` — records the row in an in-memory dict so the
      auto_stop_schedule_armed patch can update it.
    * ``_capture_table()`` — returns an object exposing the
      ``update_item`` method the patch path uses, mutating the
      in-memory row so the test can assert on the post-patch state.
    """

    def __init__(self) -> None:
        self.rows: Dict[str, dict] = {}
        self.vni_rows: List[dict] = []
        self.put_capture_calls: List[dict] = []
        self.update_item_calls: List[dict] = []

    # -- query / find ----------------------------------------------------

    def query_active_captures(self) -> List[dict]:
        return [r for r in self.rows.values() if r.get("status") == "active"]

    def find_idempotent_capture(
        self, token: str, eni_ids: list, duration_minutes: int
    ) -> Optional[dict]:
        return None

    # -- writes ----------------------------------------------------------

    def put_vni_lookup_rows(self, rows: list) -> None:
        self.vni_rows.extend(rows)

    def put_capture(self, item: dict) -> dict:
        self.put_capture_calls.append(dict(item))
        self.rows[item["capture_id"]] = dict(item)
        return {"Attributes": item}

    def update_capture_status(self, capture_id, status, stopped_reason=None):
        # Not used in the start_capture path but stub to avoid AttributeError.
        if capture_id in self.rows:
            self.rows[capture_id]["status"] = status
            if stopped_reason is not None:
                self.rows[capture_id]["stopped_reason"] = stopped_reason

    def delete_vni_lookup_for_capture(self, capture_id: str) -> int:
        before = len(self.vni_rows)
        self.vni_rows = [r for r in self.vni_rows if r.get("capture_id") != capture_id]
        return before - len(self.vni_rows)

    # -- _capture_table() surface for the auto_stop_schedule_armed patch -

    def make_table(self):
        rows = self.rows
        update_calls = self.update_item_calls

        class _FakeTable:
            def update_item(
                self_inner,
                Key,
                UpdateExpression,
                ConditionExpression=None,
                ExpressionAttributeValues=None,
                **_kw,
            ):
                # Record the call for assertions.
                update_calls.append(
                    {
                        "Key": dict(Key),
                        "UpdateExpression": UpdateExpression,
                        "ConditionExpression": ConditionExpression,
                        "ExpressionAttributeValues": dict(
                            ExpressionAttributeValues or {}
                        ),
                    }
                )

                # Apply the change in-memory so the test can verify
                # the persisted row reflects the patch. We only need
                # the very narrow update used by the schedule failure
                # path (``SET auto_stop_schedule_armed = :a``).
                cap_id = Key.get("capture_id")
                if cap_id is None or cap_id not in rows:
                    return {}
                m = re.match(
                    r"\s*SET\s+(\w+)\s*=\s*:(\w+)\s*$",
                    UpdateExpression or "",
                )
                if m and ExpressionAttributeValues is not None:
                    attr = m.group(1)
                    placeholder = ":" + m.group(2)
                    rows[cap_id][attr] = ExpressionAttributeValues[placeholder]
                return {"Attributes": rows[cap_id]}

        return _FakeTable()


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
def fake_scheduler(monkeypatch) -> _FakeScheduler:
    fake = _FakeScheduler()
    monkeypatch.setattr(main, "_scheduler_client", fake)
    return fake


@pytest.fixture
def fake_ec2(monkeypatch) -> _FakeEC2:
    fake = _FakeEC2()
    monkeypatch.setattr(main, "_ec2_client", fake)
    return fake


@pytest.fixture
def fake_state(monkeypatch) -> _FakeStateModule:
    fake = _FakeStateModule()
    monkeypatch.setattr(state, "query_active_captures", fake.query_active_captures)
    monkeypatch.setattr(
        state, "find_idempotent_capture", fake.find_idempotent_capture
    )
    monkeypatch.setattr(state, "put_vni_lookup_rows", fake.put_vni_lookup_rows)
    monkeypatch.setattr(state, "put_capture", fake.put_capture)
    monkeypatch.setattr(
        state, "update_capture_status", fake.update_capture_status
    )
    monkeypatch.setattr(
        state, "delete_vni_lookup_for_capture", fake.delete_vni_lookup_for_capture
    )
    monkeypatch.setattr(state, "_capture_table", fake.make_table)
    return fake


@pytest.fixture
def schedule_env(monkeypatch):
    """Set every environment variable the schedule path requires."""
    monkeypatch.setenv(
        "STOP_CAPTURE_INVOKER_LAMBDA_ARN", _TEST_INVOKER_LAMBDA_ARN
    )
    monkeypatch.setenv("SCHEDULE_GROUP_NAME", _TEST_SCHEDULE_GROUP_NAME)
    monkeypatch.setenv(
        "SCHEDULER_TARGET_ROLE_ARN", _TEST_SCHEDULER_TARGET_ROLE_ARN
    )


@pytest.fixture
def start_capture_env(monkeypatch, schedule_env):
    """Set every environment variable handle_start_capture needs."""
    monkeypatch.setenv("TRAFFIC_MIRROR_FILTER_ID", _TEST_FILTER_ID)
    monkeypatch.setenv("TRAFFIC_MIRROR_TARGET_ID", _TEST_TARGET_ID)
    monkeypatch.setenv("COLLECTOR_INSTANCE_ID", _TEST_COLLECTOR_INSTANCE_ID)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Matches the ``at(YYYY-MM-DDTHH:MM:SS)`` form documented for one-shot
# EventBridge Scheduler expressions. The ``ScheduleExpressionTimezone``
# field separately specifies UTC.
_AT_EXPRESSION_RE = re.compile(
    r"^at\((\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\)$"
)


def _parse_at_expression(expression: str) -> datetime:
    """Parse an ``at(YYYY-MM-DDTHH:MM:SS)`` expression into a UTC datetime."""
    m = _AT_EXPRESSION_RE.match(expression)
    assert m is not None, (
        f"Schedule expression {expression!r} does not match the documented "
        "EventBridge Scheduler one-shot at() form."
    )
    return datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=timezone.utc
    )


# ---------------------------------------------------------------------------
# Unit tests for _create_auto_stop_schedule
# ---------------------------------------------------------------------------


class TestCreateAutoStopScheduleHappyPath:
    """**Validates: Requirements 3.5, 4.6, 4.10**

    With every environment variable set, ``_create_auto_stop_schedule``
    issues exactly one ``CreateSchedule`` call with the documented
    arguments and returns ``True``.
    """

    def test_returns_true_when_create_succeeds(
        self, fake_scheduler, schedule_env
    ):
        deadline = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)

        result = main._create_auto_stop_schedule("cap-test-001", deadline)

        assert result is True
        assert len(fake_scheduler.created) == 1

    def test_uses_capture_id_as_schedule_name(
        self, fake_scheduler, schedule_env
    ):
        deadline = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)

        main._create_auto_stop_schedule("cap-name-test", deadline)

        kwargs = fake_scheduler.created[0]
        assert kwargs["Name"] == "cap-name-test"

    def test_uses_schedule_group_from_env(
        self, fake_scheduler, schedule_env
    ):
        deadline = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)

        main._create_auto_stop_schedule("cap-group-test", deadline)

        kwargs = fake_scheduler.created[0]
        assert kwargs["GroupName"] == _TEST_SCHEDULE_GROUP_NAME

    def test_uses_at_expression_with_exact_deadline(
        self, fake_scheduler, schedule_env
    ):
        # Per Req 4.6 / 4.10, the schedule must fire at the exact
        # deadline. The ``at()`` expression encodes the deadline at
        # second precision; EventBridge Scheduler's documented
        # delivery latency closes the remaining gap to ≤ 60 seconds.
        deadline = datetime(2026, 4, 20, 12, 30, 45, tzinfo=timezone.utc)

        main._create_auto_stop_schedule("cap-at-test", deadline)

        kwargs = fake_scheduler.created[0]
        assert kwargs["ScheduleExpression"] == "at(2026-04-20T12:30:45)"
        assert kwargs["ScheduleExpressionTimezone"] == "UTC"

    def test_flexible_time_window_off(
        self, fake_scheduler, schedule_env
    ):
        # FlexibleTimeWindow must be OFF so the schedule fires at the
        # exact deadline rather than within a flex window — needed to
        # satisfy the 60-second SLA in Req 4.6.
        deadline = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)

        main._create_auto_stop_schedule("cap-flex-test", deadline)

        kwargs = fake_scheduler.created[0]
        assert kwargs["FlexibleTimeWindow"] == {"Mode": "OFF"}

    def test_action_after_completion_delete(
        self, fake_scheduler, schedule_env
    ):
        # ActionAfterCompletion=DELETE so a fired schedule self-deletes
        # — this is exactly what makes ``stop_capture`` treat
        # ``ResourceNotFoundException`` as benign.
        deadline = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)

        main._create_auto_stop_schedule("cap-action-test", deadline)

        kwargs = fake_scheduler.created[0]
        assert kwargs["ActionAfterCompletion"] == "DELETE"

    def test_target_arn_is_invoker_lambda(
        self, fake_scheduler, schedule_env
    ):
        deadline = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)

        main._create_auto_stop_schedule("cap-target-arn-test", deadline)

        kwargs = fake_scheduler.created[0]
        assert kwargs["Target"]["Arn"] == _TEST_INVOKER_LAMBDA_ARN

    def test_target_role_arn_is_scheduler_target_role(
        self, fake_scheduler, schedule_env
    ):
        # Confirms Task 11's "scheduler IAM role attached to the
        # schedule is referenced correctly so the schedule can invoke
        # the Lambda" requirement: the role passed in Target.RoleArn
        # is the EventBridge Scheduler-target role from the env var,
        # *not* the agent's own runtime role.
        deadline = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)

        main._create_auto_stop_schedule("cap-role-test", deadline)

        kwargs = fake_scheduler.created[0]
        assert kwargs["Target"]["RoleArn"] == _TEST_SCHEDULER_TARGET_ROLE_ARN
        # Defensive: must not accidentally pass the runtime role or
        # any other role.
        assert "agent" not in kwargs["Target"]["RoleArn"].lower()

    def test_target_input_is_capture_id_only(
        self, fake_scheduler, schedule_env
    ):
        # The schedule's Target.Input is the *Auto_Stop_Schedule wire
        # payload* documented in Task 11 and the design's
        # "StopCaptureInvokerLambda" section: only
        # ``{"capture_id": "<id>"}``. The Lambda re-wraps this into
        # the ``InvokeAgentRuntime`` envelope before calling the
        # Network Agent runtime.
        deadline = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)

        main._create_auto_stop_schedule("cap-input-test", deadline)

        kwargs = fake_scheduler.created[0]
        payload = json.loads(kwargs["Target"]["Input"])
        assert payload == {"capture_id": "cap-input-test"}
        # Defensive: the agent-style envelope must NOT be present.
        assert "action" not in payload
        assert "params" not in payload


class TestCreateAutoStopScheduleMissingConfig:
    """**Validates: Requirements 3.5**

    When any of the three required environment variables are missing,
    the helper logs a warning, makes no AWS call, and returns
    ``False`` so the caller persists ``auto_stop_schedule_armed=false``.
    """

    def test_returns_false_when_invoker_lambda_arn_missing(
        self, fake_scheduler, monkeypatch
    ):
        monkeypatch.delenv("STOP_CAPTURE_INVOKER_LAMBDA_ARN", raising=False)
        monkeypatch.setenv("SCHEDULE_GROUP_NAME", _TEST_SCHEDULE_GROUP_NAME)
        monkeypatch.setenv(
            "SCHEDULER_TARGET_ROLE_ARN", _TEST_SCHEDULER_TARGET_ROLE_ARN
        )
        deadline = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)

        result = main._create_auto_stop_schedule("cap-test", deadline)

        assert result is False
        assert fake_scheduler.created == []

    def test_returns_false_when_schedule_group_missing(
        self, fake_scheduler, monkeypatch
    ):
        monkeypatch.setenv(
            "STOP_CAPTURE_INVOKER_LAMBDA_ARN", _TEST_INVOKER_LAMBDA_ARN
        )
        monkeypatch.delenv("SCHEDULE_GROUP_NAME", raising=False)
        monkeypatch.setenv(
            "SCHEDULER_TARGET_ROLE_ARN", _TEST_SCHEDULER_TARGET_ROLE_ARN
        )
        deadline = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)

        result = main._create_auto_stop_schedule("cap-test", deadline)

        assert result is False
        assert fake_scheduler.created == []

    def test_returns_false_when_target_role_arn_missing(
        self, fake_scheduler, monkeypatch
    ):
        monkeypatch.setenv(
            "STOP_CAPTURE_INVOKER_LAMBDA_ARN", _TEST_INVOKER_LAMBDA_ARN
        )
        monkeypatch.setenv("SCHEDULE_GROUP_NAME", _TEST_SCHEDULE_GROUP_NAME)
        monkeypatch.delenv("SCHEDULER_TARGET_ROLE_ARN", raising=False)
        deadline = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)

        result = main._create_auto_stop_schedule("cap-test", deadline)

        assert result is False
        assert fake_scheduler.created == []

    def test_returns_false_when_all_missing(
        self, fake_scheduler, monkeypatch
    ):
        monkeypatch.delenv("STOP_CAPTURE_INVOKER_LAMBDA_ARN", raising=False)
        monkeypatch.delenv("SCHEDULE_GROUP_NAME", raising=False)
        monkeypatch.delenv("SCHEDULER_TARGET_ROLE_ARN", raising=False)
        deadline = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)

        result = main._create_auto_stop_schedule("cap-test", deadline)

        assert result is False
        assert fake_scheduler.created == []


class TestCreateAutoStopScheduleAwsErrors:
    """**Validates: Requirements 3.5**

    AWS errors surface as ``False`` so the caller persists
    ``auto_stop_schedule_armed=false`` per the design's EH-3 step 10
    rule. The capture itself is *not* rolled back — the mirror
    sessions and DynamoDB row stay so the user can stop the capture
    manually.
    """

    def test_client_error_returns_false(
        self, fake_scheduler, schedule_env
    ):
        fake_scheduler.raise_on_create = ClientError(
            error_response={
                "Error": {
                    "Code": "ValidationException",
                    "Message": "schedule expression is invalid",
                }
            },
            operation_name="CreateSchedule",
        )
        deadline = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)

        result = main._create_auto_stop_schedule("cap-test", deadline)

        assert result is False

    def test_throttling_returns_false(
        self, fake_scheduler, schedule_env
    ):
        fake_scheduler.raise_on_create = ClientError(
            error_response={
                "Error": {
                    "Code": "ThrottlingException",
                    "Message": "rate-limited",
                }
            },
            operation_name="CreateSchedule",
        )
        deadline = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)

        result = main._create_auto_stop_schedule("cap-test", deadline)

        assert result is False

    def test_botocore_error_returns_false(
        self, fake_scheduler, schedule_env
    ):
        fake_scheduler.raise_on_create = BotoCoreError()
        deadline = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)

        result = main._create_auto_stop_schedule("cap-test", deadline)

        assert result is False


# ---------------------------------------------------------------------------
# Integration with handle_start_capture
# ---------------------------------------------------------------------------


class TestStartCaptureIntegration:
    """**Validates: Requirements 3.5, 3.6, 4.10**

    ``handle_start_capture`` integrates with ``_create_auto_stop_schedule``
    correctly: a successful schedule create produces
    ``data.auto_stop_schedule_armed=True``; a failure produces
    ``data.auto_stop_schedule_armed=False`` *and* the persisted row
    is patched so a reconciler can find it. Mirror sessions are NOT
    rolled back on schedule failure (design EH-3 step 10).
    """

    def test_successful_start_capture_arms_schedule(
        self, fake_ec2, fake_scheduler, fake_state, start_capture_env
    ):
        fake_ec2.add_eni_with_opt_in("eni-aaaa1111", instance_id="i-1111")
        fake_ec2.set_collector(_TEST_COLLECTOR_INSTANCE_ID)

        response = main.handle_start_capture(
            {
                "eni_ids": ["eni-aaaa1111"],
                "duration_minutes": 5,
            }
        )

        assert response["success"] is True
        assert response["data"]["auto_stop_schedule_armed"] is True
        # Exactly one CreateSchedule call.
        assert len(fake_scheduler.created) == 1
        # And the schedule's name matches the generated capture_id.
        capture_id = response["data"]["capture_id"]
        assert fake_scheduler.created[0]["Name"] == capture_id
        # The schedule's input carries the same capture_id.
        payload = json.loads(fake_scheduler.created[0]["Target"]["Input"])
        assert payload == {"capture_id": capture_id}

    def test_schedule_deadline_matches_start_time_plus_duration(
        self, fake_ec2, fake_scheduler, fake_state, start_capture_env
    ):
        # Property check: the schedule's at() expression encodes
        # ``start_time + duration_minutes`` (Req 3.5).
        fake_ec2.add_eni_with_opt_in("eni-aaaa1111", instance_id="i-1111")
        fake_ec2.set_collector(_TEST_COLLECTOR_INSTANCE_ID)

        before = datetime.now(timezone.utc)
        response = main.handle_start_capture(
            {
                "eni_ids": ["eni-aaaa1111"],
                "duration_minutes": 7,
            }
        )
        after = datetime.now(timezone.utc)

        assert response["success"] is True
        kwargs = fake_scheduler.created[0]
        scheduled_at = _parse_at_expression(kwargs["ScheduleExpression"])

        # The deadline must lie in [before + 7 min, after + 7 min].
        # We compare at second precision because the at() expression
        # is second-precision.
        expected_min = (before + timedelta(minutes=7)).replace(microsecond=0)
        expected_max = (after + timedelta(minutes=7) + timedelta(seconds=1)).replace(
            microsecond=0
        )
        assert expected_min <= scheduled_at <= expected_max

    def test_schedule_failure_does_not_roll_back_mirror_sessions(
        self, fake_ec2, fake_scheduler, fake_state, start_capture_env
    ):
        # Per design EH-3 step 10, a schedule create failure leaves
        # the mirror sessions and DDB row in place — the capture is
        # technically active, the user can stop it manually.
        fake_ec2.add_eni_with_opt_in("eni-aaaa1111", instance_id="i-1111")
        fake_ec2.set_collector(_TEST_COLLECTOR_INSTANCE_ID)
        fake_scheduler.raise_on_create = ClientError(
            error_response={
                "Error": {
                    "Code": "ServiceQuotaExceededException",
                    "Message": "schedule quota exceeded",
                }
            },
            operation_name="CreateSchedule",
        )

        response = main.handle_start_capture(
            {
                "eni_ids": ["eni-aaaa1111"],
                "duration_minutes": 5,
            }
        )

        # The action still reports success (the capture is active).
        assert response["success"] is True
        # But auto_stop_schedule_armed is False so the caller knows.
        assert response["data"]["auto_stop_schedule_armed"] is False
        # And the persisted row was patched.
        capture_id = response["data"]["capture_id"]
        assert fake_state.rows[capture_id]["auto_stop_schedule_armed"] is False
        # The patch went through update_item with the documented
        # condition expression.
        patch_calls = [
            c
            for c in fake_state.update_item_calls
            if c["Key"].get("capture_id") == capture_id
        ]
        assert len(patch_calls) == 1
        assert patch_calls[0]["ConditionExpression"] == "attribute_exists(capture_id)"
        # The mirror session was NOT rolled back.
        assert len(fake_ec2.created_mirror_sessions) == 1
        assert fake_ec2.deleted_mirror_sessions == []

    def test_schedule_failure_when_config_missing_does_not_call_aws(
        self, fake_ec2, fake_scheduler, fake_state, monkeypatch
    ):
        # When the schedule env vars are missing (e.g. CDK Tasks 26-27
        # have not been deployed in the test environment), the
        # capture still proceeds and the row records auto_stop_schedule_armed=false.
        monkeypatch.setenv("TRAFFIC_MIRROR_FILTER_ID", _TEST_FILTER_ID)
        monkeypatch.setenv("TRAFFIC_MIRROR_TARGET_ID", _TEST_TARGET_ID)
        monkeypatch.setenv(
            "COLLECTOR_INSTANCE_ID", _TEST_COLLECTOR_INSTANCE_ID
        )
        monkeypatch.delenv("STOP_CAPTURE_INVOKER_LAMBDA_ARN", raising=False)
        monkeypatch.delenv("SCHEDULE_GROUP_NAME", raising=False)
        monkeypatch.delenv("SCHEDULER_TARGET_ROLE_ARN", raising=False)

        fake_ec2.add_eni_with_opt_in("eni-aaaa1111", instance_id="i-1111")
        fake_ec2.set_collector(_TEST_COLLECTOR_INSTANCE_ID)

        response = main.handle_start_capture(
            {
                "eni_ids": ["eni-aaaa1111"],
                "duration_minutes": 5,
            }
        )

        assert response["success"] is True
        assert response["data"]["auto_stop_schedule_armed"] is False
        # No AWS schedule call was made.
        assert fake_scheduler.created == []
        # The persisted row reflects the missing schedule.
        capture_id = response["data"]["capture_id"]
        assert fake_state.rows[capture_id]["auto_stop_schedule_armed"] is False


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


# Strategy: capture_id values matching Capture_Id_Format
# ([A-Za-z0-9_-]{1,128}). We restrict to a smaller range to keep
# Hypothesis examples fast while still exercising the boundary.
_capture_id_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="_-",
    ),
    min_size=1,
    max_size=64,
)


# Strategy: deadlines in a wide window so we exercise multiple
# year/month/day boundaries. Hypothesis already produces edge cases
# like the millisecond boundary.
_deadline_strategy = st.datetimes(
    min_value=datetime(2025, 1, 1),
    max_value=datetime(2030, 12, 31, 23, 59, 59),
    timezones=st.just(timezone.utc),
)


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(capture_id=_capture_id_strategy, deadline=_deadline_strategy)
def test_property_schedule_input_carries_exact_capture_id(
    monkeypatch, capture_id, deadline
):
    """**Validates: Requirements 3.5**

    For any valid (capture_id, deadline) pair, the schedule's
    ``Target.Input`` JSON object always contains exactly the same
    capture_id with no transformation, and never wraps it into an
    agent-style envelope. This is the wire-format contract the
    StopCaptureInvokerLambda relies on.
    """
    monkeypatch.setattr(main, "_scheduler_client", None)
    monkeypatch.setenv(
        "STOP_CAPTURE_INVOKER_LAMBDA_ARN", _TEST_INVOKER_LAMBDA_ARN
    )
    monkeypatch.setenv("SCHEDULE_GROUP_NAME", _TEST_SCHEDULE_GROUP_NAME)
    monkeypatch.setenv(
        "SCHEDULER_TARGET_ROLE_ARN", _TEST_SCHEDULER_TARGET_ROLE_ARN
    )
    fake = _FakeScheduler()
    monkeypatch.setattr(main, "_scheduler_client", fake)

    result = main._create_auto_stop_schedule(capture_id, deadline)

    assert result is True
    assert len(fake.created) == 1
    target = fake.created[0]["Target"]
    payload = json.loads(target["Input"])
    # Exactly one key, exactly the supplied capture_id.
    assert payload == {"capture_id": capture_id}


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(deadline=_deadline_strategy)
def test_property_at_expression_encodes_exact_deadline(
    monkeypatch, deadline
):
    """**Validates: Requirements 4.6, 4.10**

    For any deadline, the schedule's ``ScheduleExpression`` encodes
    exactly the supplied UTC instant at second precision. EventBridge
    Scheduler's documented delivery latency closes the remaining gap
    to the 60-second SLA in Req 4.6.
    """
    monkeypatch.setattr(main, "_scheduler_client", None)
    monkeypatch.setenv(
        "STOP_CAPTURE_INVOKER_LAMBDA_ARN", _TEST_INVOKER_LAMBDA_ARN
    )
    monkeypatch.setenv("SCHEDULE_GROUP_NAME", _TEST_SCHEDULE_GROUP_NAME)
    monkeypatch.setenv(
        "SCHEDULER_TARGET_ROLE_ARN", _TEST_SCHEDULER_TARGET_ROLE_ARN
    )
    fake = _FakeScheduler()
    monkeypatch.setattr(main, "_scheduler_client", fake)

    result = main._create_auto_stop_schedule("cap-prop-test", deadline)

    assert result is True
    kwargs = fake.created[0]
    scheduled_at = _parse_at_expression(kwargs["ScheduleExpression"])
    # The encoded deadline must equal the input deadline at second
    # precision (microseconds are stripped by strftime).
    expected = deadline.replace(microsecond=0)
    assert scheduled_at == expected
    # And the timezone is always UTC.
    assert kwargs["ScheduleExpressionTimezone"] == "UTC"


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    invoker_arn_set=st.booleans(),
    group_name_set=st.booleans(),
    role_arn_set=st.booleans(),
)
def test_property_missing_any_env_returns_false_no_aws_call(
    monkeypatch,
    invoker_arn_set,
    group_name_set,
    role_arn_set,
):
    """**Validates: Requirements 3.5**

    If any of the three required environment variables is unset, the
    helper makes no AWS call and returns ``False`` — never partially
    creating a schedule. Skipped when all three are set (that is the
    happy-path case covered separately).
    """
    from hypothesis import assume

    assume(not (invoker_arn_set and group_name_set and role_arn_set))

    monkeypatch.setattr(main, "_scheduler_client", None)
    if invoker_arn_set:
        monkeypatch.setenv(
            "STOP_CAPTURE_INVOKER_LAMBDA_ARN", _TEST_INVOKER_LAMBDA_ARN
        )
    else:
        monkeypatch.delenv("STOP_CAPTURE_INVOKER_LAMBDA_ARN", raising=False)
    if group_name_set:
        monkeypatch.setenv("SCHEDULE_GROUP_NAME", _TEST_SCHEDULE_GROUP_NAME)
    else:
        monkeypatch.delenv("SCHEDULE_GROUP_NAME", raising=False)
    if role_arn_set:
        monkeypatch.setenv(
            "SCHEDULER_TARGET_ROLE_ARN", _TEST_SCHEDULER_TARGET_ROLE_ARN
        )
    else:
        monkeypatch.delenv("SCHEDULER_TARGET_ROLE_ARN", raising=False)

    fake = _FakeScheduler()
    monkeypatch.setattr(main, "_scheduler_client", fake)
    deadline = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)

    result = main._create_auto_stop_schedule("cap-prop-missing", deadline)

    assert result is False
    assert fake.created == []
