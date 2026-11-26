"""Which accounts an organization scan covers. Read-only and never raises: when the organization cannot be listed,
the scan covers the hub alone and the reason is reported (an empty list must never read as "no member accounts")."""
from botocore.exceptions import BotoCoreError, ClientError

NOT_LISTED = "Organization accounts (only this account was scanned)"


def _code(e):
    return e.response.get("Error", {}).get("Code", "Unknown") if isinstance(e, ClientError) else type(e).__name__


def _active(a):
    # `State` replaces the deprecated `Status`; read whichever the API returns.
    return (a.get("State") or a.get("Status")) == "ACTIVE"


def scope_accounts(session, hub_id, listed=()):
    """{"accounts": [{"id", "name", "hub"}] (hub first, members by id), "source": "organization" | "list" | "hub",
    "unavailable": [[what, why]]}. `listed` (explicit account ids) replaces the organization listing. A member's
    `name` is its Organizations name; the hub's is None so its IAM alias names it, as in a single-account scan."""
    hub = {"id": hub_id, "name": None, "hub": True}
    if listed:
        ids = sorted({a for a in listed if a != hub_id})
        return {"accounts": [hub] + [{"id": a, "name": None, "hub": False} for a in ids], "source": "list",
                "unavailable": []}
    try:
        pages = session.client("organizations").get_paginator("list_accounts").paginate()
        members = [a for p in pages for a in p["Accounts"] if _active(a) and a["Id"] != hub_id]
    except (ClientError, BotoCoreError) as e:
        code = _code(e)
        why = ("this account is not in an organization" if code == "AWSOrganizationsNotInUseException"
               else f"{code}: this account is neither the management account nor a delegated administrator")
        return {"accounts": [hub], "source": "hub", "unavailable": [[NOT_LISTED, why]]}
    members.sort(key=lambda a: a["Id"])
    return {"accounts": [hub] + [{"id": a["Id"], "name": (a.get("Name") or "")[:64] or None, "hub": False}
                                 for a in members],
            "source": "organization", "unavailable": []}
