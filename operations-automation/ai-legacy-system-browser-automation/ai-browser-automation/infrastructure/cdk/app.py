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
from shared.utils import get_region

app = cdk.App()

# Get region for multi-region support
region = get_region()

LegacySystemAutomationStack(
    app,
    f"LegacySystemAutomationAgentCore-{region}",
    env={"region": region},
    description="AgentCore Browser Tool for Legacy System Automation Demo (uksb-do9bhieqqh)(tag:legacy-automation,operations-automation)",
)

app.synth()
