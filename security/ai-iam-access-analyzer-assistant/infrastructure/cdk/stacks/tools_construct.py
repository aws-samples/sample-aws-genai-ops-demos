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

from .scan_permissions import HUB_TOOL_ROLE_NAME, SPOKE_ROLE_NAME


class ToolsConstruct(Construct):
    """Lambda functions implementing the IAM analysis tools."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        reports_bucket: s3.IBucket,
        full_total_scan: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Shared IAM role for all tool functions (read-only access).
        #
        # role_name is explicit and fixed (#173 Level 2, hub-and-spoke) so
        # that each spoke account's trust policy can pin aws:PrincipalArn
        # to this exact role (see scan_permissions.HUB_TOOL_ROLE_NAME and
        # spoke_stack.py) without any cross-stack lookup. CDK's default
        # auto-generated role name is unpredictable at spoke-stack synth
        # time (spoke stacks synthesize independently, often before this
        # stack even exists in a fresh account), so a fixed name is
        # required here, not just convenient. This only matters when
        # multi-account deploy is opted into; single-account deploys are
        # unaffected by naming the role explicitly.
        tool_role = iam.Role(
            self,
            "ToolExecutionRole",
            role_name=HUB_TOOL_ROLE_NAME,
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        # Read-only permissions for security services.
        #
        # resources=["*"] is required by the AWS IAM authorization model for
        # every action in this statement: the IAM Read family and the sibling
        # security-service control-plane calls do not support resource-level
        # authorization per the AWS service authorization reference
        # (https://docs.aws.amazon.com/service-authorization/latest/reference/).
        # A policy that names a specific role/user/policy ARN would be
        # rejected. The `*` here is the least privilege AWS actually allows.
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
                    # IAM read-only (access-key triage — #175). Purely
                    # read; no *AccessKey* write action is granted. Root
                    # keys are detected via iam:GetAccountSummary.
                    "iam:ListAccessKeys",
                    "iam:GetAccessKeyLastUsed",
                    "iam:GetAccountSummary",
                    "iam:ListUserPolicies",
                    "iam:GetUserPolicy",
                    "iam:ListGroupsForUser",
                    "iam:ListAttachedGroupPolicies",
                    "iam:ListGroupPolicies",
                    "iam:GetGroupPolicy",
                ],
                resources=["*"],
            )
        )

        # S3 write for generated reports
        reports_bucket.grant_read_write(tool_role)

        # Dedicated presigner role. Lambda's runtime STS session tokens rotate
        # underneath us, and S3 rejects a presigned URL with InvalidToken once
        # the underlying token rotates — even if the URL's own X-Amz-Expires
        # hasn't been reached yet. To offer download links longer than a few
        # minutes we sign with the credentials of a role we assume ourselves,
        # whose returned session token is stable for its full DurationSeconds.
        presigner_role = iam.Role(
            self,
            "PresignerRole",
            assumed_by=iam.ArnPrincipal(tool_role.role_arn),
            description=(
                "Assumed by IAM Analyzer tool Lambdas to mint presigned S3 "
                "download URLs whose lifetime does not depend on Lambda's "
                "rolling runtime STS token."
            ),
            max_session_duration=Duration.hours(1),
        )
        reports_bucket.grant_read(presigner_role)

        tool_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["sts:AssumeRole"],
                resources=[presigner_role.role_arn],
            )
        )

        self.presigner_role = presigner_role

        # Cross-account spoke-role assumption (#173 Level 2, hub-and-spoke,
        # opt-in). The specific spoke role ARN depends on which account_id
        # a user asks about at runtime — it is NOT known at synth time, so
        # this cannot be scoped to a literal ARN list the way the
        # presigner grant above is. Scoped instead to the fixed spoke role
        # NAME across any account/partition, via a wildcard account-id
        # segment: "arn:*:iam::*:role/IamAnalyzerAssistantSpokeRole". This
        # still meaningfully narrows the grant — tool_role cannot assume
        # ANY role in a target account, only one with this exact name,
        # and the spoke role's OWN trust policy (spoke_stack.py) is the
        # second half of the guardrail: it only trusts this specific
        # tool_role ARN back. Granting sts:AssumeRole here has zero effect
        # on accounts that haven't deployed SpokeStack at all — there is
        # no role by this name to assume, and the call simply fails.
        tool_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["sts:AssumeRole"],
                resources=[f"arn:*:iam::*:role/{SPOKE_ROLE_NAME}"],
            )
        )

        # Path to Lambda source code
        tools_path = str(
            Path(__file__).resolve().parent.parent.parent.parent / "src"
        )

        # Common Lambda props
        common_props = {
            "runtime": lambda_.Runtime.PYTHON_3_14,
            "timeout": Duration.seconds(60),
            "memory_size": 512,
            "role": tool_role,
            "environment": {
                "REPORTS_BUCKET": reports_bucket.bucket_name,
                "FULL_TOTAL_SCAN": str(full_total_scan).lower(),
                "PRESIGNER_ROLE_ARN": presigner_role.role_arn,
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
            runtime=lambda_.Runtime.PYTHON_3_14,
            role=tool_role,
            environment={"REPORTS_BUCKET": reports_bucket.bucket_name},
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
            runtime=lambda_.Runtime.PYTHON_3_14,
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
            runtime=lambda_.Runtime.PYTHON_3_14,
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

        # Access-key triage (#175). Iterates every IAM user with keys and
        # resolves the effective policy per user; give it more time and
        # memory than the default so a fleet of ~200 users with policy
        # walks completes well under the API Gateway 29s ceiling.
        self.triage_access_keys_fn = lambda_.Function(
            self,
            "TriageAccessKeys",
            handler="tools.triage_access_keys.handler",
            code=lambda_.Code.from_asset(tools_path),
            timeout=Duration.seconds(120),
            memory_size=512,
            runtime=lambda_.Runtime.PYTHON_3_14,
            role=tool_role,
            environment={
                "REPORTS_BUCKET": reports_bucket.bucket_name,
            },
        )

        # Cross-account IAM/CloudTrail read (#173 Level 2, opt-in).
        # SPOKE_ROLE_NAME is passed explicitly rather than relying on the
        # tool code's hardcoded default matching scan_permissions.py by
        # coincidence — this is the same single-source-of-truth constant
        # used by spoke_stack.py and the sts:AssumeRole grant above, so a
        # future rename only has to happen in one place. Works with no
        # deploy-time change for the default single-account path: calling
        # this tool with the hub's own account_id never calls
        # sts:AssumeRole at all (session_for_account() returns None), so
        # it functions even if no SpokeStack/OrgStack has ever been
        # deployed anywhere — only a DIFFERENT account_id requires a
        # spoke role to actually exist there.
        self.cross_account_iam_context_fn = lambda_.Function(
            self,
            "CrossAccountIamContext",
            handler="tools.cross_account_iam_context.handler",
            code=lambda_.Code.from_asset(tools_path),
            runtime=common_props["runtime"],
            timeout=common_props["timeout"],
            memory_size=common_props["memory_size"],
            role=common_props["role"],
            environment={
                **common_props["environment"],
                "SPOKE_ROLE_NAME": SPOKE_ROLE_NAME,
            },
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
            "triage_access_keys": self.triage_access_keys_fn,
            "cross_account_iam_context": self.cross_account_iam_context_fn,
        }
