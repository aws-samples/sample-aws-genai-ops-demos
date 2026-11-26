"""
Start targeted Prowler scans for risky CloudTrail write calls.

EventBridge rules (built from rules.json) send matching "AWS API Call via
CloudTrail" events to SQS. This function receives what the SQS batching window
collected, maps every call to the Prowler checks and the resource it touched,
coalesces the batch into one Fargate task per scan group and starts the
scanner task definition with SCAN_MODE=event container overrides. The scanner
uploads the partial report to event-reports/{scan_id}/, which ingest upserts
by finding_uid without touching any other finding.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

RULES_FILE = Path(__file__).with_name('rules.json')
CLOUDTRAIL_DETAIL_TYPE = 'AWS API Call via CloudTrail'
STARTED_BY = 'prowler-event-scan'

# ECS rejects RunTask overrides longer than 8192 characters.
MAX_OVERRIDE_CHARS = 7000
MAX_UNITS_PER_TASK = 40

# Prowler 5.52 validates --resource-arn with this pattern and aborts the run on a mismatch.
ARN_PATTERN = re.compile(
    r'^arn:aws(-cn|-eusc|-us-gov|-iso|-iso-b)?:[a-zA-Z0-9\-]+:([a-z]{2}-[a-z]+-\d{1})?:(\d{12})?:'
    r'[a-zA-Z0-9\-_\/:\.\*]+(:\d+)?$'
)
CHECK_PATTERN = re.compile(r'^[a-z0-9_]+$')
GROUP_PATTERN = re.compile(r'^[a-z0-9-]{1,24}$')
FUNCTION_NAME_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,140}$')
SG_NOT_FOUND_CODES = {'InvalidGroup.NotFound', 'InvalidGroupId.Malformed', 'InvalidGroupId.NotFound'}


@dataclass(frozen=True)
class Rule:
    id: str
    group: str
    source: str
    resolver: str
    checks: tuple[str, ...]
    event_names: tuple[str, ...] = ()
    event_name_prefixes: tuple[str, ...] = ()
    delay_seconds: int = 0

    def matches(self, source: str, event_name: str) -> bool:
        if source != self.source:
            return False
        return event_name in self.event_names or any(event_name.startswith(p) for p in self.event_name_prefixes)


@dataclass(frozen=True)
class Target:
    """ARNs that must be scanned together, or a region for check-scoped rules."""
    arns: tuple[str, ...] = ()
    region: Optional[str] = None


@dataclass(frozen=True)
class ScanRequest:
    group: str
    checks: tuple[str, ...]
    target: Target
    message_id: str
    event_name: str


@dataclass
class TaskPlan:
    group: str
    checks: list[str]
    arns: list[str] = field(default_factory=list)
    region: Optional[str] = None
    message_ids: set[str] = field(default_factory=set)
    event_names: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class EventContext:
    partition: str
    account: str
    region: str
    home_region: str


@dataclass(frozen=True)
class Config:
    cluster_arn: str
    task_definition_arn: str
    container_name: str
    subnet_ids: tuple[str, ...]
    security_group_id: str
    assign_public_ip: str

    @property
    def partition(self) -> str:
        return self.task_definition_arn.split(':')[1]

    @classmethod
    def from_env(cls) -> 'Config':
        return cls(
            cluster_arn=os.environ['CLUSTER_ARN'],
            task_definition_arn=os.environ['TASK_DEFINITION_ARN'],
            container_name=os.environ['CONTAINER_NAME'],
            subnet_ids=tuple(s for s in os.environ['SUBNET_IDS'].split(',') if s),
            security_group_id=os.environ['SECURITY_GROUP_ID'],
            assign_public_ip=os.environ.get('ASSIGN_PUBLIC_IP', 'ENABLED'),
        )


class Lookups:
    """Read-only AWS lookups the resolvers need, cached for one invocation."""

    def __init__(self, session: Optional[boto3.session.Session] = None):
        self._session = session or boto3.session.Session()
        self._clients: dict[tuple[str, str], Any] = {}
        self._groups: dict[tuple[str, str, str], list[tuple[str, Optional[str]]]] = {}
        self._user_arns: dict[str, Optional[str]] = {}

    def _client(self, service: str, region: str):
        key = (service, region)
        if key not in self._clients:
            self._clients[key] = self._session.client(service, region_name=region)
        return self._clients[key]

    def security_group_vpcs(self, region: str, group_ids: Iterable[str], group_names: Iterable[str]) -> dict[str, str]:
        """Map each existing security group to its VPC. Deleted groups are skipped."""
        found: dict[str, str] = {}
        queries = [('GroupIds', gid) for gid in group_ids] + [('GroupNames', name) for name in group_names]
        for param, value in queries:
            key = (region, param, value)
            if key not in self._groups:
                try:
                    groups = self._client('ec2', region).describe_security_groups(**{param: [value]})
                    self._groups[key] = [(g['GroupId'], g.get('VpcId')) for g in groups.get('SecurityGroups', [])]
                except ClientError as exc:
                    if exc.response.get('Error', {}).get('Code') not in SG_NOT_FOUND_CODES:
                        raise
                    self._groups[key] = []
            for group_id, vpc_id in self._groups[key]:
                if vpc_id:
                    found[group_id] = vpc_id
        return found

    def iam_user_arn(self, user_name: str, region: str) -> Optional[str]:
        """Return the user's ARN including its path, or None if the user is gone."""
        if user_name not in self._user_arns:
            try:
                user = self._client('iam', region).get_user(UserName=user_name)['User']
                self._user_arns[user_name] = user['Arn']
            except ClientError as exc:
                if exc.response.get('Error', {}).get('Code') != 'NoSuchEntity':
                    raise
                self._user_arns[user_name] = None
        return self._user_arns[user_name]


def _str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def resolve_security_group(detail: dict, ctx: EventContext, lookups: Lookups) -> list[Target]:
    params = detail.get('requestParameters') or {}
    response = detail.get('responseElements') or {}
    group_ids: set[str] = set()
    if _str(params.get('groupId')):
        group_ids.add(params['groupId'])
    wrapped = params.get('ModifySecurityGroupRulesRequest') or {}
    if isinstance(wrapped, dict):
        group_ids.update(g for g in (wrapped.get('GroupId'), wrapped.get('groupId')) if _str(g))
    if isinstance(response, dict):
        for item in (response.get('securityGroupRuleSet') or {}).get('items') or []:
            if isinstance(item, dict) and _str(item.get('groupId')):
                group_ids.add(item['groupId'])
    group_names = set()
    if not group_ids and _str(params.get('groupName')):
        group_names.add(params['groupName'])
    if not group_ids and not group_names:
        return []
    vpcs = lookups.security_group_vpcs(ctx.region, sorted(group_ids), sorted(group_names))
    prefix = f'arn:{ctx.partition}:ec2:{ctx.region}:{ctx.account}'
    # Prowler skips a group whose VPC is filtered out by --resource-arn, so the VPC rides along.
    return [
        Target(arns=(f'{prefix}:security-group/{group_id}', f'{prefix}:vpc/{vpc_id}'))
        for group_id, vpc_id in sorted(vpcs.items())
    ]


def resolve_s3_bucket(detail: dict, ctx: EventContext, lookups: Lookups) -> list[Target]:
    bucket = _str((detail.get('requestParameters') or {}).get('bucketName'))
    return [Target(arns=(f'arn:{ctx.partition}:s3:::{bucket}',))] if bucket else []


def resolve_s3_account(detail: dict, ctx: EventContext, lookups: Lookups) -> list[Target]:
    # Prowler builds the account ARN from the scanner's own region, not the event's.
    return [Target(arns=(f'arn:{ctx.partition}:s3:{ctx.home_region}:{ctx.account}:account',))]


def resolve_iam_user(detail: dict, ctx: EventContext, lookups: Lookups) -> list[Target]:
    user_name = _str((detail.get('requestParameters') or {}).get('userName'))
    if not user_name:
        return []
    arn = lookups.iam_user_arn(user_name, ctx.home_region)
    return [Target(arns=(arn,))] if arn else []


def resolve_lambda_function(detail: dict, ctx: EventContext, lookups: Lookups) -> list[Target]:
    reference = _str((detail.get('requestParameters') or {}).get('functionName'))
    if not reference:
        return []
    region, account = ctx.region, ctx.account
    parts = reference.split(':')
    if reference.startswith('arn:') and len(parts) >= 7 and parts[5] == 'function':
        region, account, name = parts[3], parts[4], parts[6]
    elif len(parts) >= 3 and parts[1] == 'function':
        account, name = parts[0], parts[2]
    else:
        name = parts[0]
    if not FUNCTION_NAME_PATTERN.match(name):
        return []
    return [Target(arns=(f'arn:{ctx.partition}:lambda:{region}:{account}:function:{name}',))]


def resolve_region(detail: dict, ctx: EventContext, lookups: Lookups) -> list[Target]:
    return [Target(region=ctx.region)]


RESOLVERS: dict[str, Callable[[dict, EventContext, Lookups], list[Target]]] = {
    'security_group': resolve_security_group,
    's3_bucket': resolve_s3_bucket,
    's3_account': resolve_s3_account,
    'iam_user': resolve_iam_user,
    'lambda_function': resolve_lambda_function,
    'region': resolve_region,
}


def load_rules(path: Path = RULES_FILE) -> tuple[Rule, ...]:
    raw = json.loads(path.read_text())
    rules = tuple(
        Rule(
            id=r['id'],
            group=r['group'],
            source=r['source'],
            resolver=r['resolver'],
            checks=tuple(r['checks']),
            event_names=tuple(r.get('eventNames', ())),
            event_name_prefixes=tuple(r.get('eventNamePrefixes', ())),
            delay_seconds=int(r.get('delaySeconds', 0)),
        )
        for r in raw['rules']
    )
    for rule in rules:
        if rule.resolver not in RESOLVERS:
            raise ValueError(f'rule {rule.id}: unknown resolver {rule.resolver}')
        if not GROUP_PATTERN.match(rule.group):
            raise ValueError(f'rule {rule.id}: invalid group {rule.group}')
        if not rule.checks or not all(CHECK_PATTERN.match(c) for c in rule.checks):
            raise ValueError(f'rule {rule.id}: invalid checks')
        if not (rule.event_names or rule.event_name_prefixes):
            raise ValueError(f'rule {rule.id}: no event names')
    return rules


def match_rule(rules: Iterable[Rule], source: str, event_name: str) -> Optional[Rule]:
    return next((rule for rule in rules if rule.matches(source, event_name)), None)


def requests_from_event(
    envelope: Any,
    message_id: str,
    rules: Iterable[Rule],
    partition: str,
    lookups: Lookups,
    home_region: str,
) -> list[ScanRequest]:
    if not isinstance(envelope, dict) or envelope.get('detail-type') != CLOUDTRAIL_DETAIL_TYPE:
        return []
    detail = envelope.get('detail') or {}
    if detail.get('errorCode'):
        return []
    event_name = detail.get('eventName') or ''
    rule = match_rule(rules, envelope.get('source') or '', event_name)
    if rule is None:
        logger.info('No rule for %s %s', envelope.get('source'), event_name)
        return []
    ctx = EventContext(
        partition=partition,
        account=detail.get('recipientAccountId') or envelope.get('account') or '',
        region=detail.get('awsRegion') or envelope.get('region') or home_region,
        home_region=home_region,
    )
    requests = []
    for target in RESOLVERS[rule.resolver](detail, ctx, lookups):
        invalid = [arn for arn in target.arns if not ARN_PATTERN.match(arn)]
        if invalid:
            logger.warning('Skipping %s target with ARNs Prowler cannot scan: %s', event_name, invalid)
            continue
        requests.append(ScanRequest(rule.group, rule.checks, target, message_id, event_name))
    if not requests:
        logger.info('Nothing to scan for %s: resource gone or not resolvable', event_name)
    return requests


def container_overrides(plan: TaskPlan, scan_id: str, container_name: str) -> dict:
    environment = [
        {'name': 'SCAN_MODE', 'value': 'event'},
        {'name': 'EVENT_SCAN_ID', 'value': scan_id},
        {'name': 'PROWLER_CHECKS', 'value': ' '.join(plan.checks)},
    ]
    if plan.arns:
        environment.append({'name': 'PROWLER_RESOURCE_ARNS', 'value': ' '.join(plan.arns)})
    if plan.region:
        environment.append({'name': 'PROWLER_REGION', 'value': plan.region})
    return {'containerOverrides': [{'name': container_name, 'environment': environment}]}


def new_scan_id(group: str, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-event-{group}-{uuid.uuid4().hex[:6]}"


def override_chars(plan: TaskPlan, container_name: str = 'Prowler') -> int:
    return len(json.dumps(container_overrides(plan, new_scan_id(plan.group), container_name)))


def plan_tasks(requests: Iterable[ScanRequest]) -> list[TaskPlan]:
    """Coalesce requests into as few Prowler runs as possible.

    A run never mixes groups: Prowler intersects --checks with the checks it
    derives from the ARNs' services, so mixing e.g. RDS and EC2 ARNs drops checks.
    """
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for request in requests:
        bucket = buckets.setdefault(
            (request.group, request.target.region or ''),
            {'checks': set(), 'units': {}, 'message_ids': set(), 'event_names': set()},
        )
        bucket['checks'].update(request.checks)
        if request.target.arns:
            unit = bucket['units'].setdefault(request.target.arns, {'message_ids': set(), 'event_names': set()})
            unit['message_ids'].add(request.message_id)
            unit['event_names'].add(request.event_name)
        else:
            bucket['message_ids'].add(request.message_id)
            bucket['event_names'].add(request.event_name)

    plans: list[TaskPlan] = []
    for (group, region), bucket in sorted(buckets.items()):
        checks = sorted(bucket['checks'])
        if region:
            plans.append(TaskPlan(group, checks, region=region, message_ids=bucket['message_ids'],
                                  event_names=bucket['event_names']))
            continue
        current: Optional[TaskPlan] = None
        units_in_current = 0
        for arns, unit in sorted(bucket['units'].items()):
            if current is not None:
                merged = current.arns + [a for a in arns if a not in current.arns]
                fits = override_chars(TaskPlan(group, checks, arns=merged)) <= MAX_OVERRIDE_CHARS
                if units_in_current < MAX_UNITS_PER_TASK and fits:
                    current.arns = merged
                    current.message_ids |= unit['message_ids']
                    current.event_names |= unit['event_names']
                    units_in_current += 1
                    continue
                plans.append(current)
            current = TaskPlan(group, checks, arns=list(dict.fromkeys(arns)),
                               message_ids=set(unit['message_ids']), event_names=set(unit['event_names']))
            units_in_current = 1
        if current is not None:
            plans.append(current)
    return plans


def launch(plan: TaskPlan, ecs, config: Config, now: Optional[datetime] = None) -> tuple[str, str]:
    scan_id = new_scan_id(plan.group, now)
    response = ecs.run_task(
        cluster=config.cluster_arn,
        taskDefinition=config.task_definition_arn,
        launchType='FARGATE',
        platformVersion='LATEST',
        count=1,
        startedBy=STARTED_BY,
        networkConfiguration={
            'awsvpcConfiguration': {
                'subnets': list(config.subnet_ids),
                'securityGroups': [config.security_group_id],
                'assignPublicIp': config.assign_public_ip,
            }
        },
        overrides=container_overrides(plan, scan_id, config.container_name),
    )
    failures = response.get('failures') or []
    tasks = response.get('tasks') or []
    if failures or not tasks:
        raise RuntimeError(f'ecs:RunTask failed for {scan_id}: {failures}')
    task_arn = tasks[0]['taskArn']
    logger.info(json.dumps({
        'message': 'event scan started',
        'scan_id': scan_id,
        'task_arn': task_arn,
        'group': plan.group,
        'events': sorted(plan.event_names),
        'checks': plan.checks,
        'resource_arns': plan.arns,
        'region': plan.region,
        'sqs_messages': len(plan.message_ids),
    }))
    return scan_id, task_arn


def process(records: list[dict], rules: Iterable[Rule], config: Config, lookups: Lookups, ecs,
            home_region: str) -> dict:
    requests: list[ScanRequest] = []
    failed: set[str] = set()
    for record in records:
        message_id = record.get('messageId') or ''
        try:
            envelope = json.loads(record.get('body') or '')
        except ValueError:
            logger.warning('Dropping message %s: body is not JSON', message_id)
            continue
        try:
            requests.extend(requests_from_event(envelope, message_id, rules, config.partition, lookups, home_region))
        except (ClientError, BotoCoreError):
            logger.exception('Lookup failed for message %s; SQS will retry it', message_id)
            failed.add(message_id)
        except Exception:  # noqa: BLE001 - one malformed event must not block the batch
            logger.exception('Dropping message %s: unexpected event shape', message_id)

    for plan in plan_tasks(requests):
        try:
            launch(plan, ecs, config)
        except Exception:  # noqa: BLE001 - the contributing messages are retried by SQS
            logger.exception('Could not start an event scan for group %s', plan.group)
            failed.update(plan.message_ids)
    return {'batchItemFailures': [{'itemIdentifier': m} for m in sorted(failed) if m]}


RULES = load_rules()
_SESSION: Optional[boto3.session.Session] = None


def handler(event, context):
    global _SESSION
    _SESSION = _SESSION or boto3.session.Session()
    records = event.get('Records') or []
    logger.info('Received %d messages', len(records))
    return process(
        records,
        RULES,
        Config.from_env(),
        Lookups(_SESSION),
        _SESSION.client('ecs'),
        os.environ.get('AWS_REGION', 'us-east-1'),
    )
