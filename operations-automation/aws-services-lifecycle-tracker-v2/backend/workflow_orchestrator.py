"""
Workflow Orchestrator for AWS Services Lifecycle Tracker
Orchestrates the complete extraction workflow: config → extraction → storage → metadata
Coordinates between the data extractor, database operations, and error handling
"""
import json
import re
from datetime import datetime, timezone
from typing import Dict, List, Any

from database_reads import get_service_config
from database_writes import store_deprecation_data, update_service_metadata
from data_extractor import DataExtractor


def extract_service_lifecycle(service_name: str, force_refresh: bool = False, override_urls: list = None, refresh_origin: str = "manual") -> dict:
    """
    Main orchestration function for service lifecycle extraction
    
    Workflow:
    1. Create data extractor instance
    2. Extract data using hybrid HTML + LLM approach
    3. Store extracted data in database
    4. Update service metadata
    5. Return comprehensive results
    
    Args:
        service_name: AWS service to extract (e.g., 'lambda', 'eks')
        force_refresh: Whether to force fresh extraction
        override_urls: Optional URLs to override service config URLs
        
    Returns:
        dict: Comprehensive extraction results including success status, 
              storage results, and metadata updates
    """
    try:
        # Create data extractor instance
        extractor = DataExtractor()
        
        # Extract data using hybrid approach
        extraction_result = extractor.extract_service_data(
            service_name=service_name,
            force_refresh=force_refresh,
            override_urls=override_urls
        )
        
        if not extraction_result.get('success'):
            # Update metadata even on failure
            try:
                metadata_result = update_service_metadata(service_name, False)
            except:
                metadata_result = {"success": False, "error": "Could not update metadata"}
            
            return {
                **extraction_result,
                "metadata_update": metadata_result
            }
        
        # Store all extracted data
        all_extracted_items = extraction_result.get('items', [])
        if all_extracted_items:
            storage_result = store_deprecation_data(service_name, all_extracted_items)
        else:
            storage_result = {
                "success": False,
                "error": "No deprecation items were extracted from any URLs"
            }
        
        # Update service configuration metadata (always update, even on failure)
        extraction_success = storage_result.get('success', False)
        extraction_duration = extraction_result.get('extraction_duration')  # Get duration from extraction result
        metadata_result = update_service_metadata(service_name, extraction_success, refresh_origin, extraction_duration)
        
        # Return comprehensive result
        return {
            **extraction_result,
            "success": extraction_success,
            "refresh_origin": refresh_origin,
            "storage_result": storage_result,
            "metadata_update": metadata_result
        }
        
    except Exception as e:
        # Even on catastrophic failure, try to update metadata
        try:
            metadata_result = update_service_metadata(service_name, False, refresh_origin)
        except:
            metadata_result = {"success": False, "error": "Could not update metadata"}
        
        return {
            "success": False,
            "service_name": service_name,
            "refresh_origin": refresh_origin,
            "error": f"Extraction failed: {str(e)}",
            "metadata_update": metadata_result,
            "extraction_date": datetime.now(timezone.utc).isoformat()
        }