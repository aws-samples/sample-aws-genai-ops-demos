"""
FinOps Gamification Console - API Handler
Handles all REST API routes for findings, teams, scoping, scores, and leaderboard.
"""

import json
import os
import uuid
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key, Attr

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize DynamoDB
dynamodb = boto3.resource('dynamodb')

# Table references from environment
TEAMS_TABLE = os.environ.get('TEAMS_TABLE', 'finops-teams')
SCOPING_TABLE = os.environ.get('SCOPING_TABLE', 'finops-scoping')
FINDINGS_TABLE = os.environ.get('FINDINGS_TABLE', 'finops-findings')
LEARNINGS_TABLE = os.environ.get('LEARNINGS_TABLE', 'finops-learnings')
SCORES_TABLE = os.environ.get('SCORES_TABLE', 'finops-scores')

# Initialize table objects
teams_table = dynamodb.Table(TEAMS_TABLE)
scoping_table = dynamodb.Table(SCOPING_TABLE)
findings_table = dynamodb.Table(FINDINGS_TABLE)
learnings_table = dynamodb.Table(LEARNINGS_TABLE)
scores_table = dynamodb.Table(SCORES_TABLE)

# Scoring configuration
SCORING_CONFIG = {
    'accept_base_points': 10,
    'reject_base_points': 5,
    'savings_multiplier': 0.1,  # Points per $1 saved
    'learning_contribution_points': 25,
    'streak_bonus_multiplier': 1.5,
}


class DecimalEncoder(json.JSONEncoder):
    """Handle Decimal types from DynamoDB."""
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o) if o % 1 else int(o)
        return super().default(o)


def json_response(status_code: int, body: Any) -> dict:
    """Create standardized API response."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization,X-Amz-Date,X-Api-Key',
        },
        'body': json.dumps(body, cls=DecimalEncoder),
    }


def get_user_info(event: dict) -> dict:
    """Extract user info from Cognito authorizer claims."""
    claims = event.get('requestContext', {}).get('authorizer', {}).get('claims', {})
    return {
        'userId': claims.get('sub', 'unknown'),
        'email': claims.get('email', 'unknown'),
        'name': f"{claims.get('given_name', '')} {claims.get('family_name', '')}".strip() or 'Unknown User',
        'groups': claims.get('cognito:groups', '').split(',') if claims.get('cognito:groups') else [],
    }


def is_admin(user_info: dict) -> bool:
    """Check if user has admin role."""
    return 'finops-admin' in user_info.get('groups', [])


def is_champion(user_info: dict) -> bool:
    """Check if user has champion or admin role."""
    groups = user_info.get('groups', [])
    return 'finops-admin' in groups or 'champion' in groups


def get_current_month() -> str:
    """Get current month in YYYY-MM format."""
    return datetime.now(timezone.utc).strftime('%Y-%m')


def get_current_timestamp() -> str:
    """Get current ISO timestamp."""
    return datetime.now(timezone.utc).isoformat()


# ==================== FINDINGS ====================

def list_findings(event: dict, user_info: dict) -> dict:
    """List findings with optional filtering by status or team."""
    params = event.get('queryStringParameters') or {}
    status = params.get('status')
    team_id = params.get('teamId')
    
    try:
        if status:
            # Query by status
            response = findings_table.query(
                IndexName='status-createdAt-index',
                KeyConditionExpression=Key('status').eq(status),
                ScanIndexForward=False,  # Most recent first
                Limit=100,
            )
        elif team_id:
            # Query by team
            response = findings_table.query(
                IndexName='teamId-status-index',
                KeyConditionExpression=Key('assignedTeamId').eq(team_id),
                Limit=100,
            )
        else:
            # Scan all (for admins) or filter by user's team (for champions)
            if is_admin(user_info):
                response = findings_table.scan(Limit=100)
            else:
                # Get user's team and filter
                user_team = get_user_team(user_info['userId'])
                if user_team:
                    response = findings_table.query(
                        IndexName='teamId-status-index',
                        KeyConditionExpression=Key('assignedTeamId').eq(user_team),
                        Limit=100,
                    )
                else:
                    response = {'Items': []}
        
        return json_response(200, {
            'findings': response.get('Items', []),
            'count': len(response.get('Items', [])),
        })
    except Exception as e:
        logger.error(f"Error listing findings: {e}")
        return json_response(500, {'error': 'Failed to list findings'})


def get_finding(finding_id: str) -> dict:
    """Get a single finding by ID."""
    try:
        response = findings_table.get_item(Key={'findingId': finding_id})
        finding = response.get('Item')
        
        if not finding:
            return json_response(404, {'error': 'Finding not found'})
        
        return json_response(200, finding)
    except Exception as e:
        logger.error(f"Error getting finding {finding_id}: {e}")
        return json_response(500, {'error': 'Failed to get finding'})


def update_finding(finding_id: str, body: dict, user_info: dict) -> dict:
    """Update finding fields (admin/champion only)."""
    if not is_champion(user_info):
        return json_response(403, {'error': 'Insufficient permissions'})
    
    try:
        # Build update expression
        update_parts = []
        attr_values = {}
        attr_names = {}
        
        allowed_fields = ['priority', 'notes', 'assignedTeamId', 'tags']
        for field in allowed_fields:
            if field in body:
                update_parts.append(f"#{field} = :{field}")
                attr_names[f"#{field}"] = field
                attr_values[f":{field}"] = body[field]
        
        if not update_parts:
            return json_response(400, {'error': 'No valid fields to update'})
        
        # Add audit fields
        update_parts.append("#updatedAt = :updatedAt")
        update_parts.append("#updatedBy = :updatedBy")
        attr_names["#updatedAt"] = "updatedAt"
        attr_names["#updatedBy"] = "updatedBy"
        attr_values[":updatedAt"] = get_current_timestamp()
        attr_values[":updatedBy"] = user_info['userId']
        
        response = findings_table.update_item(
            Key={'findingId': finding_id},
            UpdateExpression=f"SET {', '.join(update_parts)}",
            ExpressionAttributeNames=attr_names,
            ExpressionAttributeValues=attr_values,
            ReturnValues='ALL_NEW',
        )
        
        return json_response(200, response.get('Attributes', {}))
    except Exception as e:
        logger.error(f"Error updating finding {finding_id}: {e}")
        return json_response(500, {'error': 'Failed to update finding'})


def accept_finding(finding_id: str, body: dict, user_info: dict) -> dict:
    """Accept a finding and record learning."""
    if not is_champion(user_info):
        return json_response(403, {'error': 'Insufficient permissions'})
    
    try:
        # Get current finding
        response = findings_table.get_item(Key={'findingId': finding_id})
        finding = response.get('Item')
        
        if not finding:
            return json_response(404, {'error': 'Finding not found'})
        
        if finding.get('status') != 'pending':
            return json_response(400, {'error': f"Finding already {finding.get('status')}"})
        
        timestamp = get_current_timestamp()
        estimated_savings = float(finding.get('estimatedSavingsUsd', 0))
        
        # Update finding status
        findings_table.update_item(
            Key={'findingId': finding_id},
            UpdateExpression="""
                SET #status = :status,
                    acceptedAt = :acceptedAt,
                    acceptedBy = :acceptedBy,
                    acceptNotes = :acceptNotes,
                    implementationDetails = :implementationDetails,
                    updatedAt = :updatedAt
            """,
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'accepted',
                ':acceptedAt': timestamp,
                ':acceptedBy': user_info['userId'],
                ':acceptNotes': body.get('notes', ''),
                ':implementationDetails': body.get('implementationDetails', ''),
                ':updatedAt': timestamp,
            },
        )
        
        # Create learning record if pattern is worth capturing
        if body.get('createLearning', False):
            create_learning_from_finding(finding, body, user_info)
        
        # Update user score
        points = calculate_accept_points(estimated_savings)
        update_user_score(user_info['userId'], user_info['name'], points, estimated_savings)
        
        logger.info(f"Finding {finding_id} accepted by {user_info['email']}, +{points} points")
        
        return json_response(200, {
            'message': 'Finding accepted',
            'findingId': finding_id,
            'pointsEarned': points,
            'savingsUsd': estimated_savings,
        })
    except Exception as e:
        logger.error(f"Error accepting finding {finding_id}: {e}")
        return json_response(500, {'error': 'Failed to accept finding'})


def reject_finding(finding_id: str, body: dict, user_info: dict) -> dict:
    """Reject a finding with learning loop feedback."""
    if not is_champion(user_info):
        return json_response(403, {'error': 'Insufficient permissions'})
    
    reason = body.get('reason')
    if not reason:
        return json_response(400, {'error': 'Rejection reason is required'})
    
    try:
        # Get current finding
        response = findings_table.get_item(Key={'findingId': finding_id})
        finding = response.get('Item')
        
        if not finding:
            return json_response(404, {'error': 'Finding not found'})
        
        if finding.get('status') != 'pending':
            return json_response(400, {'error': f"Finding already {finding.get('status')}"})
        
        timestamp = get_current_timestamp()
        category = body.get('category', 'other')
        
        # Update finding status
        findings_table.update_item(
            Key={'findingId': finding_id},
            UpdateExpression="""
                SET #status = :status,
                    rejectedAt = :rejectedAt,
                    rejectedBy = :rejectedBy,
                    rejectionReason = :rejectionReason,
                    rejectionCategory = :rejectionCategory,
                    updatedAt = :updatedAt
            """,
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'rejected',
                ':rejectedAt': timestamp,
                ':rejectedBy': user_info['userId'],
                ':rejectionReason': reason,
                ':rejectionCategory': category,
                ':updatedAt': timestamp,
            },
        )
        
        # Record learning from rejection (learning loop)
        create_learning_from_rejection(finding, body, user_info)
        
        # Award points for rejection (still valuable feedback)
        points = SCORING_CONFIG['reject_base_points']
        if body.get('detailedFeedback'):
            points += SCORING_CONFIG['learning_contribution_points']
        
        update_user_score(user_info['userId'], user_info['name'], points, 0)
        
        logger.info(f"Finding {finding_id} rejected by {user_info['email']}, reason: {category}")
        
        return json_response(200, {
            'message': 'Finding rejected',
            'findingId': finding_id,
            'pointsEarned': points,
            'learningRecorded': True,
        })
    except Exception as e:
        logger.error(f"Error rejecting finding {finding_id}: {e}")
        return json_response(500, {'error': 'Failed to reject finding'})


# ==================== TEAMS ====================

def list_teams(user_info: dict) -> dict:
    """List all teams."""
    try:
        response = teams_table.scan()
        return json_response(200, {
            'teams': response.get('Items', []),
            'count': len(response.get('Items', [])),
        })
    except Exception as e:
        logger.error(f"Error listing teams: {e}")
        return json_response(500, {'error': 'Failed to list teams'})


def get_team(team_id: str) -> dict:
    """Get a single team by ID."""
    try:
        response = teams_table.get_item(Key={'teamId': team_id})
        team = response.get('Item')
        
        if not team:
            return json_response(404, {'error': 'Team not found'})
        
        return json_response(200, team)
    except Exception as e:
        logger.error(f"Error getting team {team_id}: {e}")
        return json_response(500, {'error': 'Failed to get team'})


def create_team(body: dict, user_info: dict) -> dict:
    """Create a new team (admin only)."""
    if not is_admin(user_info):
        return json_response(403, {'error': 'Admin access required'})
    
    name = body.get('name')
    if not name:
        return json_response(400, {'error': 'Team name is required'})
    
    try:
        team_id = str(uuid.uuid4())
        timestamp = get_current_timestamp()
        
        team = {
            'teamId': team_id,
            'name': name,
            'description': body.get('description', ''),
            'members': body.get('members', []),
            'slackChannel': body.get('slackChannel', ''),
            'costCenter': body.get('costCenter', ''),
            'createdAt': timestamp,
            'createdBy': user_info['userId'],
            'updatedAt': timestamp,
        }
        
        teams_table.put_item(Item=team)
        
        logger.info(f"Team '{name}' created by {user_info['email']}")
        return json_response(201, team)
    except Exception as e:
        logger.error(f"Error creating team: {e}")
        return json_response(500, {'error': 'Failed to create team'})


def update_team(team_id: str, body: dict, user_info: dict) -> dict:
    """Update a team (admin only)."""
    if not is_admin(user_info):
        return json_response(403, {'error': 'Admin access required'})
    
    try:
        update_parts = []
        attr_values = {}
        attr_names = {}
        
        allowed_fields = ['name', 'description', 'members', 'slackChannel', 'costCenter']
        for field in allowed_fields:
            if field in body:
                update_parts.append(f"#{field} = :{field}")
                attr_names[f"#{field}"] = field
                attr_values[f":{field}"] = body[field]
        
        if not update_parts:
            return json_response(400, {'error': 'No valid fields to update'})
        
        update_parts.append("#updatedAt = :updatedAt")
        attr_names["#updatedAt"] = "updatedAt"
        attr_values[":updatedAt"] = get_current_timestamp()
        
        response = teams_table.update_item(
            Key={'teamId': team_id},
            UpdateExpression=f"SET {', '.join(update_parts)}",
            ExpressionAttributeNames=attr_names,
            ExpressionAttributeValues=attr_values,
            ReturnValues='ALL_NEW',
        )
        
        return json_response(200, response.get('Attributes', {}))
    except Exception as e:
        logger.error(f"Error updating team {team_id}: {e}")
        return json_response(500, {'error': 'Failed to update team'})


def delete_team(team_id: str, user_info: dict) -> dict:
    """Delete a team (admin only)."""
    if not is_admin(user_info):
        return json_response(403, {'error': 'Admin access required'})
    
    try:
        teams_table.delete_item(Key={'teamId': team_id})
        logger.info(f"Team {team_id} deleted by {user_info['email']}")
        return json_response(200, {'message': 'Team deleted', 'teamId': team_id})
    except Exception as e:
        logger.error(f"Error deleting team {team_id}: {e}")
        return json_response(500, {'error': 'Failed to delete team'})


# ==================== SCOPING ====================

def list_scoping_rules(user_info: dict) -> dict:
    """List all scoping rules."""
    try:
        response = scoping_table.scan()
        return json_response(200, {
            'rules': response.get('Items', []),
            'count': len(response.get('Items', [])),
        })
    except Exception as e:
        logger.error(f"Error listing scoping rules: {e}")
        return json_response(500, {'error': 'Failed to list scoping rules'})


def create_scoping_rule(body: dict, user_info: dict) -> dict:
    """Create a scoping rule (admin only)."""
    if not is_admin(user_info):
        return json_response(403, {'error': 'Admin access required'})
    
    rule_type = body.get('type')
    pattern = body.get('pattern')
    team_id = body.get('teamId')
    
    if not all([rule_type, pattern, team_id]):
        return json_response(400, {'error': 'type, pattern, and teamId are required'})
    
    valid_types = ['accountId', 'resourceTag', 'serviceName', 'costCenter']
    if rule_type not in valid_types:
        return json_response(400, {'error': f'Invalid type. Must be one of: {valid_types}'})
    
    try:
        rule_id = str(uuid.uuid4())
        timestamp = get_current_timestamp()
        
        rule = {
            'ruleId': rule_id,
            'type': rule_type,
            'pattern': pattern,
            'teamId': team_id,
            'description': body.get('description', ''),
            'priority': body.get('priority', 100),
            'createdAt': timestamp,
            'createdBy': user_info['userId'],
        }
        
        scoping_table.put_item(Item=rule)
        
        logger.info(f"Scoping rule created: {rule_type}={pattern} -> {team_id}")
        return json_response(201, rule)
    except Exception as e:
        logger.error(f"Error creating scoping rule: {e}")
        return json_response(500, {'error': 'Failed to create scoping rule'})


def delete_scoping_rule(rule_id: str, user_info: dict) -> dict:
    """Delete a scoping rule (admin only)."""
    if not is_admin(user_info):
        return json_response(403, {'error': 'Admin access required'})
    
    try:
        scoping_table.delete_item(Key={'ruleId': rule_id})
        logger.info(f"Scoping rule {rule_id} deleted by {user_info['email']}")
        return json_response(200, {'message': 'Scoping rule deleted', 'ruleId': rule_id})
    except Exception as e:
        logger.error(f"Error deleting scoping rule {rule_id}: {e}")
        return json_response(500, {'error': 'Failed to delete scoping rule'})


# ==================== LEADERBOARD & SCORES ====================

def get_leaderboard(event: dict) -> dict:
    """Get leaderboard for current or specified month."""
    params = event.get('queryStringParameters') or {}
    month = params.get('month', get_current_month())
    
    try:
        response = scores_table.query(
            IndexName='month-savings-index',
            KeyConditionExpression=Key('month').eq(month),
            ScanIndexForward=False,  # Highest savings first
            Limit=50,
        )
        
        # Add rank to each entry
        entries = response.get('Items', [])
        for idx, entry in enumerate(entries, 1):
            entry['rank'] = idx
        
        return json_response(200, {
            'month': month,
            'leaderboard': entries,
            'count': len(entries),
        })
    except Exception as e:
        logger.error(f"Error getting leaderboard: {e}")
        return json_response(500, {'error': 'Failed to get leaderboard'})


def get_user_scores(user_id: str, event: dict) -> dict:
    """Get scores for a specific user."""
    params = event.get('queryStringParameters') or {}
    
    try:
        if params.get('month'):
            # Get specific month
            response = scores_table.get_item(
                Key={'userId': user_id, 'month': params['month']}
            )
            score = response.get('Item')
            if not score:
                return json_response(404, {'error': 'No scores found for this month'})
            return json_response(200, score)
        else:
            # Get all months for user
            response = scores_table.query(
                KeyConditionExpression=Key('userId').eq(user_id),
                ScanIndexForward=False,
                Limit=12,  # Last 12 months
            )
            return json_response(200, {
                'userId': user_id,
                'scores': response.get('Items', []),
            })
    except Exception as e:
        logger.error(f"Error getting scores for user {user_id}: {e}")
        return json_response(500, {'error': 'Failed to get user scores'})


# ==================== LEARNINGS ====================

def list_learnings(event: dict) -> dict:
    """List learnings with optional filtering."""
    params = event.get('queryStringParameters') or {}
    service = params.get('service')
    
    try:
        if service:
            response = learnings_table.query(
                IndexName='service-category-index',
                KeyConditionExpression=Key('service').eq(service),
                Limit=100,
            )
        else:
            response = learnings_table.scan(Limit=100)
        
        return json_response(200, {
            'learnings': response.get('Items', []),
            'count': len(response.get('Items', [])),
        })
    except Exception as e:
        logger.error(f"Error listing learnings: {e}")
        return json_response(500, {'error': 'Failed to list learnings'})


# ==================== DASHBOARD ====================

def get_dashboard_stats(user_info: dict) -> dict:
    """Get aggregated dashboard statistics."""
    try:
        current_month = get_current_month()
        
        # Count findings by status
        pending_response = findings_table.query(
            IndexName='status-createdAt-index',
            KeyConditionExpression=Key('status').eq('pending'),
            Select='COUNT',
        )
        accepted_response = findings_table.query(
            IndexName='status-createdAt-index',
            KeyConditionExpression=Key('status').eq('accepted'),
            Select='COUNT',
        )
        rejected_response = findings_table.query(
            IndexName='status-createdAt-index',
            KeyConditionExpression=Key('status').eq('rejected'),
            Select='COUNT',
        )
        
        # Get total savings this month
        leaderboard = scores_table.query(
            IndexName='month-savings-index',
            KeyConditionExpression=Key('month').eq(current_month),
        )
        total_savings = sum(
            float(entry.get('totalSavingsUsd', 0)) 
            for entry in leaderboard.get('Items', [])
        )
        total_points = sum(
            int(entry.get('totalPoints', 0)) 
            for entry in leaderboard.get('Items', [])
        )
        
        # Get user's personal stats
        user_score = scores_table.get_item(
            Key={'userId': user_info['userId'], 'month': current_month}
        ).get('Item', {})
        
        # Get team count
        teams_response = teams_table.scan(Select='COUNT')
        
        return json_response(200, {
            'month': current_month,
            'findings': {
                'pending': pending_response.get('Count', 0),
                'accepted': accepted_response.get('Count', 0),
                'rejected': rejected_response.get('Count', 0),
                'total': (pending_response.get('Count', 0) + 
                         accepted_response.get('Count', 0) + 
                         rejected_response.get('Count', 0)),
            },
            'savings': {
                'totalUsd': total_savings,
                'totalPoints': total_points,
            },
            'teams': {
                'count': teams_response.get('Count', 0),
            },
            'user': {
                'points': int(user_score.get('totalPoints', 0)),
                'savingsUsd': float(user_score.get('totalSavingsUsd', 0)),
                'findingsAccepted': int(user_score.get('findingsAccepted', 0)),
                'findingsRejected': int(user_score.get('findingsRejected', 0)),
            },
        })
    except Exception as e:
        logger.error(f"Error getting dashboard stats: {e}")
        return json_response(500, {'error': 'Failed to get dashboard statistics'})


# ==================== HELPER FUNCTIONS ====================

def get_user_team(user_id: str) -> str | None:
    """Get the team ID for a user."""
    try:
        response = teams_table.scan(
            FilterExpression=Attr('members').contains(user_id)
        )
        teams = response.get('Items', [])
        return teams[0]['teamId'] if teams else None
    except Exception as e:
        logger.error(f"Error getting user team: {e}")
        return None


def calculate_accept_points(savings: float) -> int:
    """Calculate points for accepting a finding."""
    base = SCORING_CONFIG['accept_base_points']
    savings_bonus = int(savings * SCORING_CONFIG['savings_multiplier'])
    return base + savings_bonus


def update_user_score(user_id: str, user_name: str, points: int, savings: float) -> None:
    """Update user's score for the current month."""
    month = get_current_month()
    
    try:
        scores_table.update_item(
            Key={'userId': user_id, 'month': month},
            UpdateExpression="""
                SET userName = :userName,
                    totalPoints = if_not_exists(totalPoints, :zero) + :points,
                    totalSavingsUsd = if_not_exists(totalSavingsUsd, :zeroDecimal) + :savings,
                    findingsAccepted = if_not_exists(findingsAccepted, :zero) + :accepted,
                    findingsRejected = if_not_exists(findingsRejected, :zero) + :rejected,
                    lastActivity = :timestamp
            """,
            ExpressionAttributeValues={
                ':userName': user_name,
                ':points': points,
                ':savings': Decimal(str(savings)),
                ':accepted': 1 if savings > 0 else 0,
                ':rejected': 0 if savings > 0 else 1,
                ':timestamp': get_current_timestamp(),
                ':zero': 0,
                ':zeroDecimal': Decimal('0'),
            },
        )
    except Exception as e:
        logger.error(f"Error updating user score: {e}")


def create_learning_from_finding(finding: dict, body: dict, user_info: dict) -> None:
    """Create a learning record from an accepted finding."""
    try:
        learning = {
            'learningId': str(uuid.uuid4()),
            'findingId': finding['findingId'],
            'type': 'acceptance',
            'service': finding.get('service', 'unknown'),
            'category': finding.get('category', 'general'),
            'title': f"Accepted: {finding.get('title', 'Untitled')}",
            'description': body.get('learningDescription', finding.get('description', '')),
            'implementationDetails': body.get('implementationDetails', ''),
            'estimatedSavingsUsd': finding.get('estimatedSavingsUsd', 0),
            'tags': finding.get('tags', []),
            'createdAt': get_current_timestamp(),
            'createdBy': user_info['userId'],
        }
        
        learnings_table.put_item(Item=learning)
        logger.info(f"Learning created from finding {finding['findingId']}")
    except Exception as e:
        logger.error(f"Error creating learning: {e}")


def create_learning_from_rejection(finding: dict, body: dict, user_info: dict) -> None:
    """Create a learning record from a rejected finding (learning loop)."""
    try:
        learning = {
            'learningId': str(uuid.uuid4()),
            'findingId': finding['findingId'],
            'type': 'rejection',
            'service': finding.get('service', 'unknown'),
            'category': body.get('category', 'other'),
            'title': f"Rejected: {finding.get('title', 'Untitled')}",
            'rejectionReason': body.get('reason', ''),
            'detailedFeedback': body.get('detailedFeedback', ''),
            'suggestedImprovement': body.get('suggestedImprovement', ''),
            'tags': ['rejection', body.get('category', 'other')],
            'createdAt': get_current_timestamp(),
            'createdBy': user_info['userId'],
        }
        
        learnings_table.put_item(Item=learning)
        logger.info(f"Rejection learning created from finding {finding['findingId']}")
    except Exception as e:
        logger.error(f"Error creating rejection learning: {e}")


# ==================== MAIN HANDLER ====================

def handler(event: dict, context) -> dict:
    """Main Lambda handler - routes requests to appropriate function."""
    logger.info(f"Request: {event.get('httpMethod')} {event.get('path')}")
    
    http_method = event.get('httpMethod', '')
    path = event.get('path', '')
    path_params = event.get('pathParameters') or {}
    
    # Parse body if present
    body = {}
    if event.get('body'):
        try:
            body = json.loads(event['body'])
        except json.JSONDecodeError:
            return json_response(400, {'error': 'Invalid JSON body'})
    
    # Get user info from Cognito
    user_info = get_user_info(event)
    
    try:
        # Route: /findings
        if path == '/findings' and http_method == 'GET':
            return list_findings(event, user_info)
        
        # Route: /findings/{findingId}
        if path.startswith('/findings/') and '{findingId}' not in path:
            finding_id = path_params.get('findingId') or path.split('/')[-1].split('/')[0]
            
            if '/accept' in path and http_method == 'POST':
                return accept_finding(finding_id, body, user_info)
            elif '/reject' in path and http_method == 'POST':
                return reject_finding(finding_id, body, user_info)
            elif http_method == 'GET':
                return get_finding(finding_id)
            elif http_method == 'PATCH':
                return update_finding(finding_id, body, user_info)
        
        # Route: /teams
        if path == '/teams':
            if http_method == 'GET':
                return list_teams(user_info)
            elif http_method == 'POST':
                return create_team(body, user_info)
        
        # Route: /teams/{teamId}
        if path.startswith('/teams/') and path_params.get('teamId'):
            team_id = path_params['teamId']
            if http_method == 'GET':
                return get_team(team_id)
            elif http_method == 'PUT':
                return update_team(team_id, body, user_info)
            elif http_method == 'DELETE':
                return delete_team(team_id, user_info)
        
        # Route: /scoping
        if path == '/scoping':
            if http_method == 'GET':
                return list_scoping_rules(user_info)
            elif http_method == 'POST':
                return create_scoping_rule(body, user_info)
        
        # Route: /scoping/{ruleId}
        if path.startswith('/scoping/') and path_params.get('ruleId'):
            if http_method == 'DELETE':
                return delete_scoping_rule(path_params['ruleId'], user_info)
        
        # Route: /leaderboard
        if path == '/leaderboard' and http_method == 'GET':
            return get_leaderboard(event)
        
        # Route: /scores/{userId}
        if path.startswith('/scores/') and path_params.get('userId'):
            if http_method == 'GET':
                return get_user_scores(path_params['userId'], event)
        
        # Route: /learnings
        if path == '/learnings' and http_method == 'GET':
            return list_learnings(event)
        
        # Route: /dashboard
        if path == '/dashboard' and http_method == 'GET':
            return get_dashboard_stats(user_info)
        
        # Route not found
        return json_response(404, {'error': f'Route not found: {http_method} {path}'})
        
    except Exception as e:
        logger.error(f"Unhandled error: {e}", exc_info=True)
        return json_response(500, {'error': 'Internal server error'})
