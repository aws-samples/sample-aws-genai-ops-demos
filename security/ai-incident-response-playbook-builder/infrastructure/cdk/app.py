#!/usr/bin/env python3
"""CDK app for AI Incident Response Playbook Builder."""

import aws_cdk as cdk
from stack import PlaybookBuilderStack
from shared.utils import get_region

app = cdk.App()

region = get_region()

PlaybookBuilderStack(
    app,
    f"PlaybookBuilderStack-{region}",
    env=cdk.Environment(
        account=None,
        region=region,
    ),
    description="AI Incident Response Playbook Builder - generates architecture-aware security playbooks (uksb-do9bhieqqh)(tag:ir-playbook-builder,security)",
)

app.synth()
