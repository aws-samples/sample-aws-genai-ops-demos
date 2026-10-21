"""Storage construct for generated reports and policies."""

from aws_cdk import (
    Duration,
    RemovalPolicy,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
)
from constructs import Construct


class StorageConstruct(Construct):
    """S3 bucket for generated policies/reports + DynamoDB async job store."""

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

        # Async conversation job store. The slow model path (two Bedrock
        # rounds) can exceed the API Gateway 29s integration ceiling, so it
        # runs off-request in a worker Lambda; this table holds the job's
        # status and rendered result between the 202 response and the
        # frontend's status poll (see src/jobs.py).
        #
        # - On-demand billing: a demo sees a handful of jobs — pay per
        #   request, no provisioned capacity to size or manage.
        # - TTL on `expires_at`: rows self-delete ~1h after creation, so the
        #   table never accumulates and there is nothing to operate.
        # - RemovalPolicy.DESTROY: a demo table should disappear on teardown.
        self.jobs_table = dynamodb.Table(
            self,
            "ConversationJobs",
            partition_key=dynamodb.Attribute(
                name="job_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="expires_at",
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery=False,
        )
