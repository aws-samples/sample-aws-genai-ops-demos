"""Shared cross-account read permissions (#173 Level 2, hub-and-spoke).

Single source of truth for the read-only IAM + CloudTrail calls the hub's
tool Lambda needs when scanning a SPOKE account, mirrored from the local
permissions already granted to the hub's own ``ToolExecutionRole`` in
``tools_construct.py``. Deliberately a SUBSET of that role's permissions —
Security Hub reads are intentionally excluded here, because Level 1
(``capabilities.py``'s ``_probe_org_detection``, already shipped) already
gets org-wide Security Hub aggregation for free when the hub account is
the Security Hub delegated administrator, with no spoke role or
cross-account call involved at all. This module exists only for the
permissions a spoke role needs that Security Hub delegation does NOT
cover: reading IAM resources and CloudTrail events that live in each
member account individually.

Ported from the hub-and-spoke pattern in
``operations-automation/aws-services-lifecycle-tracker`` (issue #144) —
same fixed-role-name, same ``scan-permissions.ts`` single-source-of-truth
idea, same spoke-stack shape, adapted from TypeScript CDK to this
package's Python CDK app.
"""

# Read-only IAM calls the cross-account tool needs in a spoke account.
# Mirrors the IAM subset of ToolExecutionRole's policy in
# tools_construct.py — kept as a literal subset (not re-exported from
# there) because the hub's local policy also includes access-key-triage
# actions (ListAccessKeys, GetAccessKeyLastUsed, GetAccountSummary, the
# User/Group policy walk) that the Level 2 tool does not use. Narrower is
# safer for a role another account's Lambda assumes.
IAM_READ_ACTIONS: list[str] = [
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
]

# Read-only CloudTrail call the cross-account tool needs in a spoke
# account, for the same "has this permission actually been used" analysis
# generate_policy and compare_roles already do locally.
CLOUDTRAIL_READ_ACTIONS: list[str] = [
    "cloudtrail:LookupEvents",
]

# Fixed name of the role deployed into each spoke/member account. Fixed
# (not looked up via SSM or a DynamoDB row) so the hub can derive the full
# ARN from nothing but the target account id — see
# ``src/tools/cross_account_iam_context.py``'s ``session_for_account()``.
SPOKE_ROLE_NAME = "IamAnalyzerAssistantSpokeRole"

# Fixed name of the hub's tool-execution role (ToolExecutionRole in
# tools_construct.py), so each spoke's trust policy can pin
# aws:PrincipalArn to this one specific role rather than trusting the
# whole hub account root. Mirrors HUB_PIPELINE_ROLE_NAME in the ported
# lifecycle-tracker pattern. tools_construct.py must set
# role_name=HUB_TOOL_ROLE_NAME explicitly on ToolExecutionRole for this
# to resolve to a real ARN — CDK's default auto-generated role name is
# not predictable at spoke-stack synth time.
HUB_TOOL_ROLE_NAME = "iam-analyzer-assistant-tool-role"
