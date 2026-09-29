"""
Kubernetes operations for the DevOps Agent Lab.

kubectl comes from the Lambda layer (@aws-cdk/lambda-layer-kubectl-v36); the EKS
bearer token is an STS presigned URL, same mechanism as `aws eks get-token`.
No aws CLI in the layer: AWS calls go through boto3.

Every scenario in lab/scenarios.yaml names a `handler`; HANDLERS maps that name to
three functions:
    inject() -> dict   break something
    revert() -> dict   put it back
    probe()  -> dict   {"injected": bool, "facts": [fact, ...]}  read from the cluster,
                       never from a state store (a manual kubectl fix must show)
`environment()` returns the facts shown in the Lab header. Facts are labelled values
the UI renders without knowing what they are (see facts.py).
"""

import base64
import json
import logging
import os
import subprocess
import time
from typing import Any, Callable, Dict, List, NamedTuple

import boto3

from facts import console_link, console_url, fact, item

logger = logging.getLogger(__name__)

EKS_CLUSTER_NAME = os.environ.get('EKS_CLUSTER_NAME', '')
NAMESPACE = os.environ.get('K8S_NAMESPACE', 'payment-demo')
DEPLOYMENT_NAME = os.environ.get('DEPLOYMENT_NAME', 'payment-processor')
METRICS_NAMESPACE = os.environ.get('METRICS_NAMESPACE', '')
REGION = os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION') or ''

KUBECTL = '/opt/kubectl/kubectl'
KUBECONFIG_PATH = '/tmp/kubeconfig'

_eks = None
_cloudwatch = None
_cluster_endpoint = None
_cluster_ca_data = None


def _eks_client():
    global _eks
    if _eks is None:
        _eks = boto3.client('eks')
    return _eks


def _cloudwatch_client():
    global _cloudwatch
    if _cloudwatch is None:
        _cloudwatch = boto3.client('cloudwatch')
    return _cloudwatch


# ---------------------------------------------------------------------------
# kubectl plumbing
# ---------------------------------------------------------------------------

def _get_eks_token(cluster_name: str) -> str:
    """EKS bearer token from an STS presigned GetCallerIdentity URL."""
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
        'url': f'{sts.meta.endpoint_url}/?Action=GetCallerIdentity&Version=2011-06-15',
        'body': {},
        'headers': {'x-k8s-aws-id': cluster_name},
        'context': {},
    }
    signed_url = signer.generate_presigned_url(params, region_name=REGION, expires_in=60, operation_name='')
    return 'k8s-aws-v1.' + base64.urlsafe_b64encode(signed_url.encode('utf-8')).decode('utf-8').rstrip('=')


def _setup_kubeconfig() -> None:
    """Write a kubeconfig with a fresh token. Cluster endpoint/CA are cached per container."""
    global _cluster_endpoint, _cluster_ca_data
    if _cluster_endpoint is None:
        cluster = _eks_client().describe_cluster(name=EKS_CLUSTER_NAME)['cluster']
        _cluster_endpoint = cluster['endpoint']
        _cluster_ca_data = cluster['certificateAuthority']['data']

    kubeconfig = {
        'apiVersion': 'v1',
        'kind': 'Config',
        'clusters': [{'name': 'eks', 'cluster': {
            'server': _cluster_endpoint,
            'certificate-authority-data': _cluster_ca_data,
        }}],
        'contexts': [{'name': 'eks', 'context': {'cluster': 'eks', 'user': 'eks'}}],
        'current-context': 'eks',
        'users': [{'name': 'eks', 'user': {'token': _get_eks_token(EKS_CLUSTER_NAME)}}],
    }
    with open(KUBECONFIG_PATH, 'w') as f:
        json.dump(kubeconfig, f)


def kubectl(args: list, timeout: int = 30) -> Dict[str, Any]:
    """Run kubectl; returns {"stdout", "stderr", "returncode"}."""
    _setup_kubeconfig()
    cmd = [KUBECTL] + args
    logger.info('Running: %s', ' '.join(cmd))
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, 'KUBECONFIG': KUBECONFIG_PATH},
    )
    return {'stdout': result.stdout, 'stderr': result.stderr, 'returncode': result.returncode}


def _kubectl_json(args: list):
    r = kubectl(args + ['-o', 'json'])
    if r['returncode'] != 0:
        raise RuntimeError(r['stderr'].strip() or 'kubectl failed')
    return json.loads(r['stdout'])


def _must(r: Dict[str, Any], what: str) -> None:
    if r['returncode'] != 0:
        raise RuntimeError(f"{what}: {r['stderr'].strip()}")


def _cluster_console(fragment: str = '') -> str:
    return console_url('eks', f'/clusters/{EKS_CLUSTER_NAME}{fragment}')


def _pods_fact(label: str, namespace: str, selector: str) -> Dict[str, Any]:
    """One fact listing the pods behind a selector: name, phase (waiting reason wins), restarts."""
    try:
        pods = _kubectl_json(['get', 'pods', '-n', namespace, '-l', selector]).get('items', [])
    except RuntimeError as e:
        logger.warning('Pod listing failed for %s/%s: %s', namespace, selector, e)
        return fact(label, 'Unreachable', status='warning', detail=str(e))
    items = []
    for pod in pods:
        phase = pod.get('status', {}).get('phase', 'Unknown')
        restarts, ready = 0, True
        for cs in pod.get('status', {}).get('containerStatuses', []):
            restarts += cs.get('restartCount', 0)
            ready = ready and cs.get('ready', False)
            reason = cs.get('state', {}).get('waiting', {}).get('reason')
            if reason:
                phase = reason
        healthy = ready and phase == 'Running'
        pending = phase in ('Pending', 'ContainerCreating')
        items.append(item(pod['metadata']['name'],
                          status='success' if healthy else 'pending' if pending else 'error',
                          detail=phase + (f', {restarts} restarts' if restarts else '')))
    return fact(label, items=items, value=None if items else 'No pods',
                status=None if items else 'error',
                link=console_link('Console', _cluster_console(f'/pods?namespace={namespace}')))


def _deployment_fact(dep: dict) -> Dict[str, Any]:
    """One fact for a Deployment: ready/desired replicas as a progress bar."""
    meta, spec, st = dep.get('metadata', {}), dep.get('spec', {}), dep.get('status', {})
    desired, ready = spec.get('replicas', 0), st.get('readyReplicas', 0)
    status = 'error' if desired and not ready else 'in-progress' if ready < desired else 'success'
    return fact('Deployment', meta.get('name', ''), status=status,
                detail=f"{meta.get('namespace', '')} namespace",
                progress={'percent': round(ready / desired * 100) if desired else 0, 'text': f'{ready}/{desired} replicas ready'},
                link=console_link('Console', _cluster_console()))


def environment() -> List[Dict[str, Any]]:
    """Facts about the environment as a whole, shown in the Lab header."""
    return [
        fact('Cluster', EKS_CLUSTER_NAME, link=console_link('Console', _cluster_console())),
        fact('Namespace', NAMESPACE),
    ]


# ---------------------------------------------------------------------------
# Scenario: database connection failure
# ---------------------------------------------------------------------------

WRONG_PASSWORD = 'wrong-password'


def inject_db_connection_failure() -> Dict[str, Any]:
    """Wrong DB_PASSWORD on payment-processor, then a restart so it CrashLoopBackOffs."""
    _must(kubectl(['set', 'env', f'deployment/{DEPLOYMENT_NAME}', f'DB_PASSWORD={WRONG_PASSWORD}', '-n', NAMESPACE]),
          'set env')
    _must(kubectl(['scale', f'deployment/{DEPLOYMENT_NAME}', '--replicas=0', '-n', NAMESPACE]), 'scale to 0')
    time.sleep(3)
    _must(kubectl(['scale', f'deployment/{DEPLOYMENT_NAME}', '--replicas=1', '-n', NAMESPACE]), 'scale to 1')
    return {'message': f'{DEPLOYMENT_NAME} restarted with a wrong DB_PASSWORD; expect CrashLoopBackOff.'}


def revert_db_connection_failure() -> Dict[str, Any]:
    """Point DB_PASSWORD back at the db-credentials secret."""
    dep = _kubectl_json(['get', f'deployment/{DEPLOYMENT_NAME}', '-n', NAMESPACE])
    env = dep['spec']['template']['spec']['containers'][0].get('env', [])
    idx = next((i for i, e in enumerate(env) if e.get('name') == 'DB_PASSWORD'), None)
    if idx is None:
        return {'message': 'DB_PASSWORD not present; nothing to restore.'}
    patch = json.dumps([{
        'op': 'replace',
        'path': f'/spec/template/spec/containers/0/env/{idx}',
        'value': {'name': 'DB_PASSWORD', 'valueFrom': {'secretKeyRef': {'name': 'db-credentials', 'key': 'DB_PASSWORD'}}},
    }])
    _must(kubectl(['patch', f'deployment/{DEPLOYMENT_NAME}', '-n', NAMESPACE, '--type=json', f'-p={patch}']),
          'patch deployment')
    return {'message': 'Database credentials restored; payment processor recovering.'}


def probe_db_connection_failure() -> Dict[str, Any]:
    try:
        dep = _kubectl_json(['get', f'deployment/{DEPLOYMENT_NAME}', '-n', NAMESPACE])
    except RuntimeError as e:
        return {'injected': False, 'error': str(e), 'facts': []}
    injected = any(
        e.get('name') == 'DB_PASSWORD' and e.get('value') == WRONG_PASSWORD
        for e in dep['spec']['template']['spec']['containers'][0].get('env', [])
    )
    return {
        'injected': injected,
        'facts': [
            _deployment_fact(dep),
            _pods_fact('Pods', NAMESPACE, f'app.kubernetes.io/name={DEPLOYMENT_NAME}'),
            fact('DB_PASSWORD', 'wrong-password (literal)' if injected else 'from secret db-credentials',
                 status='error' if injected else 'success'),
        ],
    }


# ---------------------------------------------------------------------------
# Scenario: DNS resolution failure
# ---------------------------------------------------------------------------

COREDNS_REPLICAS = 2


def inject_dns_resolution_failure() -> Dict[str, Any]:
    """Scale CoreDNS to 0. Fluent Bit cannot ship logs without DNS, so also push the
    custom metric that the DNS alarm watches."""
    _must(kubectl(['scale', 'deployment/coredns', '--replicas=0', '-n', 'kube-system']), 'scale coredns')
    if METRICS_NAMESPACE:
        try:
            _cloudwatch_client().put_metric_data(
                Namespace=METRICS_NAMESPACE,
                MetricData=[{'MetricName': 'DnsResolutionErrors', 'Value': 1, 'Unit': 'Count'}],
            )
        except Exception as e:  # metric is a helper, not the scenario
            logger.warning('put_metric_data failed: %s', e)
    return {'message': 'CoreDNS scaled to 0; all in-cluster service discovery fails.'}


def revert_dns_resolution_failure() -> Dict[str, Any]:
    _must(kubectl(['scale', 'deployment/coredns', f'--replicas={COREDNS_REPLICAS}', '-n', 'kube-system']),
          'restore coredns')
    return {'message': f'CoreDNS restored to {COREDNS_REPLICAS} replicas; DNS resolution recovering.'}


def probe_dns_resolution_failure() -> Dict[str, Any]:
    try:
        dep = _kubectl_json(['get', 'deployment/coredns', '-n', 'kube-system'])
    except RuntimeError as e:
        return {'injected': False, 'error': str(e), 'facts': []}
    return {
        'injected': dep.get('spec', {}).get('replicas', COREDNS_REPLICAS) == 0,
        'facts': [
            _deployment_fact(dep),
            _pods_fact('CoreDNS pods', 'kube-system', 'k8s-app=kube-dns'),
        ],
    }


# ---------------------------------------------------------------------------
# Registry: scenarios.yaml `handler` -> functions
# ---------------------------------------------------------------------------

class Handler(NamedTuple):
    inject: Callable[[], Dict[str, Any]]
    revert: Callable[[], Dict[str, Any]]
    probe: Callable[[], Dict[str, Any]]


HANDLERS: Dict[str, Handler] = {
    'db_connection_failure': Handler(inject_db_connection_failure, revert_db_connection_failure, probe_db_connection_failure),
    'dns_resolution_failure': Handler(inject_dns_resolution_failure, revert_dns_resolution_failure, probe_dns_resolution_failure),
}
