"""
Dashboard API Lambda.

Exposed via Lambda Function URL with IAM authentication. The React dashboard
signs every request with SigV4 using temporary credentials from the Cognito
Identity Pool.

Routes (matched against event['requestContext']['http']['path'] + method):

    GET  /findings                                 — list findings (supports ?severity=, ?status=, ?limit=)
    GET  /findings/{finding_uid}                   — full finding + presigned URL to remediation markdown
    POST /findings/{finding_uid}/insights          — generate Bedrock insight for finding (sync)
    POST /findings/{finding_uid}/investigate       — dispatch DevOps Agent investigation
    GET  /findings/{finding_uid}/investigation     — DevOps Agent status + journal for finding
    GET  /investigations                           — every DevOps Agent backlog task dispatched by this demo
    GET  /scans                                    — most recent scan_ids (derived from findings)
    GET  /scans/running                            — ECS tasks currently running/recently stopped
    GET  /scans/running/{taskArn}/logs             — tqdm progress + phase parsed from CloudWatch
    POST /scans                                    — start a Prowler Fargate task on-demand
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import unquote

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_ddb = boto3.resource('dynamodb')
_s3 = boto3.client('s3')
_ecs = boto3.client('ecs')
_sns = boto3.client('sns')
_lambda = boto3.client('lambda')
_logs = boto3.client('logs')

TABLE = _ddb.Table(os.environ['FINDINGS_TABLE'])
REMEDIATIONS_BUCKET = os.environ['REMEDIATIONS_BUCKET']
CLUSTER_ARN = os.environ['SCANNER_CLUSTER_ARN']
TASK_DEF_ARN = os.environ['SCANNER_TASK_DEFINITION_ARN']
SUBNETS = [s for s in os.environ['SCANNER_SUBNET_IDS'].split(',') if s]
SG_ID = os.environ['SCANNER_SECURITY_GROUP_ID']
SCANNER_LOG_GROUP = os.environ.get('SCANNER_LOG_GROUP', '/aws/ecs/prowler-security-scanner')
DEVOPS_AGENT_TOPIC_ARN = os.environ.get('DEVOPS_AGENT_TOPIC_ARN', '')
DEVOPS_AGENT_REGION = os.environ.get('DEVOPS_AGENT_REGION', '')
DEVOPS_AGENT_SPACE_ID = os.environ.get('DEVOPS_AGENT_SPACE_ID', '')
REMEDIATION_LAMBDA = os.environ.get('REMEDIATION_LAMBDA', '')
COST_EVENTS_TABLE_NAME = os.environ.get('COST_EVENTS_TABLE', '')
COST_TABLE = _ddb.Table(COST_EVENTS_TABLE_NAME) if COST_EVENTS_TABLE_NAME else None

# The Lambda runtime's bundled botocore does not yet ship the devops-agent
# service model, so we call the DevOps Agent HTTPS API directly with SigV4
# signing via botocore's internal primitives (no extra dependencies).
from botocore.auth import SigV4Auth  # noqa: E402
from botocore.awsrequest import AWSRequest  # noqa: E402
from botocore.exceptions import BotoCoreError, ClientError  # noqa: E402
from urllib import error as urllib_error, request as urllib_request  # noqa: E402


def _incident_id(finding_uid: str) -> str:
    """Return the identifier DevOps Agent will store for this finding.

    The webhook Lambda (cdk/lambda/devops-agent-trigger) sanitises `:` and
    `/` out of the id because the generic webhook drops tasks whose
    incidentId contains them. We have to match that sanitisation here or
    the strict search will never find any task.
    """
    import re
    safe = re.sub(r'[^A-Za-z0-9-]', '-', finding_uid)[:200]
    return f'prowler-{safe}'


DEVOPS_SAFE_ERRORS = (RuntimeError, urllib_error.URLError, json.JSONDecodeError, BotoCoreError, ClientError)


def _devops_request(path: str, body: dict | None = None) -> dict:
    """POST to the DevOps Agent REST API with SigV4 auth.

    The service uses REST-style endpoints under
    https://dp.aidevops.{region}.api.aws/{path}  (SigV4 service name: 'aidevops').
    Returns parsed JSON body on success, raises on error.
    """
    if not DEVOPS_AGENT_REGION:
        raise RuntimeError('DEVOPS_AGENT_REGION not set')
    host = f'dp.aidevops.{DEVOPS_AGENT_REGION}.api.aws'
    if not path.startswith('/'):
        path = '/' + path
    url = f'https://{host}{path}'
    data = json.dumps(body or {}).encode('utf-8')
    req = AWSRequest(
        method='POST',
        url=url,
        data=data,
        headers={
            'Content-Type': 'application/json',
            'Host': host,
        },
    )
    creds = boto3.Session().get_credentials().get_frozen_credentials()
    SigV4Auth(creds, 'aidevops', DEVOPS_AGENT_REGION).add_auth(req)
    py_req = urllib_request.Request(
        url,
        data=data,
        method='POST',
        headers=dict(req.headers.items()),
    )
    # Defense-in-depth: guarantee the URL is HTTPS against the expected host
    # before calling urlopen, even though both are constructed above from the
    # Lambda env var (itself set by CDK, not user input). This keeps static
    # analysis (bandit B310 / semgrep dynamic-urllib) from flagging the call.
    if not py_req.full_url.startswith(f'https://{host}/'):
        raise RuntimeError(f'refusing to call non-HTTPS or unexpected host: {py_req.full_url!r}')
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib_request.urlopen(py_req, timeout=10) as resp:  # nosec B310 - scheme+host validated above
            raw = resp.read().decode('utf-8') or '{}'
            return json.loads(raw)
    except urllib_error.HTTPError as e:
        err = e.read().decode('utf-8')
        logger.warning('aidevops POST %s -> %s %s', path, e.code, err)
        raise RuntimeError(f'{e.code}: {err}')


def _devops_available() -> tuple[bool, str | None]:
    if not DEVOPS_AGENT_REGION:
        return False, 'DEVOPS_AGENT_REGION not set'
    if not DEVOPS_AGENT_SPACE_ID:
        return False, 'DEVOPS_AGENT_SPACE_ID not set'
    return True, None


def _json(status: int, body: Any) -> dict[str, Any]:
    return {
        'statusCode': status,
        'headers': {'content-type': 'application/json'},
        'body': json.dumps(body, default=str),
    }


def _list_findings(qs: dict[str, str]) -> dict[str, Any]:
    severity = qs.get('severity')
    status = qs.get('status')
    limit = int(qs.get('limit', '50'))
    limit = max(1, min(limit, 500))

    if severity:
        resp = TABLE.query(
            IndexName='severity-index',
            KeyConditionExpression=Key('severity').eq(severity.upper()),
            Limit=limit,
            ScanIndexForward=False,
        )
    elif status:
        resp = TABLE.query(
            IndexName='status-index',
            KeyConditionExpression=Key('status').eq(status.upper()),
            Limit=limit,
            ScanIndexForward=False,
        )
    else:
        resp = TABLE.scan(Limit=limit)

    items = resp.get('Items', [])
    # Strip raw payload from list view — it's huge.
    for i in items:
        i.pop('raw', None)
    return _json(200, {'items': items, 'count': len(items)})


def _get_finding(finding_uid: str) -> dict[str, Any]:
    resp = TABLE.get_item(Key={'finding_uid': finding_uid})
    item = resp.get('Item')
    if not item:
        return _json(404, {'error': 'finding not found'})

    # Inline the remediation markdown in the response so the browser doesn't
    # have to hit S3 directly (which would need CORS + a second fetch).
    remediation_key = item.get('remediation_s3_key')
    if remediation_key:
        try:
            obj = _s3.get_object(Bucket=REMEDIATIONS_BUCKET, Key=remediation_key)
            item['remediation_markdown'] = obj['Body'].read().decode('utf-8')
        except Exception as exc:  # noqa: BLE001 — best effort, degrade gracefully
            logger.warning('Could not inline remediation markdown %s: %s', remediation_key, exc)
    return _json(200, item)


def _decimal_to_number(obj: Any) -> Any:
    """Recursively convert DynamoDB Decimals to native JSON-friendly numbers."""
    from decimal import Decimal as _Dec
    if isinstance(obj, list):
        return [_decimal_to_number(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _decimal_to_number(v) for k, v in obj.items()}
    if isinstance(obj, _Dec):
        return float(obj)
    return obj


def _list_cost_events(qs: dict[str, str]) -> dict[str, Any]:
    """Return the most recent N cost events ordered by created_at desc.

    Uses the `by-date` GSI so it's cheap even when the table grows.
    """
    if COST_TABLE is None:
        return _json(200, {'events': [], 'error': 'COST_EVENTS_TABLE not configured'})
    limit = int(qs.get('limit', '100'))
    limit = max(1, min(limit, 500))
    resp = COST_TABLE.query(
        IndexName='by-date',
        KeyConditionExpression=Key('partition_key').eq('cost'),
        Limit=limit,
        ScanIndexForward=False,  # newest first
    )
    items = _decimal_to_number(resp.get('Items', []))
    return _json(200, {'events': items, 'count': len(items)})


def _cost_summary() -> dict[str, Any]:
    """Aggregate totals by event type across all cost events on record."""
    if COST_TABLE is None:
        return _json(200, {'total_usd': 0, 'by_type': {}, 'error': 'COST_EVENTS_TABLE not configured'})
    # Scan is fine here — the table is capped at a few thousand rows by TTL.
    resp = COST_TABLE.scan(
        ProjectionExpression='event_type, cost_usd, input_tokens, output_tokens',
    )
    items = resp.get('Items', [])
    by_type: dict[str, dict[str, float]] = {}
    total = 0.0
    total_input_tokens = 0
    total_output_tokens = 0
    for it in items:
        et = it.get('event_type') or 'unknown'
        cost = float(it.get('cost_usd') or 0)
        total += cost
        bucket = by_type.setdefault(et, {'count': 0, 'cost_usd': 0.0})
        bucket['count'] += 1
        bucket['cost_usd'] += cost
        total_input_tokens += int(it.get('input_tokens') or 0)
        total_output_tokens += int(it.get('output_tokens') or 0)
    return _json(200, {
        'total_usd': round(total, 6),
        'total_events': len(items),
        'total_input_tokens': total_input_tokens,
        'total_output_tokens': total_output_tokens,
        'by_type': {k: {'count': v['count'], 'cost_usd': round(v['cost_usd'], 6)} for k, v in by_type.items()},
    })


RAW_REPORTS_BUCKET = os.environ.get('RAW_REPORTS_BUCKET', '')


def _list_scans() -> dict[str, Any]:
    """List every scan ever produced by listing prefixes in the raw-reports
    bucket.

    The previous implementation derived scans from the findings table, but
    the table is overwritten every scan (one item per finding_uid) so only
    the *latest* scan_id survived. The S3 bucket keeps one
    `raw-reports/{scan_id}/` prefix per scan forever, so paginating those
    prefixes is both cheaper and historically accurate.
    """
    if not RAW_REPORTS_BUCKET:
        return _json(200, {'scans': [], 'error': 'RAW_REPORTS_BUCKET not configured'})

    paginator = _s3.get_paginator('list_objects_v2')
    seen: dict[str, str] = {}
    try:
        for page in paginator.paginate(
            Bucket=RAW_REPORTS_BUCKET,
            Prefix='raw-reports/',
            Delimiter='/',
        ):
            for cp in page.get('CommonPrefixes') or []:
                # CommonPrefix looks like "raw-reports/20260509T060142Z/"
                name = (cp.get('Prefix') or '').rstrip('/').split('/')[-1]
                if not name:
                    continue
                # Grab the first object in the prefix for an authoritative
                # LastModified. MaxKeys=1 keeps this cheap — we only need one.
                try:
                    inner = _s3.list_objects_v2(
                        Bucket=RAW_REPORTS_BUCKET,
                        Prefix=f'raw-reports/{name}/',
                        MaxKeys=1,
                    )
                    contents = inner.get('Contents') or []
                    if contents:
                        last_seen = contents[0]['LastModified'].isoformat()
                    else:
                        last_seen = name
                except Exception:
                    last_seen = name
                seen[name] = last_seen
    except Exception as exc:
        logger.warning('list_scans: ListObjectsV2 failed: %s', exc)
        return _json(200, {'scans': [], 'error': str(exc)})

    out = [{'scan_id': k, 'last_seen_at': v} for k, v in seen.items()]
    out.sort(key=lambda x: x['scan_id'], reverse=True)
    return _json(200, {'scans': out[:50]})


def _generate_insights(finding_uid: str) -> dict[str, Any]:
    """Synchronously invoke the Bedrock-backed remediation-context Lambda.

    Blocks until Bedrock returns the markdown (or the underlying Lambda times out
    — 5 min). Returns the generated markdown inline.
    """
    if not REMEDIATION_LAMBDA:
        return _json(500, {'error': 'REMEDIATION_LAMBDA not configured'})
    resp = _lambda.invoke(
        FunctionName=REMEDIATION_LAMBDA,
        InvocationType='RequestResponse',
        Payload=json.dumps({'finding_uid': finding_uid}).encode('utf-8'),
    )
    status = resp.get('StatusCode', 500)
    payload = resp.get('Payload')
    raw = payload.read().decode('utf-8') if payload else '{}'
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return _json(502, {'error': 'Bedrock lambda returned non-JSON', 'raw': raw})
    if status != 200 or parsed.get('statusCode') != 200:
        return _json(502, {'error': 'Bedrock generation failed', 'detail': parsed})

    # The remediation-context Lambda writes to S3 + DynamoDB, then returns the
    # key. Fetch the fresh markdown so the browser gets it inline.
    body = json.loads(parsed.get('body') or '{}')
    key = body.get('remediation_s3_key')
    if not key:
        return _json(502, {'error': 'No remediation_s3_key in Bedrock response'})
    obj = _s3.get_object(Bucket=REMEDIATIONS_BUCKET, Key=key)
    markdown = obj['Body'].read().decode('utf-8')
    return _json(200, {
        'remediation_s3_key': key,
        'remediation_markdown': markdown,
    })


def _caller_identity(event: dict) -> str:
    """Return a human-ish caller string for audit attribution.

    Function URL IAM auth surfaces the caller in requestContext.authorizer.
    Falls back to the Cognito email / user id in the identity claims.
    """
    rc = (event.get('requestContext') or {})
    auth = rc.get('authorizer') or {}
    iam = auth.get('iam') or {}
    user_arn = iam.get('userArn') or iam.get('userId')
    if user_arn:
        return str(user_arn)
    return 'unknown'


def _suppress_finding(finding_uid: str, body: dict, event: dict) -> dict[str, Any]:
    """Mark a finding as suppressed with a reason.

    Body shape: {"reason": "accepted risk, tracked in JIRA-123"}.
    Adds three attributes to the item: suppressed_at, suppress_reason,
    suppressed_by. Suppressed findings remain visible in the UI but are
    styled as such and excluded from compliance pass rate.
    """
    reason = (body.get('reason') or '').strip()
    if not reason:
        return _json(400, {'error': 'reason is required'})
    resp = TABLE.get_item(Key={'finding_uid': finding_uid})
    if not resp.get('Item'):
        return _json(404, {'error': 'finding not found'})
    now = datetime.utcnow().isoformat() + 'Z'
    TABLE.update_item(
        Key={'finding_uid': finding_uid},
        UpdateExpression='SET suppressed_at = :t, suppress_reason = :r, suppressed_by = :u',
        ExpressionAttributeValues={
            ':t': now,
            ':r': reason[:500],
            ':u': _caller_identity(event)[:500],
        },
    )
    return _json(200, {'finding_uid': finding_uid, 'suppressed_at': now, 'reason': reason})


def _unsuppress_finding(finding_uid: str) -> dict[str, Any]:
    resp = TABLE.get_item(Key={'finding_uid': finding_uid})
    if not resp.get('Item'):
        return _json(404, {'error': 'finding not found'})
    TABLE.update_item(
        Key={'finding_uid': finding_uid},
        UpdateExpression='REMOVE suppressed_at, suppress_reason, suppressed_by',
    )
    return _json(200, {'finding_uid': finding_uid, 'suppressed': False})


def _investigate_finding(finding_uid: str) -> dict[str, Any]:
    """Send a single finding to the DevOps Agent webhook (via SNS fan-out).

    This is what the TAM clicks in the UI: targeted investigation of the
    finding they want to show, instead of fire-and-forget on every CRITICAL.
    """
    if not DEVOPS_AGENT_TOPIC_ARN:
        return _json(500, {'error': 'DEVOPS_AGENT_TOPIC_ARN not configured'})

    resp = TABLE.get_item(Key={'finding_uid': finding_uid})
    item = resp.get('Item')
    if not item:
        return _json(404, {'error': 'finding not found'})

    # Marshal DynamoDB Decimals etc. through default=str.
    _sns.publish(
        TopicArn=DEVOPS_AGENT_TOPIC_ARN,
        Subject=f"Manual investigation: {item.get('check_id', 'unknown')}",
        Message=json.dumps(item, default=str),
    )
    return _json(202, {
        'incidentId': _incident_id(finding_uid),
        'message': 'Investigation dispatched to DevOps Agent',
    })


def _get_investigation(finding_uid: str) -> dict[str, Any]:
    """Fetch what the DevOps Agent has discovered for this finding.

    Queries list-backlog-tasks + list-journal-records from the Agent Space
    and filters by tasks whose description mentions our finding_uid. The
    journal records are the agent's step-by-step investigation log.
    """
    incident_id = _incident_id(finding_uid)
    ok, err = _devops_available()
    if not ok:
        return _json(200, {
            'incidentId': incident_id,
            'status': 'not_configured',
            'tasks': [],
            'journal': [],
            'error': err,
        })

    try:
        tasks_resp = _devops_request(
            f'/backlog/agent-space/{DEVOPS_AGENT_SPACE_ID}/tasks/list',
            {},
        )
        all_tasks = tasks_resp.get('tasks', [])
    except DEVOPS_SAFE_ERRORS as exc:
        logger.warning('ListBacklogTasks failed: %s', exc)
        return _json(200, {
            'incidentId': incident_id,
            'status': 'error',
            'error': str(exc),
            'tasks': [],
            'journal': [],
            'agentSpaceId': DEVOPS_AGENT_SPACE_ID,
        })

    # Strict match: the backlog task belongs to this finding only if *both*
    # check_id and resource_uid appear in its blob. Checking only the
    # 'prowler-<uid>' prefix would miss tasks whose title/description got
    # paraphrased by the agent.
    check_id: str | None = None
    resource_uid: str | None = None
    try:
        meta_resp = TABLE.get_item(
            Key={'finding_uid': finding_uid},
            ProjectionExpression='check_id, resource_uid',
        )
        meta_item = meta_resp.get('Item') or {}
        check_id = meta_item.get('check_id') or None
        resource_uid = meta_item.get('resource_uid') or None
    except DEVOPS_SAFE_ERRORS as exc:
        logger.warning('DynamoDB lookup for %s failed: %s', finding_uid, exc)

    def _task_matches(t: dict[str, Any]) -> bool:
        blob = '\n'.join([
            t.get('title') or '',
            t.get('description') or '',
            json.dumps(t.get('tags') or {}, default=str),
            json.dumps(t.get('metadata') or {}, default=str),
        ])
        if incident_id in blob or finding_uid in blob:
            return True
        if check_id and resource_uid and check_id in blob and resource_uid in blob:
            return True
        return False

    matching = [t for t in all_tasks if _task_matches(t)]

    journal = []
    for task in matching[:3]:
        exec_id = task.get('executionId')
        if not exec_id:
            # Task has no execution yet → try to resolve the latest execution
            # via ListExecutions (POST /journal/agent-space/{id}/executions).
            task_id = task.get('taskId')
            if not task_id:
                continue
            try:
                e = _devops_request(
                    f'/journal/agent-space/{DEVOPS_AGENT_SPACE_ID}/executions',
                    {'taskId': task_id},
                )
                execs = e.get('executions', [])
                if execs:
                    exec_id = execs[0].get('executionId')
            except DEVOPS_SAFE_ERRORS as exc:
                logger.warning('ListExecutions failed for task %s: %s', task_id, exc)
            if not exec_id:
                continue
        try:
            j = _devops_request(
                f'/journal/agent-space/{DEVOPS_AGENT_SPACE_ID}/journalRecords',
                {'executionId': exec_id},
            )
            journal.extend(j.get('records', []))
        except DEVOPS_SAFE_ERRORS as exc:
            logger.warning('ListJournalRecords failed for %s: %s', exec_id, exc)

    # 'idle' means "no task has ever been dispatched for this finding".
    # 'pending' is only meaningful after a task has been queued.
    if not matching:
        status = 'idle'
    else:
        statuses = {t.get('status') for t in matching}
        if 'COMPLETED' in statuses:
            status = 'completed'
        elif any(s in statuses for s in ('IN_PROGRESS', 'RUNNING', 'ACTIVE')):
            status = 'in_progress'
        else:
            status = 'pending'

    return _json(200, {
        'incidentId': incident_id,
        'status': status,
        'agentSpaceId': DEVOPS_AGENT_SPACE_ID,
        'executionId': (matching[0].get('executionId') if matching else None),
        'tasks': [
            {
                'taskId': t.get('taskId'),
                'executionId': t.get('executionId'),
                'title': t.get('title'),
                'status': t.get('status'),
                'priority': t.get('priority'),
                'createdAt': t.get('createdAt'),
                'updatedAt': t.get('updatedAt'),
            }
            for t in matching
        ],
        'journal': [
            {
                'timestamp': r.get('timestamp'),
                'type': r.get('type'),
                'content': r.get('content'),
            }
            for r in journal[:50]  # cap payload
        ],
    })


def _list_running_scans() -> dict[str, Any]:
    """Return Prowler Fargate tasks that are currently in flight.

    Covers PROVISIONING/PENDING/RUNNING plus tasks that stopped in the
    last 30 minutes so the UI can show a 'just finished' badge.
    """
    running = _ecs.list_tasks(cluster=CLUSTER_ARN, desiredStatus='RUNNING').get('taskArns', [])
    stopped = _ecs.list_tasks(cluster=CLUSTER_ARN, desiredStatus='STOPPED').get('taskArns', [])
    all_arns = running + stopped[:20]  # cap the stopped list for latency
    if not all_arns:
        return _json(200, {'tasks': []})
    desc = _ecs.describe_tasks(cluster=CLUSTER_ARN, tasks=all_arns).get('tasks', [])
    tasks = []
    for t in desc:
        tasks.append({
            'taskArn': t.get('taskArn'),
            'lastStatus': t.get('lastStatus'),
            'desiredStatus': t.get('desiredStatus'),
            'createdAt': t.get('createdAt'),
            'startedAt': t.get('startedAt'),
            'stoppedAt': t.get('stoppedAt'),
            'stoppedReason': t.get('stoppedReason'),
            'healthStatus': t.get('healthStatus'),
        })
    tasks.sort(key=lambda x: x.get('createdAt') or '', reverse=True)
    return _json(200, {'tasks': tasks[:10]})


# Matches tqdm's progress line: "description |▉▉▉▉| 123/590 [ 21%] ..."
# Prowler writes an ANSI-coloured variant; strip ANSI first.
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')
_TQDM_RE = re.compile(r'(?P<label>.{1,80}?)\s*\|[^|]*\|\s*(?P<current>\d+)\s*/\s*(?P<total>\d+)\s*\[\s*(?P<pct>\d+)\s*%\s*\]')


def _parse_scanner_line(raw: str) -> dict[str, Any] | None:
    clean = _ANSI_RE.sub('', raw).strip()
    if not clean:
        return None
    m = _TQDM_RE.search(clean)
    if m:
        try:
            return {
                'phase': 'scanning',
                'label': m.group('label').strip() or 'Scanning…',
                'current': int(m.group('current')),
                'total': int(m.group('total')),
                'percent': int(m.group('pct')),
                'line': clean,
            }
        except (ValueError, TypeError):
            pass
    # High-signal phase transitions from the wrapper script.
    lower = clean.lower()
    if 'starting prowler' in lower:
        return {'phase': 'starting', 'label': 'Starting Prowler scanner', 'percent': 0, 'line': clean}
    if 'prowler exited' in lower:
        return {'phase': 'uploading', 'label': 'Prowler finished · uploading report', 'percent': 95, 'line': clean}
    if 'uploading' in lower and 's3://' in lower:
        return {'phase': 'uploading', 'label': 'Uploading OCSF report to S3', 'percent': 97, 'line': clean}
    if '[scanner] done' in lower:
        return {'phase': 'done', 'label': 'Scan complete', 'percent': 100, 'line': clean}
    return None


def _get_scan_logs(task_arn: str) -> dict[str, Any]:
    """Parse the tail of the scanner CloudWatch Logs for a task.

    ECS awslogs driver writes to streams named "prowler/<container>/<taskId>".
    We read the last ~200 events and extract the newest tqdm progress line
    plus the latest phase transition (starting / scanning / uploading / done)
    so the UI can render a percentage and a descriptive label.
    """
    # Frontend passes the ARN URL-encoded (%3A, %2F) as a single path segment;
    # the Function URL keeps it encoded. Decode before extracting the task id.
    task_arn = unquote(task_arn or '')
    task_id = task_arn.split('/')[-1]
    if not task_id:
        return _json(400, {'error': 'taskArn required'})
    prefix = f'prowler/Prowler/{task_id}'
    try:
        resp = _logs.filter_log_events(
            logGroupName=SCANNER_LOG_GROUP,
            logStreamNamePrefix=prefix,
            limit=200,
        )
    except _logs.exceptions.ResourceNotFoundException:
        return _json(200, {'taskArn': task_arn, 'progress': None, 'events': []})
    except Exception as exc:  # noqa: BLE001
        logger.warning('filter_log_events failed for %s: %s', task_id, exc)
        return _json(200, {'taskArn': task_arn, 'progress': None, 'events': [], 'error': str(exc)})

    events = resp.get('events') or []
    messages = [e.get('message') or '' for e in events]

    # Newest progress line wins; scan right-to-left for the first one that parses.
    progress: dict[str, Any] | None = None
    for msg in reversed(messages):
        parsed = _parse_scanner_line(msg)
        if parsed:
            progress = parsed
            break

    tail = [m.strip() for m in messages[-10:] if m and m.strip()]
    return _json(200, {'taskArn': task_arn, 'progress': progress, 'tail': tail})


def _run_scan() -> dict[str, Any]:
    if not SUBNETS or not SG_ID:
        return _json(500, {'error': 'scanner networking not configured'})
    resp = _ecs.run_task(
        cluster=CLUSTER_ARN,
        taskDefinition=TASK_DEF_ARN,
        launchType='FARGATE',
        platformVersion='LATEST',
        count=1,
        networkConfiguration={
            'awsvpcConfiguration': {
                'subnets': SUBNETS,
                'securityGroups': [SG_ID],
                'assignPublicIp': 'ENABLED',
            }
        },
    )
    failures = resp.get('failures') or []
    if failures:
        return _json(500, {'error': 'ecs run_task failed', 'failures': failures})
    tasks = resp.get('tasks') or []
    return _json(202, {'task_arns': [t.get('taskArn') for t in tasks]})


def _list_investigations() -> dict[str, Any]:
    """List every DevOps Agent backlog task created by this demo.

    Incidents we dispatch tag themselves with `prowler-<finding_uid>` inside
    the task description. finding_uids contain colons, slashes, and other
    characters that prevent a naive "split on whitespace after prowler-"
    parser from recovering the full id. Instead we:

      1. Scan DynamoDB for all known finding_uids.
      2. For each backlog task, find the longest matching `prowler-<uid>`
         substring (longest to handle cases where one uid is a prefix of
         another — e.g. 'foo' vs 'foo:bar').

    O(findings × tasks) in the worst case but both are tiny in a demo.
    """
    ok, err = _devops_available()
    if not ok:
        return _json(200, {'investigations': [], 'error': err, 'status': 'not_configured'})

    try:
        tasks_resp = _devops_request(
            f'/backlog/agent-space/{DEVOPS_AGENT_SPACE_ID}/tasks/list',
            {},
        )
        all_tasks = tasks_resp.get('tasks', [])
    except DEVOPS_SAFE_ERRORS as exc:
        logger.warning('ListBacklogTasks (investigations) failed: %s', exc)
        return _json(200, {'investigations': [], 'error': str(exc), 'status': 'error'})

    # Load every known (finding_uid, check_id, resource_uid) tuple. The
    # DevOps Agent creates tasks whose description includes lines like:
    #
    #     Check ID: iam_aws_attached_policy_no_administrative_privileges
    #     Resource: arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
    #
    # so we match tasks → findings on (check_id AND resource_uid). That
    # survives the LLM rewriting the title, which it does for longer runs.
    try:
        scan = TABLE.scan(ProjectionExpression='finding_uid, check_id, resource_uid')
        findings_index: list[dict[str, str]] = [
            {
                'finding_uid': i.get('finding_uid') or '',
                'check_id': i.get('check_id') or '',
                'resource_uid': i.get('resource_uid') or '',
            }
            for i in scan.get('Items', [])
            if i.get('finding_uid')
        ]
    except DEVOPS_SAFE_ERRORS as exc:
        logger.warning('Scan findings table failed: %s', exc)
        findings_index = []

    # Precompute a check_title → finding_uid index for the final fallback
    # (older tasks whose description got truncated to just the title).
    try:
        scan_titles = TABLE.scan(ProjectionExpression='finding_uid, check_title')
        title_to_uid: dict[str, str] = {}
        for i in scan_titles.get('Items', []):
            ct = (i.get('check_title') or '').strip()
            uid = i.get('finding_uid')
            if ct and uid and ct not in title_to_uid:
                title_to_uid[ct] = uid
    except DEVOPS_SAFE_ERRORS as exc:
        logger.warning('Scan for check_title index failed: %s', exc)
        title_to_uid = {}

    by_finding: dict[str, dict[str, Any]] = {}
    unmatched_samples: list[str] = []
    for t in all_tasks:
        title = t.get('title') or ''
        blob = '\n'.join([
            title,
            t.get('description') or '',
            json.dumps(t.get('tags') or {}, default=str),
            json.dumps(t.get('metadata') or {}, default=str),
            json.dumps(t.get('context') or {}, default=str),
        ])
        matched_uid: str | None = None
        # (1) Strongest match: both check_id and resource_uid present.
        for f in findings_index:
            check_id = f['check_id']
            resource_uid = f['resource_uid']
            if not check_id or not resource_uid:
                continue
            if check_id in blob and resource_uid in blob:
                matched_uid = f['finding_uid']
                break
        # (2) Fallback: task title quotes the check_title verbatim. Happens
        # for older backlog entries where the agent truncated the payload.
        if not matched_uid:
            for check_title, uid in title_to_uid.items():
                if check_title and check_title in title:
                    matched_uid = uid
                    break
        if not matched_uid:
            if len(unmatched_samples) < 3:
                unmatched_samples.append(f"task {t.get('taskId')}: title={(title)[:200]!r}")
            continue
        existing = by_finding.get(matched_uid)
        updated = t.get('updatedAt') or t.get('createdAt') or ''
        if not existing or updated > (existing.get('updatedAt') or existing.get('createdAt') or ''):
            by_finding[matched_uid] = t

    # Hydrate with check metadata from DynamoDB (best-effort).
    out: list[dict[str, Any]] = []
    for finding_uid, task in by_finding.items():
        meta: dict[str, Any] = {}
        try:
            resp = TABLE.get_item(
                Key={'finding_uid': finding_uid},
                ProjectionExpression='check_id, check_title, severity, service_name, resource_uid',
            )
            meta = resp.get('Item') or {}
        except DEVOPS_SAFE_ERRORS as exc:
            logger.warning('DynamoDB get_item for %s failed: %s', finding_uid, exc)
        out.append({
            'finding_uid': finding_uid,
            'incidentId': _incident_id(finding_uid),
            'taskId': task.get('taskId'),
            'executionId': task.get('executionId'),
            'status': task.get('status'),
            'priority': task.get('priority'),
            'title': task.get('title'),
            'createdAt': task.get('createdAt'),
            'updatedAt': task.get('updatedAt'),
            'check_id': meta.get('check_id'),
            'check_title': meta.get('check_title'),
            'severity': meta.get('severity'),
            'service_name': meta.get('service_name'),
            'resource_uid': meta.get('resource_uid'),
        })

    out.sort(key=lambda r: (r.get('updatedAt') or r.get('createdAt') or ''), reverse=True)
    if unmatched_samples:
        logger.warning('list_investigations: %d/%d tasks could not be matched to a finding. Samples: %s',
                       len(unmatched_samples), len(all_tasks), ' | '.join(unmatched_samples))
    return _json(200, {
        'investigations': out,
        'agentSpaceId': DEVOPS_AGENT_SPACE_ID,
    })


def handler(event, context):
    http = (event.get('requestContext') or {}).get('http') or {}
    method = (http.get('method') or '').upper()
    path = http.get('path') or '/'
    qs = event.get('queryStringParameters') or {}
    logger.info('%s %s', method, path)

    # Normalize: Function URL paths always start with '/'
    parts = [p for p in path.split('/') if p]

    if method == 'GET' and parts == ['findings']:
        return _list_findings(qs)
    # finding_uid may contain slashes (e.g. role/RoleName/PolicyName). Treat
    # everything between /findings/ and an optional /investigate|/investigation
    # suffix as the uid.
    if parts and parts[0] == 'findings' and len(parts) >= 2:
        suffix = parts[-1] if parts[-1] in ('investigate', 'investigation', 'insights', 'suppress') else None
        if suffix:
            uid = '/'.join(parts[1:-1])
            if method == 'POST' and suffix == 'investigate':
                return _investigate_finding(uid)
            if method == 'GET' and suffix == 'investigation':
                return _get_investigation(uid)
            if method == 'POST' and suffix == 'insights':
                return _generate_insights(uid)
            if method == 'POST' and suffix == 'suppress':
                body = {}
                try:
                    body = json.loads(event.get('body') or '{}')
                except json.JSONDecodeError:
                    pass
                return _suppress_finding(uid, body, event)
            if method == 'DELETE' and suffix == 'suppress':
                return _unsuppress_finding(uid)
        else:
            uid = '/'.join(parts[1:])
            if method == 'GET':
                return _get_finding(uid)
    if method == 'GET' and parts == ['investigations']:
        return _list_investigations()
    if method == 'GET' and parts == ['cost', 'events']:
        return _list_cost_events(qs)
    if method == 'GET' and parts == ['cost', 'summary']:
        return _cost_summary()
    if method == 'GET' and parts == ['scans']:
        return _list_scans()
    if method == 'GET' and parts == ['scans', 'running']:
        return _list_running_scans()
    # /scans/running/<taskArn>/logs — live progress for a task. The task ARN
    # has embedded slashes (arn:aws:ecs:.../task/cluster/id). Even though the
    # browser URL-encodes it as a single segment, the SigV4 signer re-decodes
    # it before signing (so the wire path matches AWS canonicalisation),
    # meaning the ARN arrives here split across multiple path parts. Accept
    # any number of segments between `running` and `logs` and join them back.
    if (method == 'GET' and len(parts) >= 4
            and parts[0] == 'scans' and parts[1] == 'running' and parts[-1] == 'logs'):
        return _get_scan_logs('/'.join(parts[2:-1]))
    if method == 'POST' and parts == ['scans']:
        return _run_scan()

    return _json(404, {'error': f'no route for {method} {path}'})
