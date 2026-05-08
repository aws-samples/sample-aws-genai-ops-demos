"""
Generate a remediation playbook for a Prowler finding using Amazon Bedrock.

Invoked asynchronously by the ingest-findings Lambda for every CRITICAL/HIGH
finding. Calls the Converse API (default model: amazon.nova-pro-v1:0) with a
system prompt that constrains Nova to return a three-section markdown
playbook: Impact / Root cause / Remediation steps (with CLI and CDK/Terraform
snippets).

The markdown is stored in S3 at s3://{REMEDIATIONS_BUCKET}/{finding_uid}.md
and the DynamoDB item is updated with `remediation_s3_key` and
`remediation_generated_at` so the dashboard and the DevOps Agent webhook can
reference it.
"""

import json
import logging
import os

import boto3

from cost_events import log_cost_event

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_bedrock = boto3.client('bedrock-runtime')
_s3 = boto3.client('s3')
_ddb = boto3.resource('dynamodb')

TABLE_NAME = os.environ['FINDINGS_TABLE']
BUCKET = os.environ['REMEDIATIONS_BUCKET']
MODEL_ID = os.environ['BEDROCK_MODEL_ID']

_table = _ddb.Table(TABLE_NAME)

SYSTEM_PROMPT = """You are an AWS security engineer. Given a Prowler security finding in OCSF form,
produce a concise Markdown playbook tailored to the finding's status:

- If status is FAIL: produce a **remediation playbook** with the three sections
  `## Impact`, `## Root cause`, `## Remediation steps` (numbered steps +
  fenced `bash` AWS CLI + fenced `typescript` AWS CDK v2 snippet).
- If status is PASS: produce a **hardening playbook** with the three sections
  `## Why this passes`, `## What it protects against`, `## How to keep it
  passing` (monitoring/guardrails, with the same code blocks).
- If status is MANUAL: produce a **review playbook** with `## What to review`,
  `## Decision criteria`, `## Next steps` (checklist the human operator
  should work through).

Keep the whole response under 900 words. Do not invent ARNs or account IDs;
use placeholders like <account-id> or <resource-arn>."""


def _build_user_prompt(item: dict) -> str:
    # Give Nova the Prowler-native remediation, categories, notes, and
    # control IDs up front. This keeps the model from reinventing guidance
    # the scanner has already produced and lets it expand on concrete
    # references rather than on-the-fly approximations.
    parts = [
        "Here is the Prowler finding (OCSF):",
        f"- finding_uid: {item.get('finding_uid')}",
        f"- severity: {item.get('severity')}",
        f"- status: {item.get('status')}",
        f"- check_id: {item.get('check_id')}",
        f"- check_title: {item.get('check_title')}",
        f"- check_description: {item.get('check_description')}",
        f"- service: {item.get('service_name')}",
        f"- resource: {item.get('resource_uid')}",
        f"- region: {item.get('region')}",
        f"- compliance: {', '.join(item.get('compliance_frameworks') or [])}",
        f"- status_extended: {item.get('status_extended')}",
    ]
    risk_details = item.get('risk_details')
    if risk_details:
        parts.append(f"- risk_details: {risk_details}")
    categories = item.get('categories')
    if categories:
        parts.append(f"- categories: {', '.join(categories)}")
    notes = item.get('notes')
    if notes:
        parts.append(f"- notes: {notes}")
    finding_types = item.get('finding_types')
    if finding_types:
        parts.append(f"- finding_types: {', '.join(finding_types)}")

    # Include per-framework control IDs if present — auditors care about the
    # exact control that failed, not just the framework name.
    controls = item.get('compliance_controls')
    if isinstance(controls, dict) and controls:
        parts.append("- compliance_controls:")
        for fw, ids in list(controls.items())[:20]:
            if isinstance(ids, list):
                parts.append(f"    {fw}: {', '.join(ids[:10])}")

    guidance = item.get('remediation_guidance')
    if guidance:
        parts.append("")
        parts.append(
            "Prowler's canonical remediation guidance (use this as the baseline and "
            "elaborate on it; do not reinvent the steps):"
        )
        parts.append(guidance)

    remediation_url = item.get('remediation_url')
    if remediation_url:
        parts.append(f"Prowler reference: {remediation_url}")
    additional_urls = item.get('additional_urls')
    if additional_urls:
        parts.append(f"Additional references: {', '.join(additional_urls[:5])}")

    parts.extend([
        "",
        "Raw OCSF payload (truncated):",
        (item.get('raw') or '')[:8000],
    ])
    return "\n".join(parts)


def handler(event, context):
    finding_uid = event.get('finding_uid')
    if not finding_uid:
        logger.error('remediation-context invoked without finding_uid')
        return {'statusCode': 400, 'body': 'finding_uid missing'}

    logger.info('Generating remediation for %s via %s', finding_uid, MODEL_ID)

    response = _table.get_item(Key={'finding_uid': finding_uid})
    item = response.get('Item')
    if not item:
        logger.error('Finding %s not found', finding_uid)
        return {'statusCode': 404, 'body': 'finding not found'}

    converse = _bedrock.converse(
        modelId=MODEL_ID,
        system=[{'text': SYSTEM_PROMPT}],
        messages=[{'role': 'user', 'content': [{'text': _build_user_prompt(item)}]}],
        inferenceConfig={'maxTokens': 1500, 'temperature': 0.2},
    )

    blocks = converse.get('output', {}).get('message', {}).get('content', [])
    markdown = ''.join(b.get('text', '') for b in blocks if isinstance(b, dict))
    if not markdown.strip():
        logger.error('Bedrock returned empty remediation for %s', finding_uid)
        return {'statusCode': 502, 'body': 'empty remediation'}

    # Log the Bedrock cost event — tokens come straight from the Converse
    # response so the Cost page shows the real spend, not an estimate.
    usage = converse.get('usage') or {}
    log_cost_event(
        'bedrock_insights',
        finding_uid=finding_uid,
        model_id=MODEL_ID,
        input_tokens=int(usage.get('inputTokens') or 0),
        output_tokens=int(usage.get('outputTokens') or 0),
        metadata={'check_id': item.get('check_id'), 'severity': item.get('severity')},
    )

    s3_key = f"{finding_uid}.md"
    _s3.put_object(
        Bucket=BUCKET,
        Key=s3_key,
        Body=markdown.encode('utf-8'),
        ContentType='text/markdown',
        Metadata={'finding_uid': finding_uid, 'model_id': MODEL_ID},
    )

    _table.update_item(
        Key={'finding_uid': finding_uid},
        UpdateExpression=(
            'SET remediation_s3_key = :k, remediation_generated_at = :t, remediation_model = :m'
        ),
        ExpressionAttributeValues={
            ':k': s3_key,
            ':t': context.aws_request_id if context else 'local',
            ':m': MODEL_ID,
        },
    )

    logger.info('Remediation written to s3://%s/%s', BUCKET, s3_key)
    return {'statusCode': 200, 'body': json.dumps({'remediation_s3_key': s3_key})}
