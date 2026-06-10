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
    """Collecteur d'événements AWS Health."""

    def __init__(self, region: str = 'us-east-1'):
        """
        Initialise le client Health en us-east-1 (endpoint global).

        Args:
            region: Région AWS pour le client Health. Doit être us-east-1
                    car l'API Health est un endpoint global accessible
                    uniquement depuis cette région.
        """
        self.region = region
        self.client = boto3.client('health', region_name=self.region)

    def collect_events(self, service_filter: Optional[List[str]] = None) -> dict:
        """
        Collecte les événements Health actifs.

        Args:
            service_filter: Liste des services à surveiller (depuis service_configs).
                           Utilise le champ health_event_mapping des configs.

        Returns:
            dict avec:
                - success: bool indiquant le succès global
                - events_collected: nombre d'événements collectés
                - events_enriched: nombre d'événements avec détails
                - errors: liste des erreurs rencontrées
                - events: liste des événements collectés
        """
        errors: List[str] = []
        events: List[dict] = []

        # Build filter parameters
        filter_params: Dict[str, Any] = {
            'eventStatusCodes': ['open', 'upcoming', 'closed']
        }

        if service_filter:
            filter_params['services'] = service_filter

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
        Appelle health:DescribeEvents avec pagination.

        Args:
            filter_params: Paramètres de filtre pour l'API Health.

        Returns:
            Liste complète des événements paginés.

        Raises:
            ClientError: Si l'API retourne une erreur non-récupérable.
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
        Récupère les détails pour une liste d'ARNs d'événements.

        L'API Health limite à 10 ARNs par appel, donc on batch les requêtes.

        Args:
            event_arns: Liste des ARNs d'événements pour lesquels
                       récupérer les détails.

        Returns:
            Liste des détails d'événements.

        Raises:
            ClientError: Si l'API retourne une erreur non-récupérable.
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
        Applique un backoff exponentiel en cas de throttling.

        Delay = 2^(N-1) * base_delay seconds.
        Returns False if attempt > max_attempts.

        Args:
            attempt: Numéro de la tentative courante (1-indexed).
            base_delay: Délai de base en secondes.
            max_attempts: Nombre maximum de tentatives.

        Returns:
            True si le backoff a été appliqué (on peut réessayer),
            False si le nombre maximum de tentatives est dépassé.
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
        Formate un événement brut de l'API Health en structure standardisée.

        Args:
            event: Événement brut retourné par describe_events.

        Returns:
            Événement formaté avec champs standardisés.
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
    def _format_datetime(dt) -> str:
        """
        Formate un datetime en ISO 8601 string.

        Args:
            dt: Objet datetime ou None.

        Returns:
            String ISO 8601 ou chaîne vide si None.
        """
        if dt is None:
            return ''
        if isinstance(dt, datetime):
            return dt.isoformat()
        return str(dt)
