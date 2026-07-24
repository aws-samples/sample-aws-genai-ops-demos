"""Frontend hosting construct (CloudFront + S3)."""

from aws_cdk import (
    RemovalPolicy,
    aws_s3 as s3,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    CfnOutput,
)
from constructs import Construct


class FrontendConstruct(Construct):
    """CloudFront distribution serving React app from S3."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        api_endpoint: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # S3 bucket for static frontend assets
        self.hosting_bucket = s3.Bucket(
            self,
            "FrontendBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # Origin Access Control for CloudFront -> S3
        oac = cloudfront.S3OriginAccessControl(
            self,
            "OAC",
            signing=cloudfront.Signing.SIGV4_NO_OVERRIDE,
        )

        # CloudFront distribution
        self.distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(
                    self.hosting_bucket,
                    origin_access_control=oac,
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            default_root_object="index.html",
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                ),
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                ),
            ],
        )

        self.distribution_url = f"https://{self.distribution.distribution_domain_name}"
