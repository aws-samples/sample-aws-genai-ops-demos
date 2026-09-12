"""
Test bootstrap: make the backend modules and the repo-wide aws_utils importable.

At runtime the CDK stack stages shared/utils/aws_utils.py next to the backend
sources (see cdk/lib/pipeline-stack.ts), so `from aws_utils import get_region`
resolves inside the Lambda. Tests reproduce that layout on sys.path.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
_SHARED_UTILS = os.path.join(_BACKEND, '..', '..', '..', 'shared', 'utils')

for p in (_BACKEND, os.path.abspath(_SHARED_UTILS)):
    if p not in sys.path:
        sys.path.insert(0, p)
