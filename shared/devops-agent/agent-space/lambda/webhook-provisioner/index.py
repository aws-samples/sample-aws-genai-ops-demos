"""
CloudFormation custom resource: the eventChannel webhook of a DevOps Agent Agent Space.

Why a custom resource is unavoidable:
- AWS::DevOpsAgent::Service cannot register the eventChannel service type.
- AWS::DevOpsAgent::Association exposes no webhook URL or HMAC secret.
- AssociateService returns the secret exactly once, in its create response.

Create/Update: RegisterService(eventChannel) if the account has none yet, AssociateService,
write the secret straight into the Secrets Manager secret the construct created, return only
the webhook URL. The secret value never enters CloudFormation state, events or outputs.
PhysicalResourceId is the association id, so a replacement (any property change) makes
CloudFormation call Delete on the previous association.

The Lambda runtime's boto3 may not carry the agent's service model yet, so the control-plane
calls (cp.aidevops.<region>.api.aws, rest-json, signing name aidevops) are SigV4-signed by
hand, like the data-plane calls in lab/lambda/devops_agent.py. No dependency to bundle.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ['AWS_REGION']
# Control plane: cp.aidevops.<region>.api.aws (the data plane is dp.aidevops..., see devops_agent.py).
ENDPOINT = os.environ.get('DEVOPS_AGENT_ENDPOINT') or f'https://cp.aidevops.{REGION}.api.aws'
SERVICE_TYPE = 'eventChannel'
NO_ASSOCIATION = ('failed', 'deleted', 'unknown')


class ApiError(Exception):
    def __init__(self, status: int, error_type: str, body: str):
        super().__init__(f'{status} {error_type}: {body[:300]}')
        self.status = status
        self.error_type = error_type

    @property
    def not_found(self) -> bool:
        return self.status == 404 or 'NotFound' in self.error_type


class ProvisioningError(Exception):
    """Compensation failed: carries the live association id so a rollback Delete can retry."""

    def __init__(self, message: str, association_id: str):
        super().__init__(message)
        self.association_id = association_id


def _call(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    url = ENDPOINT + path
    data = json.dumps(body).encode('utf-8') if body is not None else None
    request = AWSRequest(method=method, url=url, data=data,
                         headers={'Content-Type': 'application/json'} if data else {})
    credentials = boto3.Session().get_credentials().get_frozen_credentials()
    SigV4Auth(credentials, 'aidevops', REGION).add_auth(request)
    req = urllib.request.Request(url, data=data, headers=dict(request.headers), method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        error_type = e.headers.get('x-amzn-ErrorType', '')
        try:
            error_type = error_type or json.loads(raw).get('__type', '')
        except ValueError:
            pass
        raise ApiError(e.code, error_type, raw) from None


def ensure_event_channel_service() -> str:
    """The eventChannel registration is account-level and may already exist (another demo)."""
    try:
        registered = _call('POST', f'/v1/register/{SERVICE_TYPE}', {'serviceDetails': {SERVICE_TYPE: {}}})
        if registered.get('serviceId'):
            logger.info('Registered eventChannel service %s', registered['serviceId'])
            return registered['serviceId']
    except ApiError as e:
        logger.info('RegisterService failed (%s); looking for an existing registration', e)
    next_token = None
    while True:
        page = _call('POST', '/v1/services/list',
                     {'filterServiceType': SERVICE_TYPE, **({'nextToken': next_token} if next_token else {})})
        for service in page.get('services', []):
            if service.get('serviceType') == SERVICE_TYPE and service.get('serviceId'):
                logger.info('Reusing eventChannel service %s', service['serviceId'])
                return service['serviceId']
        next_token = page.get('nextToken')
        if not next_token:
            raise RuntimeError('Could not register or locate the account-level eventChannel service')


def disassociate(agent_space_id: str, association_id: str) -> None:
    _call('DELETE', f'/v1/agentspaces/{agent_space_id}/associations/{association_id}')


def create_webhook(agent_space_id: str, secret_arn: str) -> Dict[str, str]:
    service_id = ensure_event_channel_service()
    # The create response is the one and only carrier of webhookSecret.
    response = _call('POST', f'/v1/agentspaces/{agent_space_id}/associations',
                     {'serviceId': service_id, 'configuration': {SERVICE_TYPE: {}}})
    association_id = (response.get('association') or {}).get('associationId')
    webhook = response.get('webhook') or {}
    if not association_id:
        listed = _call('POST', f'/v1/agentspaces/{agent_space_id}/associations/list', {})
        association_id = next((a.get('associationId') for a in listed.get('associations', [])
                               if a.get('serviceId') == service_id), None)
    if not association_id:
        raise RuntimeError('AssociateService succeeded but no association id could be recovered')
    try:
        if not webhook.get('webhookUrl') or not webhook.get('webhookSecret'):
            raise RuntimeError('AssociateService did not return complete webhook credentials '
                               f"(url={'present' if webhook.get('webhookUrl') else 'MISSING'}, "
                               f"secret={'present' if webhook.get('webhookSecret') else 'MISSING'})")
        # Persist before anything goes back to CloudFormation.
        boto3.client('secretsmanager').put_secret_value(SecretId=secret_arn, SecretString=webhook['webhookSecret'])
    except Exception as e:
        # A live webhook exists whose secret can never be read again: compensate now.
        try:
            disassociate(agent_space_id, association_id)
            logger.info('Compensated partial webhook creation %s', association_id)
        except Exception as cleanup_error:
            raise ProvisioningError(f'Webhook setup failed ({e}) and compensating disassociation '
                                    f'also failed: {cleanup_error}', association_id) from e
        raise
    logger.info('Webhook created; secret stored (association %s)', association_id)
    return {'associationId': association_id, 'webhookUrl': webhook['webhookUrl']}


def delete_webhook(agent_space_id: Optional[str], association_id: Optional[str]) -> None:
    if not agent_space_id or not association_id or association_id in NO_ASSOCIATION:
        logger.info('Nothing to delete (%s, %s)', agent_space_id, association_id)
        return
    try:
        disassociate(agent_space_id, association_id)
        logger.info('Webhook association %s deleted', association_id)
    except ApiError as e:
        if e.not_found:
            logger.info('Webhook association %s already absent', association_id)
            return
        raise  # fail closed: CloudFormation keeps ownership and can retry


def respond(event: Dict[str, Any], context: Any, status: str, physical_id: str,
            data: Optional[Dict[str, str]] = None, reason: Optional[str] = None) -> None:
    body = json.dumps({
        'Status': status,
        'Reason': reason or f'See CloudWatch log stream {context.log_stream_name}',
        'PhysicalResourceId': physical_id,
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'Data': data or {},
    }).encode('utf-8')
    req = urllib.request.Request(event['ResponseURL'], data=body, method='PUT',
                                 headers={'Content-Type': '', 'Content-Length': str(len(body))})
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status >= 300:
            raise RuntimeError(f'CloudFormation response upload failed: {resp.status}')


def handler(event: Dict[str, Any], context: Any) -> None:
    # ResponseURL is a bearer URL: never log it.
    logger.info('Request %s', json.dumps({k: v for k, v in event.items() if k != 'ResponseURL'}))
    props = event.get('ResourceProperties', {})
    physical_id = event.get('PhysicalResourceId') or 'failed'
    try:
        if event['RequestType'] in ('Create', 'Update'):
            result = create_webhook(props['AgentSpaceId'], props['SecretArn'])
            respond(event, context, 'SUCCESS', result['associationId'], {'WebhookUrl': result['webhookUrl']})
            return
        delete_webhook(props.get('AgentSpaceId'), event.get('PhysicalResourceId'))
        respond(event, context, 'SUCCESS', event.get('PhysicalResourceId') or 'deleted')
    except Exception as e:
        if isinstance(e, ProvisioningError):
            physical_id = e.association_id
        logger.exception('Webhook provisioning failed')
        respond(event, context, 'FAILED', physical_id, reason=f'{type(e).__name__}: {e}')
