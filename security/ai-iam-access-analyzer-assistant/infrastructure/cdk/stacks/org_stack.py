"""Hub-side StackSet rollout of the spoke role (#173 Level 2, opt-in).

Deployed in the ORGANIZATIONS MANAGEMENT account (or a delegated
administrator for StackSets), this stack creates a service-managed
CloudFormation StackSet that rolls SpokeStack's one IAM role out to every
account in the target Organizational Unit(s) — automatically covering
accounts that join later, with no per-account manual deploy.

Only instantiated when a CDK context value is supplied (see
infrastructure/cdk/app.py's `if org_targets:` guard) — the default,
single-account deploy path never touches this file at all, matching the
opt-in requirement from Ben's #173 spec and the contributor guide's
"default deploy behavior must stay byte-for-byte identical" rule.

Ported from the hub-and-spoke pattern in
operations-automation/aws-services-lifecycle-tracker/cdk/lib/org-stack.ts
(issue #144), adapted from TypeScript CDK to this package's Python CDK
app. The SpokeStack template is synthesized in-memory from an inner,
bootstrapless cdk.App — this guarantees the StackSet-deployed role is
byte-identical to one deployed by hand via `cdk deploy SpokeStack`, and
avoids requiring every target account to be CDK-bootstrapped (a
service-managed StackSet with an inline template needs no S3 assets, so
BootstraplessSynthesizer is correct here, not a workaround).
"""

import json
import re

import aws_cdk as cdk
from aws_cdk import (
    Stack,
    aws_cloudformation as cloudformation,
)
from constructs import Construct

from .spoke_stack import SpokeStack

_ROOT_OR_OU_RE = re.compile(r"^(r-[0-9a-z]{4,32}|ou-[0-9a-z]{4,32}-[0-9a-z]{8,32})$")


class OrgStack(Stack):
    """Service-managed StackSet that rolls SpokeStack out org-wide."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        hub_account_id: str,
        target_ou_ids: list[str],
        external_id: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if not target_ou_ids:
            raise ValueError("target_ou_ids must be a non-empty list of root/OU ids")
        for ou_id in target_ou_ids:
            if not _ROOT_OR_OU_RE.match(ou_id):
                raise ValueError(
                    f"target_ou_ids entries must be an Organizations root id "
                    f"(r-xxxx) or OU id (ou-xxxx-yyyyyyyy), got {ou_id!r}"
                )

        # Synthesize SpokeStack's template in-memory, from an inner app
        # with no bootstrap requirement — the StackSet needs the plain
        # CloudFormation JSON, not a deployable CDK stack of its own.
        inner_app = cdk.App()
        spoke = SpokeStack(
            inner_app,
            "SpokeStackTemplate",
            hub_account_id=hub_account_id,
            external_id=external_id,
            synthesizer=cdk.BootstraplessSynthesizer(),
        )
        template = inner_app.synth().get_stack_by_name(spoke.stack_name).template

        self.stack_set = cloudformation.CfnStackSet(
            self,
            "SpokeStackSet",
            stack_set_name=f"{construct_id}-SpokeRole",
            permission_model="SERVICE_MANAGED",
            capabilities=["CAPABILITY_NAMED_IAM"],
            # New accounts joining the targeted OU(s) later automatically
            # get the spoke role with no manual re-run of this stack.
            auto_deployment=cloudformation.CfnStackSet.AutoDeploymentProperty(
                enabled=True,
                retain_stacks_on_account_removal=False,
            ),
            # Lets a delegated administrator (not just the Organizations
            # management account itself) own and deploy this StackSet.
            call_as="SELF",
            template_body=json.dumps(template),
            operation_preferences=cloudformation.CfnStackSet.OperationPreferencesProperty(
                failure_tolerance_percentage=100,
                max_concurrent_percentage=100,
                region_concurrency_type="PARALLEL",
            ),
            stack_instances_group=[
                cloudformation.CfnStackSet.StackInstancesProperty(
                    deployment_targets=cloudformation.CfnStackSet.DeploymentTargetsProperty(
                        organizational_unit_ids=target_ou_ids,
                        # Explicitly exclude the hub account from the
                        # StackSet targets: the hub reads its own IAM/
                        # CloudTrail data with its own credentials, no
                        # spoke role needed there (same as
                        # tools_construct.py's session_for_account()
                        # helper returning None for the hub's own
                        # account id — see cross_account_iam_context.py).
                        account_filter_type="DIFFERENCE",
                        accounts=[hub_account_id],
                    ),
                    regions=[self.region],  # IAM is global; one region is enough
                )
            ],
        )
