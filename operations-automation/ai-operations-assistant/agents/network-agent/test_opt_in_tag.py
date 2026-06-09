"""
Unit tests for the Capture_Opt_In_Tag enforcement (Task 7, Req 3.14).

Exercises ``main._check_opt_in_tag`` end-to-end: the function fetches
ENI tags via ``ec2:DescribeNetworkInterfaces`` and (when the ENI is
attached) instance tags via ``ec2:DescribeInstances`` and rejects the
request unless every requested ENI carries
``goat-network-capture-allowed=true`` on either itself or its parent
EC2 instance.

The tests use a small in-memory fake EC2 client that implements only
the two API operations the function calls. This keeps the test suite
hermetic (no AWS calls, no ``moto`` dependency) while still validating
the real code path including the EH-1 error category mapping documented
in the design.

Run from the ``network-agent`` directory:

    python -m pytest test_opt_in_tag.py -v
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pytest
from botocore.exceptions import ClientError

import main
from validation import ValidationError


# ---------------------------------------------------------------------------
# Fake EC2 client
# ---------------------------------------------------------------------------


class _FakeEC2:
    """In-memory EC2 stand-in for ``describe_network_interfaces`` and
    ``describe_instances``.

    The fake stores an ENI-by-ID dict and an instance-by-ID dict and
    returns AWS-shaped responses for the two methods the function
    under test calls. Tests can register a ClientError to be raised on
    the next call by setting ``raise_on_describe_enis`` or
    ``raise_on_describe_instances``.
    """

    def __init__(
        self,
        enis: Dict[str, dict],
        instances: Optional[Dict[str, dict]] = None,
    ) -> None:
        self._enis = enis
        self._instances = instances or {}
        self.raise_on_describe_enis: Optional[Exception] = None
        self.raise_on_describe_instances: Optional[Exception] = None
        self.describe_eni_calls: List[List[str]] = []
        self.describe_instance_calls: List[List[str]] = []

    def describe_network_interfaces(self, NetworkInterfaceIds: List[str]) -> dict:
        if self.raise_on_describe_enis is not None:
            raise self.raise_on_describe_enis
        self.describe_eni_calls.append(list(NetworkInterfaceIds))
        return {
            "NetworkInterfaces": [
                self._enis[eid] for eid in NetworkInterfaceIds if eid in self._enis
            ]
        }

    def describe_instances(self, InstanceIds: List[str]) -> dict:
        if self.raise_on_describe_instances is not None:
            raise self.raise_on_describe_instances
        self.describe_instance_calls.append(list(InstanceIds))
        return {
            "Reservations": [
                {
                    "Instances": [
                        self._instances[iid]
                        for iid in InstanceIds
                        if iid in self._instances
                    ]
                }
            ]
        }


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _eni(
    eni_id: str,
    *,
    tags: Optional[Dict[str, str]] = None,
    instance_id: Optional[str] = None,
) -> dict:
    """Build an AWS-shaped ENI record."""
    record = {
        "NetworkInterfaceId": eni_id,
        "TagSet": [
            {"Key": k, "Value": v} for k, v in (tags or {}).items()
        ],
    }
    if instance_id is not None:
        record["Attachment"] = {"InstanceId": instance_id, "Status": "attached"}
    return record


def _instance(instance_id: str, *, tags: Optional[Dict[str, str]] = None) -> dict:
    """Build an AWS-shaped Instance record."""
    return {
        "InstanceId": instance_id,
        "Tags": [
            {"Key": k, "Value": v} for k, v in (tags or {}).items()
        ],
    }


@pytest.fixture(autouse=True)
def reset_ec2_singleton(monkeypatch):
    """Force ``main._get_ec2_client`` to use the fake injected per-test.

    The Network Agent caches its boto3 clients in module-level globals
    so the cold-start cost is paid once per container. Tests inject a
    fresh fake by setting ``main._ec2_client`` and resetting it at
    teardown so cases stay isolated.
    """
    monkeypatch.setattr(main, "_ec2_client", None)
    yield
    monkeypatch.setattr(main, "_ec2_client", None)


def _install_fake(monkeypatch, fake: _FakeEC2) -> None:
    """Install the supplied fake as the cached EC2 client."""
    monkeypatch.setattr(main, "_ec2_client", fake)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestPermitsTaggedEni:
    """ENIs (or their parent instances) carrying the opt-in tag are accepted."""

    def test_eni_with_tag_on_eni_itself(self, monkeypatch):
        fake = _FakeEC2(
            enis={
                "eni-aaaa1111": _eni(
                    "eni-aaaa1111",
                    tags={"goat-network-capture-allowed": "true"},
                ),
            }
        )
        _install_fake(monkeypatch, fake)

        # Should not raise.
        main._check_opt_in_tag(["eni-aaaa1111"])

    def test_eni_unattached_with_tag(self, monkeypatch):
        # Unattached ENIs cannot carry an instance — the tag must be on
        # the ENI itself.
        fake = _FakeEC2(
            enis={
                "eni-bbbb2222": _eni(
                    "eni-bbbb2222",
                    tags={"goat-network-capture-allowed": "true"},
                    instance_id=None,
                ),
            }
        )
        _install_fake(monkeypatch, fake)

        main._check_opt_in_tag(["eni-bbbb2222"])

    def test_eni_inherits_tag_from_parent_instance(self, monkeypatch):
        # The ENI itself has no tag, but its parent EC2 instance does.
        # Req 3.14 explicitly permits this configuration.
        fake = _FakeEC2(
            enis={
                "eni-cccc3333": _eni(
                    "eni-cccc3333",
                    tags={"Environment": "demo"},
                    instance_id="i-1111aaaa",
                ),
            },
            instances={
                "i-1111aaaa": _instance(
                    "i-1111aaaa",
                    tags={"goat-network-capture-allowed": "true"},
                ),
            },
        )
        _install_fake(monkeypatch, fake)

        main._check_opt_in_tag(["eni-cccc3333"])

    def test_multiple_enis_all_tagged(self, monkeypatch):
        fake = _FakeEC2(
            enis={
                "eni-1111aaaa": _eni(
                    "eni-1111aaaa",
                    tags={"goat-network-capture-allowed": "true"},
                ),
                "eni-2222bbbb": _eni(
                    "eni-2222bbbb",
                    tags={"goat-network-capture-allowed": "true"},
                    instance_id="i-zzzz9999",
                ),
                "eni-3333cccc": _eni(
                    "eni-3333cccc",
                    instance_id="i-yyyy8888",
                ),
            },
            instances={
                "i-zzzz9999": _instance("i-zzzz9999"),
                "i-yyyy8888": _instance(
                    "i-yyyy8888",
                    tags={"goat-network-capture-allowed": "true"},
                ),
            },
        )
        _install_fake(monkeypatch, fake)

        main._check_opt_in_tag(["eni-1111aaaa", "eni-2222bbbb", "eni-3333cccc"])

    def test_describe_instances_called_only_when_attachments_exist(
        self, monkeypatch
    ):
        # An entirely unattached set of ENIs should not trigger a
        # DescribeInstances call.
        fake = _FakeEC2(
            enis={
                "eni-aaaa0000": _eni(
                    "eni-aaaa0000",
                    tags={"goat-network-capture-allowed": "true"},
                ),
            }
        )
        _install_fake(monkeypatch, fake)

        main._check_opt_in_tag(["eni-aaaa0000"])

        assert fake.describe_eni_calls == [["eni-aaaa0000"]]
        assert fake.describe_instance_calls == []

    def test_describe_instances_deduplicates_parent_ids(self, monkeypatch):
        # Two ENIs on the same instance should only produce one
        # DescribeInstances call referencing that instance once.
        fake = _FakeEC2(
            enis={
                "eni-1111aaaa": _eni(
                    "eni-1111aaaa", instance_id="i-shared001"
                ),
                "eni-2222bbbb": _eni(
                    "eni-2222bbbb", instance_id="i-shared001"
                ),
            },
            instances={
                "i-shared001": _instance(
                    "i-shared001",
                    tags={"goat-network-capture-allowed": "true"},
                ),
            },
        )
        _install_fake(monkeypatch, fake)

        main._check_opt_in_tag(["eni-1111aaaa", "eni-2222bbbb"])

        assert fake.describe_instance_calls == [["i-shared001"]]


# ---------------------------------------------------------------------------
# Rejection paths (the core of Task 7)
# ---------------------------------------------------------------------------


class TestRejectsUntaggedEni:
    """ENIs without the opt-in tag (on either ENI or instance) are rejected."""

    def test_unattached_eni_without_tag(self, monkeypatch):
        fake = _FakeEC2(
            enis={"eni-aaaa1111": _eni("eni-aaaa1111")},
        )
        _install_fake(monkeypatch, fake)

        with pytest.raises(ValidationError) as excinfo:
            main._check_opt_in_tag(["eni-aaaa1111"])

        # The error category must match the design's EH-1 mapping.
        assert excinfo.value.error_category == "unauthorized"
        # The message must name the offending ENI and the missing tag.
        assert "eni-aaaa1111" in excinfo.value.message
        assert "goat-network-capture-allowed=true" in excinfo.value.message

    def test_attached_eni_without_tag_on_either_surface(self, monkeypatch):
        fake = _FakeEC2(
            enis={
                "eni-bbbb2222": _eni(
                    "eni-bbbb2222", instance_id="i-naked0001"
                ),
            },
            instances={
                "i-naked0001": _instance(
                    "i-naked0001", tags={"Environment": "demo"}
                ),
            },
        )
        _install_fake(monkeypatch, fake)

        with pytest.raises(ValidationError) as excinfo:
            main._check_opt_in_tag(["eni-bbbb2222"])

        assert excinfo.value.error_category == "unauthorized"
        assert "eni-bbbb2222" in excinfo.value.message
        assert "goat-network-capture-allowed=true" in excinfo.value.message

    def test_tag_with_wrong_value_is_rejected(self, monkeypatch):
        # Only the literal value "true" satisfies the check. "yes",
        # "True", "1", and similar truthy aliases must be rejected.
        for bad_value in ("yes", "True", "TRUE", "1", "enabled", "false"):
            fake = _FakeEC2(
                enis={
                    "eni-cccc3333": _eni(
                        "eni-cccc3333",
                        tags={"goat-network-capture-allowed": bad_value},
                    ),
                }
            )
            _install_fake(monkeypatch, fake)

            with pytest.raises(ValidationError) as excinfo:
                main._check_opt_in_tag(["eni-cccc3333"])

            assert excinfo.value.error_category == "unauthorized", (
                f"Tag value {bad_value!r} should not satisfy the opt-in check"
            )

    def test_tag_with_wrong_key_is_rejected(self, monkeypatch):
        # Only the literal key "goat-network-capture-allowed" satisfies
        # the check. Variants must not.
        for bad_key in (
            "Goat-Network-Capture-Allowed",
            "goat_network_capture_allowed",
            "goat-capture-allowed",
        ):
            fake = _FakeEC2(
                enis={
                    "eni-dddd4444": _eni(
                        "eni-dddd4444", tags={bad_key: "true"}
                    ),
                }
            )
            _install_fake(monkeypatch, fake)

            with pytest.raises(ValidationError) as excinfo:
                main._check_opt_in_tag(["eni-dddd4444"])

            assert excinfo.value.error_category == "unauthorized", (
                f"Tag key {bad_key!r} should not satisfy the opt-in check"
            )

    def test_lists_all_offending_enis_in_one_message(self, monkeypatch):
        # When the user supplies multiple ENIs and several lack the tag,
        # the error message lists every offender so the user can fix
        # them in a single round trip.
        fake = _FakeEC2(
            enis={
                "eni-aaaaaaaa": _eni(
                    "eni-aaaaaaaa",
                    tags={"goat-network-capture-allowed": "true"},
                ),
                "eni-bbbbbbbb": _eni("eni-bbbbbbbb"),
                "eni-cccccccc": _eni("eni-cccccccc"),
            }
        )
        _install_fake(monkeypatch, fake)

        with pytest.raises(ValidationError) as excinfo:
            main._check_opt_in_tag(
                ["eni-aaaaaaaa", "eni-bbbbbbbb", "eni-cccccccc"]
            )

        assert excinfo.value.error_category == "unauthorized"
        # Both offenders named, the tagged ENI not named.
        assert "eni-bbbbbbbb" in excinfo.value.message
        assert "eni-cccccccc" in excinfo.value.message
        assert "eni-aaaaaaaa" not in excinfo.value.message
        assert "goat-network-capture-allowed=true" in excinfo.value.message


# ---------------------------------------------------------------------------
# AWS surface: missing ENIs and propagated client errors
# ---------------------------------------------------------------------------


class TestAwsSurface:
    """The function surfaces AWS conditions correctly."""

    def test_missing_eni_raises_invalid_parameter(self, monkeypatch):
        # AWS may return fewer ENIs than requested if the caller named
        # an identifier that does not exist in the account or that the
        # runtime role cannot see. This is a caller-fault condition,
        # categorized as ``invalid_parameter`` rather than
        # ``unauthorized``.
        fake = _FakeEC2(enis={})
        _install_fake(monkeypatch, fake)

        with pytest.raises(ValidationError) as excinfo:
            main._check_opt_in_tag(["eni-zzzz9999"])

        assert excinfo.value.error_category == "invalid_parameter"
        assert "eni-zzzz9999" in excinfo.value.message

    def test_partial_missing_set_lists_only_missing(self, monkeypatch):
        # When some ENIs exist and others do not, the missing-ENI
        # error should be raised first (and list only the missing
        # identifiers).
        fake = _FakeEC2(
            enis={
                "eni-present0": _eni(
                    "eni-present0",
                    tags={"goat-network-capture-allowed": "true"},
                ),
            }
        )
        _install_fake(monkeypatch, fake)

        with pytest.raises(ValidationError) as excinfo:
            main._check_opt_in_tag(["eni-present0", "eni-missing1"])

        assert excinfo.value.error_category == "invalid_parameter"
        assert "eni-missing1" in excinfo.value.message
        assert "eni-present0" not in excinfo.value.message

    def test_describe_eni_client_error_propagates(self, monkeypatch):
        fake = _FakeEC2(enis={})
        fake.raise_on_describe_enis = ClientError(
            error_response={
                "Error": {
                    "Code": "RequestLimitExceeded",
                    "Message": "Throttled",
                }
            },
            operation_name="DescribeNetworkInterfaces",
        )
        _install_fake(monkeypatch, fake)

        with pytest.raises(ClientError) as excinfo:
            main._check_opt_in_tag(["eni-aaaa1111"])

        assert (
            excinfo.value.response["Error"]["Code"] == "RequestLimitExceeded"
        )

    def test_describe_instances_client_error_propagates(self, monkeypatch):
        fake = _FakeEC2(
            enis={
                "eni-aaaa1111": _eni(
                    "eni-aaaa1111", instance_id="i-cccc3333"
                ),
            }
        )
        fake.raise_on_describe_instances = ClientError(
            error_response={
                "Error": {
                    "Code": "AccessDeniedException",
                    "Message": "User is not authorized",
                }
            },
            operation_name="DescribeInstances",
        )
        _install_fake(monkeypatch, fake)

        with pytest.raises(ClientError) as excinfo:
            main._check_opt_in_tag(["eni-aaaa1111"])

        assert (
            excinfo.value.response["Error"]["Code"]
            == "AccessDeniedException"
        )


# ---------------------------------------------------------------------------
# Integration with handle_start_capture rejection envelope
# ---------------------------------------------------------------------------


class TestStartCaptureIntegration:
    """The opt-in failure surfaces correctly through ``handle_start_capture``.

    These tests exercise the full ``handle_start_capture`` path far
    enough to confirm that an opt-in failure is reported as the
    documented response envelope:

    - ``success`` is ``False``.
    - ``metadata.errorCategory`` is ``"unauthorized"``.
    - The error message names the offending ENI and the missing tag.

    They patch ``state`` so we don't need a DynamoDB fake; the path
    short-circuits on the opt-in check before any DDB write.
    """

    def test_start_capture_returns_unauthorized_envelope(
        self, monkeypatch
    ):
        # No active captures so the concurrency check passes.
        monkeypatch.setattr(
            "state.query_active_captures", lambda: []
        )

        fake = _FakeEC2(
            enis={
                "eni-aaaa1111": _eni("eni-aaaa1111"),
            }
        )
        _install_fake(monkeypatch, fake)

        response = main.handle_start_capture(
            {
                "eni_ids": ["eni-aaaa1111"],
                "duration_minutes": 15,
                "filter_id": "tmf-1234",
            }
        )

        assert response["success"] is False
        assert response["domain"] == "network"
        assert response["metadata"]["errorCategory"] == "unauthorized"
        # source_api stays ``ec2:CreateTrafficMirrorSession`` per the
        # Task 6 instructions — the rejection happens before the
        # session-create call but we attribute the action to its
        # primary AWS surface.
        assert (
            response["metadata"]["sourceApi"]
            == "ec2:CreateTrafficMirrorSession"
        )
        assert "eni-aaaa1111" in response["formattedText"]
        assert (
            "goat-network-capture-allowed=true" in response["formattedText"]
        )

    def test_start_capture_does_not_create_mirror_sessions_on_rejection(
        self, monkeypatch
    ):
        # Track whether CreateTrafficMirrorSession was ever called by
        # extending the fake; the function under test must NOT create
        # any sessions when the opt-in check fails (Req 3.2 / 3.14).
        monkeypatch.setattr(
            "state.query_active_captures", lambda: []
        )

        class _RecordingFakeEC2(_FakeEC2):
            def __init__(self, enis, instances=None):
                super().__init__(enis, instances)
                self.create_mirror_calls = 0

            def create_traffic_mirror_session(self, **_kwargs):
                self.create_mirror_calls += 1
                return {"TrafficMirrorSession": {}}

        fake = _RecordingFakeEC2(
            enis={
                "eni-aaaa1111": _eni("eni-aaaa1111"),
            }
        )
        _install_fake(monkeypatch, fake)

        response = main.handle_start_capture(
            {
                "eni_ids": ["eni-aaaa1111"],
                "duration_minutes": 15,
                "filter_id": "tmf-1234",
            }
        )

        assert response["success"] is False
        assert fake.create_mirror_calls == 0
