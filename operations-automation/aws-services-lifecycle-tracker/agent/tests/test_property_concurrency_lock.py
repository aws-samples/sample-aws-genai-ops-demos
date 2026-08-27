"""
Property-Based Tests for concurrency lock mechanism.

Feature: extended-coverage-and-health-integration, Property 11: Concurrency lock prevents parallel execution

**Validates: Requirements 8.4**

Tests verify:
- For any state where a lock is already acquired and not expired, any additional
  acquisition attempt SHALL fail (return False).
- For any state without a lock or with an expired lock, acquisition SHALL succeed
  (return True).

The lock state machine:
  no-lock → acquired → released/expired
"""
import sys
import os
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock AWS dependencies before importing the module
sys.modules['aws_utils'] = MagicMock()
sys.modules['aws_utils'].get_region = MagicMock(return_value='us-east-1')

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st
from botocore.exceptions import ClientError


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Strategy for TTL in minutes (reasonable range: 1 to 60 minutes)
ttl_minutes_strategy = st.integers(min_value=1, max_value=60)

# Strategy for lock IDs (non-empty strings starting with underscore)
lock_id_strategy = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz_"),
    min_size=3,
    max_size=30,
).map(lambda s: f"_{s}")

# Strategy for time offsets in minutes (for simulating time passage)
time_offset_minutes_strategy = st.integers(min_value=1, max_value=120)


# ---------------------------------------------------------------------------
# Simulated DynamoDB Table for Lock Testing
# ---------------------------------------------------------------------------

class SimulatedLockTable:
    """
    Simulates DynamoDB conditional write behavior for the lock mechanism.

    This mock accurately replicates:
    - put_item with ConditionExpression (attribute_not_exists OR expires_at < :now)
    - delete_item with ConditionExpression (lock_holder = :holder)
    """

    def __init__(self):
        self.items = {}  # key -> item dict

    def put_item(self, **kwargs):
        """Simulate conditional put for lock acquisition."""
        item = kwargs.get('Item', {})
        condition = kwargs.get('ConditionExpression', '')
        expr_values = kwargs.get('ExpressionAttributeValues', {})

        service_name = item.get('service_name')
        existing = self.items.get(service_name)

        # Evaluate condition: attribute_not_exists(service_name) OR expires_at < :now
        if existing is not None:
            # Item exists - check if expired
            now_str = expr_values.get(':now', '')
            existing_expires = existing.get('expires_at', '')

            if existing_expires >= now_str:
                # Lock is still valid - conditional check fails
                error_response = {
                    'Error': {
                        'Code': 'ConditionalCheckFailedException',
                        'Message': 'The conditional request failed'
                    }
                }
                raise ClientError(error_response, 'PutItem')

        # Condition satisfied - store the item
        self.items[service_name] = item

    def delete_item(self, **kwargs):
        """Simulate conditional delete for lock release."""
        key = kwargs.get('Key', {})
        expr_values = kwargs.get('ExpressionAttributeValues', {})

        service_name = key.get('service_name')
        existing = self.items.get(service_name)

        if existing is None:
            # Item doesn't exist - conditional check fails
            error_response = {
                'Error': {
                    'Code': 'ConditionalCheckFailedException',
                    'Message': 'The conditional request failed'
                }
            }
            raise ClientError(error_response, 'DeleteItem')

        # Check lock_holder matches
        holder = expr_values.get(':holder', '')
        if existing.get('lock_holder') != holder:
            error_response = {
                'Error': {
                    'Code': 'ConditionalCheckFailedException',
                    'Message': 'The conditional request failed'
                }
            }
            raise ClientError(error_response, 'DeleteItem')

        # Condition satisfied - delete the item
        del self.items[service_name]


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestPropertyConcurrencyLock:
    """
    Property 11: Concurrency lock prevents parallel execution

    Feature: extended-coverage-and-health-integration, Property 11: Concurrency lock prevents parallel execution
    **Validates: Requirements 8.4**
    """

    def _get_fresh_module(self):
        """Re-import concurrency_lock with fresh state."""
        import concurrency_lock
        concurrency_lock._current_lock_holder = None
        return concurrency_lock

    @given(ttl=ttl_minutes_strategy, lock_id=lock_id_strategy)
    @settings(max_examples=100, deadline=None)
    def test_acquire_succeeds_when_no_lock_exists(self, ttl, lock_id):
        """
        For any state without a lock, acquisition SHALL succeed (return True).

        **Validates: Requirements 8.4**
        """
        concurrency_lock = self._get_fresh_module()
        sim_table = SimulatedLockTable()

        with patch('concurrency_lock._get_table', return_value=sim_table):
            result = concurrency_lock.acquire_lock(
                table_name='test-table',
                lock_id=lock_id,
                ttl_minutes=ttl,
            )

        assert result is True, (
            f"Acquire should succeed when no lock exists "
            f"(lock_id={lock_id}, ttl={ttl})"
        )

    @given(ttl=ttl_minutes_strategy, lock_id=lock_id_strategy)
    @settings(max_examples=100, deadline=None)
    def test_second_acquire_fails_when_lock_held(self, ttl, lock_id):
        """
        For any state where a lock is already acquired and not expired,
        any additional acquisition attempt SHALL fail (return False).

        **Validates: Requirements 8.4**
        """
        concurrency_lock = self._get_fresh_module()
        sim_table = SimulatedLockTable()

        with patch('concurrency_lock._get_table', return_value=sim_table):
            # First acquire should succeed
            first_result = concurrency_lock.acquire_lock(
                table_name='test-table',
                lock_id=lock_id,
                ttl_minutes=ttl,
            )
            assert first_result is True

            # Reset holder to simulate a different process trying to acquire
            concurrency_lock._current_lock_holder = None

            # Second acquire should fail (lock is held and not expired)
            second_result = concurrency_lock.acquire_lock(
                table_name='test-table',
                lock_id=lock_id,
                ttl_minutes=ttl,
            )

        assert second_result is False, (
            f"Acquire should fail when lock is already held and not expired "
            f"(lock_id={lock_id}, ttl={ttl})"
        )

    @given(
        ttl=ttl_minutes_strategy,
        lock_id=lock_id_strategy,
        extra_minutes=time_offset_minutes_strategy,
    )
    @settings(max_examples=100, deadline=None)
    def test_acquire_succeeds_when_lock_expired(self, ttl, lock_id, extra_minutes):
        """
        For any state with an expired lock (expires_at < now), acquisition
        SHALL succeed (return True).

        **Validates: Requirements 8.4**
        """
        assume(extra_minutes > ttl)  # Ensure the lock has actually expired

        concurrency_lock = self._get_fresh_module()
        sim_table = SimulatedLockTable()

        # Simulate an expired lock: set acquired_at and expires_at in the past
        past_time = datetime.now(timezone.utc) - timedelta(minutes=extra_minutes)
        expired_at = past_time + timedelta(minutes=ttl)

        sim_table.items[lock_id] = {
            'service_name': lock_id,
            'lock_holder': 'old-holder-id',
            'acquired_at': past_time.isoformat(),
            'expires_at': expired_at.isoformat(),
        }

        with patch('concurrency_lock._get_table', return_value=sim_table):
            result = concurrency_lock.acquire_lock(
                table_name='test-table',
                lock_id=lock_id,
                ttl_minutes=ttl,
            )

        assert result is True, (
            f"Acquire should succeed when lock is expired "
            f"(lock_id={lock_id}, ttl={ttl}, expired {extra_minutes - ttl} min ago)"
        )

    @given(
        ttl=ttl_minutes_strategy,
        lock_id=lock_id_strategy,
        num_attempts=st.integers(min_value=2, max_value=10),
    )
    @settings(max_examples=100, deadline=None)
    def test_multiple_concurrent_attempts_only_one_succeeds(self, ttl, lock_id, num_attempts):
        """
        For any number of concurrent acquisition attempts on a held lock,
        all additional attempts SHALL fail (only the first succeeds).

        **Validates: Requirements 8.4**
        """
        concurrency_lock = self._get_fresh_module()
        sim_table = SimulatedLockTable()

        results = []
        with patch('concurrency_lock._get_table', return_value=sim_table):
            for i in range(num_attempts):
                # Reset holder to simulate different processes
                concurrency_lock._current_lock_holder = None
                result = concurrency_lock.acquire_lock(
                    table_name='test-table',
                    lock_id=lock_id,
                    ttl_minutes=ttl,
                )
                results.append(result)

        # Exactly one should succeed (the first one)
        assert results[0] is True, "First acquisition should succeed"
        assert all(r is False for r in results[1:]), (
            f"All subsequent acquisitions should fail, got: {results}"
        )

    @given(ttl=ttl_minutes_strategy, lock_id=lock_id_strategy)
    @settings(max_examples=100, deadline=None)
    def test_acquire_after_release_succeeds(self, ttl, lock_id):
        """
        For any state where a lock was released (no longer exists),
        acquisition SHALL succeed (return True).

        This models the state machine transition:
        no-lock → acquired → released → acquired (again)

        **Validates: Requirements 8.4**
        """
        concurrency_lock = self._get_fresh_module()
        sim_table = SimulatedLockTable()

        with patch('concurrency_lock._get_table', return_value=sim_table):
            # Acquire the lock
            first_result = concurrency_lock.acquire_lock(
                table_name='test-table',
                lock_id=lock_id,
                ttl_minutes=ttl,
            )
            assert first_result is True

            # Release the lock
            concurrency_lock.release_lock(
                table_name='test-table',
                lock_id=lock_id,
            )

            # Acquire again should succeed (lock was released)
            second_result = concurrency_lock.acquire_lock(
                table_name='test-table',
                lock_id=lock_id,
                ttl_minutes=ttl,
            )

        assert second_result is True, (
            f"Acquire should succeed after release "
            f"(lock_id={lock_id}, ttl={ttl})"
        )
