"""
AWS Services Lifecycle Tracker - Main Agent Entry Point
Handles routing between API actions and extraction operations
"""
import json
from bedrock_agentcore.runtime import BedrockAgentCoreApp

# Import READ operations (future API candidates)
from database_reads import (
    list_services,
    list_deprecations,
    get_metrics,
    convert_decimals
)

# Import WRITE operations (stay with agent)
from database_writes import update_service_config

# Import workflow orchestration logic
from workflow_orchestrator import extract_service_lifecycle

# Create the AgentCore app
app = BedrockAgentCoreApp()


def get_all_enabled_services() -> list:
    """Get list of all enabled services"""
    from database_reads import list_services
    
    services_result = list_services()
    if 'error' in services_result:
        return []
    
    enabled_services = []
    for service in services_result.get('services', []):
        if service.get('enabled', True):
            service_name = service['service_name']
            enabled_services.append(service_name)
    
    return enabled_services


def handle_multi_service_extraction(payload: dict) -> dict:
    """Handle extraction for multiple services"""
    from datetime import datetime, timezone
    import os
    import boto3
    
    try:
        services_spec = payload.get('services')
        force_refresh = payload.get('force_refresh', True)
        extraction_type = payload.get('extraction_type', 'manual')
        refresh_origin = payload.get('refresh_origin', 'manual')  # Track origin
        
        # Determine which services to process
        if services_spec == 'all':
            services_to_process = get_all_enabled_services()
        elif isinstance(services_spec, list):
            services_to_process = services_spec
        else:
            return {
                "success": False,
                "error": f"Invalid services specification: {services_spec}"
            }
        
        if not services_to_process:
            return {
                "success": False,
                "error": "No enabled services found to process"
            }
        
        # Process each service
        results = []
        successful_extractions = 0
        failed_extractions = 0
        total_items_extracted = 0
        
        for service_name in services_to_process:
            try:
                result = extract_service_lifecycle(
                    service_name=service_name,
                    force_refresh=force_refresh,
                    refresh_origin=refresh_origin
                )
                
                if result.get('success'):
                    successful_extractions += 1
                    total_items_extracted += result.get('total_items_extracted', 0)
                else:
                    failed_extractions += 1
                
                results.append({
                    'service_name': service_name,
                    'success': result.get('success', False),
                    'items_extracted': result.get('total_items_extracted', 0),
                    'error': result.get('error'),
                    'duration': result.get('duration', 0)
                })
                
            except Exception as service_error:
                failed_extractions += 1
                results.append({
                    'service_name': service_name,
                    'success': False,
                    'error': str(service_error),
                    'items_extracted': 0
                })
        
        # Create summary response
        response = {
            'success': successful_extractions > 0,
            'extraction_type': extraction_type,
            'refresh_origin': refresh_origin,
            'total_services_processed': len(services_to_process),
            'successful_extractions': successful_extractions,
            'failed_extractions': failed_extractions,
            'total_items_extracted': total_items_extracted,
            'results': results,
            'extraction_date': datetime.now(timezone.utc).isoformat()
        }
        
        # Send notification if this was a scheduled extraction
        if extraction_type != 'manual':
            send_extraction_notification(response)
        
        return convert_decimals(response)
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Multi-service extraction failed: {str(e)}",
            "extraction_date": datetime.now(timezone.utc).isoformat()
        }



def send_extraction_notification(result: dict) -> None:
    """Send SNS notification about extraction results"""
    try:
        import boto3
        import os
        
        # Only send notifications for scheduled extractions
        topic_arn = os.environ.get('NOTIFICATION_TOPIC_ARN')
        if not topic_arn:
            return
        
        sns = boto3.client('sns')
        
        successful = result['successful_extractions']
        total = result['total_services_processed']
        extraction_type = result.get('extraction_type', 'manual')
        
        subject = f"AWS Lifecycle Tracker - {extraction_type.title()} Extraction Complete"
        
        message = f"""AWS Services Lifecycle Tracker Extraction Results

Extraction Type: {extraction_type}
Total Services: {total}
Successful: {successful}
Failed: {result['failed_extractions']}
Total Items: {result['total_items_extracted']}
Date: {result['extraction_date']}

Service Results:
"""
        
        for service_result in result['results']:
            status = "✅" if service_result['success'] else "❌"
            items = service_result.get('items_extracted', 0)
            error = service_result.get('error', '')
            
            message += f"{status} {service_result['service_name']}: {items} items"
            if error:
                message += f" (Error: {error})"
            message += "\n"
        
        sns.publish(
            TopicArn=topic_arn,
            Subject=subject,
            Message=message
        )
        
    except Exception as e:
        # Don't fail the extraction if notification fails
        print(f"Warning: Failed to send notification: {str(e)}")


def handle_api_action(action: str, payload: dict) -> dict:
    """Handle admin UI API actions (read operations)"""
    
    if action == 'list_services':
        return list_services()
    
    elif action == 'list_deprecations':
        filters = payload.get('filters', {})
        return list_deprecations(filters)
    
    elif action == 'get_metrics':
        return get_metrics()
    

    
    elif action == 'update_service':
        service_name = payload.get('service_name')
        updates = payload.get('updates', {})
        return update_service_config(service_name, updates)
    
    else:
        return {'error': f'Unknown action: {action}'}


@app.entrypoint
def main_handler(payload):
    """
    Main entry point for the agent
    Routes requests to either API actions or extraction operations
    
    Payload formats:
    - API Actions: {"action": "list_services"} or {"action": "list_deprecations", "filters": {...}}
    - Single Service: {"service_name": "lambda", "force_refresh": false}
    - Multiple Services: {"services": ["lambda", "eks"], "force_refresh": true}
    - All Services: {"services": "all", "force_refresh": true}
    - Scheduled: {"services": "all", "extraction_type": "weekly", "force_refresh": true}
    - EventBridge Scheduler: {"AgentRuntimeArn": "...", "Payload": "{\"services\":\"all\",\"force_refresh\":true,\"refresh_origin\":\"Auto\"}"}
    """
    try:
        # Handle both dict and string payloads
        if isinstance(payload, str):
            payload = json.loads(payload)
        
        # Handle EventBridge Scheduler format (nested Payload)
        if isinstance(payload, dict) and 'Payload' in payload and 'AgentRuntimeArn' in payload:
            # Extract the actual payload from the EventBridge Scheduler wrapper
            inner_payload = payload['Payload']
            if isinstance(inner_payload, str):
                payload = json.loads(inner_payload)
            else:
                payload = inner_payload
        
        # Check if this is an API action request
        if isinstance(payload, dict) and 'action' in payload:
            action = payload['action']
            result = handle_api_action(action, payload)
            # Ensure result is JSON serializable
            return convert_decimals(result)
        
        # Handle multi-service extraction
        if 'services' in payload:
            return handle_multi_service_extraction(payload)
        
        # Handle single service extraction
        service_name = payload.get("service_name")
        if not service_name:
            return {
                "success": False,
                "error": "No service_name or services provided. Expected format: {'service_name': 'lambda'} or {'services': 'all'}"
            }
        
        force_refresh = payload.get("force_refresh", False)
        override_urls = payload.get("urls")
        refresh_origin = payload.get("refresh_origin", "manual")
        
        # Run single service extraction
        result = extract_service_lifecycle(
            service_name=service_name,
            force_refresh=force_refresh,
            override_urls=override_urls,
            refresh_origin=refresh_origin
        )
        
        return convert_decimals(result)
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Request failed: {str(e)}"
        }


if __name__ == "__main__":
    app.run()
