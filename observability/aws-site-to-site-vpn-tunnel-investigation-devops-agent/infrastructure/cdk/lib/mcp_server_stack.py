"""MCP Server Stack — Lambda + API Gateway + API Key for business context."""
import aws_cdk as cdk
from aws_cdk import (
    aws_apigateway as apigw,
    aws_lambda as lambda_,
    aws_iam as iam,
    CfnOutput,
)
from constructs import Construct


class McpServerStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        role = iam.Role(self, "LambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        # CDK handles zip + S3 upload automatically from local mcp-server/ directory
        fn = lambda_.Function(self, "McpFunction",
            function_name="vpn-devops-mcp-server",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="app.lambda_handler",
            code=lambda_.Code.from_asset("../../mcp-server", exclude=["__pycache__", "*.pyc"]),
            timeout=cdk.Duration.seconds(30),
            memory_size=128,
            role=role,
        )

        api = apigw.RestApi(self, "McpApi",
            rest_api_name="vpn-devops-mcp-api",
        )

        mcp_resource = api.root.add_resource("mcp")

        mcp_resource.add_method("POST",
            apigw.LambdaIntegration(fn),
            api_key_required=True,
        )

        mcp_resource.add_method("OPTIONS",
            apigw.LambdaIntegration(fn),
            api_key_required=False,
        )

        api_key = api.add_api_key("McpApiKey",
            api_key_name="vpn-devops-mcp-api-key",
        )

        plan = api.add_usage_plan("McpUsagePlan",
            name="vpn-devops-mcp-usage-plan",
            api_stages=[apigw.UsagePlanPerApiStage(api=api, stage=api.deployment_stage)],
        )
        plan.add_api_key(api_key)

        CfnOutput(self, "McpEndpoint",
            value=f"https://{api.rest_api_id}.execute-api.{self.region}.amazonaws.com/prod/mcp",
            description="MCP Server endpoint URL",
        )
        CfnOutput(self, "ApiKeyId",
            value=api_key.key_id,
            description="API Key ID (retrieve value via AWS CLI)",
        )
