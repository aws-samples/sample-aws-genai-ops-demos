"""
Local tests for the Lab engine (Lambda durable function) and the API routing.

The durable handler runs for real inside DurableFunctionTestRunner; only the
kubectl-backed handlers are mocked. What is under test is the orchestration:
inject -> wait for a rollback -> revert, on both roads (manual callback, timeout).

Run from cdk/lambda/failure-simulator-api:  python -m pytest tests -q
"""

import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LAMBDA_DIR = os.path.dirname(HERE)
sys.path.insert(0, LAMBDA_DIR)
os.environ.setdefault('AWS_REGION', 'eu-west-1')
os.environ.setdefault('AWS_DEFAULT_REGION', 'eu-west-1')
os.environ['SCENARIOS_FILE'] = os.path.join(LAMBDA_DIR, '..', '..', '..', 'lab', 'scenarios.yaml')

from aws_durable_execution_sdk_python.execution import InvocationStatus  # noqa: E402
from aws_durable_execution_sdk_python.lambda_service import OperationType  # noqa: E402
from aws_durable_execution_sdk_python_testing import DurableFunctionTestRunner  # noqa: E402

import engine  # noqa: E402
import k8s_ops  # noqa: E402
import scenarios  # noqa: E402


def _fake_handlers():
    calls = []
    fake = k8s_ops.Handler(
        inject=lambda: calls.append('inject') or {'message': 'broken'},
        revert=lambda: calls.append('revert') or {'message': 'fixed'},
        probe=lambda: {'injected': 'inject' in calls and 'revert' not in calls},
    )
    return calls, {name: fake for name in k8s_ops.HANDLERS}


def _step_names(res) -> set:
    return {op.name for op in res.get_all_operations() if op.operation_type == OperationType.STEP and op.name}


def _result(res) -> dict:
    return json.loads(res.result) if isinstance(res.result, str) else res.result


def test_scenarios_yaml_is_consistent():
    data = scenarios.load()
    assert data['schemaVersion'] == 1
    ids = [s['id'] for s in data['scenarios']]
    assert len(ids) == len(set(ids)) and ids, 'scenario ids must be unique'
    for s in data['scenarios']:
        assert s['handler'] in k8s_ops.HANDLERS, f"{s['id']}: handler {s['handler']!r} not in k8s_ops.HANDLERS"
        assert scenarios.auto_revert_seconds(s) > 0
        if s.get('triggersAlarm'):
            assert (s.get('alarm') or {}).get('envVar'), f"{s['id']}: triggersAlarm needs alarm.envVar"
        assert len(f"{s['id']}-{int(time.time())}") <= 64
        # An unquoted "text: more text" list item parses as a one-key mapping, which the UI
        # cannot render (React error #31). Every walkthrough line must be a plain string.
        for key in ('incidentChain', 'customerImpact', 'demoFlow'):
            for line in s.get(key) or []:
                assert isinstance(line, str), f"{s['id']}.{key}: quote this line in scenarios.yaml -> {line!r}"


def test_manual_rollback_resolves_callback_then_reverts():
    calls, handlers = _fake_handlers()
    with patch.dict(k8s_ops.HANDLERS, handlers, clear=True):
        runner = DurableFunctionTestRunner(handler=engine.handler, poll_interval=0.2)
        with runner:
            arn = runner.run_async(input=json.dumps({'scenarioId': 'db-connection-failure'}), timeout=60)
            # One callback per execution; its history event is not named after the wait step.
            callback_id = runner.wait_for_callback(arn, timeout=30)
            assert calls == ['inject'], 'revert must not run before the callback'
            runner.send_callback_success(callback_id, result=json.dumps({'reason': 'manual'}).encode())
            res = runner.wait_for_result(arn, timeout=30)

    assert res.status == InvocationStatus.SUCCEEDED
    assert calls == ['inject', 'revert']
    assert {engine.STEP_INJECT, engine.STEP_REVERT} <= _step_names(res)
    out = _result(res)
    assert out['revertReason'] == 'manual'
    assert out['scenarioId'] == 'db-connection-failure'


def test_timeout_auto_reverts():
    calls, handlers = _fake_handlers()
    short = {**scenarios.get('dns-resolution-failure'), 'autoRevertSeconds': 1}
    with patch.dict(k8s_ops.HANDLERS, handlers, clear=True), \
         patch.object(scenarios, 'get', return_value=short):
        runner = DurableFunctionTestRunner(handler=engine.handler, poll_interval=0.2)
        with runner:
            res = runner.run(input=json.dumps({'scenarioId': 'dns-resolution-failure'}), timeout=60)

    assert res.status == InvocationStatus.SUCCEEDED
    assert calls == ['inject', 'revert']
    assert _result(res)['revertReason'] == 'auto'


def test_unknown_scenario_fails_before_touching_the_cluster():
    calls, handlers = _fake_handlers()
    with patch.dict(k8s_ops.HANDLERS, handlers, clear=True):
        runner = DurableFunctionTestRunner(handler=engine.handler, poll_interval=0.2)
        with runner:
            res = runner.run(input=json.dumps({'scenarioId': 'nope'}), timeout=30)
    assert res.status == InvocationStatus.FAILED
    assert calls == []


# ---------------------------------------------------------------------------
# API routing (index.py) with the Lambda control plane mocked
# ---------------------------------------------------------------------------

@pytest.fixture
def api():
    import index
    index.ENGINE_FUNCTION_ARN = 'arn:aws:lambda:eu-west-1:123456789012:function:lab-engine:live'
    fake_lambda = MagicMock()
    with patch.object(index, '_lambda_client', return_value=fake_lambda):
        yield index, fake_lambda


def _event(method, path):
    return {'httpMethod': method, 'path': path}


def test_inject_refused_while_another_scenario_runs(api):
    index, fake_lambda = api
    fake_lambda.list_durable_executions_by_function.return_value = {'DurableExecutions': [
        {'DurableExecutionName': 'dns-resolution-failure-1700000000', 'DurableExecutionArn': 'arn:x', 'Status': 'RUNNING'}]}
    resp = index.handler(_event('POST', '/admin/scenarios/db-connection-failure/inject'), None)
    assert resp['statusCode'] == 409
    fake_lambda.invoke.assert_not_called()


def test_inject_starts_a_uniquely_named_execution(api):
    index, fake_lambda = api
    fake_lambda.list_durable_executions_by_function.return_value = {'DurableExecutions': []}
    fake_lambda.invoke.return_value = {'DurableExecutionArn': 'arn:run'}
    resp = index.handler(_event('POST', '/admin/scenarios/db-connection-failure/inject'), None)
    assert resp['statusCode'] == 202
    kwargs = fake_lambda.invoke.call_args.kwargs
    assert kwargs['FunctionName'].endswith(':live')
    assert kwargs['InvocationType'] == 'Event'
    assert kwargs['DurableExecutionName'].startswith('db-connection-failure-')
    assert json.loads(kwargs['Payload']) == {'scenarioId': 'db-connection-failure'}


def test_rollback_resolves_the_callback_of_the_running_execution(api):
    index, fake_lambda = api
    fake_lambda.list_durable_executions_by_function.return_value = {'DurableExecutions': [
        {'DurableExecutionName': 'db-connection-failure-1700000000', 'DurableExecutionArn': 'arn:run', 'Status': 'RUNNING'}]}
    paginator = MagicMock()
    paginator.paginate.return_value = [{'Events': [
        {'EventType': 'StepSucceeded', 'Name': 'inject'},
        {'EventType': 'CallbackStarted', 'Name': 'await-rollback', 'CallbackStartedDetails': {'CallbackId': 'cb-1', 'Timeout': 600}},
    ]}]
    fake_lambda.get_paginator.return_value = paginator
    resp = index.handler(_event('DELETE', '/admin/scenarios/db-connection-failure/inject'), None)
    assert resp['statusCode'] == 200
    fake_lambda.send_durable_execution_callback_success.assert_called_once()
    assert fake_lambda.send_durable_execution_callback_success.call_args.kwargs['CallbackId'] == 'cb-1'


def test_status_never_filters_on_more_than_one_execution_status(api):
    """ListDurableExecutionsByFunction rejects multi-status filters (InvalidParameterValueException)."""
    index, fake_lambda = api
    fake_lambda.list_durable_executions_by_function.return_value = {'DurableExecutions': []}
    with patch.object(index.k8s_ops, 'HANDLERS', {}), patch.object(index, '_alarms', return_value={}):
        resp = index.handler(_event('GET', '/admin/status'), None)
    assert resp['statusCode'] == 200
    for call in fake_lambda.list_durable_executions_by_function.call_args_list:
        assert len(call.kwargs.get('Statuses', [])) <= 1, call.kwargs


def test_status_returns_probe_facts_plus_alarm_fact(api):
    """The UI knows nothing about the domain: everything it shows is a labelled fact."""
    index, fake_lambda = api
    fake_lambda.list_durable_executions_by_function.return_value = {'DurableExecutions': []}
    probe = lambda: {'injected': True, 'facts': [{'label': 'Thing', 'value': 'broken', 'status': 'error'}]}
    handlers = {name: index.k8s_ops.Handler(lambda: {}, lambda: {}, probe) for name in index.k8s_ops.HANDLERS}
    with patch.object(index.k8s_ops, 'HANDLERS', handlers), \
         patch.object(index, '_alarms', return_value={'x': {'name': 'x', 'state': 'ALARM', 'reason': ''}}), \
         patch.object(index.scenarios, 'alarm_name', return_value='x'):
        body = json.loads(index.handler(_event('GET', '/admin/status'), None)['body'])
    for sid, st in body['scenarios'].items():
        assert st['injected'] is True
        labels = [f['label'] for f in st['facts']]
        assert labels[0] == 'Thing'
        assert 'Alarm' in labels, f'{sid}: alarm-driven scenarios get an Alarm fact'
        assert all('label' in f for f in st['facts'])
        assert 'pods' not in st and 'deployment' not in st


def test_unknown_route_is_404(api):
    index, _ = api
    assert index.handler(_event('GET', '/admin/nope'), None)['statusCode'] == 404
    assert index.handler(_event('POST', '/admin/scenarios/nope/inject'), None)['statusCode'] == 404
