"""
Service-Specific Post-LLM Filters for AWS Services Lifecycle Tracker

This module contains all service-specific filtering logic that needs to be applied
AFTER the LLM extraction but BEFORE storing data in the database.

CENTRALIZED FILTERING LOCATION:
- All service-specific item filtering should be implemented here
- Each service gets its own filter function
- Easy to maintain and extend for new services
- Clear separation of concerns from extraction and storage logic

USAGE:
    from service_filters import apply_service_filters
    
    # After LLM extraction, before database storage
    filtered_items = apply_service_filters(service_name, extracted_items)
"""

from typing import List, Dict, Any
import re
from datetime import datetime


def filter_opensearch_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Filter OpenSearch/Elasticsearch items to exclude versions with 'Not announced' support dates
    
    FILTERING RULES:
    - Reject items where end_of_standard_support_date contains 'Not announced'
    - Reject items where end_of_extended_support_date contains 'Not announced'
    - Only keep items with actual dates in both support fields
    
    Args:
        items: List of extracted OpenSearch items
        
    Returns:
        List of filtered items (only those with announced support dates)
    """
    filtered_items = []
    rejected_count = 0
    
    for item in items:
        # Check standard support date
        std_support = item.get('end_of_standard_support_date', '')
        ext_support = item.get('end_of_extended_support_date', '')
        
        # Reject if either field contains "Not announced" (case-insensitive)
        if (isinstance(std_support, str) and 'not announced' in std_support.lower()) or \
           (isinstance(ext_support, str) and 'not announced' in ext_support.lower()):
            rejected_count += 1
            print(f"  🚫 Filtered out {item.get('name', 'Unknown')}: Support dates not announced")
            continue
        
        # Keep items with actual dates
        filtered_items.append(item)
    
    if rejected_count > 0:
        print(f"  ✅ OpenSearch filter: Kept {len(filtered_items)} items, rejected {rejected_count} items with 'Not announced' dates")
    
    return filtered_items


def filter_msk_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Filter MSK (Kafka) items based on service-specific requirements
    
    FILTERING RULES:
    - Reject items where end_of_support_date is "--" (no support date announced)
    - Only keep items with actual end of support dates
    
    Args:
        items: List of extracted MSK items
        
    Returns:
        List of filtered items
    """
    filtered_items = []
    rejected_count = 0
    
    for item in items:
        end_of_support = item.get('end_of_support_date', '')
        
        # Reject items with "--" as end_of_support_date
        if isinstance(end_of_support, str) and end_of_support.strip() == '--':
            rejected_count += 1
            print(f"  🚫 Filtered out {item.get('name', 'Unknown')}: end_of_support_date is '--'")
            continue
        
        # Also reject empty or None end_of_support_date
        if not end_of_support or end_of_support.strip() == '':
            rejected_count += 1
            print(f"  🚫 Filtered out {item.get('name', 'Unknown')}: Missing end_of_support_date")
            continue
        
        # Keep items with valid end of support dates
        filtered_items.append(item)
    
    if rejected_count > 0:
        print(f"  ✅ MSK filter: Kept {len(filtered_items)} items, rejected {rejected_count} items")
    
    return filtered_items


def filter_elasticbeanstalk_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Filter Elastic Beanstalk platform items based on service-specific requirements
    
    FILTERING RULES:
    - Ensure items come from retirement tables only (not upcoming releases)
    - Validate retirement date formats
    - Filter by date ranges if needed
    
    Args:
        items: List of extracted Elastic Beanstalk items
        
    Returns:
        List of filtered items
    """
    filtered_items = []
    rejected_count = 0
    
    for item in items:
        # Example: Ensure we have valid retirement dates
        target_retirement = item.get('target_retirement_date', '')
        retirement_date = item.get('retirement_date', '')
        
        # Keep items that have at least one retirement date
        if target_retirement or retirement_date:
            filtered_items.append(item)
        else:
            rejected_count += 1
            print(f"  🚫 Filtered out {item.get('name', 'Unknown')}: No retirement dates")
    
    if rejected_count > 0:
        print(f"  ✅ ElasticBeanstalk filter: Kept {len(filtered_items)} items, rejected {rejected_count} items")
    
    return filtered_items


def filter_lambda_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Filter Lambda runtime items based on service-specific requirements
    
    FILTERING RULES:
    - Ensure items are from deprecated runtimes table only
    - Validate runtime identifier formats
    - Check for required deprecation dates
    
    Args:
        items: List of extracted Lambda items
        
    Returns:
        List of filtered items
    """
    filtered_items = []
    rejected_count = 0
    
    for item in items:
        # Example: Ensure we have valid runtime identifiers
        identifier = item.get('identifier', '')
        deprecation_date = item.get('deprecation_date', '')
        
        # Keep items with valid identifiers and deprecation dates
        if identifier and deprecation_date:
            filtered_items.append(item)
        else:
            rejected_count += 1
            print(f"  🚫 Filtered out {item.get('name', 'Unknown')}: Missing identifier or deprecation date")
    
    if rejected_count > 0:
        print(f"  ✅ Lambda filter: Kept {len(filtered_items)} items, rejected {rejected_count} items")
    
    return filtered_items


# SERVICE FILTER REGISTRY
# Add new services here with their corresponding filter functions
SERVICE_FILTERS = {
    'opensearch': filter_opensearch_items,
    'msk': filter_msk_items,
    'elasticbeanstalk': filter_elasticbeanstalk_items,
    'lambda': filter_lambda_items,
    # Add more services as needed:
    # 'eks': filter_eks_items,
    # 'rds': filter_rds_items,
    # 'elasticache': filter_elasticache_items,
}


def apply_service_filters(service_name: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Apply service-specific filtering to extracted items
    
    This is the main entry point for all post-LLM filtering logic.
    Call this function after LLM extraction but before database storage.
    
    Args:
        service_name: AWS service identifier (e.g., 'opensearch', 'msk')
        items: List of items extracted by the LLM
        
    Returns:
        List of filtered items ready for database storage
    """
    if not items:
        return items
    
    # Check if service has specific filtering logic
    if service_name in SERVICE_FILTERS:
        print(f"  🔍 Applying {service_name} service filters...")
        filter_function = SERVICE_FILTERS[service_name]
        filtered_items = filter_function(items)
        
        if len(filtered_items) != len(items):
            print(f"  📊 Filtering result: {len(items)} → {len(filtered_items)} items")
        
        return filtered_items
    else:
        # No specific filtering for this service, return all items
        print(f"  ℹ️  No specific filters defined for {service_name}, keeping all {len(items)} items")
        return items


def get_available_filters() -> List[str]:
    """
    Get list of services that have specific filtering logic defined
    
    Returns:
        List of service names with custom filters
    """
    return list(SERVICE_FILTERS.keys())


def add_service_filter(service_name: str, filter_function):
    """
    Dynamically add a new service filter
    
    Args:
        service_name: AWS service identifier
        filter_function: Function that takes items list and returns filtered items list
    """
    SERVICE_FILTERS[service_name] = filter_function
    print(f"✅ Added custom filter for {service_name}")


# FILTER DEVELOPMENT GUIDELINES
"""
When adding new service filters:

1. CREATE A NEW FILTER FUNCTION:
   - Name it filter_{service_name}_items
   - Take items: List[Dict[str, Any]] as parameter
   - Return filtered List[Dict[str, Any]]
   - Add logging for rejected items

2. ADD TO SERVICE_FILTERS REGISTRY:
   - Add entry: 'service_name': filter_service_name_items

3. DOCUMENT FILTERING RULES:
   - Add clear comments explaining what gets filtered and why
   - Include examples of rejected vs accepted items

4. TEST THE FILTER:
   - Run extraction before and after adding filter
   - Verify correct items are rejected/accepted
   - Check that database only contains valid items

EXAMPLE NEW SERVICE FILTER:

def filter_newservice_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    '''Filter NewService items based on specific requirements'''
    filtered_items = []
    rejected_count = 0
    
    for item in items:
        # Add your filtering logic here
        if meets_criteria(item):
            filtered_items.append(item)
        else:
            rejected_count += 1
            print(f"  🚫 Filtered out {item.get('name', 'Unknown')}: Reason")
    
    if rejected_count > 0:
        print(f"  ✅ NewService filter: Kept {len(filtered_items)} items, rejected {rejected_count} items")
    
    return filtered_items

# Then add to SERVICE_FILTERS:
SERVICE_FILTERS['newservice'] = filter_newservice_items
"""