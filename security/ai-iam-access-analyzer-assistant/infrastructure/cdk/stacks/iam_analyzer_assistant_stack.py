"""Main stack for AI IAM Access Analyzer Assistant."""

from aws_cdk import (
    Stack,
    CfnOutput,
)
from constructs import Construct

from .auth_construct import AuthConstruct
from .storage_construct import StorageConstruct
from .tools_construct import ToolsConstruct
from .api_construct import ApiConstruct
from .frontend_construct import FrontendConstruct


class IamAnalyzerAssistantStack(Stack):
    """Single stack deploying all resources for the IAM Analyzer Assistant."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Authentication
        auth = AuthConstruct(self, "Auth")

        # Storage for generated reports/policies
        storage = StorageConstruct(self, "Storage")

        # Tool Lambda functions (IAM analysis capabilities)
        tools = ToolsConstruct(
            self,
            "Tools",
            reports_bucket=storage.reports_bucket,
        )

        # API Gateway + Conversation Lambda (Bedrock orchestration)
        api = ApiConstruct(
            self,
            "Api",
            user_pool=auth.user_pool,
            tools_functions=tools.functions,
            reports_bucket=storage.reports_bucket,
        )

        # Frontend hosting (CloudFront + S3)
        frontend = FrontendConstruct(
            self,
            "Frontend",
            api_endpoint=api.api_endpoint,
        )

        # Stack outputs
        CfnOutput(self, "WebsiteUrl", value=frontend.distribution_url)
        CfnOutput(self, "ApiEndpoint", value=api.api_endpoint)
        CfnOutput(self, "UserPoolId", value=auth.user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=auth.user_pool_client.user_pool_client_id)
        CfnOutput(self, "IdentityPoolId", value=auth.identity_pool.ref)
        CfnOutput(self, "Region", value=self.region)
        CfnOutput(self, "FrontendBucketName", value=frontend.hosting_bucket.bucket_name)
        CfnOutput(self, "DistributionId", value=frontend.distribution.distribution_id)
