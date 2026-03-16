#!/usr/bin/env python3
"""CDK App for Graviton Migration Assessment Demo."""

import aws_cdk as cdk
from stack import GravitonAssessmentStack
from shared.utils import get_region

app = cdk.App()

# Get region for multi-region support
region = get_region()

GravitonAssessmentStack(
    app,
    f"GravitonAssessmentStack-{region}",
    env={"region": region},
    description="Graviton migration assessment and cost optimization analysis (uksb-do9bhieqqh)(tag:graviton-migration,cost-optimization)",
)

app.synth()