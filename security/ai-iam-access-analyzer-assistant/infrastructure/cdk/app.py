#!/usr/bin/env python3
"""CDK app for AI IAM Access Analyzer Assistant."""

import os

import aws_cdk as cdk

from stacks.iam_analyzer_assistant_stack import IamAnalyzerAssistantStack


app = cdk.App()

# Region detection: env var > CLI config > fallback
region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
account = os.environ.get("CDK_DEFAULT_ACCOUNT") or os.environ.get("AWS_ACCOUNT_ID")

env = cdk.Environment(region=region, account=account)

IamAnalyzerAssistantStack(
    app,
    f"IamAnalyzerAssistantStack-{region}",
    env=env,
    description="IAM Security Assistant - Conversational least-privilege policy management (uksb-do9bhieqqh)(tag:ai-iam-access-analyzer-assistant,security)",
)

app.synth()
