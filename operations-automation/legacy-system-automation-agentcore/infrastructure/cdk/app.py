#!/usr/bin/env python3
"""
CDK App for Legacy System Automation with AgentCore Browser Tool

Deploys:
- AgentCore Browser Tool (custom browser with optional recording)
- Nova Act Workflow Definition
- S3 bucket for session recordings (optional)
- Required IAM roles and permissions
"""

import aws_cdk as cdk
from stack import LegacySystemAutomationStack

app = cdk.App()

LegacySystemAutomationStack(
    app,
    "LegacySystemAutomationAgentCore",
    description="AgentCore Browser Tool for Legacy System Automation Demo",
    env=cdk.Environment(
        region="us-east-1"
    )
)

app.synth()
