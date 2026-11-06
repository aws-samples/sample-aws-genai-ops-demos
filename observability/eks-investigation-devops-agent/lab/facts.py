"""
Facts: the labelled values a probe returns so the Lab UI can render live state
without knowing the domain (pods, a BGP session, a file system's throughput mode).

    fact("Deployment", "payment-processor", status="success", detail="1/1 replicas ready",
         progress={"percent": 100, "text": "1/1"}, link=console_link("Console", url))
    fact("Pods", items=[item("payment-processor-abc", status="success", detail="Running")])

`status` is a Cloudscape StatusIndicator type: success | error | warning | pending |
stopped | in-progress | info | loading.
"""

import os
from typing import Any, Dict, List, Optional

import boto3

REGION = os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION') or ''


def console_host(region: str = REGION) -> str:
    """Console host for the region's partition (never a literal partition domain per demo)."""
    partition = boto3.session.Session().get_partition_for_region(region) if region else 'aws'
    if partition == 'aws-cn':
        return f'https://{region}.console.amazonaws.cn'
    if partition == 'aws-us-gov':
        return f'https://{region}.console.amazonaws-us-gov.com'
    return f'https://{region}.console.aws.amazon.com'


def console_url(service: str, fragment: str, region: str = REGION) -> str:
    """Hash-routed console URL (CloudWatch, Lambda, ...): /<service>/home?region=R#<fragment>."""
    return f'{console_host(region)}/{service}/home?region={region}#{fragment}'


def eks_console_url(cluster: str, path: str = '', region: str = REGION) -> str:
    """EKS console URL. The EKS console uses a path-based scheme (no #hash), e.g.
    /eks/clusters/<cluster>/resources/namespaces/<ns>. The old /eks/home#/clusters/...
    hash format no longer resolves."""
    return f'{console_host(region)}/eks/clusters/{cluster}{path}'


def eks_pod_console_url(cluster: str, namespace: str, pod: str, region: str = REGION) -> str:
    """EKS console URL for a single pod's detail page. The pod name is a path segment;
    namespace and region are query params (the scheme the current EKS console uses)."""
    return (f'{console_host(region)}/eks/clusters/{cluster}/resources/pods/{pod}'
            f'?namespace={namespace}&region={region}')


def console_link(text: str, href: str) -> Dict[str, str]:
    return {'text': text, 'href': href}


def item(text: str, *, status: Optional[str] = None, detail: Optional[str] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {'text': text}
    if status:
        out['status'] = status
    if detail:
        out['detail'] = detail
    return out


def fact(label: str, value: Optional[str] = None, *, status: Optional[str] = None,
         detail: Optional[str] = None, progress: Optional[Dict[str, Any]] = None,
         link: Optional[Dict[str, str]] = None, items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {'label': label}
    if value is not None:
        out['value'] = value
    if items is not None:
        out['items'] = items
    if status:
        out['status'] = status
    if detail:
        out['detail'] = detail
    if progress:
        out['progress'] = progress      # {"percent": 0-100, "text": "1/1 ready"}
    if link:
        out['link'] = link              # {"text": "Console", "href": "https://..."}
    return out
