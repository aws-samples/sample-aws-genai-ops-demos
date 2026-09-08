"""
AWS Health Event Collector for AWS Services Lifecycle Tracker.

Collects events from the AWS Health API (global endpoint in us-east-1),
filters them by configured services, and retrieves event details.

Handles throttling with exponential backoff and pagination for large result sets.
"""

import time
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class HealthCollector:
    """AWS Health event collector."""

    def __init__(self, region: str = 'us-east-1'):
        """
        Initialize the Health client in us-east-1 (global endpoint).

        Args:
            region: AWS region for the Health client. Must be us-east-1
                    because the Health API is a global endpoint only
                    accessible from this region.
        """
        self.region = region
        self.client = boto3.client('health', region_name=self.region)

    def collect_events(self, service_filter: Optional[List[str]] = None) -> dict:
        """
        Collect active Health events.

        Args:
            service_filter: List of services to monitor (from service_configs).
                           Uses the health_event_mapping field from configs.

        Returns:
            dict with:
                - success: bool indicating overall success
                - events_collected: number of events collected
                - events_enriched: number of events with details
                - errors: list of errors encountered
                - events: list of collected events
        """
        errors: List[str] = []
        events: List[dict] = []

        # Build filter parameters.
        #
        # NOTE (issue #98, E3): the Health API caps `filter.services` at 10
        # entries, so passing all tracked services (32+) raises a
        # ValidationException on every poll. We therefore do NOT send a
        # `services` filter to the API and instead filter the results
        # client-side (see _matches_service_filter below). This mirrors the
        # existing pattern in _describe_event_details, which batches ARNs by 10
        # to respect a similar per-call limit.
        filter_params: Dict[str, Any] = {
            'eventStatusCodes': ['open', 'upcoming', 'closed']
        }

        # Describe events with pagination
        try:
            raw_events = self._describe_events(filter_params)
        except Exception as e:
            error_msg = f"Failed to describe events: {str(e)}"
            logger.error(error_msg)
            errors.append(error_msg)
            return {
                'success': False,
                'events_collected': 0,
                'events_enriched': 0,
                'errors': errors,
                'events': []
            }

        # Filter client-side against the tracked services (health_event_mapping
        # codes). When no filter is provided, keep everything.
        if service_filter:
            raw_events = [
                e for e in raw_events
                if self._matches_service_filter(e.get('service'), service_filter)
            ]

        events_collected = len(raw_events)
        logger.info(f"Collected {events_collected} health events")

        # Get event details for collected events
        events_enriched = 0
        if raw_events:
            event_arns = [event['arn'] for event in raw_events]
            try:
                details = self._describe_event_details(event_arns)
                # Merge details into events
                details_by_arn = {d['event']['arn']: d for d in details}
                for event in raw_events:
                    arn = event['arn']
                    enriched_event = self._format_event(event)
                    if arn in details_by_arn:
                        detail = details_by_arn[arn]
                        enriched_event['description'] = detail.get(
                            'eventDescription', {}
                        ).get('latestDescription', '')
                        events_enriched += 1
                    events.append(enriched_event)
            except Exception as e:
                error_msg = f"Failed to describe event details: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
                # Still return events without details
                events = [self._format_event(event) for event in raw_events]

        success = events_collected > 0 or len(errors) == 0
        return {
            'success': success,
            'events_collected': events_collected,
            'events_enriched': events_enriched,
            'errors': errors,
            'events': events
        }

    def _describe_events(self, filter_params: dict) -> List[dict]:
        """
        Call health:DescribeEvents with pagination.

        Args:
            filter_params: Filter parameters for the Health API.

        Returns:
            Complete list of paginated events.

        Raises:
            ClientError: If the API returns a non-recoverable error.
        """
        all_events: List[dict] = []
        next_token: Optional[str] = None
        attempt = 0

        while True:
            try:
                kwargs: Dict[str, Any] = {'filter': filter_params}
                if next_token:
                    kwargs['nextToken'] = next_token

                response = self.client.describe_events(**kwargs)
                events = response.get('events', [])
                all_events.extend(events)

                next_token = response.get('nextToken')
                if not next_token:
                    break

                # Reset attempt counter on success
                attempt = 0

            except ClientError as e:
                error_code = e.response['Error']['Code']
                if error_code in ('Throttling', 'TooManyRequestsException'):
                    attempt += 1
                    if not self._apply_backoff(attempt):
                        raise
                elif error_code == 'AccessDeniedException':
                    logger.error(
                        "Access denied to Health API. "
                        "Ensure IAM permissions include: "
                        "health:DescribeEvents, health:DescribeEventDetails, "
                        "health:DescribeAffectedEntities, health:DescribeEventTypes"
                    )
                    raise
                else:
                    raise

        return all_events

    def _describe_event_details(self, event_arns: List[str]) -> List[dict]:
        """
        Retrieve details for a list of event ARNs.

        The Health API limits to 10 ARNs per call, so requests are batched.

        Args:
            event_arns: List of event ARNs to retrieve details for.

        Returns:
            List of event details.

        Raises:
            ClientError: If the API returns a non-recoverable error.
        """
        all_details: List[dict] = []
        batch_size = 10  # API limit per call
        attempt = 0

        for i in range(0, len(event_arns), batch_size):
            batch = event_arns[i:i + batch_size]

            while True:
                try:
                    response = self.client.describe_event_details(
                        eventArns=batch
                    )
                    successful = response.get('successfulSet', [])
                    failed = response.get('failedSet', [])

                    all_details.extend(successful)

                    if failed:
                        for failure in failed:
                            logger.warning(
                                f"Failed to get details for event "
                                f"{failure.get('eventArn', 'unknown')}: "
                                f"{failure.get('errorName', 'unknown')} - "
                                f"{failure.get('errorMessage', '')}"
                            )

                    # Reset attempt counter on success
                    attempt = 0
                    break

                except ClientError as e:
                    error_code = e.response['Error']['Code']
                    if error_code in ('Throttling', 'TooManyRequestsException'):
                        attempt += 1
                        if not self._apply_backoff(attempt):
                            raise
                    else:
                        raise

        return all_details

    def _apply_backoff(self, attempt: int, base_delay: float = 1.0, max_attempts: int = 5) -> bool:
        """
        Apply exponential backoff on throttling.

        Delay = 2^(N-1) * base_delay seconds.
        Returns False if attempt > max_attempts.

        Args:
            attempt: Current attempt number (1-indexed).
            base_delay: Base delay in seconds.
            max_attempts: Maximum number of attempts.

        Returns:
            True if backoff was applied (can retry),
            False if max attempts exceeded.
        """
        if attempt > max_attempts:
            logger.error(
                f"Max retry attempts ({max_attempts}) exceeded for Health API"
            )
            return False

        delay = (2 ** (attempt - 1)) * base_delay
        logger.info(
            f"Throttled by Health API. "
            f"Attempt {attempt}/{max_attempts}, waiting {delay:.1f}s"
        )
        time.sleep(delay)
        return True

    def _format_event(self, event: dict) -> dict:
        """
        Format a raw Health API event into a standardized structure.

        Args:
            event: Raw event returned by describe_events.

        Returns:
            Formatted event with standardized fields.
        """
        collected_at = datetime.now(timezone.utc).isoformat()

        # Calculate TTL: 90 days from now
        ttl = int(time.time()) + (90 * 24 * 60 * 60)

        formatted = {
            'event_arn': event.get('arn', ''),
            'health_service': event.get('service', ''),
            'event_type_code': event.get('eventTypeCode', ''),
            'event_type_category': event.get('eventTypeCategory', ''),
            'region': event.get('region', ''),
            'availability_zone': event.get('availabilityZone', ''),
            'start_time': self._format_datetime(event.get('startTime')),
            'end_time': self._format_datetime(event.get('endTime')),
            'last_updated_time': self._format_datetime(event.get('lastUpdatedTime')),
            'status_code': event.get('statusCode', ''),
            'description': '',
            'collected_at': collected_at,
            'ttl': ttl,
        }

        return formatted

    @staticmethod
    def _matches_service_filter(event_service: Optional[str], service_filter: List[str]) -> bool:
        """
        Return True if a Health event's service matches one of the tracked
        services, compared case-insensitively.

        The Health API returns `service` as an uppercase code (e.g. 'ECS',
        'NEPTUNE'), which corresponds to the `health_event_mapping` values in
        the service configs. Used for client-side filtering because the API's
        `filter.services` parameter is capped at 10 entries (issue #98, E3).

        Args:
            event_service: The event's `service` value from the Health API.
            service_filter: Tracked service codes (from health_event_mapping).

        Returns:
            True if the event should be kept, False otherwise.
        """
        if not event_service:
            return False
        allowed = {s.upper() for s in service_filter if s}
        return event_service.upper() in allowed

    @staticmethod
    def _format_datetime(dt) -> str:
        """
        Format a datetime to ISO 8601 string.

        Args:
            dt: Datetime object or None.

        Returns:
            ISO 8601 string or empty string if None.
        """
        if dt is None:
            return ''
        if isinstance(dt, datetime):
            return dt.isoformat()
        return str(dt)
