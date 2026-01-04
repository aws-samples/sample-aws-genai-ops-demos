#!/usr/bin/env python3
"""
Data API - Handles deprecation data viewing and system metrics

This Lambda function provides API endpoints for the admin UI to:
1. View deprecation data with filtering
2. Get system health and metrics
3. Export data in various formats
4. View deprecation timelines
"""

import json
import boto3
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional
from botocore.exceptions import ClientError

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')

# Environment variables
LIFECYCLE_TABLE_NAME = os.environ['LIFECYCLE_TABLE_NAME']
CONFIG_TABLE_NAME = os.environ['CONFIG_TABLE_NAME']

# DynamoDB tables
lifecycle_table = dynamodb.Table(LIFECYCLE_TABLE_NAME)
config_table = dynamodb.Table(CONFIG_TABLE_NAME)

def lambda_handler(event, context):
    """
    Main Lambda handler for data API requests from the admin UI.
    """
    
    try:
        print(f"📥 Data API request: {json.dumps(event, indent=2)}")
        
        # Parse the API Gateway event
        http_method = event.get('httpMethod', '')
        path = event.get('path', '')
        path_parameters = event.get('pathParameters') or {}
        query_parameters = event.get('queryStringParameters') or {}
        
        # Route the request
        if http_method == 'GET' and path == '/deprecations':
            return handle_list_deprecations(query_parameters)
        elif http_method == 'GET' and '/deprecations/' in path and not '/status/' in path:
            service_name = path_parameters.get('service')
            return handle_get_service_deprecations(service_name, query_parameters)
        elif http_method == 'GET' and '/deprecations/status/' in path:
            status = path_parameters.get('status')
            return handle_get_deprecations_by_status(status, query_parameters)
        elif http_method == 'GET' and path == '/deprecations/timeline':
            return handle_get_timeline(query_parameters)
        elif http_method == 'GET' and path == '/admin/health':
            return handle_health_check()
        elif http_method == 'GET' and path == '/admin/metrics':
            return handle_get_metrics()
        else:
            return create_error_response(400, f"Unsupported request: {http_method} {path}")
            
    except Exception as e:
        print(f"💥 Data API Error: {str(e)}")
        return create_error_response(500, f"Internal server error: {str(e)}")

def handle_list_deprecations(query_parameters: Dict[str, Any]) -> Dict[str, Any]:
    """
    List all deprecations with optional filtering.
    """
    
    try:
        print("📋 Listing deprecations")
        
        # Parse query parameters
        limit = int(query_parameters.get('limit', 100))
        service_filter = query_parameters.get('service')
        status_filter = query_parameters.get('status')
        
        # Build scan parameters
        scan_params = {'Limit': min(limit, 1000)}  # Cap at 1000
        
        if service_filter:
            scan_params['FilterExpression'] = 'service_name = :service'
            scan_params['ExpressionAttributeValues'] = {':service': service_filter}
        
        if status_filter:
            if 'FilterExpression' in scan_params:
                scan_params['FilterExpression'] += ' AND #status = :status'
                scan_params['ExpressionAttributeValues'][':status'] = status_filter
            else:
                scan_params['FilterExpression'] = '#status = :status'
                scan_params['ExpressionAttributeValues'] = {':status': status_filter}
            scan_params['ExpressionAttributeNames'] = {'#status': 'status'}
        
        # Perform scan
        response = lifecycle_table.scan(**scan_params)
        
        deprecations = []
        for item in response.get('Items', []):
            deprecations.append(format_deprecation_item(item))
        
        # Sort by extraction date (most recent first)
        deprecations.sort(key=lambda x: x.get('extraction_date', ''), reverse=True)
        
        return create_success_response({
            "deprecations": deprecations,
            "total_count": len(deprecations),
            "filters_applied": {
                "service": service_filter,
                "status": status_filter,
                "limit": limit
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        print(f"❌ List deprecations failed: {str(e)}")
        return create_error_response(500, f"Failed to list deprecations: {str(e)}")de
f handle_get_service_deprecations(service_name: str, query_parameters: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get deprecations for a specific service.
    """
    
    try:
        if not service_name:
            return create_error_response(400, "Service name is required")
        
        print(f"🔍 Getting deprecations for service: {service_name}")
        
        # Query by service name
        response = lifecycle_table.query(
            KeyConditionExpression='service_name = :service',
            ExpressionAttributeValues={':service': service_name}
        )
        
        deprecations = []
        for item in response.get('Items', []):
            deprecations.append(format_deprecation_item(item))
        
        # Sort by item_id
        deprecations.sort(key=lambda x: x.get('item_id', ''))
        
        return create_success_response({
            "service_name": service_name,
            "deprecations": deprecations,
            "total_count": len(deprecations),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        print(f"❌ Get service deprecations failed: {str(e)}")
        return create_error_response(500, f"Failed to get service deprecations: {str(e)}")

def handle_get_deprecations_by_status(status: str, query_parameters: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get deprecations filtered by status.
    """
    
    try:
        if not status:
            return create_error_response(400, "Status is required")
        
        print(f"📊 Getting deprecations by status: {status}")
        
        # Query using status GSI
        response = lifecycle_table.query(
            IndexName='status-index',
            KeyConditionExpression='#status = :status',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':status': status}
        )
        
        deprecations = []
        for item in response.get('Items', []):
            deprecations.append(format_deprecation_item(item))
        
        # Sort by deprecation date
        deprecations.sort(key=lambda x: x.get('deprecation_date', ''))
        
        return create_success_response({
            "status": status,
            "deprecations": deprecations,
            "total_count": len(deprecations),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        print(f"❌ Get deprecations by status failed: {str(e)}")
        return create_error_response(500, f"Failed to get deprecations by status: {str(e)}")

def handle_get_timeline(query_parameters: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get deprecation timeline view.
    """
    
    try:
        print("📅 Getting deprecation timeline")
        
        # Get all deprecations with dates
        response = lifecycle_table.scan(
            FilterExpression='attribute_exists(deprecation_date) OR attribute_exists(end_of_support_date)',
            ProjectionExpression='service_name, item_id, #name, deprecation_date, end_of_support_date, #status',
            ExpressionAttributeNames={'#name': 'name', '#status': 'status'}
        )
        
        timeline_items = []
        for item in response.get('Items', []):
            # Add deprecation date event
            if item.get('deprecation_date'):
                timeline_items.append({
                    'date': item['deprecation_date'],
                    'event_type': 'deprecation',
                    'service_name': item['service_name'],
                    'item_name': item.get('name', item.get('item_id', '')),
                    'status': item.get('status', 'deprecated')
                })
            
            # Add end of support date event
            if item.get('end_of_support_date'):
                timeline_items.append({
                    'date': item['end_of_support_date'],
                    'event_type': 'end_of_support',
                    'service_name': item['service_name'],
                    'item_name': item.get('name', item.get('item_id', '')),
                    'status': item.get('status', 'deprecated')
                })
        
        # Sort by date
        timeline_items.sort(key=lambda x: x['date'])
        
        # Group by date for better visualization
        timeline_by_date = {}
        for item in timeline_items:
            date = item['date']
            if date not in timeline_by_date:
                timeline_by_date[date] = []
            timeline_by_date[date].append(item)
        
        return create_success_response({
            "timeline": timeline_by_date,
            "total_events": len(timeline_items),
            "date_range": {
                "earliest": timeline_items[0]['date'] if timeline_items else None,
                "latest": timeline_items[-1]['date'] if timeline_items else None
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        print(f"❌ Get timeline failed: {str(e)}")
        return create_error_response(500, f"Failed to get timeline: {str(e)}")

def handle_health_check() -> Dict[str, Any]:
    """
    Perform system health check.
    """
    
    try:
        print("🏥 Performing health check")
        
        # Check DynamoDB table accessibility
        lifecycle_health = check_table_health(lifecycle_table)
        config_health = check_table_health(config_table)
        
        # Get basic metrics
        config_count = get_table_item_count(config_table)
        lifecycle_count = get_table_item_count(lifecycle_table)
        
        # Determine overall health
        overall_health = "healthy"
        if not lifecycle_health or not config_health:
            overall_health = "unhealthy"
        elif config_count == 0:
            overall_health = "warning"
        
        return create_success_response({
            "status": overall_health,
            "components": {
                "lifecycle_table": "healthy" if lifecycle_health else "unhealthy",
                "config_table": "healthy" if config_health else "unhealthy"
            },
            "metrics": {
                "total_services": config_count,
                "total_deprecations": lifecycle_count
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        print(f"❌ Health check failed: {str(e)}")
        return create_error_response(500, f"Health check failed: {str(e)}")

def handle_get_metrics() -> Dict[str, Any]:
    """
    Get system metrics and statistics.
    """
    
    try:
        print("📊 Getting system metrics")
        
        # Get service statistics
        service_stats = get_service_statistics()
        
        # Get deprecation statistics
        deprecation_stats = get_deprecation_statistics()
        
        # Get recent activity
        recent_activity = get_recent_activity()
        
        return create_success_response({
            "service_statistics": service_stats,
            "deprecation_statistics": deprecation_stats,
            "recent_activity": recent_activity,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        print(f"❌ Get metrics failed: {str(e)}")
        return create_error_response(500, f"Failed to get metrics: {str(e)}")

def format_deprecation_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format a DynamoDB item as a deprecation item for API response.
    """
    
    return {
        'service_name': item.get('service_name'),
        'item_id': item.get('item_id'),
        'name': item.get('name'),
        'identifier': item.get('identifier'),
        'status': item.get('status'),
        'deprecation_date': item.get('deprecation_date'),
        'end_of_support_date': item.get('end_of_support_date'),
        'replacement': item.get('replacement'),
        'extraction_date': item.get('extraction_date'),
        'last_verified': item.get('last_verified'),
        'source_url': item.get('source_url'),
        'service_specific': item.get('service_specific', {})
    }

def check_table_health(table) -> bool:
    """
    Check if a DynamoDB table is accessible.
    """
    
    try:
        table.meta.client.describe_table(TableName=table.table_name)
        return True
    except Exception:
        return False

def get_table_item_count(table) -> int:
    """
    Get approximate item count for a table.
    """
    
    try:
        response = table.meta.client.describe_table(TableName=table.table_name)
        return response['Table']['ItemCount']
    except Exception:
        return 0

def get_service_statistics() -> Dict[str, Any]:
    """
    Get service-related statistics.
    """
    
    try:
        response = config_table.scan()
        services = response.get('Items', [])
        
        total_services = len(services)
        enabled_services = len([s for s in services if s.get('enabled', True)])
        
        return {
            'total_services': total_services,
            'enabled_services': enabled_services,
            'disabled_services': total_services - enabled_services
        }
    except Exception:
        return {'total_services': 0, 'enabled_services': 0, 'disabled_services': 0}

def get_deprecation_statistics() -> Dict[str, Any]:
    """
    Get deprecation-related statistics.
    """
    
    try:
        response = lifecycle_table.scan(
            ProjectionExpression='#status',
            ExpressionAttributeNames={'#status': 'status'}
        )
        
        items = response.get('Items', [])
        status_counts = {}
        
        for item in items:
            status = item.get('status', 'unknown')
            status_counts[status] = status_counts.get(status, 0) + 1
        
        return {
            'total_items': len(items),
            'by_status': status_counts
        }
    except Exception:
        return {'total_items': 0, 'by_status': {}}

def get_recent_activity() -> List[Dict[str, Any]]:
    """
    Get recent extraction activity.
    """
    
    try:
        # Get items from last 7 days
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        
        response = lifecycle_table.scan(
            FilterExpression='extraction_date > :week_ago',
            ExpressionAttributeValues={':week_ago': week_ago},
            ProjectionExpression='service_name, extraction_date',
            Limit=50
        )
        
        activity = []
        for item in response.get('Items', []):
            activity.append({
                'service_name': item.get('service_name'),
                'extraction_date': item.get('extraction_date'),
                'activity_type': 'extraction'
            })
        
        # Sort by date (most recent first)
        activity.sort(key=lambda x: x.get('extraction_date', ''), reverse=True)
        
        return activity[:20]  # Return last 20 activities
    except Exception:
        return []

def create_success_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a successful API Gateway response.
    """
    
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization'
        },
        'body': json.dumps({
            'success': True,
            'data': data
        })
    }

def create_error_response(status_code: int, message: str) -> Dict[str, Any]:
    """
    Create an error API Gateway response.
    """
    
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization'
        },
        'body': json.dumps({
            'success': False,
            'error': message,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    }