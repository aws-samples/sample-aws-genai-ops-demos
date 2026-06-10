"""
Concurrency lock mechanism for AWS Services Lifecycle Tracker.

Uses DynamoDB conditional writes to prevent parallel execution of
health collection (or other exclusive operations).

The lock is stored in the service-extraction-config table with a special
service_name key (e.g., '_health_collection_lock').
"""
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from aws_utils import get_region


# Initialize DynamoDB
region = get_region()
dynamodb = boto3.resource('dynamodb', region_name=region)
CONFIG_TABLE_NAME = os.environ.get('CONFIG_TABLE_NAME', 'service-extraction-config')
config_table = dynamodb.Table(CONFIG_TABLE_NAME)

# Module-level state to track the current lock holder for release validation
_current_lock_holder: Optional[str] = None


def acquire_lock(
    table_name: str = None,
    lock_id: str = '_health_collection_lock',
    ttl_minutes: int = 10
) -> bool:
    """
    Acquire a concurrency lock via DynamoDB conditional write.

    Uses a conditional put_item that succeeds only if:
    - The lock item does not exist (attribute_not_exists), OR
    - The existing lock has expired (expires_at < current time)

    Args:
        table_name: DynamoDB table name. Defaults to CONFIG_TABLE_NAME env var.
        lock_id: The service_name key for the lock item.
        ttl_minutes: Lock expiration time in minutes (default: 10).

    Returns:
        True if the lock was successfully acquired, False if another
        execution holds the lock (ConditionalCheckFailedException).
    """
    global _current_lock_holder

    table = _get_table(table_name)
    now = datetime.now(timezone.utc)
    lock_holder = str(uuid.uuid4())
    expires_at = now + timedelta(minutes=ttl_minutes)

    try:
        table.put_item(
            Item={
                'service_name': lock_id,
                'lock_holder': lock_holder,
                'acquired_at': now.isoformat(),
                'expires_at': expires_at.isoformat(),
            },
            ConditionExpression=(
                'attribute_not_exists(service_name) OR expires_at < :now'
            ),
            ExpressionAttributeValues={
                ':now': now.isoformat(),
            },
        )
        _current_lock_holder = lock_holder
        return True

    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return False
        raise


def release_lock(
    table_name: str = None,
    lock_id: str = '_health_collection_lock'
) -> None:
    """
    Release a concurrency lock from DynamoDB.

    Only releases the lock if the current process holds it (lock_holder matches).

    Args:
        table_name: DynamoDB table name. Defaults to CONFIG_TABLE_NAME env var.
        lock_id: The service_name key for the lock item.
    """
    global _current_lock_holder

    if _current_lock_holder is None:
        return

    table = _get_table(table_name)

    try:
        table.delete_item(
            Key={'service_name': lock_id},
            ConditionExpression='lock_holder = :holder',
            ExpressionAttributeValues={
                ':holder': _current_lock_holder,
            },
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            # Lock is held by someone else or already released — nothing to do
            pass
        else:
            raise
    finally:
        _current_lock_holder = None


def _get_table(table_name: Optional[str]):
    """Return the DynamoDB Table resource for the given name or default."""
    if table_name:
        return dynamodb.Table(table_name)
    return config_table
