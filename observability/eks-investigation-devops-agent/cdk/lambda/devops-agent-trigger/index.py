import json
import boto3
import os
import hmac
import hashlib
import base64
import logging
from datetime import datetime
from urllib import request, error

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def get_secret():
    """Retrieve webhook secret from Secrets Manager"""
    client = boto3.client('secretsmanager')
    response = client.get_secret_value(SecretId=os.environ['SECRET_ARN'])
    return response['SecretString']


def generate_signature(secret: str, timestamp: str, payload: dict) -> str:
    """Generate HMAC-SHA256 signature for DevOps Agent webhook"""
    message = f"{timestamp}:{json.dumps(payload)}"
    hmac_obj = hmac.new(
        secret.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    )
    return base64.b64encode(hmac_obj.digest()).decode('utf-8')


def send_to_devops_agent(payload: dict):
    """Send incident event to DevOps Agent webhook"""
    webhook_url = os.environ['WEBHOOK_URL']
    secret = get_secret()
    timestamp = datetime.utcnow().isoformat() + 'Z'

    signature = generate_signature(secret, timestamp, payload)

    headers = {
        'Content-Type': 'application/json',
        'x-amzn-event-timestamp': timestamp,
        'x-amzn-event-signature': signature
    }

    req = request.Request(
        webhook_url,
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST'
    )

    try:
        with request.urlopen(req) as response:
            return response.status, response.read().decode('utf-8')
    except error.HTTPError as e:
        logger.error(f"HTTP Error: {e.code} - {e.read().decode('utf-8')}")
        raise


def map_alarm_to_priority(alarm_name: str, alarm_description: str) -> str:
    """Map CloudWatch alarm to DevOps Agent priority based on alarm metadata."""
    text = f"{alarm_name} {alarm_description}".lower()
    if any(w in text for w in ('critical', 'database', 'connection', 'crash')):
        return 'CRITICAL'
    elif any(w in text for w in ('error', '5xx', 'failure', 'dns')):
        return 'HIGH'
    elif any(w in text for w in ('latency', 'degraded', 'slow')):
        return 'MEDIUM'
    return 'HIGH'


def handler(event, context):
    """
    Triggered by SNS when CloudWatch alarm fires.
    Sends a generic incident event to DevOps Agent webhook.
    
    The payload intentionally does NOT prescribe what to investigate.
    It forwards the CloudWatch alarm data and lets the DevOps Agent
    autonomously determine the root cause.
    """
    logger.info(f"Received event: {json.dumps(event)}")

    cluster_name = os.environ['EKS_CLUSTER_NAME']
    region = os.environ['AWS_REGION_NAME']

    for record in event.get('Records', []):
        sns_message = record.get('Sns', {})
        message = sns_message.get('Message', '')

        try:
            alarm_data = json.loads(message)
            alarm_name = alarm_data.get('AlarmName', 'Unknown')
            alarm_description = alarm_data.get('AlarmDescription', '')
            new_state = alarm_data.get('NewStateValue', 'ALARM')
            reason = alarm_data.get('NewStateReason', '')
            state_change_time = alarm_data.get('StateChangeTime', datetime.utcnow().isoformat())
            alarm_arn = alarm_data.get('AlarmArn', '')
            trigger = alarm_data.get('Trigger', {})
        except json.JSONDecodeError:
            alarm_name = 'CloudWatch Alarm'
            alarm_description = message
            new_state = 'ALARM'
            reason = message
            state_change_time = datetime.utcnow().isoformat()
            alarm_arn = ''
            trigger = {}

        # Only trigger on ALARM state (not OK)
        if new_state != 'ALARM':
            logger.info(f"Skipping non-ALARM state: {new_state}")
            continue

        # Build a generic incident payload — let the agent figure out the rest
        incident_payload = {
            'eventType': 'incident',
            'incidentId': f"{alarm_name}-{context.aws_request_id}",
            'action': 'created',
            'priority': map_alarm_to_priority(alarm_name, alarm_description),
            'title': f"CloudWatch Alarm: {alarm_name}",
            'description': (
                f"CloudWatch alarm '{alarm_name}' triggered in {region}.\n\n"
                f"Description: {alarm_description}\n"
                f"Reason: {reason}\n\n"
                f"EKS Cluster: {cluster_name}\n"
                f"Region: {region}"
            ),
            'timestamp': state_change_time,
            'service': 'cloudwatch',
            'data': {
                'alarmName': alarm_name,
                'alarmArn': alarm_arn,
                'alarmDescription': alarm_description,
                'newStateValue': new_state,
                'newStateReason': reason,
                'trigger': {
                    'metricName': trigger.get('MetricName', ''),
                    'namespace': trigger.get('Namespace', ''),
                    'statistic': trigger.get('Statistic', ''),
                    'period': trigger.get('Period', 0),
                    'threshold': trigger.get('Threshold', 0),
                },
                'clusterName': cluster_name,
                'namespace': 'payment-demo',
                'region': region,
            }
        }

        logger.info(f"Sending incident to DevOps Agent: {json.dumps(incident_payload)}")

        status, response = send_to_devops_agent(incident_payload)
        logger.info(f"DevOps Agent response: {status} - {response}")

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Incident sent to DevOps Agent',
                'incidentId': incident_payload['incidentId'],
                'webhookStatus': status
            })
        }

    return {
        'statusCode': 200,
        'body': json.dumps({'message': 'No ALARM records to process'})
    }
