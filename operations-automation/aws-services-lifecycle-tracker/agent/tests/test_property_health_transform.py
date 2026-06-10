"""
Property-based tests for Health event transformation completeness.

Feature: extended-coverage-and-health-integration, Property 5: Health event transformation completeness

**Validates: Requirements 3.2, 6.4**

Tests that _format_event() ALWAYS produces a record containing all required fields
(event_arn, service, event_type_code, event_type_category, region, start_time,
end_time, status_code, description) and that the TTL field equals the collection
timestamp + 90 days in Unix seconds.
"""
import sys
import os
import time
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
))

from hypothesis import given, settings
from hypothesis import strategies as st

from health_collector import HealthCollector


# --- Constants ---

# Required output fields per design doc Property 5
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
    lambda service, region, resource_id: (
        f"arn:aws:health:{region}::{service}/{resource_id}"
    ),
    service=st.sampled_from(['EC2', 'RDS', 'LAMBDA', 'S3', 'EKS', 'ECS', 'DYNAMODB']),
    region=st.sampled_from(AWS_REGIONS),
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


# --- Helper ---

def _create_collector():
    """Create a HealthCollector with mocked boto3 client."""
    with patch('health_collector.boto3.client') as mock_boto3:
        mock_boto3.return_value = MagicMock()
        collector = HealthCollector(region='us-east-1')
    return collector


# --- Property Tests ---

class TestHealthEventTransformProperty:
    """
    Feature: extended-coverage-and-health-integration, Property 5: Health event transformation completeness

    For any event returned by the Health API (valid structure), the transformation
    SHALL produce a record containing all required fields and a TTL field equal to
    the collection timestamp + 90 days in Unix seconds.
    """

    @given(event=health_event_strategy)
    @settings(max_examples=150)
    def test_transformation_produces_all_required_fields(self, event):
        """
        For ANY valid Health API event, _format_event() MUST produce a record
        containing ALL required fields. No field is silently dropped.

        **Validates: Requirements 3.2**
        """
        collector = _create_collector()
        result = collector._format_event(event)

        missing_fields = REQUIRED_OUTPUT_FIELDS - set(result.keys())
        assert not missing_fields, (
            f"Transformed event is missing required fields: {missing_fields}. "
            f"Input event ARN: {event['arn']}. "
            f"Output keys: {set(result.keys())}"
        )

    @given(event=health_event_strategy)
    @settings(max_examples=150)
    def test_ttl_equals_collected_at_plus_90_days(self, event):
        """
        For ANY valid Health API event, the TTL field SHALL equal approximately
        collected_at + 90 days in Unix seconds (within a small margin).

        **Validates: Requirements 6.4**
        """
        collector = _create_collector()

        before_time = int(time.time())
        result = collector._format_event(event)
        after_time = int(time.time())

        ttl = result['ttl']
        expected_min = before_time + TTL_90_DAYS_SECONDS
        expected_max = after_time + TTL_90_DAYS_SECONDS + TTL_TOLERANCE_SECONDS

        assert expected_min <= ttl <= expected_max, (
            f"TTL {ttl} is not within expected range "
            f"[{expected_min}, {expected_max}]. "
            f"Expected: collected_at + 90 days in Unix seconds."
        )

    @given(event=health_event_strategy)
    @settings(max_examples=150)
    def test_no_field_silently_dropped(self, event):
        """
        For ANY valid Health API event, the transformation SHALL NOT silently
        drop any required field (i.e., all required fields must have a value
        that is not None).

        **Validates: Requirements 3.2, 6.4**
        """
        collector = _create_collector()
        result = collector._format_event(event)

        for field in REQUIRED_OUTPUT_FIELDS:
            assert field in result, (
                f"Required field '{field}' is missing from output"
            )
            assert result[field] is not None, (
                f"Required field '{field}' is None in output. "
                f"Input event: arn={event['arn']}"
            )

    @given(event=health_event_strategy)
    @settings(max_examples=100)
    def test_input_values_correctly_mapped_to_output(self, event):
        """
        For ANY valid Health API event, the input fields SHALL be correctly
        mapped to output fields without data loss or corruption.

        **Validates: Requirements 3.2**
        """
        collector = _create_collector()
        result = collector._format_event(event)

        # Verify key field mappings
        assert result['event_arn'] == event['arn'], (
            f"event_arn mismatch: {result['event_arn']!r} != {event['arn']!r}"
        )
        assert result['health_service'] == event['service'], (
            f"health_service mismatch: {result['health_service']!r} != {event['service']!r}"
        )
        assert result['event_type_code'] == event['eventTypeCode'], (
            f"event_type_code mismatch: {result['event_type_code']!r} != {event['eventTypeCode']!r}"
        )
        assert result['event_type_category'] == event['eventTypeCategory'], (
            f"event_type_category mismatch"
        )
        assert result['region'] == event['region'], (
            f"region mismatch: {result['region']!r} != {event['region']!r}"
        )
        assert result['status_code'] == event['statusCode'], (
            f"status_code mismatch: {result['status_code']!r} != {event['statusCode']!r}"
        )
