"""
The connection load function (cdk/lambda/connection-load): it holds connections only while
its reserved concurrency is above 0, stops at the writer's refusal, hands over before the
Lambda timeout, and never outlives its safety limit. MySQL and the AWS APIs are mocked.

Run from the demo's lab/ folder:  python -m pytest tests -q
"""

import importlib.util
import json
import os
from unittest.mock import MagicMock, patch

import pymysql
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LOAD_INDEX = os.path.join(HERE, '..', '..', 'cdk', 'lambda', 'connection-load', 'index.py')

os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')
os.environ['AWS_LAMBDA_FUNCTION_NAME'] = 'aurora-bizctx-connection-load'
os.environ['TARGET_CONNECTIONS'] = '5'
os.environ['MAX_STORM_SECONDS'] = '3600'

import botocore  # noqa: E402

STAND_IN_CA = os.path.join(os.path.dirname(botocore.__file__), 'cacert.pem')


@pytest.fixture
def load():
    spec = importlib.util.spec_from_file_location('connection_load', LOAD_INDEX)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._secrets = MagicMock()
    module._secrets.get_secret_value.return_value = {'SecretString': json.dumps({'username': 'admin', 'password': 'x', 'port': 3306})}  # pragma: allowlist secret
    module._lambda = MagicMock()
    # global-bundle.pem is downloaded into the asset at bundling time, not kept in the repo:
    # any PEM bundle stands in for it, so the real _tls() runs in every test.
    module.RDS_CA_BUNDLE = STAND_IN_CA
    with patch.object(module.time, 'sleep'):
        yield module


def _context(remaining_ms):
    ctx = MagicMock()
    ctx.get_remaining_time_in_millis.side_effect = list(remaining_ms) + [remaining_ms[-1]] * 100
    return ctx


def _switch(load, values):
    load._lambda.get_function_concurrency.side_effect = [{'ReservedConcurrentExecutions': v} for v in values]


def test_does_nothing_while_switched_off(load):
    _switch(load, [0])
    with patch.object(load.pymysql, 'connect') as connect:
        out = load.handler({}, _context([900_000]))
    assert out['reason'] == 'switched-off' and out['held'] == 0
    connect.assert_not_called()


def test_holds_up_to_the_target_and_closes_everything_when_switched_off(load):
    _switch(load, [2, 2, 0])
    conns = [MagicMock() for _ in range(5)]
    with patch.object(load.pymysql, 'connect', side_effect=conns):
        out = load.handler({'startedAt': int(load.time.time())}, _context([900_000]))
    assert out == {'held': 5, 'generation': 0, 'reason': 'switched-off'}
    assert all(c.close.called for c in conns)


def test_stops_opening_when_the_writer_refuses(load):
    _switch(load, [2, 0])
    refused = pymysql.err.OperationalError(1040, 'Too many connections')
    with patch.object(load.pymysql, 'connect', side_effect=[MagicMock(), MagicMock(), refused]):
        out = load.handler({}, _context([900_000]))
    assert out['held'] == 2


def test_hands_over_before_the_lambda_timeout_carrying_the_storm_start_time(load):
    _switch(load, [2, 2])
    started = int(load.time.time())
    conns = [MagicMock() for _ in range(5)]
    with patch.object(load.pymysql, 'connect', side_effect=conns):
        out = load.handler({'startedAt': started, 'generation': 1}, _context([30_000]))
    assert out['reason'] == 'handed-over'
    payload = json.loads(load._lambda.invoke.call_args.kwargs['Payload'])
    assert payload == {'startedAt': started, 'generation': 2}
    assert all(c.close.called for c in conns), 'the old invocation releases its connections for the new one'


def test_never_starts_past_the_safety_limit(load):
    _switch(load, [2])
    out = load.handler({'startedAt': int(load.time.time()) - 4000}, _context([900_000]))
    assert out['reason'] == 'safety-limit'
    load._lambda.get_function_concurrency.assert_not_called()


def test_an_unreadable_switch_reads_as_off(load):
    load._lambda.get_function_concurrency.side_effect = Exception('endpoint unreachable')
    assert load.switched_on() is False


def test_tls_to_the_writer_verifies_the_certificate_and_the_hostname(load):
    """_tls() loads the bundled RDS CA and requires a valid certificate for the cluster endpoint's name."""
    spec = importlib.util.spec_from_file_location('connection_load_fresh', LOAD_INDEX)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    assert os.path.basename(fresh.RDS_CA_BUNDLE) == 'global-bundle.pem', 'the deployed function reads the bundled RDS CA'
    ctx = load._tls()
    assert ctx.verify_mode == load.ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    with patch.object(load.pymysql, 'connect') as connect:
        load._connect({'username': 'admin', 'password': 'x', 'port': 3306})  # pragma: allowlist secret
    sent = connect.call_args.kwargs['ssl']
    assert sent.verify_mode == load.ssl.CERT_REQUIRED and sent.check_hostname is True
