"""
Property-based tests for Health event API multi-criteria filtering.

Feature: extended-coverage-and-health-integration, Property 10: Health event API filtering correctness

**Validates: Requirements 5.3**

For *any* set of Health_Events stored and *for any* combination of filters
(service, event_type_category, severity, status_code), the API SHALL return
only events satisfying ALL filters simultaneously (conjunction/AND logic).
"""
import sys
import os

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
))

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from health_reads import _apply_post_filters


# --- Strategies ---

# Valid values for each filterable field
SERVICES = ['lambda', 'ec2', 'rds', 'eks', 'ecs', 's3', 'dynamodb', 'sagemaker']
EVENT_TYPE_CATEGORIES = ['issue', 'accountNotification', 'scheduledChange']
SEVERITIES = ['critical', 'high', 'medium', 'low']
STATUS_CODES = ['open', 'closed', 'upcoming']


@st.composite
def health_event_strategy(draw):
    """Generate a single Health event with valid field values."""
    service = draw(st.sampled_from(SERVICES))
    return {
        'event_arn': draw(st.builds(
            lambda suffix: f"arn:aws:health:us-east-1::event/{suffix}",
            suffix=st.text(alphabet='abcdef0123456789', min_size=6, max_size=12),
        )),
        'service_name': service,
        'health_service': service.upper(),
        'event_type_category': draw(st.sampled_from(EVENT_TYPE_CATEGORIES)),
        'severity': draw(st.sampled_from(SEVERITIES)),
        'status_code': draw(st.sampled_from(STATUS_CODES)),
        'region': draw(st.sampled_from(['us-east-1', 'eu-west-1', 'ap-southeast-1'])),
        'start_time': '2025-06-01T00:00:00Z',
        'end_time': '',
        'description': 'Test event',
    }


@st.composite
def health_events_list_strategy(draw):
    """Generate a list of 0 to 20 Health events."""
    num_events = draw(st.integers(min_value=0, max_value=20))
    events = [draw(health_event_strategy()) for _ in range(num_events)]
    return events


@st.composite
def filter_combination_strategy(draw):
    """
    Generate an arbitrary combination of filters.
    Each filter key is independently included or excluded.
    When included, a value is drawn from the valid set for that key.
    """
    filters = {}

    if draw(st.booleans()):
        filters['service'] = draw(st.sampled_from(SERVICES))

    if draw(st.booleans()):
        filters['event_type_category'] = draw(st.sampled_from(EVENT_TYPE_CATEGORIES))

    if draw(st.booleans()):
        filters['severity'] = draw(st.sampled_from(SEVERITIES))

    if draw(st.booleans()):
        filters['status_code'] = draw(st.sampled_from(STATUS_CODES))

    return filters


def event_matches_filters(event: dict, filters: dict) -> bool:
    """
    Reference implementation: returns True if the event satisfies ALL filters.
    Uses the same matching logic as _apply_post_filters.
    """
    if filters.get('service'):
        service = filters['service']
        if event.get('service_name') != service and event.get('health_service') != service:
            return False

    if filters.get('event_type_category'):
        if event.get('event_type_category') != filters['event_type_category']:
            return False

    if filters.get('status_code'):
        if event.get('status_code') != filters['status_code']:
            return False

    if filters.get('severity'):
        if event.get('severity') != filters['severity']:
            return False

    return True


# --- Property Tests ---

class TestHealthFilteringProperty:
    """
    Feature: extended-coverage-and-health-integration, Property 10: Health event API filtering correctness

    For *any* set of Health_Events stored and *for any* combination of filters
    (service, event_type_category, severity, status_code), the API SHALL return
    only events satisfying ALL filters simultaneously.
    """

    @given(
        events=health_events_list_strategy(),
        filters=filter_combination_strategy(),
    )
    @settings(max_examples=200)
    def test_filtered_output_contains_only_matching_events(self, events, filters):
        """
        For ANY set of events and ANY filter combination, the filtered output
        SHALL contain ONLY events that match ALL applied filters (soundness).

        **Validates: Requirements 5.3**
        """
        result = _apply_post_filters(events, filters)

        for event in result:
            assert event_matches_filters(event, filters), (
                f"Event does not satisfy all filters.\n"
                f"Event: service_name={event.get('service_name')}, "
                f"event_type_category={event.get('event_type_category')}, "
                f"severity={event.get('severity')}, "
                f"status_code={event.get('status_code')}\n"
                f"Filters: {filters}"
            )

    @given(
        events=health_events_list_strategy(),
        filters=filter_combination_strategy(),
    )
    @settings(max_examples=200)
    def test_no_matching_event_is_excluded(self, events, filters):
        """
        For ANY set of events and ANY filter combination, NO event that
        satisfies ALL filters SHALL be excluded from the output (completeness).

        **Validates: Requirements 5.3**
        """
        result = _apply_post_filters(events, filters)

        # Compute expected: events that should be in the result
        expected = [e for e in events if event_matches_filters(e, filters)]

        assert len(result) == len(expected), (
            f"Expected {len(expected)} events in filtered output, "
            f"but got {len(result)}.\n"
            f"Filters: {filters}\n"
            f"Missing events: {[e for e in expected if e not in result]}"
        )

    @given(
        events=health_events_list_strategy(),
        filters=filter_combination_strategy(),
    )
    @settings(max_examples=200)
    def test_conjunction_logic_all_filters_applied_simultaneously(self, events, filters):
        """
        The filtering SHALL apply ALL filters as a conjunction (AND logic).
        The result set is the intersection of individually filtered sets.

        **Validates: Requirements 5.3**
        """
        assume(len(filters) >= 2)

        result = set(id(e) for e in _apply_post_filters(events, filters))

        # Apply each filter individually and compute intersection
        individual_results = []
        for key in filters:
            single_filter = {key: filters[key]}
            single_result = set(id(e) for e in _apply_post_filters(events, single_filter))
            individual_results.append(single_result)

        # Intersection of all individual filter results
        expected_intersection = individual_results[0]
        for s in individual_results[1:]:
            expected_intersection = expected_intersection & s

        assert result == expected_intersection, (
            f"Multi-filter result does not equal intersection of individual filters.\n"
            f"Filters: {filters}\n"
            f"Multi-filter count: {len(result)}, "
            f"Intersection count: {len(expected_intersection)}"
        )

    @given(events=health_events_list_strategy())
    @settings(max_examples=150)
    def test_empty_filters_returns_all_events(self, events):
        """
        When no filters are applied (empty dict), ALL events SHALL be returned.

        **Validates: Requirements 5.3**
        """
        result = _apply_post_filters(events, {})

        assert len(result) == len(events), (
            f"Expected all {len(events)} events with empty filters, "
            f"got {len(result)}"
        )

    @given(
        events=health_events_list_strategy(),
        filters=filter_combination_strategy(),
    )
    @settings(max_examples=150)
    def test_filter_preserves_event_order(self, events, filters):
        """
        The filtered output SHALL preserve the relative order of events
        from the input list.

        **Validates: Requirements 5.3**
        """
        result = _apply_post_filters(events, filters)

        # Verify order preservation: each result event appears in the same
        # relative order as in the input
        result_indices = []
        for res_event in result:
            for i, input_event in enumerate(events):
                if input_event is res_event:
                    result_indices.append(i)
                    break

        assert result_indices == sorted(result_indices), (
            f"Filtered output does not preserve input order.\n"
            f"Result indices: {result_indices}"
        )
