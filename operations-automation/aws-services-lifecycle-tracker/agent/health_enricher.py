"""
AWS Health Event Enricher for AWS Services Lifecycle Tracker.

Correlates Health events with lifecycle data to provide enriched notifications
with priority calculation, service mapping, and time-remaining information
for scheduled changes.

Requirements covered: 4.1, 4.2, 4.3, 5.1, 5.2
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class HealthEnricher:
    """Enriches Health events with lifecycle data."""

    def enrich_events(self, events: List[dict], service_configs: dict) -> List[dict]:
        """
        Enrich events with lifecycle data.

        For each event:
        1. Map the Health service to the lifecycle service_name (via health_event_mapping)
        2. Retrieve the lifecycle items for the matched service
        3. Add the deprecated / end-of-support versions
        4. Calculate the priority (higher for scheduledChange + deprecated items)

        Args:
            events: List of raw Health events (from HealthCollector).
            service_configs: Dictionary of service configurations
                            (contents of service_configs.json['services']).

        Returns:
            List of enriched events ready for storage.
        """
        enriched_events: List[dict] = []

        for event in events:
            health_service = event.get('health_service', '')

            # Step 1: Map health service name to internal service_name
            service_name = self._map_service_name(health_service, service_configs)

            if service_name is None:
                logger.debug(
                    f"Health service '{health_service}' not mapped to any "
                    f"configured service. Event filtered out."
                )
                continue

            # Step 2: Retrieve lifecycle items for the service
            lifecycle_items = self._get_lifecycle_items(service_name, service_configs)

            # Step 3: Build enrichment context
            enrichment = {
                'service_name': service_name,
                'lifecycle_items': lifecycle_items,
                'deprecated_items': [
                    item for item in lifecycle_items
                    if item.get('status') in ('deprecated', 'extended_support', 'end_of_life')
                ],
            }

            # Step 4: Calculate priority
            priority = self._calculate_priority(event, lifecycle_items)
            enrichment['priority'] = priority

            # Step 5: Format final notification
            notification = self._format_health_notification(event, enrichment)
            enriched_events.append(notification)

        logger.info(
            f"Enriched {len(enriched_events)} events out of {len(events)} total"
        )
        return enriched_events

    def _map_service_name(self, health_service: str, service_configs: dict) -> Optional[str]:
        """
        Map the Health API service name to the internal service_name.

        Looks in service_configs for a service whose 'health_event_mapping'
        field matches the Health service name. If no explicit mapping is
        found, falls back to a case-insensitive match on the service name.

        Args:
            health_service: Service name as returned by the Health API
                           (e.g. 'LAMBDA', 'EKS', 'RDS').
            service_configs: Dictionary of service configurations.

        Returns:
            The matching internal service_name, or None if not found.
        """
        if not health_service:
            return None

        # First pass: look for explicit health_event_mapping
        for service_key, config in service_configs.items():
            mapping = config.get('health_event_mapping', '')
            if mapping and mapping.upper() == health_service.upper():
                return service_key

        # Second pass: try case-insensitive match on service key
        health_lower = health_service.lower()
        for service_key in service_configs:
            if service_key.lower() == health_lower:
                return service_key

        return None

    def _calculate_priority(self, event: dict, lifecycle_items: List[dict]) -> str:
        """
        Calculate the priority: critical, high, medium, low.

        Rules:
        - issue with status_code=open → critical
        - scheduledChange with deprecated/extended_support items → high or critical
        - scheduledChange without lifecycle concerns → medium
        - accountNotification → low

        Args:
            event: Health event with event_type_category and status_code fields.
            lifecycle_items: List of lifecycle items for the matched service.

        Returns:
            Calculated priority: 'critical', 'high', 'medium', or 'low'.
        """
        event_type_category = event.get('event_type_category', '')
        status_code = event.get('status_code', '')

        # Rule 1: issue with status_code=open → critical
        if event_type_category == 'issue' and status_code == 'open':
            return 'critical'

        # Rule 2: scheduledChange with deprecated/extended_support items
        if event_type_category == 'scheduledChange':
            deprecated_items = [
                item for item in lifecycle_items
                if item.get('status') in ('deprecated', 'extended_support', 'end_of_life')
            ]

            if deprecated_items:
                # Critical if items are end_of_life, high otherwise
                has_eol = any(
                    item.get('status') == 'end_of_life'
                    for item in deprecated_items
                )
                return 'critical' if has_eol else 'high'

            # Rule 3: scheduledChange without lifecycle concerns → medium
            return 'medium'

        # Rule 4: accountNotification → low
        if event_type_category == 'accountNotification':
            return 'low'

        # Default: issue with non-open status or unknown type
        if event_type_category == 'issue':
            return 'high'

        return 'medium'

    def _format_health_notification(self, event: dict, enrichment: dict) -> dict:
        """
        Format the final notification for storage.

        Includes: time_remaining for future scheduledChange events.

        Args:
            event: Health event formatted by HealthCollector.
            enrichment: Enrichment data including service_name,
                       lifecycle_items, deprecated_items, and priority.

        Returns:
            Formatted notification ready for DynamoDB storage.
        """
        notification = {
            # Core event fields
            'event_arn': event.get('event_arn', ''),
            'health_service': event.get('health_service', ''),
            'service_name': enrichment.get('service_name', ''),
            'event_type_code': event.get('event_type_code', ''),
            'event_type_category': event.get('event_type_category', ''),
            'region': event.get('region', ''),
            'availability_zone': event.get('availability_zone', ''),
            'start_time': event.get('start_time', ''),
            'end_time': event.get('end_time', ''),
            'last_updated_time': event.get('last_updated_time', ''),
            'status_code': event.get('status_code', ''),
            'description': event.get('description', ''),

            # Enrichment fields
            'priority': enrichment.get('priority', 'medium'),
            'lifecycle_context': self._build_lifecycle_context(enrichment),
            'notification_status': self._determine_notification_status(event),

            # Metadata
            'collected_at': event.get('collected_at', ''),
            'ttl': event.get('ttl', 0),
        }

        # Add time_remaining for future scheduledChange events
        if event.get('event_type_category') == 'scheduledChange':
            time_remaining = self._calculate_time_remaining(event.get('start_time', ''))
            if time_remaining is not None:
                notification['time_remaining'] = time_remaining

        return notification

    def _get_lifecycle_items(self, service_name: str, service_configs: dict) -> List[dict]:
        """
        Retrieve lifecycle items from DynamoDB for a given service.

        Attempts to read from the lifecycle table via database_reads.
        Returns an empty list on error.

        Args:
            service_name: Internal service name.
            service_configs: Service configurations (for context).

        Returns:
            List of lifecycle items for the service.
        """
        try:
            from database_reads import list_deprecations
            result = list_deprecations(filters={'service': service_name})
            if 'error' in result:
                logger.warning(
                    f"Error fetching lifecycle items for {service_name}: "
                    f"{result['error']}"
                )
                return []
            return result.get('items', [])
        except ImportError:
            logger.warning(
                "database_reads module not available. "
                "Lifecycle enrichment disabled."
            )
            return []
        except Exception as e:
            logger.warning(
                f"Failed to fetch lifecycle items for {service_name}: {str(e)}"
            )
            return []

    def _build_lifecycle_context(self, enrichment: dict) -> dict:
        """
        Build the lifecycle context for inclusion in the notification.

        Args:
            enrichment: Enrichment data.

        Returns:
            Lifecycle context dictionary (deprecated versions, dates).
        """
        deprecated_items = enrichment.get('deprecated_items', [])

        if not deprecated_items:
            return {}

        context = {
            'deprecated_count': len(deprecated_items),
            'items': []
        }

        for item in deprecated_items:
            context_item = {
                'name': item.get('name', ''),
                'identifier': item.get('identifier', ''),
                'status': item.get('status', ''),
            }
            # Include relevant date fields if present
            for date_field in ('deprecation_date', 'end_of_standard_support_date',
                               'end_of_extended_support_date', 'end_of_life_date'):
                if item.get(date_field):
                    context_item[date_field] = item[date_field]

            context['items'].append(context_item)

        return context

    def _determine_notification_status(self, event: dict) -> str:
        """
        Determine the notification status based on the event.

        Args:
            event: Health event.

        Returns:
            'active' or 'resolved'.
        """
        status_code = event.get('status_code', '')
        if status_code == 'closed':
            return 'resolved'
        return 'active'

    @staticmethod
    def _calculate_time_remaining(start_time_str: str) -> Optional[str]:
        """
        Calculate the time remaining before a future scheduledChange event.

        Args:
            start_time_str: Start date in ISO 8601 format.

        Returns:
            Human-readable string representing the time remaining
            (e.g. '5 days, 3 hours'), or None if the event is in the
            past or the date is invalid.
        """
        if not start_time_str:
            return None

        try:
            start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)

            if start_time <= now:
                return None

            delta = start_time - now
            total_seconds = int(delta.total_seconds())

            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60

            parts = []
            if days > 0:
                parts.append(f"{days} day{'s' if days != 1 else ''}")
            if hours > 0:
                parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
            if minutes > 0 and days == 0:
                parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")

            if not parts:
                return "less than 1 minute"

            return ", ".join(parts)

        except (ValueError, TypeError):
            logger.warning(f"Invalid start_time format: {start_time_str}")
            return None
