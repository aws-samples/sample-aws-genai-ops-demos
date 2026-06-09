"""
Unit and property-based tests for ``handle_transform_capture`` (Task 10, Reqs 3.12, 3.13, 6.10).

Exercises:

- Validates ``capture_id`` against ``Capture_Id_Format`` and surfaces
  ``invalid_parameter`` for malformed values (Reqs 5.20, 6.10).
- Returns ``not_found`` when the Capture_State_Table has no row for
  the supplied ``capture_id`` (Req 3.13). The Step Functions
  ``StartExecution`` API is **never** called in this case.
- Surfaces ``configuration_missing`` when the ``TRANSFORMATION_SFN_ARN``
  or ``CAPTURE_STATE_TABLE`` environment variable is unset.
- Calls ``stepfunctions:StartExecution`` once with the state machine
  ARN supplied via ``TRANSFORMATION_SFN_ARN`` and input payload
  ``{"capture_id": <id>}`` (Req 3.12).
- Persists the returned ``transform_execution_arn`` on the row via
  ``state.update_capture_transform_execution_arn``; a persist failure
  does not fail the response since the execution has already started.
- Sets ``metadata.sourceApi = "stepfunctions:StartExecution"``.
- Surfaces botocore errors as ``aws_*`` categories per ``_classify_aws_error``.

Tests use small in-memory fakes for Step Functions and the ``state``
module so the full handler path runs without AWS or ``moto``.

Run from the ``network-agent`` directory:

    python -m pytest test_transform_capture.py -v
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import pytest
from botocore.exceptions import BotoCoreError, ClientError
from hypothesis import HealthCheck, given, settings, strategies as st

import main
import state
from validation import ValidationError


# ---------------------------------------------------------------------------
# Fake Step Functions client
# ---------------------------------------------------------------------------


class _FakeSfn:
    """In-memory stand-in for ``boto3.client('stepfunctions')`` supporting
    only ``start_execution``.

    Records every call so tests can assert on the exact arguments
    submitted by ``handle_transform_capture``. ``raise_on_next`` lets
    a test simulate an AWS error without touching ``moto`` or the
    network.
    """

    def __init__(self, execution_arn: str = "arn:aws:states:us-east-1:000000000000:execution:goat-transform:exec-001") -> None:
        self.execution_arn = execution_arn
        self.calls: List[Dict[str, Any]] = []
        self.raise_on_next: Optional[Exception] = None

    def start_execution(self, *, stateMachineArn: str, input: str) -> dict:
        if self.raise_on_next is not None:
            exc = self.raise_on_next
            self.raise_on_next = None
            raise exc
        self.calls.append(
            {"stateMachineArn": stateMachineArn, "input": input}
        )
        return {"executionArn": self.execution_arn, "startDate": None}


# ---------------------------------------------------------------------------
# Fake state-module surface
# ---------------------------------------------------------------------------


class _FakeState:
    """In-memory recorder for the two ``state`` functions ``transform_capture`` calls."""

    def __init__(self) -> None:
        self.rows: Dict[str, Optional[dict]] = {}
        self.get_raises: Dict[str, Exception] = {}
        self.update_arn_raises: Dict[str, Exception] = {}
        self.update_arn_calls: List[Dict[str, str]] = []

    def get_capture(self, capture_id: str) -> Optional[dict]:
        if capture_id in self.get_raises:
            raise self.get_raises[capture_id]
        return self.rows.get(capture_id)

    def update_capture_transform_execution_arn(
        self, capture_id: str, transform_execution_arn: str
    ) -> dict:
        if capture_id in self.update_arn_raises:
            raise self.update_arn_raises[capture_id]
        self.update_arn_calls.append(
            {
                "capture_id": capture_id,
                "transform_execution_arn": transform_execution_arn,
            }
        )
        existing = self.rows.get(capture_id) or {}
        new_row = dict(existing)
        new_row["transform_execution_arn"] = transform_execution_arn
        self.rows[capture_id] = new_row
        return {"Attributes": new_row}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_main_singletons(monkeypatch):
    """Reset the boto3 client cache singletons between tests."""
    monkeypatch.setattr(main, "_sfn_client", None)
    monkeypatch.setattr(main, "_s3_client", None)
    monkeypatch.setattr(main, "_ec2_client", None)
    monkeypatch.setattr(main, "_scheduler_client", None)
    yield
    monkeypatch.setattr(main, "_sfn_client", None)
    monkeypatch.setattr(main, "_s3_client", None)
    monkeypatch.setattr(main, "_ec2_client", None)
    monkeypatch.setattr(main, "_scheduler_client", None)


@pytest.fixture
def fake_sfn(monkeypatch) -> _FakeSfn:
    """Install a fresh ``_FakeSfn`` as ``main._sfn_client``."""
    fake = _FakeSfn()
    monkeypatch.setattr(main, "_sfn_client", fake)
    return fake


@pytest.fixture
def fake_state(monkeypatch) -> _FakeState:
    """Patch the two ``state`` functions transform_capture calls."""
    fake = _FakeState()
    monkeypatch.setattr(state, "get_capture", fake.get_capture)
    monkeypatch.setattr(
        state,
        "update_capture_transform_execution_arn",
        fake.update_capture_transform_execution_arn,
    )
    return fake


@pytest.fixture
def sfn_env(monkeypatch):
    """Set the ``TRANSFORMATION_SFN_ARN`` env var so the action can run."""
    monkeypatch.setenv(
        "TRANSFORMATION_SFN_ARN",
        "arn:aws:states:us-east-1:000000000000:stateMachine:goat-transform",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    capture_id: str = "cap-test-001",
    *,
    status: str = "stopped",
    deadline: str = "2026-01-01T12:15:00+00:00",
    start_time: str = "2026-01-01T12:00:00+00:00",
) -> dict:
    """Build a minimal Capture_State_Table row for transform_capture tests."""
    return {
        "capture_id": capture_id,
        "status": status,
        "start_time": start_time,
        "deadline": deadline,
        "duration_minutes": 15,
        "eni_ids": ["eni-aaaaaaaa"],
        "mirror_session_ids": ["tms-1111"],
    }


def _aws_client_error(code: str, op: str = "StartExecution") -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": code, "Message": f"{code} on {op}"}},
        operation_name=op,
    )


# ---------------------------------------------------------------------------
# Caller-fault paths (validation, lookup)
# ---------------------------------------------------------------------------


class TestValidation:
    """Caller-fault response envelopes for invalid parameters."""

    def test_missing_capture_id_returns_invalid_parameter(
        self, fake_sfn, fake_state, sfn_env
    ):
        result = main.handle_transform_capture({})

        assert result["success"] is False
        assert result["domain"] == "network"
        assert result["metadata"]["errorCategory"] == "invalid_parameter"
        assert (
            result["metadata"]["sourceApi"] == "stepfunctions:StartExecution"
        )
        # Step Functions was never called.
        assert fake_sfn.calls == []
        # The row was never updated.
        assert fake_state.update_arn_calls == []

    def test_empty_capture_id_returns_invalid_parameter(
        self, fake_sfn, fake_state, sfn_env
    ):
        result = main.handle_transform_capture({"capture_id": ""})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_sfn.calls == []

    def test_too_long_capture_id_returns_invalid_parameter(
        self, fake_sfn, fake_state, sfn_env
    ):
        result = main.handle_transform_capture(
            {"capture_id": "a" * 129}
        )

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_sfn.calls == []

    def test_capture_id_with_disallowed_char_returns_invalid_parameter(
        self, fake_sfn, fake_state, sfn_env
    ):
        # Whitespace, dots, and slashes are not in [A-Za-z0-9_-].
        result = main.handle_transform_capture(
            {"capture_id": "cap test/1.2"}
        )

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_sfn.calls == []

    def test_non_dict_params_treated_as_missing_capture_id(
        self, fake_sfn, fake_state, sfn_env
    ):
        result = main.handle_transform_capture("not a dict")  # type: ignore[arg-type]

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_sfn.calls == []


class TestNotFound:
    """When the Capture_State_Table has no row, Step Functions is never called."""

    def test_unknown_capture_id_returns_not_found(
        self, fake_sfn, fake_state, sfn_env
    ):
        # No rows pre-loaded — fake_state.rows is empty.
        result = main.handle_transform_capture(
            {"capture_id": "cap-missing"}
        )

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "not_found"
        assert (
            result["metadata"]["sourceApi"] == "stepfunctions:StartExecution"
        )
        assert "cap-missing" in result["error"]
        # Critical Req 3.13: Step Functions execution must NOT have started.
        assert fake_sfn.calls == []
        assert fake_state.update_arn_calls == []


class TestConfigurationMissing:
    """Missing TRANSFORMATION_SFN_ARN env var surfaces as configuration_missing."""

    def test_missing_sfn_env_surfaces_configuration_missing(
        self, fake_sfn, fake_state, monkeypatch
    ):
        # Ensure the env var is absent.
        monkeypatch.delenv("TRANSFORMATION_SFN_ARN", raising=False)
        fake_state.rows["cap-001"] = _make_row("cap-001")

        result = main.handle_transform_capture({"capture_id": "cap-001"})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "configuration_missing"
        # The row existed, so we got past the not_found gate; but the
        # missing env var stops the action before StartExecution is
        # called.
        assert fake_sfn.calls == []
        assert fake_state.update_arn_calls == []

    def test_state_error_on_get_capture_surfaces_configuration_missing(
        self, fake_sfn, fake_state, sfn_env
    ):
        fake_state.get_raises["cap-001"] = state.StateError(
            "Required environment variable 'CAPTURE_STATE_TABLE' is not set."
        )

        result = main.handle_transform_capture({"capture_id": "cap-001"})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "configuration_missing"
        assert fake_sfn.calls == []


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    """Success path: row exists, StartExecution succeeds, ARN persisted."""

    def test_invokes_start_execution_with_capture_id_input(
        self, fake_sfn, fake_state, sfn_env
    ):
        fake_state.rows["cap-001"] = _make_row("cap-001")

        result = main.handle_transform_capture({"capture_id": "cap-001"})

        assert result["success"] is True
        assert result["domain"] == "network"
        assert (
            result["metadata"]["sourceApi"] == "stepfunctions:StartExecution"
        )

        # Exactly one StartExecution call with the documented input.
        assert len(fake_sfn.calls) == 1
        call = fake_sfn.calls[0]
        assert (
            call["stateMachineArn"]
            == "arn:aws:states:us-east-1:000000000000:stateMachine:goat-transform"
        )
        # Req 3.12: input is ``{"capture_id": <id>}``. Compare as JSON
        # so ordering / whitespace is irrelevant.
        assert json.loads(call["input"]) == {"capture_id": "cap-001"}

    def test_returns_execution_arn_in_data(
        self, fake_sfn, fake_state, sfn_env
    ):
        fake_state.rows["cap-002"] = _make_row("cap-002")

        result = main.handle_transform_capture({"capture_id": "cap-002"})

        assert result["success"] is True
        assert result["data"]["capture_id"] == "cap-002"
        assert (
            result["data"]["transform_execution_arn"]
            == fake_sfn.execution_arn
        )
        assert result["data"]["transform_execution_arn_persisted"] is True
        # ``status`` field reflects the row at time of call.
        assert result["data"]["status"] == "stopped"

    def test_persists_execution_arn_on_row(
        self, fake_sfn, fake_state, sfn_env
    ):
        fake_state.rows["cap-003"] = _make_row("cap-003")

        main.handle_transform_capture({"capture_id": "cap-003"})

        # Exactly one persist call with the right arguments.
        assert len(fake_state.update_arn_calls) == 1
        call = fake_state.update_arn_calls[0]
        assert call["capture_id"] == "cap-003"
        assert call["transform_execution_arn"] == fake_sfn.execution_arn

        # The recorded row now carries the ARN.
        assert (
            fake_state.rows["cap-003"]["transform_execution_arn"]
            == fake_sfn.execution_arn
        )

    def test_status_unchanged_by_action(
        self, fake_sfn, fake_state, sfn_env
    ):
        # The transform_capture action **must not** transition the row
        # to ``transformed`` immediately — the state transition happens
        # only after the Step Functions execution succeeds, which is
        # observed asynchronously by the orchestration agent.
        fake_state.rows["cap-004"] = _make_row("cap-004", status="stopped")

        main.handle_transform_capture({"capture_id": "cap-004"})

        assert fake_state.rows["cap-004"]["status"] == "stopped"

    def test_persist_failure_does_not_fail_action(
        self, fake_sfn, fake_state, sfn_env
    ):
        # If StartExecution succeeds but the persist update fails, the
        # action **still** returns success because the execution has
        # already been started — we cannot un-start it. The response
        # data signals the failure via ``transform_execution_arn_persisted``.
        fake_state.rows["cap-005"] = _make_row("cap-005")
        fake_state.update_arn_raises["cap-005"] = _aws_client_error(
            "ConditionalCheckFailedException", op="UpdateItem"
        )

        result = main.handle_transform_capture({"capture_id": "cap-005"})

        assert result["success"] is True
        assert (
            result["data"]["transform_execution_arn"]
            == fake_sfn.execution_arn
        )
        assert result["data"]["transform_execution_arn_persisted"] is False
        # The formattedText surfaces the persist failure so an
        # operator can trace it.
        assert "failed to persist" in result["formattedText"]


# ---------------------------------------------------------------------------
# AWS error paths
# ---------------------------------------------------------------------------


class TestAwsErrors:
    """``stepfunctions:StartExecution`` failures map to ``aws_*`` categories."""

    def test_throttling_maps_to_aws_throttled(
        self, fake_sfn, fake_state, sfn_env
    ):
        fake_state.rows["cap-006"] = _make_row("cap-006")
        fake_sfn.raise_on_next = _aws_client_error("ThrottlingException")

        result = main.handle_transform_capture({"capture_id": "cap-006"})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "aws_throttled"
        assert (
            result["metadata"]["sourceApi"] == "stepfunctions:StartExecution"
        )
        # The row was never updated since StartExecution failed.
        assert fake_state.update_arn_calls == []

    def test_access_denied_maps_to_aws_access_denied(
        self, fake_sfn, fake_state, sfn_env
    ):
        fake_state.rows["cap-007"] = _make_row("cap-007")
        fake_sfn.raise_on_next = _aws_client_error("AccessDeniedException")

        result = main.handle_transform_capture({"capture_id": "cap-007"})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "aws_access_denied"

    def test_botocore_error_propagated_as_aws_other(
        self, fake_sfn, fake_state, sfn_env
    ):
        fake_state.rows["cap-008"] = _make_row("cap-008")
        fake_sfn.raise_on_next = BotoCoreError()

        result = main.handle_transform_capture({"capture_id": "cap-008"})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "aws_other"

    def test_dynamodb_error_on_get_capture_surfaces_aws_category(
        self, fake_sfn, fake_state, sfn_env
    ):
        fake_state.get_raises["cap-009"] = _aws_client_error(
            "InternalError", op="GetItem"
        )

        result = main.handle_transform_capture({"capture_id": "cap-009"})

        assert result["success"] is False
        assert (
            result["metadata"]["errorCategory"] == "aws_service_unavailable"
        )
        # Step Functions was never reached because the row read failed.
        assert fake_sfn.calls == []


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


# Capture_Id_Format from the requirements glossary: 1..128 chars from
# [A-Za-z0-9_-]. We narrow to a manageable subset for the property
# tests to keep run time predictable.
_CAPTURE_ID_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "_-"
)


@st.composite
def valid_capture_ids(draw):
    return draw(
        st.text(
            alphabet=_CAPTURE_ID_ALPHABET,
            min_size=1,
            max_size=64,
        )
    )


class TestProperties:
    """Property-based invariants for ``handle_transform_capture``."""

    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(capture_id=valid_capture_ids())
    def test_valid_capture_id_with_existing_row_always_starts_execution(
        self, capture_id, fake_sfn, fake_state, sfn_env
    ):
        """For every valid capture_id whose row exists, StartExecution
        is called exactly once with the id as the input payload, and
        the response surfaces the returned executionArn."""
        # Reset the recorder between examples so we can assert on the
        # exact argument shape.
        fake_sfn.calls = []
        fake_state.update_arn_calls = []
        fake_state.rows[capture_id] = _make_row(capture_id)

        result = main.handle_transform_capture({"capture_id": capture_id})

        assert result["success"] is True
        assert (
            result["data"]["transform_execution_arn"]
            == fake_sfn.execution_arn
        )
        assert len(fake_sfn.calls) == 1
        assert json.loads(fake_sfn.calls[0]["input"]) == {
            "capture_id": capture_id
        }

    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(capture_id=valid_capture_ids())
    def test_unknown_capture_id_never_starts_execution(
        self, capture_id, fake_sfn, fake_state, sfn_env
    ):
        """For every valid capture_id whose row is **absent**, the
        handler returns ``not_found`` and Step Functions is not
        invoked. This is the core safety property of Req 3.13."""
        fake_sfn.calls = []
        fake_state.rows.clear()

        result = main.handle_transform_capture({"capture_id": capture_id})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "not_found"
        assert fake_sfn.calls == []
