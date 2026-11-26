from types import SimpleNamespace

import pytest

from aws_durable_execution_sdk_python.exceptions import ExecutionError, StepError

from s3dp.service import labels, pipeline

RUN = "scan-20261125-101500"
HUB, MEMBER, DENIED = "111122223333", "222233334444", "333344445555"


class Item:
    def __init__(self, index, status, result=None):
        self.index, self.status, self.result = index, status, result


class FakeContext:
    def __init__(self, fail=()):
        """fail: step names whose map item fails (the SDK reports the item FAILED after its retries)."""
        self.names, self.fail = [], set(fail)

    def step(self, fn, name=None, config=None):
        self.names.append(name)
        try:
            return fn(None)
        except pipeline.UnrecoverableError:
            raise
        except Exception as e:      # like the SDK after retries: StepError carrying the inner type
            t = type(e)
            raise StepError(str(e), error_type=f"{t.__module__}.{t.__qualname__}") from e

    def map(self, items, fn, name=None, config=None):
        self.names.append(name)
        out = []
        for i, x in enumerate(items):
            child = FakeContext(self.fail)
            res = fn(child, x, i, items)
            self.names += child.names
            failed = any(n in self.fail for n in child.names)
            out.append(Item(i, pipeline.BatchItemStatus.FAILED) if failed
                       else Item(i, pipeline.BatchItemStatus.SUCCEEDED, res))
        return SimpleNamespace(all=out)


@pytest.fixture
def calls(monkeypatch):
    log = []
    monkeypatch.setenv("MODEL_ID", "")
    monkeypatch.setenv("MULTI_ACCOUNT", "true")
    monkeypatch.setattr(pipeline.store, "from_env", lambda: "STORE")
    monkeypatch.setattr(pipeline.boto3, "Session", lambda: "SESSION")
    state = {"ai": {"classification": True, "narrative": "pending"}, "accounts": [HUB], "units": 3,
             "denied": set(), "score_error": None}

    def accounts(s, ses, rid, at, scope):
        log.append(("accounts", rid, scope))
        return {"run_id": rid, "started_at": "t0", "scope": scope, "account_id": HUB, "accounts": state["accounts"]}

    def regions(s, ses, run, a):
        if a in state["denied"]:
            return {"account": a, "units": 0, "buckets": 0, "denied": "AccessDenied"}
        return {"account": a, "units": state["units"], "buckets": 5}

    monkeypatch.setattr(pipeline.steps, "accounts", accounts)
    monkeypatch.setattr(pipeline.steps, "regions", regions)
    monkeypatch.setattr(pipeline.steps, "collect",
                        lambda s, ses, run, a, n: log.append(("collect", a, n)) or {"n": n, "items": 1})
    monkeypatch.setattr(pipeline.steps, "assemble",
                        lambda s, run, per_account: log.append(("assemble", per_account)) or {})
    monkeypatch.setattr(pipeline.steps, "classify", lambda s, ses, run, model: state["ai"])

    def score(s, run):
        if state["score_error"]:
            raise state["score_error"]
        return {}
    monkeypatch.setattr(pipeline.steps, "score", score)
    monkeypatch.setattr(pipeline.steps, "narrative",
                        lambda s, ses, run, model: log.append(("narrative",)) or {"narrative": "complete"})
    monkeypatch.setattr(pipeline.steps, "publish", lambda s, run, at: {"report": True})
    monkeypatch.setattr(pipeline.steps, "fail", lambda s, rid, code, at: log.append(("fail", rid, code)))
    return log, state


def _assembled(log):
    return next(c[1] for c in log if c[0] == "assemble")


def test_step_order_and_labels(calls):
    log, _ = calls
    ctx = FakeContext()
    out = pipeline._run({"run_id": RUN}, ctx)
    assert out == {"report": True}
    assert ctx.names == ["accounts", "regions", f"regions-{HUB}", "collect", f"collect-{HUB}-0", f"collect-{HUB}-1",
                         f"collect-{HUB}-2", "assemble", "classify", "score", "narrative", "publish"]
    assert {labels.label_for(n) for n in ctx.names} == set(labels.STEPS)
    assert ("accounts", RUN, "account") in log                           # scope defaults to this account


def test_failed_units_go_to_assemble(calls):
    log, _ = calls
    pipeline._run({"run_id": RUN}, FakeContext(fail={f"collect-{HUB}-1"}))
    assert _assembled(log) == [{"account": HUB, "units": 3, "buckets": 5, "failed": [1]}]


def test_organization_maps_every_account_and_skips_denied_ones(calls):
    log, state = calls
    state.update(accounts=[HUB, MEMBER, DENIED], units=2, denied={DENIED})
    ctx = FakeContext(fail={f"regions-{MEMBER}"})        # the member's overview step fails after its retries
    pipeline._run({"run_id": RUN, "scope": "organization"}, ctx)
    assert [n for n in ctx.names if n.startswith("regions-")] == [f"regions-{a}" for a in (HUB, MEMBER, DENIED)]
    assert [c[1:] for c in log if c[0] == "collect"] == [(HUB, 0), (HUB, 1)]     # no work for failed or denied
    assert _assembled(log) == [
        {"account": HUB, "units": 2, "buckets": 5, "failed": []},
        None,
        {"account": DENIED, "units": 0, "buckets": 0, "denied": "AccessDenied", "failed": []}]


def test_organization_scope_needs_a_multi_account_deployment(calls, monkeypatch):
    monkeypatch.setenv("MULTI_ACCOUNT", "false")
    ctx = FakeContext()
    with pytest.raises(ValueError):
        pipeline._run({"run_id": RUN, "scope": "organization"}, ctx)
    with pytest.raises(ValueError):
        pipeline._run({"run_id": RUN, "scope": "everything"}, ctx)
    assert ctx.names == []


def test_bad_run_id_raises_before_any_step(calls):
    ctx = FakeContext()
    with pytest.raises(ValueError):
        pipeline._run({"run_id": "nope"}, ctx)
    assert ctx.names == []


def test_failure_recorded_then_reraised_without_message(calls):
    log, state = calls
    state["score_error"] = RuntimeError("secret detail")
    ctx = FakeContext()
    with pytest.raises(StepError):
        pipeline._run({"run_id": RUN}, ctx)
    assert ctx.names[-1] == "fail"
    assert ("fail", RUN, "RuntimeError") in log


def test_narrative_skipped_without_ai(calls):
    log, state = calls
    state["ai"] = {"classification": False, "narrative": "off"}
    ctx = FakeContext()
    pipeline._run({"run_id": RUN}, ctx)
    assert "narrative" in ctx.names and ("narrative",) not in log


def test_narrative_runs_with_ai(calls):
    log, _ = calls
    pipeline._run({"run_id": RUN}, FakeContext())
    assert ("narrative",) in log


def test_unrecoverable_error_is_not_recorded(calls):
    log, state = calls
    state["score_error"] = ExecutionError("x")
    ctx = FakeContext()
    with pytest.raises(ExecutionError):
        pipeline._run({"run_id": RUN}, ctx)
    assert "fail" not in ctx.names and not [c for c in log if c[0] == "fail"]


def test_trailing_newline_run_id_rejected(calls):
    with pytest.raises(ValueError):
        pipeline._run({"run_id": RUN + "\n"}, FakeContext())
