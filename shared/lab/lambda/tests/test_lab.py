"""
Tests for the shared Lab: engine (Lambda durable function), API routing, YAML validation.

The durable handler runs for real inside DurableFunctionTestRunner against the fixture
handlers (tests/fixtures/handlers.py), which record calls and touch nothing. What is
under test is the orchestration: inject -> wait for a rollback -> revert, on both roads
(manual callback, timeout), plus the API rules every demo relies on.

Run from shared/lab/lambda:  python -m pytest tests -q
Validate a demo's own lab/ folder: python validate.py <demo>/lab
"""

import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LAMBDA_DIR = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, 'fixtures')
sys.path.insert(0, LAMBDA_DIR)
sys.path.insert(0, FIXTURES)
os.environ.setdefault('AWS_REGION', 'eu-west-1')
os.environ.setdefault('AWS_DEFAULT_REGION', 'eu-west-1')
os.environ['SCENARIOS_FILE'] = os.path.join(FIXTURES, 'scenarios.yaml')

from aws_durable_execution_sdk_python.execution import InvocationStatus  # noqa: E402
from aws_durable_execution_sdk_python.lambda_service import OperationType  # noqa: E402
from aws_durable_execution_sdk_python_testing import DurableFunctionTestRunner  # noqa: E402

import engine  # noqa: E402
import handlers  # noqa: E402  (the fixture module)
import scenarios  # noqa: E402
import validate  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_calls():
    handlers.CALLS.clear()
    yield
    handlers.CALLS.clear()


def _step_names(res) -> set:
    return {op.name for op in res.get_all_operations() if op.operation_type == OperationType.STEP and op.name}


def _result(res) -> dict:
    return json.loads(res.result) if isinstance(res.result, str) else res.result


# ---------------------------------------------------------------------------
# scenarios.yaml validation (what validate.py enforces for every demo)
# ---------------------------------------------------------------------------

def test_fixture_yaml_passes_validation():
    assert validate.problems(scenarios.load(), handlers) == []


def test_validation_catches_the_known_mistakes():
    data = scenarios.load()
    broken = json.loads(json.dumps(data))
    broken['scenarios'][0]['handler'] = 'missing'
    broken['scenarios'][0]['demoFlow'] = [{'Open the console': 'unquoted colon'}]
    broken['scenarios'][1]['triggersAlarm'] = True          # no alarm.envVar
    broken['scenarios'][1]['id'] = broken['scenarios'][0]['id']
    del broken['scenarios'][0]['demonstrates']['withoutCapability']
    text = '\n'.join(validate.problems(broken, handlers))
    for expected in ('unknown handler', 'quote', 'alarm.envVar', 'duplicate', 'withoutCapability'):
        assert expected in text, f'expected a problem mentioning {expected!r}:\n{text}'


# ---------------------------------------------------------------------------
# Engine: both roads to `revert`
# ---------------------------------------------------------------------------

def test_manual_rollback_resolves_callback_then_reverts():
    runner = DurableFunctionTestRunner(handler=engine.handler, poll_interval=0.2)
    with runner:
        arn = runner.run_async(input=json.dumps({'scenarioId': 'thing-breaks'}), timeout=60)
        # One callback per execution; its history event is not named after the wait step.
        callback_id = runner.wait_for_callback(arn, timeout=30)
        assert handlers.CALLS == ['inject'], 'revert must not run before the callback'
        runner.send_callback_success(callback_id, result=json.dumps({'reason': 'manual'}).encode())
        res = runner.wait_for_result(arn, timeout=30)

    assert res.status == InvocationStatus.SUCCEEDED
    assert handlers.CALLS == ['inject', 'revert']
    assert {engine.STEP_INJECT, engine.STEP_REVERT} <= _step_names(res)
    out = _result(res)
    assert out['revertReason'] == 'manual'
    assert out['scenarioId'] == 'thing-breaks'


def test_timeout_auto_reverts():
    short = {**scenarios.get('other-thing-breaks'), 'autoRevertSeconds': 1}
    with patch.object(scenarios, 'get', return_value=short):
        runner = DurableFunctionTestRunner(handler=engine.handler, poll_interval=0.2)
        with runner:
            res = runner.run(input=json.dumps({'scenarioId': 'other-thing-breaks'}), timeout=60)

    assert res.status == InvocationStatus.SUCCEEDED
    assert handlers.CALLS == ['inject', 'revert']
    assert _result(res)['revertReason'] == 'auto'


def test_unknown_scenario_fails_before_touching_the_environment():
    runner = DurableFunctionTestRunner(handler=engine.handler, poll_interval=0.2)
    with runner:
        res = runner.run(input=json.dumps({'scenarioId': 'nope'}), timeout=30)
    assert res.status == InvocationStatus.FAILED
    assert handlers.CALLS == []


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
        {'DurableExecutionName': 'other-thing-breaks-1700000000', 'DurableExecutionArn': 'arn:x', 'Status': 'RUNNING'}]}
    resp = index.handler(_event('POST', '/admin/scenarios/thing-breaks/inject'), None)
    assert resp['statusCode'] == 409
    fake_lambda.invoke.assert_not_called()


def test_inject_starts_a_uniquely_named_execution(api):
    index, fake_lambda = api
    fake_lambda.list_durable_executions_by_function.return_value = {'DurableExecutions': []}
    fake_lambda.invoke.return_value = {'DurableExecutionArn': 'arn:run'}
    resp = index.handler(_event('POST', '/admin/scenarios/thing-breaks/inject'), None)
    assert resp['statusCode'] == 202
    kwargs = fake_lambda.invoke.call_args.kwargs
    assert kwargs['FunctionName'].endswith(':live')
    assert kwargs['InvocationType'] == 'Event'
    assert kwargs['DurableExecutionName'].startswith('thing-breaks-')
    assert json.loads(kwargs['Payload']) == {'scenarioId': 'thing-breaks'}


def test_rollback_resolves_the_callback_of_the_running_execution(api):
    index, fake_lambda = api
    fake_lambda.list_durable_executions_by_function.return_value = {'DurableExecutions': [
        {'DurableExecutionName': 'thing-breaks-1700000000', 'DurableExecutionArn': 'arn:run', 'Status': 'RUNNING'}]}
    paginator = MagicMock()
    paginator.paginate.return_value = [{'Events': [
        {'EventType': 'StepSucceeded', 'Name': 'inject'},
        {'EventType': 'CallbackStarted', 'Name': 'await-rollback', 'CallbackStartedDetails': {'CallbackId': 'cb-1', 'Timeout': 600}},
    ]}]
    fake_lambda.get_paginator.return_value = paginator
    resp = index.handler(_event('DELETE', '/admin/scenarios/thing-breaks/inject'), None)
    assert resp['statusCode'] == 200
    fake_lambda.send_durable_execution_callback_success.assert_called_once()
    assert fake_lambda.send_durable_execution_callback_success.call_args.kwargs['CallbackId'] == 'cb-1'


def test_status_never_filters_on_more_than_one_execution_status(api):
    """ListDurableExecutionsByFunction rejects multi-status filters (InvalidParameterValueException)."""
    index, fake_lambda = api
    fake_lambda.list_durable_executions_by_function.return_value = {'DurableExecutions': []}
    with patch.object(index, '_alarms', return_value={}):
        resp = index.handler(_event('GET', '/admin/status'), None)
    assert resp['statusCode'] == 200
    for call in fake_lambda.list_durable_executions_by_function.call_args_list:
        assert len(call.kwargs.get('Statuses', [])) <= 1, call.kwargs


def test_status_returns_probe_facts_plus_alarm_fact(api):
    """The UI knows nothing about the domain: everything it shows is a labelled fact."""
    index, fake_lambda = api
    fake_lambda.list_durable_executions_by_function.return_value = {'DurableExecutions': []}
    os.environ['THING_ALARM_NAME'] = 'thing-alarm'
    with patch.object(index, '_alarms', return_value={'thing-alarm': {'name': 'thing-alarm', 'state': 'ALARM', 'reason': ''}}):
        body = json.loads(index.handler(_event('GET', '/admin/status'), None)['body'])
    st = body['scenarios']['thing-breaks']
    labels = [f['label'] for f in st['facts']]
    assert labels == ['Thing', 'Alarm'], 'probe facts first, then the alarm fact for alarm-driven scenarios'
    assert st['facts'][1]['status'] == 'error'
    assert [f['label'] for f in body['scenarios']['other-thing-breaks']['facts']] == ['Thing'], 'no alarm fact without triggersAlarm'
    assert [f['label'] for f in body['environment']['facts']] == ['Environment']


def test_scenarios_route_serves_the_yaml_with_alarm_names(api):
    index, _ = api
    os.environ['THING_ALARM_NAME'] = 'thing-alarm'
    body = json.loads(index.handler(_event('GET', '/admin/scenarios'), None)['body'])
    assert [s['id'] for s in body['scenarios']] == ['thing-breaks', 'other-thing-breaks']
    assert body['scenarios'][0]['alarmName'] == 'thing-alarm'
    assert body['skills'][0]['source'] == 'agent-tools'
    assert body['capability']['prompt']


def test_unknown_route_is_404(api):
    index, _ = api
    assert index.handler(_event('GET', '/admin/nope'), None)['statusCode'] == 404
    assert index.handler(_event('POST', '/admin/scenarios/nope/inject'), None)['statusCode'] == 404
