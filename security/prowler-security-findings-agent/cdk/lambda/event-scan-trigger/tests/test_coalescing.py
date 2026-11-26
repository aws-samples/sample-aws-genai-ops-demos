import json
import unittest

from botocore.exceptions import ClientError

from support import (ACCOUNT, CONFIG, REGION, FakeEcs, FakeLookups, authorize_sg_ingress, cloudtrail_event,
                     environment, sqs_record)
import index


def run(records, lookups=None, ecs=None):
    ecs = ecs or FakeEcs()
    result = index.process(records, index.RULES, CONFIG, lookups or FakeLookups(), ecs, REGION)
    return result, ecs


def sg_lookups(count):
    return FakeLookups(groups={f'sg-{i:04d}': f'vpc-{i % 3}' for i in range(count)})


class CoalescingTest(unittest.TestCase):
    def test_burst_on_one_group_starts_one_task(self):
        records = [sqs_record(authorize_sg_ingress('sg-0001', port=p)) for p in range(20, 50)]
        result, ecs = run(records, sg_lookups(2))
        self.assertEqual(result, {'batchItemFailures': []})
        self.assertEqual(len(ecs.calls), 1)
        env = environment(ecs.calls[0])
        self.assertEqual(env['PROWLER_RESOURCE_ARNS'].split(), [
            f'arn:aws:ec2:{REGION}:{ACCOUNT}:security-group/sg-0001',
            f'arn:aws:ec2:{REGION}:{ACCOUNT}:vpc/vpc-1',
        ])

    def test_mixed_burst_starts_one_task_per_group(self):
        records = [
            sqs_record(authorize_sg_ingress('sg-0001')),
            sqs_record(authorize_sg_ingress('sg-0002')),
            sqs_record(cloudtrail_event('aws.s3', 'PutBucketPolicy', request={'bucketName': 'a'})),
            sqs_record(cloudtrail_event('aws.s3', 'PutAccountPublicAccessBlock', request={})),
            sqs_record(cloudtrail_event('aws.iam', 'AttachUserPolicy', request={'userName': 'alice'})),
            sqs_record(cloudtrail_event('aws.lambda', 'AddPermission20150331v2', request={'functionName': 'f'})),
            sqs_record(cloudtrail_event('aws.rds', 'ModifyDBInstance', request={'dBInstanceIdentifier': 'db'})),
        ]
        lookups = sg_lookups(3)
        lookups.users = {'alice': f'arn:aws:iam::{ACCOUNT}:user/alice'}
        _, ecs = run(records, lookups)
        by_group = {environment(c)['EVENT_SCAN_ID'].split('-event-')[1].rsplit('-', 1)[0]: environment(c)
                    for c in ecs.calls}
        self.assertEqual(sorted(by_group), ['awslambda-function', 'ec2-securitygroup', 'iam-user', 'rds-instance', 's3'])
        for group, env in by_group.items():
            services = {arn.split(':')[2] for arn in env.get('PROWLER_RESOURCE_ARNS', '').split()}
            self.assertLessEqual(len(services), 1, f'{group} mixes services: {services}')
        s3 = by_group['s3']
        self.assertEqual(sorted(s3['PROWLER_RESOURCE_ARNS'].split()),
                         ['arn:aws:s3:::a', f'arn:aws:s3:{REGION}:{ACCOUNT}:account'])
        self.assertIn('s3_account_level_public_access_blocks', s3['PROWLER_CHECKS'].split())
        self.assertIn('s3_bucket_public_access', s3['PROWLER_CHECKS'].split())
        rds = by_group['rds-instance']
        self.assertEqual(rds['PROWLER_REGION'], REGION)
        self.assertNotIn('PROWLER_RESOURCE_ARNS', rds)

    def test_region_scoped_rules_get_one_task_per_region(self):
        records = [sqs_record(cloudtrail_event('aws.rds', 'ModifyDBInstance', region=r, request={}))
                   for r in ('us-east-1', 'eu-west-1', 'us-east-1')]
        _, ecs = run(records)
        self.assertEqual(sorted(environment(c)['PROWLER_REGION'] for c in ecs.calls), ['eu-west-1', 'us-east-1'])

    def test_large_burst_is_split_under_the_override_limit(self):
        count = 300
        records = [sqs_record(authorize_sg_ingress(f'sg-{i:04d}')) for i in range(count)]
        _, ecs = run(records, sg_lookups(count))
        self.assertGreater(len(ecs.calls), 1)
        seen_groups = []
        for call in ecs.calls:
            self.assertLessEqual(len(json.dumps(call['overrides'])), 8192)
            arns = environment(call)['PROWLER_RESOURCE_ARNS'].split()
            groups = [a for a in arns if ':security-group/' in a]
            self.assertLessEqual(len(groups), index.MAX_UNITS_PER_TASK)
            vpcs = {a.rsplit('/', 1)[1] for a in arns if ':vpc/' in a}
            for sg in groups:
                self.assertIn(f'vpc-{int(sg[-4:]) % 3}', vpcs, 'security group split from its VPC')
            seen_groups.extend(groups)
        self.assertEqual(len(seen_groups), count)
        self.assertEqual(len(set(seen_groups)), count)


class HandlerTest(unittest.TestCase):
    def test_run_task_request(self):
        _, ecs = run([sqs_record(authorize_sg_ingress('sg-0001'))], sg_lookups(2))
        call = ecs.calls[0]
        self.assertEqual(call['cluster'], CONFIG.cluster_arn)
        self.assertEqual(call['taskDefinition'], CONFIG.task_definition_arn)
        self.assertEqual(call['launchType'], 'FARGATE')
        self.assertEqual(call['startedBy'], 'prowler-event-scan')
        self.assertEqual(call['networkConfiguration'], {'awsvpcConfiguration': {
            'subnets': ['subnet-aaa', 'subnet-bbb'], 'securityGroups': ['sg-scanner'], 'assignPublicIp': 'DISABLED',
        }})
        self.assertEqual(call['overrides']['containerOverrides'][0]['name'], 'Prowler')
        env = environment(call)
        self.assertEqual(env['SCAN_MODE'], 'event')
        self.assertRegex(env['EVENT_SCAN_ID'], r'^[0-9]{8}T[0-9]{6}Z-event-ec2-securitygroup-[0-9a-f]{6}$')
        self.assertIn('ec2_securitygroup_allow_ingress_from_internet_to_tcp_port_22', env['PROWLER_CHECKS'].split())
        self.assertNotIn('PROWLER_REGION', env)

    def test_failed_launch_retries_only_its_messages(self):
        sg = sqs_record(authorize_sg_ingress('sg-0001'), 'm-sg')
        s3 = sqs_record(cloudtrail_event('aws.s3', 'PutBucketPolicy', request={'bucketName': 'a'}), 'm-s3')
        ecs = FakeEcs(fail_when=lambda env: '-event-s3-' in env['EVENT_SCAN_ID'])
        result, ecs = run([sg, s3], sg_lookups(2), ecs)
        self.assertEqual(result, {'batchItemFailures': [{'itemIdentifier': 'm-s3'}]})
        self.assertEqual(len(ecs.calls), 2)

    def test_lookup_errors_are_retried_and_bad_messages_dropped(self):
        throttled = ClientError({'Error': {'Code': 'RequestLimitExceeded', 'Message': 'slow down'}},
                                'DescribeSecurityGroups')
        records = [
            sqs_record(authorize_sg_ingress('sg-0001'), 'm-sg'),
            sqs_record('not json', 'm-garbage'),
            sqs_record(cloudtrail_event('aws.lambda', 'AddPermission20150331v2', request={'functionName': 'f'}), 'm-fn'),
        ]
        result, ecs = run(records, FakeLookups(error=throttled))
        self.assertEqual(result, {'batchItemFailures': [{'itemIdentifier': 'm-sg'}]})
        self.assertEqual(len(ecs.calls), 1)
        self.assertIn('function:f', environment(ecs.calls[0])['PROWLER_RESOURCE_ARNS'])

    def test_nothing_to_scan_starts_nothing(self):
        result, ecs = run([sqs_record(authorize_sg_ingress('sg-gone'))], FakeLookups())
        self.assertEqual(result, {'batchItemFailures': []})
        self.assertEqual(ecs.calls, [])


if __name__ == '__main__':
    unittest.main()
