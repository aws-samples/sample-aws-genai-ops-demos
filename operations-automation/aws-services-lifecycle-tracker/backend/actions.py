"""
Action router and shared helpers for the AWS Services Lifecycle Tracker.

Compute-agnostic: no AgentCore, Step Functions or Lambda specifics live here.
Used by api.py (UI actions, pipeline control) and pipeline.py
(the durable refresh pipeline). Issue #139.
"""
import json

from database_reads import (
    list_services,
    list_deprecations,
    get_metrics,
    convert_decimals
)
from database_writes import update_service_config
from workflow_orchestrator import extract_service_lifecycle
from account_discovery import discover_and_save


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


def slim_extraction_result(service_name: str, result: dict) -> dict:
    """Reduce a full extract_service_lifecycle() result to a summary.

    The full result carries every extracted item (already persisted to
    DynamoDB); callers that aggregate many services - and durable steps whose
    return values are checkpointed - only need the outcome. Also reads the
    correct duration key (extraction_duration): the old code read 'duration',
    which never existed, so durations were always reported as 0.
    """
    return {
        'service_name': service_name,
        'success': bool(result.get('success', False)),
        'items_extracted': int(result.get('total_items_extracted', 0) or 0),
        'error': result.get('error'),
        'duration': float(result.get('extraction_duration', 0) or 0),
    }


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
                
                results.append(slim_extraction_result(service_name, result))
                
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


def _last_scan_info() -> dict:
    """When the inventory was last written, how many rows, which regions (issue #141)."""
    from database_reads import inventory_table
    latest, regions, count = None, set(), 0
    kwargs = {'ProjectionExpression': 'last_verified, #r', 'ExpressionAttributeNames': {'#r': 'region'}}
    try:
        while True:
            page = inventory_table.scan(**kwargs)
            for row in page.get('Items', []):
                count += 1
                lv = str(row.get('last_verified') or '')
                if lv and (latest is None or lv > latest):
                    latest = lv
                if row.get('region'):
                    regions.add(str(row['region']))
            if 'LastEvaluatedKey' not in page:
                break
            kwargs['ExclusiveStartKey'] = page['LastEvaluatedKey']
    except Exception as e:
        print(f"Warning: could not read inventory for last_scan: {e}")
    return {'last_verified': latest, 'resources': count, 'regions': sorted(regions)}


def handle_api_action(action: str, payload: dict) -> dict:
    """Handle admin UI API actions (read operations)"""
    
    if action == 'list_services':
        return list_services()
    
    elif action == 'list_enabled_service_names':
        # Lightweight name-only listing for the Refresh All state machine's
        # ResolveServices step (issue #126). Returns just the enabled service
        # names so the Step Functions state payload stays small compared to
        # the full list_services configs.
        return {'services': get_all_enabled_services()}
    
    elif action == 'list_deprecations':
        filters = payload.get('filters', {})
        return list_deprecations(filters)
    
    elif action == 'get_metrics':
        return get_metrics()
    
    elif action == 'list_scanners':
        # Scanner coverage for the UI (issue #141): which config service keys
        # have an account scanner behind them, and the last completed scan.
        from account_discovery import SCANNER_SERVICE_KEYS, COST_STATUS_KEY, load_health_status, load_control_row
        return {
            'scanners': [
                {'label': label, 'service_keys': keys}
                for label, keys in SCANNER_SERVICE_KEYS.items()
            ],
            'last_scan': _last_scan_info(),
            # Outcome of the AWS Health cross-check of the last scan (#141)
            'health': load_health_status(),
            # Outcome of the Extended Support pricing pass of the last scan (#142)
            'cost_exposure': load_control_row(COST_STATUS_KEY),
        }
    
    elif action == 'discover_account':
        # Discover actual resources in the customer's AWS account.
        # Inventory goes to the dedicated aws-account-inventory table (issue
        # #116); discover_and_save resolves it from INVENTORY_TABLE_NAME.
        # NEVER pass the lifecycle (facts) table here: discovery reconciliation
        # deletes rows outside the current run, which on the facts table would
        # destroy extraction data.
        import os
        region = payload.get('region') or os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION') or 'us-east-1'
        include_supported = payload.get('include_supported', True)
        
        return discover_and_save(
            region=region,
            include_supported=include_supported,
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
    
    else:
        return {'error': f'Unknown action: {action}'}


def dispatch(payload) -> dict:
    """
    Route a request payload to the right operation. Compute-agnostic.

    Payload formats:
    - API Actions: {"action": "list_services"} or {"action": "list_deprecations", "filters": {...}}
    - Single Service: {"service_name": "lambda", "force_refresh": false}
    - Multiple Services: {"services": ["lambda", "eks"], "force_refresh": true}
    - All Services: {"services": "all", "force_refresh": true}
    """
    try:
        if isinstance(payload, str):
            payload = json.loads(payload)

        if not isinstance(payload, dict):
            return {"success": False, "error": "Payload must be a JSON object"}

        if 'action' in payload:
            return convert_decimals(handle_api_action(payload['action'], payload))

        if 'services' in payload:
            return handle_multi_service_extraction(payload)

        service_name = payload.get("service_name")
        if not service_name:
            return {
                "success": False,
                "error": "No action, service_name or services provided. Expected e.g. "
                         "{'action': 'list_services'}, {'service_name': 'lambda'} or {'services': 'all'}"
            }

        result = extract_service_lifecycle(
            service_name=service_name,
            force_refresh=payload.get("force_refresh", False),
            override_urls=payload.get("urls"),
            refresh_origin=payload.get("refresh_origin", "manual")
        )
        return convert_decimals(result)

    except Exception as e:
        return {
            "success": False,
            "error": f"Request failed: {str(e)}"
        }
