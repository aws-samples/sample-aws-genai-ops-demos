"""CDK Stack for SaaS Status MCP Server.

Deploys:
- S3 bucket for deployment package
- IAM role for AgentCore Runtime
- AgentCore Runtime (CfnRuntime) with MCP protocol, PUBLIC network
- CloudWatch Log Group for structured logging
"""

import aws_cdk as cdk
from aws_cdk import (
    Stack,
    aws_bedrockagentcore as bedrockagentcore,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
    CfnOutput,
    RemovalPolicy,
)
from constructs import Construct


class SaasStatusMcpStack(Stack):
    """Stack for the SaaS Status MCP Server hosted on AgentCore Runtime."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        region = cdk.Aws.REGION
        account = cdk.Aws.ACCOUNT_ID

        # S3 bucket for deployment package (created by deploy script before CDK runs)
        bucket_name = f"saas-status-mcp-{account}-{region}"
        deployment_bucket = s3.Bucket.from_bucket_name(
            self, "DeploymentBucket", bucket_name
        )

        # CloudWatch Log Group for server logs
        log_group = logs.LogGroup(
            self,
            "SaasStatusMcpLogs",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # IAM role for AgentCore Runtime
        runtime_role = iam.Role(
            self,
            "SaasStatusMcpRuntimeRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description="IAM role for SaaS Status MCP Server on AgentCore Runtime",
        )

        # CloudWatch Logs permissions
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=[
                    f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/*",
                ],
            )
        )

        # S3 read access for deployment package
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["s3:GetObject"],
                resources=[f"{deployment_bucket.bucket_arn}/*"],
            )
        )

        # AgentCore Runtime — MCP server
        runtime = bedrockagentcore.CfnRuntime(
            self,
            "SaasStatusMcpRuntime",
            agent_runtime_name="saas_status_mcp",
            description="SaaS Status MCP Server — queries Statuspage.io APIs for upstream dependency checks",
            role_arn=runtime_role.role_arn,
            agent_runtime_artifact={
                "codeConfiguration": {
                    "code": {
                        "s3": {
                            "bucket": deployment_bucket.bucket_name,
                            "prefix": "agent/deployment_package.zip",
                        },
                    },
                    "entryPoint": ["main.py"],
                    "runtime": "PYTHON_3_13",
                },
            },
            network_configuration={"networkMode": "PUBLIC"},
            protocol_configuration="MCP",
            environment_variables={
                "LOG_LEVEL": "INFO",
                # S3-backed provider registry (conditional GET, no redeploy to update)
                "PROVIDERS_BUCKET": bucket_name,
                "PROVIDERS_KEY": "config/providers.json",
                "PROVIDERS_POLL_INTERVAL": "60",
            },
        )

        # Outputs
        CfnOutput(
            self,
            "RuntimeArn",
            value=runtime.attr_agent_runtime_arn,
            description="AgentCore Runtime ARN for the MCP server",
            # Exported so the registration stack can import it cross-stack without
            # needing a CLI lookup (the registration stack may target a different region,
            # but the export name is globally unique and Fn.import_value resolves it).
            export_name=f"SaasStatusMcp-RuntimeArn-{region}",
        )

        # MCP invocation endpoint: the runtime ARN is URL-encoded and placed in the
        # path, with the DEFAULT qualifier. This is the URL registered in DevOps Agent
        # (SigV4) and used by the local proxy. `:` -> %3A and `/` -> %2F.
        encoded_arn = cdk.Fn.join(
            "%2F",
            cdk.Fn.split(
                "/",
                cdk.Fn.join(
                    "%3A",
                    cdk.Fn.split(":", runtime.attr_agent_runtime_arn),
                ),
            ),
        )
        CfnOutput(
            self,
            "RuntimeEndpoint",
            value=f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT",
            description="MCP invocation endpoint URL (register this in DevOps Agent SigV4 config)",
        )

        CfnOutput(
            self,
            "RuntimeRoleArn",
            value=runtime_role.role_arn,
            description="IAM role ARN for AgentCore Runtime",
        )

        CfnOutput(
            self,
            "DeploymentBucketName",
            value=deployment_bucket.bucket_name,
            description="S3 bucket for deployment packages",
        )

        CfnOutput(
            self,
            "LogGroupName",
            value=log_group.log_group_name,
            description="CloudWatch Log Group for server logs",
        )
