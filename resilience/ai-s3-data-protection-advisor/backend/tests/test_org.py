"""collect/org.scope_accounts: the hub first, active members by id; the hub alone, with the reason, when the
organization cannot be listed."""
import boto3
from botocore.stub import Stubber

from s3dp.collect import org

HUB, A, B = "111122223333", "222233334444", "333344445555"


class Session:
    def __init__(self, client):
        self.c = client

    def client(self, name, **kw):
        assert name == "organizations"
        return self.c


def _org(pages=None, error=None):
    c = boto3.client("organizations", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")
    st = Stubber(c)
    if error:
        st.add_client_error("list_accounts", error, http_status_code=400)
    for i, accounts in enumerate(pages or []):
        resp = {"Accounts": accounts}
        if i < len(pages) - 1:
            resp["NextToken"] = f"t{i}"
        st.add_response("list_accounts", resp)
    st.activate()
    return Session(c), st


def test_members_sorted_hub_first_inactive_skipped_across_pages():
    s, st = _org(pages=[
        [{"Id": B, "Name": "b", "State": "ACTIVE"}, {"Id": HUB, "Name": "mgmt", "State": "ACTIVE"}],
        [{"Id": A, "Name": "a", "Status": "ACTIVE"},                      # older answer shape: Status only
         {"Id": "444455556666", "Name": "gone", "State": "SUSPENDED"}]])
    out = org.scope_accounts(s, HUB)
    st.assert_no_pending_responses()
    assert out == {"accounts": [{"id": HUB, "name": None, "hub": True}, {"id": A, "name": "a", "hub": False},
                                {"id": B, "name": "b", "hub": False}],
                   "source": "organization", "unavailable": []}


def test_not_in_an_organization():
    s, _ = _org(error="AWSOrganizationsNotInUseException")
    out = org.scope_accounts(s, HUB)
    assert out["accounts"] == [{"id": HUB, "name": None, "hub": True}] and out["source"] == "hub"
    assert out["unavailable"] == [[org.NOT_LISTED, "this account is not in an organization"]]


def test_member_account_hub_cannot_list():
    s, _ = _org(error="AccessDeniedException")
    out = org.scope_accounts(s, HUB)
    assert out["source"] == "hub" and out["unavailable"][0][1].startswith("AccessDeniedException:")


def test_explicit_list_needs_no_organizations_call():
    out = org.scope_accounts(None, HUB, [B, A, HUB, A])
    assert [a["id"] for a in out["accounts"]] == [HUB, A, B] and out["source"] == "list"
