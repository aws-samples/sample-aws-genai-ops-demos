#!/usr/bin/env python3
"""CDK app for AI Incident Response Playbook Builder."""

import os
import sys

# Add repo root to path for shared utilities
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import aws_cdk as cdk
from stack import PlaybookBuilderStack

app = cdk.App()

region = os.environ.get("CDK_DEFAULT_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))

PlaybookBuilderStack(
    app,
    f"PlaybookBuilderStack-{region}",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=region,
    ),
)

app.synth()
