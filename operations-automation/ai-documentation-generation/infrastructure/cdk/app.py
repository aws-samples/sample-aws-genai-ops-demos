#!/usr/bin/env python3
"""CDK App for Automated Documentation Generation Demo."""

import aws_cdk as cdk
from stack import DocumentationGeneratorStack
from shared.utils import get_region

app = cdk.App()

# Get region for multi-region support
region = get_region()

DocumentationGeneratorStack(
    app,
    f"DocumentationGeneratorStack-{region}",
    env={"region": region},
    description="AWS Transform Documentation Generator - CodeBuild-based solution for comprehensive codebase analysis (uksb-do9bhieqqh)(tag:doc-generation,operations-automation)",
)

app.synth()
