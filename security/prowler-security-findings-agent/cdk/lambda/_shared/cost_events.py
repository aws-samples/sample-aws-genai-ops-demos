"""
Shared helper that writes a per-invocation cost event to DynamoDB.

Every Lambda that performs a billable action (Bedrock Converse call, DevOps
Agent webhook dispatch, Fargate scan) calls `log_cost_event(...)` so the
dashboard Cost page can render a live stream of spend.

Pricing is hardcoded for demo simplicity — values come from the public AWS
pricing pages as of mid-2026. The point of the Cost panel is to teach the
operator *where* the money goes, not to serve as a billing of record;
AWS Cost Explorer is the source of truth for actual charges.
"""

import logging
import os
import time
import uuid
from decimal import Decimal
from typing import Any, Optional

import boto3

logger = logging.getLogger(__name__)

# The cost-events DynamoDB table. All callers read the name from the
# COST_EVENTS_TABLE env var so the CDK can wire it once per Lambda.
_COST_TABLE_NAME = os.environ.get('COST_EVENTS_TABLE', '')

_ddb = boto3.resource('dynamodb')
_table = _ddb.Table(_COST_TABLE_NAME) if _COST_TABLE_NAME else None

# Single GSI partition key — every row goes into the same "logical" bucket so
# we can Query by date without a full table scan.
_PARTITION = 'cost'

# TTL: cost events expire after 90 days. The dashboard only cares about the
# recent stream; keeping older rows would bloat queries.
_TTL_SECONDS = 90 * 24 * 60 * 60

# Hardcoded pricing table. Values are USD per 1K tokens (Bedrock) or USD per
# invocation (fixed-price events).
BEDROCK_PRICES_PER_1K = {
    # Amazon Nova Bedrock pricing. See:
    # https://aws.amazon.com/bedrock/pricing/
    'global.amazon.nova-2-lite-v1:0': {'input': Decimal('0.00006'), 'output': Decimal('0.00024')},
    'amazon.nova-2-lite-v1:0':        {'input': Decimal('0.00006'), 'output': Decimal('0.00024')},
    'eu.amazon.nova-pro-v1:0':   {'input': Decimal('0.0008'), 'output': Decimal('0.0032')},
    'amazon.nova-pro-v1:0':      {'input': Decimal('0.0008'), 'output': Decimal('0.0032')},
    'eu.amazon.nova-lite-v1:0':  {'input': Decimal('0.00006'), 'output': Decimal('0.00024')},
    'amazon.nova-lite-v1:0':     {'input': Decimal('0.00006'), 'output': Decimal('0.00024')},
    'eu.amazon.nova-micro-v1:0': {'input': Decimal('0.000035'), 'output': Decimal('0.00014')},
    'amazon.nova-micro-v1:0':    {'input': Decimal('0.000035'), 'output': Decimal('0.00014')},
}

# Fixed-price per invocation estimates (conservative demo values).
FIXED_PRICES = {
    # A full DevOps Agent investigation runs ~30-120 s at $29.88/hr on the
    # higher end. 0.50 USD per dispatch is a conservative average used for
    # the Cost panel; AWS billing will settle against actual usage.
    'devops_agent_dispatch': Decimal('0.50'),
    # 1 vCPU + 2 GB × ~5 min on Fargate eu-west-1 Linux on-demand pricing.
    # ($0.04048/vCPU-hr + $0.004445/GB-hr) × (5/60 hr) × (1 vCPU + 2 GB)
    # ≈ $0.0041 — round up to cover image pull + public IP.
    'scan': Decimal('0.02'),
}


def _bedrock_cost(model_id: str, input_tokens: int, output_tokens: int) -> Decimal:
    prices = BEDROCK_PRICES_PER_1K.get(model_id)
    if not prices:
        logger.warning('No pricing entry for model %s — cost will be 0', model_id)
        return Decimal('0')
    return (
        (Decimal(input_tokens) / Decimal(1000)) * prices['input']
        + (Decimal(output_tokens) / Decimal(1000)) * prices['output']
    ).quantize(Decimal('0.000001'))


def log_cost_event(
    event_type: str,
    *,
    finding_uid: Optional[str] = None,
    model_id: Optional[str] = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_ms: int = 0,
    user: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Write one cost event to the cost-events DynamoDB table.

    Returns the item written (useful for tests/logs) or None if the table
    isn't configured — the caller treats cost logging as best-effort and
    never fails the primary operation when the helper is unavailable.
    """
    if _table is None:
        logger.warning('COST_EVENTS_TABLE not configured; skipping cost log')
        return None

    # Compute cost
    if event_type == 'bedrock_insights' and model_id:
        cost = _bedrock_cost(model_id, input_tokens, output_tokens)
    elif event_type in FIXED_PRICES:
        cost = FIXED_PRICES[event_type]
    else:
        cost = Decimal('0')

    now = int(time.time())
    created_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))

    item: dict[str, Any] = {
        'event_id': str(uuid.uuid4()),
        'partition_key': _PARTITION,
        'created_at': created_at,
        'event_type': event_type,
        'cost_usd': cost,
        'ttl': now + _TTL_SECONDS,
    }
    if finding_uid: item['finding_uid'] = finding_uid
    if model_id:    item['model_id'] = model_id
    if input_tokens:  item['input_tokens'] = input_tokens
    if output_tokens: item['output_tokens'] = output_tokens
    if duration_ms:   item['duration_ms'] = duration_ms
    if user:          item['user'] = user
    if metadata:      item['metadata'] = metadata

    try:
        _table.put_item(Item=item)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning('Failed to log cost event (%s): %s', event_type, exc)
        return None
    return item
