"""Checks rules.json against an installed Prowler (pip install prowler==<rules.json prowlerVersion>).

Skipped when Prowler is not importable. Run before bumping the scanner's Prowler version.
"""

import json
import types
import unittest

from support import ACCOUNT, REGION
import index

try:
    from prowler.lib.check.utils import recover_checks_from_provider
    from prowler.providers.aws.aws_provider import AwsProvider
except ImportError:  # pragma: no cover - optional dependency
    AwsProvider = None

SAMPLE_TARGETS = {
    'security_group': (f'arn:aws:ec2:{REGION}:{ACCOUNT}:security-group/sg-0aaa',
                       f'arn:aws:ec2:{REGION}:{ACCOUNT}:vpc/vpc-0111'),
    's3_bucket': ('arn:aws:s3:::demo-bucket',),
    's3_account': (f'arn:aws:s3:{REGION}:{ACCOUNT}:account',),
    'iam_user': (f'arn:aws:iam::{ACCOUNT}:user/engineering/alice',),
    'lambda_function': (f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:orders',),
}


@unittest.skipIf(AwsProvider is None, 'prowler is not installed')
class ProwlerCompatibilityTest(unittest.TestCase):
    def test_every_check_exists(self):
        available = {name for name, _ in recover_checks_from_provider('aws')}
        for rule in index.RULES:
            self.assertEqual(sorted(set(rule.checks) - available), [], rule.id)

    def test_resource_arns_keep_every_mapped_check(self):
        # Prowler intersects --checks with the checks it derives from the --resource-arn services.
        for group in sorted({r.group for r in index.RULES if r.resolver != 'region'}):
            rules = [r for r in index.RULES if r.group == group]
            arns = [arn for r in rules for arn in SAMPLE_TARGETS[r.resolver]]
            for arn in arns:
                self.assertRegex(arn, index.ARN_PATTERN)
            provider = types.SimpleNamespace(_audit_resources=arns, type='aws')
            selected = set(AwsProvider.get_checks_from_input_arn(provider))
            mapped = {c for r in rules for c in r.checks}
            self.assertEqual(sorted(mapped - selected), [], group)

    def test_rules_file_names_the_verified_version(self):
        from importlib.metadata import version
        with open(index.RULES_FILE) as fh:
            expected = json.load(fh)['prowlerVersion']
        self.assertEqual(version('prowler'), expected)


if __name__ == '__main__':
    unittest.main()
