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

# Import Health READ operations
from health_reads import (
    list_health_events,
    get_health_event,
    get_health_summary,
)

# Import WRITE operations (stay with agent)
from database_writes import update_service_config

# Import workflow orchestration logic
from workflow_orchestrator import extract_service_lifecycle

# Import account discovery
from account_discovery import discover_and_save

# Import Health collection and enrichment
from health_collector import HealthCollector
from health_enricher import HealthEnricher
from concurrency_lock import acquire_lock, release_lock

# Import Health monitoring (failure tracking and graceful degradation)
from health_monitoring import (
    track_collection_result,
    is_health_collection_enabled,
    disable_health_collection,
)

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
        from aws_utils import get_region
        
        # Only send notifications for scheduled extractions
        topic_arn = os.environ.get('NOTIFICATION_TOPIC_ARN')
        if not topic_arn:
            return
        
        # Initialize SNS client using deployment region
        region = get_region()
        sns = boto3.client('sns', region_name=region)
        
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


def _handle_collect_health_events(payload: dict) -> dict:
    """
    Handle the collect_health_events action.

    Workflow:
    0. Check if Health collection is enabled (graceful degradation)
    1. Acquire the concurrency lock
    2. Get service configs to build the service_filter list (from health_event_mapping)
    3. Call HealthCollector.collect_events(service_filter)
    4. Enrich events via HealthEnricher
    5. Write enriched events to DynamoDB via batch write
    6. Release the lock
    7. Track collection result (success/failure counter + CloudWatch alarm)
    8. Return success/failure result
    """
    import os

    # Step 0: Check if Health collection is enabled (graceful degradation - Req 9.3)
    if not is_health_collection_enabled():
        return {
            'success': False,
            'reason': 'health_collection_disabled',
            'error': 'Health collection is disabled due to previous errors (e.g., insufficient permissions). '
                     'Use enable_health_collection to re-enable.'
        }

    # Step 1: Acquire concurrency lock
    lock_acquired = acquire_lock()
    if not lock_acquired:
        return {
            'success': False,
            'reason': 'concurrent_execution',
            'error': 'Another health collection is already in progress'
        }

    try:
        # Step 2: Get service configs and build service_filter from health_event_mapping
        services_result = list_services()
        if 'error' in services_result:
            track_collection_result(success=False)
            return {
                'success': False,
                'error': f"Failed to load service configs: {services_result['error']}"
            }

        service_configs = {}
        service_filter = []
        for service in services_result.get('services', []):
            service_name = service.get('service_name', '')
            if not service_name or service_name.startswith('_'):
                continue
            service_configs[service_name] = service
            mapping = service.get('health_event_mapping')
            if mapping:
                service_filter.append(mapping)

        # Step 3: Collect events from AWS Health API
        collector = HealthCollector()
        collection_result = collector.collect_events(
            service_filter=service_filter if service_filter else None
        )

        if not collection_result.get('success'):
            # Check for AccessDeniedException → graceful degradation (Req 9.3)
            errors = collection_result.get('errors', [])
            for error in errors:
                if 'AccessDeniedException' in str(error) or 'not authorized' in str(error).lower():
                    disable_health_collection(
                        reason=f"AccessDeniedException: insufficient permissions for Health API. "
                               f"Details: {error}"
                    )
                    break

            track_collection_result(success=False)
            return {
                'success': False,
                'error': 'Health event collection failed',
                'details': errors
            }

        raw_events = collection_result.get('events', [])

        # Step 4: Enrich events with lifecycle data
        enricher = HealthEnricher()
        enriched_events = enricher.enrich_events(raw_events, service_configs)

        # Step 5: Write enriched events to DynamoDB
        events_written = 0
        write_errors = []
        if enriched_events:
            events_written, write_errors = _batch_write_health_events(enriched_events)

        # Step 7: Track success (resets failure counter - Req 8.2)
        track_collection_result(success=True)

        # Step 8: Return result
        return {
            'success': True,
            'events_collected': collection_result.get('events_collected', 0),
            'events_enriched': len(enriched_events),
            'events_written': events_written,
            'errors': collection_result.get('errors', []) + write_errors
        }

    except Exception as e:
        # Track failure (increments counter, may emit CloudWatch alarm - Req 8.2)
        track_collection_result(success=False)
        return {
            'success': False,
            'error': f'Health collection failed: {str(e)}'
        }
    finally:
        # Step 6: Always release the lock
        release_lock()


def _batch_write_health_events(events: list) -> tuple:
    """
    Write enriched health events to the aws-health-events DynamoDB table.

    Uses batch_writer for efficient bulk inserts.

    Args:
        events: List of enriched health event dicts ready for DynamoDB.

    Returns:
        Tuple of (events_written count, list of error strings).
    """
    import os
    import boto3
    from aws_utils import get_region

    region = get_region()
    dynamodb_resource = boto3.resource('dynamodb', region_name=region)
    table_name = os.environ.get('HEALTH_TABLE_NAME', 'aws-health-events')
    table = dynamodb_resource.Table(table_name)

    events_written = 0
    errors = []

    try:
        with table.batch_writer() as batch:
            for event in events:
                try:
                    # Ensure required keys are present
                    if not event.get('event_arn') or not event.get('event_type_category'):
                        errors.append(
                            f"Skipping event with missing key fields: "
                            f"arn={event.get('event_arn')}"
                        )
                        continue
                    batch.put_item(Item=event)
                    events_written += 1
                except Exception as item_error:
                    errors.append(
                        f"Failed to write event {event.get('event_arn', 'unknown')}: "
                        f"{str(item_error)}"
                    )
    except Exception as e:
        errors.append(f"Batch write failed: {str(e)}")

    return events_written, errors


def handle_api_action(action: str, payload: dict) -> dict:
    """Handle admin UI API actions (read operations)"""
    
    if action == 'list_services':
        return list_services()
    
    elif action == 'list_deprecations':
        filters = payload.get('filters', {})
        return list_deprecations(filters)
    
    elif action == 'get_metrics':
        return get_metrics()
    
    elif action == 'discover_account':
        # Discover actual resources in the customer's AWS account
        import os
        region = payload.get('region') or os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION') or 'us-east-1'
        include_supported = payload.get('include_supported', True)
        table_name = os.environ.get('LIFECYCLE_TABLE_NAME', 'aws-services-lifecycle')
        
        return discover_and_save(
            region=region,
            include_supported=include_supported,
            table_name=table_name
        )
    
    elif action == 'update_service':
        service_name = payload.get('service_name')
        updates = payload.get('updates', {})
        return update_service_config(service_name, updates)
    
    # Action Plan operations
    elif action == 'list_action_plans':
        from action_plans import list_action_plans
        filters = payload.get('filters', {})
        return list_action_plans(filters)
    
    elif action == 'get_action_plan':
        from action_plans import get_action_plan
        plan_id = payload.get('plan_id')
        return get_action_plan(plan_id)
    
    elif action == 'create_action_plan':
        from action_plans import create_action_plan
        return create_action_plan(payload)
    
    elif action == 'update_action_plan':
        from action_plans import update_action_plan
        plan_id = payload.get('plan_id')
        updates = payload.get('updates', {})
        return update_action_plan(plan_id, updates)
    
    elif action == 'delete_action_plan':
        from action_plans import delete_action_plan
        plan_id = payload.get('plan_id')
        return delete_action_plan(plan_id)
    
    # Health Event operations
    elif action == 'collect_health_events':
        return _handle_collect_health_events(payload)
    
    elif action == 'list_health_events':
        filters = payload.get('filters', {})
        return list_health_events(filters)
    
    elif action == 'get_health_event':
        event_arn = payload.get('event_arn')
        return get_health_event(event_arn)
    
    elif action == 'get_health_summary':
        return get_health_summary()
    
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
