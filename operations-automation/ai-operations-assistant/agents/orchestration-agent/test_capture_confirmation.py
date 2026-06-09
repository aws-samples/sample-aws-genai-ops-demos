"""
Unit and property-based tests for the orchestration agent's
instance-to-ENI resolution and Capture_Confirmation_Prompt helpers
introduced by Task 35.

Run from the ``orchestration-agent`` directory::

    python -m pytest test_capture_confirmation.py -v

Scope:

- ``compute_capture_cost_usd`` — match the design's cost formula and
  the values in ``prices.json``.
- ``estimate_capture_bytes`` — match the documented heuristic.
- ``derive_capture_idempotency_token`` — deterministic SHA-256 over
  ``eni_ids ∥ duration_minutes ∥ user_id ∥ floor(timestamp, 1m)``.
- ``format_capture_confirmation_prompt`` — every required line of the
  prompt (bulleted ENIs, duration with " (default)" suffix when the
  15-minute default applies, estimated cost, yes/no closer).
- ``is_affirmative_response`` / ``is_negative_response`` — match the
  Affirmative_Response_Set and Negative_Response_Set tokens.
- ``prepare_capture_confirmation`` (the ``@tool``) — orchestrates the
  helpers and returns a JSON envelope the LLM consumes.
- JSON parity — ``prices.json`` agrees with the values referenced by
  the helpers so the Capture_Confirmation_Prompt cannot drift from
  the README cost-estimate table.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import string
from datetime import datetime, timezone
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

# AgentCore's BedrockAgentCoreApp constructor in main.py imports the
# bedrock-agentcore SDK, which expects a region. Set both env vars
# before importing main so the module loads cleanly outside the
# AgentCore runtime (mirrors the pattern in
# test_capture_authorization.py).
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import main  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _formula(
    eni_count: int,
    duration_minutes: int,
    *,
    price_per_eni_hour: float,
    price_per_gb: float,
    estimated_bytes: int = None,
) -> float:
    """Reference reimplementation of the design's cost formula."""
    duration_hours = duration_minutes / 60.0
    if estimated_bytes is None:
        estimated_bytes = eni_count * duration_minutes * 60 * 125_000
    return (
        eni_count * duration_hours * price_per_eni_hour
        + (estimated_bytes / 1e9) * price_per_gb
    )


def _prepare_callable():
    """Return the underlying Python function behind the ``@tool`` decorator."""
    tool_obj = main.prepare_capture_confirmation
    for attr in ("original_function", "_function", "function", "fn"):
        candidate = getattr(tool_obj, attr, None)
        if callable(candidate):
            return candidate
    if callable(tool_obj):
        return tool_obj
    raise RuntimeError(
        "Could not resolve underlying callable for prepare_capture_confirmation"
    )


# ---------------------------------------------------------------------------
# JSON parity — bundled prices.json must match the agents/shared canonical
# ---------------------------------------------------------------------------


class TestPricesJsonParity:
    """The bundled ``prices.json`` mirrors ``agents/shared/prices.json``.

    The orchestration agent loads its own copy of the shared price
    table at container build time. The two copies must stay in lockstep
    so the chat confirmation cost (orchestration agent) and the README
    cost-estimate table (which reads ``agents/shared/prices.json``) do
    not drift.
    """

    @pytest.fixture(scope="class")
    def local_prices(self):
        path = Path(main.__file__).parent / "prices.json"
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    @pytest.fixture(scope="class")
    def shared_prices(self):
        # ``agents/shared/prices.json`` lives one directory up and over.
        path = (
            Path(main.__file__).parent.parent / "shared" / "prices.json"
        )
        if not path.exists():
            pytest.skip(
                "agents/shared/prices.json missing — run from a checkout "
                "that includes the shared module"
            )
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    def test_currency_matches(self, local_prices, shared_prices):
        """Validates: Requirements 14.2, 17.2."""
        assert local_prices["currency"] == shared_prices["currency"]

    def test_traffic_mirror_default_eni_hour_price_matches(
        self, local_prices, shared_prices
    ):
        """Validates: Requirements 14.2, 17.2."""
        assert (
            local_prices["trafficMirror"]["eniHourPriceDefault"]
            == shared_prices["trafficMirror"]["eniHourPriceDefault"]
        )

    def test_traffic_mirror_data_price_per_gb_matches(
        self, local_prices, shared_prices
    ):
        """Validates: Requirements 14.2, 17.2."""
        assert (
            local_prices["trafficMirror"]["dataPricePerGb"]
            == shared_prices["trafficMirror"]["dataPricePerGb"]
        )

    def test_heuristic_matches(self, local_prices, shared_prices):
        """Validates: Requirements 14.2, 17.2."""
        assert (
            local_prices["heuristic"]["mbpsPerEni"]
            == shared_prices["heuristic"]["mbpsPerEni"]
        )
        assert (
            local_prices["heuristic"]["bytesPerSecondPerMbps"]
            == shared_prices["heuristic"]["bytesPerSecondPerMbps"]
        )

    def test_regional_eni_hour_prices_are_identical(
        self, local_prices, shared_prices
    ):
        """Validates: Requirements 14.2, 17.2."""
        local_table = local_prices["trafficMirror"]["eniHourPriceByRegion"]
        shared_table = shared_prices["trafficMirror"]["eniHourPriceByRegion"]
        assert set(local_table.keys()) == set(shared_table.keys()), (
            "Region key set drift between orchestration-agent prices.json "
            "and agents/shared/prices.json"
        )
        for region in shared_table:
            assert local_table[region] == shared_table[region], (
                f"Drift detected for region {region}: "
                f"local={local_table[region]} shared={shared_table[region]}"
            )


# ---------------------------------------------------------------------------
# estimate_capture_bytes
# ---------------------------------------------------------------------------


class TestEstimateCaptureBytes:
    def test_zero_enis_yields_zero_bytes(self):
        assert main.estimate_capture_bytes(0, 15) == 0

    def test_zero_duration_yields_zero_bytes(self):
        assert main.estimate_capture_bytes(3, 0) == 0

    def test_one_eni_one_minute_matches_documented_heuristic(self):
        # 1 ENI * 1 min * 60 s/min * 125_000 B/s = 7_500_000
        assert main.estimate_capture_bytes(1, 1) == 7_500_000

    def test_three_enis_fifteen_minutes_matches_documented_heuristic(self):
        # 3 * 15 * 60 * 125_000 = 337_500_000
        assert main.estimate_capture_bytes(3, 15) == 337_500_000


# ---------------------------------------------------------------------------
# compute_capture_cost_usd — unit tests
# ---------------------------------------------------------------------------


class TestComputeCaptureCostUsd:
    def test_zero_enis_zero_cost(self):
        assert main.compute_capture_cost_usd(0, 60) == 0.0

    def test_zero_duration_zero_cost(self):
        assert main.compute_capture_cost_usd(3, 0) == 0.0

    def test_one_eni_15_minutes_matches_documented_formula(self):
        """Hand-computed: 1 ENI * 15 min, default heuristic.

        eni_hours_cost = 1 * (15/60) * 0.015 = 0.00375
        estimated_bytes = 1 * 15 * 60 * 125_000 = 112_500_000
        data_cost = (112_500_000 / 1e9) * 0.015 = 0.0016875
        total ~= 0.0054375
        """
        result = main.compute_capture_cost_usd(1, 15, region="us-east-1")
        assert result == pytest.approx(0.0054375, rel=1e-9)

    def test_three_enis_60_minutes_matches_documented_formula(self):
        """3 ENIs, 60 min, default heuristic.

        eni_hours_cost = 3 * 1.0 * 0.015 = 0.045
        estimated_bytes = 3 * 60 * 60 * 125_000 = 1_350_000_000
        data_cost = (1_350_000_000 / 1e9) * 0.015 = 0.02025
        total = 0.06525
        """
        result = main.compute_capture_cost_usd(3, 60, region="eu-west-1")
        assert result == pytest.approx(0.06525, rel=1e-9)

    def test_estimated_bytes_override_is_used(self):
        result = main.compute_capture_cost_usd(
            1, 60, region="us-east-1", estimated_bytes=1_000_000_000
        )
        # 1 * 1.0 * 0.015 = 0.015 + (1e9 / 1e9) * 0.015 = 0.015 -> 0.030
        assert result == pytest.approx(0.030, rel=1e-9)

    def test_unknown_region_falls_back_to_default(self):
        a = main.compute_capture_cost_usd(2, 30, region="xx-fake-1")
        b = main.compute_capture_cost_usd(2, 30, region=None)
        assert a == b

    def test_negative_eni_count_raises(self):
        with pytest.raises(ValueError):
            main.compute_capture_cost_usd(-1, 15)

    def test_negative_duration_raises(self):
        with pytest.raises(ValueError):
            main.compute_capture_cost_usd(1, -1)

    def test_negative_estimated_bytes_raises(self):
        with pytest.raises(ValueError):
            main.compute_capture_cost_usd(1, 15, estimated_bytes=-1)


# ---------------------------------------------------------------------------
# derive_capture_idempotency_token — unit tests
# ---------------------------------------------------------------------------


class TestDeriveCaptureIdempotencyToken:
    def test_returns_64_char_hex_digest(self):
        ts = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)
        token = main.derive_capture_idempotency_token(
            ["eni-aaaa1111"], 15, user_id="alice", timestamp=ts
        )
        assert len(token) == 64
        assert re.fullmatch(r"[0-9a-f]{64}", token)

    def test_eni_order_does_not_change_token(self):
        ts = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)
        a = main.derive_capture_idempotency_token(
            ["eni-aaaa1111", "eni-bbbb2222"], 15, user_id="alice", timestamp=ts
        )
        b = main.derive_capture_idempotency_token(
            ["eni-bbbb2222", "eni-aaaa1111"], 15, user_id="alice", timestamp=ts
        )
        assert a == b

    def test_seconds_within_same_minute_are_dropped(self):
        a = main.derive_capture_idempotency_token(
            ["eni-aaaa1111"],
            15,
            user_id="alice",
            timestamp=datetime(2026, 4, 20, 12, 30, 5, tzinfo=timezone.utc),
        )
        b = main.derive_capture_idempotency_token(
            ["eni-aaaa1111"],
            15,
            user_id="alice",
            timestamp=datetime(2026, 4, 20, 12, 30, 59, tzinfo=timezone.utc),
        )
        assert a == b

    def test_different_minutes_produce_different_tokens(self):
        a = main.derive_capture_idempotency_token(
            ["eni-aaaa1111"],
            15,
            user_id="alice",
            timestamp=datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc),
        )
        b = main.derive_capture_idempotency_token(
            ["eni-aaaa1111"],
            15,
            user_id="alice",
            timestamp=datetime(2026, 4, 20, 12, 31, 0, tzinfo=timezone.utc),
        )
        assert a != b

    def test_different_users_produce_different_tokens(self):
        ts = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)
        a = main.derive_capture_idempotency_token(
            ["eni-aaaa1111"], 15, user_id="alice", timestamp=ts
        )
        b = main.derive_capture_idempotency_token(
            ["eni-aaaa1111"], 15, user_id="bob", timestamp=ts
        )
        assert a != b

    def test_different_durations_produce_different_tokens(self):
        ts = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)
        a = main.derive_capture_idempotency_token(
            ["eni-aaaa1111"], 15, user_id="alice", timestamp=ts
        )
        b = main.derive_capture_idempotency_token(
            ["eni-aaaa1111"], 30, user_id="alice", timestamp=ts
        )
        assert a != b

    def test_user_id_falls_back_to_context_var(self):
        ts = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)
        token_var = main._CURRENT_USER_ID.set("alice-from-ctxvar")
        try:
            from_ctxvar = main.derive_capture_idempotency_token(
                ["eni-aaaa1111"], 15, timestamp=ts
            )
        finally:
            main._CURRENT_USER_ID.reset(token_var)
        explicit = main.derive_capture_idempotency_token(
            ["eni-aaaa1111"], 15, user_id="alice-from-ctxvar", timestamp=ts
        )
        assert from_ctxvar == explicit

    def test_naive_timestamp_is_treated_as_utc(self):
        ts_naive = datetime(2026, 4, 20, 12, 30, 0)
        ts_aware = ts_naive.replace(tzinfo=timezone.utc)
        a = main.derive_capture_idempotency_token(
            ["eni-aaaa1111"], 15, user_id="alice", timestamp=ts_naive
        )
        b = main.derive_capture_idempotency_token(
            ["eni-aaaa1111"], 15, user_id="alice", timestamp=ts_aware
        )
        assert a == b

    def test_empty_eni_ids_raises(self):
        with pytest.raises(ValueError):
            main.derive_capture_idempotency_token([], 15, user_id="alice")

    def test_non_string_eni_raises(self):
        with pytest.raises(ValueError):
            main.derive_capture_idempotency_token([123], 15, user_id="alice")

    def test_negative_duration_raises(self):
        with pytest.raises(ValueError):
            main.derive_capture_idempotency_token(
                ["eni-aaaa1111"], -1, user_id="alice"
            )

    def test_duration_bool_raises(self):
        with pytest.raises(ValueError):
            main.derive_capture_idempotency_token(
                ["eni-aaaa1111"], True, user_id="alice"
            )


# ---------------------------------------------------------------------------
# format_capture_confirmation_prompt — unit tests
# ---------------------------------------------------------------------------


class TestFormatCaptureConfirmationPrompt:
    def test_prompt_includes_bulleted_eni_list(self):
        result = main.format_capture_confirmation_prompt(
            [
                {"eni_id": "eni-aaaa1111", "attached_instance_id": "i-1234abcd"},
                {"eni_id": "eni-bbbb2222", "attached_instance_id": None},
            ],
            duration_minutes=15,
        )
        assert "- `eni-aaaa1111` (attached to `i-1234abcd`)" in result["prompt_text"]
        assert "- `eni-bbbb2222`" in result["prompt_text"]

    def test_prompt_includes_default_suffix_when_duration_is_none(self):
        result = main.format_capture_confirmation_prompt(
            [{"eni_id": "eni-aaaa1111"}], duration_minutes=None
        )
        assert "Duration**: 15 minutes (default)" in result["prompt_text"]
        assert result["metadata"]["applied_default_15"] is True
        assert result["metadata"]["duration_minutes"] == 15

    def test_prompt_omits_default_suffix_when_user_supplies_duration(self):
        result = main.format_capture_confirmation_prompt(
            [{"eni_id": "eni-aaaa1111"}], duration_minutes=30
        )
        assert "Duration**: 30 minutes" in result["prompt_text"]
        assert "(default)" not in result["prompt_text"]
        assert result["metadata"]["applied_default_15"] is False
        assert result["metadata"]["duration_minutes"] == 30

    def test_prompt_includes_estimated_cost_in_usd(self):
        result = main.format_capture_confirmation_prompt(
            [{"eni_id": "eni-aaaa1111"}], duration_minutes=15, region="us-east-1"
        )
        # The cost line uses 4-decimal formatting and the USD label.
        assert "USD" in result["prompt_text"]
        assert re.search(
            r"\*\*Estimated cost\*\*: \$\d+\.\d{4} USD", result["prompt_text"]
        )

    def test_prompt_ends_with_yes_no_question(self):
        result = main.format_capture_confirmation_prompt(
            [{"eni_id": "eni-aaaa1111"}], duration_minutes=15
        )
        assert result["prompt_text"].endswith(
            "Reply 'yes' to start the capture or 'no' to cancel."
        )

    def test_metadata_estimated_cost_matches_compute_capture_cost(self):
        result = main.format_capture_confirmation_prompt(
            [{"eni_id": "eni-aaaa1111"}, {"eni_id": "eni-bbbb2222"}],
            duration_minutes=20,
            region="us-east-1",
        )
        expected = round(
            main.compute_capture_cost_usd(2, 20, region="us-east-1"), 4
        )
        assert result["metadata"]["estimated_cost_usd"] == expected

    def test_empty_eni_list_raises(self):
        with pytest.raises(ValueError):
            main.format_capture_confirmation_prompt([], duration_minutes=15)

    def test_missing_eni_id_raises(self):
        with pytest.raises(ValueError):
            main.format_capture_confirmation_prompt(
                [{"foo": "bar"}], duration_minutes=15
            )

    def test_duration_above_60_raises(self):
        with pytest.raises(ValueError):
            main.format_capture_confirmation_prompt(
                [{"eni_id": "eni-aaaa1111"}], duration_minutes=61
            )

    def test_duration_below_1_raises(self):
        with pytest.raises(ValueError):
            main.format_capture_confirmation_prompt(
                [{"eni_id": "eni-aaaa1111"}], duration_minutes=0
            )


# ---------------------------------------------------------------------------
# is_affirmative_response / is_negative_response — unit tests
# ---------------------------------------------------------------------------


class TestAffirmativeResponseSet:
    @pytest.mark.parametrize(
        "value",
        ["yes", "y", "ok", "okay", "sure", "confirm", "proceed", "go", "accept"],
    )
    def test_documented_tokens_are_affirmative(self, value):
        assert main.is_affirmative_response(value) is True

    @pytest.mark.parametrize(
        "value", ["YES", "Y", "Ok", "OkAy", "  yes  ", "yes!", "yes.", "yes,"]
    )
    def test_case_whitespace_punctuation_normalized(self, value):
        assert main.is_affirmative_response(value) is True

    @pytest.mark.parametrize(
        "value",
        ["no", "n", "cancel", "abort", "stop", "nevermind", "NO", "  no  ", "no."],
    )
    def test_documented_negative_tokens_are_negative(self, value):
        assert main.is_negative_response(value) is True

    def test_non_affirmative_text_rejected(self):
        assert main.is_affirmative_response("yeah") is False
        assert main.is_affirmative_response("yes please") is False  # multi-word
        assert main.is_affirmative_response("") is False
        assert main.is_affirmative_response(None) is False

    def test_non_negative_text_rejected(self):
        assert main.is_negative_response("nope") is False
        assert main.is_negative_response("don't") is False
        assert main.is_negative_response(None) is False

    def test_affirmative_and_negative_sets_are_disjoint(self):
        assert main._AFFIRMATIVE_RESPONSE_SET.isdisjoint(
            main._NEGATIVE_RESPONSE_SET
        )


# ---------------------------------------------------------------------------
# prepare_capture_confirmation @tool — unit tests
# ---------------------------------------------------------------------------


class TestPrepareCaptureConfirmationTool:
    def setup_method(self):
        # Reset the user-id ContextVar to a stable value for deterministic
        # idempotency tokens.
        self._token = main._CURRENT_USER_ID.set("alice")

    def teardown_method(self):
        main._CURRENT_USER_ID.reset(self._token)

    def test_happy_path_returns_prompt_token_and_metadata(self):
        result_str = _prepare_callable()(
            ["eni-aaaa1111"],
            duration_minutes=15,
            region="us-east-1",
            instance_ids=["i-1234abcd"],
        )
        result = json.loads(result_str)
        assert result["success"] is True
        assert "prompt_text" in result
        assert "idempotency_token" in result
        assert len(result["idempotency_token"]) == 64
        assert result["metadata"]["eni_ids"] == ["eni-aaaa1111"]
        assert result["metadata"]["duration_minutes"] == 15
        assert result["metadata"]["applied_default_15"] is False
        assert result["metadata"]["region"] == "us-east-1"
        assert result["metadata"]["estimated_cost_usd"] >= 0

    def test_default_duration_applied_when_not_supplied(self):
        result_str = _prepare_callable()(["eni-aaaa1111"])
        result = json.loads(result_str)
        assert result["success"] is True
        assert result["metadata"]["applied_default_15"] is True
        assert result["metadata"]["duration_minutes"] == 15
        assert "(default)" in result["prompt_text"]

    def test_returns_error_on_empty_eni_list(self):
        result = json.loads(_prepare_callable()([]))
        assert result["success"] is False
        assert "non-empty" in result["error"]

    def test_returns_error_on_too_many_enis(self):
        result = json.loads(
            _prepare_callable()(
                ["eni-1111aaaa", "eni-2222bbbb", "eni-3333cccc", "eni-4444dddd"]
            )
        )
        assert result["success"] is False
        assert "Capture_Eni_Limit" in result["error"]

    def test_returns_error_on_duration_above_60(self):
        result = json.loads(
            _prepare_callable()(["eni-aaaa1111"], duration_minutes=61)
        )
        assert result["success"] is False
        assert "Capture_Duration_Limit" in result["error"]

    def test_returns_error_on_non_integer_duration(self):
        result = json.loads(
            _prepare_callable()(["eni-aaaa1111"], duration_minutes="15")  # type: ignore[arg-type]
        )
        assert result["success"] is False

    def test_returns_error_when_instance_ids_length_mismatches(self):
        result = json.loads(
            _prepare_callable()(
                ["eni-aaaa1111", "eni-bbbb2222"],
                duration_minutes=15,
                instance_ids=["i-1234abcd"],  # only one
            )
        )
        assert result["success"] is False
        assert "instance_ids" in result["error"]

    def test_returns_error_on_non_string_eni_id(self):
        result = json.loads(
            _prepare_callable()([123], duration_minutes=15)  # type: ignore[list-item]
        )
        assert result["success"] is False
        assert "eni_ids[0]" in result["error"]

    def test_idempotency_token_is_stable_in_same_minute(self):
        # Two consecutive calls within the same wall-clock minute must
        # produce the same idempotency token.
        a = json.loads(
            _prepare_callable()(["eni-aaaa1111"], duration_minutes=15)
        )["idempotency_token"]
        b = json.loads(
            _prepare_callable()(["eni-aaaa1111"], duration_minutes=15)
        )["idempotency_token"]
        assert a == b


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


_eni_count = st.integers(min_value=0, max_value=10)
_duration_minutes = st.integers(min_value=0, max_value=120)
_eni_id_strategy = st.text(
    alphabet=string.hexdigits.lower()[:16], min_size=8, max_size=17
).map(lambda s: f"eni-{s}")


class TestCostFormulaProperties:
    """Property tests for the cost formula.

    Each property is annotated with the requirement(s) it validates.
    These properties stand on the same Correctness Property 12 the
    design document declares for the shared price table.
    """

    _PROP_SETTINGS = settings(
        max_examples=100,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.function_scoped_fixture,
        ],
    )

    @given(_eni_count, _duration_minutes)
    @_PROP_SETTINGS
    def test_cost_matches_documented_formula(self, eni_count, duration_minutes):
        """Validates: Requirements 14.2, 17.2.

        For every ``(eni_count, duration_minutes)`` pair, the value
        returned by :func:`main.compute_capture_cost_usd` is exactly
        the value given by the formula documented in the design's
        ``Capture_Confirmation_Prompt`` section, evaluated against the
        unit prices in ``prices.json``.
        """
        prices = json._default_decoder.decode(
            (Path(main.__file__).parent / "prices.json").read_text(
                encoding="utf-8"
            )
        )
        price_per_eni_hour = prices["trafficMirror"]["eniHourPriceDefault"]
        price_per_gb = prices["trafficMirror"]["dataPricePerGb"]

        actual = main.compute_capture_cost_usd(eni_count, duration_minutes)
        expected = _formula(
            eni_count,
            duration_minutes,
            price_per_eni_hour=price_per_eni_hour,
            price_per_gb=price_per_gb,
        )
        assert actual == pytest.approx(expected, rel=1e-12, abs=1e-15)

    @given(_eni_count, _duration_minutes)
    @_PROP_SETTINGS
    def test_cost_is_non_negative(self, eni_count, duration_minutes):
        """Validates: Requirements 14.2, 17.2."""
        assert main.compute_capture_cost_usd(eni_count, duration_minutes) >= 0

    @given(st.integers(min_value=0, max_value=10), _duration_minutes)
    @_PROP_SETTINGS
    def test_cost_is_linear_in_eni_count(self, eni_count, duration_minutes):
        """Validates: Requirements 14.2, 17.2.

        Doubling ``eni_count`` doubles the cost (default-heuristic
        formula is linear in the eni count).
        """
        single = main.compute_capture_cost_usd(eni_count, duration_minutes)
        double = main.compute_capture_cost_usd(eni_count * 2, duration_minutes)
        assert double == pytest.approx(single * 2, rel=1e-12, abs=1e-15)

    @given(_eni_count, st.integers(min_value=1, max_value=60))
    @_PROP_SETTINGS
    def test_cost_is_monotonic_in_duration(self, eni_count, duration_minutes):
        """Validates: Requirements 14.2, 17.2."""
        shorter = main.compute_capture_cost_usd(eni_count, duration_minutes)
        longer = main.compute_capture_cost_usd(eni_count, duration_minutes + 1)
        assert longer >= shorter


class TestIdempotencyTokenProperties:
    """Property tests for the Capture_Idempotency_Token derivation."""

    _PROP_SETTINGS = settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )

    @given(
        st.lists(
            _eni_id_strategy, min_size=1, max_size=3, unique=True
        ),
        st.integers(min_value=1, max_value=60),
    )
    @_PROP_SETTINGS
    def test_token_is_64_hex_chars(self, eni_ids, duration_minutes):
        """Validates: Requirements 9.21.

        Every derived token is a 64-character lowercase hex string
        (the SHA-256 digest length).
        """
        ts = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)
        token = main.derive_capture_idempotency_token(
            eni_ids, duration_minutes, user_id="alice", timestamp=ts
        )
        assert len(token) == 64
        assert re.fullmatch(r"[0-9a-f]{64}", token)

    @given(
        st.lists(
            _eni_id_strategy, min_size=1, max_size=3, unique=True
        ),
        st.integers(min_value=1, max_value=60),
    )
    @_PROP_SETTINGS
    def test_token_is_invariant_under_eni_permutation(
        self, eni_ids, duration_minutes
    ):
        """Validates: Requirements 9.21, 3.15.

        Permuting ``eni_ids`` does not change the token. The Network
        Agent's idempotency check (Req 3.15) compares ENI sets rather
        than ordered lists, so the token must follow the same
        semantics.
        """
        ts = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)
        token_a = main.derive_capture_idempotency_token(
            eni_ids, duration_minutes, user_id="alice", timestamp=ts
        )
        token_b = main.derive_capture_idempotency_token(
            list(reversed(eni_ids)),
            duration_minutes,
            user_id="alice",
            timestamp=ts,
        )
        assert token_a == token_b

    @given(
        st.lists(
            _eni_id_strategy, min_size=1, max_size=3, unique=True
        ),
        st.integers(min_value=1, max_value=60),
        st.integers(min_value=0, max_value=59),
    )
    @_PROP_SETTINGS
    def test_token_is_invariant_under_seconds(
        self, eni_ids, duration_minutes, second
    ):
        """Validates: Requirements 9.21.

        Two requests within the same minute (any second offset)
        compute identical tokens.
        """
        base = datetime(2026, 4, 20, 12, 30, 0, tzinfo=timezone.utc)
        token_a = main.derive_capture_idempotency_token(
            eni_ids, duration_minutes, user_id="alice", timestamp=base
        )
        token_b = main.derive_capture_idempotency_token(
            eni_ids,
            duration_minutes,
            user_id="alice",
            timestamp=base.replace(second=second),
        )
        assert token_a == token_b
