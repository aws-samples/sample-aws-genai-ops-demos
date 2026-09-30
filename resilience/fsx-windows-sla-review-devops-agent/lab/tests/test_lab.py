"""
Tests for the FSx demo's Lab: the shared durable engine wired to this demo's scenarios,
the API rules the UI relies on, and the scenarios.yaml consistency.

The durable handler runs for real inside DurableFunctionTestRunner; only the FSx-backed
handlers are replaced by recorders. Under test: the orchestration (inject -> wait for a
rollback -> revert, on both roads) and the API contract (Lambda function URL events).

Run from the demo's lab/ folder:  python -m pytest tests -q
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LAB_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.abspath(os.path.join(LAB_DIR, '..', '..', '..'))
sys.path.insert(0, LAB_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, 'shared', 'devops-agent', 'lab', 'lambda'))
os.environ.setdefault('AWS_REGION', 'eu-west-1')
os.environ.setdefault('AWS_DEFAULT_REGION', 'eu-west-1')
os.environ['SCENARIOS_FILE'] = os.path.join(LAB_DIR, 'scenarios.yaml')
os.environ['FILE_SYSTEM_ID'] = 'fs-0123456789abcdef0'
os.environ['FREE_STORAGE_ALARM_NAME'] = 'test-free-storage-capacity'
os.environ['MISCONFIGURED_ALARM_NAME'] = 'test-file-system-misconfigured'

from aws_durable_execution_sdk_python.execution import InvocationStatus  # noqa: E402
from aws_durable_execution_sdk_python.lambda_service import OperationType  # noqa: E402
from aws_durable_execution_sdk_python_testing import DurableFunctionTestRunner  # noqa: E402

import engine  # noqa: E402  (shared)
import engine_main  # noqa: E402
import handlers  # noqa: E402
import scenarios  # noqa: E402


def _fake_handlers():
    calls = []
    fake = handlers.Handler(
        inject=lambda: calls.append('inject') or {'message': 'broken'},
        revert=lambda: calls.append('revert') or {'message': 'fixed'},
        probe=lambda: {'injected': 'inject' in calls and 'revert' not in calls, 'facts': []},
    )
    return calls, {name: fake for name in handlers.HANDLERS}


def _step_names(res) -> set:
    return {op.name for op in res.get_all_operations() if op.operation_type == OperationType.STEP and op.name}


def _result(res) -> dict:
    return json.loads(res.result) if isinstance(res.result, str) else res.result


# ---------------------------------------------------------------------------
# scenarios.yaml is the single source of truth: keep it consistent with handlers.py
# ---------------------------------------------------------------------------

def test_scenarios_yaml_is_consistent():
    data = scenarios.load()
    assert data['schemaVersion'] == 1
    ids = [s['id'] for s in data['scenarios']]
    assert len(ids) == len(set(ids)) and ids, 'scenario ids must be unique'
    for s in data['scenarios']:
        assert s['handler'] in handlers.HANDLERS, f"{s['id']}: handler {s['handler']!r} not in handlers.HANDLERS"
        assert scenarios.auto_revert_seconds(s) > 0
        if s.get('triggersAlarm'):
            assert (s.get('alarm') or {}).get('envVar'), f"{s['id']}: triggersAlarm needs alarm.envVar"
        assert len(engine.Engine.execution_name(s['id'])) <= 64
        for k in ('check', 'withCapability', 'withoutCapability'):
            assert (s.get('demonstrates') or {}).get(k), f"{s['id']}: demonstrates.{k} (no difference, no scenario)"
        # An unquoted "text: more text" list item parses as a one-key mapping, which the UI
        # cannot render (React error #31). Every walkthrough line must be a plain string.
        for key in ('incidentChain', 'customerImpact', 'demoFlow'):
            for line in s.get(key) or []:
                assert isinstance(line, str), f"{s['id']}.{key}: quote this line in scenarios.yaml -> {line!r}"
    assert set(handlers.HANDLERS) == {s['handler'] for s in data['scenarios']}, 'every handler belongs to a scenario'


def test_alarm_driven_scenario_names_the_canary_alarm():
    s = scenarios.get('misconfigured-ad-credentials')
    assert s['triggersAlarm'] and scenarios.alarm_name(s) == 'test-file-system-misconfigured'
    assert not any(scenarios.alarm_name(o) for o in scenarios.all_scenarios() if o['id'] != s['id'])


# ---------------------------------------------------------------------------
# Engine: both roads to `revert`
# ---------------------------------------------------------------------------

def test_manual_rollback_resolves_callback_then_reverts():
    calls, fakes = _fake_handlers()
    with patch.dict(handlers.HANDLERS, fakes, clear=True):
        runner = DurableFunctionTestRunner(handler=engine_main.handler, poll_interval=0.2)
        with runner:
            arn = runner.run_async(input=json.dumps({'scenarioId': 'backups-disabled'}), timeout=60)
            callback_id = runner.wait_for_callback(arn, timeout=30)
            assert calls == ['inject'], 'revert must not run before the callback'
            runner.send_callback_success(callback_id, result=json.dumps({'reason': 'manual'}).encode())
            res = runner.wait_for_result(arn, timeout=30)

    assert res.status == InvocationStatus.SUCCEEDED
    assert calls == ['inject', 'revert']
    assert {engine.STEP_INJECT, engine.STEP_REVERT} <= _step_names(res)
    out = _result(res)
    assert out['revertReason'] == 'manual'
    assert out['scenarioId'] == 'backups-disabled'


def test_timeout_auto_reverts():
    calls, fakes = _fake_handlers()
    short = {**scenarios.get('alarms-removed'), 'autoRevertSeconds': 1}
    with patch.dict(handlers.HANDLERS, fakes, clear=True), patch.object(scenarios, 'get', return_value=short):
        runner = DurableFunctionTestRunner(handler=engine_main.handler, poll_interval=0.2)
        with runner:
            res = runner.run(input=json.dumps({'scenarioId': 'alarms-removed'}), timeout=60)

    assert res.status == InvocationStatus.SUCCEEDED
    assert calls == ['inject', 'revert']
    assert _result(res)['revertReason'] == 'auto'


def test_unknown_scenario_fails_before_touching_the_file_system():
    calls, fakes = _fake_handlers()
    with patch.dict(handlers.HANDLERS, fakes, clear=True):
        runner = DurableFunctionTestRunner(handler=engine_main.handler, poll_interval=0.2)
        with runner:
            res = runner.run(input=json.dumps({'scenarioId': 'nope'}), timeout=30)
    assert res.status == InvocationStatus.FAILED
    assert calls == []


# ---------------------------------------------------------------------------
# Handlers: the alarm the Lab recreates matches what the review looks for
# ---------------------------------------------------------------------------

def test_recreated_alarm_is_an_aws_fsx_alarm_scoped_to_the_file_system():
    os.environ['FREE_STORAGE_THRESHOLD_BYTES'] = str(int(32 * 1024 ** 3 * 0.2))
    os.environ['ALARMS_TOPIC_ARN'] = 'arn:aws:sns:eu-west-1:123456789012:alarms'
    cw = MagicMock()
    with patch.dict(handlers._clients, {'cloudwatch': cw}, clear=True), \
         patch.object(handlers, 'FREE_STORAGE_THRESHOLD_BYTES', int(32 * 1024 ** 3 * 0.2)), \
         patch.object(handlers, 'ALARMS_TOPIC_ARN', os.environ['ALARMS_TOPIC_ARN']):
        handlers.revert_alarms_removed()
    kwargs = cw.put_metric_alarm.call_args.kwargs
    assert kwargs['Namespace'] == 'AWS/FSx' and kwargs['MetricName'] == 'FreeStorageCapacity'
    assert kwargs['Dimensions'] == [{'Name': 'FileSystemId', 'Value': 'fs-0123456789abcdef0'}]
    assert kwargs['AlarmName'] == 'test-free-storage-capacity'
    assert kwargs['Threshold'] == int(32 * 1024 ** 3 * 0.2) and kwargs['ComparisonOperator'] == 'LessThanThreshold'
    assert kwargs['AlarmActions'] == [os.environ['ALARMS_TOPIC_ARN']]


def test_alarm_probe_counts_only_aws_fsx_alarms_on_this_file_system():
    cw = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{'MetricAlarms': [
        {'AlarmName': 'test-free-storage-capacity', 'Namespace': 'AWS/FSx', 'StateValue': 'OK',
         'Dimensions': [{'Name': 'FileSystemId', 'Value': 'fs-0123456789abcdef0'}]},
        {'AlarmName': 'other-file-system', 'Namespace': 'AWS/FSx', 'StateValue': 'OK',
         'Dimensions': [{'Name': 'FileSystemId', 'Value': 'fs-other'}]},
        {'AlarmName': 'test-file-system-misconfigured', 'Namespace': 'fsx-sla-review', 'StateValue': 'OK',
         'Dimensions': [{'Name': 'FileSystemId', 'Value': 'fs-0123456789abcdef0'}]},
    ]}]
    cw.get_paginator.return_value = paginator
    with patch.dict(handlers._clients, {'cloudwatch': cw}, clear=True):
        probe = handlers.probe_alarms_removed()
    assert probe['injected'] is False
    listed = next(f for f in probe['facts'] if f['label'].startswith('AWS/FSx alarms'))
    assert [i['text'] for i in listed['items']] == ['test-free-storage-capacity']

    paginator.paginate.return_value = [{'MetricAlarms': []}]
    with patch.dict(handlers._clients, {'cloudwatch': cw}, clear=True):
        probe = handlers.probe_alarms_removed()
    assert probe['injected'] is True
    assert probe['facts'][0]['value'] == 'Not found' and probe['facts'][0]['status'] == 'error'


def test_credentials_probe_reads_the_lifecycle_not_a_flag():
    fsx = MagicMock()
    fsx.describe_file_systems.return_value = {'FileSystems': [{
        'FileSystemId': 'fs-0123456789abcdef0', 'Lifecycle': 'MISCONFIGURED',
        'FailureDetails': {'Message': 'ACTIVE_DIRECTORY_INVALID_CREDENTIALS: ...'},
        'WindowsConfiguration': {'SelfManagedActiveDirectoryConfiguration': {'DomainName': 'corp.example.com', 'UserName': 'FSxService', 'DnsIps': ['10.0.1.10']}},
        'AdministrativeActions': [],
    }]}
    with patch.dict(handlers._clients, {'fsx': fsx}, clear=True):
        probe = handlers.probe_misconfigured_ad_credentials()
    assert probe['injected'] is True
    lifecycle = probe['facts'][0]
    assert lifecycle['label'] == 'Lifecycle' and lifecycle['status'] == 'error' and 'INVALID_CREDENTIALS' in lifecycle['detail']

    fsx.describe_file_systems.return_value['FileSystems'][0].update({'Lifecycle': 'AVAILABLE', 'FailureDetails': None})
    with patch.dict(handlers._clients, {'fsx': fsx}, clear=True):
        assert handlers.probe_misconfigured_ad_credentials()['injected'] is False


# ---------------------------------------------------------------------------
# API (api.py) with the Lambda control plane mocked; function URL (payload 2.0) events
# ---------------------------------------------------------------------------

@pytest.fixture
def api():
    os.environ['ENGINE_FUNCTION_ARN'] = 'arn:aws:lambda:eu-west-1:123456789012:function:lab-engine:live'
    import api as api_module
    fake_lambda = MagicMock()
    api_module._engine = engine.Engine(os.environ['ENGINE_FUNCTION_ARN'], lambda_client=fake_lambda)
    yield api_module, fake_lambda
    api_module._engine = None


def _event(method, path):
    return {'rawPath': path, 'requestContext': {'http': {'method': method, 'path': path}}}


def test_inject_refused_while_another_scenario_runs(api):
    index, fake_lambda = api
    fake_lambda.list_durable_executions_by_function.return_value = {'DurableExecutions': [
        {'DurableExecutionName': 'alarms-removed-1700000000', 'DurableExecutionArn': 'arn:x', 'Status': 'RUNNING'}]}
    resp = index.handler(_event('POST', '/admin/scenarios/backups-disabled/inject'), None)
    assert resp['statusCode'] == 409
    fake_lambda.invoke.assert_not_called()


def test_inject_starts_a_uniquely_named_execution(api):
    index, fake_lambda = api
    fake_lambda.list_durable_executions_by_function.return_value = {'DurableExecutions': []}
    fake_lambda.invoke.return_value = {'DurableExecutionArn': 'arn:run'}
    resp = index.handler(_event('POST', '/admin/scenarios/misconfigured-ad-credentials/inject'), None)
    assert resp['statusCode'] == 202
    kwargs = fake_lambda.invoke.call_args.kwargs
    assert kwargs['FunctionName'].endswith(':live')
    assert kwargs['InvocationType'] == 'Event'
    assert kwargs['DurableExecutionName'].startswith('misconfigured-ad-credentials-')
    assert json.loads(kwargs['Payload']) == {'scenarioId': 'misconfigured-ad-credentials'}


def test_rollback_resolves_the_callback_of_the_running_execution(api):
    index, fake_lambda = api
    fake_lambda.list_durable_executions_by_function.return_value = {'DurableExecutions': [
        {'DurableExecutionName': 'backups-disabled-1700000000', 'DurableExecutionArn': 'arn:run', 'Status': 'RUNNING'}]}
    paginator = MagicMock()
    paginator.paginate.return_value = [{'Events': [
        {'EventType': 'StepSucceeded', 'Name': 'inject'},
        {'EventType': 'CallbackStarted', 'Name': 'await-rollback', 'CallbackStartedDetails': {'CallbackId': 'cb-1', 'Timeout': 600}},
    ]}]
    fake_lambda.get_paginator.return_value = paginator
    resp = index.handler(_event('DELETE', '/admin/scenarios/backups-disabled/inject'), None)
    assert resp['statusCode'] == 200
    fake_lambda.send_durable_execution_callback_success.assert_called_once()
    assert fake_lambda.send_durable_execution_callback_success.call_args.kwargs['CallbackId'] == 'cb-1'


def test_status_returns_probe_facts_plus_alarm_fact_and_run_phases(api):
    index, fake_lambda = api
    fake_lambda.list_durable_executions_by_function.return_value = {'DurableExecutions': [
        {'DurableExecutionName': 'misconfigured-ad-credentials-1700000000', 'DurableExecutionArn': 'arn:run', 'Status': 'RUNNING'}]}
    paginator = MagicMock()
    paginator.paginate.return_value = [{'Events': [
        {'EventType': 'StepSucceeded', 'Name': 'inject'},
        {'EventType': 'CallbackStarted', 'Name': 'await-rollback', 'CallbackStartedDetails': {'CallbackId': 'cb-1', 'Timeout': 600}},
    ]}]
    fake_lambda.get_paginator.return_value = paginator
    probe = lambda: {'injected': True, 'facts': [{'label': 'Lifecycle', 'value': 'MISCONFIGURED', 'status': 'error'}]}
    fakes = {name: handlers.Handler(lambda: {}, lambda: {}, probe) for name in handlers.HANDLERS}
    with patch.object(index.handlers, 'HANDLERS', fakes), \
         patch.object(index.handlers, 'environment', return_value=[]), \
         patch.object(index, '_alarms', return_value={'test-file-system-misconfigured': {'name': 'test-file-system-misconfigured', 'state': 'ALARM', 'reason': ''}}):
        body = json.loads(index.handler(_event('GET', '/admin/status'), None)['body'])
    assert body['busy'] == 'misconfigured-ad-credentials'
    st = body['scenarios']['misconfigured-ad-credentials']
    assert [f['label'] for f in st['facts']] == ['Lifecycle', 'Alarm']
    assert st['facts'][1]['status'] == 'error'
    assert [p['id'] for p in st['run']['phases']] == ['inject', 'await-rollback', 'revert']
    assert [p['status'] for p in st['run']['phases']] == ['success', 'in-progress', 'pending']
    # Chat-only scenarios carry no alarm fact.
    assert [f['label'] for f in body['scenarios']['backups-disabled']['facts']] == ['Lifecycle']
    for call in fake_lambda.list_durable_executions_by_function.call_args_list:
        assert len(call.kwargs.get('Statuses', [])) <= 1, call.kwargs


def test_scenarios_route_fills_the_region_into_the_prompt(api):
    index, _ = api
    with patch.object(index.handlers, 'environment', return_value=[]):
        body = json.loads(index.handler(_event('GET', '/admin/scenarios'), None)['body'])
    assert '{region}' not in body['capability']['prompt'] and index.REGION in body['capability']['prompt']
    assert [s['id'] for s in body['scenarios']] == ['misconfigured-ad-credentials', 'backups-disabled', 'alarms-removed']
    assert body['scenarios'][0]['alarmName'] == 'test-file-system-misconfigured'


def test_unknown_route_is_404(api):
    index, _ = api
    assert index.handler(_event('GET', '/admin/nope'), None)['statusCode'] == 404
    assert index.handler(_event('POST', '/admin/scenarios/nope/inject'), None)['statusCode'] == 404
