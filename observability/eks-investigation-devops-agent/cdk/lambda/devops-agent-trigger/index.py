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


def map_alarm_to_priority(alarm_name: str, state_reason: str) -> str:
    """Map CloudWatch alarm to DevOps Agent priority"""
    if 'database' in alarm_name.lower() or 'connection' in alarm_name.lower():
        return 'CRITICAL'
    elif 'error' in alarm_name.lower() or '5xx' in alarm_name.lower():
        return 'CRITICAL'
    elif 'latency' in alarm_name.lower():
        return 'HIGH'
    return 'HIGH'


def handler(event, context):
    """
    Triggered by SNS when CloudWatch alarm fires.
    Sends incident event to DevOps Agent webhook.
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
        except json.JSONDecodeError:
            alarm_name = 'CloudWatch Alarm'
            alarm_description = message
            new_state = 'ALARM'
            reason = message
            state_change_time = datetime.utcnow().isoformat()

        # Only trigger on ALARM state (not OK)
        if new_state != 'ALARM':
            logger.info(f"Skipping non-ALARM state: {new_state}")
            continue

        # Build DevOps Agent incident payload
        incident_payload = {
            'eventType': 'incident',
            'incidentId': f"{alarm_name}-{context.aws_request_id}",
            'action': 'created',
            'priority': map_alarm_to_priority(alarm_name, reason),
            'title': f"CRITICAL: {alarm_name}",
            'description': f"""Database connection error detected on e-commerce payment platform.

IMPACT: Payment processing is failing. Customers cannot complete purchases.

Cluster: {cluster_name}
Region: {region}
Namespace: payment-demo
Alarm: {alarm_name}
Reason: {reason}

Please investigate:
1. Check payment-processor pods for database connection errors
2. Verify RDS instance status and connectivity
3. Check database credentials and secrets
4. Review application logs for connection pool exhaustion
5. Verify security group and network connectivity to RDS""",
            'timestamp': state_change_time,
            'service': 'payment-processor',
            'data': {
                'alarmName': alarm_name,
                'alarmDescription': alarm_description,
                'newStateValue': new_state,
                'newStateReason': reason,
                'clusterName': cluster_name,
                'namespace': 'payment-demo',
                'region': region,
                'impact': 'Payment processing unavailable'
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
