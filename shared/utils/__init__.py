"""
Shared AWS utility functions for GenAI Ops demos.
"""

from .aws_utils import get_region, get_account_id, get_bedrock_model_id

__all__ = ['get_region', 'get_account_id', 'get_bedrock_model_id']
