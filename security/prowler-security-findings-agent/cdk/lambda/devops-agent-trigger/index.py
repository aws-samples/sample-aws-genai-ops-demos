"""
DevOps Agent trigger Lambda.

Subscribed to the ingest pipeline's SNS topic. For each CRITICAL/HIGH Prowler
finding it builds a DevOps Agent incident payload, signs it with HMAC-SHA256,
and POSTs it to the customer-provided webhook URL.

The payload includes the Nova-generated remediation markdown (if the ingest
pipeline has already produced one and uploaded it to the remediations bucket),
so the agent starts the investigation with a ready-made remediation proposal.

Adapted from
observability/eks-investigation-devops-agent/cdk/lambda/devops-agent-trigger/index.py
-- HMAC signing and request flow are identical; only the payload shape and the
severity-to-priority mapping differ.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
from datetime import datetime
from urllib import error, request

import boto3

from cost_events import log_cost_event

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_secrets = boto3.client('secretsmanager')
_s3 = boto3.client('s3')


def get_secret() -> str:
    response = _secrets.get_secret_value(SecretId=os.environ['SECRET_ARN'])
    return response['SecretString']


def generate_signature(secret: str, timestamp: str, payload: dict) -> str:
    message = f"{timestamp}:{json.dumps(payload)}"
    mac = hmac.new(secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode('utf-8')


def send_to_devops_agent(payload: dict) -> tuple[int, str]:
    webhook_url = os.environ['WEBHOOK_URL']
    # Defense-in-depth: the webhook URL is captured at deploy time from the
    # DevOps Agent console and stored in a Lambda env var. It should always be
    # HTTPS — reject anything else to keep urllib from ever opening a file://
    # or http:// URL even if someone misconfigures the stack.
    if not webhook_url.startswith('https://'):
        raise RuntimeError(f'WEBHOOK_URL must be HTTPS, got: {webhook_url!r}')
    secret = get_secret()
    timestamp = datetime.utcnow().isoformat() + 'Z'
    signature = generate_signature(secret, timestamp, payload)

    headers = {
        'Content-Type': 'application/json',
        'x-amzn-event-timestamp': timestamp,
        'x-amzn-event-signature': signature,
    }
    req = request.Request(
        webhook_url,
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with request.urlopen(req) as response:  # nosec B310 - scheme validated above
            return response.status, response.read().decode('utf-8')
    except error.HTTPError as e:
        logger.error('HTTP Error: %s - %s', e.code, e.read().decode('utf-8'))
        raise


def map_finding_to_priority(severity: str) -> str:
    s = (severity or '').upper()
    if s == 'CRITICAL':
        return 'CRITICAL'
    if s == 'HIGH':
        return 'HIGH'
    if s == 'MEDIUM':
        return 'MEDIUM'
    return 'LOW'


def load_remediation_markdown(key: str | None) -> str:
    if not key:
        return ''
    bucket = os.environ.get('REMEDIATIONS_BUCKET', '')
    if not bucket:
        return ''
    try:
        obj = _s3.get_object(Bucket=bucket, Key=key)
        body = obj['Body'].read().decode('utf-8')
        # Clip to keep the webhook payload reasonable (~20 KB of markdown).
        return body[:20000]
    except Exception as exc:  # noqa: BLE001 — log and continue; remediation is best-effort
        logger.warning('Could not load remediation markdown s3://%s/%s: %s', bucket, key, exc)
        return ''


def handler(event, context):
    logger.info('Received event: %s', json.dumps(event))

    webhook_url = os.environ.get('WEBHOOK_URL', '')
    if not webhook_url or webhook_url == 'NOT_CONFIGURED':
        logger.warning('WEBHOOK_URL not configured; skipping DevOps Agent dispatch')
        return {'statusCode': 200, 'body': json.dumps({'skipped': 'webhook_not_configured'})}

    account_id = os.environ.get('AWS_ACCOUNT_ID', '')
    region = os.environ.get('AWS_REGION_NAME', '')
    devops_region = os.environ.get('DEVOPS_AGENT_REGION', '')
    space_id = os.environ.get('DEVOPS_AGENT_SPACE_ID', '')

    results = []
    for record in event.get('Records', []):
        sns_message = record.get('Sns', {})
        raw = sns_message.get('Message', '')
        try:
            finding = json.loads(raw)
        except json.JSONDecodeError:
            logger.error('SNS message was not JSON: %s', raw)
            continue

        severity = finding.get('severity', 'LOW')
        check_id = finding.get('check_id', 'unknown')
        resource_uid = finding.get('resource_uid', 'unknown')
        title = finding.get('check_title') or f'Prowler finding {check_id}'
        remediation_key = finding.get('remediation_s3_key')
        remediation_md = load_remediation_markdown(remediation_key)

        # Trim long free-text fields so the final payload stays small.
        # The DevOps Agent generic webhook silently drops incidents whose
        # payload is above a few KB — it still returns 200 OK but the backlog
        # task is never created. Observed threshold: ~8-10 KB total payload.
        check_description = (finding.get('check_description') or '')[:500]
        status_extended = (finding.get('status_extended') or '')[:500]
        compliance = ', '.join((finding.get('compliance_frameworks') or [])[:6])

        description_parts = [
            f"A Prowler finding with severity {severity} was detected in AWS account {account_id} ({region}).",
            '',
            f"Check ID: {check_id}",
            f"Resource: {resource_uid}",
            f"Service: {finding.get('service_name', 'unknown')}",
            f"Compliance: {compliance}",
            '',
            f"Finding description: {check_description}",
            f"Extended info: {status_extended}",
        ]
        # Remediation markdown is the biggest contributor to payload size.
        # Cap it aggressively here (the agent only needs a summary; the full
        # playbook is still available in the dashboard via Bedrock Insights).
        if remediation_md:
            description_parts.extend([
                '',
                '---',
                'Bedrock Insights summary (truncated — full playbook available in the dashboard):',
                '',
                remediation_md[:2000],
            ])

        # DevOps Agent's generic webhook rejects incidentIds with ':' or '/'
        # (the backlog task create step silently drops them even though the
        # HTTP call returns 200). Sanitize to alphanumerics + dashes; we still
        # carry the original finding_uid in data.findingUid for matching.
        safe_uid = re.sub(r'[^A-Za-z0-9-]', '-', finding.get('finding_uid') or 'unknown')[:200]
        incident_payload = {
            'eventType': 'incident',
            'incidentId': f"prowler-{safe_uid}",
            'action': 'created',
            'priority': map_finding_to_priority(severity),
            'title': f"Prowler finding: {title}",
            'description': '\n'.join(description_parts),
            'timestamp': finding.get('last_seen_at') or datetime.utcnow().isoformat() + 'Z',
            'service': 'prowler',
            'data': {
                'findingUid': (finding.get('finding_uid') or '')[:500],
                'checkId': check_id,
                'severity': severity,
                'service': finding.get('service_name'),
                'resourceUid': resource_uid,
                'region': finding.get('region') or region,
                'accountId': account_id,
                'complianceFrameworks': (finding.get('compliance_frameworks') or [])[:6],
                'remediationS3Key': remediation_key,
                'devOpsAgentRegion': devops_region,
                'devOpsAgentSpaceId': space_id,
            },
        }

        logger.info('Sending incident to DevOps Agent: %s', incident_payload['incidentId'])
        status, body = send_to_devops_agent(incident_payload)
        logger.info('DevOps Agent response: %s - %s', status, body)
        results.append({'incidentId': incident_payload['incidentId'], 'webhookStatus': status})

        # Best-effort cost log. Only count successful dispatches (2xx).
        if 200 <= status < 300:
            log_cost_event(
                'devops_agent_dispatch',
                finding_uid=finding.get('finding_uid'),
                metadata={
                    'check_id': check_id,
                    'severity': severity,
                    'incident_id': incident_payload['incidentId'],
                },
            )

    return {'statusCode': 200, 'body': json.dumps({'dispatched': results})}
