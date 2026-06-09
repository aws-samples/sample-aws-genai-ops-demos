"""
Unit and property-based tests for the shared price table.

Run from the ``agents/shared`` directory::

    python -m pytest test_prices.py -v

These tests cover:

- The exact formula documented in the design's
  ``Capture_Confirmation_Prompt`` section is what
  :func:`prices.compute_capture_cost_usd` returns.
- The Python module ``prices.py`` and the JSON file ``prices.json``
  carry identical values, so the chat confirmation cost (which reads
  the Python module) and the README cost-estimate table (which can
  read either) cannot drift.
- Boundary cases (zero ENIs, zero duration, custom ``estimated_bytes``
  overrides, unknown regions) behave per the documented contract.

The property tests use Hypothesis to assert universal invariants of
the formula (linearity in ``eni_count``, monotonicity in
``duration_minutes``, and overall non-negativity).

Each property test is annotated with the requirement(s) it validates.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

import prices


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _formula(
    eni_count: int,
    duration_minutes: int,
    *,
    price_per_eni_hour: float = prices.TRAFFIC_MIRROR_ENI_HOUR_PRICE_USD,
    price_per_gb: float = prices.TRAFFIC_MIRROR_DATA_PRICE_PER_GB_USD,
    estimated_bytes: int = None,
) -> float:
    """Reference reimplementation of the design's cost formula."""
    duration_hours = duration_minutes / 60.0
    if estimated_bytes is None:
        estimated_bytes = (
            eni_count * duration_minutes * 60 * prices.BYTES_PER_SECOND_PER_MBPS
        )
    return (
        eni_count * duration_hours * price_per_eni_hour
        + (estimated_bytes / 1e9) * price_per_gb
    )


# ---------------------------------------------------------------------------
# Constants and table integrity
# ---------------------------------------------------------------------------


class TestConstants:
    """Sanity checks for the seeded price table values."""

    def test_traffic_mirror_data_price_per_gb_is_documented_rate(self):
        """The data charge documented in the design is ``$0.015/GB``."""
        assert prices.TRAFFIC_MIRROR_DATA_PRICE_PER_GB_USD == 0.015

    def test_default_eni_hour_price_matches_us_east_1(self):
        """The default rate is the published rate for ``us-east-1``."""
        assert (
            prices.TRAFFIC_MIRROR_ENI_HOUR_PRICE_USD
            == prices.TRAFFIC_MIRROR_ENI_HOUR_PRICE_BY_REGION["us-east-1"]
        )

    def test_mbps_per_eni_heuristic_is_one(self):
        """Heuristic documented in the design is 1 Mbps per ENI."""
        assert prices.MBPS_PER_ENI_HEURISTIC == 1.0

    def test_bytes_per_second_per_mbps_is_125000(self):
        """The conversion ``1e6 bits / 8 bits per byte`` is ``125000``."""
        assert prices.BYTES_PER_SECOND_PER_MBPS == 125_000

    def test_all_regional_prices_are_positive(self):
        for region, price in (
            prices.TRAFFIC_MIRROR_ENI_HOUR_PRICE_BY_REGION.items()
        ):
            assert price > 0, f"Price for {region} must be positive"


# ---------------------------------------------------------------------------
# JSON / Python parity
# ---------------------------------------------------------------------------


class TestJsonPythonParity:
    """``prices.json`` must mirror ``prices.py`` so the two cannot drift."""

    @pytest.fixture(scope="class")
    def json_data(self):
        path = Path(__file__).parent / "prices.json"
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    def test_currency_is_usd(self, json_data):
        assert json_data["currency"] == "USD"

    def test_traffic_mirror_default_eni_hour_price_matches(self, json_data):
        """Validates: Requirements 14.2, 17.2."""
        assert (
            json_data["trafficMirror"]["eniHourPriceDefault"]
            == prices.TRAFFIC_MIRROR_ENI_HOUR_PRICE_USD
        )

    def test_traffic_mirror_data_price_per_gb_matches(self, json_data):
        """Validates: Requirements 14.2, 17.2."""
        assert (
            json_data["trafficMirror"]["dataPricePerGb"]
            == prices.TRAFFIC_MIRROR_DATA_PRICE_PER_GB_USD
        )

    def test_s3_storage_price_matches(self, json_data):
        """Validates: Requirements 14.2."""
        assert (
            json_data["s3"]["standardStoragePricePerGbMonth"]
            == prices.S3_STANDARD_STORAGE_PRICE_PER_GB_MONTH_USD
        )

    def test_heuristic_mbps_per_eni_matches(self, json_data):
        """Validates: Requirements 14.2, 17.2."""
        assert (
            json_data["heuristic"]["mbpsPerEni"]
            == prices.MBPS_PER_ENI_HEURISTIC
        )

    def test_heuristic_bytes_per_second_per_mbps_matches(self, json_data):
        """Validates: Requirements 14.2, 17.2."""
        assert (
            json_data["heuristic"]["bytesPerSecondPerMbps"]
            == prices.BYTES_PER_SECOND_PER_MBPS
        )

    def test_regional_eni_hour_prices_are_identical(self, json_data):
        """Every region in the Python table appears in JSON with the same price.

        Validates: Requirements 14.2, 17.2.
        """
        json_table = json_data["trafficMirror"]["eniHourPriceByRegion"]
        py_table = prices.TRAFFIC_MIRROR_ENI_HOUR_PRICE_BY_REGION
        assert set(json_table.keys()) == set(py_table.keys()), (
            "Region key set drift between prices.py and prices.json"
        )
        for region in py_table:
            assert json_table[region] == py_table[region], (
                f"Drift detected for region {region}: "
                f"prices.py={py_table[region]} prices.json={json_table[region]}"
            )


# ---------------------------------------------------------------------------
# estimate_bytes
# ---------------------------------------------------------------------------


class TestEstimateBytes:
    def test_zero_enis_yields_zero_bytes(self):
        assert prices.estimate_bytes(0, 15) == 0

    def test_zero_duration_yields_zero_bytes(self):
        assert prices.estimate_bytes(3, 0) == 0

    def test_documented_heuristic_one_eni_one_minute(self):
        # 1 ENI * 1 minute * 60 s/min * 125000 B/s = 7_500_000 (~7.5 MB)
        assert prices.estimate_bytes(1, 1) == 7_500_000

    def test_documented_heuristic_three_enis_fifteen_minutes(self):
        # 3 * 15 * 60 * 125000 = 337_500_000
        assert prices.estimate_bytes(3, 15) == 337_500_000


# ---------------------------------------------------------------------------
# get_traffic_mirror_eni_hour_price
# ---------------------------------------------------------------------------


class TestGetTrafficMirrorEniHourPrice:
    def test_known_region_returns_table_value(self):
        assert (
            prices.get_traffic_mirror_eni_hour_price("eu-west-1")
            == prices.TRAFFIC_MIRROR_ENI_HOUR_PRICE_BY_REGION["eu-west-1"]
        )

    def test_unknown_region_returns_default(self):
        assert (
            prices.get_traffic_mirror_eni_hour_price("xx-fake-1")
            == prices.TRAFFIC_MIRROR_ENI_HOUR_PRICE_USD
        )

    def test_none_region_returns_default(self):
        assert (
            prices.get_traffic_mirror_eni_hour_price(None)
            == prices.TRAFFIC_MIRROR_ENI_HOUR_PRICE_USD
        )


# ---------------------------------------------------------------------------
# compute_capture_cost_usd
# ---------------------------------------------------------------------------


class TestComputeCaptureCostUsd:
    """Unit tests for the documented cost formula."""

    def test_zero_enis_zero_cost(self):
        assert prices.compute_capture_cost_usd(0, 60) == 0.0

    def test_zero_duration_zero_cost(self):
        assert prices.compute_capture_cost_usd(3, 0) == 0.0

    def test_documented_formula_one_eni_15_minutes(self):
        """Hand-computed: 1 ENI * 15 min, default heuristic.

        eni_hours_cost = 1 * (15/60) * 0.015 = 0.00375
        estimated_bytes = 1 * 15 * 60 * 125_000 = 112_500_000
        data_cost = (112_500_000 / 1e9) * 0.015 = 0.0016875
        total ~= 0.0054375
        """
        result = prices.compute_capture_cost_usd(1, 15)
        assert math.isclose(result, 0.0054375, rel_tol=1e-9)

    def test_documented_formula_three_enis_60_minutes(self):
        """3 ENIs, 60 min, default heuristic.

        eni_hours_cost = 3 * 1.0 * 0.015 = 0.045
        estimated_bytes = 3 * 60 * 60 * 125_000 = 1_350_000_000
        data_cost = (1_350_000_000 / 1e9) * 0.015 = 0.02025
        total = 0.06525
        """
        result = prices.compute_capture_cost_usd(3, 60)
        assert math.isclose(result, 0.06525, rel_tol=1e-9)

    def test_estimated_bytes_override_is_used(self):
        result = prices.compute_capture_cost_usd(
            1, 60, estimated_bytes=1_000_000_000
        )
        # 1 * 1.0 * 0.015 = 0.015 + (1e9 / 1e9) * 0.015 = 0.015 -> 0.030
        assert math.isclose(result, 0.030, rel_tol=1e-9)

    def test_negative_eni_count_raises(self):
        with pytest.raises(ValueError):
            prices.compute_capture_cost_usd(-1, 15)

    def test_negative_duration_minutes_raises(self):
        with pytest.raises(ValueError):
            prices.compute_capture_cost_usd(1, -1)

    def test_negative_estimated_bytes_raises(self):
        with pytest.raises(ValueError):
            prices.compute_capture_cost_usd(1, 15, estimated_bytes=-1)

    def test_unknown_region_uses_default_price(self):
        a = prices.compute_capture_cost_usd(2, 30, region="xx-fake-1")
        b = prices.compute_capture_cost_usd(2, 30, region=None)
        assert a == b


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


_eni_count = st.integers(min_value=0, max_value=10)
_duration_minutes = st.integers(min_value=0, max_value=120)
_estimated_bytes = st.integers(min_value=0, max_value=10**12)


class TestComputeCaptureCostUsdProperties:
    """Property tests for the cost formula.

    Each property is annotated with the requirements it validates.
    The single Correctness Property the design defines for this module
    is Property 12 (Requirements 14.2, 17.2): the cost printed in the
    Capture_Confirmation_Prompt equals the cost computed by the README
    cost-estimate formula using the same ``prices.py`` module values.
    The tests below assert the underlying formula invariants that
    Property 12 stands on.
    """

    @given(_eni_count, _duration_minutes)
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_property_matches_documented_formula(
        self, eni_count, duration_minutes
    ):
        """Validates: Requirements 14.2, 17.2.

        For every ``(eni_count, duration_minutes)`` pair, the value
        returned by :func:`prices.compute_capture_cost_usd` is exactly
        the value given by the formula documented in the design's
        ``Capture_Confirmation_Prompt`` section. This is the
        executable form of the design's Correctness Property 12.
        """
        actual = prices.compute_capture_cost_usd(eni_count, duration_minutes)
        expected = _formula(eni_count, duration_minutes)
        assert math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-15)

    @given(_eni_count, _duration_minutes, _estimated_bytes)
    @settings(max_examples=200)
    def test_property_estimated_bytes_override(
        self, eni_count, duration_minutes, estimated_bytes
    ):
        """Validates: Requirements 14.2, 17.2.

        Supplying an ``estimated_bytes`` override produces the same
        cost as the documented formula with that override.
        """
        actual = prices.compute_capture_cost_usd(
            eni_count, duration_minutes, estimated_bytes=estimated_bytes
        )
        expected = _formula(
            eni_count, duration_minutes, estimated_bytes=estimated_bytes
        )
        assert math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-15)

    @given(_eni_count, _duration_minutes)
    @settings(max_examples=100)
    def test_property_non_negative(self, eni_count, duration_minutes):
        """Validates: Requirements 14.2, 17.2.

        The cost is always non-negative for non-negative inputs.
        """
        result = prices.compute_capture_cost_usd(eni_count, duration_minutes)
        assert result >= 0

    @given(st.integers(min_value=0, max_value=10), _duration_minutes)
    @settings(max_examples=100)
    def test_property_linear_in_eni_count(self, eni_count, duration_minutes):
        """Validates: Requirements 14.2, 17.2.

        The default-heuristic formula is linear in ``eni_count``:
        doubling ``eni_count`` doubles the cost (within float
        tolerance).
        """
        single = prices.compute_capture_cost_usd(eni_count, duration_minutes)
        double = prices.compute_capture_cost_usd(
            eni_count * 2, duration_minutes
        )
        assert math.isclose(double, single * 2, rel_tol=1e-12, abs_tol=1e-15)

    @given(_eni_count, st.integers(min_value=1, max_value=60))
    @settings(max_examples=100)
    def test_property_monotonic_in_duration(
        self, eni_count, duration_minutes
    ):
        """Validates: Requirements 14.2, 17.2.

        For ``eni_count > 0``, longer captures cost no less than
        shorter captures. For ``eni_count == 0``, both are zero.
        """
        shorter = prices.compute_capture_cost_usd(
            eni_count, duration_minutes
        )
        longer = prices.compute_capture_cost_usd(
            eni_count, duration_minutes + 1
        )
        assert longer >= shorter
