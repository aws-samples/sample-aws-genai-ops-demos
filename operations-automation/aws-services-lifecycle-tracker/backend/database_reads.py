"""
Database READ operations for AWS Services Lifecycle Tracker
These functions should be moved to a dedicated API Gateway + Lambda in the future
to reduce AgentCore costs for simple queries

This module provides read operations for:
- Service configurations (service-extraction-config table)
- Lifecycle/deprecation items (aws-services-lifecycle table)

"""
import os
import boto3
from decimal import Decimal
from typing import Dict, List, Any

from aws_utils import get_region


# Initialize DynamoDB using deployment region
region = get_region()
dynamodb = boto3.resource('dynamodb', region_name=region)
LIFECYCLE_TABLE_NAME = os.environ.get('LIFECYCLE_TABLE_NAME', 'aws-services-lifecycle')
CONFIG_TABLE_NAME = os.environ.get('CONFIG_TABLE_NAME', 'service-extraction-config')
# Backend-owned runtime state (issue #116, Option B): extraction metadata and
# control rows (e.g. the last Health cross-check) live here, separate from
# repo-owned config.
STATE_TABLE_NAME = os.environ.get('STATE_TABLE_NAME', 'service-extraction-state')
# Discovered account inventory - decoupled from the public deprecation facts.
INVENTORY_TABLE_NAME = os.environ.get('INVENTORY_TABLE_NAME', 'aws-account-inventory')

lifecycle_table = dynamodb.Table(LIFECYCLE_TABLE_NAME)
config_table = dynamodb.Table(CONFIG_TABLE_NAME)
state_table = dynamodb.Table(STATE_TABLE_NAME)
inventory_table = dynamodb.Table(INVENTORY_TABLE_NAME)


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
        
        # Defensive guard (issue #98 follow-up): control rows moved to the
        # state table under issue #116, but keep filtering '_'-prefixed keys
        # in case legacy rows remain in an existing deployment's config table.
        services = [
            s for s in services
            if not str(s.get('service_name', '')).startswith('_')
        ]
        
        # Merge backend-owned runtime state (issue #116, Option B) into each
        # config row so the UI keeps its single ServiceConfig shape. Only
        # state for known services is merged, so control rows in the state
        # table never surface here.
        state_response = state_table.scan()
        state_items = state_response.get('Items', [])
        while 'LastEvaluatedKey' in state_response:
            state_response = state_table.scan(ExclusiveStartKey=state_response['LastEvaluatedKey'])
            state_items.extend(state_response.get('Items', []))
        state_by_service = {s['service_name']: s for s in state_items if 'service_name' in s}
        for service in services:
            state = state_by_service.get(service.get('service_name'))
            if state:
                for field in ('extraction_count', 'last_extraction', 'success_rate',
                              'last_refresh_origin', 'last_extraction_duration'):
                    if field in state:
                        service[field] = state[field]
        
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
        
        def _collect(table):
            if filters.get('service'):
                response = table.query(
                    KeyConditionExpression='service_name = :service',
                    ExpressionAttributeValues={':service': filters['service']}
                )
            else:
                response = table.scan()
            
            rows = response.get('Items', [])
            
            while 'LastEvaluatedKey' in response:
                if filters.get('service'):
                    response = table.query(
                        KeyConditionExpression='service_name = :service',
                        ExpressionAttributeValues={':service': filters['service']},
                        ExclusiveStartKey=response['LastEvaluatedKey']
                    )
                else:
                    response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
                rows.extend(response.get('Items', []))
            return rows
        
        # Union: public deprecation facts + the account's discovered inventory
        # (issue #116 - inventory lives in its own table; rows stay
        # distinguishable via the inventory# item_id prefix and provenance tag).
        items = _collect(lifecycle_table) + _collect(inventory_table)
        
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
        # Exclude internal control rows ('_'-prefixed keys) so dashboard counts
        # reflect real services only, consistent with list_services (#98 follow-up).
        services = [
            s for s in services
            if not str(s.get('service_name', '')).startswith('_')
        ]
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


