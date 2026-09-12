"""Unit tests for scan target resolution (org_targets.py, #144). Offline."""
from botocore.exceptions import ClientError

from org_targets import resolve_targets

HUB = "111111111111"


class _Pages:
    def __init__(self, key, items):
        self._key, self._items = key, items

    def paginate(self, **_):
        return iter([{self._key: self._items}])


class FakeOrg:
    """Root r-1 { A, ou-prod { B, ou-prod-eu { C } }, ou-sandbox { D (SUSPENDED) } }"""
    accounts = {
        "A": {"Id": "222222222222", "Name": "Account A", "Status": "ACTIVE"},
        "B": {"Id": "333333333333", "Name": "Account B", "Status": "ACTIVE"},
        "C": {"Id": "444444444444", "Name": "Account C", "Status": "ACTIVE"},
        "D": {"Id": "555555555555", "Name": "Sandbox", "Status": "SUSPENDED"},
        "H": {"Id": HUB, "Name": "Hub", "Status": "ACTIVE"},
    }
    children = {"r-1": (["H", "A"], ["ou-prod", "ou-sandbox"]), "ou-prod": (["B"], ["ou-prod-eu"]),
                "ou-prod-eu": (["C"], []), "ou-sandbox": (["D"], [])}
    parent_of = {HUB: ("r-1", "ROOT"), "222222222222": ("r-1", "ROOT"), "333333333333": ("ou-prod", "ORGANIZATIONAL_UNIT"),
                 "444444444444": ("ou-prod-eu", "ORGANIZATIONAL_UNIT"), "555555555555": ("ou-sandbox", "ORGANIZATIONAL_UNIT"),
                 "ou-prod": ("r-1", "ROOT"), "ou-prod-eu": ("ou-prod", "ORGANIZATIONAL_UNIT"), "ou-sandbox": ("r-1", "ROOT")}
    ou_names = {"ou-prod": "Prod", "ou-prod-eu": "EU", "ou-sandbox": "Sandbox"}

    def __init__(self, denied=False):
        self.denied = denied

    def _deny(self, op):
        if self.denied:
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "nope"}}, op)

    def get_paginator(self, name):
        self._deny(name)
        if name == "list_accounts":
            return _Pages("Accounts", list(self.accounts.values()))
        if name == "list_accounts_for_parent":
            return _ParentPages(self, "Accounts")
        if name == "list_organizational_units_for_parent":
            return _ParentPages(self, "OrganizationalUnits")
        raise AssertionError(name)

    def list_parents(self, ChildId):
        pid, ptype = self.parent_of[ChildId]
        return {"Parents": [{"Id": pid, "Type": ptype}]}

    def describe_organizational_unit(self, OrganizationalUnitId):
        return {"OrganizationalUnit": {"Id": OrganizationalUnitId, "Name": self.ou_names[OrganizationalUnitId]}}


class _ParentPages:
    def __init__(self, org, key):
        self._org, self._key = org, key

    def paginate(self, ParentId):
        accts, ous = self._org.children.get(ParentId, ([], []))
        if self._key == "Accounts":
            return iter([{"Accounts": [self._org.accounts[k] for k in accts]}])
        return iter([{"OrganizationalUnits": [{"Id": o, "Name": self._org.ou_names[o]} for o in ous]}])


def test_default_is_hub_only():
    r = resolve_targets({}, HUB, "eu-central-1")
    assert r == {"accounts": [{"id": HUB, "name": "", "ou_path": "", "status": "ACTIVE"}],
                 "regions": ["eu-central-1"], "source": "hub", "errors": []}


def test_manual_list_adds_hub_first_and_keeps_regions():
    r = resolve_targets({"accounts": [{"id": "333333333333", "name": "B"}], "regions": ["eu-west-1", "us-east-1"]}, HUB, "eu-central-1")
    assert [a["id"] for a in r["accounts"]] == [HUB, "333333333333"]
    assert r["regions"] == ["eu-west-1", "us-east-1"] and r["source"] == "manual"


def test_organization_source_lists_active_accounts_with_ou_paths():
    r = resolve_targets({"source": "organization"}, HUB, "eu-central-1", org_client=FakeOrg())
    ids = [a["id"] for a in r["accounts"]]
    assert ids == [HUB, "222222222222", "333333333333", "444444444444"]  # hub first, suspended D dropped
    by_id = {a["id"]: a for a in r["accounts"]}
    assert by_id["444444444444"] == {"id": "444444444444", "name": "Account C", "ou_path": "Root/Prod/EU", "status": "ACTIVE"}
    assert by_id["222222222222"]["ou_path"] == "Root"
    assert r["errors"] == []


def test_ou_source_is_recursive_and_exclusions_apply():
    r = resolve_targets({"source": "ou", "ou_ids": ["ou-prod"], "exclude_accounts": ["444444444444"]}, HUB, "eu-central-1", org_client=FakeOrg())
    assert [a["id"] for a in r["accounts"]] == [HUB, "333333333333"]  # C excluded, hub always present


def test_access_denied_falls_back_to_manual_list_and_reports():
    r = resolve_targets({"source": "organization", "accounts": [{"id": "222222222222", "name": "A"}]}, HUB, "eu-central-1",
                        org_client=FakeOrg(denied=True))
    assert [a["id"] for a in r["accounts"]] == [HUB, "222222222222"]
    assert len(r["errors"]) == 1 and "AccessDeniedException" in r["errors"][0]
