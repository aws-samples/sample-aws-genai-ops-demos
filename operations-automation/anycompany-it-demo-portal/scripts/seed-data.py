#!/usr/bin/env python3
"""
Seed script to populate DynamoDB tables with mock data for the IT Portal Demo
"""

import boto3
import json
import sys
import os
from decimal import Decimal
from datetime import datetime, timedelta

def decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError

def load_json_data(filename):
    """Load JSON data from mock_data directory"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(script_dir, 'mock_data', filename)
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Convert numeric values to Decimal for DynamoDB compatibility
            return json.loads(json.dumps(data), parse_float=Decimal, parse_int=Decimal)
    except FileNotFoundError:
        print(f"✗ Error: Could not find {filename} in mock_data directory")
        return []
    except json.JSONDecodeError as e:
        print(f"✗ Error: Invalid JSON in {filename}: {str(e)}")
        return []

def seed_data(region='us-east-1'):
    """Populate DynamoDB tables with mock data"""
    
    dynamodb = boto3.resource('dynamodb', region_name=region)
    
    # Load mock data from JSON files
    print("Loading mock data from JSON files...")
    tickets_data = load_json_data('tickets.json')
    inventory_data = load_json_data('inventory.json')
    purchase_orders_data = load_json_data('purchase_orders.json')
    assets_data = load_json_data('assets.json')
    shipping_data = load_json_data('shipping.json')
    vendors_data = load_json_data('vendors.json')
    models_data = load_json_data('models.json')
    
    # Verify all data loaded successfully
    data_files = [
        ('tickets.json', tickets_data),
        ('inventory.json', inventory_data),
        ('purchase_orders.json', purchase_orders_data),
        ('assets.json', assets_data),
        ('shipping.json', shipping_data),
        ('vendors.json', vendors_data),
        ('models.json', models_data)
    ]
    
    for filename, data in data_files:
        if not data:
            print(f"✗ Failed to load {filename} - skipping seeding")
            return
        print(f"✓ Loaded {len(data)} items from {filename}")
    
    # Populate tables
    tables_data = [
        ('anycompany-tickets', tickets_data),
        ('anycompany-inventory', inventory_data),
        ('anycompany-purchase-orders', purchase_orders_data),
        ('anycompany-assets', assets_data),
        ('anycompany-shipping', shipping_data),
        ('anycompany-vendors', vendors_data),
        ('anycompany-models', models_data)
    ]
    
    for table_name, data in tables_data:
        try:
            table = dynamodb.Table(table_name)
            print(f"Populating {table_name}...")
            
            for item in data:
                table.put_item(Item=item)
                print(f"  ✓ Added item: {item.get('id', item.get('name', 'Unknown'))}")
            
            print(f"✓ {table_name} populated with {len(data)} items")
            
        except Exception as e:
            print(f"✗ Error populating {table_name}: {str(e)}")
    
    print("\n=== Mock Data Population Complete ===")
    print("All DynamoDB tables have been populated with sample data.")
    print("You can now test the IT Portal Demo with realistic data.")

if __name__ == "__main__":
    region = sys.argv[1] if len(sys.argv) > 1 else 'us-east-1'
    print(f"Seeding data in region: {region}")
    seed_data(region)