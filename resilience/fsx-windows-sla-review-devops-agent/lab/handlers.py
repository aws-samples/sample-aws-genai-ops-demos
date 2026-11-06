"""
Lab handlers for the FSx for Windows SLA review demo: the only Lab code that knows FSx.

Every scenario in lab/scenarios.yaml names a `handler`; HANDLERS maps that name to three
functions:
    inject() -> dict   break or mis-configure the file system
    revert() -> dict   put it back
    probe()  -> dict   {"injected": bool, "facts": [fact, ...]}  read from the live file
                       system, never from a state store (a fix made in the console must show)
`environment()` returns the facts shown in the Lab header.

All changes go through UpdateFileSystem or the CloudWatch alarm APIs. FSx applies a
configuration update asynchronously (lifecycle AVAILABLE -> UPDATING -> AVAILABLE or
MISCONFIGURED) and refuses a second update while one is in flight, so revert() waits for
the file system to settle before, and after, its own update. The engine function's timeout
(cdk/lib/lab-stack.ts) is sized for that wait.
"""

import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, NamedTuple, Optional

import boto3

from facts import console_link, console_url, fact, item


class Handler(NamedTuple):
    """One scenario's code: break it, put it back, read the live state."""
    inject: Callable[[], Dict[str, Any]]
    revert: Callable[[], Dict[str, Any]]
    probe: Callable[[], Dict[str, Any]]


logger = logging.getLogger(__name__)

FILE_SYSTEM_ID = os.environ.get('FILE_SYSTEM_ID', '')
DOMAIN_NAME = os.environ.get('DOMAIN_NAME', '')
DNS_IP = os.environ.get('DNS_IP', '')
DOMAIN_CONTROLLER_INSTANCE_ID = os.environ.get('DOMAIN_CONTROLLER_INSTANCE_ID', '')
SERVICE_ACCOUNT_SECRET_ARN = os.environ.get('SERVICE_ACCOUNT_SECRET_ARN', '')
BACKUP_RETENTION_DAYS = int(os.environ.get('BACKUP_RETENTION_DAYS') or 7)
FREE_STORAGE_ALARM_NAME = os.environ.get('FREE_STORAGE_ALARM_NAME', '')
FREE_STORAGE_THRESHOLD_BYTES = int(os.environ.get('FREE_STORAGE_THRESHOLD_BYTES') or 0)
ALARMS_TOPIC_ARN = os.environ.get('ALARMS_TOPIC_ARN', '')

SETTLED = ('AVAILABLE', 'MISCONFIGURED', 'MISCONFIGURED_UNAVAILABLE', 'FAILED')
BROKEN = ('MISCONFIGURED', 'MISCONFIGURED_UNAVAILABLE')
LIFECYCLE_STATUS = {'AVAILABLE': 'success', 'UPDATING': 'in-progress', 'CREATING': 'in-progress',
                    'MISCONFIGURED': 'error', 'MISCONFIGURED_UNAVAILABLE': 'error', 'FAILED': 'error'}
DEPLOYMENT_TYPES = {'SINGLE_AZ_1': 'Single-AZ 1', 'SINGLE_AZ_2': 'Single-AZ 2', 'MULTI_AZ_1': 'Multi-AZ'}

_clients: Dict[str, Any] = {}


def _client(name: str):
    if name not in _clients:
        _clients[name] = boto3.client(name)
    return _clients[name]


# ---------------------------------------------------------------------------
# The file system
# ---------------------------------------------------------------------------

def describe() -> Dict[str, Any]:
    return _client('fsx').describe_file_systems(FileSystemIds=[FILE_SYSTEM_ID])['FileSystems'][0]


def _file_system_console() -> Dict[str, str]:
    return console_link('Console', console_url('fsx', f'file-system-details/{FILE_SYSTEM_ID}'))


def _lifecycle_detail(fs: Dict[str, Any]) -> str:
    return ((fs.get('FailureDetails') or {}).get('Message')
            or (fs.get('LifecycleTransitionReason') or {}).get('Message') or '')


def _lifecycle_fact(fs: Dict[str, Any]) -> Dict[str, Any]:
    lifecycle = fs.get('Lifecycle', 'UNKNOWN')
    return fact('Lifecycle', lifecycle.replace('_', ' '), status=LIFECYCLE_STATUS.get(lifecycle, 'warning'),
                detail=_lifecycle_detail(fs) or None, link=_file_system_console())


def _deployment_fact(fs: Dict[str, Any]) -> Dict[str, Any]:
    win = fs.get('WindowsConfiguration') or {}
    deployment = win.get('DeploymentType', '')
    return fact('Deployment', DEPLOYMENT_TYPES.get(deployment, deployment),
                status='warning' if deployment.startswith('SINGLE') else 'success',
                detail=f"{fs.get('StorageCapacity', '?')} GiB {fs.get('StorageType', '')}, {win.get('ThroughputCapacity', '?')} MB/s"
                       + ('; one Availability Zone, backups are the recovery path' if deployment.startswith('SINGLE') else ''))


def _wait_until_settled(max_seconds: int) -> Dict[str, Any]:
    """FSx refuses a configuration update while one is applied: wait for a settled lifecycle."""
    deadline = time.time() + max_seconds
    fs = describe()
    while fs.get('Lifecycle') not in SETTLED and time.time() < deadline:
        time.sleep(15)
        fs = describe()
    return fs


def _wait_until_leaves(lifecycle: str, max_seconds: int) -> Dict[str, Any]:
    """After an update call, the lifecycle leaves AVAILABLE within seconds; wait so the probe sees it."""
    deadline = time.time() + max_seconds
    fs = describe()
    while fs.get('Lifecycle') == lifecycle and time.time() < deadline:
        time.sleep(5)
        fs = describe()
    return fs


def _update(windows_configuration: Dict[str, Any]) -> None:
    _client('fsx').update_file_system(FileSystemId=FILE_SYSTEM_ID, WindowsConfiguration=windows_configuration)


def _service_account() -> Dict[str, str]:
    raw = _client('secretsmanager').get_secret_value(SecretId=SERVICE_ACCOUNT_SECRET_ARN)['SecretString']
    return json.loads(raw)


def _latest_update_action(fs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    actions = [a for a in fs.get('AdministrativeActions') or [] if a.get('AdministrativeActionType') == 'FILE_SYSTEM_UPDATE']
    if not actions:
        return None
    return max(actions, key=lambda a: a.get('RequestTime') or 0)


def _update_action_fact(fs: Dict[str, Any]) -> Dict[str, Any]:
    action = _latest_update_action(fs)
    if action is None:
        return fact('Last configuration update', 'None', status='stopped')
    status = action.get('Status', '')
    when = action.get('RequestTime')
    detail = (action.get('FailureDetails') or {}).get('Message') or (when.strftime('%H:%M:%S UTC') if when else None)
    return fact('Last configuration update', status.replace('_', ' ').title(),
                status={'COMPLETED': 'success', 'FAILED': 'error', 'IN_PROGRESS': 'in-progress', 'PENDING': 'pending',
                        'UPDATED_OPTIMIZING': 'in-progress'}.get(status, 'info'),
                detail=detail)


def _domain_controller_fact() -> Dict[str, Any]:
    if not DOMAIN_CONTROLLER_INSTANCE_ID:
        return fact('Domain controller', DNS_IP or 'Unknown')
    try:
        inst = _client('ec2').describe_instances(InstanceIds=[DOMAIN_CONTROLLER_INSTANCE_ID])['Reservations'][0]['Instances'][0]
        state = inst.get('State', {}).get('Name', 'unknown')
    except Exception as e:
        logger.warning('describe_instances failed: %s', e)
        state = 'unknown'
    return fact('Domain controller', f'{DNS_IP} ({state})', status='success' if state == 'running' else 'warning',
                detail=f'{DOMAIN_NAME}, self-managed Active Directory on EC2',
                link=console_link('Console', console_url('ec2', f'InstanceDetails:instanceId={DOMAIN_CONTROLLER_INSTANCE_ID}')))


def environment() -> List[Dict[str, Any]]:
    """Facts about the environment as a whole, shown in the Lab header."""
    try:
        fs = describe()
    except Exception as e:
        return [fact('File system', FILE_SYSTEM_ID or 'Not configured', status='warning', detail=str(e))]
    return [
        fact('File system', FILE_SYSTEM_ID, status=LIFECYCLE_STATUS.get(fs.get('Lifecycle', ''), 'warning'),
             detail=fs.get('DNSName') or fs.get('Lifecycle'), link=_file_system_console()),
        _deployment_fact(fs),
        _domain_controller_fact(),
    ]


# ---------------------------------------------------------------------------
# Scenario: Active Directory credentials rotated
# ---------------------------------------------------------------------------

def inject_misconfigured_ad_credentials() -> Dict[str, Any]:
    """Give FSx a service-account password nobody rotated in AD: validation fails, MISCONFIGURED."""
    account = _service_account()
    _wait_until_settled(120)
    _update({'SelfManagedActiveDirectoryConfiguration': {
        'UserName': account['username'],
        'Password': f'Rotated-{int(time.time())}-NotTheRealOne!',
    }})
    fs = _wait_until_leaves('AVAILABLE', 60)
    return {'message': f"Service-account password changed on the file system; lifecycle {fs.get('Lifecycle')}. "
                       'FSx validates against the domain and reports MISCONFIGURED within minutes.'}


def revert_misconfigured_ad_credentials() -> Dict[str, Any]:
    """Restore the password FSx should hold and wait for AVAILABLE, so the run ends when the file system is back."""
    account = _service_account()
    fs = _wait_until_settled(300)
    _update({'SelfManagedActiveDirectoryConfiguration': {'UserName': account['username'], 'Password': account['password']}})
    time.sleep(10)
    fs = _wait_until_settled(420)
    return {'message': f"Correct service-account credentials restored; lifecycle {fs.get('Lifecycle')}."}


def probe_misconfigured_ad_credentials() -> Dict[str, Any]:
    fs = describe()
    lifecycle = fs.get('Lifecycle', '')
    action = _latest_update_action(fs)
    ad_update_in_flight = bool(action and action.get('Status') in ('PENDING', 'IN_PROGRESS')
                               and 'SelfManagedActiveDirectoryConfiguration' in json.dumps(action.get('TargetFileSystemValues') or {}, default=str))
    win = fs.get('WindowsConfiguration') or {}
    ad = win.get('SelfManagedActiveDirectoryConfiguration') or {}
    return {
        'injected': lifecycle in BROKEN or (lifecycle == 'UPDATING' and ad_update_in_flight),
        'facts': [
            _lifecycle_fact(fs),
            fact('Active Directory', ad.get('DomainName') or DOMAIN_NAME,
                 detail=f"service account {ad.get('UserName', '?')}, DNS {', '.join(ad.get('DnsIps') or [DNS_IP])}"),
            _update_action_fact(fs),
        ],
    }


# ---------------------------------------------------------------------------
# Scenario: automatic backups disabled
# ---------------------------------------------------------------------------

def inject_backups_disabled() -> Dict[str, Any]:
    _wait_until_settled(120)
    _update({'AutomaticBackupRetentionDays': 0})
    return {'message': 'Automatic backup retention set to 0 days: no daily recovery point from now on.'}


def revert_backups_disabled() -> Dict[str, Any]:
    _wait_until_settled(300)
    _update({'AutomaticBackupRetentionDays': BACKUP_RETENTION_DAYS})
    return {'message': f'Automatic backups restored, kept {BACKUP_RETENTION_DAYS} days.'}


def _latest_backup_fact() -> Dict[str, Any]:
    try:
        backups = _client('fsx').describe_backups(Filters=[{'Name': 'file-system-id', 'Values': [FILE_SYSTEM_ID]}], MaxResults=50).get('Backups', [])
    except Exception as e:
        return fact('Most recent backup', 'Unknown', status='warning', detail=str(e))
    if not backups:
        return fact('Most recent backup', 'None yet', status='pending', detail='the first automatic backup runs in the daily window')
    latest = max(backups, key=lambda b: b.get('CreationTime'))
    return fact('Most recent backup', latest['CreationTime'].strftime('%Y-%m-%d %H:%M UTC'),
                status='success' if latest.get('Lifecycle') == 'AVAILABLE' else 'in-progress',
                detail=f"{latest.get('Type', '').lower()} backup {latest.get('BackupId', '')}, {latest.get('Lifecycle', '').lower()}",
                link=console_link('Console', console_url('fsx', 'backups')))


def probe_backups_disabled() -> Dict[str, Any]:
    fs = describe()
    win = fs.get('WindowsConfiguration') or {}
    retention = int(win.get('AutomaticBackupRetentionDays') or 0)
    return {
        'injected': retention == 0,
        'facts': [
            fact('Automatic backups', 'Off' if retention == 0 else f'Kept {retention} days',
                 status='error' if retention == 0 else 'success',
                 detail=f"daily window {win.get('DailyAutomaticBackupStartTime', '?')} UTC", link=_file_system_console()),
            _deployment_fact(fs),
            _latest_backup_fact(),
        ],
    }


# ---------------------------------------------------------------------------
# Scenario: no CloudWatch alarm on the file system
# ---------------------------------------------------------------------------

def _fsx_alarms() -> List[Dict[str, Any]]:
    """Every AWS/FSx metric alarm scoped to this file system (what the review counts, dimension 7)."""
    out = []
    for page in _client('cloudwatch').get_paginator('describe_alarms').paginate(AlarmTypes=['MetricAlarm']):
        for alarm in page.get('MetricAlarms', []):
            dims = {d['Name']: d['Value'] for d in alarm.get('Dimensions', [])}
            if alarm.get('Namespace') == 'AWS/FSx' and dims.get('FileSystemId') == FILE_SYSTEM_ID:
                out.append(alarm)
    return out


def inject_alarms_removed() -> Dict[str, Any]:
    _client('cloudwatch').delete_alarms(AlarmNames=[FREE_STORAGE_ALARM_NAME])
    return {'message': f'Alarm {FREE_STORAGE_ALARM_NAME} deleted: nothing watches the file system any more.'}


def revert_alarms_removed() -> Dict[str, Any]:
    """Recreate the alarm as cdk/lib/file-system-stack.ts defines it (same name, metric, threshold, action)."""
    gib = FREE_STORAGE_THRESHOLD_BYTES / 0.2 / 1024 ** 3
    _client('cloudwatch').put_metric_alarm(
        AlarmName=FREE_STORAGE_ALARM_NAME,
        AlarmDescription=f"FSx file system {FILE_SYSTEM_ID}: free storage below 20% of {gib:.0f} GiB (the review's storage floor; writes fail at 0)",
        Namespace='AWS/FSx', MetricName='FreeStorageCapacity',
        Dimensions=[{'Name': 'FileSystemId', 'Value': FILE_SYSTEM_ID}],
        Statistic='Minimum', Period=300, EvaluationPeriods=1,
        Threshold=FREE_STORAGE_THRESHOLD_BYTES, ComparisonOperator='LessThanThreshold',
        TreatMissingData='notBreaching',
        AlarmActions=[ALARMS_TOPIC_ARN] if ALARMS_TOPIC_ARN else [],
    )
    return {'message': f'Alarm {FREE_STORAGE_ALARM_NAME} recreated on FreeStorageCapacity at the 20% floor.'}


ALARM_STATE = {'OK': 'success', 'ALARM': 'error', 'INSUFFICIENT_DATA': 'pending'}


def probe_alarms_removed() -> Dict[str, Any]:
    alarms = _fsx_alarms()
    free_storage = next((a for a in alarms if a['AlarmName'] == FREE_STORAGE_ALARM_NAME), None)
    return {
        'injected': free_storage is None,
        'facts': [
            fact('FreeStorageCapacity alarm', free_storage['StateValue'].replace('_', ' ') if free_storage else 'Not found',
                 status=ALARM_STATE.get(free_storage['StateValue'], 'info') if free_storage else 'error',
                 detail=FREE_STORAGE_ALARM_NAME,
                 link=console_link('Console', console_url('cloudwatch', f'alarmsV2:alarm/{FREE_STORAGE_ALARM_NAME}')) if free_storage else None),
            fact('AWS/FSx alarms on this file system',
                 items=[item(a['AlarmName'], status=ALARM_STATE.get(a['StateValue'], 'info'), detail=a['StateValue']) for a in alarms],
                 value=None if alarms else 'None', status=None if alarms else 'error',
                 link=console_link('Console', console_url('cloudwatch', 'alarmsV2:'))),
        ],
    }


# ---------------------------------------------------------------------------
# Registry: scenarios.yaml `handler` -> functions
# ---------------------------------------------------------------------------

HANDLERS: Dict[str, Handler] = {
    'misconfigured_ad_credentials': Handler(inject_misconfigured_ad_credentials, revert_misconfigured_ad_credentials, probe_misconfigured_ad_credentials),
    'backups_disabled': Handler(inject_backups_disabled, revert_backups_disabled, probe_backups_disabled),
    'alarms_removed': Handler(inject_alarms_removed, revert_alarms_removed, probe_alarms_removed),
}
