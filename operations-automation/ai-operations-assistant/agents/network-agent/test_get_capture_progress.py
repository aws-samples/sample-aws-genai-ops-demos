"""
Unit and property-based tests for ``handle_get_capture_progress`` (Task 10, Reqs 3.17, 3.18, 6.10).

Exercises:

- Validates ``capture_id`` against ``Capture_Id_Format`` and surfaces
  ``invalid_parameter`` for malformed values (Reqs 5.20, 6.10).
- Returns ``not_found`` when the Capture_State_Table has no row for
  the supplied ``capture_id`` (Req 3.18). The S3 ``ListObjectsV2``
  API is **never** called in this case.
- Surfaces ``configuration_missing`` when the ``DATA_BUCKET_NAME``
  or ``CAPTURE_STATE_TABLE`` environment variable is unset.
- Computes ``time_remaining_seconds = (deadline - now).total_seconds()``
  from the row's ``deadline``. Negative for past deadlines.
- Lists ``s3://{bucket}/raw/{capture_id}/`` and aggregates
  ``s3_objects_uploaded_count`` (count) and ``bytes_uploaded`` (sum
  of ``Size``) across every page of the paginator (Req 3.17).
- Sets ``metadata.sourceApi = "s3:ListObjectsV2"``.
- Surfaces botocore errors as ``aws_*`` categories per ``_classify_aws_error``.

Tests use small in-memory fakes for S3 and the ``state`` module so
the full handler path runs without AWS or ``moto``.

Run from the ``network-agent`` directory:

    python -m pytest test_get_capture_progress.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

import pytest
from botocore.exceptions import BotoCoreError, ClientError
from hypothesis import HealthCheck, given, settings, strategies as st

import main
import state
from validation import ValidationError


# ---------------------------------------------------------------------------
# Fake S3 client + paginator
# ---------------------------------------------------------------------------


class _FakeS3Paginator:
    """Minimal stand-in for ``boto3`` S3 paginator returning supplied pages."""

    def __init__(self, pages: List[Dict[str, Any]], raise_on_paginate: Optional[Exception] = None) -> None:
        self.pages = pages
        self.raise_on_paginate = raise_on_paginate
        self.paginate_calls: List[Dict[str, Any]] = []

    def paginate(self, *, Bucket: str, Prefix: str) -> Iterable[Dict[str, Any]]:
        self.paginate_calls.append({"Bucket": Bucket, "Prefix": Prefix})
        if self.raise_on_paginate is not None:
            raise self.raise_on_paginate
        return iter(self.pages)


class _FakeS3:
    """In-memory stand-in for ``boto3.client('s3')`` supporting
    ``get_paginator('list_objects_v2')``.

    The fake exposes a single paginator that returns the pages
    supplied at construction. A test can swap the paginator at
    runtime to simulate bucket changes between calls.
    """

    def __init__(self, pages: Optional[List[Dict[str, Any]]] = None) -> None:
        self._paginator = _FakeS3Paginator(pages if pages is not None else [])

    @property
    def paginator(self) -> _FakeS3Paginator:
        return self._paginator

    def set_pages(
        self,
        pages: List[Dict[str, Any]],
        raise_on_paginate: Optional[Exception] = None,
    ) -> None:
        self._paginator = _FakeS3Paginator(pages, raise_on_paginate)

    def get_paginator(self, name: str) -> _FakeS3Paginator:
        if name != "list_objects_v2":
            raise NotImplementedError(
                f"_FakeS3 does not implement paginator {name!r}"
            )
        return self._paginator


# ---------------------------------------------------------------------------
# Fake state-module surface
# ---------------------------------------------------------------------------


class _FakeState:
    """In-memory recorder for the single ``state.get_capture`` call."""

    def __init__(self) -> None:
        self.rows: Dict[str, Optional[dict]] = {}
        self.get_raises: Dict[str, Exception] = {}
        self.get_calls: List[str] = []

    def get_capture(self, capture_id: str) -> Optional[dict]:
        self.get_calls.append(capture_id)
        if capture_id in self.get_raises:
            raise self.get_raises[capture_id]
        return self.rows.get(capture_id)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_main_singletons(monkeypatch):
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
def fake_s3(monkeypatch) -> _FakeS3:
    fake = _FakeS3()
    monkeypatch.setattr(main, "_s3_client", fake)
    return fake


@pytest.fixture
def fake_state(monkeypatch) -> _FakeState:
    fake = _FakeState()
    monkeypatch.setattr(state, "get_capture", fake.get_capture)
    return fake


@pytest.fixture
def bucket_env(monkeypatch):
    """Set the ``DATA_BUCKET_NAME`` env var so the action can run."""
    monkeypatch.setenv("DATA_BUCKET_NAME", "goat-network-data-test")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    capture_id: str = "cap-test-001",
    *,
    status: str = "active",
    deadline: Optional[str] = None,
    start_time: str = "2026-01-01T12:00:00+00:00",
) -> dict:
    """Build a minimal row dict with a sensible default deadline."""
    if deadline is None:
        # 5 minutes in the future — gives every test a safely positive
        # ``time_remaining_seconds`` unless they override it.
        deadline = (
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).isoformat()
    return {
        "capture_id": capture_id,
        "status": status,
        "start_time": start_time,
        "deadline": deadline,
        "duration_minutes": 15,
        "eni_ids": ["eni-aaaaaaaa"],
        "mirror_session_ids": ["tms-1111"],
    }


def _s3_object(key: str, size: int) -> Dict[str, Any]:
    return {"Key": key, "Size": size}


def _aws_client_error(code: str, op: str = "ListObjectsV2") -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": code, "Message": f"{code} on {op}"}},
        operation_name=op,
    )


# ---------------------------------------------------------------------------
# Caller-fault paths
# ---------------------------------------------------------------------------


class TestValidation:
    def test_missing_capture_id_returns_invalid_parameter(
        self, fake_s3, fake_state, bucket_env
    ):
        result = main.handle_get_capture_progress({})

        assert result["success"] is False
        assert result["domain"] == "network"
        assert result["metadata"]["errorCategory"] == "invalid_parameter"
        assert result["metadata"]["sourceApi"] == "s3:ListObjectsV2"
        # No DynamoDB or S3 call was made.
        assert fake_state.get_calls == []
        assert fake_s3.paginator.paginate_calls == []

    def test_empty_capture_id_returns_invalid_parameter(
        self, fake_s3, fake_state, bucket_env
    ):
        result = main.handle_get_capture_progress({"capture_id": ""})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_state.get_calls == []
        assert fake_s3.paginator.paginate_calls == []

    def test_capture_id_with_disallowed_char_returns_invalid_parameter(
        self, fake_s3, fake_state, bucket_env
    ):
        result = main.handle_get_capture_progress(
            {"capture_id": "cap test/1.2"}
        )

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "invalid_parameter"
        assert fake_state.get_calls == []

    def test_non_dict_params_treated_as_missing_capture_id(
        self, fake_s3, fake_state, bucket_env
    ):
        result = main.handle_get_capture_progress(None)  # type: ignore[arg-type]

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "invalid_parameter"


class TestNotFound:
    def test_unknown_capture_id_returns_not_found(
        self, fake_s3, fake_state, bucket_env
    ):
        result = main.handle_get_capture_progress(
            {"capture_id": "cap-missing"}
        )

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "not_found"
        assert "cap-missing" in result["error"]
        # S3 must not have been called when the row is missing
        # (Req 3.18: the action surfaces ``not_found`` without
        # listing the bucket).
        assert fake_s3.paginator.paginate_calls == []


class TestConfigurationMissing:
    def test_missing_bucket_env_surfaces_configuration_missing(
        self, fake_s3, fake_state, monkeypatch
    ):
        monkeypatch.delenv("DATA_BUCKET_NAME", raising=False)
        fake_state.rows["cap-001"] = _make_row("cap-001")

        result = main.handle_get_capture_progress({"capture_id": "cap-001"})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "configuration_missing"
        # Got past not_found gate but env var missing stops S3 call.
        assert fake_s3.paginator.paginate_calls == []

    def test_state_error_on_get_capture_surfaces_configuration_missing(
        self, fake_s3, fake_state, bucket_env
    ):
        fake_state.get_raises["cap-001"] = state.StateError(
            "Required environment variable 'CAPTURE_STATE_TABLE' is not set."
        )

        result = main.handle_get_capture_progress({"capture_id": "cap-001"})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "configuration_missing"
        assert fake_s3.paginator.paginate_calls == []


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_returns_documented_field_set(
        self, fake_s3, fake_state, bucket_env
    ):
        # Per Req 3.17, the response data must include:
        # capture_id, status, start_time, deadline,
        # time_remaining_seconds, s3_objects_uploaded_count,
        # bytes_uploaded.
        row = _make_row("cap-001", status="active")
        fake_state.rows["cap-001"] = row
        fake_s3.set_pages([
            {"Contents": [
                _s3_object("raw/cap-001/01.pcap", 1024),
                _s3_object("raw/cap-001/02.pcap", 2048),
            ]},
        ])

        result = main.handle_get_capture_progress({"capture_id": "cap-001"})

        assert result["success"] is True
        assert result["domain"] == "network"
        assert result["metadata"]["sourceApi"] == "s3:ListObjectsV2"

        data = result["data"]
        assert data["capture_id"] == "cap-001"
        assert data["status"] == "active"
        assert data["start_time"] == row["start_time"]
        assert data["deadline"] == row["deadline"]
        assert data["s3_objects_uploaded_count"] == 2
        assert data["bytes_uploaded"] == 1024 + 2048
        # ``time_remaining_seconds`` is positive for a future
        # deadline; the row default puts it ~5 minutes ahead.
        assert isinstance(data["time_remaining_seconds"], float)
        assert data["time_remaining_seconds"] > 0

    def test_lists_correct_bucket_and_prefix(
        self, fake_s3, fake_state, bucket_env
    ):
        fake_state.rows["cap-bucket-test"] = _make_row("cap-bucket-test")

        main.handle_get_capture_progress({"capture_id": "cap-bucket-test"})

        # Exactly one paginate call to the right bucket and prefix.
        assert len(fake_s3.paginator.paginate_calls) == 1
        call = fake_s3.paginator.paginate_calls[0]
        assert call["Bucket"] == "goat-network-data-test"
        assert call["Prefix"] == "raw/cap-bucket-test/"

    def test_aggregates_across_paginated_results(
        self, fake_s3, fake_state, bucket_env
    ):
        fake_state.rows["cap-paged"] = _make_row("cap-paged")
        fake_s3.set_pages([
            {"Contents": [
                _s3_object("raw/cap-paged/01.pcap", 100),
                _s3_object("raw/cap-paged/02.pcap", 200),
            ]},
            {"Contents": [
                _s3_object("raw/cap-paged/03.pcap", 300),
            ]},
            # Empty page (legitimate when no objects matched).
            {},
            {"Contents": [
                _s3_object("raw/cap-paged/04.pcap", 400),
            ]},
        ])

        result = main.handle_get_capture_progress({"capture_id": "cap-paged"})

        assert result["success"] is True
        assert result["data"]["s3_objects_uploaded_count"] == 4
        assert result["data"]["bytes_uploaded"] == 100 + 200 + 300 + 400

    def test_zero_objects_when_nothing_uploaded_yet(
        self, fake_s3, fake_state, bucket_env
    ):
        fake_state.rows["cap-empty"] = _make_row("cap-empty")
        # No pages, no Contents — the bucket is empty for this prefix.
        fake_s3.set_pages([])

        result = main.handle_get_capture_progress({"capture_id": "cap-empty"})

        assert result["success"] is True
        assert result["data"]["s3_objects_uploaded_count"] == 0
        assert result["data"]["bytes_uploaded"] == 0

    def test_negative_time_remaining_for_past_deadline(
        self, fake_s3, fake_state, bucket_env
    ):
        # Deadline already in the past — time_remaining_seconds must
        # be negative per Req 3.17.
        past_deadline = (
            datetime.now(timezone.utc) - timedelta(minutes=5)
        ).isoformat()
        fake_state.rows["cap-past"] = _make_row(
            "cap-past", deadline=past_deadline
        )
        fake_s3.set_pages([])

        result = main.handle_get_capture_progress({"capture_id": "cap-past"})

        assert result["success"] is True
        assert result["data"]["time_remaining_seconds"] < 0
        # The formattedText should mention that the deadline has passed.
        assert "deadline passed" in result["formattedText"]

    def test_z_suffix_deadline_parsed(
        self, fake_s3, fake_state, bucket_env
    ):
        # Defensive: deadlines emitted with the legacy "Z" suffix
        # should still parse correctly. This guards against rows
        # written by older code paths.
        future = datetime.now(timezone.utc) + timedelta(minutes=10)
        z_deadline = future.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        fake_state.rows["cap-z"] = _make_row("cap-z", deadline=z_deadline)
        fake_s3.set_pages([])

        result = main.handle_get_capture_progress({"capture_id": "cap-z"})

        assert result["success"] is True
        assert result["data"]["time_remaining_seconds"] > 0

    def test_unparseable_deadline_yields_none_time_remaining(
        self, fake_s3, fake_state, bucket_env
    ):
        # If the row carries a corrupt deadline, the action does
        # NOT crash — it returns success with ``time_remaining_seconds=None``
        # so the caller can still see the upload counts.
        fake_state.rows["cap-bad-deadline"] = _make_row(
            "cap-bad-deadline", deadline="not an iso string"
        )
        fake_s3.set_pages([
            {"Contents": [_s3_object("raw/cap-bad-deadline/01.pcap", 50)]},
        ])

        result = main.handle_get_capture_progress(
            {"capture_id": "cap-bad-deadline"}
        )

        assert result["success"] is True
        assert result["data"]["time_remaining_seconds"] is None
        assert result["data"]["s3_objects_uploaded_count"] == 1
        assert result["data"]["bytes_uploaded"] == 50

    def test_object_with_zero_size_counted(
        self, fake_s3, fake_state, bucket_env
    ):
        # A pcap with Size=0 is still a valid object — count it but
        # don't add to bytes.
        fake_state.rows["cap-empty-pcap"] = _make_row("cap-empty-pcap")
        fake_s3.set_pages([
            {"Contents": [
                _s3_object("raw/cap-empty-pcap/01.pcap", 0),
                _s3_object("raw/cap-empty-pcap/02.pcap", 100),
            ]},
        ])

        result = main.handle_get_capture_progress(
            {"capture_id": "cap-empty-pcap"}
        )

        assert result["success"] is True
        assert result["data"]["s3_objects_uploaded_count"] == 2
        assert result["data"]["bytes_uploaded"] == 100


# ---------------------------------------------------------------------------
# AWS error paths
# ---------------------------------------------------------------------------


class TestAwsErrors:
    def test_s3_throttling_maps_to_aws_throttled(
        self, fake_s3, fake_state, bucket_env
    ):
        fake_state.rows["cap-001"] = _make_row("cap-001")
        fake_s3.set_pages(
            [], raise_on_paginate=_aws_client_error("ThrottlingException")
        )

        result = main.handle_get_capture_progress({"capture_id": "cap-001"})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "aws_throttled"
        assert result["metadata"]["sourceApi"] == "s3:ListObjectsV2"

    def test_s3_access_denied_maps_to_aws_access_denied(
        self, fake_s3, fake_state, bucket_env
    ):
        fake_state.rows["cap-002"] = _make_row("cap-002")
        fake_s3.set_pages(
            [], raise_on_paginate=_aws_client_error("AccessDeniedException")
        )

        result = main.handle_get_capture_progress({"capture_id": "cap-002"})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "aws_access_denied"

    def test_s3_botocore_error_maps_to_aws_other(
        self, fake_s3, fake_state, bucket_env
    ):
        fake_state.rows["cap-003"] = _make_row("cap-003")
        fake_s3.set_pages([], raise_on_paginate=BotoCoreError())

        result = main.handle_get_capture_progress({"capture_id": "cap-003"})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "aws_other"

    def test_dynamodb_error_on_get_capture_surfaces_aws_category(
        self, fake_s3, fake_state, bucket_env
    ):
        fake_state.get_raises["cap-004"] = _aws_client_error(
            "InternalError", op="GetItem"
        )

        result = main.handle_get_capture_progress({"capture_id": "cap-004"})

        assert result["success"] is False
        assert (
            result["metadata"]["errorCategory"] == "aws_service_unavailable"
        )
        # S3 must not have been listed when DynamoDB failed.
        assert fake_s3.paginator.paginate_calls == []


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


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


@st.composite
def s3_pages(draw):
    """Generate 0..3 pages each with 0..4 objects of size 0..10000."""
    page_count = draw(st.integers(min_value=0, max_value=3))
    pages = []
    for page_idx in range(page_count):
        object_count = draw(st.integers(min_value=0, max_value=4))
        if object_count == 0:
            pages.append({})
            continue
        contents = []
        for i in range(object_count):
            size = draw(st.integers(min_value=0, max_value=10_000))
            contents.append(
                _s3_object(f"raw/test/page{page_idx}-obj{i}.pcap", size)
            )
        pages.append({"Contents": contents})
    return pages


class TestProperties:
    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(capture_id=valid_capture_ids(), pages=s3_pages())
    def test_aggregation_matches_sum_of_object_sizes(
        self, capture_id, pages, fake_s3, fake_state, bucket_env
    ):
        """For every valid capture_id and S3 page set, the response's
        ``s3_objects_uploaded_count`` equals the total Contents count
        and ``bytes_uploaded`` equals the sum of Sizes."""
        # Reset fakes for the next example.
        fake_state.rows.clear()
        fake_state.rows[capture_id] = _make_row(capture_id)
        fake_s3.set_pages(pages)

        result = main.handle_get_capture_progress({"capture_id": capture_id})

        assert result["success"] is True

        expected_count = sum(
            len(page.get("Contents", []) or []) for page in pages
        )
        expected_bytes = sum(
            int(obj.get("Size", 0) or 0)
            for page in pages
            for obj in (page.get("Contents", []) or [])
        )

        assert result["data"]["s3_objects_uploaded_count"] == expected_count
        assert result["data"]["bytes_uploaded"] == expected_bytes

    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(capture_id=valid_capture_ids())
    def test_unknown_capture_id_never_lists_s3(
        self, capture_id, fake_s3, fake_state, bucket_env
    ):
        """For every valid capture_id whose row is absent, the
        handler returns ``not_found`` and S3 is never listed. This is
        the core safety property of Req 3.18 — there must be no S3
        traffic for unknown captures.
        """
        fake_state.rows.clear()
        # Reset the paginator so we can assert no call was made.
        fake_s3.set_pages([])
        fake_s3.paginator.paginate_calls = []

        result = main.handle_get_capture_progress({"capture_id": capture_id})

        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "not_found"
        assert fake_s3.paginator.paginate_calls == []

    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(
        capture_id=valid_capture_ids(),
        seconds_offset=st.integers(min_value=-3600, max_value=3600),
    )
    def test_time_remaining_sign_matches_deadline_relative_to_now(
        self, capture_id, seconds_offset, fake_s3, fake_state, bucket_env
    ):
        """``time_remaining_seconds`` is positive when the deadline is
        in the future, negative when in the past, and approximately
        zero when ``deadline == now``. The exact value is allowed to
        drift by a small amount because the handler captures ``now``
        slightly after the test does."""
        deadline = (
            datetime.now(timezone.utc) + timedelta(seconds=seconds_offset)
        ).isoformat()
        fake_state.rows.clear()
        fake_state.rows[capture_id] = _make_row(capture_id, deadline=deadline)
        fake_s3.set_pages([])

        result = main.handle_get_capture_progress({"capture_id": capture_id})

        assert result["success"] is True
        observed = result["data"]["time_remaining_seconds"]
        # Allow a small tolerance for the time the handler took to
        # invoke ``datetime.now`` after the test set the deadline.
        # The sign assertion is what matters: a future deadline must
        # give a positive value, a past deadline must give a negative
        # value.
        if seconds_offset > 5:
            assert observed > 0
        elif seconds_offset < -5:
            assert observed < 0
        # For the small range around 0, just assert the magnitude is
        # reasonable.
        assert abs(observed - seconds_offset) <= 30
