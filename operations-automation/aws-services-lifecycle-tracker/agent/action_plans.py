"""
Action Plans Module - Manages deprecation remediation tracking
"""
import os
import uuid
import boto3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Any

# Table name
ACTION_PLAN_TABLE = os.environ.get('ACTION_PLAN_TABLE_NAME', 'deprecation-action-plans')

def get_dynamodb_resource():
    """Get DynamoDB resource with region handling"""
    region = os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION')
    if region:
        return boto3.resource('dynamodb', region_name=region)
    return boto3.resource('dynamodb')

def convert_decimals(obj: Any) -> Any:
    """Convert Decimal objects to int/float for JSON serialization"""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    elif isinstance(obj, dict):
        return {k: convert_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimals(i) for i in obj]
    return obj

def list_action_plans(filters: Optional[Dict] = None) -> Dict:
    """List all action plans with optional filtering"""
    try:
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table(ACTION_PLAN_TABLE)
        
        # Check for specific filters
        if filters:
            owner = filters.get('owner')
            plan_status = filters.get('plan_status')
            
            if owner:
                # Query by owner using GSI
                response = table.query(
                    IndexName='owner-index',
                    KeyConditionExpression='#owner = :owner',
                    ExpressionAttributeNames={'#owner': 'owner'},
                    ExpressionAttributeValues={':owner': owner}
                )
            elif plan_status:
                # Query by status using GSI
                response = table.query(
                    IndexName='plan-status-index',
                    KeyConditionExpression='plan_status = :status',
                    ExpressionAttributeValues={':status': plan_status}
                )
            else:
                # Full scan with filter
                response = table.scan()
        else:
            # Full scan
            response = table.scan()
        
        plans = response.get('Items', [])
        
        # Sort by created_at descending
        plans.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return convert_decimals({
            'success': True,
            'plans': plans,
            'count': len(plans)
        })
        
    except Exception as e:
        return {'success': False, 'error': str(e)}

def get_action_plan(plan_id: str) -> Dict:
    """Get a specific action plan by ID"""
    try:
        if not plan_id:
            return {'success': False, 'error': 'plan_id is required'}
        
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table(ACTION_PLAN_TABLE)
        
        response = table.get_item(Key={'plan_id': plan_id})
        
        if 'Item' not in response:
            return {'success': False, 'error': f'Action plan {plan_id} not found'}
        
        return convert_decimals({
            'success': True,
            'plan': response['Item']
        })
        
    except Exception as e:
        return {'success': False, 'error': str(e)}


def create_action_plan(data: Dict) -> Dict:
    """Create a new action plan"""
    try:
        # Validate required fields
        required_fields = ['service_name', 'item_id', 'owner']
        for field in required_fields:
            if not data.get(field):
                return {'success': False, 'error': f'{field} is required'}
        
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table(ACTION_PLAN_TABLE)
        
        now = datetime.now(timezone.utc).isoformat()
        plan_id = str(uuid.uuid4())
        
        plan = {
            'plan_id': plan_id,
            'service_name': data['service_name'],
            'item_id': data['item_id'],
            'item_name': data.get('item_name', data['item_id']),
            'owner': data['owner'],
            'plan_status': data.get('plan_status', 'not_started'),
            'priority': data.get('priority', 'medium'),
            'target_date': data.get('target_date', ''),
            'notes': data.get('notes', ''),
            'created_at': now,
            'updated_at': now,
            'created_by': data.get('created_by', data['owner']),
        }
        
        table.put_item(Item=plan)
        
        return convert_decimals({
            'success': True,
            'plan': plan,
            'message': f'Action plan created with ID: {plan_id}'
        })
        
    except Exception as e:
        return {'success': False, 'error': str(e)}

def update_action_plan(plan_id: str, updates: Dict) -> Dict:
    """Update an existing action plan"""
    try:
        if not plan_id:
            return {'success': False, 'error': 'plan_id is required'}
        
        if not updates:
            return {'success': False, 'error': 'No updates provided'}
        
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table(ACTION_PLAN_TABLE)
        
        # Check if plan exists
        existing = table.get_item(Key={'plan_id': plan_id})
        if 'Item' not in existing:
            return {'success': False, 'error': f'Action plan {plan_id} not found'}
        
        # Build update expression
        update_expr_parts = []
        expr_attr_names = {}
        expr_attr_values = {}
        
        allowed_fields = ['owner', 'plan_status', 'priority', 'target_date', 'notes']
        
        for field in allowed_fields:
            if field in updates:
                update_expr_parts.append(f'#{field} = :{field}')
                expr_attr_names[f'#{field}'] = field
                expr_attr_values[f':{field}'] = updates[field]
        
        # Always update updated_at
        update_expr_parts.append('#updated_at = :updated_at')
        expr_attr_names['#updated_at'] = 'updated_at'
        expr_attr_values[':updated_at'] = datetime.now(timezone.utc).isoformat()
        
        update_expr = 'SET ' + ', '.join(update_expr_parts)
        
        response = table.update_item(
            Key={'plan_id': plan_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_attr_names,
            ExpressionAttributeValues=expr_attr_values,
            ReturnValues='ALL_NEW'
        )
        
        return convert_decimals({
            'success': True,
            'plan': response.get('Attributes', {}),
            'message': f'Action plan {plan_id} updated'
        })
        
    except Exception as e:
        return {'success': False, 'error': str(e)}

def delete_action_plan(plan_id: str) -> Dict:
    """Delete an action plan"""
    try:
        if not plan_id:
            return {'success': False, 'error': 'plan_id is required'}
        
        dynamodb = get_dynamodb_resource()
        table = dynamodb.Table(ACTION_PLAN_TABLE)
        
        # Check if plan exists
        existing = table.get_item(Key={'plan_id': plan_id})
        if 'Item' not in existing:
            return {'success': False, 'error': f'Action plan {plan_id} not found'}
        
        table.delete_item(Key={'plan_id': plan_id})
        
        return {
            'success': True,
            'message': f'Action plan {plan_id} deleted'
        }
        
    except Exception as e:
        return {'success': False, 'error': str(e)}
