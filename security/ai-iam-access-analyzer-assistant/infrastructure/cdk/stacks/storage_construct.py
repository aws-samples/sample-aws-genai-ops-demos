"""Storage construct for generated reports and policies."""

from aws_cdk import (
    Duration,
    RemovalPolicy,
    aws_s3 as s3,
)
from constructs import Construct


class StorageConstruct(Construct):
    """S3 bucket for storing generated policies and reports."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.reports_bucket = s3.Bucket(
            self,
            "ReportsBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    expiration=Duration.days(90),
                    id="ExpireOldReports",
                )
            ],
        )
