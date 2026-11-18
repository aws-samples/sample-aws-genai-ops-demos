"""
Lab handlers for the Aurora MySQL business-context demo: the only Lab code that knows Aurora.

Every scenario in lab/scenarios.yaml names a `handler`; HANDLERS maps that name to three
functions:
    inject() -> dict   break or mis-tag the cluster
    revert() -> dict   put it back
    probe()  -> dict   {"injected": bool, "facts": [fact, ...]}  read from the live environment,
                       never from a state store (a tag fixed in the console must show)
`environment()` returns the facts shown in the Lab header.

Two levers, both read back by the probes:
  * the cluster's tags (ListTagsForResource / AddTagsToResource / RemoveTagsFromResource);
  * the connection storm: the load function's reserved concurrency is its switch (0 = off).
    The load function itself (cdk/lambda/connection-load) opens and holds the connections from
    inside the VPC; this code never talks MySQL.

The probes are disjoint, so a condition owned by one scenario never shows on another card:
  connection_storm       the load is on and the cluster is NOT tagged Compliance=GDPR
  compliance_tags_gdpr   the cluster is tagged Compliance=GDPR (with or without the load)
  business_tags_removed  at least one of the baseline business tag keys is absent

Slow settling: DatabaseConnections is a one-minute metric. revert() of a storm waits until the
latest datapoint is back under the alarm threshold (up to 4 minutes), so the run does not end
while the card would still read the storm as live. The engine's timeout (cdk/lib/lab-stack.ts)
is sized for it.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, NamedTuple, Optional

import boto3

from facts import console_link, console_url, fact, item


class Handler(NamedTuple):
    """One scenario's code: break it, put it back, read the live state.

    `quick_revert` is the same fix without waiting for slow metrics to settle. The Lab API uses it
    for a direct revert (no engine run owns the condition): that road runs in a 60 s function
    behind CloudFront's 30 s origin timeout, and the UI polls the status anyway. The engine
    always uses `revert`, which waits, so a run does not end while the card still reads live.
    """
    inject: Callable[[], Dict[str, Any]]
    revert: Callable[[], Dict[str, Any]]
    probe: Callable[[], Dict[str, Any]]
    quick_revert: Optional[Callable[[], Dict[str, Any]]] = None


logger = logging.getLogger(__name__)

CLUSTER_ID = os.environ.get('CLUSTER_ID', '')
CLUSTER_ARN = os.environ.get('CLUSTER_ARN', '')
WRITER_INSTANCE_ID = os.environ.get('WRITER_INSTANCE_ID', '')
LOAD_FUNCTION_NAME = os.environ.get('LOAD_FUNCTION_NAME', '')
LOAD_CONCURRENCY_ON = int(os.environ.get('LOAD_CONCURRENCY_ON') or 2)
CONNECTIONS_THRESHOLD = int(os.environ.get('CONNECTIONS_THRESHOLD') or 60)
MAX_CONNECTIONS = int(os.environ.get('MAX_CONNECTIONS') or 90)
# Exactly the tags CDK put on the cluster (cdk/lib/constants.ts BUSINESS_TAGS).
BASELINE_TAGS: Dict[str, str] = json.loads(os.environ.get('BASELINE_TAGS') or '{}')

GDPR_TAGS = {'Compliance': 'GDPR', 'DataClassification': 'PersonalData'}

STORM_SETTLE_SECONDS = 240
QUICK_OFF_MESSAGE = 'Load generator off; the connections close within seconds and DatabaseConnections follows within about two minutes.'
INJECT_VISIBLE_SECONDS = 20

_clients: Dict[str, Any] = {}


def _client(name: str):
    if name not in _clients:
        _clients[name] = boto3.client(name)
    return _clients[name]


# ---------------------------------------------------------------------------
# Reading the environment
# ---------------------------------------------------------------------------

def tags() -> Dict[str, str]:
    resp = _client('rds').list_tags_for_resource(ResourceName=CLUSTER_ARN)
    return {t['Key']: t['Value'] for t in resp.get('TagList', [])}


def load_concurrency() -> int:
    """Reserved concurrency of the load function: 0 = the storm is off."""
    resp = _client('lambda').get_function_concurrency(FunctionName=LOAD_FUNCTION_NAME)
    return int(resp.get('ReservedConcurrentExecutions') or 0)


def connections() -> Optional[float]:
    """Latest one-minute Maximum of DatabaseConnections on the writer (None: no datapoint in 5 minutes)."""
    end = datetime.now(timezone.utc)
    resp = _client('cloudwatch').get_metric_data(
        MetricDataQueries=[{
            'Id': 'connections',
            'MetricStat': {
                'Metric': {'Namespace': 'AWS/RDS', 'MetricName': 'DatabaseConnections',
                           'Dimensions': [{'Name': 'DBInstanceIdentifier', 'Value': WRITER_INSTANCE_ID}]},
                'Period': 60, 'Stat': 'Maximum',
            },
        }],
        StartTime=end - timedelta(minutes=5), EndTime=end, ScanBy='TimestampDescending',
    )
    values = (resp.get('MetricDataResults') or [{}])[0].get('Values') or []
    return values[0] if values else None


def _cluster_console() -> Dict[str, str]:
    return console_link('Console', console_url('rds', f'database:id={CLUSTER_ID};is-cluster=true'))


def _load_console() -> Dict[str, str]:
    return console_link('Console', console_url('lambda', f'/functions/{LOAD_FUNCTION_NAME}'))


def _load_fact(concurrency: int, latest: Optional[float] = None) -> Dict[str, Any]:
    """The switch, read against the metric: on with the writer under the threshold is not a storm (yet)."""
    detail = f'{LOAD_FUNCTION_NAME}, reserved concurrency {concurrency}'
    if concurrency <= 0:
        return fact('Load generator', 'Off', status='stopped', detail=detail, link=_load_console())
    if latest is not None and latest <= CONNECTIONS_THRESHOLD:
        # Right after Inject the one-minute metric lags; if this persists, the function failed to connect.
        return fact('Load generator', 'On, connections not held yet', status='warning',
                    detail=f'{detail}. The metric lags about a minute; if this stays, read the function log',
                    link=_load_console())
    return fact('Load generator', 'On', status='error', detail=detail, link=_load_console())


def _connections_fact(value: Optional[float]) -> Dict[str, Any]:
    if value is None:
        return fact('Connections', 'No datapoint yet', status='pending',
                    detail='DatabaseConnections on the writer, one-minute Maximum')
    n = int(value)
    status = 'error' if n > CONNECTIONS_THRESHOLD else 'success'
    return fact('Connections', f'{n} of about {MAX_CONNECTIONS}', status=status,
                detail=f'alarm above {CONNECTIONS_THRESHOLD}; one-minute Maximum, about a minute behind',
                progress={'percent': min(100, round(100 * n / MAX_CONNECTIONS)), 'text': f'{n} of {MAX_CONNECTIONS} connections'})


def _tag_fact(label: str, key: str, current: Dict[str, str], bad_values: tuple = ()) -> Dict[str, Any]:
    value = current.get(key)
    if value is None:
        return fact(label, 'Not set', status='stopped' if not bad_values else 'success', link=_cluster_console())
    return fact(label, value, status='error' if value in bad_values else 'success', link=_cluster_console())


def _business_tags_fact(current: Dict[str, str]) -> Dict[str, Any]:
    items = [item(key, status='success' if key in current else 'error', detail=current.get(key, 'missing'))
             for key in BASELINE_TAGS]
    missing = [k for k in BASELINE_TAGS if k not in current]
    return fact('Business tags', None if items else 'None', items=items,
                status='error' if missing else 'success', link=_cluster_console())


def environment() -> List[Dict[str, Any]]:
    """Facts about the environment as a whole, shown in the Lab header."""
    try:
        cluster = _client('rds').describe_db_clusters(DBClusterIdentifier=CLUSTER_ID)['DBClusters'][0]
    except Exception as e:
        return [fact('Aurora MySQL cluster', CLUSTER_ID or 'Not configured', status='warning', detail=str(e))]
    status = cluster.get('Status', 'unknown')
    out = [fact('Aurora MySQL cluster', CLUSTER_ID, status='success' if status == 'available' else 'in-progress',
                detail=f"{status}, {cluster.get('Engine', '')} {cluster.get('EngineVersion', '')}", link=_cluster_console())]
    try:
        writer = _client('rds').describe_db_instances(DBInstanceIdentifier=WRITER_INSTANCE_ID)['DBInstances'][0]
        out.append(fact('Writer', WRITER_INSTANCE_ID,
                        status='success' if writer.get('DBInstanceStatus') == 'available' else 'in-progress',
                        detail=f"{writer.get('DBInstanceClass', '')}, {writer.get('DBInstanceStatus', '')}, "
                               f"Performance Insights {'on' if writer.get('PerformanceInsightsEnabled') else 'off'}"))
    except Exception as e:
        out.append(fact('Writer', WRITER_INSTANCE_ID or 'Unknown', status='warning', detail=str(e)))
    try:
        current = tags()
        present = [k for k in BASELINE_TAGS if k in current]
        out.append(fact('Business tags on the cluster', f'{len(present)} of {len(BASELINE_TAGS)}',
                        status='success' if len(present) == len(BASELINE_TAGS) else 'warning',
                        detail=', '.join(BASELINE_TAGS)))
    except Exception as e:
        out.append(fact('Business tags on the cluster', 'Unknown', status='warning', detail=str(e)))
    return out


# ---------------------------------------------------------------------------
# The storm (shared by two scenarios)
# ---------------------------------------------------------------------------

def _switch_off() -> None:
    _client('lambda').put_function_concurrency(FunctionName=LOAD_FUNCTION_NAME, ReservedConcurrentExecutions=0)


def _storm_on() -> Dict[str, Any]:
    """Switch the load on and start it. A failure part-way leaves the switch off (fail closed), then raises."""
    try:
        _client('lambda').put_function_concurrency(FunctionName=LOAD_FUNCTION_NAME, ReservedConcurrentExecutions=LOAD_CONCURRENCY_ON)
        _client('lambda').invoke(FunctionName=LOAD_FUNCTION_NAME, InvocationType='Event',
                                 Payload=json.dumps({'startedAt': int(time.time())}).encode())
    except Exception:
        try:
            _switch_off()
        except Exception:
            logger.exception('Could not switch the load off after a failed inject')
        raise
    # Give the load a moment to open its connections, so the card and the cluster agree.
    time.sleep(INJECT_VISIBLE_SECONDS)
    return {'message': f'Load generator on: it opens and holds about 85 of the about {MAX_CONNECTIONS} connections on {WRITER_INSTANCE_ID}, leaving a few free.'}


def _storm_off(wait: bool = True) -> Dict[str, Any]:
    """Switch the load off (it exits within seconds); with `wait`, also wait for the metric to come back under the threshold."""
    _switch_off()
    if not wait:
        return {'message': QUICK_OFF_MESSAGE}
    return {'message': f'Load generator off; connections closed{_settle()}.'}


def _settle() -> str:
    """Wait (up to STORM_SETTLE_SECONDS) for DatabaseConnections to fall under the threshold; '' when it did."""
    deadline = time.time() + STORM_SETTLE_SECONDS
    latest = connections()
    while latest is not None and latest > CONNECTIONS_THRESHOLD and time.time() < deadline:
        time.sleep(15)
        latest = connections()
    settled = latest is None or latest <= CONNECTIONS_THRESHOLD
    return '' if settled else f' (DatabaseConnections still reads {int(latest)}, it catches up within minutes)'


# ---------------------------------------------------------------------------
# Scenario: connection storm on a business-critical cluster
# ---------------------------------------------------------------------------

def inject_connection_storm() -> Dict[str, Any]:
    return _storm_on()


def revert_connection_storm() -> Dict[str, Any]:
    return _storm_off()


def quick_revert_connection_storm() -> Dict[str, Any]:
    return _storm_off(wait=False)


def probe_connection_storm() -> Dict[str, Any]:
    concurrency = load_concurrency()
    current = tags()
    latest = connections()
    return {
        'injected': concurrency > 0 and current.get('Compliance') != GDPR_TAGS['Compliance'],
        'facts': [_load_fact(concurrency, latest), _connections_fact(latest)],
    }


# ---------------------------------------------------------------------------
# Scenario: connection storm on a cluster tagged GDPR
# ---------------------------------------------------------------------------

def _restore_classification() -> str:
    baseline = BASELINE_TAGS.get('DataClassification', 'Confidential')
    _client('rds').remove_tags_from_resource(ResourceName=CLUSTER_ARN, TagKeys=['Compliance'])
    _client('rds').add_tags_to_resource(ResourceName=CLUSTER_ARN, Tags=[{'Key': 'DataClassification', 'Value': baseline}])
    return f'Compliance tag removed, DataClassification back to {baseline}.'


def inject_compliance_tags_gdpr() -> Dict[str, Any]:
    _client('rds').add_tags_to_resource(ResourceName=CLUSTER_ARN, Tags=[{'Key': k, 'Value': v} for k, v in GDPR_TAGS.items()])
    try:
        storm = _storm_on()
    except Exception:
        # The engine never reverts a failed inject: put the tags back so nothing stays half-injected.
        try:
            _restore_classification()
        except Exception:
            logger.exception('Could not restore the tags after a failed inject')
        raise
    return {'message': 'Cluster tagged Compliance=GDPR, DataClassification=PersonalData. ' + storm['message']}


def revert_compliance_tags_gdpr() -> Dict[str, Any]:
    # Switch off, restore the tags, THEN wait: a metric read failing during the wait must not
    # leave the cluster tagged GDPR until a step retry reaches the tags.
    _switch_off()
    restored = _restore_classification()
    return {'message': f'Load generator off; connections closed{_settle()}. {restored}'}


def quick_revert_compliance_tags_gdpr() -> Dict[str, Any]:
    _switch_off()
    return {'message': f'{QUICK_OFF_MESSAGE} {_restore_classification()}'}


def probe_compliance_tags_gdpr() -> Dict[str, Any]:
    current = tags()
    latest = connections()
    return {
        'injected': current.get('Compliance') == GDPR_TAGS['Compliance'],
        'facts': [
            _tag_fact('Compliance tag', 'Compliance', current, bad_values=('GDPR',)),
            _tag_fact('DataClassification', 'DataClassification', current, bad_values=('PersonalData',)),
            _load_fact(load_concurrency(), latest),
            _connections_fact(latest),
        ],
    }


# ---------------------------------------------------------------------------
# Scenario: business tags missing
# ---------------------------------------------------------------------------

def inject_business_tags_removed() -> Dict[str, Any]:
    _client('rds').remove_tags_from_resource(ResourceName=CLUSTER_ARN, TagKeys=list(BASELINE_TAGS))
    return {'message': f"Removed {', '.join(BASELINE_TAGS)} from {CLUSTER_ID}."}


def revert_business_tags_removed() -> Dict[str, Any]:
    """Put back exactly the tags CDK created (BASELINE_TAGS comes from the stack)."""
    _client('rds').add_tags_to_resource(ResourceName=CLUSTER_ARN, Tags=[{'Key': k, 'Value': v} for k, v in BASELINE_TAGS.items()])
    return {'message': f'Restored the {len(BASELINE_TAGS)} business tags on {CLUSTER_ID}.'}


def probe_business_tags_removed() -> Dict[str, Any]:
    current = tags()
    return {
        'injected': any(k not in current for k in BASELINE_TAGS),
        'facts': [_business_tags_fact(current)],
    }


# ---------------------------------------------------------------------------
# Registry: scenarios.yaml `handler` -> functions
# ---------------------------------------------------------------------------

HANDLERS: Dict[str, Handler] = {
    'connection_storm': Handler(inject_connection_storm, revert_connection_storm, probe_connection_storm,
                                quick_revert_connection_storm),
    'compliance_tags_gdpr': Handler(inject_compliance_tags_gdpr, revert_compliance_tags_gdpr, probe_compliance_tags_gdpr,
                                    quick_revert_compliance_tags_gdpr),
    'business_tags_removed': Handler(inject_business_tags_removed, revert_business_tags_removed, probe_business_tags_removed),
}
