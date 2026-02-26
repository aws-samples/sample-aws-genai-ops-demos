#!/usr/bin/env python3
import aws_cdk as cdk
from stack import AnyCompanyITPortalStack
from shared.utils import get_region

app = cdk.App()

# Get region for multi-region support
region = get_region()

AnyCompanyITPortalStack(
    app,
    f"AnyCompanyITPortalStack-{region}",
    env={"region": region},
    description="Multi-portal IT demo environment for AI automation workflows (uksb-do9bhieqqh)(tag:it-portal-demo,operations-automation)",
)

app.synth()
