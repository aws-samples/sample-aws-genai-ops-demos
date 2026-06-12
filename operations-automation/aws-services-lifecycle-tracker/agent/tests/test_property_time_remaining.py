"""
Property-based tests for time_remaining calculation in HealthEnricher.

Feature: extended-coverage-and-health-integration, Property 9: Scheduled change notification includes time calculation

**Validates: Requirements 5.2**

Tests that _calculate_time_remaining():
1. Returns a non-empty string for any future datetime
2. Returns None for any past datetime
3. Contains days/hours info for events far in the future
4. Is consistent: closer events have shorter or equal time_remaining representation
"""
import sys
import os

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
))

from datetime import datetime, timezone, timedelta

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from health_enricher import HealthEnricher


# --- Strategies ---

# Strategy for future offsets (1 minute to 365 days ahead)
future_offset_strategy = st.timedeltas(
    min_value=timedelta(minutes=2),
    max_value=timedelta(days=365)
)

# Strategy for past offsets (1 minute to 365 days behind)
past_offset_strategy = st.timedeltas(
    min_value=timedelta(minutes=2),
    max_value=timedelta(days=365)
)

# Strategy for far-future offsets (more than 1 day ahead)
far_future_offset_strategy = st.timedeltas(
    min_value=timedelta(days=1, hours=1),
    max_value=timedelta(days=365)
)

# Strategy for two different future offsets to test ordering consistency
two_future_offsets_strategy = st.tuples(
    st.timedeltas(min_value=timedelta(minutes=5), max_value=timedelta(days=180)),
    st.timedeltas(min_value=timedelta(minutes=5), max_value=timedelta(days=180))
)


# --- Helper ---

def _make_iso_string(dt: datetime) -> str:
    """Format a datetime as ISO 8601 string with UTC timezone."""
    return dt.strftime('%Y-%m-%dT%H:%M:%S+00:00')


def _parse_time_remaining_to_minutes(time_remaining: str) -> int:
    """
    Parse a time_remaining string into approximate total minutes.
    Handles formats like '5 days, 3 hours', '2 hours, 15 minutes', 'less than 1 minute'.
    """
    if time_remaining == "less than 1 minute":
        return 0

    total_minutes = 0
    parts = time_remaining.split(", ")
    for part in parts:
        tokens = part.split()
        if len(tokens) >= 2:
            value = int(tokens[0])
            unit = tokens[1]
            if 'day' in unit:
                total_minutes += value * 24 * 60
            elif 'hour' in unit:
                total_minutes += value * 60
            elif 'minute' in unit:
                total_minutes += value
    return total_minutes


# --- Property Tests ---

class TestTimeRemainingProperty:
    """
    Feature: extended-coverage-and-health-integration, Property 9: Scheduled change notification includes time calculation

    For any Health_Event of type scheduledChange with a start_time in the future,
    the Health_Notification SHALL contain the field start_time original and a field
    time_remaining calculated representing the duration before the event starts.
    """

    @given(offset=future_offset_strategy)
    @settings(max_examples=100)
    def test_future_datetime_returns_non_empty_string(self, offset):
        """
        For ANY future datetime string, _calculate_time_remaining SHALL return
        a non-empty string representing the duration before the event.

        **Validates: Requirements 5.2**
        """
        future_time = datetime.now(timezone.utc) + offset
        start_time_str = _make_iso_string(future_time)

        result = HealthEnricher._calculate_time_remaining(start_time_str)

        assert result is not None, (
            f"Expected non-None result for future time {start_time_str}"
        )
        assert isinstance(result, str), (
            f"Expected string result, got {type(result)}"
        )
        assert len(result) > 0, (
            f"Expected non-empty string for future time {start_time_str}"
        )

    @given(offset=past_offset_strategy)
    @settings(max_examples=100)
    def test_past_datetime_returns_none(self, offset):
        """
        For ANY past datetime string, _calculate_time_remaining SHALL return None.

        **Validates: Requirements 5.2**
        """
        past_time = datetime.now(timezone.utc) - offset
        start_time_str = _make_iso_string(past_time)

        result = HealthEnricher._calculate_time_remaining(start_time_str)

        assert result is None, (
            f"Expected None for past time {start_time_str}, got '{result}'"
        )

    @given(offset=far_future_offset_strategy)
    @settings(max_examples=100)
    def test_far_future_contains_days_or_hours(self, offset):
        """
        For ANY event far in the future (more than 1 day), the time_remaining
        SHALL contain days and/or hours information.

        **Validates: Requirements 5.2**
        """
        future_time = datetime.now(timezone.utc) + offset
        start_time_str = _make_iso_string(future_time)

        result = HealthEnricher._calculate_time_remaining(start_time_str)

        assert result is not None, (
            f"Expected non-None for far future time {start_time_str}"
        )
        has_days = 'day' in result
        has_hours = 'hour' in result
        assert has_days or has_hours, (
            f"Expected 'day' or 'hour' in result for far future event, "
            f"got '{result}' (offset={offset})"
        )

    @given(offsets=two_future_offsets_strategy)
    @settings(max_examples=100)
    def test_closer_events_have_shorter_or_equal_time_remaining(self, offsets):
        """
        For ANY two future events, the one closer in time SHALL have a shorter
        or equal time_remaining (in parsed minutes) compared to the farther one.

        **Validates: Requirements 5.2**
        """
        offset_a, offset_b = offsets
        assume(abs((offset_a - offset_b).total_seconds()) > 120)

        now = datetime.now(timezone.utc)
        time_a = now + offset_a
        time_b = now + offset_b

        result_a = HealthEnricher._calculate_time_remaining(_make_iso_string(time_a))
        result_b = HealthEnricher._calculate_time_remaining(_make_iso_string(time_b))

        assert result_a is not None, "Expected non-None for future time_a"
        assert result_b is not None, "Expected non-None for future time_b"

        minutes_a = _parse_time_remaining_to_minutes(result_a)
        minutes_b = _parse_time_remaining_to_minutes(result_b)

        if offset_a < offset_b:
            assert minutes_a <= minutes_b, (
                f"Closer event (offset={offset_a}) should have shorter/equal "
                f"time_remaining ({minutes_a} min) than farther event "
                f"(offset={offset_b}, {minutes_b} min). "
                f"Results: '{result_a}' vs '{result_b}'"
            )
        else:
            assert minutes_b <= minutes_a, (
                f"Closer event (offset={offset_b}) should have shorter/equal "
                f"time_remaining ({minutes_b} min) than farther event "
                f"(offset={offset_a}, {minutes_a} min). "
                f"Results: '{result_b}' vs '{result_a}'"
            )
