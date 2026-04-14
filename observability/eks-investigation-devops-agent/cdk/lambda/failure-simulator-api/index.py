"""
Admin API Lambda — Failure Simulator for EKS DevOps Agent Demo.

Uses kubectl (from Lambda layer) to inject/rollback/status failures on EKS.
No pip dependencies — just stdlib + boto3 + kubectl binary from layer.
"""

import base64
import json
import logging
import os
import subprocess
from datetime import datetime, timedelta
from typing import Any, Dict

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients
eks_client = boto3.client('eks')
cloudwatch_client = boto3.client('cloudwatch')

# Environment variables
EKS_CLUSTER_NAME = os.environ.get('EKS_CLUSTER_NAME', '')
NAMESPACE = os.environ.get('K8S_NAMESPACE', 'payment-demo')
DEPLOYMENT_NAME = os.environ.get('DEPLOYMENT_NAME', 'payment-processor')
ALARM_NAME = os.environ.get('ALARM_NAME', '')
DNS_ALARM_NAME = os.environ.get('DNS_ALARM_NAME', '')
REGION = os.environ.get('AWS_REGION', os.environ.get('AWS_REGION_NAME', 'us-east-1'))

# Persistent state via DynamoDB (survives cold starts, shared across invocations)
STATE_TABLE_NAME = os.environ.get('STATE_TABLE_NAME', '')
AUTO_REVERT_SECONDS = 10 * 60  # 10 minutes

dynamodb_resource = boto3.resource('dynamodb')


def _load_state() -> Dict[str, Any]:
    """Load scenario state from DynamoDB."""
    if not STATE_TABLE_NAME:
        return {}
    try:
        table = dynamodb_resource.Table(STATE_TABLE_NAME)
        response = table.get_item(Key={'pk': 'scenarios'})
        return response.get('Item', {}).get('state', {})
    except Exception as e:
        logger.warning('Failed to load state: %s', e)
        return {}


def _save_state(state: Dict[str, Any]):
    """Save scenario state to DynamoDB."""
    if not STATE_TABLE_NAME:
        return
    try:
        table = dynamodb_resource.Table(STATE_TABLE_NAME)
        table.put_item(Item={'pk': 'scenarios', 'state': state})
    except Exception as e:
        logger.warning('Failed to save state: %s', e)


def _check_and_auto_revert():
    """Check for expired scenarios and auto-revert them."""
    state = _load_state()
    now = datetime.utcnow()
    reverted = []

    for scenario_id, info in list(state.items()):
        expires_at = info.get('expiresAt')
        if not expires_at:
            continue
        try:
            expiry = datetime.fromisoformat(expires_at.replace('Z', ''))
            if now > expiry:
                logger.info('Auto-reverting expired scenario: %s', scenario_id)
                if scenario_id == 'db-connection-failure':
                    rollback_db_failure()
                elif scenario_id == 'dns-resolution-failure':
                    rollback_dns_failure()
                del state[scenario_id]
                reverted.append(scenario_id)
        except (ValueError, TypeError) as e:
            logger.warning('Failed to parse expiresAt for %s: %s', scenario_id, e)

    if reverted:
        _save_state(state)
        logger.info('Auto-reverted scenarios: %s', reverted)

    return reverted

# Binary paths from the kubectl Lambda layer (@aws-cdk/lambda-layer-kubectl-v31).
# This layer provides kubectl and helm — do NOT install these tools separately.
# See failure-simulator-api-stack.ts for the layer configuration.
# NOTE: aws CLI is NOT included in this layer. Use boto3 for AWS API calls.
KUBECTL = '/opt/kubectl/kubectl'

# Cache cluster info (endpoint + CA) across invocations — these don't change.
# Token is regenerated every invocation since it expires after 60s.
_cluster_endpoint = None
_cluster_ca_data = None


def _get_eks_token(cluster_name: str) -> str:
    """Generate EKS bearer token using STS presigned URL (same as aws eks get-token)."""
    from botocore.signers import RequestSigner

    session = boto3.Session()
    sts = session.client('sts', region_name=REGION)

    signer = RequestSigner(
        service_id=sts.meta.service_model.service_id,
        region_name=REGION,
        signing_name='sts',
        signature_version='v4',
        credentials=session.get_credentials(),
        event_emitter=session.events,
    )

    params = {
        'method': 'GET',
        'url': f'https://sts.{REGION}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15',
        'body': {},
        'headers': {'x-k8s-aws-id': cluster_name},
        'context': {},
    }

    signed_url = signer.generate_presigned_url(
        params, region_name=REGION, expires_in=60, operation_name='',
    )

    return 'k8s-aws-v1.' + base64.urlsafe_b64encode(
        signed_url.encode('utf-8')
    ).decode('utf-8').rstrip('=')


def _setup_kubeconfig():
    """Build kubeconfig with a fresh EKS token. Cluster info is cached, token is not."""
    global _cluster_endpoint, _cluster_ca_data

    # Fetch cluster info once (cold start only)
    if _cluster_endpoint is None:
        cluster_info = eks_client.describe_cluster(name=EKS_CLUSTER_NAME)
        cluster = cluster_info['cluster']
        _cluster_endpoint = cluster['endpoint']
        _cluster_ca_data = cluster['certificateAuthority']['data']

    # Always generate a fresh token (expires after 60s)
    token = _get_eks_token(EKS_CLUSTER_NAME)

    kubeconfig = {
        'apiVersion': 'v1',
        'kind': 'Config',
        'clusters': [{'name': 'eks', 'cluster': {
            'server': _cluster_endpoint,
            'certificate-authority-data': _cluster_ca_data,
        }}],
        'contexts': [{'name': 'eks', 'context': {'cluster': 'eks', 'user': 'eks'}}],
        'current-context': 'eks',
        'users': [{'name': 'eks', 'user': {'token': token}}],
    }

    with open('/tmp/kubeconfig', 'w') as f:
        json.dump(kubeconfig, f)

    os.environ['KUBECONFIG'] = '/tmp/kubeconfig'


def _kubectl(args: list, timeout: int = 30) -> Dict[str, Any]:
    """Run a kubectl command and return stdout/stderr/returncode."""
    _setup_kubeconfig()
    cmd = [KUBECTL] + args
    logger.info('Running: %s', ' '.join(cmd))

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, 'KUBECONFIG': '/tmp/kubeconfig'},
    )
    return {
        'stdout': result.stdout,
        'stderr': result.stderr,
        'returncode': result.returncode,
    }


# ============================================================================
# K8s Operations — inject/rollback failures via kubectl
# ============================================================================

def inject_db_failure() -> Dict[str, Any]:
    """
    Inject database connection failure.
    1. kubectl set env — wrong DB_PASSWORD
    2. kubectl scale --replicas=0
    3. kubectl scale --replicas=1
    """
    import time

    # Step 1: Set wrong password
    r = _kubectl(['set', 'env', f'deployment/{DEPLOYMENT_NAME}',
                  'DB_PASSWORD=wrong-password', '-n', NAMESPACE])
    if r['returncode'] != 0:
        return {'success': False, 'message': f"Failed to set env: {r['stderr']}"}
    logger.info('Set wrong DB_PASSWORD')

    # Step 2: Scale to 0
    r = _kubectl(['scale', f'deployment/{DEPLOYMENT_NAME}',
                  '--replicas=0', '-n', NAMESPACE])
    if r['returncode'] != 0:
        return {'success': False, 'message': f"Failed to scale down: {r['stderr']}"}
    logger.info('Scaled to 0')

    time.sleep(3)

    # Step 3: Scale to 1
    r = _kubectl(['scale', f'deployment/{DEPLOYMENT_NAME}',
                  '--replicas=1', '-n', NAMESPACE])
    if r['returncode'] != 0:
        return {'success': False, 'message': f"Failed to scale up: {r['stderr']}"}
    logger.info('Scaled to 1 with broken config')

    # Save state for auto-revert timer
    state = _load_state()
    now = datetime.utcnow()
    state['db-connection-failure'] = {
        'injectedAt': now.isoformat() + 'Z',
        'expiresAt': (now + timedelta(seconds=AUTO_REVERT_SECONDS)).isoformat() + 'Z',
    }
    _save_state(state)

    return {
        'success': True,
        'message': 'Database connection failure injected. Payment processor will CrashLoopBackOff. Auto-reverts in 10 minutes.',
        'scenario': 'db-connection-failure',
    }


def rollback_db_failure() -> Dict[str, Any]:
    """
    Rollback database connection failure.
    Patches deployment to restore DB_PASSWORD from secretKeyRef.
    """
    patch_json = json.dumps([{
        'op': 'replace',
        'path': '/spec/template/spec/containers/0/env/4',
        'value': {
            'name': 'DB_PASSWORD',
            'valueFrom': {
                'secretKeyRef': {
                    'name': 'db-credentials',
                    'key': 'DB_PASSWORD',
                }
            }
        }
    }])

    r = _kubectl(['patch', f'deployment/{DEPLOYMENT_NAME}', '-n', NAMESPACE,
                  '--type=json', f'-p={patch_json}'])
    if r['returncode'] != 0:
        return {'success': False, 'message': f"Failed to patch: {r['stderr']}"}

    logger.info('Restored DB_PASSWORD from secret')

    # Clear state
    state = _load_state()
    state.pop('db-connection-failure', None)
    _save_state(state)

    return {
        'success': True,
        'message': 'Database credentials restored. Payment processor recovering.',
        'scenario': 'db-connection-failure',
    }


def get_status() -> Dict[str, Any]:
    """Get current status with per-scenario injection state."""
    result: Dict[str, Any] = {
        'success': True,
        'region': REGION,
        'clusterName': EKS_CLUSTER_NAME,
        'namespace': NAMESPACE,
        'triggerLambdaName': f'{EKS_CLUSTER_NAME.replace("-cluster", "")}-devops-trigger',
        'devOpsAgentRegion': os.environ.get('DEVOPS_AGENT_REGION', 'us-east-1'),
        'devOpsAgentSpaceId': os.environ.get('DEVOPS_AGENT_SPACE_ID', ''),
    }

    scenarios: Dict[str, Any] = {}

    # --- DB Connection Failure: check DB_PASSWORD env var ---
    r = _kubectl(['get', f'deployment/{DEPLOYMENT_NAME}', '-n', NAMESPACE, '-o', 'json'])
    if r['returncode'] == 0:
        dep = json.loads(r['stdout'])
        spec = dep.get('spec', {})
        status_obj = dep.get('status', {})
        result['deployment'] = {
            'name': DEPLOYMENT_NAME,
            'replicas': spec.get('replicas', 0),
            'readyReplicas': status_obj.get('readyReplicas', 0),
            'availableReplicas': status_obj.get('availableReplicas', 0),
        }

        db_injected = False
        containers = spec.get('template', {}).get('spec', {}).get('containers', [])
        if containers:
            for env in containers[0].get('env', []):
                if env.get('name') == 'DB_PASSWORD':
                    db_injected = env.get('value') == 'wrong-password'
                    break
        scenarios['db-connection-failure'] = {'injected': db_injected}
    else:
        result['deployment'] = None
        scenarios['db-connection-failure'] = {'injected': False}

    # --- DNS Resolution Failure: check CoreDNS replica count ---
    r = _kubectl(['get', 'deployment/coredns', '-n', 'kube-system', '-o', 'json'])
    if r['returncode'] == 0:
        coredns = json.loads(r['stdout'])
        coredns_replicas = coredns.get('spec', {}).get('replicas', 2)
        scenarios['dns-resolution-failure'] = {'injected': coredns_replicas == 0}
    else:
        scenarios['dns-resolution-failure'] = {'injected': False}

    # --- DNS: CoreDNS pods ---
    r = _kubectl(['get', 'pods', '-n', 'kube-system', '-l', 'k8s-app=kube-dns', '-o', 'json'])
    dns_pods = []
    if r['returncode'] == 0:
        pods_json = json.loads(r['stdout'])
        for pod in pods_json.get('items', []):
            pod_status = pod.get('status', {}).get('phase', 'Unknown')
            ready = all(cs.get('ready', False) for cs in pod.get('status', {}).get('containerStatuses', []))
            dns_pods.append({
                'name': pod['metadata']['name'],
                'status': pod_status,
                'ready': ready,
                'restarts': sum(cs.get('restartCount', 0) for cs in pod.get('status', {}).get('containerStatuses', [])),
            })
    scenarios['dns-resolution-failure']['pods'] = dns_pods

    # Enrich scenarios with remaining time from persistent state
    state = _load_state()
    now = datetime.utcnow()
    for scenario_id, scenario_data in scenarios.items():
        if scenario_id in state and state[scenario_id].get('expiresAt'):
            try:
                expiry = datetime.fromisoformat(state[scenario_id]['expiresAt'].replace('Z', ''))
                remaining = max(0, int((expiry - now).total_seconds()))
                scenario_data['remainingSeconds'] = remaining
                scenario_data['expiresAt'] = state[scenario_id]['expiresAt']
            except (ValueError, TypeError):
                pass

    result['scenarios'] = scenarios
    # Legacy field for backward compatibility
    result['injected'] = scenarios.get('db-connection-failure', {}).get('injected', False)

    # --- Pods (payment-processor) ---
    r = _kubectl(['get', 'pods', '-n', NAMESPACE,
                  '-l', f'app.kubernetes.io/name={DEPLOYMENT_NAME}',
                  '-o', 'json'])
    if r['returncode'] == 0:
        pods_json = json.loads(r['stdout'])
        pod_list = []
        for pod in pods_json.get('items', []):
            pod_status = pod.get('status', {}).get('phase', 'Unknown')
            restarts = 0
            ready = True
            for cs in pod.get('status', {}).get('containerStatuses', []):
                restarts += cs.get('restartCount', 0)
                if not cs.get('ready', False):
                    ready = False
                waiting = cs.get('state', {}).get('waiting', {})
                if waiting.get('reason'):
                    pod_status = waiting['reason']
            pod_list.append({
                'name': pod['metadata']['name'],
                'status': pod_status,
                'ready': ready,
                'restarts': restarts,
            })
        result['pods'] = pod_list
    else:
        result['pods'] = []
        result['k8sError'] = r['stderr']

    # --- CloudWatch Alarms (per-scenario) ---
    try:
        alarm_names = [n for n in [ALARM_NAME, DNS_ALARM_NAME] if n]
        if alarm_names:
            alarms_resp = cloudwatch_client.describe_alarms(AlarmNames=alarm_names)
            alarms_by_name = {}
            for a in alarms_resp.get('MetricAlarms', []):
                alarms_by_name[a['AlarmName']] = {
                    'name': a['AlarmName'],
                    'state': a['StateValue'],
                    'reason': a.get('StateReason', ''),
                }
            # Assign to scenarios
            if ALARM_NAME:
                scenarios['db-connection-failure']['alarm'] = alarms_by_name.get(ALARM_NAME, {'name': ALARM_NAME, 'state': 'NOT_FOUND'})
            if DNS_ALARM_NAME:
                scenarios['dns-resolution-failure']['alarm'] = alarms_by_name.get(DNS_ALARM_NAME, {'name': DNS_ALARM_NAME, 'state': 'NOT_FOUND'})
            # Legacy: first alarm for backward compat
            result['alarm'] = alarms_by_name.get(ALARM_NAME, {'state': 'NOT_CONFIGURED'})
        else:
            result['alarm'] = {'state': 'NOT_CONFIGURED'}
    except Exception as e:
        result['alarm'] = {'state': 'ERROR', 'error': str(e)}

    return result


# ============================================================================
# DNS Resolution Failure — scale down CoreDNS to break service discovery
# ============================================================================

def inject_dns_failure() -> Dict[str, Any]:
    """
    Inject DNS resolution failure by scaling CoreDNS to 0 replicas.
    Also publishes a custom CloudWatch metric to trigger the DNS alarm
    (since Fluent Bit can't ship logs when DNS is down).
    """
    r = _kubectl(['scale', 'deployment/coredns', '--replicas=0', '-n', 'kube-system'])
    if r['returncode'] != 0:
        return {'success': False, 'message': f"Failed to scale CoreDNS: {r['stderr']}"}
    logger.info('Scaled CoreDNS to 0')

    # Publish custom metric — Fluent Bit can't ship logs when DNS is broken,
    # so we signal the alarm directly via a custom CloudWatch metric.
    try:
        cloudwatch_client.put_metric_data(
            Namespace=os.environ.get('METRICS_NAMESPACE', 'devops-agent-eks/dev'),
            MetricData=[{
                'MetricName': 'DnsResolutionErrors',
                'Value': 1,
                'Unit': 'Count',
            }],
        )
        logger.info('Published DnsResolutionErrors metric')
    except Exception as e:
        logger.warning('Failed to publish metric: %s', e)

    # Save state for auto-revert timer
    state = _load_state()
    now = datetime.utcnow()
    state['dns-resolution-failure'] = {
        'injectedAt': now.isoformat() + 'Z',
        'expiresAt': (now + timedelta(seconds=AUTO_REVERT_SECONDS)).isoformat() + 'Z',
    }
    _save_state(state)

    return {
        'success': True,
        'message': 'DNS resolution failure injected. CoreDNS scaled to 0 — all service discovery will fail. Auto-reverts in 10 minutes.',
        'scenario': 'dns-resolution-failure',
    }


def rollback_dns_failure() -> Dict[str, Any]:
    """Restore CoreDNS by scaling back to 2 replicas."""
    r = _kubectl(['scale', 'deployment/coredns', '--replicas=2', '-n', 'kube-system'])
    if r['returncode'] != 0:
        return {'success': False, 'message': f"Failed to restore CoreDNS: {r['stderr']}"}
    logger.info('Restored CoreDNS to 2 replicas')

    # Clear state
    state = _load_state()
    state.pop('dns-resolution-failure', None)
    _save_state(state)

    return {
        'success': True,
        'message': 'CoreDNS restored to 2 replicas. DNS resolution recovering.',
        'scenario': 'dns-resolution-failure',
    }


# ============================================================================
# DevOps Agent API — cross-region SigV4-signed HTTP calls
# ============================================================================

def _devops_agent_api(method: str, path: str, body: str = None) -> Dict[str, Any]:
    """Make a SigV4-signed HTTP request to the DevOps Agent API.

    The Lambda runtime's boto3 doesn't include the devops-agent service model
    yet, so we make direct SigV4-signed requests.
    All DevOps Agent APIs use the dp. (data plane) prefix.
    """
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    import urllib.request

    devops_agent_region = os.environ.get('DEVOPS_AGENT_REGION', 'us-east-1')
    url = f'https://dp.aidevops.{devops_agent_region}.api.aws{path}'

    session = boto3.Session()
    credentials = session.get_credentials().get_frozen_credentials()

    headers = {}
    if body:
        headers['Content-Type'] = 'application/json'

    request = AWSRequest(method=method, url=url, headers=headers, data=body)
    SigV4Auth(credentials, 'aidevops', devops_agent_region).add_auth(request)

    req = urllib.request.Request(url, headers=dict(request.headers), method=method,
                                 data=body.encode('utf-8') if body else None)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode('utf-8'))


def get_usage() -> Dict[str, Any]:
    """Fetch DevOps Agent account usage."""
    try:
        data = _devops_agent_api('GET', '/usage/account')
        return {'success': True, **data}
    except Exception as e:
        logger.warning('Failed to get DevOps Agent usage: %s', e)
        return {'success': False, 'message': str(e)}


def get_logs() -> Dict[str, Any]:
    """Fetch recent investigations from DevOps Agent with execution details."""
    agent_space_id = os.environ.get('DEVOPS_AGENT_SPACE_ID', '')
    if not agent_space_id:
        return {'success': False, 'message': 'DEVOPS_AGENT_SPACE_ID not configured'}

    try:
        # List recent tasks (POST with body), sorted newest first
        # Fetch more than we need, then filter out LINKED tasks (no own execution)
        tasks_data = _devops_agent_api(
            'POST',
            f'/backlog/agent-space/{agent_space_id}/tasks/list',
            json.dumps({'limit': 20, 'sortField': 'CREATED_AT', 'order': 'DESC'})
        )
        tasks = [t for t in tasks_data.get('tasks', []) if t.get('status') != 'LINKED'][:10]

        logs = []
        for task in tasks:
            task_id = task.get('taskId', '')
            entry = {
                'taskId': task_id,
                'title': task.get('title', ''),
                'status': task.get('status', ''),
                'priority': task.get('priority', ''),
                'taskType': task.get('taskType', ''),
                'createdAt': task.get('createdAt', ''),
                'updatedAt': task.get('updatedAt', ''),
            }

            # Get executions
            try:
                exec_data = _devops_agent_api(
                    'POST',
                    f'/journal/agent-space/{agent_space_id}/executions',
                    json.dumps({'taskId': task_id, 'limit': 1})
                )
                executions = exec_data.get('executions', [])
                if executions:
                    exe = executions[0]
                    entry['executionId'] = exe.get('executionId', '')
                    entry['executionStatus'] = exe.get('executionStatus', '')
                    entry['agentType'] = exe.get('agentType', '')
                    entry['executionCreatedAt'] = exe.get('createdAt', '')
                    entry['executionUpdatedAt'] = exe.get('updatedAt', '')

                    # Get journal records for tool call count and summary
                    exec_id = exe.get('executionId', '')
                    if exec_id:
                        journal_data = _devops_agent_api(
                            'POST',
                            f'/journal/agent-space/{agent_space_id}/journalRecords',
                            json.dumps({'executionId': exec_id, 'limit': 100, 'order': 'DESC'})
                        )
                        records = journal_data.get('records', [])
                        tool_calls = 0
                        skill_reads = 0
                        skill_names = []
                        summary_md = ''
                        for rec in records:
                            rt = rec.get('recordType', '')
                            content = rec.get('content', '')
                            content_str = str(content)
                            if rt == 'message' and 'tool_use' in content_str:
                                tool_calls += 1
                                # GA loads skills via fs_read with /aidevops/skills/ path
                                if '/aidevops/skills/' in content_str:
                                    # Extract skill names from paths like /aidevops/skills/user/skill-name/SKILL.md
                                    import re
                                    for match in re.findall(r'/aidevops/skills/\w+/([^/]+)/SKILL\.md', content_str):
                                        if match not in skill_names:
                                            skill_names.append(match)
                            elif rt == 'investigation_summary_md' and not summary_md:
                                summary_md = content if isinstance(content, str) else ''
                        entry['toolCalls'] = tool_calls
                        entry['skillReads'] = len(skill_names)
                        entry['skillNames'] = skill_names
                        entry['summaryMd'] = summary_md[:3000] if summary_md else ''
                        entry['journalRecordCount'] = len(records)
            except Exception as e:
                logger.warning('Failed to get execution details for task %s: %s', task_id, e)

            logs.append(entry)

        return {'success': True, 'logs': logs}
    except Exception as e:
        logger.warning('Failed to get DevOps Agent logs: %s', e)
        return {'success': False, 'message': str(e)}


# ============================================================================
# Lambda Handler
# ============================================================================

def handler(event, context):
    """API Gateway proxy handler. Routes: /admin/{scenario}/inject, /admin/{scenario}/status."""
    logger.info('Event: %s', json.dumps(event))

    # Check for expired scenarios on every request (server-side auto-revert)
    try:
        _check_and_auto_revert()
    except Exception as e:
        logger.warning('Auto-revert check failed: %s', e)

    path = event.get('path', '')
    method = event.get('httpMethod', '')

    if method == 'OPTIONS':
        return _cors_response(200, '')

    try:
        # Legacy routes (backward compatible)
        if path == '/admin/inject' and method == 'POST':
            result = inject_db_failure()
        elif path == '/admin/inject' and method == 'DELETE':
            result = rollback_db_failure()
        elif path == '/admin/status' and method == 'GET':
            result = get_status()
        # Scenario-based routes: /admin/scenarios/{id}/inject
        elif path == '/admin/scenarios/db-connection-failure/inject' and method == 'POST':
            result = inject_db_failure()
        elif path == '/admin/scenarios/db-connection-failure/inject' and method == 'DELETE':
            result = rollback_db_failure()
        elif path == '/admin/scenarios/dns-resolution-failure/inject' and method == 'POST':
            result = inject_dns_failure()
        elif path == '/admin/scenarios/dns-resolution-failure/inject' and method == 'DELETE':
            result = rollback_dns_failure()
        # Usage route: /admin/usage
        elif path == '/admin/usage' and method == 'GET':
            result = get_usage()
        # Logs route: /admin/logs
        elif path == '/admin/logs' and method == 'GET':
            result = get_logs()
        else:
            return _cors_response(404, {'success': False, 'message': f'Unknown route: {method} {path}'})

        status_code = 200 if result.get('success') else 500
        return _cors_response(status_code, result)

    except Exception as e:
        logger.error('Unhandled error: %s', e, exc_info=True)
        return _cors_response(500, {'success': False, 'message': str(e)})


def _cors_response(status_code: int, body) -> Dict[str, Any]:
    """Build API Gateway response with CORS headers."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET,POST,DELETE,OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        },
        'body': json.dumps(body, default=str) if body else '',
    }
