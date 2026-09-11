"""
Property-based tests for Health event transformation completeness.

Feature: extended-coverage-and-health-integration, Property 5: Health event transformation completeness

**Validates: Requirements 3.2, 6.4**

Tests that _format_event() ALWAYS produces a record containing all required fields
(event_arn, health_service, event_type_code, event_type_category, region, start_time,
end_time, status_code, description, collected_at, ttl) and that the TTL field equals
the collection timestamp + 90 days in Unix seconds.
"""
import sys
import os
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
))

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from health_collector import HealthCollector


# --- Constants ---

REQUIRED_OUTPUT_FIELDS = frozenset([
    'event_arn',
    'health_service',
    'event_type_code',
    'event_type_category',
    'region',
    'start_time',
    'end_time',
    'status_code',
    'description',
    'collected_at',
    'ttl',
])

# 90 days in seconds
TTL_90_DAYS_SECONDS = 90 * 24 * 60 * 60

# Tolerance for TTL check: allow 5 seconds drift (test execution time)
TTL_TOLERANCE_SECONDS = 5

# Valid event type categories from AWS Health API
EVENT_TYPE_CATEGORIES = ['issue', 'accountNotification', 'scheduledChange']

# Valid status codes from AWS Health API
STATUS_CODES = ['open', 'closed', 'upcoming']

# Sample AWS regions
AWS_REGIONS = [
    'us-east-1', 'us-west-2', 'eu-west-1', 'ap-southeast-1',
    'ap-northeast-1', 'eu-central-1', 'sa-east-1', 'ca-central-1',
]


# --- Strategies ---

# Strategy for ARN-like strings
arn_strategy = st.builds(
    lambda service, region, account, resource_id: (
        f"arn:aws:health:{region}::{service}/{resource_id}"
    ),
    service=st.sampled_from(['EC2', 'RDS', 'LAMBDA', 'S3', 'EKS', 'ECS', 'DYNAMODB']),
    region=st.sampled_from(AWS_REGIONS),
    account=st.from_regex(r'[0-9]{12}', fullmatch=True),
    resource_id=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
        min_size=5,
        max_size=50,
    ),
)

# Strategy for service names as returned by Health API
health_service_strategy = st.sampled_from([
    'EC2', 'RDS', 'LAMBDA', 'S3', 'EKS', 'ECS', 'DYNAMODB',
    'ELASTICACHE', 'CLOUDFRONT', 'APIGATEWAY', 'SNS', 'SQS',
    'KINESIS', 'SAGEMAKER', 'BEDROCK', 'OPENSEARCH',
])

# Strategy for event type codes
event_type_code_strategy = st.builds(
    lambda service, suffix: f"AWS_{service}_{suffix}",
    service=st.sampled_from(['EC2', 'RDS', 'LAMBDA', 'EKS', 'ECS']),
    suffix=st.sampled_from([
        'OPERATIONAL_ISSUE', 'MAINTENANCE', 'SCHEDULED_CHANGE',
        'SECURITY_NOTIFICATION', 'INSTANCE_RETIREMENT',
        'PERSISTENCE_ISSUE', 'API_ISSUE',
    ]),
)

# Strategy for datetime objects (past to near future)
datetime_strategy = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 12, 31),
    timezones=st.just(timezone.utc),
)

# Optional datetime (can be None)
optional_datetime_strategy = st.one_of(st.none(), datetime_strategy)

# Strategy for a valid Health API event structure
health_event_strategy = st.fixed_dictionaries({
    'arn': arn_strategy,
    'service': health_service_strategy,
    'eventTypeCode': event_type_code_strategy,
    'eventTypeCategory': st.sampled_from(EVENT_TYPE_CATEGORIES),
    'region': st.sampled_from(AWS_REGIONS),
    'statusCode': st.sampled_from(STATUS_CODES),
    'startTime': datetime_strategy,
    'endTime': optional_datetime_strategy,
    'lastUpdatedTime': optional_datetime_strategy,
    'availabilityZone': st.one_of(
        st.none(),
        st.builds(
            lambda region, suffix: f"{region}{suffix}",
            region=st.sampled_from(AWS_REGIONS),
            suffix=st.sampled_from(['a', 'b', 'c', 'd']),
        ),
    ),
})


# --- Property Tests ---

class TestHealthEventTransformationProperty:
    """
    Feature: extended-coverage-and-health-integration, Property 5: Health event transformation completeness

    For any event returned by the Health API (valid structure), the transformation
    SHALL produce a record containing all required fields and a TTL field equal to
    the collection timestamp + 90 days in Unix seconds.
    """

    def setup_method(self):
        """Create a HealthCollector instance for testing (no actual API calls)."""
        # We only call _format_event which doesn't use the boto3 client
        # So we can safely instantiate with mocked region
        self._original_client = HealthCollector.__init__

        # Patch __init__ to avoid boto3 client creation
        def patched_init(self_inner, region='us-east-1'):
            self_inner.region = region
            self_inner.client = None  # Not needed for _format_event

        HealthCollector.__init__ = patched_init
        self.collector = HealthCollector()

    def teardown_method(self):
        """Restore original __init__."""
        HealthCollector.__init__ = self._original_client

    @given(event=health_event_strategy)
    @settings(max_examples=150)
    def test_transformation_contains_all_required_fields(self, event):
        """
        For ANY valid Health API event structure, _format_event() MUST produce
        a record containing ALL required fields.

        **Validates: Requirements 3.2, 6.4**
        """
        result = self.collector._format_event(event)

        missing_fields = REQUIRED_OUTPUT_FIELDS - set(result.keys())
        assert not missing_fields, (
            f"Transformed event is missing required fields: {missing_fields}. "
            f"Input event ARN: {event['arn']}"
        )

    @given(event=health_event_strategy)
    @settings(max_examples=150)
    def test_ttl_equals_collected_at_plus_90_days(self, event):
        """
        For ANY valid Health API event, the TTL field SHALL equal the
        collection timestamp + 90 days in Unix seconds (within tolerance).

        **Validates: Requirements 6.4**
        """
        before_time = int(time.time())
        result = self.collector._format_event(event)
        after_time = int(time.time())

        ttl = result['ttl']
        expected_ttl_min = before_time + TTL_90_DAYS_SECONDS
        expected_ttl_max = after_time + TTL_90_DAYS_SECONDS + TTL_TOLERANCE_SECONDS

        assert expected_ttl_min <= ttl <= expected_ttl_max, (
            f"TTL {ttl} not within expected range "
            f"[{expected_ttl_min}, {expected_ttl_max}]. "
            f"Expected collected_at + 90 days."
        )

    @given(event=health_event_strategy)
    @settings(max_examples=150)
    def test_event_arn_equals_input_arn(self, event):
        """
        For ANY valid Health API event, the event_arn field in the output
        SHALL equal the arn field from the input event.

        **Validates: Requirements 3.2**
        """
        result = self.collector._format_event(event)

        assert result['event_arn'] == event['arn'], (
            f"event_arn mismatch: output {result['event_arn']!r} != "
            f"input {event['arn']!r}"
        )

    @given(event=health_event_strategy)
    @settings(max_examples=100)
    def test_collected_at_is_valid_iso8601(self, event):
        """
        For ANY valid Health API event, the collected_at field SHALL be
        a valid ISO 8601 timestamp.

        **Validates: Requirements 3.2**
        """
        result = self.collector._format_event(event)

        collected_at = result['collected_at']
        assert collected_at, "collected_at must not be empty"

        # Verify it's parseable as ISO 8601
        try:
            parsed = datetime.fromisoformat(collected_at)
            assert parsed.tzinfo is not None, "collected_at must include timezone info"
        except ValueError as e:
            raise AssertionError(
                f"collected_at '{collected_at}' is not valid ISO 8601: {e}"
            )

    @given(event=health_event_strategy)
    @settings(max_examples=100)
    def test_service_fields_preserved(self, event):
        """
        For ANY valid Health API event, the health_service, event_type_code,
        event_type_category, region, and status_code fields SHALL preserve
        the corresponding input values.

        **Validates: Requirements 3.2**
        """
        result = self.collector._format_event(event)

        assert result['health_service'] == event['service'], (
            f"health_service mismatch: {result['health_service']!r} != {event['service']!r}"
        )
        assert result['event_type_code'] == event['eventTypeCode'], (
            f"event_type_code mismatch: {result['event_type_code']!r} != {event['eventTypeCode']!r}"
        )
        assert result['event_type_category'] == event['eventTypeCategory'], (
            f"event_type_category mismatch: {result['event_type_category']!r} != {event['eventTypeCategory']!r}"
        )
        assert result['region'] == event['region'], (
            f"region mismatch: {result['region']!r} != {event['region']!r}"
        )
        assert result['status_code'] == event['statusCode'], (
            f"status_code mismatch: {result['status_code']!r} != {event['statusCode']!r}"
        )
