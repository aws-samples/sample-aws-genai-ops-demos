"""
FinOps Gamification Console - Ingestion Handler
Retrieves FinOps Agent reports from Slack and parses them into findings.
"""

import json
import os
import re
import uuid
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
secrets_manager = boto3.client('secretsmanager')

# Environment variables
FINDINGS_TABLE = os.environ.get('FINDINGS_TABLE', 'finops-findings')
LEARNINGS_TABLE = os.environ.get('LEARNINGS_TABLE', 'finops-learnings')
SCOPING_TABLE = os.environ.get('SCOPING_TABLE', 'finops-scoping')
SLACK_SECRET_ARN = os.environ.get('SLACK_SECRET_ARN', '')
SLACK_CHANNEL_ID = os.environ.get('SLACK_CHANNEL_ID', '')

# Initialize table objects
findings_table = dynamodb.Table(FINDINGS_TABLE)
learnings_table = dynamodb.Table(LEARNINGS_TABLE)
scoping_table = dynamodb.Table(SCOPING_TABLE)

# Pattern definitions for parsing FinOps Agent reports
FINDING_PATTERNS = {
    'ec2_rightsizing': {
        'pattern': r'(?:EC2|Instance)\s+(?:rightsizing|right-sizing).*?(\$[\d,]+(?:\.\d{2})?)\s*(?:per\s+month|monthly|/month)?',
        'category': 'rightsizing',
        'service': 'EC2',
    },
    'ebs_optimization': {
        'pattern': r'(?:EBS|Volume).*?(?:optimization|unused|unattached).*?(\$[\d,]+(?:\.\d{2})?)',
        'category': 'storage',
        'service': 'EBS',
    },
    's3_lifecycle': {
        'pattern': r'S3.*?(?:lifecycle|storage\s+class|Glacier).*?(\$[\d,]+(?:\.\d{2})?)',
        'category': 'storage',
        'service': 'S3',
    },
    'rds_optimization': {
        'pattern': r'RDS.*?(?:rightsizing|Reserved\s+Instance|RI|idle).*?(\$[\d,]+(?:\.\d{2})?)',
        'category': 'database',
        'service': 'RDS',
    },
    'lambda_optimization': {
        'pattern': r'Lambda.*?(?:memory|duration|concurrency).*?(\$[\d,]+(?:\.\d{2})?)',
        'category': 'compute',
        'service': 'Lambda',
    },
    'reserved_instances': {
        'pattern': r'(?:Reserved\s+Instance|RI|Savings\s+Plan).*?(\$[\d,]+(?:\.\d{2})?)',
        'category': 'commitment',
        'service': 'General',
    },
    'unused_resources': {
        'pattern': r'(?:unused|idle|orphaned).*?(?:resource|EIP|NAT|ALB).*?(\$[\d,]+(?:\.\d{2})?)',
        'category': 'waste',
        'service': 'General',
    },
}

# Rejection categories for learning loop
REJECTION_CATEGORIES = [
    'already_implemented',
    'business_constraint',
    'technical_blocker',
    'incorrect_analysis',
    'missing_context',
    'compliance_requirement',
    'performance_impact',
    'other',
]


def get_slack_token() -> str | None:
    """Retrieve Slack Bot Token from Secrets Manager."""
    if not SLACK_SECRET_ARN:
        logger.warning("SLACK_SECRET_ARN not configured")
        return None
    
    try:
        response = secrets_manager.get_secret_value(SecretId=SLACK_SECRET_ARN)
        secret = json.loads(response['SecretString'])
        token = secret.get('token')
        
        if token == 'PLACEHOLDER':
            logger.warning("Slack token is still PLACEHOLDER - needs configuration")
            return None
        
        return token
    except ClientError as e:
        logger.error(f"Failed to retrieve Slack token: {e}")
        return None


def fetch_slack_messages(token: str, channel_id: str, limit: int = 50) -> list[dict]:
    """Fetch recent messages from a Slack channel using the Slack API."""
    import urllib.request
    import urllib.error
    
    url = f"https://slack.com/api/conversations.history?channel={channel_id}&limit={limit}"
    
    request = urllib.request.Request(url, headers={
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    })
    
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            if not data.get('ok'):
                logger.error(f"Slack API error: {data.get('error')}")
                return []
            
            return data.get('messages', [])
    except urllib.error.URLError as e:
        logger.error(f"Failed to fetch Slack messages: {e}")
        return []


def parse_savings_amount(amount_str: str) -> float:
    """Parse a dollar amount string to float."""
    # Remove $ and commas, convert to float
    cleaned = amount_str.replace('$', '').replace(',', '')
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def extract_resource_ids(text: str) -> list[str]:
    """Extract AWS resource IDs from text."""
    patterns = [
        r'i-[a-f0-9]{8,17}',  # EC2 instances
        r'vol-[a-f0-9]{8,17}',  # EBS volumes
        r'arn:aws:[a-z0-9-]+:[a-z0-9-]*:\d*:[a-z0-9-/]+',  # ARNs
        r'sg-[a-f0-9]{8,17}',  # Security groups
        r'vpc-[a-f0-9]{8,17}',  # VPCs
        r'subnet-[a-f0-9]{8,17}',  # Subnets
    ]
    
    resource_ids = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        resource_ids.extend(matches)
    
    return list(set(resource_ids))


def extract_account_ids(text: str) -> list[str]:
    """Extract AWS account IDs from text."""
    pattern = r'\b(\d{12})\b'
    return list(set(re.findall(pattern, text)))


def extract_tags(text: str) -> dict[str, str]:
    """Extract resource tags mentioned in text."""
    # Look for common tag patterns like Key=Value or Key:Value
    tags = {}
    
    patterns = [
        r'(?:tag|Tag)[:\s]*([a-zA-Z0-9-_]+)\s*[=:]\s*([a-zA-Z0-9-_]+)',
        r'(?:Environment|Env)[:\s]*([a-zA-Z0-9-_]+)',
        r'(?:Team|team|Owner|owner)[:\s]*([a-zA-Z0-9-_]+)',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            if isinstance(match, tuple) and len(match) == 2:
                tags[match[0]] = match[1]
            elif isinstance(match, str):
                # Single capture group patterns
                if 'Environment' in pattern or 'Env' in pattern:
                    tags['Environment'] = match
                elif 'Team' in pattern or 'Owner' in pattern:
                    tags['Owner'] = match
    
    return tags


def parse_finops_report(message_text: str, message_ts: str) -> list[dict]:
    """Parse a FinOps Agent report message and extract findings."""
    findings = []
    
    # Check if this looks like a FinOps report
    finops_indicators = [
        'cost optimization',
        'cost savings',
        'recommendation',
        'potential savings',
        'estimated savings',
        'finops',
        'cost reduction',
    ]
    
    text_lower = message_text.lower()
    if not any(indicator in text_lower for indicator in finops_indicators):
        return findings
    
    # Extract account and resource context
    account_ids = extract_account_ids(message_text)
    resource_ids = extract_resource_ids(message_text)
    tags = extract_tags(message_text)
    
    # Try to match against known finding patterns
    for finding_type, config in FINDING_PATTERNS.items():
        matches = re.finditer(config['pattern'], message_text, re.IGNORECASE | re.DOTALL)
        
        for match in matches:
            # Extract savings amount
            savings_match = match.group(1) if match.groups() else None
            savings_amount = parse_savings_amount(savings_match) if savings_match else 0.0
            
            # Extract the relevant text around the match for context
            start = max(0, match.start() - 100)
            end = min(len(message_text), match.end() + 100)
            context = message_text[start:end].strip()
            
            finding = {
                'findingId': str(uuid.uuid4()),
                'type': finding_type,
                'title': f"{config['service']} {config['category'].title()} Opportunity",
                'description': context,
                'service': config['service'],
                'category': config['category'],
                'estimatedSavingsUsd': Decimal(str(savings_amount)),
                'status': 'pending',
                'priority': calculate_priority(savings_amount),
                'sourceMessageTs': message_ts,
                'accountIds': account_ids,
                'resourceIds': resource_ids[:10],  # Limit to 10 for readability
                'tags': list(tags.keys()),
                'createdAt': datetime.now(timezone.utc).isoformat(),
                'source': 'slack-ingestion',
            }
            
            findings.append(finding)
    
    # If no specific patterns matched but it looks like a finding, create a generic one
    if not findings and any(indicator in text_lower for indicator in finops_indicators):
        # Try to find any dollar amount
        dollar_pattern = r'\$[\d,]+(?:\.\d{2})?'
        dollar_matches = re.findall(dollar_pattern, message_text)
        total_savings = sum(parse_savings_amount(m) for m in dollar_matches)
        
        if total_savings > 0:
            finding = {
                'findingId': str(uuid.uuid4()),
                'type': 'generic_recommendation',
                'title': 'Cost Optimization Recommendation',
                'description': message_text[:500],  # First 500 chars
                'service': 'General',
                'category': 'optimization',
                'estimatedSavingsUsd': Decimal(str(total_savings)),
                'status': 'pending',
                'priority': calculate_priority(total_savings),
                'sourceMessageTs': message_ts,
                'accountIds': account_ids,
                'resourceIds': resource_ids[:10],
                'tags': list(tags.keys()),
                'createdAt': datetime.now(timezone.utc).isoformat(),
                'source': 'slack-ingestion',
            }
            findings.append(finding)
    
    return findings


def calculate_priority(savings_amount: float) -> str:
    """Calculate finding priority based on potential savings."""
    if savings_amount >= 10000:
        return 'critical'
    elif savings_amount >= 5000:
        return 'high'
    elif savings_amount >= 1000:
        return 'medium'
    else:
        return 'low'


def get_scoping_rules() -> list[dict]:
    """Get all scoping rules from DynamoDB."""
    try:
        response = scoping_table.scan()
        return response.get('Items', [])
    except Exception as e:
        logger.error(f"Failed to fetch scoping rules: {e}")
        return []


def assign_finding_to_team(finding: dict, scoping_rules: list[dict]) -> str | None:
    """Assign a finding to a team based on scoping rules."""
    # Sort rules by priority (lower number = higher priority)
    sorted_rules = sorted(scoping_rules, key=lambda r: r.get('priority', 100))
    
    for rule in sorted_rules:
        rule_type = rule.get('type')
        pattern = rule.get('pattern', '')
        
        if rule_type == 'accountId':
            if pattern in finding.get('accountIds', []):
                return rule.get('teamId')
        
        elif rule_type == 'serviceName':
            if pattern.lower() == finding.get('service', '').lower():
                return rule.get('teamId')
        
        elif rule_type == 'resourceTag':
            if pattern in finding.get('tags', []):
                return rule.get('teamId')
        
        elif rule_type == 'costCenter':
            # Cost center matching would require additional metadata
            pass
    
    return None


def check_for_similar_learnings(finding: dict) -> list[dict]:
    """Check if there are relevant learnings for this type of finding."""
    try:
        response = learnings_table.query(
            IndexName='service-category-index',
            KeyConditionExpression='service = :service',
            ExpressionAttributeValues={
                ':service': finding.get('service', 'unknown'),
            },
            Limit=5,
        )
        return response.get('Items', [])
    except Exception as e:
        logger.error(f"Failed to check learnings: {e}")
        return []


def is_duplicate_finding(finding: dict, processed_timestamps: set) -> bool:
    """Check if this finding was already processed."""
    # Check by message timestamp
    if finding.get('sourceMessageTs') in processed_timestamps:
        return True
    
    # Could also check by similar content hash
    return False


def store_finding(finding: dict) -> bool:
    """Store a finding in DynamoDB."""
    try:
        findings_table.put_item(Item=finding)
        logger.info(f"Stored finding: {finding['findingId']} - {finding['title']}")
        return True
    except Exception as e:
        logger.error(f"Failed to store finding: {e}")
        return False


def get_processed_timestamps(limit: int = 100) -> set:
    """Get recently processed message timestamps to avoid duplicates."""
    try:
        response = findings_table.scan(
            ProjectionExpression='sourceMessageTs',
            Limit=limit,
        )
        return {
            item.get('sourceMessageTs') 
            for item in response.get('Items', []) 
            if item.get('sourceMessageTs')
        }
    except Exception as e:
        logger.error(f"Failed to get processed timestamps: {e}")
        return set()


def handler(event: dict, context) -> dict:
    """
    Main Lambda handler - ingests FinOps reports from Slack.
    
    Can be triggered by:
    - EventBridge schedule (hourly)
    - Manual invocation
    - API Gateway (webhook from Slack)
    """
    logger.info("Starting FinOps report ingestion")
    
    # Check if this is a Slack webhook event
    if event.get('body'):
        try:
            body = json.loads(event['body'])
            # Handle Slack URL verification challenge
            if body.get('type') == 'url_verification':
                return {
                    'statusCode': 200,
                    'body': body.get('challenge', ''),
                }
        except json.JSONDecodeError:
            pass
    
    # Get Slack token
    token = get_slack_token()
    if not token:
        logger.error("Slack token not available - skipping ingestion")
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Slack token not configured',
                'findingsProcessed': 0,
            }),
        }
    
    # Check channel configuration
    channel_id = SLACK_CHANNEL_ID
    if not channel_id:
        logger.error("SLACK_CHANNEL_ID not configured")
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Slack channel not configured',
                'findingsProcessed': 0,
            }),
        }
    
    # Fetch messages from Slack
    messages = fetch_slack_messages(token, channel_id)
    logger.info(f"Fetched {len(messages)} messages from Slack")
    
    # Get scoping rules for team assignment
    scoping_rules = get_scoping_rules()
    logger.info(f"Loaded {len(scoping_rules)} scoping rules")
    
    # Get already processed timestamps to avoid duplicates
    processed_timestamps = get_processed_timestamps()
    
    # Process each message
    findings_created = 0
    findings_skipped = 0
    
    for message in messages:
        message_text = message.get('text', '')
        message_ts = message.get('ts', '')
        
        # Skip if already processed
        if message_ts in processed_timestamps:
            continue
        
        # Parse the message for findings
        findings = parse_finops_report(message_text, message_ts)
        
        for finding in findings:
            # Check for duplicates
            if is_duplicate_finding(finding, processed_timestamps):
                findings_skipped += 1
                continue
            
            # Assign to team based on scoping rules
            team_id = assign_finding_to_team(finding, scoping_rules)
            if team_id:
                finding['assignedTeamId'] = team_id
            
            # Check for relevant learnings (could enrich the finding)
            learnings = check_for_similar_learnings(finding)
            if learnings:
                finding['relatedLearnings'] = [l['learningId'] for l in learnings[:3]]
            
            # Store the finding
            if store_finding(finding):
                findings_created += 1
                processed_timestamps.add(message_ts)
    
    logger.info(f"Ingestion complete: {findings_created} created, {findings_skipped} skipped")
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Ingestion complete',
            'messagesProcessed': len(messages),
            'findingsCreated': findings_created,
            'findingsSkipped': findings_skipped,
        }),
    }
