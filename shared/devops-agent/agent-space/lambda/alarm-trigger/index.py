"""
Alarm -> incident: turns a CloudWatch alarm notification (via SNS) into a signed incident
event on the Agent Space's eventChannel webhook, which starts an investigation.

The payload does not prescribe what to investigate: it forwards the alarm as-is plus the
lines the demo declared in INCIDENT_CONTEXT (a JSON object, label -> value, e.g. the cluster
or file system the alarm is about) and lets the agent find the root cause.

Environment: WEBHOOK_URL, SECRET_ARN, SECRET_REGION (the Agent Space region, where the HMAC
secret lives; may differ from this function's region), INCIDENT_CONTEXT (optional).
"""

import base64
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Tuple
from urllib import error, request

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get('AWS_REGION', '')
CONTEXT: Dict[str, str] = json.loads(os.environ.get('INCIDENT_CONTEXT') or '{}')


def get_secret() -> str:
    client = boto3.client('secretsmanager', region_name=os.environ.get('SECRET_REGION') or REGION)
    return client.get_secret_value(SecretId=os.environ['SECRET_ARN'])['SecretString']


def sign(secret: str, timestamp: str, payload: Dict[str, Any]) -> str:
    """HMAC-SHA256 over '<timestamp>:<json payload>', base64, as the webhook verifies it."""
    message = f'{timestamp}:{json.dumps(payload)}'
    return base64.b64encode(hmac.new(secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).digest()).decode('utf-8')


def send(payload: Dict[str, Any]) -> Tuple[int, str]:
    timestamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + 'Z'
    headers = {
        'Content-Type': 'application/json',
        'x-amzn-event-timestamp': timestamp,
        'x-amzn-event-signature': sign(get_secret(), timestamp, payload),
    }
    req = request.Request(os.environ['WEBHOOK_URL'], data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
    try:
        with request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode('utf-8')
    except error.HTTPError as e:
        logger.error('Webhook HTTP %s: %s', e.code, e.read().decode('utf-8', 'replace'))
        raise


def priority(alarm_name: str, alarm_description: str) -> str:
    text = f'{alarm_name} {alarm_description}'.lower()
    if any(w in text for w in ('critical', 'database', 'connection', 'crash')):
        return 'CRITICAL'
    if any(w in text for w in ('error', '5xx', 'failure', 'dns')):
        return 'HIGH'
    if any(w in text for w in ('latency', 'degraded', 'slow')):
        return 'MEDIUM'
    return 'HIGH'


def incident(alarm: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    name = alarm.get('AlarmName', 'CloudWatch Alarm')
    description = alarm.get('AlarmDescription', '')
    reason = alarm.get('NewStateReason', '')
    trigger = alarm.get('Trigger', {})
    context_lines = ''.join(f'{label}: {value}\n' for label, value in CONTEXT.items())
    return {
        'eventType': 'incident',
        'incidentId': f'{name}-{request_id}',
        'action': 'created',
        'priority': priority(name, description),
        'title': f'CloudWatch Alarm: {name}',
        'description': (
            f"CloudWatch alarm '{name}' triggered in {REGION}.\n\n"
            f'Description: {description}\nReason: {reason}\n\n'
            f'{context_lines}Region: {REGION}'
        ),
        'timestamp': alarm.get('StateChangeTime') or datetime.now(timezone.utc).isoformat(),
        'service': 'cloudwatch',
        'data': {
            'alarmName': name,
            'alarmArn': alarm.get('AlarmArn', ''),
            'alarmDescription': description,
            'newStateValue': alarm.get('NewStateValue', 'ALARM'),
            'newStateReason': reason,
            'trigger': {
                'metricName': trigger.get('MetricName', ''),
                'namespace': trigger.get('Namespace', ''),
                'statistic': trigger.get('Statistic', ''),
                'period': trigger.get('Period', 0),
                'threshold': trigger.get('Threshold', 0),
            },
            'context': CONTEXT,
            'region': REGION,
        },
    }


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    logger.info('Received event: %s', json.dumps(event))
    for record in event.get('Records', []):
        message = record.get('Sns', {}).get('Message', '')
        try:
            alarm = json.loads(message)
        except json.JSONDecodeError:
            alarm = {'AlarmName': 'CloudWatch Alarm', 'AlarmDescription': message, 'NewStateReason': message}
        if alarm.get('NewStateValue', 'ALARM') != 'ALARM':
            logger.info('Skipping non-ALARM state: %s', alarm.get('NewStateValue'))
            continue
        payload = incident(alarm, context.aws_request_id)
        logger.info('Sending incident to DevOps Agent: %s', json.dumps(payload))
        status, body = send(payload)
        logger.info('DevOps Agent response: %s - %s', status, body)
        return {'statusCode': 200, 'body': json.dumps({'message': 'Incident sent to DevOps Agent',
                                                       'incidentId': payload['incidentId'], 'webhookStatus': status})}
    return {'statusCode': 200, 'body': json.dumps({'message': 'No ALARM records to process'})}
