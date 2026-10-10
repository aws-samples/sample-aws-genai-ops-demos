#!/usr/bin/env python3
"""CDK app for AI IAM Access Analyzer Assistant."""
import os
import aws_cdk as cdk
from stacks.iam_analyzer_assistant_stack import IamAnalyzerAssistantStack
from stacks.org_stack import OrgStack

app = cdk.App()

# Region detection: env var > CLI config > fallback
region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
account = os.environ.get("CDK_DEFAULT_ACCOUNT") or os.environ.get("AWS_ACCOUNT_ID")
env = cdk.Environment(region=region, account=account)

IamAnalyzerAssistantStack(
    app,
    f"IamAnalyzerAssistantStack-{region}",
    env=env,
    description="IAM Security Assistant - Conversational least-privilege policy management (uksb-do9bhieqqh)(tag:ai-iam-access-analyzer-assistant,security)",
)

# Opt-in multi-account rollout (#173 Level 2, hub-and-spoke). Only
# instantiated when the orgTargets context value is supplied (via
# `cdk deploy --context orgTargets=r-xxxx,ou-xxxx-yyyyyyyy`, normally set
# by deploy-all.sh's `--multi-account [--org-targets ...]` flags, not by
# hand). The default single-account deploy path never touches this —
# `app.node.try_get_context` returns None/empty and the `if` below is
# skipped entirely, so default deploy behavior is unaffected byte-for-byte.
org_targets_raw = app.node.try_get_context("orgTargets") or ""
if org_targets_raw:
    target_ou_ids = [t.strip() for t in org_targets_raw.split(",") if t.strip()]
    external_id = app.node.try_get_context("spokeExternalId") or None
    OrgStack(
        app,
        f"IamAnalyzerAssistantOrg-{region}",
        hub_account_id=account,
        target_ou_ids=target_ou_ids,
        external_id=external_id,
        env=env,
        description="IAM Security Assistant - multi-account spoke-role rollout (#173 Level 2)",
    )

app.synth()
