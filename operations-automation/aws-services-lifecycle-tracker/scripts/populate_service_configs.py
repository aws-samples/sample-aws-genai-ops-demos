#!/usr/bin/env python3
"""
Populate DynamoDB with all service configurations from service_configs.json

This script is for manual population/testing. The CDK Data Stack automatically
populates configurations during deployment via a Custom Resource.

Usage:
    python scripts/populate_service_configs.py
"""

import boto3
import json
from decimal import Decimal
from pathlib import Path

def load_service_configs():
    """Load service configurations from service_configs.json"""
    # Script is in scripts/ folder, config file is service_configs.json
    config_path = Path(__file__).parent / 'service_configs.json'
    
    if not config_path.exists():
        raise FileNotFoundError(f"Could not find service_configs.json at {config_path}")
    
    print(f"Loading configurations from: {config_path}")
    with open(config_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        return data.get('services', {})

def populate_service_configs():
    """Populate DynamoDB with service configurations"""
    
    # Load configurations from JSON file
    try:
        services_config = load_service_configs()
    except FileNotFoundError as e:
        print(f"❌ Error: {e}")
        return False
    
    # Initialize DynamoDB
    dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
    config_table = dynamodb.Table('service-extraction-config')
    
    print("Populating service configurations from service_configs.json...")
    print("=" * 60)
    
    success_count = 0
    for service_name, config in services_config.items():
        try:
            # Add service_name to config (required for DynamoDB key)
            config['service_name'] = service_name
            
            # Put item in DynamoDB
            config_table.put_item(Item=config)
            print(f"✅ {config.get('name', service_name)}: Configuration saved")
            success_count += 1
            
        except Exception as e:
            print(f"❌ {config.get('name', service_name)}: Error - {str(e)}")
    
    print("=" * 60)
    print(f"Completed: {success_count}/{len(services_config)} service configurations processed")
    return success_count == len(services_config)

if __name__ == "__main__":
    success = populate_service_configs()
    exit(0 if success else 1)
