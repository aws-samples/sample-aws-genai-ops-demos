import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from s3dp.service import api, labels
from tests.test_service_store import make

RUN = "scan-20261125-101500"
ARN = "arn:aws:lambda:eu-west-1:111122223333:function:s3-data-protection-pipeline"
EXE = ARN + ":live/durable-execution/" + RUN + "/abc"
SAMPLE = Path(__file__).resolve().parents[2] / "sample-data" / "anycompany-data.json"


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **kw):
        assert kw["IncludeExecutionData"] is False
        return iter(self.pages)


class FakeLambda:
    def __init__(self):
        self.running, self.invokes, self.status, self.events, self.history_error = [], [], "RUNNING", [], False

    def list_durable_executions_by_function(self, **kw):
        # The real service rejects more than one status per call and alias-qualified names.
        assert kw["FunctionName"] == ARN and len(kw["Statuses"]) == 1 and kw["MaxItems"] == 1
        self.listed = getattr(self, "listed", []) + kw["Statuses"]
        return {"DurableExecutions": self.running if kw["Statuses"] == ["RUNNING"] else []}

    def invoke(self, **kw):
        self.invokes.append(kw)
        return {"DurableExecutionArn": EXE}

    def get_durable_execution(self, DurableExecutionArn):
        return {"Status": self.status}

    def get_paginator(self, name):
        assert name == "get_durable_execution_history"
        if self.history_error:
            raise RuntimeError("boom")
        return FakePaginator([{"Events": self.events}])


class FakeS3Presign:
    def __init__(self, s3):
        self.s3, self.calls = s3, []

    def put_object(self, **kw):
        return self.s3.put_object(**kw)

    def head_object(self, **kw):
        return self.s3.head_object(**kw)

    def generate_presigned_url(self, op, Params, ExpiresIn):
        self.calls.append((op, Params, ExpiresIn))
        return "https://signed/" + Params["Key"]


@pytest.fixture
def env(monkeypatch):
    st, s3, table = make()
    lam = FakeLambda()
    st.s3 = FakeS3Presign(s3)
    monkeypatch.setenv("PIPELINE_FUNCTION_ARN", ARN + ":live")
    monkeypatch.setattr(api, "_store", lambda: st)
    monkeypatch.setattr(api, "_lambda", lambda: lam)
    monkeypatch.setattr(api, "_now", lambda: datetime(2026, 11, 25, 10, 15, 0, tzinfo=timezone.utc))
    return st, lam, s3


def call(route, id=None, fmt=None):
    ev = {"routeKey": route}
    if id is not None:
        ev["pathParameters"] = {"id": id}
    if fmt is not None:
        ev["queryStringParameters"] = {"format": fmt}
    r = api.handler(ev, None)
    assert r["headers"]["Cache-Control"] == "no-store"
    assert r["headers"]["Content-Type"] == "application/json"
    return r["statusCode"], json.loads(r["body"])


def ev(kind, name):
    return {"EventType": kind, "Name": name}


def test_start_writes_record(env):
    st, lam, _ = env
    code, body = call("POST /api/scans")
    assert (code, body) == (202, {"run_id": RUN, "status": "RUNNING"})
    inv = lam.invokes[0]
    assert inv["FunctionName"] == ARN + ":live" and inv["InvocationType"] == "Event"
    assert inv["DurableExecutionName"] == RUN and json.loads(inv["Payload"]) == {"run_id": RUN}
    rec = st.get_run(RUN)
    assert rec["execution_arn"] == EXE and rec["status"] == "RUNNING" and rec["started_at"].startswith("2026-11-25T10:15")
    assert lam.listed == ["RUNNING", "PAUSING", "PAUSED"]     # one status per call: the service rejects more


def test_start_conflict(env):
    _, lam, _ = env
    lam.running = [{"DurableExecutionName": RUN, "DurableExecutionArn": EXE}]
    code, body = call("POST /api/scans")
    assert code == 409 and body["run_id"] == RUN and body["error"] and not lam.invokes


def test_bad_and_unknown_ids(env):
    assert call("GET /api/scans/{id}", "nope")[0] == 400
    assert call("GET /api/scans/{id}", RUN)[0] == 404
    assert call("GET /api/scans/{id}/report", "nope", "json")[0] == 400


def test_unknown_route_and_internal_error(env, monkeypatch):
    assert call("GET /api/nothing")[0] == 404
    monkeypatch.setattr(api, "_store", lambda: (_ for _ in ()).throw(RuntimeError("secret")))
    code, body = call("GET /api/scans")
    assert (code, body) == (500, {"error": "internal error"})


def test_report(env):
    st, _, _ = env
    assert call("GET /api/scans/{id}/report", RUN, "json")[0] == 404
    st.update_run(RUN, status="SUCCEEDED")
    st.put_bytes(f"runs/{RUN}/data.json", b"{}", "application/json")
    st.put_bytes(f"runs/{RUN}/report.xlsx", b"x", "application/octet-stream")
    code, body = call("GET /api/scans/{id}/report", RUN, "json")
    assert code == 200 and body == {"url": f"https://signed/runs/{RUN}/data.json", "expires_in": 300}
    assert "ResponseContentDisposition" not in st.s3.calls[-1][1]
    assert call("GET /api/scans/{id}/report", RUN, "xlsx")[0] == 200
    p = st.s3.calls[-1][1]
    assert p["Key"] == f"runs/{RUN}/report.xlsx" and p["Bucket"] == "b"
    assert p["ResponseContentDisposition"] == f'attachment; filename="{RUN}-report.xlsx"'
    assert st.s3.calls[-1][2] == 300
    st.put_bytes(f"runs/{RUN}/report.html", b"x", "text/html")
    assert call("GET /api/scans/{id}/report", RUN, "html")[0] == 200
    assert st.s3.calls[-1][1]["ResponseContentDisposition"] == f'attachment; filename="{RUN}-report.html"'
    assert call("GET /api/scans/{id}/report", RUN, "pdf")[0] == 400
    assert call("GET /api/scans/{id}/report", RUN, None)[0] == 400


def test_list_reconciles_failed_execution(env):
    st, lam, _ = env
    st.update_run(RUN, status="RUNNING", execution_arn=EXE, started_at="2026-11-25T10:15:00+00:00")
    lam.status = "TIMED_OUT"
    code, body = call("GET /api/scans")
    assert code == 200 and body["runs"][0]["status"] == "FAILED" and body["runs"][0]["error"] == "TIMED_OUT"
    assert st.get_run(RUN)["status"] == "FAILED"


def detail(env, status, events, rec_status="RUNNING"):
    st, lam, _ = env
    st.update_run(RUN, status=rec_status, execution_arn=EXE)
    lam.status, lam.events = status, events
    code, body = call("GET /api/scans/{id}", RUN)
    assert code == 200
    assert [s["label"] for s in body["steps"]] == list(labels.STEPS)
    return body


def test_detail_running_at_collect(env):
    evs = [ev("StepStarted", "accounts"), ev("StepSucceeded", "accounts"), ev("StepStarted", "regions"),
           ev("StepSucceeded", "regions"), ev("StepStarted", "collect-0"), ev("StepSucceeded", "collect-0"),
           ev("StepStarted", "collect-1")]
    body = detail(env, "RUNNING", evs)
    assert [s["status"] for s in body["steps"]] == \
        ["success", "success", "in-progress", "pending", "pending", "pending", "pending"]
    assert body["steps"][2]["detail"] == "1 unit collected"
    assert body["run"]["run_id"] == RUN


def test_detail_succeeded(env):
    names = ["accounts", "regions", "collect-0", "assemble", "classify", "score", "narrative", "publish"]
    evs = [e for n in names for e in (ev("StepStarted", n), ev("StepSucceeded", n))]
    body = detail(env, "SUCCEEDED", evs, "SUCCEEDED")
    assert {s["status"] for s in body["steps"]} == {"success"}


def test_detail_failed_at_score(env):
    names = ["accounts", "regions", "collect-0", "assemble", "classify"]
    evs = [e for n in names for e in (ev("StepStarted", n), ev("StepSucceeded", n))]
    evs += [ev("StepStarted", "score"), ev("StepFailed", "score"), ev("StepStarted", "fail"),
            ev("StepSucceeded", "fail")]
    body = detail(env, "FAILED", evs, "FAILED")
    assert [s["status"] for s in body["steps"]] == \
        ["success", "success", "success", "success", "error", "pending", "pending"]


def test_detail_history_failure_falls_back_to_pending(env):
    _, lam, _ = env
    lam.history_error = True
    st = env[0]
    st.update_run(RUN, status="RUNNING", execution_arn=EXE)
    code, body = call("GET /api/scans/{id}", RUN)
    assert code == 200 and {s["status"] for s in body["steps"]} == {"pending"}


def test_sample(env):
    code, body = call("GET /api/sample")
    assert code == 200 and body == json.loads(SAMPLE.read_text())


def test_detail_partial_collect_failure_is_success(env):
    evs = [e for n in ("accounts", "regions") for e in (ev("StepStarted", n), ev("StepSucceeded", n))]
    evs += [ev("StepStarted", "collect-0"), ev("StepSucceeded", "collect-0"),
            ev("StepStarted", "collect-1"), ev("StepFailed", "collect-1"),
            ev("StepStarted", "assemble"), ev("StepSucceeded", "assemble"), ev("StepStarted", "classify")]
    body = detail(env, "RUNNING", evs)
    assert [s["status"] for s in body["steps"]][:4] == ["success", "success", "success", "in-progress"]
    assert body["steps"][2]["detail"] == "1 unit collected"


def test_detail_retry_sequence_is_success(env):
    names = ["accounts", "regions", "collect-0", "assemble", "classify", "score", "narrative"]
    evs = [e for n in names for e in (ev("StepStarted", n), ev("StepSucceeded", n))]
    evs += [ev("StepStarted", "publish"), ev("StepFailed", "publish"), ev("StepStarted", "publish"),
            ev("StepSucceeded", "publish")]
    body = detail(env, "SUCCEEDED", evs, "SUCCEEDED")
    assert {s["status"] for s in body["steps"]} == {"success"}


def test_trailing_newline_id_is_400(env):
    assert call("GET /api/scans/{id}", RUN + "\n")[0] == 400
