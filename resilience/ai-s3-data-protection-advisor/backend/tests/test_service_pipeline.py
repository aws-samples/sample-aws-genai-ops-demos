from types import SimpleNamespace

import pytest

from aws_durable_execution_sdk_python.exceptions import ExecutionError, StepError

from s3dp.service import labels, pipeline

RUN = "scan-20261125-101500"
RUN_REC = {"run_id": RUN, "started_at": "t0", "account_id": "111122223333", "account_name": "x"}


class Item:
    def __init__(self, index, status, result=None):
        self.index, self.status, self.result = index, status, result


class FakeContext:
    def __init__(self, fail_units=()):
        self.names, self.fail_units = [], set(fail_units)

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
        for i, n in enumerate(items):
            if i in self.fail_units:
                out.append(Item(i, pipeline.BatchItemStatus.FAILED))
            else:
                out.append(Item(i, pipeline.BatchItemStatus.SUCCEEDED, fn(self, n, i, items)))
        return SimpleNamespace(all=out)


@pytest.fixture
def calls(monkeypatch):
    log = []
    monkeypatch.setenv("MODEL_ID", "")
    monkeypatch.setattr(pipeline.store, "from_env", lambda: "STORE")
    monkeypatch.setattr(pipeline.boto3, "Session", lambda: "SESSION")
    state = {"ai": {"classification": True, "narrative": "pending"}, "units": 3, "score_error": None}

    def rec(name, ret=None):
        def f(*a, **k):
            log.append((name, a, k))
            return ret
        return f

    monkeypatch.setattr(pipeline.steps, "accounts", lambda s, ses, rid, at: log.append(("accounts", rid)) or RUN_REC)
    monkeypatch.setattr(pipeline.steps, "regions",
                        lambda s, ses, run: {"unit_count": state["units"], "buckets": 5, "regions": ["a"]})
    monkeypatch.setattr(pipeline.steps, "collect", lambda s, ses, run, n: {"n": n, "items": 1})
    monkeypatch.setattr(pipeline.steps, "assemble",
                        lambda s, run, count, failed: log.append(("assemble", count, list(failed))) or {})
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


def test_step_order_and_labels(calls):
    ctx = FakeContext()
    out = pipeline._run({"run_id": RUN}, ctx)
    assert out == {"report": True}
    assert ctx.names == ["accounts", "regions", "collect", "collect-0", "collect-1", "collect-2", "assemble", "classify", "score", "narrative", "publish"]
    assert {labels.label_for(n) for n in ctx.names} == set(labels.STEPS)


def test_failed_units_go_to_assemble(calls):
    log, _ = calls
    pipeline._run({"run_id": RUN}, FakeContext(fail_units=[1]))
    assert ("assemble", 3, [1]) in log


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
