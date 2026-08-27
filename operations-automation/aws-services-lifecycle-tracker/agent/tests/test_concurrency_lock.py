"""
Unit tests for the concurrency lock mechanism.

Tests the DynamoDB conditional write lock used to prevent parallel execution
of health collection.

**Validates: Requirements 8.4**
"""
import sys
import os
import uuid
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock AWS dependencies before importing the module
mock_dynamodb = MagicMock()
mock_table = MagicMock()
mock_dynamodb.Table.return_value = mock_table

sys.modules['aws_utils'] = MagicMock()
sys.modules['aws_utils'].get_region = MagicMock(return_value='us-east-1')

import pytest
from unittest.mock import patch
from botocore.exceptions import ClientError


class TestAcquireLock:
    """Tests for acquire_lock function."""

    def setup_method(self):
        """Reset module state before each test."""
        import concurrency_lock
        concurrency_lock._current_lock_holder = None

    @patch('concurrency_lock.config_table')
    def test_acquire_lock_success(self, mock_config_table):
        """Lock acquisition succeeds when no lock exists."""
        import concurrency_lock

        mock_config_table.put_item.return_value = {}

        result = concurrency_lock.acquire_lock()

        assert result is True
        assert concurrency_lock._current_lock_holder is not None
        mock_config_table.put_item.assert_called_once()

        # Verify the item structure
        call_kwargs = mock_config_table.put_item.call_args[1]
        item = call_kwargs['Item']
        assert item['service_name'] == '_health_collection_lock'
        assert 'lock_holder' in item
        assert 'acquired_at' in item
        assert 'expires_at' in item

    @patch('concurrency_lock.config_table')
    def test_acquire_lock_failure_concurrent(self, mock_config_table):
        """Lock acquisition fails when lock is already held."""
        import concurrency_lock

        error_response = {
            'Error': {
                'Code': 'ConditionalCheckFailedException',
                'Message': 'Condition not met'
            }
        }
        mock_config_table.put_item.side_effect = ClientError(
            error_response, 'PutItem'
        )

        result = concurrency_lock.acquire_lock()

        assert result is False
        assert concurrency_lock._current_lock_holder is None

    @patch('concurrency_lock.config_table')
    def test_acquire_lock_custom_table(self, mock_config_table):
        """Lock acquisition uses the custom table when specified."""
        import concurrency_lock

        mock_custom_table = MagicMock()
        mock_custom_table.put_item.return_value = {}

        with patch('concurrency_lock.dynamodb') as mock_ddb:
            mock_ddb.Table.return_value = mock_custom_table
            result = concurrency_lock.acquire_lock(table_name='custom-table')

        assert result is True
        mock_ddb.Table.assert_called_with('custom-table')

    @patch('concurrency_lock.config_table')
    def test_acquire_lock_custom_lock_id(self, mock_config_table):
        """Lock uses the specified lock_id as service_name."""
        import concurrency_lock

        mock_config_table.put_item.return_value = {}

        result = concurrency_lock.acquire_lock(lock_id='_custom_lock')

        assert result is True
        call_kwargs = mock_config_table.put_item.call_args[1]
        assert call_kwargs['Item']['service_name'] == '_custom_lock'

    @patch('concurrency_lock.config_table')
    def test_acquire_lock_ttl_calculation(self, mock_config_table):
        """Lock expires_at is correctly calculated from ttl_minutes."""
        import concurrency_lock

        mock_config_table.put_item.return_value = {}

        result = concurrency_lock.acquire_lock(ttl_minutes=15)

        assert result is True
        call_kwargs = mock_config_table.put_item.call_args[1]
        item = call_kwargs['Item']

        acquired_at = datetime.fromisoformat(item['acquired_at'])
        expires_at = datetime.fromisoformat(item['expires_at'])
        delta = expires_at - acquired_at

        # Should be approximately 15 minutes
        assert 14 * 60 <= delta.total_seconds() <= 16 * 60

    @patch('concurrency_lock.config_table')
    def test_acquire_lock_condition_expression(self, mock_config_table):
        """Condition expression allows overwriting expired locks."""
        import concurrency_lock

        mock_config_table.put_item.return_value = {}

        concurrency_lock.acquire_lock()

        call_kwargs = mock_config_table.put_item.call_args[1]
        condition = call_kwargs['ConditionExpression']

        # Must allow acquisition if item doesn't exist OR is expired
        assert 'attribute_not_exists' in condition
        assert 'expires_at' in condition

    @patch('concurrency_lock.config_table')
    def test_acquire_lock_propagates_unexpected_errors(self, mock_config_table):
        """Non-conditional-check errors are propagated."""
        import concurrency_lock

        error_response = {
            'Error': {
                'Code': 'InternalServerError',
                'Message': 'Internal error'
            }
        }
        mock_config_table.put_item.side_effect = ClientError(
            error_response, 'PutItem'
        )

        with pytest.raises(ClientError):
            concurrency_lock.acquire_lock()


class TestReleaseLock:
    """Tests for release_lock function."""

    def setup_method(self):
        """Reset module state before each test."""
        import concurrency_lock
        concurrency_lock._current_lock_holder = None

    @patch('concurrency_lock.config_table')
    def test_release_lock_success(self, mock_config_table):
        """Lock is released when we hold it."""
        import concurrency_lock

        # Simulate having acquired a lock
        holder_id = str(uuid.uuid4())
        concurrency_lock._current_lock_holder = holder_id
        mock_config_table.delete_item.return_value = {}

        concurrency_lock.release_lock()

        mock_config_table.delete_item.assert_called_once()
        call_kwargs = mock_config_table.delete_item.call_args[1]
        assert call_kwargs['Key'] == {'service_name': '_health_collection_lock'}
        assert call_kwargs['ExpressionAttributeValues'][':holder'] == holder_id
        assert concurrency_lock._current_lock_holder is None

    @patch('concurrency_lock.config_table')
    def test_release_lock_noop_when_not_held(self, mock_config_table):
        """Release does nothing when no lock is held by this process."""
        import concurrency_lock

        concurrency_lock._current_lock_holder = None

        concurrency_lock.release_lock()

        mock_config_table.delete_item.assert_not_called()

    @patch('concurrency_lock.config_table')
    def test_release_lock_fails_silently_if_someone_else_holds(self, mock_config_table):
        """Release fails silently if another holder took over."""
        import concurrency_lock

        concurrency_lock._current_lock_holder = 'my-id'

        error_response = {
            'Error': {
                'Code': 'ConditionalCheckFailedException',
                'Message': 'Condition not met'
            }
        }
        mock_config_table.delete_item.side_effect = ClientError(
            error_response, 'DeleteItem'
        )

        # Should not raise
        concurrency_lock.release_lock()
        assert concurrency_lock._current_lock_holder is None

    @patch('concurrency_lock.config_table')
    def test_release_lock_propagates_unexpected_errors(self, mock_config_table):
        """Non-conditional-check errors are propagated on release."""
        import concurrency_lock

        concurrency_lock._current_lock_holder = 'my-id'

        error_response = {
            'Error': {
                'Code': 'InternalServerError',
                'Message': 'Internal error'
            }
        }
        mock_config_table.delete_item.side_effect = ClientError(
            error_response, 'DeleteItem'
        )

        with pytest.raises(ClientError):
            concurrency_lock.release_lock()

    @patch('concurrency_lock.config_table')
    def test_release_lock_custom_lock_id(self, mock_config_table):
        """Release uses specified lock_id."""
        import concurrency_lock

        concurrency_lock._current_lock_holder = 'my-id'
        mock_config_table.delete_item.return_value = {}

        concurrency_lock.release_lock(lock_id='_custom_lock')

        call_kwargs = mock_config_table.delete_item.call_args[1]
        assert call_kwargs['Key'] == {'service_name': '_custom_lock'}


class TestAcquireAndReleaseCycle:
    """Integration-style tests for the full acquire/release cycle."""

    def setup_method(self):
        """Reset module state before each test."""
        import concurrency_lock
        concurrency_lock._current_lock_holder = None

    @patch('concurrency_lock.config_table')
    def test_full_cycle(self, mock_config_table):
        """Acquire then release completes cleanly."""
        import concurrency_lock

        mock_config_table.put_item.return_value = {}
        mock_config_table.delete_item.return_value = {}

        # Acquire
        result = concurrency_lock.acquire_lock()
        assert result is True
        assert concurrency_lock._current_lock_holder is not None

        # Release
        concurrency_lock.release_lock()
        assert concurrency_lock._current_lock_holder is None

    @patch('concurrency_lock.config_table')
    def test_lock_holder_is_uuid(self, mock_config_table):
        """Lock holder is a valid UUID."""
        import concurrency_lock

        mock_config_table.put_item.return_value = {}

        concurrency_lock.acquire_lock()

        # Validate it's a proper UUID
        holder = concurrency_lock._current_lock_holder
        parsed = uuid.UUID(holder)
        assert str(parsed) == holder
