"""
AWS utility functions for the agent container runtime.

This is a container-local version of shared/utils/aws_utils.py since the shared
module is not available inside the Docker container.
"""

import os


def get_region() -> str:
    """
    Detect AWS region using environment variables.

    Inside the AgentCore container, AWS_DEFAULT_REGION or AWS_REGION is set
    by the runtime environment. Falls back to us-east-1.
    """
    return os.environ.get('AWS_DEFAULT_REGION') or os.environ.get('AWS_REGION') or 'us-east-1'
