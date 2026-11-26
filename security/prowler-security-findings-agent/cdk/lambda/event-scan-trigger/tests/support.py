"""Builders for CloudTrail-via-EventBridge events, SQS records and AWS fakes."""

import json
import os
import sys
import uuid

LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if LAMBDA_DIR not in sys.path:
    sys.path.insert(0, LAMBDA_DIR)

import index  # noqa: E402

ACCOUNT = '123456789012'
REGION = 'us-east-1'
TASK_DEF_ARN = f'arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/prowler-security-scanner:4'
CONFIG = index.Config(
    cluster_arn=f'arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/prowler-security-scanner',
    task_definition_arn=TASK_DEF_ARN,
    container_name='Prowler',
    subnet_ids=('subnet-aaa', 'subnet-bbb'),
    security_group_id='sg-scanner',
    assign_public_ip='DISABLED',
)


def cloudtrail_event(source, event_name, request=None, response=None, region=REGION, account=ACCOUNT,
                     error_code=None):
    detail = {
        'eventVersion': '1.10',
        'userIdentity': {'type': 'AssumedRole', 'accountId': account},
        'eventTime': '2026-11-25T19:30:00Z',
        'eventSource': f"{source.split('.', 1)[1]}.amazonaws.com",
        'eventName': event_name,
        'awsRegion': region,
        'requestParameters': request,
        'responseElements': response,
        'readOnly': False,
        'eventType': 'AwsApiCall',
        'managementEvent': True,
        'recipientAccountId': account,
        'eventCategory': 'Management',
    }
    if error_code:
        detail['errorCode'] = error_code
    return {
        'version': '0',
        'id': str(uuid.uuid4()),
        'detail-type': 'AWS API Call via CloudTrail',
        'source': source,
        'account': account,
        'time': '2026-11-25T19:30:00Z',
        'region': region,
        'resources': [],
        'detail': detail,
    }


def authorize_sg_ingress(group_id='sg-0aaa', port=22, cidr='0.0.0.0/0'):
    return cloudtrail_event(
        'aws.ec2', 'AuthorizeSecurityGroupIngress',
        request={
            'groupId': group_id,
            'ipPermissions': {'items': [{
                'ipProtocol': 'tcp', 'fromPort': port, 'toPort': port,
                'ipRanges': {'items': [{'cidrIp': cidr}]},
            }]},
        },
        response={
            'requestId': str(uuid.uuid4()),
            '_return': True,
            'securityGroupRuleSet': {'items': [{
                'groupOwnerId': ACCOUNT, 'groupId': group_id, 'securityGroupRuleId': 'sgr-0123',
                'isEgress': False, 'ipProtocol': 'tcp', 'fromPort': port, 'toPort': port, 'cidrIpv4': cidr,
            }]},
        },
    )


def sqs_record(body, message_id=None):
    return {
        'messageId': message_id or str(uuid.uuid4()),
        'receiptHandle': 'handle',
        'body': body if isinstance(body, str) else json.dumps(body),
        'attributes': {'ApproximateReceiveCount': '1'},
        'eventSource': 'aws:sqs',
    }


class FakeLookups:
    """Stands in for index.Lookups: {group_id: vpc_id}, {group_name: (group_id, vpc_id)}, {user: arn}."""

    def __init__(self, groups=None, group_names=None, users=None, error=None):
        self.groups = groups or {}
        self.group_names = group_names or {}
        self.users = users or {}
        self.error = error
        self.calls = []

    def security_group_vpcs(self, region, group_ids, group_names):
        self.calls.append(('security_group_vpcs', region, tuple(group_ids), tuple(group_names)))
        if self.error:
            raise self.error
        found = {gid: self.groups[gid] for gid in group_ids if gid in self.groups}
        for name in group_names:
            if name in self.group_names:
                gid, vpc = self.group_names[name]
                found[gid] = vpc
        return found

    def iam_user_arn(self, user_name, region):
        self.calls.append(('iam_user_arn', user_name, region))
        if self.error:
            raise self.error
        return self.users.get(user_name)


class FakeEcs:
    def __init__(self, fail_when=None):
        self.calls = []
        self.fail_when = fail_when

    def run_task(self, **kwargs):
        self.calls.append(kwargs)
        env = environment(kwargs)
        if self.fail_when and self.fail_when(env):
            return {'tasks': [], 'failures': [{'arn': 'x', 'reason': 'RESOURCE:ENI'}]}
        task_id = uuid.uuid4().hex
        return {'tasks': [{'taskArn': f'arn:aws:ecs:{REGION}:{ACCOUNT}:task/prowler-security-scanner/{task_id}'}],
                'failures': []}


def environment(run_task_kwargs):
    overrides = run_task_kwargs['overrides']['containerOverrides'][0]
    return {e['name']: e['value'] for e in overrides['environment']}
