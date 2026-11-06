#!/usr/bin/env python3
"""
Shared AWS utility functions for GenAI Ops demos.

Provides consistent region detection and AWS configuration across all demos.
"""

import os
import subprocess
from typing import Optional


def get_region() -> str:
    """
    Detect AWS region using consistent priority order.
    
    Priority:
    1. AWS_DEFAULT_REGION environment variable (temporary override)
    2. AWS_REGION environment variable (alternative)
    3. AWS CLI configuration (aws configure get region)
    4. Fallback to us-east-1 if nothing configured
    
    Returns:
        str: AWS region name
    """
    # Check environment variables first
    region = os.environ.get('AWS_DEFAULT_REGION') or os.environ.get('AWS_REGION')
    
    if region:
        return region
    
    # Try AWS CLI configuration
    try:
        result = subprocess.run(
            ['aws', 'configure', 'get', 'region'],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    
    # Fallback
    return 'us-east-1'


def get_account_id() -> Optional[str]:
    """
    Get AWS account ID from current credentials.
    
    Returns:
        str: AWS account ID or None if unable to determine
    """
    try:
        result = subprocess.run(
            ['aws', 'sts', 'get-caller-identity', '--query', 'Account', '--output', 'text'],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    
    return None


def get_bedrock_model_id(model_name: str = "anthropic.claude-sonnet-4-6") -> str:
    """
    Get the correct cross-region inference (CRIS) prefixed model ID for the current region.

    Amazon Bedrock newer models are often only available via cross-region inference
    profiles. This function detects the deployment region and applies the appropriate
    geographic prefix (us/eu/ap) or falls back to global for regions without a
    dedicated geographic CRIS (ca, me, af, sa, il, mx).

    Args:
        model_name: Base model identifier without prefix (e.g. "anthropic.claude-sonnet-4-6")

    Returns:
        str: CRIS-prefixed model ID (e.g. "eu.anthropic.claude-sonnet-4-6")

    Examples:
        >>> os.environ['AWS_REGION'] = 'eu-west-1'
        >>> get_bedrock_model_id()
        'eu.anthropic.claude-sonnet-4-6'

        >>> os.environ['AWS_REGION'] = 'us-east-1'
        >>> get_bedrock_model_id("anthropic.claude-sonnet-4-5-20250929-v1:0")
        'us.anthropic.claude-sonnet-4-5-20250929-v1:0'

        >>> os.environ['AWS_REGION'] = 'ca-central-1'
        >>> get_bedrock_model_id()
        'global.anthropic.claude-sonnet-4-6'
    """
    region = get_region()
    geo_prefix = region.split('-')[0]
    # Asia Pacific's CRIS profile prefix is "apac", not "ap". Mapping ap-* to
    # "ap" produces an invalid inference profile ID (e.g. "ap.anthropic.*")
    # that fails at invoke time in every ap-* region.
    cris_prefix = {'us': 'us', 'eu': 'eu', 'ap': 'apac'}.get(geo_prefix, 'global')
    return f"{cris_prefix}.{model_name}"


# AWS Transform CUSTOM (code transformation, the `atx custom` product) regions,
# per the AWS Transform user guide "Supported Regions". NOTE the guide also
# lists Asia Pacific (Singapore) "VMware agents only" and South America (São
# Paulo) "Mainframe modernization agents only" — those are OTHER AWS Transform
# products, NOT custom code transformation, so they are deliberately excluded
# here.
#
# WHY A HARDCODED LIST (a deliberate exception, not laziness):
#   Normally we detect capability at runtime rather than hardcode it. For
#   AWS Transform that is not possible:
#     - The AWS CLI has NO `transform` service, so there is no describe/list
#       call to ask "is Transform available in region X?".
#     - AWS does not publish Transform in SSM global-infrastructure either — the
#       service ids transform / transform-custom / atx all return
#       ParameterNotFound — so there is no AWS-published region dataset to query.
#     - The ONLY component that actually knows is the `atx` CLI itself (it
#       resolves transform-custom.<region>.api.aws). But `atx` is a heavy agent
#       we install INSIDE CodeBuild at migration time; we refuse to make it a
#       deploy/setup prerequisite just to probe a region before anything runs.
#   So the supported set cannot be discovered — it has to be stated. This is the
#   single place it is encoded, taken verbatim from the user guide, with a dated
#   source so it is auditable and easy to update when AWS adds a region.
#
# HOW THE STALENESS RISK IS CONTAINED:
#   This list only chooses a FALLBACK. The migration build always TRIES the
#   actual deploy region first and lets atx itself accept or reject it; the
#   fallback computed from this list is used only when atx reports the deploy
#   region is unsupported. So when AWS adds Transform to a new region, the
#   primary attempt simply starts succeeding there with no code change — this
#   list never gates the primary path, it only picks where to retry.
#
# get_transform_region maps a deploy region to the nearest supported one the
# same way get_bedrock_model_id maps a region to its CRIS prefix — by geo token.
TRANSFORM_REGIONS = frozenset({
    "us-east-1",       # US East (N. Virginia)
    "ca-central-1",    # Canada (Central)
    "eu-central-1",    # Europe (Frankfurt)
    "eu-west-2",       # Europe (London)
    "ap-south-1",      # Asia Pacific (Mumbai)
    "ap-southeast-2",  # Asia Pacific (Sydney)
    "ap-northeast-1",  # Asia Pacific (Tokyo)
    "ap-northeast-2",  # Asia Pacific (Seoul)
})


def get_transform_region(deploy_region: str = None) -> str:
    """
    Pick the AWS Transform control-plane region for a deployment region.

    AWS Transform custom is offered only in TRANSFORM_REGIONS, and its
    availability cannot be probed at runtime, so instead of failing in an
    unsupported region we route to the geographically nearest supported one —
    keeping the control plane (and its Bedrock cross-region inference) in the
    deploy region's geo. Mirrors get_bedrock_model_id's geo-based mapping.

    Rules (first match wins):
      1. The deploy region is itself a Transform region -> use it directly.
         (Covers all four APAC regions, both EU, N. Virginia, Canada.)
      2. Otherwise map by geo to that geo's nearest Transform region:
         - ap-south-*      -> ap-south-1      (Mumbai)
         - ap-southeast-*  -> ap-southeast-2  (Sydney)
         - other ap-*      -> ap-northeast-1  (Tokyo)
         - eu-*            -> eu-central-1    (Frankfurt)
         - us-* / ca-*     -> us-east-1       (N. Virginia)
      3. Anything else (af-, me-, sa-, il-, mx-, ...) -> us-east-1 (global default).

    Args:
        deploy_region: Region to map. Defaults to the detected deploy region.

    Returns:
        str: An AWS region where AWS Transform custom is available.

    Examples:
        >>> get_transform_region('eu-west-3')     # Paris -> nearest EU Transform
        'eu-central-1'
        >>> get_transform_region('ap-northeast-1')  # Tokyo is itself supported
        'ap-northeast-1'
        >>> get_transform_region('ap-southeast-1')  # Singapore -> nearest SE
        'ap-southeast-2'
        >>> get_transform_region('sa-east-1')      # São Paulo -> global default
        'us-east-1'
    """
    region = deploy_region or get_region()

    # 1. Already a Transform region.
    if region in TRANSFORM_REGIONS:
        return region

    # 2. Nearest by geo. Order matters: more specific ap-* prefixes first.
    if region.startswith("ap-south-"):
        return "ap-south-1"
    if region.startswith("ap-southeast-"):
        return "ap-southeast-2"
    if region.startswith("ap-"):
        return "ap-northeast-1"
    if region.startswith("eu-"):
        return "eu-central-1"
    if region.startswith("us-") or region.startswith("ca-"):
        return "us-east-1"

    # 3. Global default.
    return "us-east-1"
