"""
Unit tests for health_monitoring module.

Tests the failure tracking, CloudWatch alarm emission, and graceful degradation
functionality for Health collection.

Requirements: 8.2, 9.3
"""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

# Add agent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from health_monitoring import (
    track_collection_result,
    disable_health_collection,
    is_health_collection_enabled,
    enable_health_collection,
    _emit_cloudwatch_alarm,
    FAILURE_COUNTER_KEY,
    HEALTH_DISABLED_KEY,
    CONSECUTIVE_FAILURE_THRESHOLD,
    METRIC_NAMESPACE,
    METRIC_NAME,
)


@pytest.fixture
def mock_config_table():
    """Mock the DynamoDB config table."""
    with patch('health_monitoring._get_config_table') as mock_get_table:
        mock_table = MagicMock()
        mock_get_table.return_value = mock_table
        yield mock_table


@pytest.fixture
def mock_cloudwatch():
    """Mock the CloudWatch client."""
    with patch('health_monitoring._get_cloudwatch_client') as mock_get_cw:
        mock_client = MagicMock()
        mock_get_cw.return_value = mock_client
        yield mock_client


class TestTrackCollectionResult:
    """Tests for track_collection_result function."""

    def test_success_resets_counter(self, mock_config_table):
        """On success, the failure counter should be reset to 0."""
        result = track_collection_result(success=True)

        assert result['failure_count'] == 0
        assert result['alarm_emitted'] is False
        mock_config_table.put_item.assert_called_once()
        call_args = mock_config_table.put_item.call_args[1]['Item']
        assert call_args['service_name'] == FAILURE_COUNTER_KEY
        assert call_args['failure_count'] == 0

    def test_failure_increments_counter(self, mock_config_table, mock_cloudwatch):
        """On failure, the failure counter should be incremented."""
        # Simulate counter at 1 after increment
        mock_config_table.update_item.return_value = {
            'Attributes': {'failure_count': 1}
        }

        result = track_collection_result(success=False)

        assert result['failure_count'] == 1
        assert result['alarm_emitted'] is False
        mock_config_table.update_item.assert_called_once()

    def test_failure_emits_alarm_at_threshold(self, mock_config_table, mock_cloudwatch):
        """When failures reach the threshold (3), a CloudWatch alarm should be emitted."""
        # Simulate counter reaching threshold
        mock_config_table.update_item.return_value = {
            'Attributes': {'failure_count': CONSECUTIVE_FAILURE_THRESHOLD}
        }
        mock_cloudwatch.put_metric_data.return_value = {}

        result = track_collection_result(success=False)

        assert result['failure_count'] == CONSECUTIVE_FAILURE_THRESHOLD
        assert result['alarm_emitted'] is True
        mock_cloudwatch.put_metric_data.assert_called_once()

    def test_failure_emits_alarm_above_threshold(self, mock_config_table, mock_cloudwatch):
        """Alarm should continue to emit when failures exceed the threshold."""
        mock_config_table.update_item.return_value = {
            'Attributes': {'failure_count': 5}
        }
        mock_cloudwatch.put_metric_data.return_value = {}

        result = track_collection_result(success=False)

        assert result['failure_count'] == 5
        assert result['alarm_emitted'] is True

    def test_failure_no_alarm_below_threshold(self, mock_config_table, mock_cloudwatch):
        """No alarm should be emitted when failures are below the threshold."""
        mock_config_table.update_item.return_value = {
            'Attributes': {'failure_count': 2}
        }

        result = track_collection_result(success=False)

        assert result['failure_count'] == 2
        assert result['alarm_emitted'] is False
        mock_cloudwatch.put_metric_data.assert_not_called()

    def test_failure_counter_fallback_on_update_error(self, mock_config_table, mock_cloudwatch):
        """If update_item fails, fall back to put_item with count=1."""
        mock_config_table.update_item.side_effect = Exception("DynamoDB error")
        mock_config_table.put_item.return_value = {}

        result = track_collection_result(success=False)

        assert result['failure_count'] == 1
        assert result['alarm_emitted'] is False


class TestEmitCloudWatchAlarm:
    """Tests for _emit_cloudwatch_alarm function."""

    def test_emits_metric_with_correct_data(self, mock_cloudwatch):
        """Should publish metric data with correct namespace, name, and value."""
        mock_cloudwatch.put_metric_data.return_value = {}

        result = _emit_cloudwatch_alarm(3)

        assert result is True
        mock_cloudwatch.put_metric_data.assert_called_once()
        call_kwargs = mock_cloudwatch.put_metric_data.call_args[1]
        assert call_kwargs['Namespace'] == METRIC_NAMESPACE
        metric_data = call_kwargs['MetricData'][0]
        assert metric_data['MetricName'] == METRIC_NAME
        assert metric_data['Value'] == 3
        assert metric_data['Unit'] == 'Count'

    def test_returns_false_on_error(self, mock_cloudwatch):
        """Should return False if CloudWatch call fails."""
        mock_cloudwatch.put_metric_data.side_effect = Exception("CW error")

        result = _emit_cloudwatch_alarm(3)

        assert result is False


class TestDisableHealthCollection:
    """Tests for disable_health_collection function."""

    def test_stores_disabled_flag(self, mock_config_table):
        """Should store the disabled flag with the reason."""
        result = disable_health_collection("AccessDeniedException: permissions insufficient")

        assert result['success'] is True
        assert 'AccessDeniedException' in result['reason']
        mock_config_table.put_item.assert_called_once()
        call_args = mock_config_table.put_item.call_args[1]['Item']
        assert call_args['service_name'] == HEALTH_DISABLED_KEY
        assert call_args['disabled'] is True
        assert 'AccessDeniedException' in call_args['reason']

    def test_returns_error_on_failure(self, mock_config_table):
        """Should return error dict if DynamoDB write fails."""
        mock_config_table.put_item.side_effect = Exception("DB error")

        result = disable_health_collection("some reason")

        assert result['success'] is False
        assert 'error' in result


class TestIsHealthCollectionEnabled:
    """Tests for is_health_collection_enabled function."""

    def test_returns_true_when_no_disabled_flag(self, mock_config_table):
        """Should return True when no disabled entry exists."""
        mock_config_table.get_item.return_value = {}

        assert is_health_collection_enabled() is True

    def test_returns_false_when_disabled(self, mock_config_table):
        """Should return False when the disabled flag is set."""
        mock_config_table.get_item.return_value = {
            'Item': {
                'service_name': HEALTH_DISABLED_KEY,
                'disabled': True,
                'reason': 'test'
            }
        }

        assert is_health_collection_enabled() is False

    def test_returns_true_when_disabled_is_false(self, mock_config_table):
        """Should return True when the disabled field is explicitly False."""
        mock_config_table.get_item.return_value = {
            'Item': {
                'service_name': HEALTH_DISABLED_KEY,
                'disabled': False,
            }
        }

        assert is_health_collection_enabled() is True

    def test_returns_true_on_error(self, mock_config_table):
        """Should default to True (fail-open) if DynamoDB read fails."""
        mock_config_table.get_item.side_effect = Exception("DB error")

        assert is_health_collection_enabled() is True


class TestEnableHealthCollection:
    """Tests for enable_health_collection function."""

    def test_re_enables_collection(self, mock_config_table):
        """Should set disabled=False and reset counter."""
        result = enable_health_collection()

        assert result['success'] is True
        assert mock_config_table.put_item.call_count == 2  # disabled flag + counter reset

    def test_returns_error_on_failure(self, mock_config_table):
        """Should return error dict if DynamoDB write fails."""
        mock_config_table.put_item.side_effect = Exception("DB error")

        result = enable_health_collection()

        assert result['success'] is False
        assert 'error' in result
