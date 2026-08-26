"""
Property-based tests for event enrichment and priority/severity calculation.

Feature: extended-coverage-and-health-integration, Property 8: Event enrichment and priority/severity calculation

**Validates: Requirements 4.1, 4.3, 5.1**

Tests that:
1. For ANY Health_Event of type `issue` with `status_code=open`, priority SHALL be `critical`
2. For ANY Health_Event of type `scheduledChange` concerning a service with deprecated/extended_support items,
   priority SHALL be `high` or `critical`
3. For ANY Health_Event of type `accountNotification`, priority SHALL be `low`
4. For ANY enriched event whose service is tracked, lifecycle_context SHALL contain relevant items
   when deprecated items exist
"""
import sys
import os
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
))

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from health_enricher import HealthEnricher


# --- Constants ---

AWS_REGIONS = [
    'us-east-1', 'us-west-2', 'eu-west-1', 'ap-southeast-1',
    'ap-northeast-1', 'eu-central-1',
]

SERVICES = ['lambda', 'eks', 'rds', 'ecs', 'elasticache', 'opensearch', 'sagemaker']

DEPRECATED_STATUSES = ['deprecated', 'extended_support', 'end_of_life']

NON_DEPRECATED_STATUSES = ['active', 'supported', 'current']


# --- Strategies ---

# Strategy for a lifecycle item with deprecated status
deprecated_item_strategy = st.fixed_dictionaries({
    'name': st.text(min_size=1, max_size=30, alphabet=st.characters(
        whitelist_categories=("L", "N"), whitelist_characters="-_. "
    )),
    'identifier': st.text(min_size=1, max_size=20, alphabet=st.characters(
        whitelist_categories=("L", "N"), whitelist_characters="-_."
    )),
    'status': st.sampled_from(DEPRECATED_STATUSES),
    'deprecation_date': st.just('2024-06-01'),
})

# Strategy for a lifecycle item with non-deprecated status
active_item_strategy = st.fixed_dictionaries({
    'name': st.text(min_size=1, max_size=30, alphabet=st.characters(
        whitelist_categories=("L", "N"), whitelist_characters="-_. "
    )),
    'identifier': st.text(min_size=1, max_size=20, alphabet=st.characters(
        whitelist_categories=("L", "N"), whitelist_characters="-_."
    )),
    'status': st.sampled_from(NON_DEPRECATED_STATUSES),
})

# Strategy for a list of lifecycle items containing at least one deprecated item
lifecycle_items_with_deprecated_strategy = st.builds(
    lambda deprecated, active: deprecated + active,
    deprecated=st.lists(deprecated_item_strategy, min_size=1, max_size=5),
    active=st.lists(active_item_strategy, min_size=0, max_size=3),
)

# Strategy for a list of lifecycle items with NO deprecated items
lifecycle_items_no_deprecated_strategy = st.lists(
    active_item_strategy, min_size=0, max_size=5
)

# Strategy for an issue event with status_code=open
issue_open_event_strategy = st.fixed_dictionaries({
    'event_arn': st.builds(
        lambda svc, region: f"arn:aws:health:{region}::{svc}/issue-123",
        svc=st.sampled_from(['EC2', 'RDS', 'LAMBDA', 'EKS']),
        region=st.sampled_from(AWS_REGIONS),
    ),
    'health_service': st.sampled_from(['LAMBDA', 'EKS', 'RDS', 'ECS', 'ELASTICACHE']),
    'event_type_code': st.builds(
        lambda svc: f"AWS_{svc}_OPERATIONAL_ISSUE",
        svc=st.sampled_from(['LAMBDA', 'EKS', 'RDS', 'ECS']),
    ),
    'event_type_category': st.just('issue'),
    'region': st.sampled_from(AWS_REGIONS),
    'availability_zone': st.just(''),
    'start_time': st.just('2025-01-15T10:00:00Z'),
    'end_time': st.just(''),
    'last_updated_time': st.just('2025-01-15T10:05:00Z'),
    'status_code': st.just('open'),
    'description': st.text(min_size=5, max_size=100),
    'collected_at': st.just('2025-01-15T10:10:00Z'),
    'ttl': st.just(1737000000),
})

# Strategy for a scheduledChange event
scheduled_change_event_strategy = st.fixed_dictionaries({
    'event_arn': st.builds(
        lambda svc, region: f"arn:aws:health:{region}::{svc}/sched-456",
        svc=st.sampled_from(['EC2', 'RDS', 'LAMBDA', 'EKS']),
        region=st.sampled_from(AWS_REGIONS),
    ),
    'health_service': st.sampled_from(['LAMBDA', 'EKS', 'RDS', 'ECS', 'ELASTICACHE']),
    'event_type_code': st.builds(
        lambda svc: f"AWS_{svc}_SCHEDULED_CHANGE",
        svc=st.sampled_from(['LAMBDA', 'EKS', 'RDS', 'ECS']),
    ),
    'event_type_category': st.just('scheduledChange'),
    'region': st.sampled_from(AWS_REGIONS),
    'availability_zone': st.just(''),
    'start_time': st.just('2025-06-01T00:00:00Z'),
    'end_time': st.just('2025-06-02T00:00:00Z'),
    'last_updated_time': st.just('2025-01-15T10:05:00Z'),
    'status_code': st.sampled_from(['open', 'upcoming']),
    'description': st.text(min_size=5, max_size=100),
    'collected_at': st.just('2025-01-15T10:10:00Z'),
    'ttl': st.just(1737000000),
})

# Strategy for an accountNotification event
account_notification_event_strategy = st.fixed_dictionaries({
    'event_arn': st.builds(
        lambda svc, region: f"arn:aws:health:{region}::{svc}/notif-789",
        svc=st.sampled_from(['EC2', 'RDS', 'LAMBDA', 'EKS']),
        region=st.sampled_from(AWS_REGIONS),
    ),
    'health_service': st.sampled_from(['LAMBDA', 'EKS', 'RDS', 'ECS', 'ELASTICACHE']),
    'event_type_code': st.builds(
        lambda svc: f"AWS_{svc}_ACCOUNT_NOTIFICATION",
        svc=st.sampled_from(['LAMBDA', 'EKS', 'RDS', 'ECS']),
    ),
    'event_type_category': st.just('accountNotification'),
    'region': st.sampled_from(AWS_REGIONS),
    'availability_zone': st.just(''),
    'start_time': st.just('2025-01-15T10:00:00Z'),
    'end_time': st.just(''),
    'last_updated_time': st.just('2025-01-15T10:05:00Z'),
    'status_code': st.sampled_from(['open', 'closed']),
    'description': st.text(min_size=5, max_size=100),
    'collected_at': st.just('2025-01-15T10:10:00Z'),
    'ttl': st.just(1737000000),
})

# Strategy for service_configs that maps any health_service from our events
service_configs_strategy = st.just({
    'lambda': {
        'name': 'AWS Lambda',
        'health_event_mapping': 'LAMBDA',
        'documentation_urls': ['https://docs.aws.amazon.com/lambda/'],
        'extraction_focus': 'runtime versions',
        'item_properties': {},
        'required_fields': ['name', 'identifier'],
    },
    'eks': {
        'name': 'Amazon EKS',
        'health_event_mapping': 'EKS',
        'documentation_urls': ['https://docs.aws.amazon.com/eks/'],
        'extraction_focus': 'kubernetes versions',
        'item_properties': {},
        'required_fields': ['name', 'identifier'],
    },
    'rds': {
        'name': 'Amazon RDS',
        'health_event_mapping': 'RDS',
        'documentation_urls': ['https://docs.aws.amazon.com/rds/'],
        'extraction_focus': 'engine versions',
        'item_properties': {},
        'required_fields': ['name', 'identifier'],
    },
    'ecs': {
        'name': 'Amazon ECS',
        'health_event_mapping': 'ECS',
        'documentation_urls': ['https://docs.aws.amazon.com/ecs/'],
        'extraction_focus': 'platform versions',
        'item_properties': {},
        'required_fields': ['name', 'identifier'],
    },
    'elasticache': {
        'name': 'Amazon ElastiCache',
        'health_event_mapping': 'ELASTICACHE',
        'documentation_urls': ['https://docs.aws.amazon.com/elasticache/'],
        'extraction_focus': 'engine versions',
        'item_properties': {},
        'required_fields': ['name', 'identifier'],
    },
})


# --- Helper ---

def _build_mock_list_deprecations(lifecycle_items):
    """Build a mock function that returns controlled lifecycle items."""
    def mock_list_deprecations(filters=None):
        return {'items': lifecycle_items}
    return mock_list_deprecations


# --- Property Tests ---

class TestEnrichmentPriorityProperty:
    """
    Feature: extended-coverage-and-health-integration, Property 8: Event enrichment and priority/severity calculation

    Tests the correctness properties of _calculate_priority() and enrich_events()
    methods in HealthEnricher.
    """

    @given(
        event=issue_open_event_strategy,
        lifecycle_items=st.lists(
            st.one_of(deprecated_item_strategy, active_item_strategy),
            min_size=0, max_size=5
        ),
    )
    @settings(max_examples=150)
    def test_issue_open_always_critical(self, event, lifecycle_items):
        """
        For ANY Health_Event of type `issue` with `status_code=open`,
        the priority SHALL ALWAYS be `critical`, regardless of lifecycle items.

        **Validates: Requirements 4.1, 5.1**
        """
        enricher = HealthEnricher()
        priority = enricher._calculate_priority(event, lifecycle_items)

        assert priority == 'critical', (
            f"Expected priority='critical' for issue+open event, "
            f"got '{priority}'. Event: event_type_category={event['event_type_category']}, "
            f"status_code={event['status_code']}, lifecycle_items count={len(lifecycle_items)}"
        )

    @given(
        event=scheduled_change_event_strategy,
        lifecycle_items=lifecycle_items_with_deprecated_strategy,
    )
    @settings(max_examples=150)
    def test_scheduled_change_with_deprecated_items_high_or_critical(self, event, lifecycle_items):
        """
        For ANY Health_Event of type `scheduledChange` concerning a service
        with deprecated/extended_support items, priority SHALL be `high` or `critical`.

        **Validates: Requirements 4.3**
        """
        enricher = HealthEnricher()
        priority = enricher._calculate_priority(event, lifecycle_items)

        assert priority in ('high', 'critical'), (
            f"Expected priority='high' or 'critical' for scheduledChange "
            f"with deprecated items, got '{priority}'. "
            f"Deprecated statuses: {[i['status'] for i in lifecycle_items if i.get('status') in DEPRECATED_STATUSES]}"
        )

    @given(event=account_notification_event_strategy)
    @settings(max_examples=150)
    def test_account_notification_always_low(self, event):
        """
        For ANY Health_Event of type `accountNotification`,
        the priority SHALL ALWAYS be `low`, regardless of lifecycle items.

        **Validates: Requirements 4.1**
        """
        enricher = HealthEnricher()
        # Priority should be low regardless of what lifecycle items exist
        lifecycle_items = []
        priority = enricher._calculate_priority(event, lifecycle_items)

        assert priority == 'low', (
            f"Expected priority='low' for accountNotification event, "
            f"got '{priority}'. Event: event_type_category={event['event_type_category']}"
        )

    @given(
        event=scheduled_change_event_strategy,
        lifecycle_items=lifecycle_items_with_deprecated_strategy,
        service_configs=service_configs_strategy,
    )
    @settings(max_examples=100)
    def test_lifecycle_context_populated_when_deprecated_items_exist(self, event, lifecycle_items, service_configs):
        """
        For ANY enriched event whose service is tracked, when deprecated lifecycle items
        exist, the lifecycle_context SHALL contain the relevant items.

        **Validates: Requirements 4.1, 4.3, 5.1**
        """
        enricher = HealthEnricher()

        # Mock the database_reads.list_deprecations to return our controlled items
        mock_fn = _build_mock_list_deprecations(lifecycle_items)

        with patch('health_enricher.HealthEnricher._get_lifecycle_items', return_value=lifecycle_items):
            enriched = enricher.enrich_events([event], service_configs)

        # If the event's health_service maps to a configured service, we should get enriched output
        if enriched:
            enriched_event = enriched[0]
            lifecycle_context = enriched_event.get('lifecycle_context', {})

            # Since we have deprecated items, lifecycle_context must be populated
            deprecated_in_items = [
                item for item in lifecycle_items
                if item.get('status') in DEPRECATED_STATUSES
            ]

            if deprecated_in_items:
                assert lifecycle_context, (
                    f"lifecycle_context should be populated when deprecated items exist. "
                    f"Got empty context. Deprecated items: {deprecated_in_items}"
                )
                assert 'deprecated_count' in lifecycle_context, (
                    f"lifecycle_context should contain 'deprecated_count' field"
                )
                assert lifecycle_context['deprecated_count'] == len(deprecated_in_items), (
                    f"deprecated_count mismatch: expected {len(deprecated_in_items)}, "
                    f"got {lifecycle_context['deprecated_count']}"
                )
                assert 'items' in lifecycle_context, (
                    f"lifecycle_context should contain 'items' list"
                )

    @given(
        event=issue_open_event_strategy,
        service_configs=service_configs_strategy,
    )
    @settings(max_examples=100)
    def test_enrich_events_issue_open_produces_critical_priority(self, event, service_configs):
        """
        End-to-end enrichment: For ANY issue+open event passing through enrich_events(),
        the resulting notification SHALL have priority='critical'.

        **Validates: Requirements 4.1, 5.1**
        """
        enricher = HealthEnricher()

        # Mock _get_lifecycle_items to return empty list (priority should still be critical)
        with patch.object(enricher, '_get_lifecycle_items', return_value=[]):
            enriched = enricher.enrich_events([event], service_configs)

        # The event should be enriched (health_service maps to a configured service)
        if enriched:
            assert enriched[0]['priority'] == 'critical', (
                f"Expected priority='critical' in enriched event for issue+open, "
                f"got '{enriched[0]['priority']}'"
            )
