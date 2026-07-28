"""Tool Lambda functions construct."""

import os
from pathlib import Path

from aws_cdk import (
    Duration,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_s3 as s3,
)
from constructs import Construct


class ToolsConstruct(Construct):
    """Lambda functions implementing the IAM analysis tools."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        reports_bucket: s3.IBucket,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Shared IAM role for all tool functions (read-only access)
        tool_role = iam.Role(
            self,
            "ToolExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        # Read-only permissions for security services
        tool_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    # Security Hub
                    "securityhub:GetFindings",
                    "securityhub:BatchGetFindings",
                    # IAM Access Analyzer
                    "access-analyzer:ListFindings",
                    "access-analyzer:GetFinding",
                    "access-analyzer:ListAnalyzers",
                    "access-analyzer:ValidatePolicy",
                    "access-analyzer:CheckAccessNotGranted",
                    # CloudTrail
                    "cloudtrail:LookupEvents",
                    # IAM (read-only)
                    "iam:GetRole",
                    "iam:GetPolicy",
                    "iam:GetPolicyVersion",
                    "iam:ListRoles",
                    "iam:ListPolicies",
                    "iam:ListAttachedRolePolicies",
                    "iam:ListRolePolicies",
                    "iam:ListEntitiesForPolicy",
                    "iam:GetRolePolicy",
                    "iam:ListUsers",
                    "iam:GetUser",
                    "iam:ListAttachedUserPolicies",
                ],
                resources=["*"],
            )
        )

        # S3 write for generated reports
        reports_bucket.grant_read_write(tool_role)

        # Path to Lambda source code
        tools_path = str(
            Path(__file__).resolve().parent.parent.parent.parent / "src"
        )

        # Common Lambda props
        common_props = {
            "runtime": lambda_.Runtime.PYTHON_3_12,
            "timeout": Duration.seconds(60),
            "memory_size": 512,
            "role": tool_role,
            "environment": {
                "REPORTS_BUCKET": reports_bucket.bucket_name,
            },
        }

        # Tool Lambda functions
        self.list_findings_fn = lambda_.Function(
            self,
            "ListFindings",
            handler="tools.list_findings.handler",
            code=lambda_.Code.from_asset(tools_path),
            **common_props,
        )

        self.generate_policy_fn = lambda_.Function(
            self,
            "GeneratePolicy",
            handler="tools.generate_policy.handler",
            code=lambda_.Code.from_asset(tools_path),
            timeout=Duration.seconds(120),
            memory_size=1024,
            runtime=lambda_.Runtime.PYTHON_3_12,
            role=tool_role,
            environment={
                "REPORTS_BUCKET": reports_bucket.bucket_name,
            },
        )

        self.check_dependencies_fn = lambda_.Function(
            self,
            "CheckDependencies",
            handler="tools.check_dependencies.handler",
            code=lambda_.Code.from_asset(tools_path),
            **common_props,
        )

        self.validate_policy_fn = lambda_.Function(
            self,
            "ValidatePolicy",
            handler="tools.validate_policy.handler",
            code=lambda_.Code.from_asset(tools_path),
            **common_props,
        )

        self.get_finding_details_fn = lambda_.Function(
            self,
            "GetFindingDetails",
            handler="tools.get_finding_details.handler",
            code=lambda_.Code.from_asset(tools_path),
            **common_props,
        )

        self.export_report_fn = lambda_.Function(
            self,
            "ExportReport",
            handler="tools.export_report.handler",
            code=lambda_.Code.from_asset(tools_path),
            **common_props,
        )

        self.generate_action_plan_fn = lambda_.Function(
            self,
            "GenerateActionPlan",
            handler="tools.generate_action_plan.handler",
            code=lambda_.Code.from_asset(tools_path),
            timeout=Duration.seconds(90),
            memory_size=512,
            runtime=lambda_.Runtime.PYTHON_3_12,
            role=tool_role,
            environment={
                "REPORTS_BUCKET": reports_bucket.bucket_name,
            },
        )

        self.compare_roles_fn = lambda_.Function(
            self,
            "CompareRoles",
            handler="tools.compare_roles.handler",
            code=lambda_.Code.from_asset(tools_path),
            timeout=Duration.seconds(90),
            memory_size=512,
            runtime=lambda_.Runtime.PYTHON_3_12,
            role=tool_role,
            environment={
                "REPORTS_BUCKET": reports_bucket.bucket_name,
            },
        )

        self.list_exports_fn = lambda_.Function(
            self,
            "ListExports",
            handler="tools.list_exports.handler",
            code=lambda_.Code.from_asset(tools_path),
            **common_props,
        )

        # Expose as dict for the API construct
        self.functions = {
            "list_findings": self.list_findings_fn,
            "get_finding_details": self.get_finding_details_fn,
            "generate_policy": self.generate_policy_fn,
            "check_dependencies": self.check_dependencies_fn,
            "validate_policy": self.validate_policy_fn,
            "export_report": self.export_report_fn,
            "generate_action_plan": self.generate_action_plan_fn,
            "compare_roles": self.compare_roles_fn,
            "list_exports": self.list_exports_fn,
        }
