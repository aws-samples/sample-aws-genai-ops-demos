"""
Health Monitoring Module - Failure tracking and graceful degradation

Tracks consecutive Health collection failures and emits CloudWatch alarms
when failures reach a configurable threshold. Implements graceful degradation
by disabling Health collection when permissions are insufficient.

The failure counter and disabled flag are stored in the service-extraction-config
DynamoDB table (config table) as special entries prefixed with '_'.

Requirements: 8.2, 9.3
"""
import os
import boto3
from datetime import datetime, timezone
from botocore.exceptions import ClientError

from aws_utils import get_region

# Config keys stored in the config table
FAILURE_COUNTER_KEY = "_health_collection_failures"
HEALTH_DISABLED_KEY = "_health_collection_disabled"

# Threshold for emitting CloudWatch alarm
CONSECUTIVE_FAILURE_THRESHOLD = 3

# CloudWatch metric details
METRIC_NAMESPACE = "AWSLifecycleTracker"
METRIC_NAME = "HealthCollectionConsecutiveFailures"


def _get_config_table():
    """Get a reference to the service-extraction-config DynamoDB table."""
    region = get_region()
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table_name = os.environ.get('CONFIG_TABLE_NAME', 'service-extraction-config')
    return dynamodb.Table(table_name)


def _get_cloudwatch_client():
    """Get a CloudWatch client."""
    region = get_region()
    return boto3.client('cloudwatch', region_name=region)


def track_collection_result(success: bool) -> dict:
    """
    Track the result of a Health collection attempt.

    On success: resets the failure counter to 0.
    On failure: increments the failure counter. If the counter reaches
    CONSECUTIVE_FAILURE_THRESHOLD (3), emits a CloudWatch alarm metric.

    Args:
        success: True if the collection succeeded, False otherwise.

    Returns:
        dict with current failure_count and whether an alarm was emitted.

    Requirements: 8.2
    """
    config_table = _get_config_table()
    current_time = datetime.now(timezone.utc).isoformat()

    if success:
        # Reset the failure counter on success
        try:
            config_table.put_item(
                Item={
                    'service_name': FAILURE_COUNTER_KEY,
                    'failure_count': 0,
                    'last_success': current_time,
                    'last_updated': current_time,
                }
            )
        except Exception as e:
            print(f"Warning: Failed to reset health failure counter: {e}")

        return {'failure_count': 0, 'alarm_emitted': False}

    # Failure path: increment the counter
    try:
        # Try to increment existing counter
        response = config_table.update_item(
            Key={'service_name': FAILURE_COUNTER_KEY},
            UpdateExpression='SET failure_count = if_not_exists(failure_count, :zero) + :one, last_failure = :now, last_updated = :now',
            ExpressionAttributeValues={
                ':zero': 0,
                ':one': 1,
                ':now': current_time,
            },
            ReturnValues='ALL_NEW'
        )
        new_count = int(response['Attributes'].get('failure_count', 1))
    except Exception as e:
        # If update fails, try to create the item
        print(f"Warning: Failed to increment failure counter, attempting put: {e}")
        try:
            config_table.put_item(
                Item={
                    'service_name': FAILURE_COUNTER_KEY,
                    'failure_count': 1,
                    'last_failure': current_time,
                    'last_updated': current_time,
                }
            )
            new_count = 1
        except Exception as put_error:
            print(f"Error: Failed to track health collection failure: {put_error}")
            return {'failure_count': -1, 'alarm_emitted': False, 'error': str(put_error)}

    # Check if we need to emit a CloudWatch alarm
    alarm_emitted = False
    if new_count >= CONSECUTIVE_FAILURE_THRESHOLD:
        alarm_emitted = _emit_cloudwatch_alarm(new_count)

    return {'failure_count': new_count, 'alarm_emitted': alarm_emitted}


def _emit_cloudwatch_alarm(failure_count: int) -> bool:
    """
    Emit a CloudWatch metric when consecutive failures reach the threshold.

    Publishes a metric data point that can trigger a CloudWatch Alarm
    configured on the HealthCollectionConsecutiveFailures metric.

    Args:
        failure_count: The current number of consecutive failures.

    Returns:
        True if the metric was successfully published, False otherwise.

    Requirements: 8.2
    """
    try:
        cloudwatch = _get_cloudwatch_client()
        cloudwatch.put_metric_data(
            Namespace=METRIC_NAMESPACE,
            MetricData=[
                {
                    'MetricName': METRIC_NAME,
                    'Value': failure_count,
                    'Unit': 'Count',
                    'Timestamp': datetime.now(timezone.utc),
                    'Dimensions': [
                        {
                            'Name': 'Application',
                            'Value': 'AWSServicesLifecycleTracker'
                        }
                    ]
                }
            ]
        )
        print(f"CloudWatch alarm metric emitted: {METRIC_NAME}={failure_count}")
        return True
    except Exception as e:
        print(f"Error: Failed to emit CloudWatch alarm metric: {e}")
        return False


def disable_health_collection(reason: str) -> dict:
    """
    Disable Health collection gracefully.

    Stores a disabled flag with the reason in the config table.
    This is used when permissions are insufficient (AccessDeniedException)
    or other unrecoverable errors occur.

    Args:
        reason: Human-readable reason for disabling (e.g.,
                "AccessDeniedException: permissions insufficient").

    Returns:
        dict with success status.

    Requirements: 9.3
    """
    config_table = _get_config_table()
    current_time = datetime.now(timezone.utc).isoformat()

    try:
        config_table.put_item(
            Item={
                'service_name': HEALTH_DISABLED_KEY,
                'disabled': True,
                'reason': reason,
                'disabled_at': current_time,
                'last_updated': current_time,
            }
        )
        print(f"Health collection disabled: {reason}")
        return {'success': True, 'reason': reason, 'disabled_at': current_time}
    except Exception as e:
        error_msg = f"Failed to disable health collection: {e}"
        print(f"Error: {error_msg}")
        return {'success': False, 'error': error_msg}


def is_health_collection_enabled() -> bool:
    """
    Check if Health collection is enabled.

    Returns False if the disabled flag is set in the config table.
    Returns True by default (Health collection is enabled unless explicitly disabled).

    Returns:
        True if Health collection is enabled, False otherwise.

    Requirements: 9.3
    """
    config_table = _get_config_table()

    try:
        response = config_table.get_item(
            Key={'service_name': HEALTH_DISABLED_KEY}
        )
        item = response.get('Item')
        if item and item.get('disabled', False):
            return False
        return True
    except Exception as e:
        # If we can't check, default to enabled (fail-open for monitoring)
        print(f"Warning: Could not check health collection status: {e}")
        return True


def enable_health_collection() -> dict:
    """
    Re-enable Health collection after it was disabled.

    Removes the disabled flag from the config table and resets the failure counter.

    Returns:
        dict with success status.
    """
    config_table = _get_config_table()
    current_time = datetime.now(timezone.utc).isoformat()

    try:
        # Remove the disabled flag
        config_table.put_item(
            Item={
                'service_name': HEALTH_DISABLED_KEY,
                'disabled': False,
                'reason': 'Re-enabled manually',
                'enabled_at': current_time,
                'last_updated': current_time,
            }
        )

        # Reset failure counter
        config_table.put_item(
            Item={
                'service_name': FAILURE_COUNTER_KEY,
                'failure_count': 0,
                'last_updated': current_time,
            }
        )

        print("Health collection re-enabled")
        return {'success': True, 'enabled_at': current_time}
    except Exception as e:
        error_msg = f"Failed to enable health collection: {e}"
        print(f"Error: {error_msg}")
        return {'success': False, 'error': error_msg}
