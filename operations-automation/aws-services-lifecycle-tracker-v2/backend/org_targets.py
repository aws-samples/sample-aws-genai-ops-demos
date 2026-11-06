"""
Scan targets for the multi-account (hub-and-spoke) scan (issue #144).

The `_scan_targets` control row in the state table says WHAT to scan:

    {"source": "hub" | "manual" | "organization" | "ou",
     "accounts": [{"id": "1234...", "name": "..."}],   # manual list (also used as fallback)
     "ou_ids": ["ou-xxxx-yyyyyyyy"],                    # for source "ou"
     "exclude_accounts": ["1234..."],
     "regions": ["eu-central-1", ...]}

resolve_targets() turns that into the concrete list of accounts for a run,
asking AWS Organizations when the source is "organization" or "ou": account
names, status and the OU path come from there. The hub account is always
included and always scanned with its own credentials. Organizations access is
optional: the hub must be the management account or a delegated administrator
(see shared/scripts/check-org-access); when the call is denied the manual list
is used and the reason is reported, never raised.
"""
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import ClientError


def _accounts_in_organization(org) -> List[Dict]:
    accounts = []
    for page in org.get_paginator("list_accounts").paginate():
        accounts += page.get("Accounts", [])
    return accounts


def _accounts_in_ou(org, ou_id: str) -> List[Dict]:
    """Accounts directly under an OU (or root) and recursively under its child OUs."""
    accounts = []
    for page in org.get_paginator("list_accounts_for_parent").paginate(ParentId=ou_id):
        accounts += page.get("Accounts", [])
    for page in org.get_paginator("list_organizational_units_for_parent").paginate(ParentId=ou_id):
        for child in page.get("OrganizationalUnits", []):
            accounts += _accounts_in_ou(org, child["Id"])
    return accounts


def _ou_path(org, account_id: str, cache: Dict[str, str]) -> str:
    """'Root/Workloads/Prod' for an account; parents are resolved once per run."""
    names, parent_id = [], account_id
    for _ in range(10):  # OU nesting is capped at 5 by Organizations
        parents = org.list_parents(ChildId=parent_id).get("Parents", [])
        if not parents:
            break
        parent = parents[0]
        pid, ptype = parent["Id"], parent["Type"]
        if pid not in cache:
            if ptype == "ROOT":
                cache[pid] = "Root"
            else:
                cache[pid] = org.describe_organizational_unit(OrganizationalUnitId=pid)["OrganizationalUnit"]["Name"]
        names.append(cache[pid])
        if ptype == "ROOT":
            break
        parent_id = pid
    return "/".join(reversed(names))


def resolve_targets(targets: Dict, hub_account_id: str, hub_region: str, org_client=None) -> Dict:
    """Concrete accounts + regions for one run. Never raises.

    Returns {"accounts": [{id, name, ou_path, status}], "regions": [...],
             "source": str, "errors": [str]}
    """
    targets = targets or {}
    source = str(targets.get("source") or ("manual" if targets.get("accounts") else "hub")).lower()
    regions = [r for r in (targets.get("regions") or []) if r] or [hub_region]
    excluded = set(str(a) for a in (targets.get("exclude_accounts") or []))
    manual = [{"id": str(a.get("id", "")), "name": a.get("name", ""), "ou_path": a.get("ou_path", ""), "status": "ACTIVE"}
              for a in (targets.get("accounts") or []) if a.get("id")]
    errors: List[str] = []
    accounts: List[Dict] = []

    if source in ("organization", "ou"):
        org = org_client or boto3.client("organizations", region_name=hub_region)
        try:
            if source == "organization":
                raw = _accounts_in_organization(org)
            else:
                raw, seen = [], set()
                for ou_id in targets.get("ou_ids") or []:
                    for a in _accounts_in_ou(org, ou_id):
                        if a["Id"] not in seen:
                            seen.add(a["Id"]); raw.append(a)
                if not targets.get("ou_ids"):
                    errors.append("source is 'ou' but ou_ids is empty")
            cache: Dict[str, str] = {}
            for a in raw:
                if a.get("Status") != "ACTIVE":
                    continue  # suspended / closed accounts cannot be assumed into
                try:
                    ou_path = _ou_path(org, a["Id"], cache)
                except ClientError:
                    ou_path = ""
                accounts.append({"id": a["Id"], "name": a.get("Name", ""), "ou_path": ou_path, "status": "ACTIVE"})
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "ClientError")
            errors.append(f"Organizations {code}: falling back to the manual account list "
                          f"(hub must be the management account or a delegated administrator)")
            accounts = list(manual)
        except Exception as e:  # pragma: no cover
            errors.append(f"Organizations {type(e).__name__}: {str(e)[:120]}")
            accounts = list(manual)
    elif source == "manual":
        accounts = list(manual)
    else:
        source = "hub"

    accounts = [a for a in accounts if a["id"] not in excluded]
    # the hub always scans itself (with its own credentials), and comes first
    hub = next((a for a in accounts if a["id"] == hub_account_id), None) \
        or next((a for a in manual if a["id"] == hub_account_id), None) \
        or {"id": hub_account_id, "name": "", "ou_path": "", "status": "ACTIVE"}
    others = sorted((a for a in accounts if a["id"] != hub_account_id), key=lambda a: a["id"])
    accounts = [hub] + others
    return {"accounts": accounts, "regions": regions, "source": source, "errors": errors}
