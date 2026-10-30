"""
Organization account enumeration for the multi-account scan.

Single-account is the default outcome, but it is decided at RUNTIME, not by a
deploy-time flag: every refresh ATTEMPTS to read AWS Organizations and degrades
to the hub account alone when it cannot. This keeps the scan durable across
redeploys — a plain deploy-all leaves a pipeline that already fans out to
whatever org it can read, with no MULTI_ACCOUNT env var to drift out of sync.

resolve_accounts() turns a refresh spec's accounts into the concrete list of
accounts for a run:

  * explicit spec accounts win, verbatim (a user narrowing the scan);
  * otherwise AWS Organizations is asked for the ACTIVE member accounts
    (organizations:ListAccounts); the hub is always included, carries its REAL
    account id (so its inventory rows are keyed and displayed like any other
    account's) and is always scanned with the pipeline's own credentials
    (discover.session_for_account recognises the hub id and never assumes a
    role into itself);
  * a standalone account, an account that is not the org management/delegated
    administrator (AccessDenied), or a single-member org degrades cleanly to the
    hub alone with NO surfaced error — single-account is a normal outcome;
  * any OTHER failure (throttling, a genuine service error) also degrades to the
    hub, but keeps a one-line reported error so real misconfig stays visible.

Organizations access is optional: a denied/failed call never raises.
"""
import logging
from typing import Dict, List

import boto3
from botocore.exceptions import ClientError

import discover
from common import error_code

logger = logging.getLogger(__name__)

# Organizations error codes that mean "there is simply no org to enumerate from
# here" — a standalone account or a non-management/non-delegated account. These
# are the expected single-account path, so they degrade silently (errors == []).
_CLEAN_DEGRADE_CODES = frozenset({
    "AccessDeniedException",
    "AWSOrganizationsNotInUseException",
    "OrganizationNotInUseException",
})


def _active_org_accounts(org) -> List[Dict]:
    """ACTIVE member accounts of the organization (suspended/closed excluded:
    they cannot be assumed into)."""
    accounts = []
    for page in org.get_paginator("list_accounts").paginate():
        for a in page.get("Accounts", []):
            if a.get("Status") == "ACTIVE":
                accounts.append({"id": a["Id"], "name": a.get("Name", "")})
    return accounts


def resolve_accounts(spec_accounts: List[Dict], hub_account_id: str,
                     region: str, org_client=None) -> Dict:
    """Concrete accounts for one run. Never raises.

    Multi-account vs single-account is decided here at runtime: Organizations is
    always attempted and the result degrades to the hub when it cannot be read.

    Args:
        spec_accounts: the refresh spec's explicit accounts ([] = none given).
        hub_account_id: the account the pipeline runs in (always scanned). Its
            REAL id from STS; "" only when STS failed, in which case the hub is
            still returned, with the empty id (never crash).
        region: the pipeline's own region (Organizations endpoint region).
        org_client: injected Organizations client (tests); real client otherwise.

    Returns {"accounts": [{"id", "name"}], "source": str, "errors": [str]}.
      source: "manual"       — explicit spec accounts honored as-is
              "organization" — enumerated from AWS Organizations (>1 account)
              "hub"          — single-account (hub only), by degrade or 1-member org
    The hub entry always comes first and carries hub_account_id as its id.
    """
    errors: List[str] = []
    hub_only = {"accounts": [_hub_entry(hub_account_id, "")], "source": "hub", "errors": errors}

    # Explicit spec accounts win: a user narrowing the scan is honored verbatim.
    explicit = [{"id": str(a.get("id", "")), "name": a.get("name", "") or ""}
                for a in (spec_accounts or []) if a.get("id")]
    if explicit:
        return {"accounts": _hub_first(explicit, hub_account_id), "source": "manual", "errors": errors}

    # Attempt Organizations enumeration. The hub keeps its real id: discover's
    # session_for_account recognises it (account_id == caller account) and uses
    # the pipeline's own credentials rather than assuming a spoke role.
    org = org_client or boto3.client("organizations", region_name=region)
    try:
        accounts = _active_org_accounts(org)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "ClientError")
        if code in _CLEAN_DEGRADE_CODES:
            # Standalone account or not the org management/delegated admin: this
            # is the normal single-account outcome, surface no error.
            return hub_only
        # A genuine failure (throttling, service error): degrade but stay visible.
        errors.append(f"Organizations {code}: scanning the hub account only "
                      f"(the hub must be the management account or a delegated administrator)")
        return hub_only
    except Exception as e:  # pragma: no cover - defensive, never fail the run
        errors.append(f"Organizations {type(e).__name__}: {str(e)[:120]}")
        return hub_only

    # A single-member org (only the hub) is single-account, not a fan-out.
    members = [a for a in accounts if a["id"] and a["id"] != hub_account_id]
    if not members:
        hub_name = next((a.get("name", "") for a in accounts if a["id"] == hub_account_id), "")
        return {"accounts": [_hub_entry(hub_account_id, hub_name)], "source": "hub", "errors": errors}

    return {"accounts": _hub_first(accounts, hub_account_id), "source": "organization", "errors": errors}


def _hub_entry(hub_account_id: str, name: str) -> Dict:
    """The hub's account entry: its REAL id (so inventory rows are keyed
    fn#<hub>#..., the Account column shows it and the rollup can group by it) and
    its display name. When STS could not name the hub (hub_account_id == "") the
    id stays "", which session_for_account still maps to own credentials."""
    return {"id": hub_account_id or "", "name": name or ""}


def _hub_first(accounts: List[Dict], hub_account_id: str) -> List[Dict]:
    """Hub account first (scanned with its own credentials), the rest sorted.

    The hub carries its real id; session_for_account compares it to the caller
    account and uses the pipeline's own credentials (never assume-role into
    yourself). Member accounts keep their real id, which triggers the spoke-role
    assume path."""
    others = sorted((a for a in accounts if a["id"] and a["id"] != hub_account_id),
                    key=lambda a: a["id"])
    hub_name = next((a.get("name", "") for a in accounts if a["id"] == hub_account_id), "")
    return [_hub_entry(hub_account_id, hub_name)] + others


def hub_account_id(region: str) -> str:
    """The account the pipeline runs in (cached by discover._caller_account)."""
    return discover._caller_account(region)


def account_name(account_id: str, region: str, org_client=None) -> str:
    """The AWS Organizations display name for one account (e.g. "payments-prod").

    Uses organizations:DescribeAccount — the same source (and the same existing
    org-read permission) that gives member accounts their names via ListAccounts.
    Best effort: returns "" when the org cannot be read (standalone account,
    not management/delegated admin, or any error), so the caller can fall back.
    """
    if not account_id:
        return ""
    try:
        org = org_client or boto3.client("organizations", region_name=region)
        acct = org.describe_account(AccountId=account_id).get("Account") or {}
        return acct.get("Name", "")
    except Exception as exc:  # pragma: no cover - the name is best-effort
        logger.warning("could not read an account name: %s", error_code(exc))
        return ""
