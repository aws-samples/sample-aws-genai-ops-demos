"""Import ingest-findings' index.py the way the Lambda does, with S3 and DynamoDB in memory.

Run from cdk/lambda/ingest-findings:  python -m pytest tests -q
"""
import copy
import json
import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
LAMBDA_DIR = os.path.dirname(TESTS_DIR)
TABLE_NAME = 'prowler-security-findings'
BUCKET = 'prowler-security-raw-reports-111122223333'

for _var in ('AWS_PROFILE', 'AWS_DEFAULT_PROFILE', 'COST_EVENTS_TABLE'):
    os.environ.pop(_var, None)
os.environ.update({
    'AWS_CONFIG_FILE': os.devnull,
    'AWS_SHARED_CREDENTIALS_FILE': os.devnull,
    'AWS_DEFAULT_REGION': 'us-east-1',
    'AWS_ACCESS_KEY_ID': 'testing',
    'AWS_SECRET_ACCESS_KEY': 'testing',  # pragma: allowlist secret
    'AWS_SESSION_TOKEN': 'testing',  # pragma: allowlist secret
    'FINDINGS_TABLE': TABLE_NAME,
    'DEVOPS_AGENT_TOPIC_ARN': 'arn:aws:sns:us-east-1:111122223333:prowler-security-devops-agent-trigger',
    'REMEDIATION_LAMBDA': 'prowler-security-remediation-context',
})
sys.path.insert(0, LAMBDA_DIR)

import index  # noqa: E402

with open(os.path.join(TESTS_DIR, 'fixtures', 'ocsf_finding.json'), encoding='utf-8') as _f:
    OCSF_FINDING = json.load(_f)


class FindingsTable:
    """In-memory findings table: the batch writer and BatchGetItem the ingest Lambda uses."""

    def __init__(self):
        self.rows = {}
        self.puts = []
        self.batch_gets = []
        self.unprocessed_once = set()
        self.fail_batch_get = None

    def batch_writer(self, overwrite_by_pkeys=None):
        table = self

        class Writer:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def put_item(self, Item):
                table.puts.append(copy.deepcopy(Item))
                table.rows[Item['finding_uid']] = copy.deepcopy(Item)

        return Writer()

    def batch_get_item(self, RequestItems):
        request = RequestItems[TABLE_NAME]
        uids = [k['finding_uid'] for k in request['Keys']]
        self.batch_gets.append(uids)
        if self.fail_batch_get:
            raise self.fail_batch_get
        if len(uids) != len(set(uids)):
            raise AssertionError('BatchGetItem rejects duplicate keys')
        unprocessed = [uid for uid in uids if uid in self.unprocessed_once]
        self.unprocessed_once -= set(unprocessed)
        found = [copy.deepcopy(self.rows[uid]) for uid in uids if uid in self.rows and uid not in unprocessed]
        if request.get('ProjectionExpression'):
            names = request.get('ExpressionAttributeNames') or {}
            wanted = {names.get(p.strip(), p.strip()) for p in request['ProjectionExpression'].split(',')}
            found = [{k: v for k, v in row.items() if k in wanted} for row in found]
        resp = {'Responses': {TABLE_NAME: found}, 'UnprocessedKeys': {}}
        if unprocessed:
            resp['UnprocessedKeys'] = {TABLE_NAME: {'Keys': [{'finding_uid': uid} for uid in unprocessed]}}
        return resp


class FakeDynamoDB:
    def __init__(self, table):
        self.meta = type('Meta', (), {'client': table})()


class _Body:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class FakeS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        return {'Body': _Body(self.objects[(Bucket, Key)])}


class Ingest:
    """Drives the handler: each run() uploads one report and sends its S3 event."""

    def __init__(self, table, s3):
        self.table = table
        self.s3 = s3
        self.runs = 0

    def run(self, findings, scan_id=None, prefix='raw-reports'):
        self.runs += 1
        scan_id = scan_id or f'2026112{self.runs}T060000Z'
        key = f'{prefix}/{scan_id}/111122223333.ocsf.json'
        self.s3.objects[(BUCKET, key)] = json.dumps(findings).encode('utf-8')
        event = {'Records': [{'s3': {'bucket': {'name': BUCKET}, 'object': {'key': key}}}]}
        return index.handler(event, None)

    def row(self, finding_uid):
        return self.table.rows[finding_uid]


def finding(status='FAIL', **changes):
    """A copy of the recorded OCSF finding with a given status and top-level changes."""
    data = copy.deepcopy(OCSF_FINDING)
    data['status_code'] = status
    data.update(changes)
    return data


UID = 'iam_aws_attached_policy_no_administrative_privileges:' \
      'arn:aws:iam::aws:policy/aws-service-role/AWSSecurityHubServiceRolePolicy'


@pytest.fixture
def ingest(monkeypatch):
    table, s3 = FindingsTable(), FakeS3()
    monkeypatch.setattr(index, '_table', table)
    monkeypatch.setattr(index, '_ddb', FakeDynamoDB(table))
    monkeypatch.setattr(index, '_s3', s3)
    monkeypatch.setattr(index, 'log_cost_event', lambda *a, **k: None)
    monkeypatch.setattr(index.time, 'sleep', lambda seconds: None)
    return Ingest(table, s3)
