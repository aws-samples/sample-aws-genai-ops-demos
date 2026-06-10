"""
Property-based tests for exponential backoff calculation.

Feature: extended-coverage-and-health-integration, Property 7: Exponential backoff calculation

**Validates: Requirements 3.5**

Tests that _apply_backoff() calculates the correct exponential delay for any
attempt number N in [1, 5] and returns False for any N > 5.
"""
import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
))

from hypothesis import given, settings
from hypothesis import strategies as st

from health_collector import HealthCollector


# --- Strategies ---

# Strategy for valid attempt numbers (1 to 5)
valid_attempt_strategy = st.integers(min_value=1, max_value=5)

# Strategy for attempt numbers exceeding max (6+)
exceeded_attempt_strategy = st.integers(min_value=6, max_value=100)

# Strategy for base_delay (positive float)
base_delay_strategy = st.floats(min_value=0.1, max_value=10.0, allow_nan=False, allow_infinity=False)


# --- Property Tests ---

class TestExponentialBackoffProperty:
    """
    Feature: extended-coverage-and-health-integration, Property 7: Exponential backoff calculation

    For any attempt number N (1 ≤ N ≤ 5), the delay calculated SHALL be equal to
    2^(N-1) * 1.0 seconds. For any N > 5, the function SHALL return False (stop retrying).
    """

    @given(attempt=valid_attempt_strategy)
    @settings(max_examples=100)
    def test_valid_attempts_return_true_with_correct_delay(self, attempt):
        """
        For ANY attempt N in [1, 5], _apply_backoff returns True and the
        calculated delay is 2^(N-1) * base_delay (default 1.0s).

        **Validates: Requirements 3.5**
        """
        collector = HealthCollector.__new__(HealthCollector)
        expected_delay = (2 ** (attempt - 1)) * 1.0

        with patch('time.sleep') as mock_sleep:
            result = collector._apply_backoff(attempt)

        assert result is True, (
            f"_apply_backoff({attempt}) returned False, expected True for attempt <= 5"
        )
        mock_sleep.assert_called_once_with(expected_delay)

    @given(attempt=exceeded_attempt_strategy)
    @settings(max_examples=100)
    def test_exceeded_attempts_return_false(self, attempt):
        """
        For ANY attempt N > 5, _apply_backoff SHALL return False (stop retrying).

        **Validates: Requirements 3.5**
        """
        collector = HealthCollector.__new__(HealthCollector)

        with patch('time.sleep') as mock_sleep:
            result = collector._apply_backoff(attempt)

        assert result is False, (
            f"_apply_backoff({attempt}) returned True, expected False for attempt > 5"
        )
        mock_sleep.assert_not_called()

    @given(attempt=valid_attempt_strategy, base_delay=base_delay_strategy)
    @settings(max_examples=100)
    def test_custom_base_delay_scales_correctly(self, attempt, base_delay):
        """
        For ANY attempt N in [1, 5] and ANY positive base_delay, the calculated
        delay SHALL be 2^(N-1) * base_delay.

        **Validates: Requirements 3.5**
        """
        collector = HealthCollector.__new__(HealthCollector)
        expected_delay = (2 ** (attempt - 1)) * base_delay

        with patch('time.sleep') as mock_sleep:
            result = collector._apply_backoff(attempt, base_delay=base_delay)

        assert result is True
        actual_delay = mock_sleep.call_args[0][0]
        assert abs(actual_delay - expected_delay) < 1e-9, (
            f"For attempt={attempt}, base_delay={base_delay}: "
            f"expected delay={expected_delay}, got {actual_delay}"
        )
