"""Direct-invoke tests for api.py routing and validation.

The Cognito JWT 401 path is the authorizer's job, not unit-testable here. These
assert routing, 400 on bad JSON, and 400 with the offending field named for an
invalid /refresh scope (before the pipeline is invoked)."""
import json

import pytest

import api
import common
from tests.test_migrate import TRANSFORM_HAS_BRANCH_CASES


def _http_event(method, path, body=None, raw_body=None):
    return {
        "requestContext": {"http": {"method": method}},
        "rawPath": path,
        "body": raw_body if raw_body is not None else (json.dumps(body) if body is not None else None),
    }


def test_unknown_route_404():
    resp = api.handler(_http_event("DELETE", "/nope"))
    assert resp["statusCode"] == 404


def test_actions_bad_json_400():
    resp = api.handler(_http_event("POST", "/actions", raw_body="{not json"))
    assert resp["statusCode"] == 400
    assert "JSON" in json.loads(resp["body"])["error"]


def test_actions_unknown_action():
    resp = api.handler(_http_event("POST", "/actions", body={"action": "does_not_exist"}))
    assert resp["statusCode"] == 200
    assert "Unknown action" in json.loads(resp["body"])["error"]


def test_refresh_invalid_scope_returns_400_with_field(monkeypatch):
    # Must be rejected BEFORE any pipeline invoke; no AWS call should happen.
    monkeypatch.setattr(api, "PIPELINE_FUNCTION_ARN",
                        "arn:aws:lambda:us-east-1:111111111111:function:pipe:live")
    resp = api.handler(_http_event("POST", "/refresh",
                                   body={"scope": {"accounts": [{"id": "bad"}]}}))
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"].startswith("scope.accounts[0].id:")


def test_dispatch_rejects_non_dict():
    assert "error" in api.dispatch("not a dict")


def test_list_scans_route(monkeypatch):
    # GET /scans lists recent durable executions, newest first, with the
    # bounded summarize result carried through.
    monkeypatch.setattr(api, "PIPELINE_FUNCTION_ARN",
                        "arn:aws:lambda:us-east-1:111111111111:function:pipe:live")

    class _FakeLambda:
        def list_durable_executions_by_function(self, **_kwargs):
            return {"DurableExecutions": [
                {"DurableExecutionArn": "arn:...:a", "Status": "SUCCEEDED",
                 "StartTimestamp": _ts("2024-01-02T10:00:00+00:00"),
                 "EndTimestamp": _ts("2024-01-02T10:05:00+00:00"),
                 "Result": json.dumps({"discover": {"discovered": 3, "accounts_scanned": ["111"]},
                                       "assess": {"assessed": 2}})},
                {"DurableExecutionArn": "arn:...:b", "Status": "RUNNING",
                 "StartTimestamp": _ts("2024-01-03T09:00:00+00:00")},
            ]}

    monkeypatch.setattr(api, "_lambda_client", lambda: _FakeLambda())
    resp = api.handler(_http_event("GET", "/scans"))
    assert resp["statusCode"] == 200
    scans = json.loads(resp["body"])["scans"]
    assert [s["executionArn"] for s in scans] == ["arn:...:b", "arn:...:a"]  # newest first
    done = next(s for s in scans if s["executionArn"] == "arn:...:a")
    assert done["summary"]["discover"]["discovered"] == 3


def _ts(iso):
    from datetime import datetime
    return datetime.fromisoformat(iso)


# --- UI phases derived from the execution history --------------------------

class _HistoryClient:
    """Minimal stand-in for the Lambda client's get_durable_execution_history
    paginator, feeding a fixed list of StepStarted/StepSucceeded events."""
    def __init__(self, events):
        self._events = events

    def get_paginator(self, _name):
        events = self._events

        class _P:
            def paginate(self, **_kwargs):
                return [{"Events": events}]
        return _P()


def _step(event_type, name):
    return {"EventType": event_type, "Name": name}


def test_progress_phases_order_and_labels():
    # One account: start-run, one support unit, one discover cell, two assess,
    # summarize — all succeeded.
    events = [
        _step("StepStarted", "start-run"), _step("StepSucceeded", "start-run"),
        _step("StepStarted", "support-hub"), _step("StepSucceeded", "support-hub"),
        _step("StepStarted", "discover-hub-us-east-1"), _step("StepSucceeded", "discover-hub-us-east-1"),
        _step("StepStarted", "assess-hub-0"), _step("StepSucceeded", "assess-hub-0"),
        _step("StepStarted", "assess-hub-1"), _step("StepSucceeded", "assess-hub-1"),
        _step("StepStarted", "summarize"), _step("StepSucceeded", "summarize"),
    ]
    progress = api._progress_from_history(_HistoryClient(events), "arn:x", "SUCCEEDED")
    labels = [p["label"] for p in progress["phases"]]
    assert labels == [
        "Determine the accounts to scan",
        "Check each account's AWS Support tier",
        "Discover functions using deprecated runtimes and prioritize",
        "Assess migration complexity and risks",
        "Summarize findings and update the inventory",
    ]
    assert progress["support_done"] == 1
    assert progress["discover_done"] == 1
    assert progress["assess_done"] == 2


# --- rollup series read + rollup-aware filtering ---------------------------

def _inventory_with_rollups():
    """A table mix: two function rows and two rollup rows (out of time order)."""
    return [
        {"item_key": "fn#111#us-east-1#arn:a", "function_arn": "arn:a", "region": "us-east-1",
         "runtime": "python3.8", "alert_status": "Red", "priority_score": 10},
        {"item_key": "fn#111#us-east-1#arn:b", "function_arn": "arn:b", "region": "us-east-1",
         "runtime": "python3.8", "alert_status": "Yellow", "priority_score": 5},
        {"item_key": "rollup#2025-01-02T00:00:00Z", "rollup_at": "2025-01-02T00:00:00Z",
         "total_active": 1, "red": 1, "yellow": 0},
        {"item_key": "rollup#2025-01-01T00:00:00Z", "rollup_at": "2025-01-01T00:00:00Z",
         "total_active": 2, "red": 1, "yellow": 1},
    ]


def test_get_stats_returns_only_rollup_rows_ordered(monkeypatch):
    class _Table:
        def scan(self, **_kwargs):
            return {"Items": _inventory_with_rollups()}
    monkeypatch.setattr(api, "_table", lambda: _Table())

    resp = api.handler({"action": "get_stats"})
    rollups = resp["rollups"]
    # Only rollup# rows, ordered ascending by rollup_at.
    assert [r["item_key"] for r in rollups] == [
        "rollup#2025-01-01T00:00:00Z", "rollup#2025-01-02T00:00:00Z"]
    assert all(r["item_key"].startswith("rollup#") for r in rollups)


def test_list_functions_excludes_rollup_rows(monkeypatch):
    class _Table:
        def scan(self, **_kwargs):
            return {"Items": _inventory_with_rollups()}
    monkeypatch.setattr(api, "_table", lambda: _Table())

    resp = api.handler({"action": "list_functions"})
    keys = {f["item_key"] for f in resp["functions"]}
    assert keys == {"fn#111#us-east-1#arn:a", "fn#111#us-east-1#arn:b"}
    assert not any(k.startswith("rollup#") for k in keys)


def test_list_functions_resolves_hub_account_for_display(monkeypatch):
    """Hub rows store an EMPTY account_id (key/session placeholder). list_functions
    resolves the real hub id (via STS) for DISPLAY so the UI shows the account
    instead of '-' — without changing the stored item_key."""
    class _Table:
        def scan(self, **_kwargs):
            return {"Items": [
                {"item_key": "fn##us-east-1#arn:hub", "function_arn": "arn:hub",
                 "region": "us-east-1", "runtime": "python3.8", "account_id": "", "account_name": ""},
                {"item_key": "fn#222#us-east-1#arn:spoke", "function_arn": "arn:spoke",
                 "region": "us-east-1", "runtime": "python3.8", "account_id": "222", "account_name": "Spoke"},
            ]}
    monkeypatch.setattr(api, "_table", lambda: _Table())
    monkeypatch.setattr(api.discover, "_caller_account", lambda _region: "999999999999")

    resp = api.handler({"action": "list_functions"})
    by_arn = {f["function_arn"]: f for f in resp["functions"]}
    # Hub row now carries the real id + a friendly name, item_key unchanged.
    assert by_arn["arn:hub"]["account_id"] == "999999999999"
    assert "hub" in by_arn["arn:hub"]["account_name"].lower()
    assert by_arn["arn:hub"]["item_key"] == "fn##us-east-1#arn:hub"
    # Spoke row untouched.
    assert by_arn["arn:spoke"]["account_id"] == "222"
    assert by_arn["arn:spoke"]["account_name"] == "Spoke"


# --- Settings: non-secret whitelist, never a token value -------------------

class _SettingsTable:
    """A fake table recording the last put_item and serving a seeded item."""
    def __init__(self, item=None):
        self.item = item
        self.put_item_arg = None

    def put_item(self, Item=None):  # noqa: N803 - mirror boto3 kwarg name
        self.put_item_arg = Item

    def get_item(self, Key=None):  # noqa: N803 - mirror boto3 kwarg name
        return {"Item": self.item} if self.item is not None else {}


def test_put_settings_whitelists_and_strips_secret(monkeypatch):
    table = _SettingsTable()
    monkeypatch.setattr(api, "_table", lambda: table)

    resp = api.handler({"action": "put_settings", "settings": {
        "platform": "gitlab",
        "host": "https://gitlab.example.com",
        "org": "acme",
        "secretRef": "my-git-token-secret",
        "detectionMethod": "tag",
        # Not a settings field (removed): dropped like any unknown key.
        "repoTagKey": "source-repo",
        # These must NEVER be persisted:
        "token": "should-be-dropped",
        "tokenValue": "should-be-dropped",
        "unknown": "x",
    }})

    assert resp["saved"] is True
    written = table.put_item_arg
    assert written["item_key"] == api.SETTINGS_KEY
    # Only the whitelisted keys (plus item_key/updated_at) are written.
    assert written["platform"] == "gitlab"
    assert written["secretRef"] == "my-git-token-secret"
    assert "repoTagKey" not in written
    assert "updated_at" in written
    for forbidden in ("token", "tokenValue", "unknown"):
        assert forbidden not in written
        assert forbidden not in resp["settings"]


def test_get_settings_returns_stored_item(monkeypatch):
    stored = {"item_key": api.SETTINGS_KEY, "platform": "github", "org": "acme",
              "detectionMethod": "tag", "repoTag": "source-repo"}
    monkeypatch.setattr(api, "_table", lambda: _SettingsTable(item=stored))
    resp = api.handler({"action": "get_settings"})
    assert resp["settings"]["platform"] == "github"
    assert resp["settings"]["repoTag"] == "source-repo"


def test_get_settings_empty_when_absent(monkeypatch):
    monkeypatch.setattr(api, "_table", lambda: _SettingsTable(item=None))
    resp = api.handler({"action": "get_settings"})
    assert resp["settings"] == {}


def test_put_settings_persists_repo_and_branch_tags(monkeypatch):
    table = _SettingsTable()
    monkeypatch.setattr(api, "_table", lambda: table)

    resp = api.handler({"action": "put_settings", "settings": {
        "platform": "github",
        "org": "acme",
        "detectionMethod": "tag",
        "repoTag": "Git:Repository",
        "branchTag": "git:branch",
        # Not a settings field (removed): dropped like any unknown key.
        "repoTagKey": "source-repo",
        # Must NEVER be persisted:
        "token": "should-be-dropped",
    }})

    assert resp["saved"] is True
    written = table.put_item_arg
    assert written["repoTag"] == "Git:Repository"
    assert written["branchTag"] == "git:branch"
    assert "repoTagKey" not in written
    assert resp["settings"]["repoTag"] == "Git:Repository"
    assert resp["settings"]["branchTag"] == "git:branch"
    assert "token" not in written
    assert "token" not in resp["settings"]


def test_get_settings_does_not_backfill_repo_tag_from_removed_key(monkeypatch):
    # A stored attribute that is not a settings field is returned as-is and
    # never promoted to repoTag; the UI falls back to its default tag.
    stored = {"item_key": api.SETTINGS_KEY, "platform": "github", "org": "acme",
              "detectionMethod": "tag", "repoTagKey": "source-repo"}
    table = _SettingsTable(item=stored)
    monkeypatch.setattr(api, "_table", lambda: table)
    resp = api.handler({"action": "get_settings"})
    assert resp["settings"].get("repoTag") is None
    # The stored row is untouched (nothing written back).
    assert table.put_item_arg is None


def test_resolve_repo_from_tags_repo_only_prepends_org():
    # Short slug gets the org prefix.
    assert api.resolve_repo_from_tags(
        {"Git:Repository": "payments-service"}, "Git:Repository", None, org="my-org"
    ) == {"repository": "my-org/payments-service", "branch": None}
    # A value already containing "/" is NOT re-prefixed.
    assert api.resolve_repo_from_tags(
        {"Git:Repository": "acme/payments"}, "Git:Repository", None, org="my-org"
    ) == {"repository": "acme/payments", "branch": None}


def test_resolve_repo_from_tags_missing_and_branch():
    # Missing repo tag -> repository None.
    assert api.resolve_repo_from_tags(
        {"other": "x"}, "Git:Repository", "git:branch"
    ) == {"repository": None, "branch": None}
    # Repo + branch present -> both resolved.
    assert api.resolve_repo_from_tags(
        {"Git:Repository": "acme/payments", "git:branch": "release"},
        "Git:Repository", "git:branch"
    ) == {"repository": "acme/payments", "branch": "release"}
    # tags=None and a non-dict tags do not raise and resolve to all-None.
    assert api.resolve_repo_from_tags(None, "Git:Repository", "git:branch") == {
        "repository": None, "branch": None}
    assert api.resolve_repo_from_tags("not-a-dict", "Git:Repository", "git:branch") == {
        "repository": None, "branch": None}


def test_progress_discover_single_cell_shows_derived_total():
    """The Discover 'bare Running' bug fix: with a single discover cell still in
    flight, the phase derives total=1 from the checkpointed step count (not a
    None/unknown size), so the UI renders a sized progress bar instead of a bare
    'Running'."""
    events = [
        _step("StepStarted", "start-run"), _step("StepSucceeded", "start-run"),
        _step("StepStarted", "support-hub"), _step("StepSucceeded", "support-hub"),
        _step("StepStarted", "discover-hub-us-east-1"),  # running, not yet done
    ]
    progress = api._progress_from_history(_HistoryClient(events), "arn:x", "RUNNING")
    discover_phase = progress["phases"][2]
    assert discover_phase["label"] == "Discover functions using deprecated runtimes and prioritize"
    assert discover_phase["status"] == "in-progress"
    assert discover_phase["total"] == 1          # derived from the one checkpointed step
    assert discover_phase["done"] == 0
    assert discover_phase["running"] == ["hub-us-east-1"]


# --- AI code-behind detection playground (resolve_repos_demo) --------------
#
# Read-only action: picks up to 3 random inventory functions and asks Nova to
# resolve each one's source repository. Bedrock is mocked (no real AWS call);
# the inventory scan is monkeypatched the same way the other action tests do.

def _fn_rows(n):
    """n function inventory rows (fn# prefix), as _scan_inventory returns them."""
    return [
        {"item_key": f"fn#111#eu-west-3#arn:aws:lambda:eu-west-3:111:function:fn-{i}",
         "function_arn": f"arn:aws:lambda:eu-west-3:111:function:fn-{i}",
         "account_id": "111", "account_name": "Acct", "region": "eu-west-3",
         "runtime": "python3.9", "tags": {"service": f"svc-{i}"}}
        for i in range(n)
    ]


class _FakeBedrock:
    """A bedrock-runtime stand-in whose converse returns a canned toolUse block.

    When `raises` is True, converse raises so the per-function graceful fallback
    is exercised."""
    def __init__(self, raises=False):
        self.raises = raises
        self.calls = 0

    def converse(self, **_kwargs):
        self.calls += 1
        if self.raises:
            raise RuntimeError("Bedrock unavailable")
        return {"output": {"message": {"content": [
            {"toolUse": {"input": {
                "repository": "acme/orders-service",
                "confidence": 0.91,
                "evidence": ["Function name prefix matches the repo",
                             "service tag resolves to one repo in the org"],
            }}}
        ]}}}


def _patch_bedrock(monkeypatch, fake):
    """Route api.boto3.client('bedrock-runtime', ...) to `fake`; other clients
    are never built in this action, so return the fake for any name."""
    monkeypatch.setattr(api.boto3, "client", lambda *_a, **_k: fake)


def test_resolve_repos_demo_registered():
    assert "resolve_repos_demo" in api._ACTIONS


def test_resolve_repos_demo_normal_three_results(monkeypatch):
    monkeypatch.setattr(api, "_scan_inventory", lambda prefix="fn#": _fn_rows(5))
    monkeypatch.setattr(api, "_fill_hub_display", lambda rows: rows)
    fake = _FakeBedrock()
    _patch_bedrock(monkeypatch, fake)

    resp = api.handler({"action": "resolve_repos_demo", "prompt": "repos named after the service"})
    results = resp["results"]
    assert len(results) == 3
    assert fake.calls == 3  # one Bedrock call per picked function
    for r in results:
        assert r["repository"] == "acme/orders-service"
        assert r["confidence"] == 0.91
        assert isinstance(r["evidence"], list) and r["evidence"]
        # function_name derived from the arn (segment after :function:).
        assert r["function_name"] == r["function_arn"].split(":")[6]
        assert r["runtime"] == "python3.9"


def test_resolve_repos_demo_per_function_fallback_on_converse_error(monkeypatch):
    monkeypatch.setattr(api, "_scan_inventory", lambda prefix="fn#": _fn_rows(3))
    monkeypatch.setattr(api, "_fill_hub_display", lambda rows: rows)
    _patch_bedrock(monkeypatch, _FakeBedrock(raises=True))

    # Must NOT raise; every function degrades gracefully.
    resp = api.handler({"action": "resolve_repos_demo", "prompt": "x"})
    results = resp["results"]
    assert len(results) == 3
    for r in results:
        assert r["repository"] is None
        assert r["confidence"] == 0.0
        assert len(r["evidence"]) == 1


def test_resolve_repos_demo_fewer_than_three_rows(monkeypatch):
    monkeypatch.setattr(api, "_scan_inventory", lambda prefix="fn#": _fn_rows(2))
    monkeypatch.setattr(api, "_fill_hub_display", lambda rows: rows)
    _patch_bedrock(monkeypatch, _FakeBedrock())

    resp = api.handler({"action": "resolve_repos_demo", "prompt": "x"})
    assert len(resp["results"]) == 2


def test_resolve_repos_demo_zero_rows_returns_empty(monkeypatch):
    monkeypatch.setattr(api, "_scan_inventory", lambda prefix="fn#": [])
    monkeypatch.setattr(api, "_fill_hub_display", lambda rows: rows)
    _patch_bedrock(monkeypatch, _FakeBedrock())

    resp = api.handler({"action": "resolve_repos_demo", "prompt": "x"})
    assert resp == {"results": []}


# ---------------------------------------------------------------------------
# Migration surface (Phase 2): POST /migrate, GET /migrate/{arn+}, merge
# ---------------------------------------------------------------------------

_MIG_FN_ARN = "arn:aws:lambda:eu-west-3:111:function:fn-0"


def _fn_only(prefix="fn#"):
    return _fn_rows(1) if prefix == "fn#" else []


def test_start_migration_503_when_unset(monkeypatch):
    monkeypatch.setattr(api, "MIGRATE_FUNCTION_ARN", None)
    resp = api.handler(_http_event("POST", "/migrate", body={"function_arn": _MIG_FN_ARN}))
    assert resp["statusCode"] == 503
    assert "not enabled" in json.loads(resp["body"])["error"]


def test_start_migration_400_malformed_arn(monkeypatch):
    monkeypatch.setattr(api, "MIGRATE_FUNCTION_ARN",
                        "arn:aws:lambda:eu-west-3:111:function:mig:live")
    resp = api.handler(_http_event("POST", "/migrate", body={"function_arn": "not-an-arn"}))
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"].startswith("function_arn:")


def test_start_migration_404_unknown_function(monkeypatch):
    monkeypatch.setattr(api, "MIGRATE_FUNCTION_ARN",
                        "arn:aws:lambda:eu-west-3:111:function:mig:live")
    monkeypatch.setattr(api, "_scan_inventory", lambda prefix="fn#": [])
    resp = api.handler(_http_event("POST", "/migrate", body={"function_arn": _MIG_FN_ARN}))
    assert resp["statusCode"] == 404
    assert json.loads(resp["body"])["error"] == "unknown function"


def test_start_migration_202_invokes_orchestrator(monkeypatch):
    monkeypatch.setattr(api, "MIGRATE_FUNCTION_ARN",
                        "arn:aws:lambda:eu-west-3:111:function:mig:live")
    monkeypatch.setattr(api, "_scan_inventory", _fn_only)

    captured = {}

    class _FakeLambda:
        def invoke(self, **kwargs):
            captured.update(kwargs)
            return {"DurableExecutionArn": "arn:exec:1"}

    monkeypatch.setattr(api, "_lambda_client", lambda: _FakeLambda())
    resp = api.handler(_http_event("POST", "/migrate", body={"function_arn": _MIG_FN_ARN}))
    assert resp["statusCode"] == 202
    out = json.loads(resp["body"])
    assert out["executionArn"] == "arn:exec:1"
    assert out["migItemKey"] == f"mig#111#eu-west-3#{_MIG_FN_ARN}"
    assert captured["InvocationType"] == "Event"
    assert captured["DurableExecutionName"].startswith("migrate-fn-0-")
    # No pull-request flag travels to the orchestrator (the pushed branch is the end).
    payload = json.loads(captured["Payload"].decode())
    assert payload == {"function_arn": _MIG_FN_ARN}
    assert "open_pr" not in payload


def test_migration_status_not_started_when_no_mig_item(monkeypatch):
    monkeypatch.setattr(api, "_scan_inventory", _fn_only)

    class _Table:
        def get_item(self, Key):
            return {}

    monkeypatch.setattr(api, "_table", lambda: _Table())
    import urllib.parse
    encoded = urllib.parse.quote(_MIG_FN_ARN, safe="")
    resp = api.handler(_http_event("GET", f"/migrate/{encoded}"))
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["migration_status"] == "NOT_STARTED"


def test_migration_status_returns_mig_item(monkeypatch):
    monkeypatch.setattr(api, "_scan_inventory", _fn_only)

    class _Table:
        def get_item(self, Key):
            assert Key["item_key"] == f"mig#111#eu-west-3#{_MIG_FN_ARN}"
            return {"Item": {"item_key": Key["item_key"], "migration_status": "BRANCH_READY",
                             "migration_branch": "lambda-migration/python3.12-j"}}

    monkeypatch.setattr(api, "_table", lambda: _Table())
    import urllib.parse
    encoded = urllib.parse.quote(_MIG_FN_ARN, safe="")
    resp = api.handler(_http_event("GET", f"/migrate/{encoded}"))
    assert resp["statusCode"] == 200
    out = json.loads(resp["body"])
    assert out["migration_status"] == "BRANCH_READY"
    assert out["migration_branch"] == "lambda-migration/python3.12-j"


def test_list_functions_merges_migration_run_without_touching_fn_status(monkeypatch):
    fn_row = {**_fn_rows(1)[0], "migration_status": "ASSESSED"}  # Phase-1 axis
    mig_row = {"item_key": f"mig#111#eu-west-3#{_MIG_FN_ARN}",
               "function_arn": _MIG_FN_ARN, "migration_status": "BRANCH_READY",
               "migration_branch": "lambda-migration/python3.12-j",
               "migration_repo": "https://git-codecommit.eu-west-3.amazonaws.com/v1/repos/fixture",
               "migration_diff_summary": " a.py | 2 +-", "migration_build_id": "b1",
               "migration_target": "python3.12"}

    def _scan(prefix="fn#"):
        if prefix == "fn#":
            return [fn_row]
        if prefix == "mig#":
            return [mig_row]
        return [fn_row, mig_row]

    monkeypatch.setattr(api, "_scan_inventory", _scan)
    monkeypatch.setattr(api, "_fill_hub_display", lambda rows: rows)
    resp = api.handler({"action": "list_functions"})
    row = resp["functions"][0]
    # fn#'s own Phase-1 migration_status is untouched...
    assert row["migration_status"] == "ASSESSED"
    # ...and the mig# run is merged under migration_run_*.
    assert row["migration_run_status"] == "BRANCH_READY"
    assert row["migration_branch"] == "lambda-migration/python3.12-j"
    assert row["migration_repo"].endswith("/repos/fixture")
    assert row["migration_diff_summary"] == " a.py | 2 +-"
    assert row["migration_build_id"] == "b1"
    assert row["migration_target"] == "python3.12"   # the target the run used (drawer)


def test_list_functions_passes_unknown_status_through(monkeypatch):
    # An unknown mig# status is merged as-is; the frontend renders the raw value.
    fn_row = _fn_rows(1)[0]
    mig_row = {"item_key": f"mig#111#eu-west-3#{_MIG_FN_ARN}", "function_arn": _MIG_FN_ARN,
               "migration_status": "SOME_UNKNOWN_STATUS"}

    def _scan(prefix="fn#"):
        return [fn_row] if prefix == "fn#" else [mig_row]

    monkeypatch.setattr(api, "_scan_inventory", _scan)
    monkeypatch.setattr(api, "_fill_hub_display", lambda rows: rows)
    row = api.handler({"action": "list_functions"})["functions"][0]
    assert row["migration_run_status"] == "SOME_UNKNOWN_STATUS"


def test_list_functions_matches_mig_item_on_latest_qualified_arn(monkeypatch):
    # The mig# item carries the TA-style ':$LATEST' qualified ARN, the fn# row the
    # unqualified one: they are the same function (discover.index_migration_runs,
    # the matching rule shared with the rollup).
    fn_row = _fn_rows(1)[0]
    mig_row = {"item_key": f"mig#111#eu-west-3#{_MIG_FN_ARN}", "function_arn": _MIG_FN_ARN + ":$LATEST",
               "migration_status": "BRANCH_READY"}

    def _scan(prefix="fn#"):
        return [fn_row] if prefix == "fn#" else [mig_row]

    monkeypatch.setattr(api, "_scan_inventory", _scan)
    monkeypatch.setattr(api, "_fill_hub_display", lambda rows: rows)
    row = api.handler({"action": "list_functions"})["functions"][0]
    assert row["migration_run_status"] == "BRANCH_READY"


def test_list_functions_defaults_migration_run_not_started(monkeypatch):
    def _scan(prefix="fn#"):
        return _fn_rows(1) if prefix == "fn#" else []

    monkeypatch.setattr(api, "_scan_inventory", _scan)
    monkeypatch.setattr(api, "_fill_hub_display", lambda rows: rows)
    resp = api.handler({"action": "list_functions"})
    assert resp["functions"][0]["migration_run_status"] == "NOT_STARTED"


# --- GET /migrate-runs: recent batch executions (Steps card re-attach, Activity log)

_MIG_SUMMARY = {"batch_id": "b-1", "started_at": "t", "total_selected": 2, "over_cap_skipped": 0,
                "resolved": 1, "rejected": 1, "rejected_by_code": {"TAG_NOT_FOUND": 1},
                "branches_ready": 1, "no_changes": 0, "failed": 0}


class _FakeMigrateListLambda:
    """list_durable_executions_by_function over the migrate function; records the
    FunctionName / MaxItems it was called with; the SUCCEEDED item has no inline
    Result so the per-arn get_durable_execution fallback is exercised."""
    def __init__(self):
        self.calls = []
        self.get_calls = []

    def list_durable_executions_by_function(self, **kwargs):
        self.calls.append(kwargs)
        return {"DurableExecutions": [
            {"DurableExecutionArn": "arn:mig:old", "Status": "SUCCEEDED",
             "StartTimestamp": _ts("2024-01-02T10:00:00+00:00"),
             "EndTimestamp": _ts("2024-01-02T10:05:00+00:00")},
            {"DurableExecutionArn": "arn:mig:new", "Status": "RUNNING",
             "StartTimestamp": _ts("2024-01-03T09:00:00+00:00")},
        ]}

    def get_durable_execution(self, **kwargs):
        self.get_calls.append(kwargs)
        return {"Status": "SUCCEEDED", "Result": json.dumps(_MIG_SUMMARY)}


def test_list_migration_runs_empty_when_migration_not_deployed(monkeypatch):
    # A passive page-load fetch in a scan-only deployment: 200 + [], never 503.
    monkeypatch.setattr(api, "MIGRATE_FUNCTION_ARN", None)
    resp = api.handler(_http_event("GET", "/migrate-runs"))
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == {"runs": []}


def test_list_migration_runs_newest_first_with_summary(monkeypatch):
    monkeypatch.setattr(api, "MIGRATE_FUNCTION_ARN", _mig_env())
    fake = _FakeMigrateListLambda()
    monkeypatch.setattr(api, "_lambda_client", lambda: fake)
    resp = api.handler(_http_event("GET", "/migrate-runs"))
    assert resp["statusCode"] == 200
    runs = json.loads(resp["body"])["runs"]
    assert [r["executionArn"] for r in runs] == ["arn:mig:new", "arn:mig:old"]   # newest first
    # Listing spans every version: the UNQUALIFIED function ARN, default limit 10.
    assert fake.calls == [{"FunctionName": "arn:aws:lambda:eu-west-3:111:function:mig", "MaxItems": 10}]
    running, done = runs
    assert running["status"] == "RUNNING" and running["summary"] is None and running["finishedAt"] is None
    assert done["status"] == "SUCCEEDED"
    assert done["startedAt"] == "2024-01-02T10:00:00+00:00"
    assert done["finishedAt"] == "2024-01-02T10:05:00+00:00"
    assert done["summary"] == _MIG_SUMMARY                     # decoded from the Result
    assert fake.get_calls == [{"DurableExecutionArn": "arn:mig:old"}]   # only the SUCCEEDED one


def test_list_migration_runs_limit_clamped_and_validated(monkeypatch):
    monkeypatch.setattr(api, "MIGRATE_FUNCTION_ARN", _mig_env())
    fake = _FakeMigrateListLambda()
    monkeypatch.setattr(api, "_lambda_client", lambda: fake)
    event = _http_event("GET", "/migrate-runs")
    event["queryStringParameters"] = {"limit": "50"}
    assert api.handler(event)["statusCode"] == 200
    assert fake.calls[-1]["MaxItems"] == 10                    # clamped
    event["queryStringParameters"] = {"limit": "1"}
    runs = json.loads(api.handler(event)["body"])["runs"]
    assert fake.calls[-1]["MaxItems"] == 1 and len(runs) == 1
    event["queryStringParameters"] = {"limit": "abc"}
    resp = api.handler(event)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"]) == {"error": "limit: must be an integer"}


def test_list_migration_runs_direct_invocation(monkeypatch):
    monkeypatch.setattr(api, "MIGRATE_FUNCTION_ARN", _mig_env())
    monkeypatch.setattr(api, "_lambda_client", lambda: _FakeMigrateListLambda())
    out = api.handler({"action": "list_migration_runs", "limit": 2})
    assert [r["executionArn"] for r in out["runs"]] == ["arn:mig:new", "arn:mig:old"]
    # The per-function mig# listing is gone: batches are the Activity-log unit.
    assert "list_migrations" not in api._ACTIONS


# ---------------------------------------------------------------------------
# Migration BATCH surface: POST /migrate-batch, GET /migrate-run/{arn+}
# ---------------------------------------------------------------------------

def _mig_env():
    return "arn:aws:lambda:eu-west-3:111:function:mig:live"


def test_start_migration_batch_validation_non_list(monkeypatch):
    monkeypatch.setattr(api, "MIGRATE_FUNCTION_ARN", _mig_env())
    resp = api.handler(_http_event("POST", "/migrate-batch", body={"function_arns": "nope"}))
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"].startswith("function_arns:")


def test_start_migration_batch_validation_empty(monkeypatch):
    monkeypatch.setattr(api, "MIGRATE_FUNCTION_ARN", _mig_env())
    resp = api.handler(_http_event("POST", "/migrate-batch", body={"function_arns": []}))
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"].startswith("function_arns:")


def test_start_migration_batch_all_unknown_404(monkeypatch):
    monkeypatch.setattr(api, "MIGRATE_FUNCTION_ARN", _mig_env())
    monkeypatch.setattr(api, "_scan_inventory", lambda prefix="fn#": [])
    resp = api.handler(_http_event("POST", "/migrate-batch",
                                   body={"function_arns": [_MIG_FN_ARN]}))
    assert resp["statusCode"] == 404
    assert json.loads(resp["body"])["error"] == "unknown function"


def test_start_migration_batch_some_unknown_skipped_and_single_invoke(monkeypatch):
    monkeypatch.setattr(api, "MIGRATE_FUNCTION_ARN", _mig_env())
    monkeypatch.setattr(api, "_scan_inventory", _fn_only)  # only fn-0 is known

    captured = {}
    calls = {"n": 0}

    class _FakeLambda:
        def invoke(self, **kwargs):
            calls["n"] += 1
            captured.update(kwargs)
            return {"DurableExecutionArn": "arn:exec:batch"}

    monkeypatch.setattr(api, "_lambda_client", lambda: _FakeLambda())
    unknown = "arn:aws:lambda:eu-west-3:111:function:fn-404"
    resp = api.handler(_http_event("POST", "/migrate-batch",
                                   body={"function_arns": [_MIG_FN_ARN, unknown]}))
    assert resp["statusCode"] == 202
    out = json.loads(resp["body"])
    assert out["batchSize"] == 1
    assert out["skipped"] == [unknown]
    assert out["overCap"] == 0
    assert calls["n"] == 1                                  # ONE batch invoke
    assert captured["InvocationType"] == "Event"
    assert captured["DurableExecutionName"].startswith("migrate-batch-")
    payload = json.loads(captured["Payload"].decode())
    assert payload["function_arns"] == [_MIG_FN_ARN]
    assert "open_pr" not in payload


def test_start_migration_batch_ignores_unknown_request_key(monkeypatch):
    # A request key this build does not know is neither validated nor forwarded.
    monkeypatch.setattr(api, "MIGRATE_FUNCTION_ARN", _mig_env())
    monkeypatch.setattr(api, "_scan_inventory", _fn_only)
    captured = {}

    class _FakeLambda:
        def invoke(self, **kwargs):
            captured.update(kwargs)
            return {"DurableExecutionArn": "arn:exec:batch"}

    monkeypatch.setattr(api, "_lambda_client", lambda: _FakeLambda())
    resp = api.handler(_http_event("POST", "/migrate-batch",
                                   body={"function_arns": [_MIG_FN_ARN], "open_pr": True}))
    assert resp["statusCode"] == 202
    assert json.loads(captured["Payload"].decode()) == {"function_arns": [_MIG_FN_ARN]}


def _envelope(d):
    """Encode a plain dict into the SDK typed step-result envelope {"t":"m","v":{...}}."""
    def _enc(node):
        if isinstance(node, dict):
            return {"t": "m", "v": {k: _enc(v) for k, v in node.items()}}
        if isinstance(node, list):
            return {"t": "l", "v": [_enc(x) for x in node]}
        return {"t": "s", "v": node}
    return json.dumps(_enc(d))


def _resolve_step(event_type, name, result=None):
    """A Step* history event; a dict result is encoded as the SDK typed envelope."""
    ev = {"EventType": event_type, "Name": name}
    if result is not None:
        ev["StepSucceededDetails"] = {"Result": {"Payload": _envelope(result)}}
    return ev


def _context_string_payload(d):
    """ContextSucceeded payload of a wait_for_callback context with no serdes:
    the SDK json.dumps() the raw callback STRING, so the root is a JSON string
    whose content is itself JSON. Exactly ExtendedTypeSerDes.serialize(<str>)
    (pinned by test_context_string_payload_matches_sdk_serdes)."""
    return json.dumps(json.dumps(d))


def _ctx(event_type, name, result=None):
    """A Context* history event (map, map item, wait_for_callback); a dict
    result is encoded as the JSON-string payload of a transform-<i> context."""
    ev = {"EventType": event_type, "Name": name}
    if result is not None:
        ev["ContextSucceededDetails"] = {"Result": {"Payload": _context_string_payload(result)}}
    return ev


_LABELS = ("Prepare the migration batch", "Resolve source repositories",
           "Transform with AWS Transform")

_PREPARE_OK = [_step("StepStarted", "prepare-batch"),
               _resolve_step("StepSucceeded", "prepare-batch",
                             {"batch_id": "b-1", "accepted": ["arn:a", "arn:b"], "total_selected": 2})]
_TRANSFORMED = {"status": "TRANSFORMED", "branch": "upgrade/x", "failure_code": None,
                "diff_summary": "", "build_status": "SUCCEEDED"}


def _resolve_ok(i):
    return [_step("StepStarted", f"resolve-{i}"),
            _resolve_step("StepSucceeded", f"resolve-{i}", {"ok": True, "branch": "main"})]


def _resolve_rejected(i, code="TAG_NOT_FOUND"):
    return [_step("StepStarted", f"resolve-{i}"),
            _resolve_step("StepSucceeded", f"resolve-{i}", {"ok": False, "failure_code": code})]


def _transform_done(i, result=_TRANSFORMED):
    return [_ctx("ContextStarted", f"transform-{i}"),
            _ctx("ContextSucceeded", f"transform-{i}", result)]


def _phases(events, status="SUCCEEDED"):
    progress = api._migration_progress(_HistoryClient(events), "arn:x", status)
    assert [p["label"] for p in progress["phases"]] == list(_LABELS)
    assert len(progress["phases"]) == 3
    return progress["phases"], progress


def test_context_string_payload_matches_sdk_serdes():
    """The fixture encoder is pinned to the installed SDK: a str result is
    serialized as a bare JSON string (primitive fast path), never as an
    {"t":"s"} envelope, so the test cannot pass on a shape production never emits."""
    from aws_durable_execution_sdk_python.serdes import ExtendedTypeSerDes
    d = {"status": "TRANSFORMED", "branch": "b"}
    assert _context_string_payload(d) == ExtendedTypeSerDes().serialize(json.dumps(d))


def test_decode_context_result_shapes():
    assert api._decode_context_result(_context_string_payload({"status": "X"})) == {"status": "X"}
    assert api._decode_context_result(json.dumps("not json")) == {}          # string root, not JSON
    assert api._decode_context_result(json.dumps(json.dumps([1, 2]))) == {}  # JSON list inside
    assert api._decode_context_result(_envelope({"a": "b"})) == {"a": "b"}  # dict root: envelope
    assert api._decode_context_result("null") == {}
    assert api._decode_context_result("{garbage") == {}


def test_decode_step_result_returns_empty_on_string_root():
    """Why _decode_step_result is not used for the transform context: a JSON
    string root has no .get and the decoder degrades to {}."""
    assert api._decode_step_result(_context_string_payload({"status": "TRANSFORMED"})) == {}


def test_migration_progress_three_phases_from_history():
    """The happy path, history only: prepare ok, resolve-0 rejected, resolve-1
    ok, transform-1 TRANSFORMED with a branch (JSON-string payload). The flow
    ends there: no fourth phase."""
    events = _PREPARE_OK + _resolve_rejected(0) + _resolve_ok(1) + _transform_done(1)
    phases, progress = _phases(events)
    p0, p1, p2 = phases
    assert (p0["done"], p0["total"], p0["status"]) == (1, 1, "success")
    assert (p1["done"], p1["failed"], p1["total"], p1["status"]) == (1, 1, 2, "warning")
    assert (p2["done"], p2["failed"], p2["total"], p2["status"]) == (1, 0, 1, "success")
    assert p2["branchesReady"] == 1
    assert "note" not in p2 and "noChanges" not in p2
    assert progress["rejected_by_code"] == {"TAG_NOT_FOUND": 1}
    # The card header reads the batch size from the decoded prepare-batch result.
    assert progress["total_selected"] == 2
    assert progress["accepted"] == 2


def test_migration_progress_prepare_not_decoded_yet_sizes_are_none():
    phases, progress = _phases([_step("StepStarted", "prepare-batch")], "RUNNING")
    assert progress["total_selected"] is None and progress["accepted"] is None
    assert phases[0]["status"] == "in-progress"


def test_migration_progress_transformed_without_branch_is_phase2_failure():
    """A TRANSFORMED payload without a branch is a phase-2 failure (build_complete
    writes FAILED/TRANSFORM_FAILED, the orchestrator's settle is a guarded no-op)
    and is NOT a branch ready. The API fold uses the shared predicate
    (common.transform_has_branch): same table as the orchestrator's tests."""
    for result, expected in TRANSFORM_HAS_BRANCH_CASES:
        assert common.transform_has_branch(result) is expected, result
    events = (_PREPARE_OK + _resolve_ok(0) + _resolve_ok(1)
              + _transform_done(0, {"status": "TRANSFORMED", "branch": ""})
              + _transform_done(1))
    phases, _ = _phases(events)
    p2 = phases[2]
    assert (p2["done"], p2["failed"], p2["total"], p2["status"]) == (1, 1, 2, "warning")
    assert p2["branchesReady"] == 1


@pytest.mark.parametrize("events, status, p2_expect", [
    # transform-0 started only, execution FAILED: phase 2 settles.
    (_PREPARE_OK + _resolve_ok(0) + [_ctx("ContextStarted", "transform-0")], "FAILED",
     ("stopped", "Run ended before this phase completed")),
    # one transform done, one started -> warning.
    (_PREPARE_OK + _resolve_ok(0) + _resolve_ok(1) + _transform_done(0)
     + [_ctx("ContextStarted", "transform-1")], "FAILED",
     ("warning", "Run ended before this phase completed")),
    # one done, one failed, one started -> warning (mixed outcome, as _status).
    (_PREPARE_OK + _resolve_ok(0) + _resolve_ok(1) + _resolve_ok(2)
     + _transform_done(0) + [_ctx("ContextStarted", "transform-1"), _ctx("ContextFailed", "transform-1")]
     + [_ctx("ContextStarted", "transform-2")], "FAILED",
     ("warning", "Run ended before this phase completed")),
    # one failed, one started, none done -> error.
    (_PREPARE_OK + _resolve_ok(0) + _resolve_ok(1)
     + [_ctx("ContextStarted", "transform-0"), _ctx("ContextFailed", "transform-0")]
     + [_ctx("ContextStarted", "transform-1")], "FAILED",
     ("error", "Run ended before this phase completed")),
    # resolve done, nothing started in phase 2, STOPPED: phase 2 never started.
    (_PREPARE_OK + _resolve_ok(0), "STOPPED",
     ("stopped", "Run ended before this phase completed")),
])
def test_migration_progress_execution_failed_midway_settles_phases(events, status, p2_expect):
    """A finished execution receives no more events: no phase may stay pending
    or in-progress (the UI stops polling as soon as none is)."""
    phases, _ = _phases(events, status)
    assert all(p["status"] not in ("pending", "in-progress") for p in phases)
    p2 = phases[2]
    assert (p2["status"], p2.get("note")) == p2_expect


def test_migration_progress_no_changes_is_done_not_branch_ready():
    """NO_CHANGES is a phase-2 success (counted in noChanges, not in branchesReady)."""
    events = (_PREPARE_OK + _resolve_ok(0) + _resolve_ok(1)
              + _transform_done(0, {"status": "NO_CHANGES", "branch": "", "failure_code": None})
              + _transform_done(1))
    phases, _ = _phases(events)
    p2 = phases[2]
    assert (p2["done"], p2["failed"], p2["status"], p2["noChanges"]) == (2, 0, "success", 1)
    assert p2["branchesReady"] == 1


def test_migration_progress_transform_context_failed_is_failed():
    """ContextFailed on transform-<i> (callback timed out / submitter
    exhausted) is a phase-2 failure; a FAILED status in the payload too,
    whatever its failure_code (PUSH_FAILED included)."""
    events = (_PREPARE_OK + _resolve_ok(0) + _resolve_ok(1)
              + [_ctx("ContextStarted", "transform-0"), _ctx("ContextFailed", "transform-0")]
              + _transform_done(1, {"status": "FAILED", "branch": "upgrade/x", "failure_code": "PUSH_FAILED"}))
    phases, _ = _phases(events)
    p2 = phases[2]
    assert (p2["done"], p2["failed"], p2["total"], p2["status"]) == (0, 2, 2, "error")
    assert p2["branchesReady"] == 0


def test_migration_progress_tolerates_unknown_transform_status():
    # Unknown-value guard: a transform payload with a status this build does
    # not know counts as failed, never raises.
    events = _PREPARE_OK + _resolve_ok(0) + _transform_done(0, {"status": "SOME_UNKNOWN_STATUS", "branch": "b"})
    phases, _ = _phases(events)
    p2 = phases[2]
    assert (p2["done"], p2["failed"], p2["total"], p2["status"]) == (0, 1, 1, "error")


def test_migration_progress_ignores_submitter_and_callback_children():
    """The SDK children of transform-<i> (submitter step, create-callback-id
    callback), the settle-<i> step and the map contexts change no count."""
    base = _PREPARE_OK + _resolve_ok(0) + _transform_done(0)
    noise = [
        _ctx("ContextStarted", "migrate"), _ctx("ContextStarted", "map-item-0"),
        _step("StepStarted", "transform-0 submitter"),
        _resolve_step("StepSucceeded", "transform-0 submitter"),
        _step("StepFailed", "transform-0 submitter"),
        {"EventType": "CallbackStarted", "Name": "transform-0 create callback id"},
        {"EventType": "CallbackSucceeded", "Name": "transform-0 create callback id"},
        _step("StepStarted", "settle-0"), _resolve_step("StepSucceeded", "settle-0", {"ok": True}),
        _step("StepStarted", "transform-1 submitter"), _step("StepFailed", "transform-1 submitter"),
        _ctx("ContextSucceeded", "map-item-0", {"status": "FAILED"}),
        _ctx("ContextSucceeded", "migrate", {"status": "FAILED"}),
    ]
    clean, _ = _phases(base)
    noisy, _ = _phases(base + noise)
    assert noisy == clean
    assert [(p["done"], p["failed"], p["total"]) for p in clean] == [(1, 0, 1)] * 3


def test_migration_progress_rejected_by_code_from_history():
    """rejected_by_code comes from the decoded resolve-<i> results only (no mig# read)."""
    events = (_PREPARE_OK + _resolve_rejected(0, "TAG_NOT_FOUND") + _resolve_rejected(1, "BRANCH_NOT_FOUND")
              + _resolve_rejected(2, "TAG_NOT_FOUND") + _resolve_ok(3)
              + [_step("StepStarted", "resolve-4"), _step("StepFailed", "resolve-4")])
    phases, progress = _phases(events, "RUNNING")
    assert progress["rejected_by_code"] == {"TAG_NOT_FOUND": 2, "BRANCH_NOT_FOUND": 1}
    p1 = phases[1]
    assert (p1["done"], p1["failed"], p1["total"]) == (1, 4, 5)   # crashed resolve-4 is failed, not a rejection


def test_migration_progress_three_phases_split_and_settle():
    """Phase 1 splits rejected-but-succeeded resolve as FAILED (decoded result);
    phase 2 counts a transform-<i> ContextFailed as failed."""
    events = (_PREPARE_OK + _resolve_rejected(0) + _resolve_ok(1)
              + [_ctx("ContextStarted", "transform-1"), _ctx("ContextFailed", "transform-1")])
    phases, _ = _phases(events)
    p1, p2 = phases[1], phases[2]
    assert (p1["done"], p1["failed"], p1["total"]) == (1, 1, 2)
    assert (p2["done"], p2["failed"], p2["total"], p2["status"]) == (0, 1, 1, "error")
    assert p2["branchesReady"] == 0


def test_migration_progress_all_rejected_downstream_is_real_zero():
    """Regression: when EVERY function is rejected at Resolve, the downstream
    phase must report a REAL total of 0 (nothing to transform), not None. None
    means "size unknown" and made the UI render a bare label; 0 lets it say
    "Nothing to transform" (2 untagged functions -> Resolve 0 of 2, 2 rejected,
    and a real 0 for the next phase)."""
    events = _PREPARE_OK + _resolve_rejected(0) + _resolve_rejected(1)
    phases, progress = _phases(events)
    p1, p2 = phases[1], phases[2]
    assert (p1["done"], p1["failed"], p1["total"]) == (0, 2, 2)
    assert p2["total"] == 0, "zero survivors must be a real 0, not None"
    assert (p2["done"], p2["failed"]) == (0, 0)
    # Nothing to do is "skipped" (stopped icon), not a green success: the icon
    # must agree with the "Nothing to transform" message shown next to it.
    assert p2["status"] == "stopped"
    assert progress["rejected_by_code"] == {"TAG_NOT_FOUND": 2}


def test_migration_progress_rejected_vs_failed_split():
    """A pre-build business rejection (resolve {ok:False}) feeds rejected_by_code
    and never phase 2; a transform FAILED payload is phase 2's failure and never
    a rejection."""
    events = (_PREPARE_OK + _resolve_rejected(0) + _resolve_ok(1)
              + _transform_done(1, {"status": "FAILED", "branch": None, "failure_code": "TRANSFORM_FAILED"}))
    phases, progress = _phases(events)
    assert progress["rejected_by_code"] == {"TAG_NOT_FOUND": 1}
    p2 = phases[2]
    assert (p2["failed"], p2["total"], p2["done"], p2["status"]) == (1, 1, 0, "error")


def test_migrate_run_route_dispatches(monkeypatch):
    """GET /migrate-run/{arn+} returns {status, progress:{phases}}."""
    class _FakeLambda:
        def get_durable_execution(self, **_kwargs):
            return {"Status": "RUNNING", "StartTimestamp": _ts("2024-01-02T10:00:00+00:00")}

        def get_paginator(self, _name):
            class _P:
                def paginate(self, **_kwargs):
                    return [{"Events": []}]
            return _P()

    monkeypatch.setattr(api, "_lambda_client", lambda: _FakeLambda())
    resp = api.handler(_http_event("GET", "/migrate-run/arn:exec:batch"))
    assert resp["statusCode"] == 200
    out = json.loads(resp["body"])
    assert out["status"] == "RUNNING"
    assert [p["label"] for p in out["progress"]["phases"]] == list(_LABELS)
