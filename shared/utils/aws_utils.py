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
