"""Unit tests for org_accounts.resolve_accounts — the runtime multi-account
enumeration path (design §5). Pure/injected: no real AWS calls.

Multi-account vs single-account is decided at runtime by attempting
Organizations and degrading to the hub, so the injected org client — not an
env var — drives behavior. Covers:
  * standalone account / Organizations not in use -> hub only, no error;
  * a single-member org -> hub only, no error;
  * explicit spec accounts honored verbatim;
  * org-wide refresh enumerates ACTIVE member accounts, hub first, others sorted;
  * suspended/closed accounts excluded (cannot be assumed into);
  * a genuine (non-degrade) Organizations error falls back to the hub AND is
    reported, never raised.
"""
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

from org_accounts import resolve_accounts

HUB = "111111111111"
REGION = "us-east-1"


def _org_raising(code):
    """A fake Organizations client whose ListAccounts paginator raises `code`."""
    org = MagicMock()
    paginator = MagicMock()
    paginator.paginate.side_effect = ClientError(
        {"Error": {"Code": code, "Message": "no"}}, "ListAccounts")
    org.get_paginator.return_value = paginator
    return org


def _org_with(accounts):
    """A fake Organizations client whose ListAccounts paginator yields one page."""
    org = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Accounts": accounts}]
    org.get_paginator.return_value = paginator
    return org


# --- single-account (clean degrade) -----------------------------------------

def test_single_account_default_is_hub_only():
    # Standalone account: Organizations is not in use -> hub only, no error.
    org = _org_raising("AWSOrganizationsNotInUseException")
    resolved = resolve_accounts([], HUB, REGION, org_client=org)
    assert resolved["source"] == "hub"
    assert resolved["accounts"] == [{"id": HUB, "name": ""}]
    assert resolved["errors"] == []


def test_access_denied_degrades_to_hub_without_error():
    # Not the management/delegated account: AccessDenied is the normal
    # single-account outcome and must NOT surface an error.
    org = _org_raising("AccessDeniedException")
    resolved = resolve_accounts([], HUB, REGION, org_client=org)
    assert resolved["source"] == "hub"
    assert resolved["accounts"] == [{"id": HUB, "name": ""}]
    assert resolved["errors"] == []


def test_single_member_org_is_hub_only():
    # An org whose only ACTIVE account is the hub is single-account, not a fan-out.
    org = _org_with([{"Id": HUB, "Name": "hub", "Status": "ACTIVE"}])
    resolved = resolve_accounts([], HUB, REGION, org_client=org)
    assert resolved["source"] == "hub"
    assert resolved["accounts"] == [{"id": HUB, "name": "hub"}]
    assert resolved["errors"] == []


# --- explicit spec accounts win ---------------------------------------------

def test_explicit_accounts_honored():
    resolved = resolve_accounts(
        [{"id": "222222222222", "name": "payments"}], HUB, REGION, org_client=MagicMock())
    assert resolved["source"] == "manual"
    # Hub comes first (real id; own credentials), then the explicit account.
    assert resolved["accounts"][0] == {"id": HUB, "name": ""}
    assert {"id": "222222222222", "name": "payments"} in resolved["accounts"]


# --- org-wide enumeration ---------------------------------------------------

def test_org_enumeration_fans_out_to_active_members():
    org = _org_with([
        {"Id": HUB, "Name": "hub", "Status": "ACTIVE"},
        {"Id": "333333333333", "Name": "prod", "Status": "ACTIVE"},
        {"Id": "222222222222", "Name": "dev", "Status": "ACTIVE"},
    ])
    resolved = resolve_accounts([], HUB, REGION, org_client=org)
    assert resolved["source"] == "organization"
    # Hub first (real id), members sorted by id. Hub name is carried through.
    assert resolved["accounts"] == [
        {"id": HUB, "name": "hub"},
        {"id": "222222222222", "name": "dev"},
        {"id": "333333333333", "name": "prod"},
    ]
    assert resolved["errors"] == []


def test_org_enumeration_excludes_suspended_accounts():
    org = _org_with([
        {"Id": HUB, "Name": "hub", "Status": "ACTIVE"},
        {"Id": "444444444444", "Name": "closed", "Status": "SUSPENDED"},
        {"Id": "222222222222", "Name": "dev", "Status": "ACTIVE"},
    ])
    resolved = resolve_accounts([], HUB, REGION, org_client=org)
    ids = [a["id"] for a in resolved["accounts"]]
    assert "444444444444" not in ids
    assert ids == [HUB, "222222222222"]


def test_hub_keeps_empty_id_only_when_sts_could_not_name_it():
    # STS failed upstream (hub_account_id == ""): never crash, the hub is still
    # scanned, with the empty id session_for_account maps to own credentials.
    org = _org_raising("AccessDeniedException")
    resolved = resolve_accounts([], "", REGION, org_client=org)
    assert resolved["accounts"] == [{"id": "", "name": ""}]
    assert resolved["errors"] == []


def test_genuine_org_error_falls_back_to_hub_and_reports():
    # A non-degrade failure (throttling / service error) degrades to the hub but
    # keeps the misconfig visible via errors.
    org = _org_raising("TooManyRequestsException")
    resolved = resolve_accounts([], HUB, REGION, org_client=org)
    assert resolved["source"] == "hub"
    assert resolved["accounts"] == [{"id": HUB, "name": ""}]
    assert len(resolved["errors"]) == 1
    assert "TooManyRequestsException" in resolved["errors"][0]
