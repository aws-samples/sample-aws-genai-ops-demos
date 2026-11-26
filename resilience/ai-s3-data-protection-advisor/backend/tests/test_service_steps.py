import json

import pytest

from s3dp import run as seq
from s3dp.collect import scan
from s3dp.service import steps
from tests.test_cli import FakeBedrock
from tests.test_scan import ACCOUNT, _session
from tests.test_service_store import make

RUN = "scan-20261125-101500"


class Sts:
    def get_caller_identity(self):
        return {"Account": ACCOUNT}


class Session:
    """The scan's fake session plus an sts client and (optionally) Amazon Bedrock."""
    def __init__(self, inner, bedrock=None):
        self.inner, self.bedrock, self.region_name = inner, bedrock, inner.region_name

    def client(self, name, region_name=None, config=None):
        if name == "sts":
            return Sts()
        if name == "bedrock-runtime":
            return self.bedrock
        return self.inner.client(name, region_name=region_name, config=config)

    def get_partition_for_region(self, r):
        return "aws"


def _active():
    s = _session()
    for _, st in s.stubs.values():
        st.activate()
    return s


def _pipeline(store, session, model="", fail_units=()):
    run = steps.accounts(store, session, RUN, "2026-11-25T10:15:00Z")
    reg = steps.regions(store, session, run)
    for n in range(reg["unit_count"]):
        if n not in fail_units:
            steps.collect(store, session, run, n)
    out = {"run": run, "regions": reg, "assemble": steps.assemble(store, run, reg["unit_count"], list(fail_units))}
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
    assert out["regions"] == {"unit_count": 1, "buckets": 1, "regions": ["eu-west-1"]}
    tab = next(t for t in got["tabs"] if t["id"] == "recommendations")
    for sev in ("HIGH", "MEDIUM", "LOW"):
        assert run["recs"][sev] == sum(i["severity"] == sev for i in tab["items"])
    assert sum(run["recs"].values()) > 0


def test_accounts_records_running_state():
    store, _, _ = make()
    out = steps.accounts(store, Session(_active()), RUN, "t0")
    assert out == {"run_id": RUN, "started_at": "t0", "account_id": ACCOUNT, "account_name": ACCOUNT}
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
    steps.regions(store, s, run)
    ov = store.get_json(f"runs/{RUN}/overview.json")
    ov["regions"] = ["eu-west-1", "ap-south-1"]          # a recomputed overview would differ; the stored one rules
    ov["buckets"] = []
    store.put_json(f"runs/{RUN}/overview.json", ov)
    with pytest.raises(KeyError):                          # unit 1 now exists in the stored plan (no stub for it)
        steps.collect(store, s, run, 1)


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
    steps.regions(store, s, run)
    steps.collect(store, s, run, 0)
    steps.assemble(store, run, 1, [])
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
