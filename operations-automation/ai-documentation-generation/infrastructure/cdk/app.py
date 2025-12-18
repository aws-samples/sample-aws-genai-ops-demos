#!/usr/bin/env python3
"""CDK App for Automated Documentation Generation Demo."""

import aws_cdk as cdk
from stack import DocumentationGeneratorStack

app = cdk.App()

DocumentationGeneratorStack(
    app,
    "DocumentationGeneratorStack",
    description="AWS Transform Documentation Generator - CodeBuild-based solution for comprehensive codebase analysis (uksb-q3p3ydk6f3)(tag:doc-generation,operations-automation)",
)

app.synth()
