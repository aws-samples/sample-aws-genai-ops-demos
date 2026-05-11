"""
Ingest Prowler OCSF findings.

Triggered by S3:ObjectCreated on raw-reports/*.ocsf.json. Parses the OCSF
document (one JSON object per line OR a JSON array — Prowler has shipped both
shapes across versions), upserts one item per finding to DynamoDB, and:

    - For CRITICAL/HIGH findings: publishes to the DevOps Agent SNS topic and
      invokes the remediation-context Lambda asynchronously so Bedrock
      generates a markdown remediation playbook.
    - For MEDIUM/LOW/INFO findings: no downstream fan-out.

The `finding_uid` field (PK) is Prowler's stable hash of (check_id +
resource_uid), so re-running a scan updates the same DynamoDB item instead of
creating duplicates.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any
from urllib.parse import unquote_plus

import boto3
from boto3.dynamodb.conditions import Key  # noqa: F401  (handy for future queries)

from cost_events import log_cost_event

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_s3 = boto3.client('s3')
_ddb = boto3.resource('dynamodb')
_sns = boto3.client('sns')
_lambda = boto3.client('lambda')

TABLE_NAME = os.environ['FINDINGS_TABLE']
SNS_TOPIC_ARN = os.environ['DEVOPS_AGENT_TOPIC_ARN']
REMEDIATION_LAMBDA = os.environ['REMEDIATION_LAMBDA']

# AUTO_INVESTIGATE=true → every CRITICAL/HIGH finding fires the DevOps Agent
# webhook automatically on ingest (fire-and-forget). Default false so the TAM
# drives investigation from the dashboard with the 'Investigate' button —
# controlled narrative, no runaway agent costs in demo accounts.
AUTO_INVESTIGATE = os.environ.get('AUTO_INVESTIGATE', 'false').lower() == 'true'

_table = _ddb.Table(TABLE_NAME)


def _load_ocsf(body: bytes) -> list[dict[str, Any]]:
    text = body.decode('utf-8').strip()
    if not text:
        return []
    # Prowler json-ocsf output may be either a JSON array or newline-delimited JSON.
    if text.startswith('['):
        data = json.loads(text)
        return data if isinstance(data, list) else []
    findings: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        findings.append(json.loads(line))
    return findings


def _severity(finding: dict[str, Any]) -> str:
    raw = finding.get('severity') or finding.get('severity_id')
    if isinstance(raw, str):
        return raw.upper() or 'UNKNOWN'
    # OCSF severity_id: 1=Info 2=Low 3=Medium 4=High 5=Critical 6=Fatal
    mapping = {1: 'INFO', 2: 'LOW', 3: 'MEDIUM', 4: 'HIGH', 5: 'CRITICAL', 6: 'CRITICAL'}
    return mapping.get(int(raw or 0), 'UNKNOWN')


def _status(finding: dict[str, Any]) -> str:
    # OCSF status_code: PASS/FAIL/WARN/UNKNOWN; Prowler also uses status: PASS/FAIL/MANUAL
    for key in ('status_code', 'status', 'status_detail'):
        val = finding.get(key)
        if isinstance(val, str) and val:
            return val.upper()
    return 'UNKNOWN'


def _resource_uid(finding: dict[str, Any]) -> str:
    resources = finding.get('resources') or []
    if isinstance(resources, list) and resources:
        first = resources[0]
        if isinstance(first, dict):
            return first.get('uid') or first.get('name') or 'unknown'
    return finding.get('resource_uid') or 'unknown'


# Prowler OCSF v1.5 doesn't carry a top-level service_name; we infer it from
# the first segment of metadata.event_code (e.g. "accessanalyzer_enabled" →
# "accessanalyzer"). Map the ugly slug to a human-friendly AWS service name.
_SERVICE_MAP = {
    'accessanalyzer': 'IAM Access Analyzer',
    'account': 'AWS Account',
    'acm': 'ACM',
    'apigateway': 'API Gateway',
    'apigatewayv2': 'API Gateway v2',
    'appstream': 'AppStream',
    'appsync': 'AppSync',
    'athena': 'Athena',
    'autoscaling': 'Auto Scaling',
    'awslambda': 'Lambda',
    'backup': 'Backup',
    'cloudformation': 'CloudFormation',
    'cloudfront': 'CloudFront',
    'cloudtrail': 'CloudTrail',
    'cloudwatch': 'CloudWatch',
    'codeartifact': 'CodeArtifact',
    'codebuild': 'CodeBuild',
    'cognito': 'Cognito',
    'config': 'AWS Config',
    'directconnect': 'Direct Connect',
    'directoryservice': 'Directory Service',
    'dlm': 'DLM',
    'dms': 'DMS',
    'documentdb': 'DocumentDB',
    'drs': 'DRS',
    'dynamodb': 'DynamoDB',
    'ec2': 'EC2',
    'ecr': 'ECR',
    'ecs': 'ECS',
    'efs': 'EFS',
    'eks': 'EKS',
    'elasticache': 'ElastiCache',
    'elasticbeanstalk': 'Elastic Beanstalk',
    'elb': 'ELB',
    'elbv2': 'ELB v2',
    'emr': 'EMR',
    'eventbridge': 'EventBridge',
    'firehose': 'Firehose',
    'fms': 'FMS',
    'glacier': 'Glacier',
    'globalaccelerator': 'Global Accelerator',
    'glue': 'Glue',
    'guardduty': 'GuardDuty',
    'iam': 'IAM',
    'inspector2': 'Inspector',
    'kafka': 'MSK',
    'kinesis': 'Kinesis',
    'kms': 'KMS',
    'lightsail': 'Lightsail',
    'macie': 'Macie',
    'memorydb': 'MemoryDB',
    'mq': 'MQ',
    'neptune': 'Neptune',
    'networkfirewall': 'Network Firewall',
    'opensearch': 'OpenSearch',
    'organizations': 'Organizations',
    'rds': 'RDS',
    'redshift': 'Redshift',
    'resourceexplorer2': 'Resource Explorer',
    'route53': 'Route 53',
    's3': 'S3',
    'sagemaker': 'SageMaker',
    'secretsmanager': 'Secrets Manager',  # pragma: allowlist secret - service label
    'securityhub': 'Security Hub',
    'servicecatalog': 'Service Catalog',
    'ses': 'SES',
    'shield': 'Shield',
    'sns': 'SNS',
    'sqs': 'SQS',
    'ssm': 'Systems Manager',
    'ssmincidents': 'SSM Incidents',
    'stepfunctions': 'Step Functions',
    'sts': 'STS',
    'support': 'Support',
    'transfer': 'Transfer Family',
    'trustedadvisor': 'Trusted Advisor',
    'vpc': 'VPC',
    'waf': 'WAF',
    'wafv2': 'WAF v2',
    'wellarchitected': 'Well-Architected',
    'workspaces': 'WorkSpaces',
}


def _service_from_event_code(event_code: str) -> str:
    """Extract the service segment from a Prowler event_code.

    event_code is always '<service>_<check_name>' (e.g. 'accessanalyzer_enabled',
    'ec2_securitygroup_allow_ingress_from_internet_to_port_22').
    """
    if not event_code:
        return ''
    return event_code.split('_', 1)[0].lower()


def _service_name(finding: dict[str, Any]) -> str:
    """Best-effort service label for the UI."""
    metadata = finding.get('metadata') or {}
    event_code = metadata.get('event_code') or ''
    slug = _service_from_event_code(event_code)
    if slug and slug in _SERVICE_MAP:
        return _SERVICE_MAP[slug]
    if slug:
        return slug  # unknown-but-nonempty is still better than 'unknown'
    # Last resort: derive from resource ARN prefix
    resources = finding.get('resources') or []
    if isinstance(resources, list) and resources:
        first = resources[0]
        if isinstance(first, dict):
            arn = first.get('uid') or ''
            # arn:aws:<service>:region:...
            parts = arn.split(':')
            if len(parts) > 2 and parts[0] == 'arn':
                return _SERVICE_MAP.get(parts[2].lower(), parts[2].lower())
    return 'unknown'


def _to_item(finding: dict[str, Any], scan_id: str, now: str) -> dict[str, Any]:
    metadata = finding.get('metadata') or {}
    product = metadata.get('product') or {}
    finding_info = finding.get('finding_info') or {}
    check_id = (
        finding.get('check_id')
        or metadata.get('event_code')  # Prowler OCSF puts the rule identifier here
        or finding_info.get('uid')
        or finding_info.get('title')
        or 'unknown'
    )
    # IMPORTANT: the finding_uid MUST be unique per (check, resource).
    # Prowler's finding_info.uid is the CHECK identifier and repeats across
    # all resources that the check evaluated — using it as PK means every new
    # resource overwrites the previous one and you end up with one row per
    # check instead of one per finding. Always suffix with the resource uid.
    resource_uid = _resource_uid(finding)
    finding_uid = f"{check_id}:{resource_uid}"
    # Prowler compliance lives in unmapped.compliance as a dict mapping
    # framework name → list of control IDs. Keep the full map (control IDs
    # are the actionable detail auditors need) and derive the sorted framework
    # list for quick-filter chips in the UI.
    unmapped = finding.get('unmapped') or {}
    frameworks: list[str] = []
    compliance_controls: dict[str, list[str]] = {}
    if isinstance(unmapped, dict):
        raw_compliance = unmapped.get('compliance')
        if isinstance(raw_compliance, dict):
            frameworks = sorted({str(k) for k in raw_compliance.keys()})
            for fw, ctrls in raw_compliance.items():
                if isinstance(ctrls, list):
                    compliance_controls[str(fw)] = [str(c) for c in ctrls if c]
                elif ctrls:
                    compliance_controls[str(fw)] = [str(ctrls)]
        elif isinstance(raw_compliance, list):
            frameworks = [str(x) for x in raw_compliance]
    cloud = finding.get('cloud') or {}
    account = cloud.get('account') if isinstance(cloud, dict) else {}
    region = cloud.get('region') if isinstance(cloud, dict) else None

    # Prowler's own canonical remediation guidance — the same text the Prowler
    # Hub web page shows. Surfacing it structured rather than burying it inside
    # `raw` means the UI can render it as a first-class section and the model
    # can build on top of it instead of reinventing it.
    remediation = finding.get('remediation') or {}
    remediation_guidance = remediation.get('desc') or ''
    references = remediation.get('references') or []
    remediation_url = references[0] if isinstance(references, list) and references else ''

    # Extra context pulled from unmapped / finding_info. All of this is data
    # Prowler already produces; we just promote it from `raw` to structured
    # attributes so the dashboard can filter and display them.
    additional_urls: list[str] = []
    categories: list[str] = []
    notes = ''
    if isinstance(unmapped, dict):
        raw_urls = unmapped.get('additional_urls')
        if isinstance(raw_urls, list):
            additional_urls = [str(u) for u in raw_urls if u]
        raw_cats = unmapped.get('categories')
        if isinstance(raw_cats, list):
            categories = [str(c) for c in raw_cats if c]
        notes = unmapped.get('notes') or ''

    finding_types: list[str] = []
    raw_types = finding_info.get('types') if isinstance(finding_info, dict) else None
    if isinstance(raw_types, list):
        finding_types = [str(t) for t in raw_types if t]

    risk_details = finding.get('risk_details') or ''

    item: dict[str, Any] = {
        'finding_uid': finding_uid,
        'scan_id': scan_id,
        'last_seen_at': now,
        'severity': _severity(finding),
        'status': _status(finding),
        'check_id': check_id,
        'check_title': finding_info.get('title') or finding.get('message') or check_id,
        'check_description': finding_info.get('desc') or finding.get('message') or '',
        'status_extended': finding.get('status_detail') or '',
        'service_name': _service_name(finding),
        'resource_uid': resource_uid,
        'region': region or os.environ.get('AWS_REGION', ''),
        'account_id': (account or {}).get('uid') or os.environ.get('AWS_ACCOUNT_ID', ''),
        'compliance_frameworks': frameworks,
        'raw': json.dumps(finding)[:350_000],  # DynamoDB item limit safety
    }
    # Only write optional Prowler-native fields when non-empty so the table
    # stays lean for checks that do not supply them. The UI treats the absence
    # of an attribute as "no Prowler guidance for this check".
    if remediation_guidance:
        item['remediation_guidance'] = remediation_guidance
    if remediation_url:
        item['remediation_url'] = remediation_url
    if additional_urls:
        item['additional_urls'] = additional_urls
    if categories:
        item['categories'] = categories
    if notes:
        item['notes'] = notes
    if finding_types:
        item['finding_types'] = finding_types
    if risk_details:
        item['risk_details'] = risk_details
    if compliance_controls:
        item['compliance_controls'] = compliance_controls
    return item


MAX_HISTORY_ENTRIES = 20


def _batch_get_previous(finding_uids: list[str]) -> dict[str, dict]:
    """Fetch existing items for a list of finding_uids, keyed by uid.

    Used to preserve status_history across scans: the previous row's
    history + its current status get appended to the new entry.
    Returns an empty dict on the first scan (nothing to fetch) or if
    any single page fails (best-effort; history is not load-bearing).
    """
    out: dict[str, dict] = {}
    if not finding_uids:
        return out
    ddb = _ddb.meta.client
    # BatchGetItem caps at 100 keys per request.
    CHUNK = 100
    for i in range(0, len(finding_uids), CHUNK):
        chunk = finding_uids[i:i + CHUNK]
        try:
            resp = ddb.batch_get_item(RequestItems={
                TABLE_NAME: {
                    'Keys': [{'finding_uid': uid} for uid in chunk],
                    'ProjectionExpression': 'finding_uid, #st, last_seen_at, scan_id, status_history',
                    'ExpressionAttributeNames': {'#st': 'status'},
                },
            })
            for existing in resp.get('Responses', {}).get(TABLE_NAME, []):
                out[existing['finding_uid']] = existing
        except Exception as exc:  # noqa: BLE001 — best effort
            logger.warning('batch_get_item failed for chunk of %d: %s', len(chunk), exc)
    return out


def _next_history(previous: dict | None, new_status: str, new_scan_id: str, now: str) -> list[dict]:
    """Append a new (scan_id, status, ts) entry to the existing history.

    If status is unchanged from the last entry we keep the list stable to
    avoid unnecessary row churn. History is truncated to the most recent
    MAX_HISTORY_ENTRIES so item size stays bounded.
    """
    prev_history = (previous or {}).get('status_history') or []
    if not isinstance(prev_history, list):
        prev_history = []
    prev_status = (previous or {}).get('status')
    if prev_history and prev_history[-1].get('status') == new_status:
        # Same status — refresh last_seen_at on the tail entry rather than grow.
        prev_history[-1]['last_seen_at'] = now
        prev_history[-1]['scan_id'] = new_scan_id
        return prev_history[-MAX_HISTORY_ENTRIES:]
    entry = {'scan_id': new_scan_id, 'status': new_status, 'last_seen_at': now}
    # First scan ever: seed history with a single entry.
    if not prev_history and not prev_status:
        return [entry]
    # Status changed: append.
    return (prev_history + [entry])[-MAX_HISTORY_ENTRIES:]


def handler(event, context):
    logger.info('Received S3 event with %d records', len(event.get('Records', [])))
    ingested = 0
    dispatched_critical = 0

    for record in event.get('Records', []):
        bucket = record['s3']['bucket']['name']
        key = unquote_plus(record['s3']['object']['key'])
        logger.info('Processing s3://%s/%s', bucket, key)

        obj = _s3.get_object(Bucket=bucket, Key=key)
        findings = _load_ocsf(obj['Body'].read())
        scan_id = key.split('/')[1] if key.startswith('raw-reports/') else 'unknown'
        now = datetime.utcnow().isoformat() + 'Z'

        # Build all items first so we know which UIDs to pre-fetch for history.
        new_items = [_to_item(f, scan_id, now) for f in findings]
        uids = [it['finding_uid'] for it in new_items]
        previous_rows = _batch_get_previous(uids)

        with _table.batch_writer(overwrite_by_pkeys=['finding_uid']) as writer:
            for item in new_items:
                prev = previous_rows.get(item['finding_uid'])
                item['status_history'] = _next_history(prev, item['status'], scan_id, now)
                # Carry forward `first_seen_at` so the UI can show "new this scan".
                if prev and prev.get('last_seen_at'):
                    item['first_seen_at'] = prev.get('first_seen_at') or prev['last_seen_at']
                else:
                    item['first_seen_at'] = now
                writer.put_item(Item=item)
                ingested += 1

                # Bedrock Insights are generated lazily on demand (POST
                # /findings/{uid}/insights) to avoid blasting Bedrock with
                # hundreds of calls on every scan. The UI's "Generate insights"
                # button is the narrative path.
                #
                # AUTO_INVESTIGATE=true still opts in to auto-publish
                # CRITICAL/HIGH findings to the DevOps Agent webhook on ingest —
                # the agent itself handles the investigation, which is a
                # separate flow from the insights generation.
                if item['status'] == 'FAIL' and AUTO_INVESTIGATE and item['severity'] in {'CRITICAL', 'HIGH'}:
                    _sns.publish(
                        TopicArn=SNS_TOPIC_ARN,
                        Subject=f"Prowler {item['severity']}: {item['check_id']}",
                        Message=json.dumps(item, default=str),
                    )
                    dispatched_critical += 1

        logger.info('Ingested %d findings from scan %s', ingested, scan_id)

        # One S3 ObjectCreated notification = one completed Prowler scan
        # (1 Fargate task run). Log a single cost event per scan so the Cost
        # page can track scanner spend without ingesting the noise of
        # per-finding events.
        log_cost_event(
            'scan',
            metadata={'scan_id': scan_id, 'findings_ingested': ingested},
        )

    return {
        'statusCode': 200,
        'body': json.dumps({'ingested': ingested, 'dispatched': dispatched_critical}),
    }
