"""
Bedrock model id, resolved once at Lambda init.

The region-prefixed CRIS id is NEVER hardcoded. The Pipeline stack copies the
repo's shared/utils/aws_utils.py into the Lambda bundle as aws_utils.py at synth
time; here we call its get_bedrock_model_id with the UNPREFIXED Nova base id, so
the helper prepends the correct CRIS prefix (us./eu./apac./global.) for the
deployment region.

Rules enforced here:
  * Pass the unprefixed base `amazon.nova-2-lite-v1:0` — feeding the helper an
    already-`global.`-prefixed id would double-prefix to an invalid id.
  * Pass the explicit Nova base name — the helper defaults to a Claude id.
A MODEL_ID env var, if set, overrides the computed value (escape hatch, e.g. a
region where Nova 2 Lite is unavailable via the resolved profile).
"""

import os

# Unprefixed Nova base id. The CRIS prefix is added by get_bedrock_model_id.
NOVA_BASE_ID = "amazon.nova-2-lite-v1:0"

_MODEL_ID = None


def _compute() -> str:
    override = os.environ.get("MODEL_ID")
    if override:
        return override
    try:
        from aws_utils import get_bedrock_model_id  # staged copy, Lambda bundle
        return get_bedrock_model_id(NOVA_BASE_ID)
    except Exception:
        # Last-resort fallback: the global CRIS profile always resolves.
        return f"global.{NOVA_BASE_ID}"


def model_id() -> str:
    """Return the resolved Bedrock model id (computed once, then cached)."""
    global _MODEL_ID
    if _MODEL_ID is None:
        _MODEL_ID = _compute()
    return _MODEL_ID
