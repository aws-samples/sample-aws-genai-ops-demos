"""Tool: Read IAM + CloudTrail context in a specific member account (#173 Level 2).

Opt-in multi-account hub-and-spoke feature. This tool ALWAYS requires an
explicit ``account_id`` — there is deliberately no "scan every account in
my org" mode. A fan-out across every member account in a single turn
would risk the API Gateway 29-second ceiling (the same risk the
pagination/short-circuit work elsewhere in this codebase exists to avoid)
and would make the thing the user asked about ambiguous if several
accounts returned conflicting data. If a user wants an org-wide view,
the model should ask them to either name one account at a time or request
a Security Hub findings query instead (list_findings already aggregates
org-wide for free when the hub account is the Security Hub delegated
administrator — see #171/#173 Level 1 — no cross-account call needed).

For the hub's own account, no assume-role is performed at all — this
tool simply uses the Lambda's own execution-role credentials. Only a
DIFFERENT account_id triggers an sts:AssumeRole into
IamAnalyzerAssistantSpokeRole (see infrastructure/cdk/stacks/spoke_stack.py
and scan_permissions.py for the role name, trust policy, and the exact
IAM/CloudTrail actions it grants — this tool's own calls are a strict
subset of that policy, so a cross-account call either returns a real
answer or fails with AccessDenied; it can never silently see more than
the spoke role allows).
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Matches scan_permissions.SPOKE_ROLE_NAME on the CDK side — kept as an
# env var (set by tools_construct.py) rather than importing the CDK
# module directly, since this file runs inside the Lambda runtime, not
# the CDK synth process.
SPOKE_ROLE_NAME = os.environ.get("SPOKE_ROLE_NAME", "IamAnalyzerAssistantSpokeRole")

sts_client = boto3.client("sts")

# Cache the hub's own account id for the life of a warm Lambda — every
# call needs it to decide same-account vs. cross-account, and
# get_caller_identity is a real network call worth not repeating.
_HUB_ACCOUNT_ID: str | None = None


def _hub_account_id() -> str:
    global _HUB_ACCOUNT_ID
    if _HUB_ACCOUNT_ID is None:
        _HUB_ACCOUNT_ID = sts_client.get_caller_identity()["Account"]
    return _HUB_ACCOUNT_ID


def session_for_account(account_id: str):
    """Return a boto3 Session for reading ``account_id``, or None for the
    hub's own account (callers should fall back to the default/module-level
    boto3 clients in that case — see handler() below).

    Ported from the ``session_for_account()`` helper in
    ``operations-automation/aws-services-lifecycle-tracker/backend/account_discovery.py``
    (#144), adapted to this package's single-tool-per-Lambda shape rather
    than a shared scanner-index object.
    """
    if not account_id or account_id == _hub_account_id():
        return None
    partition = sts_client.meta.partition
    role_arn = f"arn:{partition}:iam::{account_id}:role/{SPOKE_ROLE_NAME}"
    creds = sts_client.assume_role(
        RoleArn=role_arn,
        RoleSessionName="iam-analyzer-assistant-cross-account",
        DurationSeconds=3600,
    )["Credentials"]
    return boto3.session.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


def _coverage(state: str, detail: str, count: int | None = None) -> dict:
    """Build a coverage entry per the #171 contract. Source is always
    "iam" — this tool reads the same service the local IAM tools do, just
    in a different account."""
    entry = {"source": "iam", "state": state, "detail": detail}
    if count is not None:
        entry["count"] = count
    return entry


def handler(event, context=None):
    """Read IAM roles + CloudTrail reachability for ONE named account.

    Args:
        event: {
            account_id: str - REQUIRED. The 12-digit account to read.
                Must be the hub's own account, or an account with
                IamAnalyzerAssistantSpokeRole deployed (via SpokeStack or
                the OrgStack StackSet rollout).
            limit: int - max roles to return (default: 20, max: 100)
        }

    Returns:
        {
            account_id, is_hub_account: bool,
            roles: [{name, arn, high_privilege: bool, create_date}],
            returned_count, has_more,
            cloudtrail_reachable: bool,
            coverage: [...]
        }
    """
    account_id = event.get("account_id")
    if not account_id:
        return {
            "error": (
                "account_id is required. This tool reads ONE account at a "
                "time by design — ask the user which account, or offer "
                "list_findings instead if they want an org-wide Security "
                "Hub view (that aggregates automatically when this hub "
                "account is the Security Hub delegated administrator, "
                "with no per-account call needed)."
            )
        }
    if not (isinstance(account_id, str) and account_id.isdigit() and len(account_id) == 12):
        return {"error": f"account_id must be a 12-digit AWS account id, got {account_id!r}"}

    limit = max(1, min(event.get("limit", 20), 100))
    is_hub_account = account_id == _hub_account_id()

    try:
        session = session_for_account(account_id)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("AccessDenied", "AccessDeniedException"):
            detail = (
                f"could not assume {SPOKE_ROLE_NAME} in account {account_id} — "
                "that account likely does not have the spoke role deployed "
                "(SpokeStack / the multi-account StackSet rollout is opt-in; "
                "it may simply not be set up for this account yet)"
            )
        else:
            detail = f"could not assume role in account {account_id}: {type(e).__name__}: {e}"
        logger.warning(detail)
        return {
            "account_id": account_id,
            "is_hub_account": is_hub_account,
            "error": detail,
            "coverage": [_coverage("unavailable", detail)],
        }
    except Exception as e:
        detail = f"could not establish a session for account {account_id}: {type(e).__name__}: {e}"
        logger.error(detail, exc_info=True)
        return {
            "account_id": account_id,
            "is_hub_account": is_hub_account,
            "error": detail,
            "coverage": [_coverage("unavailable", detail)],
        }

    # session is None for the hub's own account — fall back to the
    # Lambda's own execution-role credentials by constructing clients with
    # no explicit session, same as boto3.client(...) at module scope
    # elsewhere in this codebase.
    iam_client = session.client("iam") if session else boto3.client("iam")
    cloudtrail_client = session.client("cloudtrail") if session else boto3.client("cloudtrail")

    result = {
        "account_id": account_id,
        "is_hub_account": is_hub_account,
        "roles": [],
        "returned_count": 0,
        "has_more": False,
        "cloudtrail_reachable": False,
    }
    coverage: list = []

    try:
        paginator = iam_client.get_paginator("list_roles")
        roles: list = []
        has_more = False
        for page in paginator.paginate(PaginationConfig={"MaxItems": limit + 1}):
            for role in page.get("Roles", []):
                roles.append(role)
                if len(roles) > limit:
                    has_more = True
                    break
            if has_more:
                break

        # The high-privilege check does one iam:ListAttachedRolePolicies
        # call per role. Done sequentially, a full page of roles is that
        # many round-trips back-to-back, which pushed a single read toward
        # the API gateway's 29s ceiling (measured ~15-21s for ~20 roles).
        # Run the per-role checks CONCURRENTLY so total time is roughly the
        # slowest single call instead of their sum — the same pattern
        # compare_roles.py already uses. boto3 clients are thread-safe, and
        # executor.map preserves input order, so the roles list stays in
        # the same order the paginator returned.
        selected = roles[:limit]
        with ThreadPoolExecutor(max_workers=min(len(selected), 8) or 1) as executor:
            high_privilege_flags = list(
                executor.map(
                    lambda r: _is_high_privilege(iam_client, r["RoleName"]),
                    selected,
                )
            )

        for role, high_privilege in zip(selected, high_privilege_flags):
            result["roles"].append(
                {
                    "name": role["RoleName"],
                    "arn": role["Arn"],
                    "high_privilege": high_privilege,
                    "create_date": str(role.get("CreateDate", "")),
                }
            )

        result["returned_count"] = len(result["roles"])
        result["has_more"] = has_more
        coverage.append(
            _coverage(
                "checked" if result["roles"] else "empty",
                f"IAM roles in account {account_id}: {result['returned_count']} returned"
                + (" (more available)" if has_more else ""),
                count=result["returned_count"],
            )
        )
    except ClientError as e:
        detail = f"IAM read failed in account {account_id}: {type(e).__name__}: {e}"
        logger.warning(detail)
        coverage.append(_coverage("unavailable", detail))

    try:
        cloudtrail_client.lookup_events(MaxResults=1)
        result["cloudtrail_reachable"] = True
        coverage.append(_coverage("checked", f"CloudTrail reachable in account {account_id}"))
    except ClientError as e:
        detail = f"CloudTrail not reachable in account {account_id}: {type(e).__name__}: {e}"
        logger.warning(detail)
        coverage.append(_coverage("unavailable", detail))

    result["coverage"] = coverage
    return result


def _is_high_privilege(iam_client, role_name: str) -> bool:
    """Cheap admin/wildcard check — attached managed-policy names only, no
    policy-document fetch. This tool is a lightweight cross-account
    SNAPSHOT, not the full privilege analysis check_dependencies already
    does locally; a user who wants the deep analysis on a specific role
    found here can follow up by asking about that role by name/ARN, which
    still only works for the hub's own account today (check_dependencies
    has not been made cross-account-aware — a possible future extension,
    not part of this change)."""
    try:
        attached = iam_client.list_attached_role_policies(RoleName=role_name)
        for policy in attached.get("AttachedPolicies", []):
            name = policy["PolicyName"]
            if "Admin" in name or "FullAccess" in name:
                return True
    except ClientError:
        pass
    return False
