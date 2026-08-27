"""CDK Stack for Permission Boundary Generator Demo."""
import aws_cdk as cdk
from aws_cdk import aws_s3 as s3, CfnOutput, RemovalPolicy
from constructs import Construct


class PermissionBoundaryStack(cdk.Stack):
    """Stack that creates an S3 bucket for storing permission boundary output."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        bucket = s3.Bucket(
            self,
            "BoundaryOutputBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            enforce_ssl=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        CfnOutput(self, "BucketName", value=bucket.bucket_name)
