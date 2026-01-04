"""
Database WRITE operations for AWS Services Lifecycle Tracker
These functions should remain with the agent as they're part of the extraction workflow
"""
import os
import boto3
from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from botocore.exceptions import ClientError

from database_reads import config_table, lifecycle_table, get_service_config


def categorize_item_status(item: Dict[str, Any], service_name: str = None) -> str:
    """
    Intelligently categorize item status based on dates and service-specific logic
    Returns: 'deprecated', 'extended_support', 'end_of_life', or 'end_of_support_date'
    """
    from datetime import datetime, timezone
    
    current_date = datetime.now(timezone.utc).date()
    
    # Look for various date fields that might indicate lifecycle stage
    date_fields = [
        'end_of_support_date', 'end_of_life_date', 'eol_date',
        'deprecation_date', 'deprecated_date', 'sunset_date',
        'block_function_create_date', 'block_function_update_date',
        'target_retirement_date', 'retirement_date',
        'end_of_standard_support_date', 'end_of_extended_support_date'
    ]
    
    # Parse dates from the item
    parsed_dates = {}
    for field in date_fields:
        if field in item and item[field]:
            try:
                date_str = str(item[field]).strip()
                if date_str and date_str.lower() not in ['n/a', 'none', 'null', '', '--', 'no dates available']:
                    # Try different date formats including ElasticBeanstalk formats
                    for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%B %d, %Y', '%B %Y', '%Y-%m-%dT%H:%M:%S%z']:
                        try:
                            parsed_date = datetime.strptime(date_str, fmt).date()
                            parsed_dates[field] = parsed_date
                            break
                        except ValueError:
                            continue
            except:
                continue
    
    # SERVICE-SPECIFIC LOGIC
    
    # Lambda: Simple deprecated → end_of_life lifecycle (no extended support)
    if service_name == 'lambda':
        # Check for block dates (when functions can't be created/updated)
        if 'block_function_create_date' in parsed_dates or 'block_function_update_date' in parsed_dates:
            block_date = parsed_dates.get('block_function_create_date') or parsed_dates.get('block_function_update_date')
            if block_date <= current_date:
                return 'end_of_life'  # Runtime is blocked
        
        # Lambda runtimes are just deprecated until they're blocked
        return 'deprecated'
    
    # MSK: Use "end_of_support_date" status for consistency with documentation
    if service_name == 'msk':
        if 'end_of_support_date' in parsed_dates:
            return 'end_of_support_date'  # Keep consistent with MSK documentation terminology
        return 'deprecated'  # Fallback if no end of support date
    
    # ElasticBeanstalk: Platform retirement lifecycle
    if service_name == 'elasticbeanstalk':
        # If already retired (has retirement_date)
        if 'retirement_date' in parsed_dates:
            retirement_date = parsed_dates['retirement_date']
            if retirement_date <= current_date:
                return 'deprecated'  # Already retired
            else:
                return 'deprecated'  # Scheduled for retirement
        
        # If has target retirement date (retiring soon)
        if 'target_retirement_date' in parsed_dates:
            target_date = parsed_dates['target_retirement_date']
            days_until_retirement = (target_date - current_date).days
            
            if target_date <= current_date:
                return 'deprecated'  # Past target retirement date
            elif days_until_retirement <= 90:  # Within 3 months
                return 'deprecated'  # Imminent retirement
            else:
                return 'extended_support'  # Still supported but retiring
        
        # Default for platforms in retirement tables
        return 'extended_support'
    
    # GENERIC LOGIC for other services (EKS, RDS, etc. that have extended support)
    
    # Check for retirement/end-of-life dates
    retirement_fields = ['end_of_support_date', 'end_of_life_date', 'eol_date', 'target_retirement_date', 'retirement_date']
    retirement_date = None
    for field in retirement_fields:
        if field in parsed_dates:
            retirement_date = parsed_dates[field]
            break
    
    if retirement_date:
        days_until_retirement = (retirement_date - current_date).days
        if retirement_date <= current_date:
            return 'end_of_life'
        elif days_until_retirement <= 180:  # Within 6 months of retirement
            return 'extended_support'
        elif days_until_retirement <= 365:  # Within 1 year of retirement
            return 'extended_support'
    
    # Check for extended support periods (EKS, RDS, ElastiCache)
    if 'end_of_extended_support_date' in parsed_dates:
        extended_end = parsed_dates['end_of_extended_support_date']
        if extended_end <= current_date:
            return 'end_of_life'
        elif 'end_of_standard_support_date' in parsed_dates:
            standard_end = parsed_dates['end_of_standard_support_date']
            if standard_end <= current_date:
                return 'extended_support'  # In extended support period
    
    # Default to deprecated if we have any deprecation info
    return 'deprecated'


def validate_item_against_config(item: Dict[str, Any], config: Dict[str, Any]) -> tuple[bool, List[str]]:
    """
    Validate an extracted item against the service configuration.
    Returns (is_valid, list_of_errors)
    
    This uses the service_configs.json as the single source of truth.
    Required fields MUST be present - if they're missing, it's a prompt/config issue that needs fixing.
    
    NOTE: Service-specific filtering is now handled in service_filters.py module.
    This function only validates basic field requirements and data types.
    """
    errors = []
    
    # Check required fields - these MUST be present
    required_fields = config.get('required_fields', [])
    for field in required_fields:
        if field not in item or item[field] is None or item[field] == '':
            errors.append(f"Missing required field: {field}")
    
    # Validate status field (case-insensitive)
    status = item.get('status', 'deprecated')
    if isinstance(status, str):
        status = status.lower()  # Normalize to lowercase
        item['status'] = status  # Update the item with normalized status
    
    allowed_statuses = ['deprecated', 'end_of_life', 'extended_support', 'end_of_support_date']
    if status not in allowed_statuses:
        errors.append(f"Invalid status '{status}'. Must be one of: {allowed_statuses}")
    
    return (len(errors) == 0, errors)


# ============================================================================
# WRITE OPERATIONS - Should stay with agent (part of extraction workflow)
# ============================================================================

def store_deprecation_data(service_name: str, items: list) -> dict:
    """
    Store extracted deprecation data in DynamoDB
    
    KEEP WITH AGENT: This is part of the extraction workflow
    """
    try:
        config = get_service_config(service_name)
        if 'error' in config:
            return {'success': False, 'error': config['error']}
        
        schema_key = config.get('schema_key', 'item')
        item_properties = config.get('item_properties', {})
        current_time = datetime.now(timezone.utc).isoformat()
        
        stored_count = 0
        updated_count = 0
        errors = []
        
        # Handle empty items list
        if not items:
            return {
                'success': False,
                'service_name': service_name,
                'error': 'No items provided for storage',
                'stored_count': 0,
                'verified_count': 0,
                'total_processed': 0,
                'errors': ['No items to process'],
                'extraction_date': current_time
            }
        
        for item in items:
            try:
                # Ensure item has basic required structure
                if not isinstance(item, dict):
                    errors.append(f"Invalid item type: {type(item)}")
                    continue
                
                identifier = item.get('identifier', item.get('name', ''))
                if not identifier:
                    errors.append(f"Item missing both 'identifier' and 'name' fields: {item}")
                    continue
                    
                item_id = f"{schema_key}#{identifier}"
                
                # Validate item against service configuration (single source of truth)
                is_valid, validation_errors = validate_item_against_config(item, config)
                if not is_valid:
                    errors.append(f"Item {item.get('name', 'unknown')}: {', '.join(validation_errors)}")
                    continue
                
                # Extract only the fields defined in item_properties (service-specific fields)
                service_specific = {}
                for field in item_properties.keys():
                    if field in item:
                        # Handle different field name variations from the hybrid extractor
                        value = item[field]
                        # Clean up common field name variations
                        if field.endswith('_date') and isinstance(value, str):
                            # Normalize date formats if needed
                            service_specific[field] = value
                        else:
                            service_specific[field] = value
                
                # Intelligently determine status based on dates
                # Merge service_specific fields to top level for status categorization
                item_for_status = {**item, **service_specific}
                intelligent_status = categorize_item_status(item_for_status, service_name)
                
                # Debug logging
                if 'target_retirement_date' in item_for_status:
                    print(f"DEBUG: Item {item.get('name', 'unknown')} has target_retirement_date: {item_for_status['target_retirement_date']}, categorized as: {intelligent_status}")
                
                # Prepare DynamoDB item with common fields at top level
                db_item = {
                    'service_name': service_name,
                    'item_id': item_id,
                    'status': intelligent_status,
                    'source_url': item.get('source_url', ''),
                    'extraction_date': current_time,
                    'last_verified': current_time,
                    'service_specific': service_specific  # Only configured fields
                }
                
                # Try to store with conditional check
                try:
                    lifecycle_table.put_item(
                        Item=db_item,
                        ConditionExpression='attribute_not_exists(service_name) OR extraction_date < :new_date',
                        ExpressionAttributeValues={':new_date': current_time}
                    )
                    stored_count += 1
                except ClientError as e:
                    if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                        # Item exists and is current, just update last_verified
                        lifecycle_table.update_item(
                            Key={'service_name': service_name, 'item_id': item_id},
                            UpdateExpression='SET last_verified = :now',
                            ExpressionAttributeValues={':now': current_time}
                        )
                        updated_count += 1
                    else:
                        raise
                    
            except Exception as item_error:
                errors.append(f"Error storing item {item.get('name', 'unknown')}: {str(item_error)}")
        
        # Success only if at least one item was stored or updated
        success = (stored_count + updated_count) > 0
        
        return {
            'success': success,
            'service_name': service_name,
            'stored_count': stored_count,
            'verified_count': updated_count,
            'total_processed': len(items),
            'errors': errors,
            'extraction_date': current_time
        }
        
    except Exception as e:
        return {
            'success': False,
            'service_name': service_name,
            'error': f'Error storing deprecation data: {str(e)}',
            'extraction_date': datetime.now(timezone.utc).isoformat()
        }


def update_service_metadata(service_name: str, extraction_success: bool, refresh_origin: str = "manual", extraction_duration: float = None) -> dict:
    """
    Update service configuration with extraction metadata
    
    KEEP WITH AGENT: This tracks agent execution history
    Returns dict with success status for better error tracking
    """
    try:
        current_timestamp = datetime.now(timezone.utc).isoformat()
        
        # Get current extraction count and success rate
        config_response = config_table.get_item(Key={'service_name': service_name})
        current_config = config_response.get('Item', {})
        current_count = int(current_config.get('extraction_count', 0))
        current_rate = float(current_config.get('success_rate', 0))
        
        # Calculate new success rate
        new_count = current_count + 1
        new_rate = ((current_rate * current_count) + (100 if extraction_success else 0)) / new_count
        
        # Prepare update expression and values
        update_expression = 'SET last_extraction = :time, extraction_count = :count, success_rate = :rate, last_refresh_origin = :origin'
        expression_values = {
            ':time': current_timestamp,
            ':count': new_count,
            ':rate': Decimal(str(round(new_rate, 1))),  # Convert float to Decimal for DynamoDB
            ':origin': refresh_origin
        }
        
        # Add extraction duration if provided
        if extraction_duration is not None:
            update_expression += ', last_extraction_duration = :duration'
            expression_values[':duration'] = Decimal(str(extraction_duration))
        
        # Update service config with extraction metadata
        config_table.update_item(
            Key={'service_name': service_name},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_values
        )
        
        return {
            'success': True,
            'extraction_count': new_count,
            'success_rate': round(new_rate, 1)
        }
    except Exception as update_error:
        # Log error but return failure status
        error_msg = f"Failed to update service metadata: {str(update_error)}"
        print(f"Warning: {error_msg}")
        return {
            'success': False,
            'error': error_msg
        }


def update_service_config(service_name: str, updates: dict) -> dict:
    """
    Update service configuration
    
    FUTURE: Could move to API if we want admin UI to update configs
    For now, keep with agent for simplicity
    """
    try:
        update_expr_parts = []
        expr_values = {}
        
        for key, value in updates.items():
            update_expr_parts.append(f'{key} = :{key}')
            expr_values[f':{key}'] = value
        
        if not update_expr_parts:
            return {'error': 'No updates provided'}
        
        update_expr = 'SET ' + ', '.join(update_expr_parts)
        
        config_table.update_item(
            Key={'service_name': service_name},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values
        )
        
        return {'success': True}
    except Exception as e:
        return {'error': f'Failed to update service: {str(e)}'}
