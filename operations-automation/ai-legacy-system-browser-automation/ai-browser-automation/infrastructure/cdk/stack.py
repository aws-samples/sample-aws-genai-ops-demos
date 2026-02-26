"""
CDK Stack for Legacy System Automation with AgentCore Browser Tool

Creates the infrastructure needed to run browser automation in AWS cloud:
- AgentCore Browser Tool (custom browser)
- Nova Act Workflow Definition with S3 export config
- S3 bucket for session recordings and workflow data
- IAM roles with required permissions
"""

from aws_cdk import (
    Stack,
    CfnOutput,
    RemovalPolicy,
    aws_iam as iam,
    aws_s3 as s3,
)
from constructs import Construct


class LegacySystemAutomationStack(Stack):
    """Stack for Legacy System Automation with AgentCore Browser Tool."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # =================================================================
        # S3 Bucket for Session Recordings and Nova Act Workflow Data
        # =================================================================
        recordings_bucket = s3.Bucket(
            self,
            "BrowserRecordingsBucket",
            bucket_name=f"legacy-automation-recordings-{self.account}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=True,
        )

        # =================================================================
        # IAM Role for AgentCore Browser (for session recording)
        # =================================================================
        browser_execution_role = iam.Role(
            self,
            "BrowserExecutionRole",
            role_name=f"{construct_id}-browser-role",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {
                        "aws:SourceAccount": self.account
                    },
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:*"
                    }
                }
            ),
            description="Execution role for AgentCore Browser Tool",
        )

        # Browser permissions
        browser_execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="BrowserPermissions",
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:ConnectBrowserAutomationStream",
                    "bedrock-agentcore:ListBrowsers",
                    "bedrock-agentcore:GetBrowserSession",
                    "bedrock-agentcore:ListBrowserSessions",
                    "bedrock-agentcore:CreateBrowser",
                    "bedrock-agentcore:StartBrowserSession",
                    "bedrock-agentcore:StopBrowserSession",
                    "bedrock-agentcore:ConnectBrowserLiveViewStream",
                    "bedrock-agentcore:UpdateBrowserStream",
                    "bedrock-agentcore:DeleteBrowser",
                    "bedrock-agentcore:GetBrowser",
                ],
                resources=["*"],
            )
        )

        # S3 permissions for recordings
        browser_execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="S3RecordingPermissions",
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:PutObject",
                    "s3:GetObject",
                    "s3:ListBucket",
                    "s3:ListMultipartUploadParts",
                    "s3:AbortMultipartUpload",
                ],
                resources=[
                    recordings_bucket.bucket_arn,
                    f"{recordings_bucket.bucket_arn}/*",
                ],
            )
        )

        # CloudWatch Logs permissions
        browser_execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="CloudWatchLogsPermissions",
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                ],
                resources=["*"],
            )
        )


        # =================================================================
        # AgentCore Browser Tool (Custom Resource)
        # CDK doesn't have L2 constructs yet, using CfnResource
        # =================================================================
        from aws_cdk import CfnResource
        
        browser_tool = CfnResource(
            self,
            "BrowserTool",
            type="AWS::BedrockAgentCore::BrowserCustom",
            properties={
                "Name": "legacy_system_automation_browser",
                "Description": "Browser tool for legacy system automation demo",
                "NetworkConfiguration": {
                    "NetworkMode": "PUBLIC"
                },
                "ExecutionRoleArn": browser_execution_role.role_arn,
                "RecordingConfig": {
                    "Enabled": True,
                    "S3Location": {
                        "Bucket": recordings_bucket.bucket_name,
                        "Prefix": "browser-recordings"
                    }
                }
            }
        )
        browser_tool.node.add_dependency(browser_execution_role)
        browser_tool.node.add_dependency(recordings_bucket)

        # =================================================================
        # Nova Act Workflow Definition
        # =================================================================
        # Note: Nova Act doesn't have CloudFormation support yet.
        # After deploying this stack, run the update_workflow_s3.py script
        # to configure the workflow definition with S3 export:
        #
        #   python update_workflow_s3.py --bucket <RecordingsBucketName>
        #
        # This enables step data visualization in the AWS console.

        # =================================================================
        # Outputs
        # =================================================================
        CfnOutput(
            self,
            "BrowserId",
            value=browser_tool.get_att("BrowserId").to_string(),
            description="AgentCore Browser Tool ID",
            export_name=f"{construct_id}-BrowserId",
        )

        CfnOutput(
            self,
            "BrowserExecutionRoleArn",
            value=browser_execution_role.role_arn,
            description="IAM Role ARN for Browser execution",
            export_name=f"{construct_id}-BrowserRoleArn",
        )

        CfnOutput(
            self,
            "RecordingsBucketName",
            value=recordings_bucket.bucket_name,
            description="S3 bucket for session recordings and workflow data",
            export_name=f"{construct_id}-RecordingsBucket",
        )

        CfnOutput(
            self,
            "RecordingsLocation",
            value=f"s3://{recordings_bucket.bucket_name}/browser-recordings/",
            description="S3 location for session recordings",
        )

        CfnOutput(
            self,
            "WorkflowDataLocation",
            value=f"s3://{recordings_bucket.bucket_name}/workflow-data/",
            description="S3 location for Nova Act workflow step data",
        )

        CfnOutput(
            self,
            "LiveViewConsoleUrl",
            value=f"https://{self.region}.console.aws.amazon.com/bedrock-agentcore/builtInTools",
            description="AWS Console URL for live view",
        )
