"""
Database WRITE operations for AWS Services Lifecycle Tracker
Part of the extraction workflow (pipeline Lambda)
"""
import os
import boto3
from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple
from botocore.exceptions import ClientError

from database_reads import config_table, lifecycle_table, state_table, get_service_config
from aws_utils import get_region

import calendar
import re
from datetime import date

from dateutil import parser as date_parser



# ============================================================================
# DATE NORMALIZATION (issue #140)
# ============================================================================

_NO_DATE_VALUES = {'', 'n/a', 'na', 'none', 'null', '--', '-', 'tbd', 'to be determined',
                   'not announced', 'no dates available', 'not applicable'}
_MONTH_ONLY = re.compile(r'^\s*([A-Za-z]{3,9})\s+(\d{4})\s*$')
_YEAR_ONLY = re.compile(r'^\s*(\d{4})\s*$')


def normalize_date(value: Any) -> Optional[str]:
    """Normalize the many date spellings found in AWS docs to ISO YYYY-MM-DD.

    Handles '2027-06-03', 'February 25, 2026', '28 February 2027',
    '2026-07-27T00:00:00Z', month-only 'April 2032' (-> last day of that
    month: an end-of-support month means support lasts through the month) and
    year-only '2032' (-> Dec 31). Returns None for placeholders such as
    'N/A', 'To be determined' or anything unparseable.
    """
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.strftime('%Y-%m-%d')
    text = str(value).strip()
    if text.lower() in _NO_DATE_VALUES:
        return None

    m = _MONTH_ONLY.match(text)
    if m:
        try:
            first = date_parser.parse(f"1 {m.group(1)} {m.group(2)}")
        except (ValueError, OverflowError):
            return None
        last_day = calendar.monthrange(first.year, first.month)[1]
        return first.replace(day=last_day).strftime('%Y-%m-%d')

    m = _YEAR_ONLY.match(text)
    if m:
        return f"{m.group(1)}-12-31"

    # Day-first only matters for ambiguous numeric forms ('03/06/2027'); AWS docs
    # write those month-first (US style), and unambiguous spellings such as
    # '28 February 2027' parse correctly either way.
    try:
        return date_parser.parse(text, dayfirst=False, fuzzy=False).strftime('%Y-%m-%d')
    except (ValueError, OverflowError, TypeError):
        return None


def parse_date(value: Any) -> Optional[date]:
    """normalize_date() as a date object, or None."""
    iso = normalize_date(value)
    return date.fromisoformat(iso) if iso else None


def categorize_item_status(item: Dict[str, Any], service_name: str = None) -> str:
    """
    Intelligently categorize item status based on dates and service-specific logic
    Returns one of:
      'end_of_life'         - a lifecycle end date has already passed
      'extended_support'    - in (or within a year of entering) the extended tier
      'end_of_support_date' - a lifecycle end date is announced but > 1 year out
      'deprecated'          - deprecation is effective now (date passed or docs say so)
      'supported'           - no lifecycle dates and no deprecation signal (#119)
    """
    current_date = datetime.now(timezone.utc).date()
    
    # Look for various date fields that might indicate lifecycle stage
    date_fields = [
        'end_of_support_date', 'end_of_life_date', 'eol_date',
        'deprecation_date', 'deprecated_date', 'sunset_date',
        'block_function_create_date', 'block_function_update_date',
        'block_create_date', 'block_update_date',
        'target_retirement_date', 'retirement_date',
        'end_of_standard_support_date', 'end_of_extended_support_date'
    ]
    
    # Parse dates from the item (any spelling the docs use, see normalize_date)
    parsed_dates = {}
    for field in date_fields:
        parsed = parse_date(item.get(field))
        if parsed:
            parsed_dates[field] = parsed
    
    # SERVICE-SPECIFIC LOGIC
    
    # Lambda: deprecation date -> deprecated; block-update date -> end_of_life
    # (no extended support tier). The docs page lists supported runtimes with
    # their *future* deprecation dates too (#140): those are 'supported' until
    # the date passes, or 'end_of_support_date' once the date is within a year.
    if service_name == 'lambda':
        block_date = (parsed_dates.get('block_update_date') or parsed_dates.get('block_function_update_date')
                      or parsed_dates.get('block_create_date') or parsed_dates.get('block_function_create_date'))
        if block_date and block_date <= current_date:
            return 'end_of_life'  # Runtime is blocked
        dep_date = parsed_dates.get('deprecation_date') or parsed_dates.get('deprecated_date')
        if dep_date:
            if dep_date <= current_date:
                return 'deprecated'
            if (dep_date - current_date).days <= 365:
                return 'end_of_support_date'  # Deprecation announced, less than a year out
            return 'supported'
        # Listed without any date: only the deprecated table lacks dates (very old runtimes)
        return 'deprecated'
    
    # MSK: end_of_support_date is a display label, not a lifecycle verdict (#119).
    # Compare the date to today: past means the version is gone (end_of_life);
    # future means "EOS announced" (end_of_support_date). Versions without an
    # EOS date fall through to the generic logic instead of being blanket-
    # labeled 'deprecated' (they may simply still be supported).
    if service_name == 'msk':
        if 'end_of_support_date' in parsed_dates:
            if parsed_dates['end_of_support_date'] <= current_date:
                return 'end_of_life'
            return 'end_of_support_date'
    
    # ElasticBeanstalk: Platform retirement lifecycle
    if service_name == 'elasticbeanstalk':
        # If already retired (has retirement_date)
        if 'retirement_date' in parsed_dates:
            retirement_date = parsed_dates['retirement_date']
            if retirement_date <= current_date:
                return 'end_of_life'  # Already retired
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
    
    # 1. Hard end dates: past means gone; within a year means final stretch;
    #    further out means "end announced" (#119: the old 180/365 bands were
    #    redundant, and >365 fell through to a blanket 'deprecated').
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
        elif days_until_retirement <= 365:  # Within 1 year of the end date
            return 'extended_support'
        else:
            return 'end_of_support_date'  # End announced, more than a year out
    
    # 2. Standard/extended support pair (EKS, RDS, ElastiCache, Aurora, OpenSearch)
    #    A standard-support end within the next year is announced-and-near:
    #    'end_of_support_date', consistent with the hard-end-date rule above
    #    (#140: previously anything in the future was plain 'supported', so a
    #    version with 7 weeks of standard support left looked fine).
    std_end = parsed_dates.get('end_of_standard_support_date')
    if 'end_of_extended_support_date' in parsed_dates:
        extended_end = parsed_dates['end_of_extended_support_date']
        if extended_end <= current_date:
            return 'end_of_life'
        if std_end:
            if std_end <= current_date:
                return 'extended_support'  # In the extended support window
            if (std_end - current_date).days <= 365:
                return 'end_of_support_date'
            return 'supported'  # Still in standard support (#119)
        return 'extended_support'  # Only an extended-support end date is known
    if std_end:
        # Standard-support date alone (previously ignored entirely, #119)
        if std_end <= current_date:
            return 'extended_support'
        if (std_end - current_date).days <= 365:
            return 'end_of_support_date'
        return 'supported'
    
    # 3. Explicit deprecation signals: a passed deprecation date, or docs text
    #    that says so. A future deprecation date is announced-but-not-effective.
    for field in ['deprecation_date', 'deprecated_date', 'sunset_date']:
        if field in parsed_dates:
            if parsed_dates[field] <= current_date:
                return 'deprecated'
            return 'end_of_support_date'  # Deprecation announced for a future date
    raw_status = str(item.get('status', '')).lower()
    if 'deprecat' in raw_status:
        return 'deprecated'
    if 'end of life' in raw_status or 'end_of_life' in raw_status or 'retired' in raw_status:
        return 'end_of_life'
    
    # 4. No lifecycle dates and no deprecation signal: the item is supported
    #    (#119: the old blanket 'deprecated' fallback mislabeled current versions).
    return 'supported'


def validate_item_against_config(item: Dict[str, Any], config: Dict[str, Any]) -> Tuple[bool, List[str]]:
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
    
    # Normalize the status field (case-insensitive) WITHOUT rejecting free-text.
    #
    # The LLM returns natural-language statuses scraped from AWS docs (e.g.
    # "end of support", "current"), none of which are in the strict allow-list.
    # Rejecting them here skipped every item (issue #98, E1). The stored status
    # is not this raw value anyway: store_deprecation_data() derives the real
    # status from lifecycle dates via categorize_item_status(). So we only
    # normalize a status that is already valid and leave the rest to the
    # date-based categorizer instead of failing validation.
    allowed_statuses = ['deprecated', 'end_of_life', 'extended_support', 'end_of_support_date']
    status = item.get('status', 'deprecated')
    if isinstance(status, str) and status.lower() in allowed_statuses:
        # Keep an already-valid status normalized to lowercase.
        item['status'] = status.lower()
    # Free-text or missing statuses are intentionally not an error here; the
    # date-based categorizer is the single source of truth for the stored value.
    
    return (len(errors) == 0, errors)


# ============================================================================
# WRITE OPERATIONS (extraction workflow)
# ============================================================================

def store_deprecation_data(service_name: str, items: list) -> dict:
    """
    Store extracted deprecation data in DynamoDB
    
    Part of the extraction workflow
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

                # Backfill the identifier so validation agrees with the key
                # derivation above. Every service lists 'identifier' in
                # required_fields, but the LLM omits it when the source docs
                # have no natural identifier column (e.g. Amplify build
                # images, issue #135) - previously that hard-failed every
                # item even though the name-based fallback key was already
                # computed and usable.
                if not item.get('identifier'):
                    item['identifier'] = identifier

                item_id = f"{schema_key}#{identifier}"
                
                # Validate item against service configuration (single source of truth)
                is_valid, validation_errors = validate_item_against_config(item, config)
                if not is_valid:
                    errors.append(f"Item {item.get('name', 'unknown')}: {', '.join(validation_errors)}")
                    continue
                
                # Extract only the fields defined in item_properties (service-specific fields).
                # Date fields are stored as ISO YYYY-MM-DD (issue #140): the docs mix
                # 'February 25, 2026', '28 February 2027', 'April 2032' and ISO, and both
                # the status logic and the UI's `new Date()` need one format.
                service_specific = {}
                for field in item_properties.keys():
                    if field in item:
                        value = item[field]
                        if field.endswith('_date') or field.startswith('date_'):
                            normalized = normalize_date(value)
                            # Keep the original text when it carries meaning (e.g. 'N/A',
                            # 'To be determined') but could not become a date.
                            value = normalized if normalized else (value if value not in (None, '') else None)
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
        
        # Success means data was actually persisted, not merely that the run
        # did not throw (issue #98, E2). update_service_metadata() scores the
        # per-service success rate on this value, so gating on stored/updated
        # counts keeps the reported success rate consistent with reality.
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
    
    Tracks extraction history per service
    Returns dict with success status for better error tracking
    """
    try:
        current_timestamp = datetime.now(timezone.utc).isoformat()
        
        # Get current extraction count and success rate from the backend-owned
        # state table (issue #116, Option B) - runtime state no longer lives
        # in the repo-owned config table.
        state_response = state_table.get_item(Key={'service_name': service_name})
        current_state = state_response.get('Item', {})
        current_count = int(current_state.get('extraction_count', 0))
        current_rate = float(current_state.get('success_rate', 0))
        
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
        
        # Upsert extraction metadata into the backend-owned state table
        state_table.update_item(
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


# Runtime-state fields live in the backend-owned state table (issue #116,
# Option B) and must never be written into the config table - not even via
# the UI's update_service path.
_RUNTIME_STATE_FIELDS = {
    'extraction_count',
    'last_extraction',
    'success_rate',
    'last_refresh_origin',
    'last_extraction_duration',
}


def update_service_config(service_name: str, updates: dict) -> dict:
    """
    Update service configuration
    
    FUTURE: Could move to API if we want admin UI to update configs
    Kept in the backend for simplicity
    """
    try:
        update_expr_parts = []
        expr_values = {}
        
        for key, value in updates.items():
            if key in _RUNTIME_STATE_FIELDS:
                # Silently dropping would hide caller bugs; reject instead.
                return {'error': f"Field '{key}' is runtime state and cannot be written to the config table"}
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
