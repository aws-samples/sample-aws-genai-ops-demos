#!/usr/bin/env python3
"""
Configuration API - Handles service configuration management

This Lambda function provides API endpoints for the admin UI to:
1. List all service configurations
2. Create new service configurations
3. Update existing service configurations
4. Delete service configurations
5. Enable/disable services
"""

import json
import boto3
import os
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from botocore.exceptions import ClientError

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')

# Environment variables
CONFIG_TABLE_NAME = os.environ['CONFIG_TABLE_NAME']

# DynamoDB table
config_table = dynamodb.Table(CONFIG_TABLE_NAME)

def lambda_handler(event, context):
    """
    Main Lambda handler for configuration API requests from the admin UI.
    """
    
    try:
        print(f"📥 Config API request: {json.dumps(event, indent=2)}")
        
        # Parse the API Gateway event
        http_method = event.get('httpMethod', '')
        path = event.get('path', '')
        path_parameters = event.get('pathParameters') or {}
        body = event.get('body', '{}')
        
        # Parse request body
        if body:
            try:
                request_data = json.loads(body)
            except json.JSONDecodeError:
                request_data = {}
        else:
            request_data = {}
        
        # Route the request
        if http_method == 'GET' and path == '/services':
            return handle_list_services()
        elif http_method == 'POST' and path == '/services':
            return handle_create_service(request_data)
        elif http_method == 'GET' and '/services/' in path:
            service_name = path_parameters.get('service')
            return handle_get_service(service_name)
        elif http_method == 'PUT' and '/services/' in path:
            service_name = path_parameters.get('service')
            return handle_update_service(service_name, request_data)
        elif http_method == 'DELETE' and '/services/' in path:
            service_name = path_parameters.get('service')
            return handle_delete_service(service_name)
        else:
            return create_error_response(400, f"Unsupported request: {http_method} {path}")
            
    except Exception as e:
        print(f"💥 Config API Error: {str(e)}")
        return create_error_response(500, f"Internal server error: {str(e)}")

def handle_list_services() -> Dict[str, Any]:
    """
    List all service configurations.
    """
    
    try:
        print("📋 Listing all service configurations")
        
        response = config_table.scan()
        
        services = []
        for item in response.get('Items', []):
            services.append(format_service_config(item))
        
        # Sort by service name
        services.sort(key=lambda x: x['service_name'])
        
        return create_success_response({
            "services": services,
            "total_count": len(services),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        print(f"❌ List services failed: {str(e)}")
        return create_error_response(500, f"Failed to list services: {str(e)}")

def handle_create_service(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a new service configuration.
    """
    
    try:
        service_name = request_data.get('service_name')
        if not service_name:
            return create_error_response(400, "service_name is required")
        
        print(f"➕ Creating service configuration: {service_name}")
        
        # Validate required fields
        required_fields = ['display_name', 'documentation_urls', 'extraction_focus']
        for field in required_fields:
            if field not in request_data:
                return create_error_response(400, f"Field '{field}' is required")
        
        # Create service configuration
        config_item = {
            'service_name': service_name,
            'display_name': request_data['display_name'],
            'documentation_urls': request_data['documentation_urls'],
            'extraction_focus': request_data['extraction_focus'],
            'schema_key': request_data.get('schema_key', 'items'),
            'item_properties': request_data.get('item_properties', {}),
            'enabled': request_data.get('enabled', True),
            'extraction_schedule': request_data.get('extraction_schedule', 'weekly'),
            'created_date': datetime.now(timezone.utc).isoformat(),
            'last_modified': datetime.now(timezone.utc).isoformat(),
            'extraction_count': 0
        }
        
        # Check if service already exists
        try:
            existing = config_table.get_item(Key={'service_name': service_name})
            if 'Item' in existing:
                return create_error_response(409, f"Service '{service_name}' already exists")
        except ClientError:
            pass
        
        # Save to DynamoDB
        config_table.put_item(Item=config_item)
        
        return create_success_response({
            "message": f"Service '{service_name}' created successfully",
            "service": format_service_config(config_item)
        })
        
    except Exception as e:
        print(f"❌ Create service failed: {str(e)}")
        return create_error_response(500, f"Failed to create service: {str(e)}")def handle
_get_service(service_name: str) -> Dict[str, Any]:
    """
    Get a specific service configuration.
    """
    
    try:
        if not service_name:
            return create_error_response(400, "Service name is required")
        
        print(f"🔍 Getting service configuration: {service_name}")
        
        response = config_table.get_item(Key={'service_name': service_name})
        
        if 'Item' not in response:
            return create_error_response(404, f"Service '{service_name}' not found")
        
        return create_success_response({
            "service": format_service_config(response['Item'])
        })
        
    except Exception as e:
        print(f"❌ Get service failed: {str(e)}")
        return create_error_response(500, f"Failed to get service: {str(e)}")

def handle_update_service(service_name: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update an existing service configuration.
    """
    
    try:
        if not service_name:
            return create_error_response(400, "Service name is required")
        
        print(f"✏️ Updating service configuration: {service_name}")
        
        # Check if service exists
        response = config_table.get_item(Key={'service_name': service_name})
        if 'Item' not in response:
            return create_error_response(404, f"Service '{service_name}' not found")
        
        existing_item = response['Item']
        
        # Update fields
        update_expression = "SET last_modified = :now"
        expression_values = {':now': datetime.now(timezone.utc).isoformat()}
        
        # Build update expression for provided fields
        updatable_fields = [
            'display_name', 'documentation_urls', 'extraction_focus', 
            'schema_key', 'item_properties', 'enabled', 'extraction_schedule'
        ]
        
        for field in updatable_fields:
            if field in request_data:
                update_expression += f", {field} = :{field}"
                expression_values[f":{field}"] = request_data[field]
        
        # Perform update
        config_table.update_item(
            Key={'service_name': service_name},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_values
        )
        
        # Get updated item
        updated_response = config_table.get_item(Key={'service_name': service_name})
        
        return create_success_response({
            "message": f"Service '{service_name}' updated successfully",
            "service": format_service_config(updated_response['Item'])
        })
        
    except Exception as e:
        print(f"❌ Update service failed: {str(e)}")
        return create_error_response(500, f"Failed to update service: {str(e)}")

def handle_delete_service(service_name: str) -> Dict[str, Any]:
    """
    Delete a service configuration.
    """
    
    try:
        if not service_name:
            return create_error_response(400, "Service name is required")
        
        print(f"🗑️ Deleting service configuration: {service_name}")
        
        # Check if service exists
        response = config_table.get_item(Key={'service_name': service_name})
        if 'Item' not in response:
            return create_error_response(404, f"Service '{service_name}' not found")
        
        # Delete the service
        config_table.delete_item(Key={'service_name': service_name})
        
        return create_success_response({
            "message": f"Service '{service_name}' deleted successfully"
        })
        
    except Exception as e:
        print(f"❌ Delete service failed: {str(e)}")
        return create_error_response(500, f"Failed to delete service: {str(e)}")

def format_service_config(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format a DynamoDB item as a service configuration for API response.
    """
    
    return {
        'service_name': item.get('service_name'),
        'display_name': item.get('display_name'),
        'documentation_urls': item.get('documentation_urls', []),
        'extraction_focus': item.get('extraction_focus', ''),
        'schema_key': item.get('schema_key', 'items'),
        'item_properties': item.get('item_properties', {}),
        'enabled': item.get('enabled', True),
        'extraction_schedule': item.get('extraction_schedule', 'weekly'),
        'created_date': item.get('created_date'),
        'last_modified': item.get('last_modified'),
        'last_extraction': item.get('last_extraction'),
        'extraction_count': item.get('extraction_count', 0),
        'success_rate': item.get('success_rate', 0.0)
    }

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