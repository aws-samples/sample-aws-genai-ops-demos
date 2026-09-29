"""
DevOps Agent Lab API (API Gateway proxy handler).

Routes
  GET    /admin/scenarios                  lab/scenarios.yaml as served to the UI + environment facts
  GET    /admin/status                     per scenario: injected?, live facts (probe + alarm), current/last run
  POST   /admin/scenarios/{id}/inject      start one engine execution (refused while one is running)
  DELETE /admin/scenarios/{id}/inject      resolve the run's callback so the engine reverts now
  GET    /admin/usage                      DevOps Agent account usage
  GET    /admin/tasks                      recent tasks of the Agent Space (investigations, evaluations, chats)

State lives in two places only, neither of them a database:
  * the environment (handlers.probe): is the failure present right now?
  * the durable execution history (engine.py): where is the run, when does it auto-revert?

`handlers` is the demo's module (lab/handlers.py), bundled next to this file by the
LabBackend construct together with the demo's scenarios.yaml.
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import boto3

import devops_agent
import handlers
import scenarios
from engine import STEP_AWAIT, STEP_INJECT, STEP_REVERT
from facts import console_link, console_url, fact

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ENGINE_FUNCTION_ARN = os.environ.get('ENGINE_FUNCTION_ARN', '')   # qualified (alias) ARN
REGION = os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION') or ''
RUN_STATUSES_ACTIVE = ['RUNNING']

_lambda = None
_cloudwatch = None


def _lambda_client():
    global _lambda
    if _lambda is None:
        _lambda = boto3.client('lambda')
    return _lambda


def _cloudwatch_client():
    global _cloudwatch
    if _cloudwatch is None:
        _cloudwatch = boto3.client('cloudwatch')
    return _cloudwatch


# ---------------------------------------------------------------------------
# Durable execution lookups
# ---------------------------------------------------------------------------

def _unqualified_function_arn() -> str:
    """arn:...:function:name:live -> arn:...:function:name (the list API takes the function)."""
    parts = ENGINE_FUNCTION_ARN.split(':')
    return ':'.join(parts[:7]) if len(parts) > 7 else ENGINE_FUNCTION_ARN


def _list_executions(statuses: Optional[List[str]] = None, max_items: int = 20) -> List[Dict[str, Any]]:
    """Recent engine executions. The API accepts at most ONE status in the filter;
    pass None to list every status."""
    if not ENGINE_FUNCTION_ARN:
        return []
    kwargs: Dict[str, Any] = {'FunctionName': _unqualified_function_arn(), 'MaxItems': max_items}
    if statuses:
        kwargs['Statuses'] = statuses
    resp = _lambda_client().list_durable_executions_by_function(**kwargs)
    return resp.get('DurableExecutions', [])


def _scenario_of(execution: Dict[str, Any]) -> Optional[str]:
    """Execution names are `<scenario-id>-<epoch>`."""
    name = execution.get('DurableExecutionName', '')
    for s in scenarios.all_scenarios():
        if name.startswith(f"{s['id']}-"):
            return s['id']
    return None


def _running_execution(scenario_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    for ex in _list_executions(RUN_STATUSES_ACTIVE):
        if scenario_id is None or _scenario_of(ex) == scenario_id:
            return ex
    return None


def _decode(payload: str) -> Dict[str, Any]:
    """Step results are stored in the SDK's typed envelope ({"t":"m","v":{...}}); flatten it."""
    def _dec(node):
        if not isinstance(node, dict) or 't' not in node:
            return node
        t, v = node.get('t'), node.get('v')
        if t == 'm':
            return {k: _dec(val) for k, val in v.items()}
        if t == 'l':
            return [_dec(x) for x in v]
        return v
    try:
        return _dec(json.loads(payload)) or {}
    except Exception:
        return {}


def _epoch(ts) -> Optional[int]:
    if isinstance(ts, datetime):
        return int(ts.timestamp())
    return None


def _run_view(execution: Dict[str, Any]) -> Dict[str, Any]:
    """Condensed view of one engine execution for the UI: status, phases, countdown, callback."""
    arn = execution['DurableExecutionArn']
    status = execution.get('Status', '')
    steps: Dict[str, str] = {}
    callback: Dict[str, Any] = {}
    revert_reason = None
    try:
        paginator = _lambda_client().get_paginator('get_durable_execution_history')
        for page in paginator.paginate(DurableExecutionArn=arn, IncludeExecutionData=True):
            for ev in page.get('Events', []):
                et, name = ev.get('EventType', ''), ev.get('Name') or ''
                if et == 'StepStarted':
                    steps.setdefault(name, 'started')
                elif et == 'StepSucceeded':
                    steps[name] = 'succeeded'
                elif et == 'StepFailed':
                    steps[name] = 'failed'
                elif et == 'CallbackStarted':
                    details = ev.get('CallbackStartedDetails') or {}
                    callback = {
                        'id': details.get('CallbackId'),
                        'startedAt': _epoch(ev.get('EventTimestamp')),
                        'timeoutSeconds': details.get('Timeout'),
                        'state': 'waiting',
                    }
                elif et == 'CallbackSucceeded':
                    callback['state'] = 'resolved'
                    revert_reason = 'manual'
                elif et == 'CallbackTimedOut':
                    callback['state'] = 'timedOut'
                    revert_reason = 'auto'
                elif et == 'CallbackFailed':
                    callback['state'] = 'failed'
    except Exception as e:  # history is best effort; the run view must never 500 the status
        logger.warning('History read failed for %s: %s', arn, e)

    def step_status(name: str) -> str:
        return {'started': 'in-progress', 'succeeded': 'success', 'failed': 'error'}.get(steps.get(name, ''), 'pending')

    wait_status = {'waiting': 'in-progress', 'resolved': 'success', 'timedOut': 'success', 'failed': 'error'}.get(
        callback.get('state', ''), 'pending')
    phases = [
        {'id': STEP_INJECT, 'label': 'Inject the failure', 'status': step_status(STEP_INJECT)},
        {'id': STEP_AWAIT, 'label': 'Wait for a rollback', 'status': wait_status},
        {'id': STEP_REVERT, 'label': 'Revert', 'status': step_status(STEP_REVERT)},
    ]
    if status != 'RUNNING':
        for ph in phases:
            if ph['status'] in ('pending', 'in-progress'):
                ph['status'] = 'stopped'

    remaining = None
    if callback.get('state') == 'waiting' and callback.get('startedAt') and callback.get('timeoutSeconds'):
        remaining = max(0, callback['startedAt'] + int(callback['timeoutSeconds']) - int(time.time()))

    return {
        'executionArn': arn,
        'executionName': execution.get('DurableExecutionName'),
        'status': status,
        'startedAt': _epoch(execution.get('StartTimestamp')),
        'endedAt': _epoch(execution.get('EndTimestamp')),
        'phases': phases,
        'remainingSeconds': remaining,
        'revertReason': revert_reason,
        'callbackId': callback.get('id'),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _environment() -> Dict[str, Any]:
    env_facts = handlers.environment() if hasattr(handlers, 'environment') else []
    trigger = os.environ.get('TRIGGER_LAMBDA_NAME', '')
    return {
        'region': REGION,
        'partition': boto3.session.Session().get_partition_for_region(REGION) if REGION else 'aws',
        'devOpsAgentRegion': os.environ.get('DEVOPS_AGENT_REGION', ''),
        'devOpsAgentSpaceId': os.environ.get('DEVOPS_AGENT_SPACE_ID', ''),
        'triggerLambdaUrl': console_url('lambda', f'/functions/{trigger}') if trigger else None,
        'facts': env_facts,
    }


ALARM_STATUS = {'ALARM': 'error', 'OK': 'success', 'INSUFFICIENT_DATA': 'pending', 'NOT_FOUND': 'stopped', 'ERROR': 'warning'}


def _alarm_fact(alarm: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not alarm:
        return fact('Alarm', 'Not configured', status='stopped')
    name = alarm.get('name', '')
    return fact('Alarm', alarm['state'].replace('_', ' '), status=ALARM_STATUS.get(alarm['state'], 'info'),
                detail=alarm.get('error') or name,
                link=console_link('Console', console_url('cloudwatch', f'alarmsV2:alarm/{quote(name, safe="")}')) if name else None)


def get_scenarios() -> Dict[str, Any]:
    data = scenarios.load()
    return {
        'success': True,
        **data,
        'scenarios': [{**s, 'alarmName': scenarios.alarm_name(s)} for s in data['scenarios']],
        'environment': _environment(),
    }


def _alarms(names: List[str]) -> Dict[str, Dict[str, Any]]:
    names = [n for n in names if n]
    if not names:
        return {}
    try:
        resp = _cloudwatch_client().describe_alarms(AlarmNames=names)
        found = {a['AlarmName']: {'name': a['AlarmName'], 'state': a['StateValue'], 'reason': a.get('StateReason', '')}
                 for a in resp.get('MetricAlarms', [])}
        return {n: found.get(n, {'name': n, 'state': 'NOT_FOUND'}) for n in names}
    except Exception as e:
        return {n: {'name': n, 'state': 'ERROR', 'error': str(e)} for n in names}


def get_status() -> Dict[str, Any]:
    running = {s: ex for ex in _list_executions(RUN_STATUSES_ACTIVE) if (s := _scenario_of(ex))}
    last_done: Dict[str, Dict[str, Any]] = {}
    for ex in _list_executions(max_items=20):
        sid = _scenario_of(ex)
        if ex.get('Status') == 'RUNNING' or not sid:
            continue
        if (sid not in last_done
                    or (_epoch(ex.get('StartTimestamp')) or 0) > (_epoch(last_done[sid].get('StartTimestamp')) or 0)):
            last_done[sid] = ex

    alarms = _alarms([scenarios.alarm_name(s) for s in scenarios.all_scenarios()])
    out: Dict[str, Any] = {}
    for s in scenarios.all_scenarios():
        sid = s['id']
        handler = handlers.HANDLERS.get(s.get('handler', ''))
        try:
            probe = handler.probe() if handler else {'injected': False, 'error': f"unknown handler {s.get('handler')!r}"}
        except Exception as e:
            probe = {'injected': False, 'error': str(e)}
        entry: Dict[str, Any] = {'injected': bool(probe.get('injected')), 'facts': list(probe.get('facts') or [])}
        if probe.get('error'):
            entry['error'] = probe['error']
        if s.get('triggersAlarm'):
            entry['facts'].append(_alarm_fact(alarms.get(scenarios.alarm_name(s))))
        if sid in running:
            entry['run'] = _run_view(running[sid])
        elif sid in last_done:
            entry['lastRun'] = _run_view(last_done[sid])
        out[sid] = entry

    return {
        'success': True,
        'busy': next(iter(running), None),      # scenario id currently injected, if any
        'scenarios': out,
        'environment': _environment(),
        'checkedAt': int(time.time()),
    }


def inject(scenario_id: str) -> Dict[str, Any]:
    scenario = scenarios.get(scenario_id)
    if scenario is None:
        return {'success': False, 'message': f'Unknown scenario {scenario_id!r}', 'statusCode': 404}
    if not ENGINE_FUNCTION_ARN:
        return {'success': False, 'message': 'ENGINE_FUNCTION_ARN not configured', 'statusCode': 500}
    busy = _running_execution()
    if busy:
        return {'success': False, 'statusCode': 409,
                'message': f"Scenario {_scenario_of(busy) or busy.get('DurableExecutionName')} is already injected. "
                           'Roll it back first: one scenario at a time keeps the investigation unambiguous.'}

    # Unique per run: names collapse onto the same execution, so never reuse one. [a-zA-Z0-9-_], max 64.
    name = f'{scenario_id}-{int(time.time())}'
    resp = _lambda_client().invoke(
        FunctionName=ENGINE_FUNCTION_ARN,
        InvocationType='Event',
        DurableExecutionName=name,
        Payload=json.dumps({'scenarioId': scenario_id}).encode(),
    )
    timeout = scenarios.auto_revert_seconds(scenario)
    return {
        'success': True,
        'statusCode': 202,
        'message': f"{scenario['name']}: injection started. Auto-reverts in {timeout // 60} minutes unless rolled back.",
        'scenario': scenario_id,
        'executionName': name,
        'executionArn': resp.get('DurableExecutionArn'),
        'autoRevertSeconds': timeout,
    }


def rollback(scenario_id: str) -> Dict[str, Any]:
    scenario = scenarios.get(scenario_id)
    if scenario is None:
        return {'success': False, 'message': f'Unknown scenario {scenario_id!r}', 'statusCode': 404}

    running = _running_execution(scenario_id)
    if running:
        view = _run_view(running)
        if view.get('callbackId'):
            _lambda_client().send_durable_execution_callback_success(
                CallbackId=view['callbackId'],
                Result=json.dumps({'reason': 'manual', 'at': int(time.time())}).encode(),
            )
            return {'success': True, 'message': f"{scenario['name']}: rollback requested, the engine is reverting.",
                    'scenario': scenario_id, 'executionArn': view['executionArn']}
        return {'success': False, 'statusCode': 409,
                'message': 'The injection is still starting; retry in a few seconds.'}

    # No run owns the failure (changed by hand, or a run that already ended): revert directly.
    handler = handlers.HANDLERS.get(scenario.get('handler', ''))
    if handler and handler.probe().get('injected'):
        result = handler.revert()
        return {'success': True, 'scenario': scenario_id, **result, 'directRevert': True}
    return {'success': True, 'scenario': scenario_id, 'message': 'Nothing to roll back; the scenario is not injected.'}


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handler(event, context):
    path = event.get('path', '') or ''
    method = event.get('httpMethod', '')
    if method == 'OPTIONS':
        return _response(200, '')

    try:
        parts = [p for p in path.split('/') if p]      # ['admin', 'scenarios', '<id>', 'inject']
        if parts == ['admin', 'scenarios'] and method == 'GET':
            result = get_scenarios()
        elif parts == ['admin', 'status'] and method == 'GET':
            result = get_status()
        elif parts == ['admin', 'usage'] and method == 'GET':
            result = devops_agent.get_usage()
        elif parts == ['admin', 'tasks'] and method == 'GET':
            result = devops_agent.get_tasks()
        elif len(parts) == 4 and parts[:2] == ['admin', 'scenarios'] and parts[3] == 'inject' and method == 'POST':
            result = inject(parts[2])
        elif len(parts) == 4 and parts[:2] == ['admin', 'scenarios'] and parts[3] == 'inject' and method == 'DELETE':
            result = rollback(parts[2])
        else:
            return _response(404, {'success': False, 'message': f'Unknown route: {method} {path}'})

        status_code = result.pop('statusCode', 200 if result.get('success') else 500)
        return _response(status_code, result)
    except Exception as e:
        logger.error('Unhandled error: %s', e, exc_info=True)
        return _response(500, {'success': False, 'message': str(e)})


def _response(status_code: int, body) -> Dict[str, Any]:
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET,POST,DELETE,OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        },
        'body': json.dumps(body, default=str) if body != '' else '',
    }
