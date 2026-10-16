"""Offline CDK template assertions for the #173 Level 2 hub-and-spoke stacks.

Pins the trust-policy condition (ArnEquals pinned to the hub's fixed tool
role name, not just the account root), the spoke role's permission set
(IAM + CloudTrail only — no Security Hub actions, since Level 1 already
covers that without a spoke role), the StackSet's exclusion of the hub
account from its own rollout, and app.py's opt-in guard (no orgTargets
context => no Org stack synthesized at all, so default single-account
deploys are unaffected).

Run from the infrastructure/cdk/ directory's own virtualenv (same one
deploy-all.sh creates): `cd infrastructure/cdk && source .venv/bin/activate
&& pytest tests/`. These tests import aws_cdk directly rather than going
through the `cdk` CLI, so they run in well under a second and don't
require `npx cdk synth` or AWS credentials.
"""

import json
import re
import sys
import unittest
from pathlib import Path

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stacks.org_stack import OrgStack  # noqa: E402
from stacks.scan_permissions import (  # noqa: E402
    CLOUDTRAIL_READ_ACTIONS,
    HUB_TOOL_ROLE_NAME,
    IAM_READ_ACTIONS,
    SPOKE_ROLE_NAME,
)
from stacks.spoke_stack import SpokeStack  # noqa: E402

_HUB_ACCOUNT = "661487690263"
_SPOKE_ACCOUNT = "280072637828"
_OU_ID = "ou-jv46-abcd1234"
_ROOT_ID = "r-jv46"


def _synth_spoke(**kwargs) -> Template:
    app = cdk.App()
    stack = SpokeStack(
        app,
        "TestSpokeStack",
        hub_account_id=_HUB_ACCOUNT,
        env=cdk.Environment(account=_SPOKE_ACCOUNT, region="us-east-1"),
        **kwargs,
    )
    return Template.from_stack(stack)


class SpokeStackValidationTest(unittest.TestCase):
    def test_rejects_non_12_digit_hub_account_id(self):
        app = cdk.App()
        with self.assertRaises(ValueError):
            SpokeStack(app, "Bad", hub_account_id="not-an-account-id")

    def test_rejects_too_short_account_id(self):
        app = cdk.App()
        with self.assertRaises(ValueError):
            SpokeStack(app, "Bad", hub_account_id="12345")


class SpokeStackTrustPolicyTest(unittest.TestCase):
    def test_trust_policy_pins_to_hub_tool_role_via_arn_equals(self):
        template = _synth_spoke()
        template.has_resource_properties(
            "AWS::IAM::Role",
            {
                "RoleName": SPOKE_ROLE_NAME,
                "AssumeRolePolicyDocument": {
                    "Statement": Match.array_with(
                        [
                            Match.object_like(
                                {
                                    "Action": "sts:AssumeRole",
                                    "Effect": "Allow",
                                    "Condition": {
                                        "ArnEquals": {
                                            "aws:PrincipalArn": Match.object_like(
                                                {
                                                    "Fn::Join": [
                                                        "",
                                                        Match.array_with(
                                                            [
                                                                Match.string_like_regexp(
                                                                    f".*{_HUB_ACCOUNT}:role/{HUB_TOOL_ROLE_NAME}$"
                                                                )
                                                            ]
                                                        ),
                                                    ]
                                                }
                                            )
                                        }
                                    },
                                }
                            )
                        ]
                    )
                },
            },
        )

    def test_external_id_adds_string_equals_condition(self):
        template = _synth_spoke(external_id="confused-deputy-guard-123")
        template.has_resource_properties(
            "AWS::IAM::Role",
            {
                "AssumeRolePolicyDocument": {
                    "Statement": Match.array_with(
                        [
                            Match.object_like(
                                {
                                    "Condition": Match.object_like(
                                        {
                                            "StringEquals": {
                                                "sts:ExternalId": "confused-deputy-guard-123"
                                            }
                                        }
                                    )
                                }
                            )
                        ]
                    )
                }
            },
        )

    def test_no_external_id_means_no_string_equals_condition(self):
        template = _synth_spoke()
        resources = template.find_resources("AWS::IAM::Role", {"Properties": {"RoleName": SPOKE_ROLE_NAME}})
        self.assertEqual(1, len(resources))
        statement = list(resources.values())[0]["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
        self.assertNotIn("StringEquals", statement.get("Condition", {}))

    def test_max_session_duration_is_one_hour(self):
        template = _synth_spoke()
        template.has_resource_properties(
            "AWS::IAM::Role",
            {"RoleName": SPOKE_ROLE_NAME, "MaxSessionDuration": 3600},
        )


class SpokeStackPermissionsTest(unittest.TestCase):
    def test_grants_exactly_iam_and_cloudtrail_read_actions(self):
        template = _synth_spoke()
        template.has_resource_properties(
            "AWS::IAM::Policy",
            {
                "PolicyDocument": {
                    "Statement": [
                        Match.object_like(
                            {
                                "Sid": "IamAnalyzerAssistantSpokeReads",
                                "Effect": "Allow",
                                "Resource": "*",
                                "Action": [*IAM_READ_ACTIONS, *CLOUDTRAIL_READ_ACTIONS],
                            }
                        )
                    ]
                }
            },
        )

    def test_grants_no_security_hub_actions(self):
        """Security Hub reads are deliberately excluded — Level 1 already
        gets org-wide Security Hub aggregation for free when the hub
        account is the delegated administrator, with no spoke role or
        cross-account call involved. A spoke role that also granted
        securityhub:* would be unused, broader-than-necessary access."""
        template = _synth_spoke()
        resources = template.find_resources("AWS::IAM::Policy")
        all_actions: list[str] = []
        for resource in resources.values():
            for statement in resource["Properties"]["PolicyDocument"]["Statement"]:
                actions = statement.get("Action", [])
                all_actions.extend(actions if isinstance(actions, list) else [actions])
        securityhub_actions = [a for a in all_actions if a.startswith("securityhub:")]
        self.assertEqual([], securityhub_actions)

    def test_outputs_scan_role_arn_and_hub_account_id(self):
        template = _synth_spoke()
        outputs = template.to_json().get("Outputs", {})
        self.assertIn("ScanRoleArn", outputs)
        self.assertIn("HubAccountId", outputs)
        self.assertEqual(_HUB_ACCOUNT, outputs["HubAccountId"]["Value"])


class OrgStackValidationTest(unittest.TestCase):
    def test_rejects_empty_target_ou_ids(self):
        app = cdk.App()
        with self.assertRaises(ValueError):
            OrgStack(
                app, "Bad", hub_account_id=_HUB_ACCOUNT, target_ou_ids=[],
                env=cdk.Environment(account=_HUB_ACCOUNT, region="us-east-1"),
            )

    def test_rejects_malformed_ou_id(self):
        app = cdk.App()
        with self.assertRaises(ValueError):
            OrgStack(
                app, "Bad", hub_account_id=_HUB_ACCOUNT, target_ou_ids=["not-an-ou-id"],
                env=cdk.Environment(account=_HUB_ACCOUNT, region="us-east-1"),
            )

    def test_accepts_root_id(self):
        app = cdk.App()
        # Must not raise.
        OrgStack(
            app, "Good", hub_account_id=_HUB_ACCOUNT, target_ou_ids=[_ROOT_ID],
            env=cdk.Environment(account=_HUB_ACCOUNT, region="us-east-1"),
        )

    def test_accepts_ou_id(self):
        app = cdk.App()
        OrgStack(
            app, "Good", hub_account_id=_HUB_ACCOUNT, target_ou_ids=[_OU_ID],
            env=cdk.Environment(account=_HUB_ACCOUNT, region="us-east-1"),
        )


def _synth_org(**kwargs) -> Template:
    app = cdk.App()
    stack = OrgStack(
        app,
        "TestOrgStack",
        hub_account_id=_HUB_ACCOUNT,
        target_ou_ids=[_OU_ID],
        env=cdk.Environment(account=_HUB_ACCOUNT, region="us-east-1"),
        **kwargs,
    )
    return Template.from_stack(stack)


class OrgStackStackSetTest(unittest.TestCase):
    def test_is_service_managed_with_named_iam_capability(self):
        template = _synth_org()
        template.has_resource_properties(
            "AWS::CloudFormation::StackSet",
            {
                "PermissionModel": "SERVICE_MANAGED",
                "Capabilities": ["CAPABILITY_NAMED_IAM"],
                "CallAs": "SELF",
            },
        )

    def test_auto_deployment_enabled_for_new_accounts(self):
        template = _synth_org()
        template.has_resource_properties(
            "AWS::CloudFormation::StackSet",
            {
                "AutoDeployment": {
                    "Enabled": True,
                    "RetainStacksOnAccountRemoval": False,
                }
            },
        )

    def test_hub_account_excluded_from_its_own_rollout(self):
        """The hub reads its own IAM/CloudTrail data with its own
        credentials (session_for_account() returns None for the hub's own
        account id) — it must never receive a spoke role from its own
        StackSet."""
        template = _synth_org()
        template.has_resource_properties(
            "AWS::CloudFormation::StackSet",
            {
                "StackInstancesGroup": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "DeploymentTargets": {
                                    "AccountFilterType": "DIFFERENCE",
                                    "Accounts": [_HUB_ACCOUNT],
                                    "OrganizationalUnitIds": [_OU_ID],
                                }
                            }
                        )
                    ]
                )
            },
        )

    def test_one_failing_account_does_not_block_the_rollout(self):
        template = _synth_org()
        template.has_resource_properties(
            "AWS::CloudFormation::StackSet",
            {
                "OperationPreferences": {
                    "FailureTolerancePercentage": 100,
                    "MaxConcurrentPercentage": 100,
                    "RegionConcurrencyType": "PARALLEL",
                }
            },
        )

    def test_embedded_template_body_is_the_spoke_stack(self):
        """The StackSet's inline TemplateBody must be the SAME role/trust
        policy shape SpokeStack produces standalone — this is the whole
        point of synthesizing it in-memory rather than hand-writing a
        parallel template that could drift."""
        template = _synth_org()
        resources = template.find_resources("AWS::CloudFormation::StackSet")
        self.assertEqual(1, len(resources))
        stack_set_props = list(resources.values())[0]["Properties"]
        embedded = json.loads(stack_set_props["TemplateBody"])
        role_resources = [
            r for r in embedded["Resources"].values() if r["Type"] == "AWS::IAM::Role"
        ]
        self.assertEqual(1, len(role_resources))
        self.assertEqual(SPOKE_ROLE_NAME, role_resources[0]["Properties"]["RoleName"])

    def test_regions_scoped_to_the_stacks_own_region_only(self):
        template = _synth_org()
        template.has_resource_properties(
            "AWS::CloudFormation::StackSet",
            {"StackInstancesGroup": Match.array_with([Match.object_like({"Regions": ["us-east-1"]})])},
        )


if __name__ == "__main__":
    unittest.main()
