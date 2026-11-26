import json

import boto3
import pytest
from botocore.stub import Stubber

from s3dp import run as seq
from s3dp.collect import scan
from s3dp.service import steps
from tests.test_cli import FakeBedrock
from tests.test_scan import ACCOUNT, _session
from tests.test_service_store import make

RUN = "scan-20261125-101500"
MEMBER, DENIED = "222233334444", "333344445555"


class Sts:
    def get_caller_identity(self):
        return {"Account": ACCOUNT}


class Session:
    """The scan's fake session plus an sts client, (optionally) Amazon Bedrock and AWS Organizations."""
    def __init__(self, inner, bedrock=None, organizations=None):
        self.inner, self.bedrock, self.organizations = inner, bedrock, organizations
        self.region_name = inner.region_name

    def client(self, name, region_name=None, config=None):
        if name == "sts":
            return Sts()
        if name == "bedrock-runtime":
            return self.bedrock
        if name == "organizations":
            return self.organizations
        return self.inner.client(name, region_name=region_name, config=config)

    def get_partition_for_region(self, r):
        return "aws"


def _active(bucket=None):
    s = _session()
    if bucket:      # same account settings, another bucket name (bucket names are global)
        s.stub("s3").add_response("list_buckets", {"Buckets": [{"Name": bucket, "BucketRegion": "eu-west-1"}]})
    for _, st in s.stubs.values():
        st.activate()
    return s


def _org(accounts=None, error=None):
    c = boto3.client("organizations", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")
    st = Stubber(c)
    if error:
        st.add_client_error("list_accounts", error, http_status_code=400)
    else:
        st.add_response("list_accounts", {"Accounts": accounts})
    st.activate()
    return c


def _account(id, name, state="ACTIVE"):
    return {"Id": id, "Name": name, "State": state}


def _pipeline(store, session, model="", fail_units=(), scope="account"):
    """The pipeline's sequence over the plain steps, for every account of the scope."""
    run = steps.accounts(store, session, RUN, "2026-11-25T10:15:00Z", scope)
    per = []
    for a in run["accounts"]:
        reg = steps.regions(store, session, run, a)
        for n in range(reg["units"]):
            if n not in fail_units:
                steps.collect(store, session, run, a, n)
        per.append(dict(reg, failed=[n for n in fail_units if n < reg["units"]]))
    out = {"run": run, "regions": per, "assemble": steps.assemble(store, run, per)}
    out["classify"] = steps.classify(store, session, run, model)
    out["score"] = steps.score(store, run)
    out["narrative"] = steps.narrative(store, session, run, model)
    out["publish"] = steps.publish(store, run, "2026-11-25T10:20:00Z")
    return out


def _cli_data(session, model=""):
    """What the CLI's sequence produces for the same session."""
    from s3dp.ai import bedrock
    raw = scan.collect_account(session, ACCOUNT)
    client = bedrock.client(session) if model else None
    roles, state = seq.classify_roles(raw, client, model)
    inv, data, _ = seq.score(raw, roles, state)
    return seq.narrate(inv, data, client, model, state)


def _strip(data):
    return json.loads(json.dumps(data, default=str))


def test_steps_reproduce_the_cli_data_rules_only():
    store, s3, table = make()
    out = _pipeline(store, Session(_active()))
    expected = _cli_data(Session(_active()))
    got = store.get_json(f"runs/{RUN}/data.json")
    for d in (got, expected):
        d.get("meta", {}).pop("scan_date", None)
        d.get("meta", {}).pop("generated", None)
    assert got == _strip(expected)
    for ext in ("xlsx", "html"):
        assert store.exists(f"runs/{RUN}/report.{ext}")
    run = store.get_run(RUN)
    assert run["status"] == "SUCCEEDED" and run["finished_at"] == "2026-11-25T10:20:00Z"
    assert run["buckets"] == 1 and run["regions"] == 1 and run["scope"] == "1 account (acme)"
    assert set(run["recs"]) == {"HIGH", "MEDIUM", "LOW"} and run["ai"]["classification"] is False
    assert run["accounts"] == [{"id": ACCOUNT, "name": "acme", "outcome": "scanned"}]
    assert out["regions"] == [{"account": ACCOUNT, "units": 1, "buckets": 1, "failed": []}]
    tab = next(t for t in got["tabs"] if t["id"] == "recommendations")
    for sev in ("HIGH", "MEDIUM", "LOW"):
        assert run["recs"][sev] == sum(i["severity"] == sev for i in tab["items"])
    assert sum(run["recs"].values()) > 0


def test_accounts_records_running_state():
    store, _, _ = make()
    out = steps.accounts(store, Session(_active()), RUN, "t0")
    assert out == {"run_id": RUN, "started_at": "t0", "scope": "account", "account_id": ACCOUNT,
                   "accounts": [ACCOUNT]}
    run = store.get_run(RUN)
    assert run["status"] == "RUNNING" and run["accounts"][0]["outcome"] == "scanning"


def test_failed_unit_is_listed_as_unavailable():
    store, _, _ = make()
    _pipeline(store, Session(_active()), fail_units=(0,))
    raw = store.get_json(f"runs/{RUN}/raw.json")
    assert any("not scanned" in u[0] for u in raw["run"]["unavailable"])
    assert raw["buckets"] == []


def test_collect_uses_the_stored_overview():
    store, _, _ = make()
    s = Session(_active())
    run = steps.accounts(store, s, RUN, "t0")
    steps.regions(store, s, run, ACCOUNT)
    key = f"runs/{RUN}/accounts/{ACCOUNT}/overview.json"
    ov = store.get_json(key)
    ov["regions"] = ["eu-west-1", "ap-south-1"]          # a recomputed overview would differ; the stored one rules
    ov["buckets"] = []
    store.put_json(key, ov)
    with pytest.raises(KeyError):                          # unit 1 now exists in the stored plan (no stub for it)
        steps.collect(store, s, run, ACCOUNT, 1)


def test_ai_roles_survive_json_and_narrative_runs():
    store, _, _ = make()
    bedrock = FakeBedrock()
    out = _pipeline(store, Session(_active(), bedrock), model="m")
    assert out["classify"]["classification"] is True
    assert out["narrative"]["narrative"] == "complete"
    run = store.get_run(RUN)
    assert run["ai"] == {"classification": True, "narrative": "complete"}


def test_unavailable_bedrock_is_reported_not_fatal():
    store, _, _ = make()
    out = _pipeline(store, Session(_active(), FakeBedrock(fail="AccessDeniedException")), model="m")
    assert out["classify"]["narrative"] == "unavailable"
    data = store.get_json(f"runs/{RUN}/data.json")
    assert data["meta_ai"]["narrative"] == "unavailable"


def test_every_step_result_is_small():
    store, _, _ = make()
    out = _pipeline(store, Session(_active(), FakeBedrock()), model="m")
    for name, v in out.items():
        assert len(json.dumps(v)) < 16 * 1024, name


def test_fail_marks_the_run():
    store, _, _ = make()
    steps.fail(store, RUN, "boom", "t9")
    assert store.get_run(RUN) == {"run_id": RUN, "status": "FAILED", "error": "boom", "finished_at": "t9"}


class RoleBedrock(FakeBedrock):
    """Answers the role tool with a role for every bucket named in the prompt, the narrative tool with no texts."""
    def converse(self, **kw):
        if "toolConfig" in kw and kw["toolConfig"]["tools"][0]["toolSpec"]["name"] == "record_roles":
            self.calls.append(kw)
            names = [x["name"] for x in json.loads(kw["messages"][0]["content"][0]["text"].split("\n", 1)[1])]
            return {"output": {"message": {"content": [{"toolUse": {"name": "record_roles", "input": {"buckets": [
                {"name": n, "role": "production", "reason": "orders data", "confidence": "high"} for n in names]}}}]}}}
        return super().converse(**kw)


def test_ai_path_matches_the_cli_with_roles_round_tripped():
    store, _, _ = make()
    _pipeline(store, Session(_active(), RoleBedrock()), model="m")
    expected = _cli_data(Session(_active(), RoleBedrock()), model="m")
    got = store.get_json(f"runs/{RUN}/data.json")
    for d in (got, expected):
        d.get("meta", {}).pop("scan_date", None)
        d.get("meta", {}).pop("generated", None)
    assert got == _strip(expected)
    assert got["meta"]["ai"] == {"classification": True, "narrative": "complete"} and got["meta_ai"]
    assert got["tabs"][0] == _strip(expected)["tabs"][0]
    roles = store.get_json(f"runs/{RUN}/ai-roles.json")["roles"]
    assert all(isinstance(v, list) and len(v) == 3 for v in roles.values())     # stored as lists, read back as tuples


def test_narrative_is_idempotent_and_never_stacks_text():
    store, _, _ = make()
    _pipeline(store, Session(_active(), RoleBedrock()), model="m")
    first = store.get_json(f"runs/{RUN}/data.json")
    again = steps.narrative(store, Session(_active(), FakeBedrock(fail="ThrottlingException")), {"run_id": RUN}, "m")
    assert again == {"narrative": "complete"} and store.get_json(f"runs/{RUN}/data.json") == first


def test_narrative_retry_restarts_from_the_rules_data():
    store, _, _ = make()
    s = Session(_active(), FakeBedrock(fail="AccessDeniedException"))
    run = steps.accounts(store, s, RUN, "t0")
    reg = steps.regions(store, s, run, ACCOUNT)
    steps.collect(store, s, run, ACCOUNT, 0)
    steps.assemble(store, run, [dict(reg, failed=[])])
    steps.classify(store, s, run, "m")
    steps.score(store, run)
    rules = store.get_json(f"runs/{RUN}/data-rules.json")
    store.put_json(f"runs/{RUN}/data.json", {**rules, "stale": "half-written AI text"})    # a crashed earlier attempt
    steps.narrative(store, s, run, "m")
    assert "stale" not in store.get_json(f"runs/{RUN}/data.json")


def test_probe_failure_is_recorded_as_unavailable_on_the_run():
    store, _, _ = make()
    _pipeline(store, Session(_active(), FakeBedrock(fail="AccessDeniedException")), model="m")
    assert store.get_run(RUN)["ai"] == {"classification": False, "narrative": "unavailable"}


def test_publish_twice_gives_the_same_data():
    store, _, _ = make()
    out = _pipeline(store, Session(_active()))
    first = store.get_json(f"runs/{RUN}/data.json")
    steps.publish(store, out["run"], "t2")
    assert store.get_json(f"runs/{RUN}/data.json") == first


# ---- organization scope

@pytest.fixture
def spokes(monkeypatch):
    """MEMBER's spoke role is assumable (its own fake estate), DENIED's is not."""
    member = _active(bucket="member-orders")
    seen = []

    def for_account(hub_session, account_id, hub, role_name):
        seen.append((account_id, hub, role_name))
        if hub:
            return hub_session, None
        return (member, None) if account_id == MEMBER else (None, "AccessDenied")
    monkeypatch.setattr(steps.sessions, "for_account", for_account)
    return seen


def _org_session():
    return Session(_active(), organizations=_org([
        _account(ACCOUNT, "management"), _account(DENIED, "member-b"), _account(MEMBER, "member-a"),
        _account("444455556666", "closed", state="SUSPENDED")]))


def test_organization_scan_covers_members_and_reports_the_unreachable_one(spokes):
    store, _, _ = make()
    out = _pipeline(store, _org_session(), scope="organization")
    assert out["run"]["accounts"] == [ACCOUNT, MEMBER, DENIED]       # hub first, active members by id
    assert ("222233334444", False, "S3DataProtectionScanRole") in spokes
    assert out["regions"][2] == {"account": DENIED, "units": 0, "buckets": 0, "denied": "AccessDenied", "failed": []}

    raw = store.get_json(f"runs/{RUN}/raw.json")
    assert list(raw["accounts"]) == [ACCOUNT, MEMBER, DENIED] and raw["organization"] is None
    assert {b["name"]: b["account"] for b in raw["buckets"]} == {"orders-prod": ACCOUNT, "member-orders": MEMBER}
    assert raw["accounts"][MEMBER]["name"] == "member-a"             # Organizations name; the hub keeps its alias
    assert raw["accounts"][ACCOUNT]["name"] == "acme"
    assert raw["accounts"][DENIED]["outcome"].startswith("not scanned: AssumeRole AccessDenied")
    assert raw["accounts"][DENIED]["account_bpa"] == "unknown"       # nothing read: unknown, never "off"
    assert raw["run"]["scope"] == "3 accounts (organization)"
    rows = [u[0] for u in raw["run"]["unavailable"]]
    assert f"member-b ({DENIED}): Account not scanned (no setting read)" in rows
    assert f"member-a ({MEMBER}): S3 spend per region (Cost Explorer)" in rows   # one row per account, not merged
    assert f"acme ({ACCOUNT}): S3 spend per region (Cost Explorer)" in rows

    run = store.get_run(RUN)
    assert run["status"] == "SUCCEEDED" and run["buckets"] == 2 and run["scope"] == "3 accounts (organization)"
    assert [a["outcome"].split(":")[0] for a in run["accounts"]] == ["scanned", "scanned", "not scanned"]
    data = store.get_json(f"runs/{RUN}/data.json")
    assert data["meta"]["account"] == ACCOUNT


def test_organization_scope_falls_back_to_the_hub_and_says_why(spokes):
    store, _, _ = make()
    s = Session(_active(), organizations=_org(error="AccessDeniedException"))
    out = _pipeline(store, s, scope="organization")
    assert out["run"]["accounts"] == [ACCOUNT] and spokes == [(ACCOUNT, True, "S3DataProtectionScanRole")] * 2
    raw = store.get_json(f"runs/{RUN}/raw.json")
    first = raw["run"]["unavailable"][0]
    assert first[0] == "Organization accounts (only this account was scanned)"
    assert "neither the management account nor a delegated administrator" in first[1]
    assert raw["run"]["scope"] == "1 account (acme)"


def test_an_explicit_account_list_replaces_the_organization_listing(spokes, monkeypatch):
    monkeypatch.setenv("SCAN_ACCOUNTS", f"{MEMBER},{ACCOUNT}")
    store, _, _ = make()
    out = steps.accounts(store, Session(_active()), RUN, "t0", "organization")   # no Organizations client needed
    assert out["accounts"] == [ACCOUNT, MEMBER]
    assert store.get_json(f"runs/{RUN}/scope.json")["source"] == "list"
    assert store.get_run(RUN)["scope"] == "2 accounts (account list)"


def test_a_failed_overview_step_is_reported_not_fatal(spokes):
    store, _, _ = make()
    s = _org_session()
    run = steps.accounts(store, s, RUN, "2026-11-25T10:15:00Z", "organization")
    hub = steps.regions(store, s, run, ACCOUNT)
    steps.collect(store, s, run, ACCOUNT, 0)
    steps.assemble(store, run, [dict(hub, failed=[]), None, None])     # both members' overview steps failed
    raw = store.get_json(f"runs/{RUN}/raw.json")
    assert raw["accounts"][MEMBER]["outcome"] == "not scanned: collection failed"
    assert len(raw["buckets"]) == 1
