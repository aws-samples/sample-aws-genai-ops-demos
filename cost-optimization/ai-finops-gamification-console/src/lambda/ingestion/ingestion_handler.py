"""
FinOps Gamification Console - Ingestion Handler
Retrieves FinOps Agent reports from Slack and parses them into findings.
"""

import html
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


def _flatten_rich_text(elements: list) -> str:
    """Recursively concatenate the readable text out of a Slack rich_text
    element tree (rich_text_section, rich_text_list, rich_text_quote, text).
    HTML-unescapes each text run, since Slack encodes '<', '>', and '&' as
    entities (e.g. a header rendering "Cost Anomalies (>10% over expected)"
    is delivered as "Cost Anomalies (&gt;10% over expected)")."""
    parts = []
    for el in elements:
        el_type = el.get('type')
        if el_type == 'text':
            parts.append(html.unescape(el.get('text', '')))
        elif el_type == 'rich_text_list':
            ordered = el.get('style') == 'ordered'
            for i, item in enumerate(el.get('elements', [])):
                prefix = f"{i + 1}. " if ordered else '- '
                parts.append('\n' + prefix + _flatten_rich_text(item.get('elements', [])))
        elif 'elements' in el:
            parts.append(_flatten_rich_text(el['elements']))
    return ''.join(parts)


def _get_header_text(block: dict) -> str:
    """Extract and HTML-unescape a `header` block's plain_text."""
    return html.unescape(block.get('text', {}).get('text', ''))


def extract_full_text_from_blocks(blocks: list) -> str:
    """
    Flatten a Slack Block Kit `blocks` array into plain text.

    AWS FinOps Agent posts reports using Block Kit (header/table/rich_text
    blocks); the message's top-level `text` field is only a short, truncated
    fallback string and does not contain the report content. This walks the
    real structure so downstream parsing (account IDs, tags, recommendations)
    has the full content to work with.
    """
    lines = []
    for block in blocks:
        block_type = block.get('type')
        if block_type == 'header':
            text = _get_header_text(block)
            if text:
                lines.append(text)
        elif block_type == 'rich_text':
            flattened = _flatten_rich_text(block.get('elements', []))
            if flattened:
                lines.append(flattened)
        elif block_type == 'table':
            for row in block.get('rows', []):
                cells = [_flatten_rich_text(cell.get('elements', [])) for cell in row]
                lines.append(' | '.join(cells))
        elif block_type == 'context':
            for el in block.get('elements', []):
                if el.get('type') == 'mrkdwn':
                    lines.append(html.unescape(el.get('text', '')))
    return '\n'.join(line for line in lines if line)


# Keyword -> (service, category) used to classify a recommendation line by
# its title text. Checked in order; first match wins.
RECOMMENDATION_KEYWORDS = [
    ('reserved instance', ('General', 'commitment')),
    ('savings plan', ('General', 'commitment')),
    ('graviton', ('AmazonEC2', 'rightsizing')),
    ('rightsiz', ('AmazonEC2', 'rightsizing')),
    ('rds', ('AmazonRDS', 'optimization')),
    ('ec2', ('AmazonEC2', 'optimization')),
    ('ebs', ('AmazonEBS', 'storage')),
    ('s3', ('AmazonS3', 'storage')),
    ('lambda', ('AWSLambda', 'compute')),
    ('eks', ('AmazonEKS', 'optimization')),
    ('nat gateway', ('AmazonVPC', 'waste')),
    ('elastic ip', ('AmazonEC2', 'waste')),
    ('idle', ('General', 'waste')),
    ('unused', ('General', 'waste')),
]

# Matches lines like:
#   "Stop idle RDS instance — devops-agent-eks-dev-postgres (eu-west-1) — $13.91/month | Effort: Low"
#   "Migrate EC2 to Graviton — i-07c84273c3f3237b7 t3.medium -> t4g.medium (us-west-2) — $5.78/month (20% savings) | Effort: Very High"
RECOMMENDATION_LINE_PATTERN = re.compile(
    r'^(?P<title>.+?)\s*[—-]\s*(?P<resource>.+?)\s*\((?P<region>[a-z0-9-]+)\)\s*[—-]\s*'
    r'\$(?P<savings>[\d,]+\.?\d*)\s*/\s*month'
    r'(?:\s*\((?P<extra>[^)]+)\))?\s*\|\s*Effort:\s*(?P<effort>.+?)\s*$',
    re.IGNORECASE,
)


def classify_recommendation(title: str) -> tuple[str, str]:
    """Guess (service, category) for a recommendation from its title text."""
    title_lower = title.lower()
    for keyword, classification in RECOMMENDATION_KEYWORDS:
        if keyword in title_lower:
            return classification
    return 'General', 'optimization'


def extract_recommendations_from_blocks(blocks: list) -> list[dict]:
    """
    Extract structured optimization recommendations from a FinOps Agent
    report's Block Kit blocks.

    The Agent renders the "Optimization Recommendations" section as a
    `header` block followed by a `rich_text` block containing an ordered
    `rich_text_list`. Each list item is a single line matching
    RECOMMENDATION_LINE_PATTERN. Lines that don't match are logged and
    skipped rather than silently dropped, so format drift is visible in
    CloudWatch logs.
    """
    recommendations = []
    in_recommendations_section = False

    for block in blocks:
        block_type = block.get('type')

        if block_type == 'header':
            header_text = block.get('text', {}).get('text', '')
            in_recommendations_section = 'optimization recommendation' in header_text.lower()
            continue

        if block_type == 'divider':
            in_recommendations_section = False
            continue

        if not in_recommendations_section or block_type != 'rich_text':
            continue

        for element in block.get('elements', []):
            if element.get('type') != 'rich_text_list':
                continue

            for item in element.get('elements', []):
                item_text = _flatten_rich_text(item.get('elements', [])).strip()
                if not item_text:
                    continue

                match = RECOMMENDATION_LINE_PATTERN.match(item_text)
                if not match:
                    logger.warning(f"Could not parse recommendation line: {item_text!r}")
                    continue

                recommendations.append({
                    'title': match.group('title').strip(),
                    'resource': match.group('resource').strip(),
                    'region': match.group('region').strip(),
                    'savingsUsd': match.group('savings').replace(',', ''),
                    'extra': (match.group('extra') or '').strip(),
                    'effort': match.group('effort').strip(),
                    'rawText': item_text,
                })

    return recommendations


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


def parse_finops_report_from_text(message_text: str, message_ts: str) -> list[dict]:
    """
    Fallback parser for FinOps report messages that have no `blocks` array
    (e.g. a manually typed test message, or a plain-text webhook payload).

    Real AWS FinOps Agent Slack posts use Block Kit and are parsed by
    parse_finops_report() / extract_recommendations_from_blocks() instead;
    this path only runs when a message has no blocks to walk.
    """
    findings = []

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

    account_ids = extract_account_ids(message_text)
    resource_ids = extract_resource_ids(message_text)
    tags = extract_tags(message_text)

    for finding_type, config in FINDING_PATTERNS.items():
        matches = re.finditer(config['pattern'], message_text, re.IGNORECASE | re.DOTALL)

        for match in matches:
            savings_match = match.group(1) if match.groups() else None
            savings_amount = parse_savings_amount(savings_match) if savings_match else 0.0

            start = max(0, match.start() - 100)
            end = min(len(message_text), match.end() + 100)
            context = message_text[start:end].strip()

            findings.append({
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
                'resourceIds': resource_ids[:10],
                'tags': list(tags.keys()),
                'createdAt': datetime.now(timezone.utc).isoformat(),
                'source': 'slack-ingestion-text-fallback',
            })

    return findings


def parse_finops_report(message: dict) -> list[dict]:
    """
    Parse a FinOps Agent Slack message and extract findings.

    Real AWS FinOps Agent reports are posted using Slack Block Kit; the
    message's top-level `text` field is only a short, truncated fallback
    string and never contains the "Optimization Recommendations" section.
    Confirmed against a live report (see README "FinOps Agent Setup" for the
    raw JSON). So the primary path here walks `blocks` directly via
    extract_recommendations_from_blocks(); the legacy text-regex parser only
    runs as a fallback for messages with no `blocks` at all.
    """
    message_ts = message.get('ts', '')
    blocks = message.get('blocks')

    if not blocks:
        return parse_finops_report_from_text(message.get('text', ''), message_ts)

    full_text = extract_full_text_from_blocks(blocks)
    account_ids = extract_account_ids(full_text)
    resource_ids = extract_resource_ids(full_text)
    tags = extract_tags(full_text)

    recommendations = extract_recommendations_from_blocks(blocks)
    if not recommendations:
        # Message had blocks (so it's a real report), but the
        # "Optimization Recommendations" section wasn't found or its lines
        # didn't match RECOMMENDATION_LINE_PATTERN. Fall back to the
        # flattened text so a format change doesn't silently drop findings.
        logger.warning(
            f"No recommendations extracted from blocks for message {message_ts}; "
            "falling back to text-regex parser"
        )
        return parse_finops_report_from_text(full_text, message_ts)

    findings = []
    for rec in recommendations:
        savings_amount = parse_savings_amount(rec['savingsUsd'])
        service, category = classify_recommendation(rec['title'])

        resource_part = rec['resource']
        if rec['region']:
            resource_part += f" ({rec['region']})"

        description_parts = [rec['title'], resource_part]
        if rec['extra']:
            description_parts.append(f"({rec['extra']})")
        description_parts.append(f"Effort: {rec['effort']}")

        findings.append({
            'findingId': str(uuid.uuid4()),
            'type': 'optimization_recommendation',
            'title': rec['title'],
            'description': ' — '.join(description_parts),
            'service': service,
            'category': category,
            'estimatedSavingsUsd': Decimal(str(savings_amount)),
            'status': 'pending',
            'priority': calculate_priority(savings_amount),
            'effort': rec['effort'],
            'resourceRef': rec['resource'],
            'region': rec['region'],
            'sourceMessageTs': message_ts,
            'accountIds': account_ids,
            'resourceIds': resource_ids[:10],
            'tags': list(tags.keys()),
            'createdAt': datetime.now(timezone.utc).isoformat(),
            'source': 'slack-ingestion',
        })

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
        message_ts = message.get('ts', '')
        
        # Skip if already processed
        if message_ts in processed_timestamps:
            continue
        
        # Parse the message for findings (uses Block Kit blocks when
        # present; falls back to text-regex parsing otherwise)
        findings = parse_finops_report(message)
        
        for finding in findings:
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
            else:
                findings_skipped += 1
        
        # Mark this message as processed once all of its findings have been
        # attempted. A single message can produce multiple findings (e.g.
        # several optimization recommendations in one report), so dedup is
        # message-level via the `if message_ts in processed_timestamps`
        # check above; per-finding dedup on the same key would (and
        # previously did) incorrectly treat sibling findings from the same
        # message as duplicates of each other.
        if findings:
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
