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
