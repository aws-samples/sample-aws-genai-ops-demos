"""
Unit tests for the Health-related router actions in main.py.

Tests the routing of the 4 new Health actions:
- collect_health_events
- list_health_events
- get_health_event
- get_health_summary

Also tests manual collection trigger and concurrency lock blocking.

**Validates: Requirements 8.3**
"""
import sys
import os
from unittest.mock import patch, MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock heavy dependencies that main.py transitively imports
# These aren't available in the test environment (bs4, boto3, bedrock, etc.)
_mock_modules = [
    'aws_utils',
    'bedrock_agentcore',
    'bedrock_agentcore.runtime',
    'bs4',
    'requests',
    'boto3',
    'boto3.dynamodb',
    'boto3.dynamodb.conditions',
    'botocore',
    'botocore.exceptions',
]
for mod_name in _mock_modules:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

sys.modules['aws_utils'].get_region = MagicMock(return_value='us-east-1')

# Now we can safely import main
import pytest


@pytest.fixture(autouse=True)
def _isolate_main(monkeypatch):
    """
    Ensure 'main' module is freshly imported for each test to avoid state leaks.
    We patch at the function level to avoid module-level import issues.
    """
    # Remove cached main module so each test class gets a clean state
    if 'main' in sys.modules:
        # We keep it loaded since mocks are set up globally
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_handle_api_action():
    """Import handle_api_action from main (safe after mocks are set up)."""
    from main import handle_api_action
    return handle_api_action


# ---------------------------------------------------------------------------
# Test Classes
# ---------------------------------------------------------------------------


class TestRouteCollectHealthEvents:
    """Tests that 'collect_health_events' action routes to _handle_collect_health_events."""

    @patch('main.track_collection_result')
    @patch('main.is_health_collection_enabled', return_value=True)
    @patch('main.release_lock')
    @patch('main.acquire_lock', return_value=True)
    @patch('main.HealthEnricher')
    @patch('main.HealthCollector')
    @patch('main._batch_write_health_events', return_value=(2, []))
    @patch('main.list_services', return_value={
        'services': [
            {'service_name': 'lambda', 'enabled': True, 'health_event_mapping': 'LAMBDA'},
            {'service_name': 'eks', 'enabled': True, 'health_event_mapping': 'EKS'},
        ]
    })
    def test_collect_health_events_routes_correctly(
        self, mock_list_services, mock_batch_write, mock_collector_cls,
        mock_enricher_cls, mock_acquire, mock_release, mock_enabled, mock_track
    ):
        """handle_api_action('collect_health_events', {...}) routes to _handle_collect_health_events."""
        handle_api_action = _import_handle_api_action()

        # Setup collector mock
        mock_collector = MagicMock()
        mock_collector.collect_events.return_value = {
            'success': True,
            'events_collected': 2,
            'events_enriched': 2,
            'errors': [],
            'events': [
                {'event_arn': 'arn:aws:health:us-east-1::event/LAMBDA/001', 'health_service': 'LAMBDA'},
                {'event_arn': 'arn:aws:health:us-east-1::event/EKS/002', 'health_service': 'EKS'},
            ]
        }
        mock_collector_cls.return_value = mock_collector

        # Setup enricher mock
        mock_enricher = MagicMock()
        mock_enricher.enrich_events.return_value = [
            {'event_arn': 'arn:aws:health:us-east-1::event/LAMBDA/001', 'event_type_category': 'issue', 'service_name': 'lambda'},
            {'event_arn': 'arn:aws:health:us-east-1::event/EKS/002', 'event_type_category': 'scheduledChange', 'service_name': 'eks'},
        ]
        mock_enricher_cls.return_value = mock_enricher

        result = handle_api_action('collect_health_events', {})

        # Verify routing happened correctly
        assert result['success'] is True
        assert result['events_collected'] == 2
        assert result['events_enriched'] == 2
        mock_acquire.assert_called_once()
        mock_collector.collect_events.assert_called_once()
        mock_enricher.enrich_events.assert_called_once()
        mock_release.assert_called_once()

    @patch('main.track_collection_result')
    @patch('main.is_health_collection_enabled', return_value=True)
    @patch('main.release_lock')
    @patch('main.acquire_lock', return_value=True)
    @patch('main.HealthEnricher')
    @patch('main.HealthCollector')
    @patch('main._batch_write_health_events', return_value=(0, []))
    @patch('main.list_services', return_value={
        'services': [
            {'service_name': 'lambda', 'enabled': True, 'health_event_mapping': 'LAMBDA'},
        ]
    })
    def test_collect_health_events_passes_service_filter(
        self, mock_list_services, mock_batch_write, mock_collector_cls,
        mock_enricher_cls, mock_acquire, mock_release, mock_enabled, mock_track
    ):
        """Service filter is built from health_event_mapping in configs."""
        handle_api_action = _import_handle_api_action()

        mock_collector = MagicMock()
        mock_collector.collect_events.return_value = {
            'success': True,
            'events_collected': 0,
            'events_enriched': 0,
            'errors': [],
            'events': []
        }
        mock_collector_cls.return_value = mock_collector

        mock_enricher = MagicMock()
        mock_enricher.enrich_events.return_value = []
        mock_enricher_cls.return_value = mock_enricher

        handle_api_action('collect_health_events', {})

        # Verify the service_filter includes LAMBDA from config
        call_kwargs = mock_collector.collect_events.call_args[1]
        assert 'LAMBDA' in call_kwargs['service_filter']


class TestRouteListHealthEvents:
    """Tests that 'list_health_events' action routes to list_health_events."""

    @patch('main.list_health_events')
    def test_list_health_events_routes_correctly(self, mock_list_health):
        """handle_api_action('list_health_events', {'filters': {...}}) routes to list_health_events."""
        handle_api_action = _import_handle_api_action()

        mock_list_health.return_value = {
            'events': [
                {'event_arn': 'arn:aws:health:us-east-1::event/LAMBDA/001', 'status_code': 'open'}
            ]
        }

        filters = {'service': 'lambda', 'status_code': 'open'}
        result = handle_api_action('list_health_events', {'filters': filters})

        # Verify routing
        mock_list_health.assert_called_once_with(filters)
        assert 'events' in result
        assert len(result['events']) == 1

    @patch('main.list_health_events')
    def test_list_health_events_with_empty_filters(self, mock_list_health):
        """list_health_events is called with empty dict when no filters provided."""
        handle_api_action = _import_handle_api_action()

        mock_list_health.return_value = {'events': []}

        result = handle_api_action('list_health_events', {})

        mock_list_health.assert_called_once_with({})
        assert result == {'events': []}


class TestRouteGetHealthEvent:
    """Tests that 'get_health_event' action routes to get_health_event."""

    @patch('main.get_health_event')
    def test_get_health_event_routes_correctly(self, mock_get_event):
        """handle_api_action('get_health_event', {'event_arn': '...'}) routes to get_health_event."""
        handle_api_action = _import_handle_api_action()

        test_arn = 'arn:aws:health:us-east-1::event/LAMBDA/AWS_LAMBDA_OPERATIONAL_ISSUE/001'
        mock_get_event.return_value = {
            'event': {
                'event_arn': test_arn,
                'service_name': 'lambda',
                'status_code': 'open',
                'event_type_category': 'issue',
            }
        }

        result = handle_api_action('get_health_event', {'event_arn': test_arn})

        mock_get_event.assert_called_once_with(test_arn)
        assert 'event' in result
        assert result['event']['event_arn'] == test_arn

    @patch('main.get_health_event')
    def test_get_health_event_missing_arn(self, mock_get_event):
        """get_health_event is called with None when event_arn not in payload."""
        handle_api_action = _import_handle_api_action()

        mock_get_event.return_value = {'error': 'event_arn is required'}

        result = handle_api_action('get_health_event', {})

        mock_get_event.assert_called_once_with(None)
        assert 'error' in result


class TestRouteGetHealthSummary:
    """Tests that 'get_health_summary' action routes to get_health_summary."""

    @patch('main.get_health_summary')
    def test_get_health_summary_routes_correctly(self, mock_summary):
        """handle_api_action('get_health_summary', {}) routes to get_health_summary."""
        handle_api_action = _import_handle_api_action()

        mock_summary.return_value = {
            'summary': {
                'total_active_events': 3,
                'by_service': {'lambda': 2, 'eks': 1},
                'by_category': {'issue': 1, 'scheduledChange': 2},
                'by_severity': {'critical': 1, 'high': 2},
            }
        }

        result = handle_api_action('get_health_summary', {})

        mock_summary.assert_called_once()
        assert 'summary' in result
        assert result['summary']['total_active_events'] == 3


class TestManualCollectionTrigger:
    """Tests manual collection triggered via API (simulates EventBridge or direct invocation)."""

    @patch('main.track_collection_result')
    @patch('main.is_health_collection_enabled', return_value=True)
    @patch('main.release_lock')
    @patch('main.acquire_lock', return_value=True)
    @patch('main.HealthEnricher')
    @patch('main.HealthCollector')
    @patch('main._batch_write_health_events', return_value=(1, []))
    @patch('main.list_services', return_value={
        'services': [
            {'service_name': 'rds', 'enabled': True, 'health_event_mapping': 'RDS'},
        ]
    })
    def test_manual_collection_via_api_action(
        self, mock_list_services, mock_batch_write, mock_collector_cls,
        mock_enricher_cls, mock_acquire, mock_release, mock_enabled, mock_track
    ):
        """Manual collection can be triggered via handle_api_action('collect_health_events', {})."""
        handle_api_action = _import_handle_api_action()

        mock_collector = MagicMock()
        mock_collector.collect_events.return_value = {
            'success': True,
            'events_collected': 1,
            'events_enriched': 1,
            'errors': [],
            'events': [
                {'event_arn': 'arn:aws:health:us-east-1::event/RDS/001', 'health_service': 'RDS'},
            ]
        }
        mock_collector_cls.return_value = mock_collector

        mock_enricher = MagicMock()
        mock_enricher.enrich_events.return_value = [
            {'event_arn': 'arn:aws:health:us-east-1::event/RDS/001', 'event_type_category': 'issue', 'service_name': 'rds'},
        ]
        mock_enricher_cls.return_value = mock_enricher

        result = handle_api_action('collect_health_events', {})

        assert result['success'] is True
        assert result['events_collected'] == 1
        assert result['events_written'] == 1

    @patch('main.track_collection_result')
    @patch('main.is_health_collection_enabled', return_value=True)
    @patch('main.release_lock')
    @patch('main.acquire_lock', return_value=True)
    @patch('main.HealthEnricher')
    @patch('main.HealthCollector')
    @patch('main._batch_write_health_events', return_value=(0, []))
    @patch('main.list_services', return_value={
        'services': [
            {'service_name': 'lambda', 'enabled': True, 'health_event_mapping': 'LAMBDA'},
        ]
    })
    def test_manual_collection_releases_lock_on_success(
        self, mock_list_services, mock_batch_write, mock_collector_cls,
        mock_enricher_cls, mock_acquire, mock_release, mock_enabled, mock_track
    ):
        """Lock is always released after collection, even on success."""
        handle_api_action = _import_handle_api_action()

        mock_collector = MagicMock()
        mock_collector.collect_events.return_value = {
            'success': True,
            'events_collected': 0,
            'events_enriched': 0,
            'errors': [],
            'events': []
        }
        mock_collector_cls.return_value = mock_collector

        mock_enricher = MagicMock()
        mock_enricher.enrich_events.return_value = []
        mock_enricher_cls.return_value = mock_enricher

        handle_api_action('collect_health_events', {})

        mock_release.assert_called_once()

    @patch('main.track_collection_result')
    @patch('main.is_health_collection_enabled', return_value=True)
    @patch('main.release_lock')
    @patch('main.acquire_lock', return_value=True)
    @patch('main.HealthEnricher')
    @patch('main.HealthCollector')
    @patch('main.list_services', return_value={
        'services': [
            {'service_name': 'lambda', 'enabled': True, 'health_event_mapping': 'LAMBDA'},
        ]
    })
    def test_manual_collection_releases_lock_on_failure(
        self, mock_list_services, mock_collector_cls, mock_enricher_cls,
        mock_acquire, mock_release, mock_enabled, mock_track
    ):
        """Lock is released even when collection raises an exception."""
        handle_api_action = _import_handle_api_action()

        mock_collector = MagicMock()
        mock_collector.collect_events.side_effect = RuntimeError("Boom")
        mock_collector_cls.return_value = mock_collector

        result = handle_api_action('collect_health_events', {})

        assert result['success'] is False
        assert 'Boom' in result['error']
        mock_release.assert_called_once()


class TestCollectionBlockedByConcurrencyLock:
    """Tests that collection is blocked when lock is already held."""

    @patch('main.is_health_collection_enabled', return_value=True)
    @patch('main.release_lock')
    @patch('main.acquire_lock', return_value=False)
    def test_collection_blocked_returns_concurrent_execution(
        self, mock_acquire, mock_release, mock_enabled
    ):
        """When lock cannot be acquired, returns concurrent_execution reason."""
        handle_api_action = _import_handle_api_action()

        result = handle_api_action('collect_health_events', {})

        assert result['success'] is False
        assert result['reason'] == 'concurrent_execution'
        mock_acquire.assert_called_once()
        # Release should NOT be called since lock was never acquired
        mock_release.assert_not_called()

    @patch('main.is_health_collection_enabled', return_value=True)
    @patch('main.release_lock')
    @patch('main.acquire_lock', return_value=False)
    def test_blocked_collection_does_not_call_collector(
        self, mock_acquire, mock_release, mock_enabled
    ):
        """When lock is held, HealthCollector is never instantiated or called."""
        handle_api_action = _import_handle_api_action()

        with patch('main.HealthCollector') as mock_collector_cls:
            result = handle_api_action('collect_health_events', {})

        assert result['success'] is False
        mock_collector_cls.assert_not_called()


class TestUnknownAction:
    """Tests that unknown actions return an error."""

    def test_unknown_action_returns_error(self):
        """Unknown action returns error dict."""
        handle_api_action = _import_handle_api_action()

        result = handle_api_action('nonexistent_action', {})

        assert 'error' in result
        assert 'Unknown action' in result['error']
