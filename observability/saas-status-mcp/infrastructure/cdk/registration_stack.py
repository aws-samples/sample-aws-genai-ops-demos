"""CDK Stack for SaaS Status MCP Server — DevOps Agent Registration.

Registers the deployed AgentCore Runtime as an MCP tool source on an existing
AWS DevOps Agent Space. This stack is deployed to the Agent Space's region,
which may differ from the runtime's region.

Context variables (passed via --context at deploy time):
    agent_space_id     (str, required) — the Agent Space UUID
    agent_space_region (str, required) — region the Agent Space lives in
    runtime_arn        (str, required) — AgentCore Runtime ARN
    runtime_region     (str, required) — region the runtime lives in (SigV4 signing region)
    service_name       (str, optional, default "saas-status-mcp") — name shown in console

All context values are set automatically by scripts/setup-devops-agent.ps1|.sh,
which parses the agent_space_region from the provided Agent Space ARN and
reads the runtime_arn from the main stack's CloudFormation outputs.
"""

import aws_cdk as cdk
from aws_cdk import (
    Stack,
    aws_devopsagent as devopsagent,
    aws_iam as iam,
    CfnOutput,
)
from constructs import Construct

# The four MCP tools the server exposes (must match agent/main.py exactly).
MCP_TOOLS = [
    "list_providers",
    "get_service_status",
    "get_active_events",
    "check_all_dependencies",
]


class SaasStatusMcpRegistrationStack(Stack):
    """Registers the SaaS Status MCP runtime with a DevOps Agent Space.

    Deployed to the Agent Space's region. Takes runtime details as CDK context
    because CloudFormation cross-region output imports are not supported.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── Context ───────────────────────────────────────────────────────────
        agent_space_id = self.node.get_context("agent_space_id")
        agent_space_region = self.node.get_context("agent_space_region")  # parsed from ARN by deploy script
        runtime_arn = self.node.get_context("runtime_arn")
        runtime_region = self.node.get_context("runtime_region")
        service_name = self.node.try_get_context("service_name") or "saas-status-mcp"

        account = cdk.Aws.ACCOUNT_ID
        # Note: cdk.Aws.REGION is a token, not a string. Use agent_space_region
        # (from context) for string operations such as role names and ARN conditions.

        # Build the MCP invocation endpoint.
        # The runtime ARN is percent-encoded into the path with Fn.join/split
        # (same technique as the main stack's RuntimeEndpoint output).
        # We build the whole URL with Fn.join so that the CFN token for the
        # encoded ARN is embedded as a real CFN function, not a Python repr.
        encoded_arn = cdk.Fn.join(
            "%2F",
            cdk.Fn.split(
                "/",
                cdk.Fn.join(
                    "%3A",
                    cdk.Fn.split(":", runtime_arn),
                ),
            ),
        )
        endpoint = cdk.Fn.join("", [
            f"https://bedrock-agentcore.{runtime_region}.amazonaws.com/runtimes/",
            encoded_arn,
            "/invocations?qualifier=DEFAULT",
        ])

        # ── IAM signing role ──────────────────────────────────────────────────
        # DevOps Agent assumes this role to SigV4-sign calls to the runtime.
        # Trust is scoped to aidevops.amazonaws.com within this account and to
        # DevOps Agent services in the space's region (aws:SourceArn condition).
        signing_role = iam.Role(
            self,
            "SaasStatusMcpSigningRole",
            role_name=f"SaasStatusMcpSigningRole-{agent_space_region}",
            assumed_by=iam.ServicePrincipal(
                "aidevops.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": account},
                    # ArnLike scope: only DevOps Agent services in this account and region
                    # can assume this role. account is cdk.Aws.ACCOUNT_ID (CFN token),
                    # which resolves to the real account ID at deploy time.
                    "ArnLike": {
                        "aws:SourceArn": "arn:aws:aidevops:"
                        + agent_space_region
                        + ":"
                        + account
                        + ":service/*"
                    },
                },
            ),
            description=(
                "SigV4 signing role - DevOps Agent assumes this to invoke "
                "the SaaS Status MCP AgentCore Runtime"
            ),
        )

        # Permission: invoke the runtime (and any qualifier endpoints).
        signing_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=[runtime_arn, f"{runtime_arn}/*"],
            )
        )

        # ── DevOps Agent Service (account-level MCP registration) ─────────────
        # AWS::DevOpsAgent::Service registers the MCP server in this account.
        # It is account-scoped and shared across all Agent Spaces.
        #
        # IMPORTANT: CfnService validates the role by actually calling
        # InvokeAgentRuntime during CREATE. Without an explicit dependency, CFN
        # can create the Service before the inline policy is attached, causing a
        # 403. add_dependency ensures the role + policy are fully in place first.
        mcp_service = devopsagent.CfnService(
            self,
            "SaasStatusMcpService",
            service_type="mcpserversigv4",
            service_details=devopsagent.CfnService.ServiceDetailsProperty(
                mcp_server_sig_v4=devopsagent.CfnService.MCPServerSigV4DetailsProperty(
                    name=service_name,
                    endpoint=endpoint,
                    description="SaaS status pages (Statuspage.io) for upstream dependency checks",
                    authorization_config=devopsagent.CfnService.MCPServerSigV4AuthorizationConfigProperty(
                        region=runtime_region,   # SigV4 signing region = runtime's region
                        service="bedrock-agentcore",
                        role_arn=signing_role.role_arn,
                    ),
                )
            ),
        )
        # Block CfnService creation until the role AND its inline policy exist.
        mcp_service.node.add_dependency(signing_role)

        # ── DevOps Agent Association (attach to the Agent Space + enable tools) ─
        # AWS::DevOpsAgent::Association attaches the service to a specific Agent
        # Space and specifies which tools are allowed.
        devopsagent.CfnAssociation(
            self,
            "SaasStatusMcpAssociation",
            agent_space_id=agent_space_id,
            service_id=mcp_service.attr_service_id,
            configuration=devopsagent.CfnAssociation.ServiceConfigurationProperty(
                mcp_server_sig_v4=devopsagent.CfnAssociation.MCPServerSigV4ConfigurationProperty(
                    tools=MCP_TOOLS,
                )
            ),
        )

        # ── Outputs ──────────────────────────────────────────────────────────
        CfnOutput(
            self,
            "ServiceId",
            value=mcp_service.attr_service_id,
            description="DevOps Agent Service ID for the MCP registration",
        )

        CfnOutput(
            self,
            "SigningRoleArn",
            value=signing_role.role_arn,
            description="IAM role DevOps Agent assumes to invoke the runtime (SigV4)",
        )

        CfnOutput(
            self,
            "McpEndpoint",
            value=endpoint,
            description="MCP invocation endpoint registered with DevOps Agent",
        )
