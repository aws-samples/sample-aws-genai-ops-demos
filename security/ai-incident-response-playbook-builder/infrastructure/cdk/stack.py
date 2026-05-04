"""CDK stack for AI Incident Response Playbook Builder — S3 output bucket."""

import os

import aws_cdk as cdk
from aws_cdk import (
    Stack,
    RemovalPolicy,
    CfnOutput,
    aws_s3 as s3,
)
from constructs import Construct


class PlaybookBuilderStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # S3 bucket for playbook output
        output_bucket = s3.Bucket(
            self,
            "PlaybookOutputBucket",
            bucket_name=f"ir-playbooks-{self.account}-{self.region}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            enforce_ssl=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        CfnOutput(self, "OutputBucketName", value=output_bucket.bucket_name)
