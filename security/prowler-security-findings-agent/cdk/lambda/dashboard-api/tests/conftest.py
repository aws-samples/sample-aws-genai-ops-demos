"""Import dashboard-api's index.py the way the Lambda does, isolated from real AWS.

DevOps Agent is stubbed at the HTTP layer (botocore `before-send`), so the real
boto3 client serializes each request and parses the recorded wire response.
DynamoDB is replaced by an in-memory table.

Run from cdk/lambda/dashboard-api:  python -m pytest tests -q
"""
import copy
import json
import os
import sys
from urllib.parse import urlsplit

import pytest
from botocore.awsrequest import AWSResponse

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
LAMBDA_DIR = os.path.dirname(TESTS_DIR)
FIXTURES_DIR = os.path.join(TESTS_DIR, 'fixtures')
AGENT_SPACE_ID = '11111111-2222-4333-8444-555555555555'
REGION = 'us-east-1'  # fixed region of the recorded fixtures

for _var in ('AWS_PROFILE', 'AWS_DEFAULT_PROFILE'):
    os.environ.pop(_var, None)
os.environ.update({
    'AWS_CONFIG_FILE': os.devnull,
    'AWS_SHARED_CREDENTIALS_FILE': os.devnull,
    'AWS_DEFAULT_REGION': REGION,
    'AWS_ACCESS_KEY_ID': 'testing',
    'AWS_SECRET_ACCESS_KEY': 'testing',  # pragma: allowlist secret
    'AWS_SESSION_TOKEN': 'testing',  # pragma: allowlist secret
    'FINDINGS_TABLE': 'prowler-security-findings',
    'REMEDIATIONS_BUCKET': 'remediations',
    'SCANNER_CLUSTER_ARN': f'arn:aws:ecs:{REGION}:111122223333:cluster/prowler-security-scanner',
    'SCANNER_TASK_DEFINITION_ARN': f'arn:aws:ecs:{REGION}:111122223333:task-definition/prowler:1',
    'SCANNER_SUBNET_IDS': 'subnet-0example',
    'SCANNER_SECURITY_GROUP_ID': 'sg-0example',
    'DEVOPS_AGENT_REGION': REGION,
    'DEVOPS_AGENT_SPACE_ID': AGENT_SPACE_ID,
    'DEVOPS_AGENT_SECRET_ARN': '',
})
sys.path.insert(0, LAMBDA_DIR)

import index  # noqa: E402

FINDINGS = [
    {
        'finding_uid': 'iam_aws_attached_policy_no_administrative_privileges:'
                       'arn:aws:iam::aws:policy/aws-service-role/AWSSecurityHubServiceRolePolicy',
        'check_id': 'iam_aws_attached_policy_no_administrative_privileges',
        'check_title': "Attached AWS-managed IAM policy does not allow '*:*' administrative privileges",
        'resource_uid': 'arn:aws:iam::aws:policy/aws-service-role/AWSSecurityHubServiceRolePolicy',
        'severity': 'CRITICAL',
        'service_name': 'iam',
    },
    {
        'finding_uid': 'iam_role_cross_service_confused_deputy_prevention:'
                       'arn:aws:iam::111122223333:role/AWS-QuickSetup-StackSet-Local-AdministrationRole',
        'check_id': 'iam_role_cross_service_confused_deputy_prevention',
        'check_title': 'IAM service role prevents cross-service confused deputy attack',
        'resource_uid': 'arn:aws:iam::111122223333:role/AWS-QuickSetup-StackSet-Local-AdministrationRole',
        'severity': 'HIGH',
        'service_name': 'iam',
    },
    {
        'finding_uid': 's3_bucket_default_encryption:arn:aws:s3:::example-bucket',
        'check_id': 's3_bucket_default_encryption',
        'check_title': 'Check if S3 buckets have default encryption (SSE) enabled',
        'resource_uid': 'arn:aws:s3:::example-bucket',
        'severity': 'MEDIUM',
        'service_name': 's3',
    },
]


def load_fixture(name):
    with open(os.path.join(FIXTURES_DIR, name), encoding='utf-8') as f:
        return json.load(f)


class FakeTable:
    def __init__(self, items):
        self.items = {i['finding_uid']: i for i in items}

    def get_item(self, Key, ProjectionExpression=None):
        item = self.items.get(Key['finding_uid'])
        return {'Item': dict(item)} if item else {}

    def scan(self, ProjectionExpression=None, **kwargs):
        return {'Items': [dict(i) for i in self.items.values()]}


class _RawBody:
    def __init__(self, data):
        self._data = data

    def stream(self, **kwargs):
        yield self._data


class DevOpsAgentWire:
    """Recorded DevOps Agent API at the HTTP layer; records every request it serves."""

    def __init__(self, recorded):
        self.tasks = copy.deepcopy(recorded['list_backlog_tasks']['tasks'])
        self.executions = copy.deepcopy(recorded['list_executions'])
        self.journal = copy.deepcopy(recorded['list_journal_records'])
        self.by_type = {}
        self.errors = {}
        self.calls = []

    def add_records(self, execution_id, record_type, records):
        self.by_type[(execution_id, record_type)] = copy.deepcopy(records)

    def respond(self, operation, body):
        if operation in self.errors:
            return self.errors[operation]
        if operation == 'ListBacklogTasks':
            return 200, {'tasks': self.tasks}
        if operation == 'ListExecutions':
            return 200, self.executions.get(body['taskId'], {'executions': []})
        if operation == 'ListJournalRecords':
            if body.get('recordType'):
                records = self.by_type.get((body['executionId'], body['recordType']), [])
                records = sorted(records, key=lambda r: r['createdAt'], reverse=body.get('order') != 'ASC')
                return 200, {'records': records[:body.get('limit', 100)]}
            return 200, self.journal.get(body['executionId'], {'records': []})
        raise AssertionError(f'unexpected operation {operation}')

    def __call__(self, request, event_name, **kwargs):
        operation = event_name.split('.')[-1]
        raw = request.body or b'{}'
        body = json.loads(raw.decode('utf-8') if isinstance(raw, bytes) else raw)
        self.calls.append((operation, urlsplit(request.url).path, body))
        status, payload = self.respond(operation, body)
        headers = {'content-type': 'application/json'}
        if status >= 400:
            headers['x-amzn-errortype'] = payload.get('__type', '')
        return AWSResponse(request.url, status, headers, _RawBody(json.dumps(payload).encode('utf-8')))


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setattr(index, 'TABLE', FakeTable(FINDINGS))
    monkeypatch.setattr(index, '_devops_client', None)
    stub = DevOpsAgentWire(load_fixture('devops_agent_wire.json'))
    index._devops().meta.events.register('before-send.devops-agent', stub)
    yield stub
    monkeypatch.setattr(index, '_devops_client', None)


def get(path):
    resp = index.handler({'requestContext': {'http': {'method': 'GET', 'path': path}}}, None)
    return resp['statusCode'], json.loads(resp['body'])
