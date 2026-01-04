#!/usr/bin/env python3
"""
Extraction API - Handles UI-triggered extraction requests

This Lambda function provides API endpoints for the admin UI to:
1. Trigger manual extractions for specific services
2. Trigger bulk extractions for all services  
3. Test extraction configurations
4. Get extraction status and history
"""

import json
import boto3
import os
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from botocore.exceptions import ClientError

# Initialize AWS clients
lambda_client = boto3.client('lambda')
bedrock_agentcore = boto3.client('bedrock-agentcore')
dynamodb = boto3.resource('dynamodb')

# Environment variables
ORCHESTRATOR_FUNCTION_NAME = os.environ['ORCHESTRATOR_FUNCTION_NAME']
AGENT_RUNTIME_ARN = os.environ['AGENT_RUNTIME_ARN']
LIFECYCLE_TABLE_NAME = os.environ['LIFECYCLE_TABLE_NAME']
CONFIG_TABLE_NAME = os.environ['CONFIG_TABLE_NAME']

# DynamoDB tables
lifecycle_table = dynamodb.Table(LIFECYCLE_TABLE_NAME)
config_table = dynamodb.Table(CONFIG_TABLE_NAME)

def lambda_handler(event, context):
    """
    Main Lambda handler for extraction API requests from the admin UI.
    """
    
    try:
        print(f"📥 Extraction API request: {json.dumps(event, indent=2)}")
        
        # Parse the API Gateway event
        http_method = event.get('httpMethod', '')
        path = event.get('path', '')
        path_parameters = event.get('pathParameters') or {}
        query_parameters = event.get('queryStringParameters') or {}
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
        if http_method == 'POST' and path == '/extract':
            return handle_trigger_extraction(request_data)
        elif http_method == 'POST' and '/extract/test/' in path:
            service_name = path_parameters.get('service')
            return handle_test_extraction(service_name, request_data)
        elif http_method == 'GET' and path == '/extract/status':
            return handle_get_status(query_parameters)
        elif http_method == 'POST' and path == '/admin/refresh-all':
            return handle_refresh_all(request_data)
        else:
            return create_error_response(400, f"Unsupported request: {http_method} {path}")
            
    except Exception as e:
        print(f"💥 API Error: {str(e)}")
        return create_error_response(500, f"Internal server error: {str(e)}")

def handle_trigger_extraction(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle manual extraction trigger from UI - calls AgentCore directly.
    
    Request formats:
    - {"service_name": "lambda"} - Single service
    - {"services": ["lambda", "eks"]} - Multiple services  
    - {"services": "all"} - All enabled services
    """
    
    try:
        print(f"🚀 Triggering extraction: {request_data}")
        
        # Handle different service specifications
        if 'service_name' in request_data:
            # Single service extraction
            return handle_single_service_extraction(request_data['service_name'], request_data)
        elif 'services' in request_data:
            # Multiple services or "all" services
            return handle_multiple_services_extraction(request_data['services'], request_data)
        else:
            return create_error_response(400, "Must specify 'service_name' or 'services'")
        
    except Exception as e:
        print(f"❌ Extraction trigger failed: {str(e)}")
        return create_error_response(500, f"Failed to trigger extraction: {str(e)}")

def handle_single_service_extraction(service_name: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle extraction for a single service by calling AgentCore directly.
    """
    
    try:
        print(f"🔄 Extracting single service: {service_name}")
        
        # Create payload for AgentCore
        agent_payload = {
            "service_name": service_name,
            "force_refresh": request_data.get('force_refresh', True)
        }
        
        # Call AgentCore directly
        response = bedrock_agentcore.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            payload=json.dumps(agent_payload)
        )
        
        # Parse AgentCore response
        if 'payload' in response:
            result_text = response['payload']
        else:
            result_text = str(response)
        
        try:
            result = json.loads(result_text)
        except json.JSONDecodeError:
            result = {"success": False, "error": f"Invalid response: {result_text[:200]}"}
        
        print(f"📊 AgentCore result: {result}")
        
        return create_success_response({
            "message": f"Extraction completed for {service_name}",
            "service_name": service_name,
            "extraction_result": result,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        print(f"❌ Single service extraction failed: {str(e)}")
        return create_error_response(500, f"Failed to extract {service_name}: {str(e)}")

def handle_multiple_services_extraction(services_spec: Any, request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle extraction for multiple services by calling AgentCore for each service.
    """
    
    try:
        print(f"🔄 Extracting multiple services: {services_spec}")
        
        # Get list of services to process
        if services_spec == "all":
            services_to_process = get_all_enabled_services()
        elif isinstance(services_spec, list):
            services_to_process = services_spec
        else:
            return create_error_response(400, "Invalid services specification")
        
        if not services_to_process:
            return create_error_response(400, "No services to process")
        
        # Process each service
        results = []
        successful_extractions = 0
        failed_extractions = 0
        
        for service_name in services_to_process:
            try:
                print(f"🔄 Processing service: {service_name}")
                
                # Create payload for this service
                agent_payload = {
                    "service_name": service_name,
                    "force_refresh": request_data.get('force_refresh', True)
                }
                
                # Call AgentCore for this service
                response = bedrock_agentcore.invoke_agent_runtime(
                    agentRuntimeArn=AGENT_RUNTIME_ARN,
                    payload=json.dumps(agent_payload)
                )
                
                # Parse response
                if 'payload' in response:
                    result_text = response['payload']
                else:
                    result_text = str(response)
                
                try:
                    extraction_result = json.loads(result_text)
                except json.JSONDecodeError:
                    extraction_result = {"success": False, "error": f"Invalid response: {result_text[:200]}"}
                
                if extraction_result.get('success'):
                    successful_extractions += 1
                    print(f"✅ {service_name}: {extraction_result.get('total_items_extracted', 0)} items extracted")
                else:
                    failed_extractions += 1
                    print(f"❌ {service_name}: {extraction_result.get('error', 'Unknown error')}")
                
                results.append({
                    'service_name': service_name,
                    'success': extraction_result.get('success', False),
                    'items_extracted': extraction_result.get('total_items_extracted', 0),
                    'error': extraction_result.get('error')
                })
                
            except Exception as service_error:
                failed_extractions += 1
                error_msg = f"Service {service_name} failed: {str(service_error)}"
                print(f"❌ {error_msg}")
                
                results.append({
                    'service_name': service_name,
                    'success': False,
                    'error': error_msg,
                    'items_extracted': 0
                })
        
        # Create summary response
        summary = {
            'total_services_processed': len(services_to_process),
            'successful_extractions': successful_extractions,
            'failed_extractions': failed_extractions,
            'results': results
        }
        
        return create_success_response({
            "message": f"Bulk extraction completed for {len(services_to_process)} services",
            "extraction_summary": summary,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        print(f"❌ Multiple services extraction failed: {str(e)}")
        return create_error_response(500, f"Failed to extract multiple services: {str(e)}")

def get_all_enabled_services() -> List[str]:
    """
    Get all enabled services from the configuration table.
    """
    
    try:
        response = config_table.scan(
            FilterExpression='#enabled = :true',
            ExpressionAttributeNames={'#enabled': 'enabled'},
            ExpressionAttributeValues={':true': True}
        )
        
        services = []
        for item in response.get('Items', []):
            services.append(item['service_name'])
        
        print(f"📋 Found {len(services)} enabled services: {services}")
        return services
        
    except Exception as e:
        print(f"Error fetching enabled services: {str(e)}")
        return []

def handle_test_extraction(service_name: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle test extraction for a specific service from UI.
    """
    
    try:
        if not service_name:
            return create_error_response(400, "Service name is required")
        
        print(f"🧪 Testing extraction for service: {service_name}")
        
        # Create test payload for direct AgentCore invocation
        test_payload = {
            "service_name": service_name,
            "force_refresh": True
        }
        
        # Invoke AgentCore directly for faster testing
        response = bedrock_agentcore.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            payload=json.dumps(test_payload)
        )
        
        # Read the response
        if 'response' in response and hasattr(response['response'], 'read'):
            result_text = response['response'].read().decode('utf-8')
            result = json.loads(result_text)
        else:
            result = {"error": "Invalid response format"}
        
        print(f"🧪 Test result: {result}")
        
        return create_success_response({
            "message": f"Test extraction completed for {service_name}",
            "service_name": service_name,
            "test_result": result,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        print(f"❌ Test extraction failed: {str(e)}")
        return create_error_response(500, f"Test extraction failed: {str(e)}")

def handle_get_status(query_parameters: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get current extraction status and recent activity.
    """
    
    try:
        print("📊 Getting extraction status")
        
        # Get recent extractions from lifecycle table
        recent_extractions = get_recent_extractions()
        
        # Get service configurations
        service_configs = get_service_configs()
        
        # Calculate status summary
        status_summary = calculate_status_summary(recent_extractions, service_configs)
        
        return create_success_response({
            "status": "active",
            "summary": status_summary,
            "recent_extractions": recent_extractions[:10],  # Last 10
            "service_configs": service_configs,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        print(f"❌ Status check failed: {str(e)}")
        return create_error_response(500, f"Status check failed: {str(e)}")

def handle_refresh_all(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle full system refresh from admin UI - calls AgentCore directly for all services.
    """
    
    try:
        print("🔄 Triggering full system refresh")
        
        # Use the multiple services handler with "all" services
        refresh_request = {
            "services": "all",
            "force_refresh": True
        }
        
        return handle_multiple_services_extraction("all", refresh_request)
        
    except Exception as e:
        print(f"❌ Full refresh failed: {str(e)}")
        return create_error_response(500, f"Full refresh failed: {str(e)}")

def get_recent_extractions() -> List[Dict[str, Any]]:
    """
    Get recent extraction activity from the lifecycle table.
    """
    
    try:
        # Query recent extractions using GSI
        response = lifecycle_table.scan(
            IndexName='extraction-date-index',
            Limit=50,
            ScanIndexForward=False  # Most recent first
        )
        
        extractions = []
        for item in response.get('Items', []):
            extractions.append({
                'service_name': item.get('service_name'),
                'extraction_date': item.get('extraction_date'),
                'status': item.get('status'),
                'items_count': 1  # Each item represents one deprecation
            })
        
        # Group by service and extraction date
        grouped = {}
        for ext in extractions:
            key = f"{ext['service_name']}#{ext['extraction_date']}"
            if key not in grouped:
                grouped[key] = {
                    'service_name': ext['service_name'],
                    'extraction_date': ext['extraction_date'],
                    'items_count': 0
                }
            grouped[key]['items_count'] += 1
        
        return list(grouped.values())
        
    except Exception as e:
        print(f"Error getting recent extractions: {str(e)}")
        return []

def get_service_configs() -> List[Dict[str, Any]]:
    """
    Get all service configurations.
    """
    
    try:
        response = config_table.scan()
        
        configs = []
        for item in response.get('Items', []):
            configs.append({
                'service_name': item.get('service_name'),
                'display_name': item.get('display_name'),
                'enabled': item.get('enabled', True),
                'last_extraction': item.get('last_extraction'),
                'extraction_count': item.get('extraction_count', 0)
            })
        
        return configs
        
    except Exception as e:
        print(f"Error getting service configs: {str(e)}")
        return []

def calculate_status_summary(extractions: List[Dict], configs: List[Dict]) -> Dict[str, Any]:
    """
    Calculate system status summary.
    """
    
    total_services = len(configs)
    enabled_services = len([c for c in configs if c.get('enabled', True)])
    recent_extractions = len([e for e in extractions if e.get('extraction_date', '') > '2025-10-26'])
    
    return {
        'total_services': total_services,
        'enabled_services': enabled_services,
        'recent_extractions_24h': recent_extractions,
        'system_health': 'healthy' if enabled_services > 0 else 'warning'
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