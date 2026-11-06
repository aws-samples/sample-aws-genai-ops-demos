"""
Lab API of the EKS demo (API Gateway proxy handler).

Routes
  GET    /admin/scenarios                  lab/scenarios.yaml as served to the UI + environment facts
  GET    /admin/status                     per scenario: injected?, live facts (probe + alarm), current/last run
  POST   /admin/scenarios/{id}/inject      start one engine execution (409 while one is running)
  DELETE /admin/scenarios/{id}/inject      resolve the run's callback so the engine reverts now
  GET    /admin/usage                      DevOps Agent account usage
  GET    /admin/tasks                      recent tasks of the Agent Space

State lives in two places only, neither of them a database:
  * the cluster (handlers.probe): is the failure present right now?
  * the durable execution history (shared engine): where is the run, when does it auto-revert?
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import boto3

import devops_agent
import handlers
import scenarios
from engine import AlreadyRunning, Engine
from facts import console_link, console_url, fact

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION') or ''
STEP_LABELS = {'inject': 'Inject the failure', 'await-rollback': 'Wait for a rollback', 'revert': 'Revert'}

_engine: Optional[Engine] = None
_cloudwatch = None


def _eng() -> Engine:
    global _engine
    if _engine is None:
        _engine = Engine(os.environ.get('ENGINE_FUNCTION_ARN', ''))
    return _engine


def _cloudwatch_client():
    global _cloudwatch
    if _cloudwatch is None:
        _cloudwatch = boto3.client('cloudwatch')
    return _cloudwatch


def _phases(view: Dict[str, Any]) -> Dict[str, Any]:
    """Shared run view + this Lab's step labels (the UI renders Steps from `phases`)."""
    return {**view, 'phases': [{'id': k, 'label': STEP_LABELS[k], 'status': v} for k, v in view['steps'].items()]}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _environment() -> Dict[str, Any]:
    trigger = os.environ.get('TRIGGER_LAMBDA_NAME', '')
    return {
        'region': REGION,
        'partition': boto3.session.Session().get_partition_for_region(REGION) if REGION else 'aws',
        'devOpsAgentRegion': os.environ.get('DEVOPS_AGENT_REGION', ''),
        'devOpsAgentSpaceId': os.environ.get('DEVOPS_AGENT_SPACE_ID', ''),
        'triggerLambdaUrl': console_url('lambda', f'/functions/{trigger}') if trigger else None,
        'facts': handlers.environment(),
    }


ALARM_STATUS = {'ALARM': 'error', 'OK': 'success', 'INSUFFICIENT_DATA': 'pending', 'NOT_FOUND': 'stopped', 'ERROR': 'warning'}


def _alarm_fact(alarm: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not alarm:
        return fact('Alarm', 'Not configured', status='stopped')
    name = alarm.get('name', '')
    return fact('Alarm', alarm['state'].replace('_', ' '), status=ALARM_STATUS.get(alarm['state'], 'info'),
                detail=alarm.get('error') or name,
                link=console_link('Console', console_url('cloudwatch', f'alarmsV2:alarm/{quote(name, safe="")}')) if name else None)


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


def get_scenarios() -> Dict[str, Any]:
    data = scenarios.load()
    # Is the skill registered in the Agent Space? The panel shows the state, not instructions.
    skills = [{**skill, 'registration': devops_agent.get_skill(skill['name'])} for skill in data['skills']]
    return {
        'success': True,
        **data,
        'skills': skills,
        'scenarios': [{**s, 'alarmName': scenarios.alarm_name(s)} for s in data['scenarios']],
        'environment': _environment(),
    }


def get_status() -> Dict[str, Any]:
    eng = _eng()
    known = {s['id'] for s in scenarios.all_scenarios()}
    running: Dict[str, Dict[str, Any]] = {}
    last_done: Dict[str, Dict[str, Any]] = {}
    for ex in eng.recent():
        sid = Engine.scenario_of(ex)
        if sid not in known:
            continue
        if ex.get('Status') == 'RUNNING':
            running.setdefault(sid, ex)
        elif sid not in last_done or (ex.get('StartTimestamp') or 0) > (last_done[sid].get('StartTimestamp') or 0):
            last_done[sid] = ex

    alarms = _alarms([scenarios.alarm_name(s) for s in scenarios.all_scenarios()])
    out: Dict[str, Any] = {}
    for s in scenarios.all_scenarios():
        sid = s['id']
        ops = handlers.HANDLERS.get(s.get('handler', ''))
        try:
            probe = ops.probe() if ops else {'injected': False, 'error': f"unknown handler {s.get('handler')!r}"}
        except Exception as e:
            probe = {'injected': False, 'error': str(e)}
        entry: Dict[str, Any] = {'injected': bool(probe.get('injected')), 'facts': list(probe.get('facts') or [])}
        if probe.get('error'):
            entry['error'] = probe['error']
        if s.get('triggersAlarm'):
            entry['facts'].append(_alarm_fact(alarms.get(scenarios.alarm_name(s))))
        if sid in running:
            entry['run'] = _phases(eng.run_view(running[sid]))
        elif sid in last_done:
            entry['lastRun'] = _phases(eng.run_view(last_done[sid]))
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
    if not os.environ.get('ENGINE_FUNCTION_ARN'):
        return {'success': False, 'message': 'ENGINE_FUNCTION_ARN not configured', 'statusCode': 500}
    try:
        started = _eng().start(scenario_id)
    except AlreadyRunning as busy:
        return {'success': False, 'statusCode': 409,
                'message': f'Scenario {Engine.scenario_of(busy.execution)} is already injected. '
                           'Roll it back first: one scenario at a time keeps the investigation unambiguous.'}
    timeout = scenarios.auto_revert_seconds(scenario)
    return {
        'success': True,
        'statusCode': 202,
        'message': f"{scenario['name']}: injection started. Auto-reverts in {timeout // 60} minutes unless rolled back.",
        'scenario': scenario_id,
        **started,
        'autoRevertSeconds': timeout,
    }


def rollback(scenario_id: str) -> Dict[str, Any]:
    scenario = scenarios.get(scenario_id)
    if scenario is None:
        return {'success': False, 'message': f'Unknown scenario {scenario_id!r}', 'statusCode': 404}

    eng = _eng()
    running = eng.running(scenario_id)
    if running:
        if eng.request_rollback(running):
            return {'success': True, 'message': f"{scenario['name']}: rollback requested, the engine is reverting.",
                    'scenario': scenario_id, 'executionArn': running['DurableExecutionArn']}
        return {'success': False, 'statusCode': 409, 'message': 'The injection is still starting; retry in a few seconds.'}

    # No run owns the failure (changed by hand, or a run that already ended): revert directly.
    ops = handlers.HANDLERS.get(scenario.get('handler', ''))
    if ops and ops.probe().get('injected'):
        return {'success': True, 'scenario': scenario_id, **ops.revert(), 'directRevert': True}
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
