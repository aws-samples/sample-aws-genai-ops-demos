"""
Tests for the Aurora demo's Lab: the shared durable engine wired to this demo's scenarios,
the handlers against mocked AWS clients, the API rules the UI relies on, and the
scenarios.yaml consistency.

The durable handler runs for real inside DurableFunctionTestRunner; only the AWS-backed
handlers are replaced by recorders. Under test: the orchestration (inject -> wait for a
rollback -> revert, on both roads), what the handlers send to RDS / Lambda / CloudWatch
(including that a revert restores exactly the tags CDK created), and the API contract
(Lambda function URL events).

Not under test, and cannot be here: whether the real services accept these calls. That is
the live deploy's job (see build notes).

Run from the demo's lab/ folder:  python -m pytest tests -q
"""

import json
import os
import re
import sys
from unittest.mock import MagicMock, patch

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LAB_DIR = os.path.dirname(HERE)
DEMO_DIR = os.path.dirname(LAB_DIR)
REPO_ROOT = os.path.abspath(os.path.join(DEMO_DIR, '..', '..'))
sys.path.insert(0, LAB_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, 'shared', 'devops-agent', 'lab', 'lambda'))


def _cdk_business_tags() -> dict:
    """BUSINESS_TAGS as cdk/lib/constants.ts defines them: what the deploy puts on the cluster."""
    with open(os.path.join(DEMO_DIR, 'cdk', 'lib', 'constants.ts'), encoding='utf-8') as f:
        source = f.read()
    block = re.search(r'BUSINESS_TAGS[^=]*=\s*\{(.*?)\};', source, re.S).group(1)
    return dict(re.findall(r"(\w+):\s*'([^']*)'", block))


CDK_TAGS = _cdk_business_tags()


def _cdk_constant(name: str) -> int:
    """A numeric constant from cdk/lib/constants.ts (what the deploy uses)."""
    with open(os.path.join(DEMO_DIR, 'cdk', 'lib', 'constants.ts'), encoding='utf-8') as f:
        return int(re.search(rf'export const {name}\s*=\s*(\d+);', f.read()).group(1))

os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')
os.environ['SCENARIOS_FILE'] = os.path.join(LAB_DIR, 'scenarios.yaml')
os.environ['CLUSTER_ID'] = 'aurora-bizctx-cluster'
os.environ['CLUSTER_ARN'] = 'arn:aws:rds:us-east-1:123456789012:cluster:aurora-bizctx-cluster'
os.environ['WRITER_INSTANCE_ID'] = 'aurora-bizctx-writer'
os.environ['LOAD_FUNCTION_NAME'] = 'aurora-bizctx-connection-load'
os.environ['CONNECTIONS_ALARM_NAME'] = 'aurora-bizctx-writer-database-connections'
os.environ['CONNECTIONS_THRESHOLD'] = '60'
os.environ['BASELINE_TAGS'] = json.dumps(CDK_TAGS)

from aws_durable_execution_sdk_python.execution import InvocationStatus  # noqa: E402
from aws_durable_execution_sdk_python.lambda_service import OperationType  # noqa: E402
from aws_durable_execution_sdk_python_testing import DurableFunctionTestRunner  # noqa: E402

import engine  # noqa: E402  (shared)
import engine_main  # noqa: E402
import handlers  # noqa: E402
import scenarios  # noqa: E402

TALK_TRACK_STORM = ('The connection storm is the technical cause. The skill adds what it costs per minute, '
                    'when the SLA breaks, and that the list of affected services comes from a tag nobody has verified.')


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


class FakeAws:
    """RDS tags, the load function's reserved concurrency and the connections metric, in memory."""

    def __init__(self, tags=None, concurrency=0, connections=None):
        self.tags = dict(CDK_TAGS if tags is None else tags)
        self.concurrency = concurrency
        self.metric = list(connections or [])      # successive get_metric_data values (last one repeats)
        self.rds = MagicMock()
        self.rds.list_tags_for_resource.side_effect = lambda **_: {'TagList': [{'Key': k, 'Value': v} for k, v in self.tags.items()]}
        self.rds.add_tags_to_resource.side_effect = lambda **kw: self.tags.update({t['Key']: t['Value'] for t in kw['Tags']})
        self.rds.remove_tags_from_resource.side_effect = lambda **kw: [self.tags.pop(k, None) for k in kw['TagKeys']]
        self.lam = MagicMock()
        self.lam.get_function_concurrency.side_effect = lambda **_: {'ReservedConcurrentExecutions': self.concurrency} if self.concurrency is not None else {}
        self.lam.put_function_concurrency.side_effect = lambda **kw: setattr(self, 'concurrency', kw['ReservedConcurrentExecutions'])
        self.cw = MagicMock()
        self.cw.get_metric_data.side_effect = self._metric

    def _metric(self, **_):
        value = self.metric.pop(0) if len(self.metric) > 1 else (self.metric[0] if self.metric else None)
        return {'MetricDataResults': [{'Id': 'connections', 'Values': [] if value is None else [value]}]}

    def clients(self):
        return {'rds': self.rds, 'lambda': self.lam, 'cloudwatch': self.cw}


@pytest.fixture
def aws():
    fake = FakeAws()
    with patch.dict(handlers._clients, fake.clients(), clear=True), patch.object(handlers.time, 'sleep'):
        yield fake


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
        ops = handlers.HANDLERS[s['handler']]
        assert all(callable(f) for f in (ops.inject, ops.revert, ops.probe)), f"{s['id']}: the inject/revert/probe trio"
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
    assert data['capability']['ref'] == data['skills'][0]['ref'], 'one pinned ref for the skill'
    assert re.fullmatch(r'[0-9a-f]{40}', data['capability']['ref']), 'the skill is pinned to a commit'


def test_auto_revert_fits_the_engine_and_the_load_safety_net():
    """Engine execution ceiling is 1 hour (LabEngine default); the load function stops itself after
    LOAD_MAX_STORM_SECONDS (constants.ts), which must outlast every auto-revert plus the settle wait."""
    safety_net = _cdk_constant('LOAD_MAX_STORM_SECONDS')
    for s in scenarios.all_scenarios():
        assert scenarios.auto_revert_seconds(s) < 3600, s['id']
        if s.get('triggersAlarm'):
            assert scenarios.auto_revert_seconds(s) + handlers.STORM_SETTLE_SECONDS < safety_net, \
                f"{s['id']}: the load would stop on its own before the Lab reverts it"


def test_every_scenario_says_what_its_revert_does():
    """The 'reverting' banner reads the scenario's own revert.summary (the tags revert has no metric wait)."""
    for s in scenarios.all_scenarios():
        summary = (s.get('revert') or {}).get('summary')
        assert summary, f"{s['id']}: revert.summary"
        assert ('DatabaseConnections' in summary) == bool(s.get('triggersAlarm')), s['id']


def test_alarm_driven_scenarios_name_the_connections_alarm():
    alarm_driven = [s for s in scenarios.all_scenarios() if s.get('triggersAlarm')]
    assert [s['id'] for s in alarm_driven] == ['connection-storm-tagged', 'compliance-tag-gdpr']
    assert all(scenarios.alarm_name(s) == 'aurora-bizctx-writer-database-connections' for s in alarm_driven)
    assert scenarios.alarm_name(scenarios.get('business-tags-removed')) == ''


def test_deploy_scripts_register_the_skill_from_main_for_chat_and_incident_rca():
    """deploy-skill clones a branch or tag (git clone --branch), never a SHA: fetch main, name the tested commit."""
    tested = scenarios.load()['capability']['ref']
    name = scenarios.load()['capability']['name']
    checks = {
        'deploy-all.sh': ['SKILL_REF="main"', f'SKILL_TESTED_COMMIT="{tested}"', 'SKILL_AGENT_TYPES="CHAT,INCIDENT_RCA"',
                          '--ref "$SKILL_REF"', '--agent-types "$SKILL_AGENT_TYPES"', '--agent-space-id "$AGENT_SPACE_ID"'],
        'deploy-all.ps1': ['$skillRef = "main"', f'$skillTestedCommit = "{tested}"', '$skillAgentTypes = @("CHAT", "INCIDENT_RCA")',
                           '-Ref $skillRef', '-AgentTypes $skillAgentTypes', '-AgentSpaceId $agentSpaceId'],
    }
    for script, needles in checks.items():
        with open(os.path.join(DEMO_DIR, script), encoding='utf-8') as f:
            text = f.read()
        assert name in text and 'deploy-skill' in text, script
        for needle in needles:
            assert needle in text, f'{script}: {needle}'
        assert 'GENERIC' not in text, f'{script}: register for the declared types, not GENERIC'
        assert f'--ref "{tested}"' not in text and f'-Ref "{tested}"' not in text, f'{script}: a SHA cannot be cloned with --branch'
    with open(os.path.join(DEMO_DIR, 'README.md'), encoding='utf-8') as f:
        assert tested in f.read(), 'the README names the commit the demo was tested with'
    # The Lab's "register it" command takes the same values from scenarios.yaml.
    skill = scenarios.load()['skills'][0]
    assert skill['deployRef'] == 'main'
    assert skill['deployAgentTypes'] == ['CHAT', 'INCIDENT_RCA']


def test_register_command_is_the_one_deploy_all_runs_and_can_clone():
    """Build review B1: the missing-skill fix must work: a branch (never the tested SHA), the Agent
    Space id, the Agent Space region, and CHAT,INCIDENT_RCA (an array in PowerShell)."""
    import api as api_module
    skill = scenarios.load()['skills'][0]
    with patch.dict(os.environ, {'DEVOPS_AGENT_SPACE_ID': 'space-123', 'DEVOPS_AGENT_REGION': 'us-west-2'}):
        cmd = api_module.register_commands(skill)
    ps, sh = cmd['powershell'], cmd['bash']
    for text in (ps, sh):
        assert not re.search(r'[0-9a-f]{40}', text), f'a commit cannot be cloned with --branch: {text}'
        assert skill['ref'] not in text
    assert ps == ('& "..\\..\\shared\\devops-agent\\agent-tools\\deploy-skill.ps1" -Skill aurora-incident-business-context '
                  "-Ref main -AgentSpaceId space-123 -AgentSpaceRegion us-west-2 -AgentTypes @('CHAT', 'INCIDENT_RCA')")
    assert sh == ('../../shared/devops-agent/agent-tools/deploy-skill.sh --skill aurora-incident-business-context '
                  '--ref main --agent-space-id space-123 --agent-space-region us-west-2 --agent-types CHAT,INCIDENT_RCA')
    # Parameter names exist in the shared scripts (read-only check, shared/ untouched).
    shared = os.path.join(REPO_ROOT, 'shared', 'devops-agent', 'agent-tools')
    with open(os.path.join(shared, 'deploy-skill.ps1'), encoding='utf-8') as f:
        ps_src = f.read()
    with open(os.path.join(shared, 'deploy-skill.sh'), encoding='utf-8') as f:
        sh_src = f.read()
    for param in ('$Skill', '$Ref', '$AgentSpaceId', '$AgentSpaceRegion', '[string[]]$AgentTypes'):
        assert param in ps_src, param
    for flag in ('--skill)', '--ref)', '--agent-space-id)', '--agent-space-region)', '--agent-types)'):
        assert flag in sh_src, flag
    # Without a configured Agent Space region, the deploy region (the Lab's own) is used.
    with patch.dict(os.environ, {'DEVOPS_AGENT_SPACE_ID': 'space-123', 'DEVOPS_AGENT_REGION': ''}):
        assert f'-AgentSpaceRegion {api_module.REGION}' in api_module.register_commands(skill)['powershell']
    assert api_module.register_commands({'name': 'x', 'source': 'inline'}) is None


def test_talk_track_is_the_approved_wording():
    assert scenarios.get('connection-storm-tagged')['talkTrack'] == TALK_TRACK_STORM


# ---------------------------------------------------------------------------
# Engine: both roads to `revert`
# ---------------------------------------------------------------------------

def test_manual_rollback_resolves_callback_then_reverts():
    calls, fakes = _fake_handlers()
    with patch.dict(handlers.HANDLERS, fakes, clear=True):
        runner = DurableFunctionTestRunner(handler=engine_main.handler, poll_interval=0.2)
        with runner:
            arn = runner.run_async(input=json.dumps({'scenarioId': 'connection-storm-tagged'}), timeout=60)
            callback_id = runner.wait_for_callback(arn, timeout=30)
            assert calls == ['inject'], 'revert must not run before the callback'
            runner.send_callback_success(callback_id, result=json.dumps({'reason': 'manual'}).encode())
            res = runner.wait_for_result(arn, timeout=30)

    assert res.status == InvocationStatus.SUCCEEDED
    assert calls == ['inject', 'revert']
    assert {engine.STEP_INJECT, engine.STEP_REVERT} <= _step_names(res)
    out = _result(res)
    assert out['revertReason'] == 'manual'
    assert out['scenarioId'] == 'connection-storm-tagged'
    assert out['autoRevertSeconds'] == 1800


def test_timeout_auto_reverts():
    calls, fakes = _fake_handlers()
    short = {**scenarios.get('business-tags-removed'), 'autoRevertSeconds': 1}
    with patch.dict(handlers.HANDLERS, fakes, clear=True), patch.object(scenarios, 'get', return_value=short):
        runner = DurableFunctionTestRunner(handler=engine_main.handler, poll_interval=0.2)
        with runner:
            res = runner.run(input=json.dumps({'scenarioId': 'business-tags-removed'}), timeout=60)

    assert res.status == InvocationStatus.SUCCEEDED
    assert calls == ['inject', 'revert']
    assert _result(res)['revertReason'] == 'auto'


def test_unknown_scenario_fails_before_touching_the_cluster():
    calls, fakes = _fake_handlers()
    with patch.dict(handlers.HANDLERS, fakes, clear=True):
        runner = DurableFunctionTestRunner(handler=engine_main.handler, poll_interval=0.2)
        with runner:
            res = runner.run(input=json.dumps({'scenarioId': 'nope'}), timeout=30)
    assert res.status == InvocationStatus.FAILED
    assert calls == []


# ---------------------------------------------------------------------------
# Handlers: what is sent to AWS, what is read back, what is restored
# ---------------------------------------------------------------------------

def test_baseline_tags_are_the_ones_cdk_puts_on_the_cluster():
    assert handlers.BASELINE_TAGS == CDK_TAGS
    assert set(CDK_TAGS) == {'DependentServices', 'OnCallTeam', 'RevenuePerMinute', 'SLAThresholdMinutes', 'DataClassification'}
    assert CDK_TAGS['RevenuePerMinute'] == '5100' and CDK_TAGS['SLAThresholdMinutes'] == '20'
    assert CDK_TAGS['DataClassification'] == 'Confidential', 'the baseline names no framework'


def test_business_tags_removed_then_restored_exactly_as_cdk_created_them(aws):
    assert handlers.probe_business_tags_removed()['injected'] is False
    handlers.inject_business_tags_removed()
    assert aws.tags == {}
    probe = handlers.probe_business_tags_removed()
    assert probe['injected'] is True
    assert all(i['status'] == 'error' for i in probe['facts'][0]['items'])
    handlers.revert_business_tags_removed()
    assert aws.tags == CDK_TAGS
    assert handlers.probe_business_tags_removed()['injected'] is False


def test_one_missing_tag_is_already_injected_outside_the_lab(aws):
    aws.tags.pop('RevenuePerMinute')
    assert handlers.probe_business_tags_removed()['injected'] is True


def test_storm_switch_is_the_load_functions_reserved_concurrency(aws):
    handlers.inject_connection_storm()
    aws.lam.put_function_concurrency.assert_called_with(FunctionName='aurora-bizctx-connection-load', ReservedConcurrentExecutions=2)
    invoke = aws.lam.invoke.call_args.kwargs
    assert invoke['FunctionName'] == 'aurora-bizctx-connection-load' and invoke['InvocationType'] == 'Event'
    assert 'startedAt' in json.loads(invoke['Payload'])
    aws.metric = [82]
    probe = handlers.probe_connection_storm()
    assert probe['injected'] is True
    assert [f['label'] for f in probe['facts']] == ['Load generator', 'Connections']
    assert probe['facts'][1]['status'] == 'error' and probe['facts'][1]['progress']['percent'] == 91


def test_storm_revert_waits_for_connections_to_fall_under_the_threshold(aws):
    aws.concurrency = 2
    aws.metric = [85, 84, 70, 3]
    out = handlers.revert_connection_storm()
    assert aws.concurrency == 0
    assert aws.cw.get_metric_data.call_count == 4, 'polled until the latest datapoint was under 60'
    assert 'still reads' not in out['message']
    assert handlers.probe_connection_storm()['injected'] is False


def test_storm_revert_reports_a_metric_still_above_the_threshold_at_the_deadline(aws):
    """The settle wait gives up after STORM_SETTLE_SECONDS and says what the metric still reads."""
    aws.concurrency = 2
    aws.metric = [85]                    # never falls
    clock = {'now': 1_000_000.0}
    handlers.time.sleep.side_effect = lambda seconds: clock.__setitem__('now', clock['now'] + seconds)
    try:
        with patch.object(handlers.time, 'time', side_effect=lambda: clock['now']):
            out = handlers.revert_connection_storm()
    finally:
        handlers.time.sleep.side_effect = None
    assert aws.concurrency == 0
    assert 'DatabaseConnections still reads 85' in out['message']
    polls = handlers.STORM_SETTLE_SECONDS // 15
    assert handlers.time.sleep.call_count == polls
    assert aws.cw.get_metric_data.call_count == polls + 1


def test_gdpr_revert_restores_the_tags_before_waiting_for_the_metric(aws):
    """A metric read failing during the settle wait must not leave the cluster tagged GDPR."""
    aws.concurrency = 2
    aws.tags.update({'Compliance': 'GDPR', 'DataClassification': 'PersonalData'})
    aws.cw.get_metric_data.side_effect = RuntimeError('GetMetricData throttled')
    with pytest.raises(RuntimeError):
        handlers.revert_compliance_tags_gdpr()
    assert aws.tags == CDK_TAGS
    assert aws.concurrency == 0


def test_gdpr_scenario_tags_the_cluster_and_revert_restores_the_classification(aws):
    handlers.inject_compliance_tags_gdpr()
    assert aws.tags['Compliance'] == 'GDPR' and aws.tags['DataClassification'] == 'PersonalData'
    assert aws.concurrency == 2
    aws.metric = [3]
    handlers.revert_compliance_tags_gdpr()
    assert aws.tags == CDK_TAGS, 'Compliance removed, DataClassification back to Confidential, nothing else touched'
    assert aws.concurrency == 0


def test_probes_are_disjoint_so_one_condition_shows_on_one_card(aws):
    aws.metric = [80]
    aws.concurrency = 2
    aws.tags.update({'Compliance': 'GDPR', 'DataClassification': 'PersonalData'})
    injected = {name: ops.probe()['injected'] for name, ops in handlers.HANDLERS.items()}
    assert injected == {'connection_storm': False, 'compliance_tags_gdpr': True, 'business_tags_removed': False}
    aws.tags = dict(CDK_TAGS)
    injected = {name: ops.probe()['injected'] for name, ops in handlers.HANDLERS.items()}
    assert injected == {'connection_storm': True, 'compliance_tags_gdpr': False, 'business_tags_removed': False}


def test_connections_fact_without_a_datapoint_is_pending_not_healthy(aws):
    aws.metric = [None]
    fact = handlers.probe_connection_storm()['facts'][1]
    assert fact['status'] == 'pending'


# ---------------------------------------------------------------------------
# API (api.py) with the Lambda control plane mocked; function URL (payload 2.0) events
# ---------------------------------------------------------------------------

@pytest.fixture
def api():
    os.environ['ENGINE_FUNCTION_ARN'] = 'arn:aws:lambda:us-east-1:123456789012:function:lab-engine:live'
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
        {'DurableExecutionName': 'business-tags-removed-1700000000', 'DurableExecutionArn': 'arn:x', 'Status': 'RUNNING'}]}
    resp = index.handler(_event('POST', '/admin/scenarios/connection-storm-tagged/inject'), None)
    assert resp['statusCode'] == 409
    fake_lambda.invoke.assert_not_called()


def test_inject_starts_a_uniquely_named_execution(api):
    index, fake_lambda = api
    fake_lambda.list_durable_executions_by_function.return_value = {'DurableExecutions': []}
    fake_lambda.invoke.return_value = {'DurableExecutionArn': 'arn:run'}
    resp = index.handler(_event('POST', '/admin/scenarios/compliance-tag-gdpr/inject'), None)
    assert resp['statusCode'] == 202
    kwargs = fake_lambda.invoke.call_args.kwargs
    assert kwargs['FunctionName'].endswith(':live')
    assert kwargs['InvocationType'] == 'Event'
    assert kwargs['DurableExecutionName'].startswith('compliance-tag-gdpr-')
    assert json.loads(kwargs['Payload']) == {'scenarioId': 'compliance-tag-gdpr'}


def test_rollback_resolves_the_callback_of_the_running_execution(api):
    index, fake_lambda = api
    fake_lambda.list_durable_executions_by_function.return_value = {'DurableExecutions': [
        {'DurableExecutionName': 'connection-storm-tagged-1700000000', 'DurableExecutionArn': 'arn:run', 'Status': 'RUNNING'}]}
    paginator = MagicMock()
    paginator.paginate.return_value = [{'Events': [
        {'EventType': 'StepSucceeded', 'Name': 'inject'},
        {'EventType': 'CallbackStarted', 'Name': 'await-rollback', 'CallbackStartedDetails': {'CallbackId': 'cb-1', 'Timeout': 1800}},
    ]}]
    fake_lambda.get_paginator.return_value = paginator
    resp = index.handler(_event('DELETE', '/admin/scenarios/connection-storm-tagged/inject'), None)
    assert resp['statusCode'] == 200
    fake_lambda.send_durable_execution_callback_success.assert_called_once()
    assert fake_lambda.send_durable_execution_callback_success.call_args.kwargs['CallbackId'] == 'cb-1'


def test_rollback_without_a_run_reverts_directly_when_injected_outside_the_lab(api, aws):
    index, fake_lambda = api
    fake_lambda.list_durable_executions_by_function.return_value = {'DurableExecutions': []}
    aws.tags.pop('OnCallTeam')
    body = json.loads(index.handler(_event('DELETE', '/admin/scenarios/business-tags-removed/inject'), None)['body'])
    assert body['directRevert'] is True
    assert aws.tags == CDK_TAGS


@pytest.mark.parametrize('scenario_id', ['connection-storm-tagged', 'compliance-tag-gdpr'])
def test_direct_revert_of_a_storm_returns_without_waiting_for_the_metric(api, aws, scenario_id):
    """The API road runs behind CloudFront's 30 s origin timeout: switch off, fix the tags, return."""
    index, fake_lambda = api
    fake_lambda.list_durable_executions_by_function.return_value = {'DurableExecutions': []}
    aws.concurrency = 2
    aws.metric = [85]                    # the metric still reads the storm; it lags about a minute
    if scenario_id == 'compliance-tag-gdpr':
        aws.tags.update({'Compliance': 'GDPR', 'DataClassification': 'PersonalData'})
    handlers.time.sleep.reset_mock()
    resp = index.handler(_event('DELETE', f'/admin/scenarios/{scenario_id}/inject'), None)
    assert resp['statusCode'] == 200
    body = json.loads(resp['body'])
    assert body['directRevert'] is True
    assert aws.concurrency == 0
    assert aws.tags == CDK_TAGS
    handlers.time.sleep.assert_not_called()
    assert aws.cw.get_metric_data.call_count == 1, 'only the probe reads the metric; the revert does not poll'


def test_engine_road_still_waits_for_the_storm_to_settle():
    """engine_main hands the engine `revert` (waits), never `quick_revert`."""
    for s in scenarios.all_scenarios():
        _, revert, _ = engine_main._resolve(s['id'])
        assert revert is handlers.HANDLERS[s['handler']].revert


def test_failed_gdpr_inject_leaves_neither_tags_nor_switch_behind(aws):
    aws.lam.invoke.side_effect = RuntimeError('invoke refused')
    with pytest.raises(RuntimeError):
        handlers.inject_compliance_tags_gdpr()
    assert aws.tags == CDK_TAGS
    assert aws.concurrency == 0


def test_failed_storm_inject_leaves_the_switch_off(aws):
    aws.lam.invoke.side_effect = RuntimeError('invoke refused')
    with pytest.raises(RuntimeError):
        handlers.inject_connection_storm()
    assert aws.concurrency == 0


def test_switch_on_without_held_connections_is_not_shown_as_a_storm(aws):
    aws.concurrency = 2
    aws.metric = [4]
    load = handlers.probe_connection_storm()['facts'][0]
    assert load['value'] == 'On, connections not held yet' and load['status'] == 'warning'
    aws.metric = [83]
    load = handlers.probe_connection_storm()['facts'][0]
    assert load['value'] == 'On' and load['status'] == 'error'


def test_environment_lists_the_baseline_tag_keys_from_the_constants(aws):
    aws.rds.describe_db_clusters.return_value = {'DBClusters': [{'Status': 'available', 'Engine': 'aurora-mysql', 'EngineVersion': '8.0'}]}
    aws.rds.describe_db_instances.return_value = {'DBInstances': [{'DBInstanceStatus': 'available', 'DBInstanceClass': 'db.t4g.medium'}]}
    tags_fact = next(f for f in handlers.environment() if f['label'] == 'Business tags on the cluster')
    assert tags_fact['detail'] == ', '.join(CDK_TAGS)
    assert tags_fact['value'] == f'{len(CDK_TAGS)} of {len(CDK_TAGS)}'


def test_status_returns_probe_facts_plus_alarm_fact_and_run_phases(api):
    index, fake_lambda = api
    fake_lambda.list_durable_executions_by_function.return_value = {'DurableExecutions': [
        {'DurableExecutionName': 'connection-storm-tagged-1700000000', 'DurableExecutionArn': 'arn:run', 'Status': 'RUNNING'}]}
    paginator = MagicMock()
    paginator.paginate.return_value = [{'Events': [
        {'EventType': 'StepSucceeded', 'Name': 'inject'},
        {'EventType': 'CallbackStarted', 'Name': 'await-rollback', 'CallbackStartedDetails': {'CallbackId': 'cb-1', 'Timeout': 1800}},
    ]}]
    fake_lambda.get_paginator.return_value = paginator
    probe = lambda: {'injected': True, 'facts': [{'label': 'Connections', 'value': '82 of about 90', 'status': 'error'}]}
    fakes = {name: handlers.Handler(lambda: {}, lambda: {}, probe) for name in handlers.HANDLERS}
    alarm = 'aurora-bizctx-writer-database-connections'
    with patch.object(index.handlers, 'HANDLERS', fakes), \
         patch.object(index.handlers, 'environment', return_value=[]), \
         patch.object(index, '_alarms', return_value={alarm: {'name': alarm, 'state': 'ALARM', 'reason': ''}}):
        body = json.loads(index.handler(_event('GET', '/admin/status'), None)['body'])
    assert body['busy'] == 'connection-storm-tagged'
    st = body['scenarios']['connection-storm-tagged']
    assert [f['label'] for f in st['facts']] == ['Connections', 'Alarm']
    assert st['facts'][1]['status'] == 'error'
    assert [p['id'] for p in st['run']['phases']] == ['inject', 'await-rollback', 'revert']
    assert [p['status'] for p in st['run']['phases']] == ['success', 'in-progress', 'pending']
    # The Chat-driven scenario carries no alarm fact.
    assert [f['label'] for f in body['scenarios']['business-tags-removed']['facts']] == ['Connections']
    for call in fake_lambda.list_durable_executions_by_function.call_args_list:
        assert len(call.kwargs.get('Statuses', [])) <= 1, call.kwargs


def test_scenarios_route_fills_cluster_and_region_and_reports_the_skill_state(api):
    index, _ = api
    registration = {'success': True, 'found': True, 'assetId': 'ki-1', 'status': 'ACTIVE', 'version': 1, 'agentTypes': ['INCIDENT_RCA', 'CHAT']}
    with patch.object(index.handlers, 'environment', return_value=[]), \
         patch.object(index.devops_agent, 'get_skill', return_value=registration) as get_skill:
        body = json.loads(index.handler(_event('GET', '/admin/scenarios'), None)['body'])
    prompt = body['capability']['prompt']
    assert '{region}' not in prompt and '{clusterId}' not in prompt
    assert 'aurora-bizctx-cluster' in prompt and index.REGION in prompt
    assert [s['id'] for s in body['scenarios']] == ['connection-storm-tagged', 'compliance-tag-gdpr', 'business-tags-removed']
    assert body['scenarios'][0]['alarmName'] == 'aurora-bizctx-writer-database-connections'
    # Alarm-driven scenarios have no prompt (the alarm asks); the Chat one carries its own, filled in.
    assert body['scenarios'][0].get('prompt') is None and body['scenarios'][1].get('prompt') is None
    chat = body['scenarios'][2]['prompt']
    assert 'aurora-bizctx-cluster' in chat and index.REGION in chat and '{' not in chat
    get_skill.assert_called_once_with('aurora-incident-business-context')
    assert body['skills'][0]['registration'] == registration
    command = body['skills'][0]['registerCommand']
    assert '-Ref main' in command['powershell'] and '--ref main' in command['bash']
    assert not re.search(r'[0-9a-f]{40}', command['powershell'] + command['bash'])


def test_scenario_text_is_in_the_presenters_words():
    """Cards are read by a presenter: no capability-internal numbering, Chat scenarios have a prompt, no 'click'."""
    for s in scenarios.all_scenarios():
        check = s['demonstrates']['check']
        assert not re.search(r'\b(Dimension|Step|Check) \d', check), f"{s['id']}: say what the check is, not its number: {check!r}"
        if not s.get('triggersAlarm'):
            assert s.get('prompt'), f"{s['id']}: a Chat-driven scenario carries the prompt to paste"
        for line in (s.get('demoFlow') or []) + [s.get('description') or '', s.get('talkTrack') or '']:
            assert 'click' not in line.lower(), f"{s['id']}: write 'choose', not 'click': {line!r}"
            assert '!' not in line, f"{s['id']}: no exclamation marks: {line!r}"


def test_unknown_route_is_404(api):
    index, _ = api
    assert index.handler(_event('GET', '/admin/nope'), None)['statusCode'] == 404
    assert index.handler(_event('POST', '/admin/scenarios/nope/inject'), None)['statusCode'] == 404
