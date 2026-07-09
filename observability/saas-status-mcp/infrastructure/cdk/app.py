#!/usr/bin/env python3
"""CDK App for SaaS Status MCP Server.

Two stacks:

  SaasStatusMcpStack-<runtime-region>
      Deploys the AgentCore Runtime, IAM role, S3 bucket reference, and
      CloudWatch log group. Deployed to the runtime's region.

  SaasStatusMcpRegistrationStack-<agent-space-region>   (optional)
      Registers the runtime with an AWS DevOps Agent Space: creates the SigV4
      signing IAM role, the account-level Service record, and the Association
      that attaches it to the space with the four MCP tools.
      Deployed to the Agent Space's region (cross-region from the runtime).

      This stack is only synthesised when the required context variables are
      present.  They are provided by scripts/setup-devops-agent.ps1|.sh, which
      parses the Agent Space ARN and reads the runtime ARN from the main stack
      CloudFormation outputs before invoking `cdk deploy`.

      Required CDK context keys:
          agent_space_id     — Agent Space UUID
          agent_space_region — region the Agent Space lives in
          runtime_arn        — AgentCore Runtime ARN
          runtime_region     — region the runtime lives in
"""

import aws_cdk as cdk
from stack import SaasStatusMcpStack
from registration_stack import SaasStatusMcpRegistrationStack
from shared.utils import get_region

app = cdk.App()

# ── Main stack: AgentCore Runtime ─────────────────────────────────────────────
runtime_region = get_region()

SaasStatusMcpStack(
    app,
    f"SaasStatusMcpStack-{runtime_region}",
    env={"region": runtime_region},
    description=(
        "SaaS status MCP server for AWS DevOps Agent "
        "(uksb-do9bhieqqh)(tag:saas-status-mcp,observability)"
    ),
)

# ── Registration stack: DevOps Agent Service + Association ────────────────────
# Only synthesised when the Agent Space context is supplied.
agent_space_id = app.node.try_get_context("agent_space_id")
agent_space_region = app.node.try_get_context("agent_space_region")
runtime_arn = app.node.try_get_context("runtime_arn")

if agent_space_id and agent_space_region and runtime_arn:
    SaasStatusMcpRegistrationStack(
        app,
        f"SaasStatusMcpRegistrationStack-{agent_space_region}",
        env={"region": agent_space_region},
        description=(
            "DevOps Agent registration for the SaaS Status MCP server "
            "(uksb-do9bhieqqh)(tag:saas-status-mcp,observability)"
        ),
    )

app.synth()
