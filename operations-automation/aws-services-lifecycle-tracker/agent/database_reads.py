"""
Database READ operations for AWS Services Lifecycle Tracker
These functions should be moved to a dedicated API Gateway + Lambda in the future
to reduce AgentCore costs for simple queries

This module provides read operations for:
- Service configurations (service-extraction-config table)
- Lifecycle/deprecation items (aws-services-lifecycle table)
- Health events (aws-health-events table, via health_reads module)

Health event reads are delegated to the dedicated health_reads module which
handles GSI queries (service-index, status-index), pagination, and combined filters.
"""
import os
import boto3
from decimal import Decimal
from typing import Dict, List, Any

from aws_utils import get_region

# Re-export Health read functions for discoverability
# Health reads use GSI indexes (service-index, status-index) for efficient access
from health_reads import (
    list_health_events,
    get_health_event,
    get_health_summary,
)


# Initialize DynamoDB using deployment region
region = get_region()
dynamodb = boto3.resource('dynamodb', region_name=region)
LIFECYCLE_TABLE_NAME = os.environ.get('LIFECYCLE_TABLE_NAME', 'aws-services-lifecycle')
CONFIG_TABLE_NAME = os.environ.get('CONFIG_TABLE_NAME', 'service-extraction-config')

lifecycle_table = dynamodb.Table(LIFECYCLE_TABLE_NAME)
config_table = dynamodb.Table(CONFIG_TABLE_NAME)


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
# READ OPERATIONS
# ============================================================================

def get_service_config(service_name: str) -> dict:
    """
    Get service configuration from DynamoDB
    
    FUTURE: Move to API Gateway + Lambda
    Cost: Currently uses AgentCore (~$0.001/request)
    Future: API Gateway + Lambda (~$0.0000002/request)
    """
    try:
        response = config_table.get_item(Key={'service_name': service_name})
        if 'Item' not in response:
            return {'error': f'Service configuration not found: {service_name}'}
        return response['Item']
    except Exception as e:
        return {'error': f'Error retrieving service config: {str(e)}'}


def list_services() -> dict:
    """
    List all service configurations
    
    FUTURE: Move to API Gateway + Lambda
    Used by: Services page in UI
    """
    try:
        response = config_table.scan()
        services = response.get('Items', [])
        
        while 'LastEvaluatedKey' in response:
            response = config_table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            services.extend(response.get('Items', []))
        
        services = convert_decimals(services)
        return {'services': services}
    except Exception as e:
        return {'error': f'Failed to list services: {str(e)}'}


def list_deprecations(filters: dict = None) -> dict:
    """
    List deprecation items with optional filters
    
    FUTURE: Move to API Gateway + Lambda
    Used by: Deprecations page, Timeline page in UI
    """
    try:
        filters = filters or {}
        
        if filters.get('service'):
            response = lifecycle_table.query(
                KeyConditionExpression='service_name = :service',
                ExpressionAttributeValues={':service': filters['service']}
            )
        else:
            response = lifecycle_table.scan()
        
        items = response.get('Items', [])
        
        while 'LastEvaluatedKey' in response:
            if filters.get('service'):
                response = lifecycle_table.query(
                    KeyConditionExpression='service_name = :service',
                    ExpressionAttributeValues={':service': filters['service']},
                    ExclusiveStartKey=response['LastEvaluatedKey']
                )
            else:
                response = lifecycle_table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            items.extend(response.get('Items', []))
        
        if filters.get('status'):
            items = [item for item in items if item.get('status') == filters['status']]
        

        
        items = convert_decimals(items)
        return {'items': items}
    except Exception as e:
        return {'error': f'Failed to list deprecations: {str(e)}'}


def get_metrics() -> dict:
    """
    Get dashboard metrics
    
    FUTURE: Move to API Gateway + Lambda
    Used by: Dashboard page in UI
    """
    try:
        services_response = config_table.scan()
        services = services_response.get('Items', [])
        total_services = len(services)
        enabled_services = sum(1 for s in services if s.get('enabled', False))
        
        items_response = lifecycle_table.scan()
        items = items_response.get('Items', [])
        total_items = len(items)
        
        by_status = {'deprecated': 0, 'extended_support': 0, 'end_of_life': 0}
        by_service = {}  # Count items per service
        
        for item in items:
            status = item.get('status', '')
            if status in by_status:
                by_status[status] += 1
            
            # Count items per service
            service_name = item.get('service_name', '')
            if service_name:
                by_service[service_name] = by_service.get(service_name, 0) + 1
        
        metrics = {
            'total_services': total_services,
            'enabled_services': enabled_services,
            'total_items': total_items,
            'by_status': by_status,
            'by_service': by_service,  # Add per-service counts
            'recent_extractions': []
        }
        
        metrics = convert_decimals(metrics)
        return {'metrics': metrics}
    except Exception as e:
        return {'error': f'Failed to get metrics: {str(e)}'}


# ============================================================================
# HEALTH EVENT READ OPERATIONS (delegated to health_reads module)
# ============================================================================
#
# The following functions are re-exported from health_reads.py for unified
# access through this module. They query the aws-health-events DynamoDB table
# using GSI indexes for efficient access:
#
#   list_health_events(filters) - Query with GSI service-index or status-index
#       Supported filters: service, event_type_category, status_code, severity
#       Uses service-index GSI when filtering by service (Req 6.2)
#       Uses status-index GSI when filtering by status_code (Req 6.3)
#       Supports pagination via LastEvaluatedKey
#       Applies combined filters in post-processing
#
#   get_health_event(event_arn) - Get single event by ARN (partition key query)
#
#   get_health_summary() - Aggregated summary by service, category, severity
#
# See health_reads.py for implementation details.
# ============================================================================


def get_combined_metrics() -> dict:
    """
    Get combined dashboard metrics including both lifecycle and health data.

    Merges lifecycle metrics with health summary for a unified dashboard view.

    FUTURE: Move to API Gateway + Lambda
    Used by: Dashboard page in UI (combined view)

    Returns:
        dict with 'metrics' containing lifecycle data plus 'health_summary'.
    """
    try:
        # Get lifecycle metrics
        lifecycle_metrics = get_metrics()
        if 'error' in lifecycle_metrics:
            return lifecycle_metrics

        # Get health summary
        health_summary_result = get_health_summary()
        if 'error' in health_summary_result:
            # Graceful degradation: return lifecycle metrics without health
            lifecycle_metrics['metrics']['health_summary'] = {
                'total_active_events': 0,
                'by_service': {},
                'by_category': {},
                'by_severity': {},
                'health_available': False,
            }
            return lifecycle_metrics

        # Combine both
        lifecycle_metrics['metrics']['health_summary'] = {
            **health_summary_result.get('summary', {}),
            'health_available': True,
        }

        return lifecycle_metrics
    except Exception as e:
        return {'error': f'Failed to get combined metrics: {str(e)}'}
