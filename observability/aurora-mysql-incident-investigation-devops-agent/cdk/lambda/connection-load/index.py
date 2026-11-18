"""
Connection load: opens and holds MySQL connections to the Aurora writer, the "runaway client"
of the connection storm scenarios.

The switch is this function's own reserved concurrency, set by the Lab:
    0      off (the deployed baseline): nothing runs, a queued hand-over event is dropped
    > 0    on: hold connections, check the switch every few seconds, hand over before the
           15-minute Lambda limit by invoking itself again
The Lab never stores "the storm is on"; it reads the same concurrency back (rule: live state).

Safety net: every invocation carries the storm's start time and stops once MAX_STORM_SECONDS
have passed, even if nothing switched it off.

Runs in the VPC's isolated subnets; Secrets Manager and Lambda are reached through interface
endpoints (private DNS), the writer on 3306 over TLS verified against the bundled RDS CA.
"""
import json
import logging
import os
import ssl
import time

import boto3
import pymysql
from botocore.config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CLUSTER_ENDPOINT = os.environ.get('CLUSTER_ENDPOINT', '')
SECRET_ARN = os.environ.get('SECRET_ARN', '')
TARGET_CONNECTIONS = int(os.environ.get('TARGET_CONNECTIONS') or 85)
MAX_STORM_SECONDS = int(os.environ.get('MAX_STORM_SECONDS') or 3600)
FUNCTION_NAME = os.environ.get('AWS_LAMBDA_FUNCTION_NAME', '')

CHECK_EVERY_SECONDS = 5
# Hand over to a fresh invocation this long before the Lambda timeout.
HANDOVER_MARGIN_MS = 60_000
ER_CON_COUNT_ERROR = 1040        # Too many connections

_api_config = Config(connect_timeout=5, read_timeout=10, retries={'max_attempts': 2})
_lambda = boto3.client('lambda', config=_api_config)
_secrets = boto3.client('secretsmanager', config=_api_config)


def switched_on() -> bool:
    """Reserved concurrency above 0. Any error reads as off: the storm never outlives a doubt."""
    try:
        return int(_lambda.get_function_concurrency(FunctionName=FUNCTION_NAME).get('ReservedConcurrentExecutions') or 0) > 0
    except Exception as e:
        logger.warning('Cannot read the switch (%s); stopping', e)
        return False


def _credentials() -> dict:
    return json.loads(_secrets.get_secret_value(SecretId=SECRET_ARN)['SecretString'])


# The RDS global CA bundle, downloaded into the asset at bundling time (see database-stack.ts).
RDS_CA_BUNDLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'global-bundle.pem')


def _tls() -> ssl.SSLContext:
    """TLS to the writer, certificate and hostname verified against the RDS CA bundle."""
    return ssl.create_default_context(cafile=RDS_CA_BUNDLE)


def _connect(creds: dict):
    return pymysql.connect(host=CLUSTER_ENDPOINT, port=int(creds.get('port') or 3306), user=creds['username'],
                           password=creds['password'], connect_timeout=5, ssl=_tls())


def _top_up(conns: list, creds: dict) -> None:
    """Open connections until the target, or until the writer refuses (error 1040)."""
    while len(conns) < TARGET_CONNECTIONS:
        try:
            conns.append(_connect(creds))
        except pymysql.err.OperationalError as e:
            if e.args and e.args[0] == ER_CON_COUNT_ERROR:
                return
            logger.warning('Connect failed: %s', e)
            return


def handler(event, context):
    started_at = int((event or {}).get('startedAt') or time.time())
    generation = int((event or {}).get('generation') or 0)
    if time.time() - started_at > MAX_STORM_SECONDS:
        logger.info('Storm started %ss ago, past the %ss safety limit; not starting', int(time.time() - started_at), MAX_STORM_SECONDS)
        return {'held': 0, 'reason': 'safety-limit'}
    if not switched_on():
        return {'held': 0, 'reason': 'switched-off'}

    creds = _credentials()
    conns: list = []
    reason = 'switched-off'
    peak = 0
    try:
        while True:
            _top_up(conns, creds)
            peak = max(peak, len(conns))
            time.sleep(CHECK_EVERY_SECONDS)
            if not switched_on():
                reason = 'switched-off'
                break
            if time.time() - started_at > MAX_STORM_SECONDS:
                reason = 'safety-limit'
                break
            if context.get_remaining_time_in_millis() < HANDOVER_MARGIN_MS:
                # The next invocation tops up as these connections close: a gap of seconds.
                _lambda.invoke(FunctionName=FUNCTION_NAME, InvocationType='Event',
                               Payload=json.dumps({'startedAt': started_at, 'generation': generation + 1}).encode())
                reason = 'handed-over'
                break
    finally:
        for conn in conns:
            try:
                conn.close()
            except Exception:  # nosec B110 - closing a connection the server may already have dropped
                pass
    logger.info('Held up to %s connections (generation %s); %s', peak, generation, reason)
    return {'held': peak, 'generation': generation, 'reason': reason}
