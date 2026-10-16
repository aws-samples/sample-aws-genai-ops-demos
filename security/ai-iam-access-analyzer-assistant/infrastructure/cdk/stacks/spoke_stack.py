"""Spoke side of the multi-account cross-account read (#173 Level 2).

Deployed in a MEMBER account, this stack contains exactly one resource: a
read-only IAM role the hub's tool Lambda assumes to read IAM/CloudTrail
data there. No data, no compute, no Lambda lives in the spoke. The role
trusts only the hub account, restricted to the hub's specific tool
execution role (not the account root), optionally with an ExternalId
confused-deputy guard. Its permissions are exactly the IAM + CloudTrail
read actions in scan_permissions.py — a strict subset of what the hub's
own ToolExecutionRole already grants itself locally, with Security Hub
actions deliberately excluded (Level 1 already solves org-wide Security
Hub aggregation without any spoke role at all).

Ported from the hub-and-spoke pattern in
operations-automation/aws-services-lifecycle-tracker/cdk/lib/spoke-stack.ts
(issue #144), adapted from TypeScript CDK to this package's Python CDK
app. Deploy it by hand into one account to test, or let the StackSet in
org_stack.py roll it out to every account in an organization/OU.
"""

import re

from aws_cdk import (
    CfnOutput,
    Duration,
    Stack,
    aws_iam as iam,
)
from constructs import Construct

from .scan_permissions import (
    CLOUDTRAIL_READ_ACTIONS,
    HUB_TOOL_ROLE_NAME,
    IAM_READ_ACTIONS,
    SPOKE_ROLE_NAME,
)

_ACCOUNT_ID_RE = re.compile(r"^\d{12}$")


class SpokeStack(Stack):
    """One read-only IAM role, trusting only the hub's tool execution role."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        hub_account_id: str,
        external_id: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if not _ACCOUNT_ID_RE.match(hub_account_id):
            raise ValueError(
                f"hub_account_id must be a 12-digit AWS account id, got {hub_account_id!r}"
            )

        # Trusting the account root alone would let ANY role in the hub
        # account assume this one; the ArnEquals condition narrows it to
        # the one specific role the hub's tool Lambdas actually run as.
        # Trusting the root principal (rather than the role ARN directly
        # in `assumed_by`) keeps the trust policy valid even if the hub's
        # tool role is destroyed and recreated (new role, same name, same
        # ARN) — the condition re-evaluates against the ARN string, not a
        # frozen role identity.
        conditions: dict = {
            "ArnEquals": {
                "aws:PrincipalArn": (
                    f"arn:{self.partition}:iam::{hub_account_id}:role/{HUB_TOOL_ROLE_NAME}"
                )
            }
        }
        if external_id:
            conditions["StringEquals"] = {"sts:ExternalId": external_id}

        self.scan_role = iam.Role(
            self,
            "ScanRole",
            role_name=SPOKE_ROLE_NAME,  # fixed: the hub derives the ARN from the account id alone
            description=(
                "Read-only role assumed by the IAM Security Assistant hub "
                "to read IAM and CloudTrail data in this account (#173 Level 2)"
            ),
            assumed_by=iam.PrincipalWithConditions(
                iam.AccountPrincipal(hub_account_id), conditions
            ),
            max_session_duration=Duration.hours(1),
        )

        self.scan_role.add_to_policy(
            iam.PolicyStatement(
                sid="IamAnalyzerAssistantSpokeReads",
                effect=iam.Effect.ALLOW,
                actions=[*IAM_READ_ACTIONS, *CLOUDTRAIL_READ_ACTIONS],
                # List/Describe/Get-style read-only calls: no resource-level
                # authorization is available for this action set (same
                # justification as the hub's own ToolExecutionRole policy
                # in tools_construct.py and the CapabilitiesProbe policy in
                # api_construct.py).
                resources=["*"],
            )
        )

        CfnOutput(
            self,
            "ScanRoleArn",
            value=self.scan_role.role_arn,
            description="Role the hub assumes to read this account's IAM/CloudTrail data",
        )
        CfnOutput(self, "HubAccountId", value=hub_account_id)
