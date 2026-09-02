"""Region and Bedrock model-ID resolution.

Container-local copy of `shared/utils/aws_utils.py`: the shared module lives
outside the image build context, so it is not importable inside the AgentCore
container. Same precedent as `aws-services-lifecycle-tracker/agent/aws_utils.py`
and `ai-password-reset-chatbot/agent/aws_utils.py`.

Two deliberate deviations from the shared version:

  1. No `aws configure get region` subprocess. The AgentCore container has no
     AWS CLI, and shelling out per call is wasteful where AWS_REGION is always
     set by the runtime.
  2. The Asia Pacific CRIS prefix is `apac`, not `ap`. `ap.anthropic.*` is not
     a real inference profile ID — the shared version would produce one and
     fail at invoke time in every ap-* region.
"""

from __future__ import annotations

import os

DEFAULT_MODEL = "anthropic.claude-opus-4-8"

# Bedrock cross-region inference (CRIS) prefixes, keyed by the region's
# geography segment. Geographies without a dedicated profile (ca/me/af/sa/il/mx)
# fall back to global, whose destination list is all commercial regions.
_CRIS_PREFIXES = {"us": "us", "eu": "eu", "ap": "apac"}


def get_region() -> str:
    """Resolve the AWS region from the environment.

    AgentCore sets AWS_REGION in the container; locally it comes from .env or
    the shell. Returns "" when nothing is set — a region is never substituted,
    because the wrong one means silent cross-region calls or a confusing
    AccessDenied instead of boto3's clear NoRegionError.
    """
    return (os.environ.get("AWS_DEFAULT_REGION")
            or os.environ.get("AWS_REGION")
            or "")


def get_bedrock_model_id(model_name: str = DEFAULT_MODEL,
                         region: str = "") -> str:
    """Prefix a base model name with the CRIS profile for `region`.

    Base model IDs are not invokable on demand for these models — an inference
    profile ID is required. Hardcoding the prefix breaks deployment outside
    that one geography, so it is derived from the region instead.

    `region` defaults to the ambient region, but pass it explicitly when the
    Bedrock region differs from the deployment region: the prefix must match
    where the model is invoked, not where the agent happens to run.

    Not every model publishes a profile in every geography; when the resulting
    ID does not exist, Bedrock says so at invoke time. That is the honest
    failure — quieter than routing to the wrong continent.
    """
    geo = (region or get_region()).split("-")[0]
    return f"{_CRIS_PREFIXES.get(geo, 'global')}.{model_name}"
