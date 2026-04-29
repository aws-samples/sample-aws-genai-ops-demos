#!/usr/bin/env python3
"""CDK app for AWS Site-to-Site VPN DevOps Agent Demo."""
import aws_cdk as cdk
from shared.utils import get_region
from lib.vpn_demo_stack import VpnDemoStack
from lib.mcp_server_stack import McpServerStack


region = get_region()
env = cdk.Environment(region=region)

app = cdk.App()

# Context parameters (passed via --context or cdk.json)
key_pair_name = app.node.try_get_context("keyPairName") or ""
routing_type = app.node.try_get_context("routingType") or "bgp"
webhook_url = app.node.try_get_context("webhookUrl") or ""
webhook_secret = app.node.try_get_context("webhookSecret") or ""

# Main stack — solution adoption tracking here only (per steering)
VpnDemoStack(
    app,
    f"VpnDemoStack-{region}",
    env=env,
    description="AWS Site-to-Site VPN DevOps Agent Demo "
    "(uksb-do9bhieqqh)(tag:vpn-investigation,observability)",
    key_pair_name=key_pair_name,
    routing_type=routing_type,
    webhook_url=webhook_url,
    webhook_secret=webhook_secret,
)

# MCP server stack — no tracking (secondary stack)
McpServerStack(
    app,
    f"VpnDemoMcpServer-{region}",
    env=env,
    description="MCP Server for AWS VPN DevOps Agent Demo",
)

app.synth()
