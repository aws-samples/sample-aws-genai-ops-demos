"""Contract between the trigger's container overrides and scanner/entrypoint.sh, using fake prowler/aws CLIs."""

import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest

from support import ACCOUNT, LAMBDA_DIR

ENTRYPOINT = os.path.normpath(os.path.join(LAMBDA_DIR, '..', '..', '..', 'scanner', 'entrypoint.sh'))
BUCKET = 'raw-bucket'

FAKE_PROWLER = textwrap.dedent('''\
    #!/bin/bash
    printf '%s\\n' "$@" > "$FAKE_LOG_DIR/prowler.args"
    out=""; name=""; formats=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --output-directory) out="$2"; shift 2 ;;
            --output-filename) name="$2"; shift 2 ;;
            --output-formats) shift; while [[ $# -gt 0 && "$1" != -* ]]; do formats="$formats $1"; shift; done ;;
            *) shift ;;
        esac
    done
    if [[ "${FAKE_FINDINGS:-1}" == "1" ]]; then
        echo '[]' > "$out/$name.ocsf.json"
        [[ "$formats" == *csv* ]] && echo 'x' > "$out/$name.csv"
        [[ "$formats" == *html* ]] && echo 'x' > "$out/$name.html"
    fi
    exit "${FAKE_EXIT:-0}"
''')
FAKE_AWS = '#!/bin/bash\necho "$*" >> "$FAKE_LOG_DIR/aws.calls"\n'


class EntrypointTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix='entrypoint-test-')
        self.bin = os.path.join(self.work, 'bin')
        os.makedirs(self.bin)
        for name, body in (('prowler', FAKE_PROWLER), ('aws', FAKE_AWS)):
            path = os.path.join(self.bin, name)
            with open(path, 'w') as fh:
                fh.write(body)
            os.chmod(path, 0o755)

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def run_entrypoint(self, **env):
        full_env = {
            'PATH': f"{self.bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            'TMPDIR': self.work,
            'FAKE_LOG_DIR': self.work,
            'RAW_REPORTS_BUCKET': BUCKET,
            'AWS_REGION': 'us-east-1',
            'AWS_ACCOUNT_ID': ACCOUNT,
        }
        full_env.update(env)
        return subprocess.run(['bash', ENTRYPOINT], env=full_env, capture_output=True, text=True, timeout=60)

    def prowler_args(self):
        path = os.path.join(self.work, 'prowler.args')
        if not os.path.exists(path):
            return None
        with open(path) as fh:
            return fh.read().splitlines()

    def aws_calls(self):
        path = os.path.join(self.work, 'aws.calls')
        if not os.path.exists(path):
            return []
        with open(path) as fh:
            return fh.read().splitlines()

    def test_full_scan_is_unchanged(self):
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.prowler_args()
        out_dir = args[args.index('--output-directory') + 1]
        scan_id = re.fullmatch(rf'{re.escape(self.work)}/prowler-out-([0-9]{{8}}T[0-9]{{6}}Z)', out_dir).group(1)
        self.assertEqual(args, [
            'aws', '--output-formats', 'json-ocsf', 'csv', 'html', '--output-directory', out_dir,
            '--output-filename', ACCOUNT, '--region', 'us-east-1', '--log-level', 'WARNING', '--no-banner', '-S',
        ])
        uploads = [c.split()[3] for c in self.aws_calls()]
        self.assertEqual(uploads, [
            f's3://{BUCKET}/raw-reports/{scan_id}/{ACCOUNT}.ocsf.json',
            f's3://{BUCKET}/raw-reports/{scan_id}/{ACCOUNT}.csv',
            f's3://{BUCKET}/raw-reports/{scan_id}/{ACCOUNT}.html',
        ])

    def test_full_scan_without_report_fails(self):
        result = self.run_entrypoint(FAKE_FINDINGS='0')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.aws_calls(), [])

    def test_event_scan_with_resource_arns(self):
        arns = (f'arn:aws:ec2:us-east-1:{ACCOUNT}:security-group/sg-0aaa '
                f'arn:aws:ec2:us-east-1:{ACCOUNT}:vpc/vpc-0111')
        result = self.run_entrypoint(
            SCAN_MODE='event',
            EVENT_SCAN_ID='20261125T193000Z-event-ec2-securitygroup-a1b2c3',
            PROWLER_CHECKS='ec2_securitygroup_allow_ingress_from_internet_to_tcp_port_22 ec2_securitygroup_default_restrict_traffic',
            PROWLER_RESOURCE_ARNS=arns,
            FAKE_EXIT='3',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.prowler_args()
        self.assertEqual(args[:2], ['aws', '--output-formats'])
        self.assertEqual(args[2], 'json-ocsf')
        checks_at = args.index('--checks')
        arns_at = args.index('--resource-arn')
        self.assertEqual(args[checks_at + 1:arns_at], [
            'ec2_securitygroup_allow_ingress_from_internet_to_tcp_port_22', 'ec2_securitygroup_default_restrict_traffic',
        ])
        self.assertEqual(args[arns_at + 1:arns_at + 3], arns.split())
        self.assertEqual(args[-2:], ['-S', '--skip-sh-update'])
        self.assertNotIn('--region', args)
        self.assertNotIn('csv', args)
        self.assertEqual([c.split()[3] for c in self.aws_calls()], [
            f's3://{BUCKET}/event-reports/20261125T193000Z-event-ec2-securitygroup-a1b2c3/{ACCOUNT}.ocsf.json',
        ])

    def test_event_scan_scoped_to_a_region(self):
        result = self.run_entrypoint(
            SCAN_MODE='event',
            EVENT_SCAN_ID='20261125T193000Z-event-rds-instance-a1b2c3',
            PROWLER_CHECKS='rds_instance_no_public_access',
            PROWLER_REGION='eu-west-1',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.prowler_args()
        self.assertEqual(args[args.index('--region') + 1], 'eu-west-1')
        self.assertNotIn('--resource-arn', args)

    def test_event_scan_without_findings_uploads_nothing(self):
        result = self.run_entrypoint(
            SCAN_MODE='event',
            EVENT_SCAN_ID='20261125T193000Z-event-s3-a1b2c3',
            PROWLER_CHECKS='s3_bucket_public_access',
            PROWLER_RESOURCE_ARNS='arn:aws:s3:::gone-bucket',
            FAKE_FINDINGS='0',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('nothing to upload', result.stdout)
        self.assertEqual(self.aws_calls(), [])

    def test_event_scan_rejects_bad_input_before_running_prowler(self):
        base = {
            'SCAN_MODE': 'event',
            'EVENT_SCAN_ID': '20261125T193000Z-event-s3-a1b2c3',
            'PROWLER_CHECKS': 's3_bucket_public_access',
            'PROWLER_RESOURCE_ARNS': 'arn:aws:s3:::bucket',
        }
        cases = {
            'scan id': {'EVENT_SCAN_ID': '../../etc'},
            'check id': {'PROWLER_CHECKS': 's3_bucket_public_access;rm'},
            'arn': {'PROWLER_RESOURCE_ARNS': 'not-an-arn'},
            'scope': {'PROWLER_RESOURCE_ARNS': ''},
            'region': {'PROWLER_RESOURCE_ARNS': '', 'PROWLER_REGION': 'us-east-1;x'},
            'mode': {'SCAN_MODE': 'partial'},
        }
        for name, override in cases.items():
            with self.subTest(name):
                result = self.run_entrypoint(**{**base, **override})
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(self.prowler_args())


if __name__ == '__main__':
    unittest.main()
