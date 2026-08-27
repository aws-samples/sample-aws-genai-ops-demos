#!/usr/bin/env python3
"""CDK App for Permission Boundary Generator Demo."""
import aws_cdk as cdk
from stack import PermissionBoundaryStack
from shared.utils import get_region

app = cdk.App()
region = get_region()

PermissionBoundaryStack(
    app,
    f"PermissionBoundaryStack-{region}",
    env={"region": region},
    description="AI-powered IAM permission boundary generation from CloudTrail analysis (uksb-do9bhieqqh)(tag:permission-boundary-generator,security)",
)

app.synth()
