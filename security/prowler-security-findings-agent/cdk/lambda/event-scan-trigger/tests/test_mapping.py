import unittest

from botocore.exceptions import ClientError
from botocore.stub import Stubber
import boto3

from support import ACCOUNT, REGION, FakeLookups, authorize_sg_ingress, cloudtrail_event
import index

SG_CHECK = 'ec2_securitygroup_allow_ingress_from_internet_to_tcp_port_22'


def requests_for(envelope, lookups=None, home_region=REGION):
    return index.requests_from_event(envelope, 'm-1', index.RULES, 'aws', lookups or FakeLookups(), home_region)


class RulesFileTest(unittest.TestCase):
    def test_rules_load_and_are_unique(self):
        rules = index.load_rules()
        self.assertEqual(len({r.id for r in rules}), len(rules))
        seen = set()
        for rule in rules:
            for name in rule.event_names + rule.event_name_prefixes:
                self.assertNotIn((rule.source, name), seen, f'{name} mapped twice')
                seen.add((rule.source, name))

    def test_a_group_uses_a_single_scope(self):
        scopes = {}
        for rule in index.RULES:
            scope = 'region' if rule.resolver == 'region' else 'resource'
            self.assertEqual(scopes.setdefault(rule.group, scope), scope, rule.group)

    def test_every_rule_fits_the_ecs_override_limit(self):
        long_arn = f'arn:aws:ec2:{REGION}:{ACCOUNT}:security-group/sg-0123456789abcdef0'
        for group in {r.group for r in index.RULES}:
            checks = sorted({c for r in index.RULES if r.group == group for c in r.checks})
            plan = index.TaskPlan(group, checks, arns=[long_arn])
            self.assertLess(index.override_chars(plan), index.MAX_OVERRIDE_CHARS, group)

    def test_scan_id_matches_the_entrypoint_contract(self):
        for group in {r.group for r in index.RULES}:
            self.assertRegex(index.new_scan_id(group), r'^[0-9]{8}T[0-9]{6}Z-event-[a-z0-9-]{1,48}$')


class SecurityGroupMappingTest(unittest.TestCase):
    def test_authorize_ingress_scans_the_group_with_its_vpc(self):
        lookups = FakeLookups(groups={'sg-0aaa': 'vpc-0111'})
        [request] = requests_for(authorize_sg_ingress('sg-0aaa'), lookups)
        self.assertEqual(request.group, 'ec2-securitygroup')
        self.assertIn(SG_CHECK, request.checks)
        self.assertEqual(request.target.arns, (
            f'arn:aws:ec2:{REGION}:{ACCOUNT}:security-group/sg-0aaa',
            f'arn:aws:ec2:{REGION}:{ACCOUNT}:vpc/vpc-0111',
        ))
        self.assertIsNone(request.target.region)

    def test_modify_security_group_rules_wrapped_request(self):
        event = cloudtrail_event('aws.ec2', 'ModifySecurityGroupRules', request={
            'ModifySecurityGroupRulesRequest': {
                'GroupId': 'sg-0bbb',
                'SecurityGroupRule': {'SecurityGroupRuleId': 'sgr-1', 'SecurityGroupRule': {'CidrIpv4': '0.0.0.0/0'}},
            },
        }, response={'ModifySecurityGroupRulesResponse': {'_return': True}})
        [request] = requests_for(event, FakeLookups(groups={'sg-0bbb': 'vpc-0222'}))
        self.assertEqual(request.target.arns[0], f'arn:aws:ec2:{REGION}:{ACCOUNT}:security-group/sg-0bbb')

    def test_revoke_ingress_scans_too_so_fixes_show_up(self):
        event = cloudtrail_event('aws.ec2', 'RevokeSecurityGroupIngress',
                                 request={'groupId': 'sg-0aaa'}, response={'_return': True})
        self.assertEqual(len(requests_for(event, FakeLookups(groups={'sg-0aaa': 'vpc-0111'}))), 1)

    def test_group_name_in_default_vpc_is_resolved(self):
        event = cloudtrail_event('aws.ec2', 'AuthorizeSecurityGroupIngress', request={'groupName': 'web'})
        lookups = FakeLookups(group_names={'web': ('sg-0ccc', 'vpc-default')})
        [request] = requests_for(event, lookups)
        self.assertEqual(request.target.arns[0], f'arn:aws:ec2:{REGION}:{ACCOUNT}:security-group/sg-0ccc')
        self.assertEqual(lookups.calls[0], ('security_group_vpcs', REGION, (), ('web',)))

    def test_deleted_group_is_skipped(self):
        self.assertEqual(requests_for(authorize_sg_ingress('sg-gone'), FakeLookups()), [])

    def test_source_group_referenced_by_the_rule_is_not_scanned(self):
        event = authorize_sg_ingress('sg-0aaa')
        event['detail']['requestParameters']['ipPermissions']['items'][0]['groups'] = {
            'items': [{'groupId': 'sg-0source'}]}
        event['detail']['responseElements']['securityGroupRuleSet']['items'][0]['referencedGroupInfo'] = {
            'groupId': 'sg-0source'}
        lookups = FakeLookups(groups={'sg-0aaa': 'vpc-0111', 'sg-0source': 'vpc-0111'})
        [request] = requests_for(event, lookups)
        self.assertEqual(lookups.calls, [('security_group_vpcs', REGION, ('sg-0aaa',), ())])


class OtherServicesMappingTest(unittest.TestCase):
    def test_s3_bucket_policy(self):
        event = cloudtrail_event('aws.s3', 'PutBucketPolicy', request={
            'bucketName': 'demo-bucket', 'Host': 'demo-bucket.s3.amazonaws.com', 'bucketPolicy': {'Statement': []},
        }, region='eu-west-1')
        [request] = requests_for(event)
        self.assertEqual(request.group, 's3')
        self.assertEqual(request.target.arns, ('arn:aws:s3:::demo-bucket',))
        self.assertIn('s3_bucket_public_access', request.checks)

    def test_s3_account_public_access_block_uses_the_scanner_region(self):
        event = cloudtrail_event('aws.s3', 'DeleteAccountPublicAccessBlock', request={'Host': 's3-control'})
        [request] = requests_for(event, home_region='eu-west-1')
        self.assertEqual(request.target.arns, (f'arn:aws:s3:eu-west-1:{ACCOUNT}:account',))
        self.assertEqual(request.checks, ('s3_account_level_public_access_blocks',))

    def test_iam_attach_user_policy_uses_the_arn_with_path(self):
        arn = f'arn:aws:iam::{ACCOUNT}:user/engineering/alice'
        event = cloudtrail_event('aws.iam', 'AttachUserPolicy', request={
            'userName': 'alice', 'policyArn': 'arn:aws:iam::aws:policy/AdministratorAccess',
        })
        lookups = FakeLookups(users={'alice': arn})
        [request] = requests_for(event, lookups)
        self.assertEqual(request.target.arns, (arn,))
        self.assertEqual(request.checks, ('iam_user_administrator_access_policy',))

    def test_iam_user_gone_or_unscannable_name_is_skipped(self):
        event = cloudtrail_event('aws.iam', 'AttachUserPolicy', request={'userName': 'bob'})
        self.assertEqual(requests_for(event, FakeLookups()), [])
        event = cloudtrail_event('aws.iam', 'AttachUserPolicy', request={'userName': 'carol+ops@example.com'})
        lookups = FakeLookups(users={'carol+ops@example.com': f'arn:aws:iam::{ACCOUNT}:user/carol+ops@example.com'})
        self.assertEqual(requests_for(event, lookups), [])

    def test_lambda_function_reference_forms(self):
        expected = f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:orders'
        for reference in ('orders', 'orders:prod', f'{ACCOUNT}:function:orders',
                          f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:orders:$LATEST'):
            with self.subTest(reference=reference):
                event = cloudtrail_event('aws.lambda', 'AddPermission20150331v2', request={
                    'functionName': reference, 'statementId': 'public', 'action': 'lambda:InvokeFunction',
                    'principal': '*',
                })
                [request] = requests_for(event)
                self.assertEqual(request.target.arns, (expected,))

    def test_lambda_function_url_config(self):
        event = cloudtrail_event('aws.lambda', 'CreateFunctionUrlConfig20211031',
                                 request={'functionName': 'orders', 'authType': 'NONE'})
        [request] = requests_for(event)
        self.assertIn('awslambda_function_url_public', request.checks)

    def test_rds_modify_is_check_scoped_to_the_region(self):
        event = cloudtrail_event('aws.rds', 'ModifyDBInstance', region='eu-west-1', request={
            'dBInstanceIdentifier': 'orders-db', 'publiclyAccessible': True, 'applyImmediately': True,
        })
        [request] = requests_for(event)
        self.assertEqual(request.target, index.Target(region='eu-west-1'))
        self.assertEqual(request.checks, ('rds_instance_no_public_access',))

    def test_failed_calls_and_unmapped_events_are_ignored(self):
        failed = authorize_sg_ingress()
        failed['detail']['errorCode'] = 'Client.UnauthorizedOperation'
        lookups = FakeLookups(groups={'sg-0aaa': 'vpc-0111'})
        self.assertEqual(requests_for(failed, lookups), [])
        self.assertEqual(requests_for(cloudtrail_event('aws.ec2', 'DescribeInstances'), lookups), [])
        self.assertEqual(requests_for(cloudtrail_event('aws.s3', 'AuthorizeSecurityGroupIngress'), lookups), [])
        self.assertEqual(requests_for({'detail-type': 'Scheduled Event'}, lookups), [])
        self.assertEqual(lookups.calls, [])


class LookupsTest(unittest.TestCase):
    def _lookups(self, service):
        client = boto3.client(service, region_name=REGION, aws_access_key_id='x', aws_secret_access_key='x')
        lookups = index.Lookups(session=boto3.session.Session(region_name=REGION))
        lookups._clients[(service, REGION)] = client
        return lookups, Stubber(client)

    def test_security_group_not_found_is_skipped_and_cached(self):
        lookups, stub = self._lookups('ec2')
        stub.add_response('describe_security_groups', {'SecurityGroups': [{'GroupId': 'sg-1', 'VpcId': 'vpc-1'}]},
                          {'GroupIds': ['sg-1']})
        stub.add_client_error('describe_security_groups', 'InvalidGroup.NotFound', expected_params={'GroupIds': ['sg-2']})
        with stub:
            self.assertEqual(lookups.security_group_vpcs(REGION, ['sg-1', 'sg-2'], []), {'sg-1': 'vpc-1'})
            self.assertEqual(lookups.security_group_vpcs(REGION, ['sg-1', 'sg-2'], []), {'sg-1': 'vpc-1'})
        stub.assert_no_pending_responses()

    def test_throttling_propagates_so_sqs_retries(self):
        lookups, stub = self._lookups('ec2')
        stub.add_client_error('describe_security_groups', 'RequestLimitExceeded')
        with stub, self.assertRaises(ClientError):
            lookups.security_group_vpcs(REGION, ['sg-1'], [])

    def test_iam_user_lookup(self):
        lookups, stub = self._lookups('iam')
        stub.add_response('get_user', {'User': {
            'Path': '/eng/', 'UserName': 'alice', 'UserId': 'AIDAEXAMPLEEXAMPLE1', 'CreateDate': '2026-01-01T00:00:00Z',
            'Arn': f'arn:aws:iam::{ACCOUNT}:user/eng/alice',
        }}, {'UserName': 'alice'})
        stub.add_client_error('get_user', 'NoSuchEntity', expected_params={'UserName': 'bob'})
        with stub:
            self.assertEqual(lookups.iam_user_arn('alice', REGION), f'arn:aws:iam::{ACCOUNT}:user/eng/alice')
            self.assertIsNone(lookups.iam_user_arn('bob', REGION))


if __name__ == '__main__':
    unittest.main()
