"""
Shared AWS utility functions for G.O.A.T. agent containers.

Provides consistent region detection across all agent runtimes.
Reused by all agent containers (copied into each container at build time).
"""

import os
import subprocess


def get_region() -> str:
    """
    Detect AWS region using consistent priority order.

    Priority:
    1. AWS_DEFAULT_REGION environment variable (temporary override)
    2. AWS_REGION environment variable (set by AgentCore runtime)
    3. AWS CLI configuration (aws configure get region)
    4. Fallback to us-east-1 if nothing configured

    Returns:
        AWS region name string
    """
    region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")
    if region:
        return region

    try:
        result = subprocess.run(
            ["aws", "configure", "get", "region"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return "us-east-1"
