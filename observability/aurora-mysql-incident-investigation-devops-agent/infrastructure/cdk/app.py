#!/usr/bin/env python3
"""CDK app for Aurora MySQL Incident Investigation DevOps Agent Demo."""
import aws_cdk as cdk
from shared.utils import get_region
from lib.aurora_demo_stack import AuroraDemoStack


region = get_region()
env = cdk.Environment(region=region)

app = cdk.App()

# Context parameters (passed via --context or cdk.json)
key_pair_name = app.node.try_get_context("keyPairName") or ""
webhook_url = app.node.try_get_context("webhookUrl") or ""
webhook_secret = app.node.try_get_context("webhookSecret") or ""
ssh_cidr = app.node.try_get_context("sshCidr") or "0.0.0.0/0"

# Main stack — solution adoption tracking here only (per steering)
AuroraDemoStack(
    app,
    f"AuroraDemoStack-{region}",
    env=env,
    description="Aurora MySQL Incident Investigation DevOps Agent Demo "
    "(uksb-do9bhieqqh)(tag:aurora-incident-investigation,observability)",
    key_pair_name=key_pair_name,
    webhook_url=webhook_url,
    webhook_secret=webhook_secret,
    ssh_cidr=ssh_cidr,
)


app.synth()
