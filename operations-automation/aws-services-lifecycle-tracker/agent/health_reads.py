"""
Database READ operations for AWS Health Events.

Provides functions to query, filter, and summarize health events
stored in the aws-health-events DynamoDB table.

These functions should be moved to a dedicated API Gateway + Lambda in the future
to reduce AgentCore costs for simple queries.
"""
import os
import logging
from typing import Dict, List, Any, Optional

import boto3
from boto3.dynamodb.conditions import Key, Attr
from decimal import Decimal

from aws_utils import get_region

logger = logging.getLogger(__name__)

# Initialize DynamoDB using deployment region
region = get_region()
dynamodb = boto3.resource('dynamodb', region_name=region)
HEALTH_TABLE_NAME = os.environ.get('HEALTH_TABLE_NAME', 'aws-health-events')

health_table = dynamodb.Table(HEALTH_TABLE_NAME)


def convert_decimals(obj):
    """Recursively convert Decimal objects to float for JSON serialization"""
    if isinstance(obj, list):
        return [convert_decimals(item) for item in obj]
    elif isinstance(obj, dict):
        return {key: convert_decimals(value) for key, value in obj.items()}
    elif isinstance(obj, Decimal):
        return float(obj)
    else:
        return obj


# ============================================================================
# READ OPERATIONS - Health Events
# ============================================================================

def list_health_events(filters: Optional[Dict[str, str]] = None) -> dict:
    """
    Liste les événements Health avec filtres optionnels.

    Filtres supportés:
        - service: nom du service (utilise le GSI service-index)
        - event_type_category: issue | accountNotification | scheduledChange
        - status_code: open | closed | upcoming (utilise le GSI status-index)
        - severity: critical | high | medium | low

    Stratégie de requête:
        - Si filtre 'service' fourni → query sur GSI service-index
        - Si filtre 'status_code' fourni (sans service) → query sur GSI status-index
        - Sinon → scan de la table complète
        - Les autres filtres sont appliqués en post-filtrage côté client

    FUTURE: Move to API Gateway + Lambda
    Cost: Currently uses AgentCore (~$0.001/request)
    Future: API Gateway + Lambda (~$0.0000002/request)

    Args:
        filters: Dictionnaire de filtres optionnels.

    Returns:
        dict avec clé 'events' (liste d'événements) ou 'error' en cas d'échec.
    """
    try:
        filters = filters or {}
        items: List[dict] = []

        if filters.get('service'):
            # Use the service-index GSI for efficient query
            items = _query_by_service(filters['service'])
        elif filters.get('status_code'):
            # Use the status-index GSI for efficient query
            items = _query_by_status(filters['status_code'])
        else:
            # Full table scan when no indexed filter is provided
            items = _scan_health_table()

        # Apply remaining filters in-memory
        items = _apply_post_filters(items, filters)

        items = convert_decimals(items)
        return {'events': items}
    except Exception as e:
        logger.error(f"Failed to list health events: {str(e)}")
        return {'error': f'Failed to list health events: {str(e)}'}


def get_health_event(event_arn: str) -> dict:
    """
    Récupère un événement Health spécifique par son ARN.

    L'ARN est la partition key de la table. Comme la sort key
    (event_type_category) n'est pas fournie, on utilise une query
    sur la partition key seule pour récupérer l'événement.

    FUTURE: Move to API Gateway + Lambda

    Args:
        event_arn: L'ARN unique de l'événement Health.

    Returns:
        dict avec clé 'event' (détails de l'événement) ou 'error' en cas d'échec.
    """
    try:
        if not event_arn:
            return {'error': 'event_arn is required'}

        response = health_table.query(
            KeyConditionExpression=Key('event_arn').eq(event_arn)
        )

        items = response.get('Items', [])
        if not items:
            return {'error': f'Health event not found: {event_arn}'}

        # Return the first (and typically only) matching item
        event = convert_decimals(items[0])
        return {'event': event}
    except Exception as e:
        logger.error(f"Failed to get health event {event_arn}: {str(e)}")
        return {'error': f'Failed to get health event: {str(e)}'}


def get_health_summary() -> dict:
    """
    Retourne un résumé des événements Health actifs par service et catégorie.

    Calcule:
        - Nombre total d'événements actifs (status_code != 'closed')
        - Répartition par service (service_name)
        - Répartition par catégorie (event_type_category)
        - Répartition par sévérité (severity)

    FUTURE: Move to API Gateway + Lambda
    Used by: Dashboard Health Panel in UI

    Returns:
        dict avec clé 'summary' contenant les métriques agrégées,
        ou 'error' en cas d'échec.
    """
    try:
        # Scan all events (could optimize with status-index for active only)
        all_items = _scan_health_table()

        # Filter to active events only (not closed)
        active_items = [
            item for item in all_items
            if item.get('status_code') in ('open', 'upcoming')
        ]

        # Aggregate by service
        by_service: Dict[str, int] = {}
        for item in active_items:
            service = item.get('service_name') or item.get('health_service', 'unknown')
            by_service[service] = by_service.get(service, 0) + 1

        # Aggregate by event_type_category
        by_category: Dict[str, int] = {}
        for item in active_items:
            category = item.get('event_type_category', 'unknown')
            by_category[category] = by_category.get(category, 0) + 1

        # Aggregate by severity
        by_severity: Dict[str, int] = {}
        for item in active_items:
            severity = item.get('severity', 'unknown')
            by_severity[severity] = by_severity.get(severity, 0) + 1

        summary = {
            'total_active_events': len(active_items),
            'by_service': by_service,
            'by_category': by_category,
            'by_severity': by_severity,
        }

        summary = convert_decimals(summary)
        return {'summary': summary}
    except Exception as e:
        logger.error(f"Failed to get health summary: {str(e)}")
        return {'error': f'Failed to get health summary: {str(e)}'}


# ============================================================================
# INTERNAL HELPERS
# ============================================================================

def _query_by_service(service_name: str) -> List[dict]:
    """
    Query health events by service using the service-index GSI.

    Args:
        service_name: Name of the service to filter on.

    Returns:
        List of matching items.
    """
    items: List[dict] = []
    response = health_table.query(
        IndexName='service-index',
        KeyConditionExpression=Key('service_name').eq(service_name)
    )
    items.extend(response.get('Items', []))

    while 'LastEvaluatedKey' in response:
        response = health_table.query(
            IndexName='service-index',
            KeyConditionExpression=Key('service_name').eq(service_name),
            ExclusiveStartKey=response['LastEvaluatedKey']
        )
        items.extend(response.get('Items', []))

    return items


def _query_by_status(status_code: str) -> List[dict]:
    """
    Query health events by status using the status-index GSI.

    Args:
        status_code: Status code to filter on (open, closed, upcoming).

    Returns:
        List of matching items.
    """
    items: List[dict] = []
    response = health_table.query(
        IndexName='status-index',
        KeyConditionExpression=Key('status_code').eq(status_code)
    )
    items.extend(response.get('Items', []))

    while 'LastEvaluatedKey' in response:
        response = health_table.query(
            IndexName='status-index',
            KeyConditionExpression=Key('status_code').eq(status_code),
            ExclusiveStartKey=response['LastEvaluatedKey']
        )
        items.extend(response.get('Items', []))

    return items


def _scan_health_table() -> List[dict]:
    """
    Scan the entire health events table.

    Returns:
        List of all items in the table.
    """
    items: List[dict] = []
    response = health_table.scan()
    items.extend(response.get('Items', []))

    while 'LastEvaluatedKey' in response:
        response = health_table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        items.extend(response.get('Items', []))

    return items


def _apply_post_filters(items: List[dict], filters: Dict[str, str]) -> List[dict]:
    """
    Apply post-query filters that cannot be handled by DynamoDB indexes.

    When a GSI is used for the primary filter (e.g., service or status_code),
    the remaining filters must be applied in-memory.

    Args:
        items: List of items already retrieved from DynamoDB.
        filters: Dictionary of all requested filters.

    Returns:
        Filtered list of items satisfying ALL filter criteria.
    """
    filtered = items

    # Apply service filter (in case items came from a non-service GSI query)
    if filters.get('service'):
        service = filters['service']
        filtered = [
            item for item in filtered
            if item.get('service_name') == service
            or item.get('health_service') == service
        ]

    # Apply event_type_category filter
    if filters.get('event_type_category'):
        category = filters['event_type_category']
        filtered = [
            item for item in filtered
            if item.get('event_type_category') == category
        ]

    # Apply status_code filter (in case items came from a non-status GSI query)
    if filters.get('status_code'):
        status = filters['status_code']
        filtered = [
            item for item in filtered
            if item.get('status_code') == status
        ]

    # Apply severity filter
    if filters.get('severity'):
        severity = filters['severity']
        filtered = [
            item for item in filtered
            if item.get('severity') == severity
        ]

    return filtered
