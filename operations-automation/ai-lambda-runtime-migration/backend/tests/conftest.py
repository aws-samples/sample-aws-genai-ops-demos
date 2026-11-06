"""Make backend/ modules importable from tests without a sys.path hack in each
test file, and stage the shared aws_utils.py so model_id.py imports cleanly.

The backend directory is the Lambda bundle root; its modules import each other
by bare name (import discover, import assess, from pipeline import ...), exactly
as they do inside the Lambda. Adding the backend dir to sys.path here reproduces
that import root for the test process.
"""
import os
import shutil
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_SHARED = os.path.abspath(os.path.join(
    BACKEND_DIR, "..", "..", "..", "shared", "utils", "aws_utils.py"))

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# model_id.py imports `aws_utils` (the staged copy). At test time the Pipeline
# stack has not staged it, so place a copy next to the backend modules. This
# mirrors what stageBackend() does at synth.
_staged = os.path.join(BACKEND_DIR, "aws_utils.py")
if not os.path.exists(_staged) and os.path.exists(REPO_SHARED):
    shutil.copyfile(REPO_SHARED, _staged)

# Deterministic region for model-id computation in tests.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
