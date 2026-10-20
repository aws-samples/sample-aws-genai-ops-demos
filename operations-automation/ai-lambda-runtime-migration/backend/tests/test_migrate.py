"""Unit tests for migrate.py (Phase 2 migration orchestrator, design §Components C).

The durable runtime is not driven here (same reason as test_pipeline.py). A
@durable_step wraps a thunk that takes a StepContext and runs the body with the
original args prepended, so a step body is exercised by
`step_fn(*args)(fake_step_ctx)`. Pure helpers are tested directly.

A @durable_wait_for_callback submitter is exercised the same way:
`migrate.start_transform(resolved)("cb-1", fake_ctx)`. The batch body
(_run_batch) is driven by _FakeMapCtx, whose wait_for_callback runs the
submitter and returns a configured outcome (a raw string, or an exception).

No real AWS: DynamoDB (_table), S3, CodeBuild and CodeCommit are monkeypatched,
and the fn#/settings reads are stubbed through api.
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from aws_durable_execution_sdk_python.concurrency.models import BatchItemStatus
from aws_durable_execution_sdk_python.context import ExecutionContext
from aws_durable_execution_sdk_python.exceptions import (
    CallbackTimeoutError, CallbackSubmitterError,
    StepInterruptedError, NonDeterministicExecutionError, UnrecoverableError,
)

import common
import migrate
import transform_runtimes
from common import sanitize_execution_name, transform_has_branch
from migrate import (
    repository_url, render_additional_plan_context,
    _render_transform_config, prepare_batch, summarize_migration, derive_job_id,
    parse_build_result,
    MAX_CONTEXT, MAX_FUNCTIONS_PER_RUN,
    BUILD_TIMEOUT_MINUTES, QUEUE_TIMEOUT_MINUTES, CALLBACK_SLACK_MINUTES,
    TRANSFORM_CALLBACK_TIMEOUT, IN_PROGRESS_STALE_AFTER,
)


# --- step-body runner -------------------------------------------------------

def _run_step(step_fn, *args):
    """Execute a @durable_step body off the runtime (passes a fake StepContext)."""
    return step_fn(*args)(MagicMock())


# --- fakes ------------------------------------------------------------------

def _conditional_failed():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "no"}},
                       "UpdateItem")


class _FakeTable:
    """Per-key item storage: a get_item after an update_item sees the SET fields
    (needed for the callback-id-then-result sequence). `item` is a legacy default
    returned for any key without a stored item. `raise_conditional_for` injects a
    ConditionalCheckFailedException on guarded writes to those keys."""
    def __init__(self, item=None, items=None, raise_conditional_for=None):
        self.item = item
        self.items = dict(items or {})
        self.updates = []
        self.raise_conditional_for = set(raise_conditional_for or ())

    def get_item(self, Key):
        key = Key["item_key"]
        if key in self.items:
            return {"Item": self.items[key]}
        return {"Item": self.item} if self.item is not None else {}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        key = kwargs["Key"]["item_key"]
        if key in self.raise_conditional_for and kwargs.get("ConditionExpression"):
            raise _conditional_failed()
        names = kwargs["ExpressionAttributeNames"]
        values = kwargs["ExpressionAttributeValues"]
        # DynamoDB rule the real service enforces (seen live): every bound value
        # must be referenced by the update or condition expression.
        expressions = kwargs.get("UpdateExpression", "") + " " + kwargs.get("ConditionExpression", "")
        unused = [p for p in values if p not in expressions]
        if unused:
            raise ClientError({"Error": {"Code": "ValidationException",
                                         "Message": f"Value provided in ExpressionAttributeValues unused in expressions: keys: {unused}"}},
                              "UpdateItem")
        # #aN are SET names (paired with :vN), #rN are REMOVE names (no value).
        fields = {field: values[pn.replace("#a", ":v")] for pn, field in names.items() if pn.startswith("#a")}
        removed = [field for pn, field in names.items() if pn.startswith("#r")]
        base = self.items.get(key)
        if base is None:
            base = dict(self.item) if (self.item is not None and key.startswith("mig#")) else {}
        merged = {**base, "item_key": key, **fields}
        for field in removed:
            merged.pop(field, None)
        self.items[key] = merged
        return {}

    def status_of(self, key):
        return (self.items.get(key) or {}).get("migration_status")


def _patch_clients(monkeypatch, **clients):
    """Route migrate.boto3.client by service name (s3 / codebuild / codecommit).
    Unknown services get a fresh MagicMock."""
    def _client(service, *a, **k):
        if service not in clients:
            clients[service] = MagicMock()
        return clients[service]
    monkeypatch.setattr(migrate.boto3, "client", _client)
    return clients


def _codecommit_notfound(action):
    err = ClientError({"Error": {"Code": "RepositoryDoesNotExistException", "Message": "no"}}, action)
    return err


def _codecommit_branch_notfound():
    return ClientError({"Error": {"Code": "BranchDoesNotExistException", "Message": "no"}}, "GetBranch")


def _codecommit_accessdenied(action):
    return ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, action)


_PY_FN = {
    "item_key": "fn#111#eu-west-3#arn:aws:lambda:eu-west-3:111:function:lambda-migration-mock-python",
    "function_arn": "arn:aws:lambda:eu-west-3:111:function:lambda-migration-mock-python",
    "account_id": "111", "region": "eu-west-3", "runtime": "python3.9",
    "tags": {"source-repo": "lambda-migration-demo-fixture", "git-branch": "main"},
    "migration_risks": "Boto2 removal and async changes may break callers",
    "assessment_summary": "Upgrade from 3.9 to 3.12",
    "deprecated_apis": ["imp", "asyncio.coroutine"],
    "breaking_changes": ["collections.abc moves"],
    "dependency_issues": ["requests<2.20"],
}


def _patch_common(monkeypatch, fn_item=_PY_FN, settings=None, table=None):
    settings = settings if settings is not None else {}
    monkeypatch.setattr(migrate.api, "_scan_inventory",
                        lambda prefix="fn#": [fn_item] if fn_item is not None else [])
    monkeypatch.setattr(migrate.api, "get_settings", lambda _b: {"settings": settings})
    monkeypatch.setattr(migrate.api, "resolve_repo_from_tags", migrate.api.resolve_repo_from_tags)
    tbl = table if table is not None else _FakeTable()
    monkeypatch.setattr(migrate, "_table", lambda: tbl)
    monkeypatch.setattr(migrate, "get_region", lambda: "eu-west-3")
    return tbl


# --- sanitize_execution_name ------------------------------------------------

def test_sanitize_execution_name(monkeypatch):
    monkeypatch.setattr(common.time, "time", lambda: 1718900000)
    name = sanitize_execution_name(
        "arn:aws:lambda:eu-west-3:111:function:lambda-migration-mock-python")
    assert name == "migrate-lambda-migration-mock-python-1718900000"


def test_sanitize_execution_name_caps_and_sanitizes(monkeypatch):
    monkeypatch.setattr(common.time, "time", lambda: 1)
    name = sanitize_execution_name(
        "arn:aws:lambda:eu-west-3:111:function:My_Weird.Name$With*Chars" + "x" * 80)
    core = name[len("migrate-"):-len("-1")]
    assert len(core) <= 40
    assert all(c.isalnum() or c == "-" for c in core)


# --- repository_url ---------------------------------------------------------

def test_repository_url_eu_west_3():
    url = repository_url("lambda-migration-demo-fixture", "eu-west-3")
    assert url == ("https://git-codecommit.eu-west-3.amazonaws.com/v1/repos/"
                   "lambda-migration-demo-fixture")


def test_repository_url_defaults_region(monkeypatch):
    monkeypatch.setattr(migrate, "get_region", lambda: "us-east-1")
    assert "us-east-1" in repository_url("repo")


# --- render_additional_plan_context -----------------------------------------

_NODE_FN = {
    "function_arn": "arn:aws:lambda:eu-west-3:111:function:lambda-migration-mock-nodejs:$LATEST",
    "runtime": "nodejs18.x", "handler": "index.handler",
    "assessment_summary": "Upgrade to Node 22",
    "deprecated_apis": ["new Buffer()"], "breaking_changes": [], "dependency_issues": [],
    "migration_risks": "",
}

_DOTNET_FN = {
    "function_arn": "arn:aws:lambda:eu-west-3:111:function:orders-api",
    "runtime": "dotnet6", "handler": "Orders::Orders.Function::Handler",
    "assessment_summary": "Upgrade to .NET 10",
}

_DIRECTIVE_TAIL = (
    "Only change what this function needs: its code, its dependencies and its "
    "runtime setting in the infrastructure template. Leave other functions in "
    "the repository untouched."
)


def _paragraphs(ctx):
    return ctx.split("\n\n")


@pytest.mark.parametrize("item,family,target,head", [
    (_PY_FN, "python", "python3.13",
     'Upgrade the AWS Lambda function "lambda-migration-mock-python" '
     '(runtime python3.9) in this repository to python3.13.'),
    (_NODE_FN, "nodejs", "nodejs24.x",
     'Upgrade the AWS Lambda function "lambda-migration-mock-nodejs" '
     '(handler index.handler, runtime nodejs18.x) in this repository to nodejs24.x.'),
    (_DOTNET_FN, "dotnet", "dotnet10",
     'Upgrade the AWS Lambda function "orders-api" '
     '(handler Orders::Orders.Function::Handler, runtime dotnet6) in this repository to dotnet10.'),
])
def test_render_context_directive_paragraph_first_for_every_family(item, family, target, head):
    """Paragraph 1 is the scope directive: function name (last segment of the
    CANONICAL ARN, so :$LATEST is stripped), handler and runtime from the fn#
    row (parenthesis parts omitted when missing), the RESOLVED target."""
    ctx = render_additional_plan_context(item, family, target)
    first = _paragraphs(ctx)[0]
    assert first == f"{head} {_DIRECTIVE_TAIL}"
    assert "$LATEST" not in ctx


def test_render_context_python_has_lambda_conventions_prefix():
    ctx = render_additional_plan_context(_PY_FN, "python", "python3.13")
    paragraphs = _paragraphs(ctx)
    assert paragraphs[1] == transform_runtimes.PYTHON_CONTEXT_PREFIX
    # Advisory findings come last, under the header, one line per finding.
    assert paragraphs[2].startswith(migrate._ADVISORY_HEADER + "\n")
    assert "- Deprecated APIs: imp, asyncio.coroutine" in paragraphs[2]
    assert "- Breaking changes: collections.abc moves" in paragraphs[2]
    assert "- Dependency issues: requests<2.20" in paragraphs[2]
    assert len(paragraphs) == 3


@pytest.mark.parametrize("item,family,target", [
    (_NODE_FN, "nodejs", "nodejs24.x"),
    (_DOTNET_FN, "dotnet", "dotnet10"),
])
def test_render_context_no_lambda_explanation_for_lambda_specific_transforms(item, family, target):
    ctx = render_additional_plan_context(item, family, target)
    assert transform_runtimes.PYTHON_CONTEXT_PREFIX not in ctx
    assert "Preserve the Lambda handler" not in ctx
    assert "infrastructure template (the Runtime property" not in ctx


def test_render_context_migration_risks_is_scalar_not_char_iterated():
    ctx = render_additional_plan_context(_PY_FN, "python", "python3.13")
    # The risks string renders whole on one line, NOT char-by-char (which a
    # ", ".join() of a string would produce: "B, o, t, o, 2, ...").
    assert "- Migration risks: Boto2 removal and async changes may break callers" in ctx
    assert "B, o, t, o" not in ctx


def test_render_context_summary_never_rendered():
    for item, family, target in ((_PY_FN, "python", "python3.13"),
                                 (_NODE_FN, "nodejs", "nodejs24.x"),
                                 (_DOTNET_FN, "dotnet", "dotnet10")):
        ctx = render_additional_plan_context(item, family, target)
        assert item["assessment_summary"] not in ctx
        assert "Summary" not in ctx


def test_render_context_no_findings_no_advisory_paragraph():
    item = {**_DOTNET_FN, "deprecated_apis": [], "breaking_changes": [],
            "dependency_issues": [], "migration_risks": ""}
    ctx = render_additional_plan_context(item, "dotnet", "dotnet10")
    assert migrate._ADVISORY_HEADER not in ctx
    assert len(_paragraphs(ctx)) == 1            # the directive alone
    # A single finding is enough to bring the paragraph back.
    ctx = render_additional_plan_context({**item, "migration_risks": "none known"}, "dotnet", "dotnet10")
    assert _paragraphs(ctx)[1] == f"{migrate._ADVISORY_HEADER}\n- Migration risks: none known"


@pytest.mark.parametrize("risks", ["none", "None", " NONE "])
def test_render_context_literal_none_risks_is_no_finding(risks):
    """The assessment prompt asks for the literal 'none' when nothing is at risk:
    it must not reach AWS Transform as a '- Migration risks: none' advisory line."""
    item = {**_DOTNET_FN, "deprecated_apis": [], "breaking_changes": [],
            "dependency_issues": [], "migration_risks": risks}
    ctx = render_additional_plan_context(item, "dotnet", "dotnet10")
    assert migrate._ADVISORY_HEADER not in ctx
    assert "Migration risks" not in ctx
    assert len(_paragraphs(ctx)) == 1


def test_render_context_omits_empty_findings():
    ctx = render_additional_plan_context(_NODE_FN, "nodejs", "nodejs24.x")
    advisory = _paragraphs(ctx)[1]
    assert advisory == f"{migrate._ADVISORY_HEADER}\n- Deprecated APIs: new Buffer()"
    assert "Breaking changes" not in ctx
    assert "Dependency issues" not in ctx
    assert "Migration risks" not in ctx


def test_render_context_block_cap_applies_to_advisory_only():
    """One block-level cap on the advisory paragraph, cut at an item boundary
    with an ellipsis; the directive (and the Python prefix) are never cut."""
    # Three ~1.2k lines + a 500-char risks line: the cap (4000) falls inside the
    # Migration risks line.
    item = {**_PY_FN,
            "deprecated_apis": ["a" * 100 for _ in range(12)],
            "breaking_changes": ["b" * 100 for _ in range(12)],
            "dependency_issues": ["c" * 100 for _ in range(12)],
            "migration_risks": "d" * 500}
    ctx = render_additional_plan_context(item, "python", "python3.13")
    directive, prefix, advisory = _paragraphs(ctx)
    assert directive.endswith(_DIRECTIVE_TAIL)
    assert prefix == transform_runtimes.PYTHON_CONTEXT_PREFIX
    assert len(advisory) <= MAX_CONTEXT
    assert advisory.startswith(migrate._ADVISORY_HEADER + "\n- Deprecated APIs: ")
    # Cut on an item boundary: the ellipsis follows the complete Dependency
    # issues line, the half-fitting Migration risks line is dropped whole.
    assert advisory.endswith("c\u2026")
    assert "- Dependency issues" in advisory
    assert "- Migration risks" not in advisory
    # The whole string is longer than the cap: the cap is per-paragraph, not total.
    assert len(ctx) > MAX_CONTEXT


def test_render_context_long_single_finding_is_cut_mid_value():
    """No boundary available past the header: the value itself is cut."""
    item = {"migration_risks": "x" * 5000}
    ctx = render_additional_plan_context(item, "nodejs", "nodejs24.x")
    advisory = _paragraphs(ctx)[1]
    assert len(advisory) <= MAX_CONTEXT
    assert advisory.endswith("x\u2026")


# --- resolve_and_verify: failure-code selection -----------------------------

def _request():
    return {"function_arn": _PY_FN["function_arn"], "execution_arn": "arn:exec"}


def _written_status(table):
    """The migration_status set by the single update_item the failure path wrote."""
    assert table.updates, "expected an update_item write"
    names = table.updates[-1]["ExpressionAttributeNames"]
    values = table.updates[-1]["ExpressionAttributeValues"]
    for pn, field in names.items():
        if field == "migration_status":
            pv = pn.replace("#a", ":v")
            return values[pv]
    return None


def _written_failure_code(table):
    names = table.updates[-1]["ExpressionAttributeNames"]
    values = table.updates[-1]["ExpressionAttributeValues"]
    for pn, field in names.items():
        if field == "migration_failure_code":
            return values[pn.replace("#a", ":v")]
    return None


def test_resolve_tag_not_found(monkeypatch):
    fn = {**_PY_FN, "tags": {}}  # no source-repo tag
    tbl = _patch_common(monkeypatch, fn_item=fn)
    out = _run_step(migrate.resolve_and_verify, _request())
    assert out["ok"] is False
    assert out["failure_code"] == "TAG_NOT_FOUND"
    # A pre-build business rejection is REJECTED, never FAILED (FAILED is
    # reserved for transform failures and would inflate those counts).
    assert _written_status(tbl) == "REJECTED"


def test_resolve_runtime_unsupported(monkeypatch):
    fn = {**_PY_FN, "runtime": "go1.x"}
    tbl = _patch_common(monkeypatch, fn_item=fn)
    out = _run_step(migrate.resolve_and_verify, _request())
    assert out["failure_code"] == "RUNTIME_UNSUPPORTED"


def test_resolve_source_not_found(monkeypatch):
    tbl = _patch_common(monkeypatch)
    cc = MagicMock()
    cc.get_repository.side_effect = _codecommit_notfound("GetRepository")
    monkeypatch.setattr(migrate.boto3, "client", lambda *a, **k: cc)
    out = _run_step(migrate.resolve_and_verify, _request())
    assert out["failure_code"] == "SOURCE_NOT_FOUND"


def test_resolve_branch_not_found(monkeypatch):
    tbl = _patch_common(monkeypatch, settings={"branchTag": "git-branch"})
    cc = MagicMock()
    cc.get_repository.return_value = {}
    cc.get_branch.side_effect = _codecommit_branch_notfound()
    monkeypatch.setattr(migrate.boto3, "client", lambda *a, **k: cc)
    out = _run_step(migrate.resolve_and_verify, _request())
    assert out["failure_code"] == "BRANCH_NOT_FOUND"


def test_resolve_access_denied(monkeypatch):
    tbl = _patch_common(monkeypatch)
    cc = MagicMock()
    cc.get_repository.side_effect = _codecommit_accessdenied("GetRepository")
    monkeypatch.setattr(migrate.boto3, "client", lambda *a, **k: cc)
    out = _run_step(migrate.resolve_and_verify, _request())
    assert out["failure_code"] == "ACCESS_DENIED"


def test_resolve_success_writes_unguarded_queued(monkeypatch):
    tbl = _patch_common(monkeypatch, settings={"branchTag": "git-branch"})
    cc = MagicMock()
    cc.get_repository.return_value = {}
    cc.get_branch.return_value = {}
    monkeypatch.setattr(migrate.boto3, "client", lambda *a, **k: cc)
    out = _run_step(migrate.resolve_and_verify, _request())
    assert out["ok"] is True
    assert out["target"] == "python3.13"
    assert out["transform_name"] == "AWS/python-version-upgrade"
    assert out["repository_url"].endswith("/lambda-migration-demo-fixture")
    assert out["branch"] == "main"
    assert _written_status(tbl) == "QUEUED"
    # The QUEUED create is UNGUARDED (no ConditionExpression).
    assert "ConditionExpression" not in tbl.updates[-1]


def test_resolve_target_is_family_default(monkeypatch):
    """The managed transformation owns the target: a plain fn# row (runtime only,
    no assessed target) resolves to the family defaultTarget, which is persisted
    on the mig# item (the drawer shows it)."""
    tbl = _patch_common(monkeypatch, fn_item=_PY_FN)
    cc = MagicMock()
    cc.get_repository.return_value = {}
    cc.get_branch.return_value = {}
    monkeypatch.setattr(migrate.boto3, "client", lambda *a, **k: cc)
    out = _run_step(migrate.resolve_and_verify, _request())
    assert out["target"] == "python3.13"  # the family default
    assert tbl.items[f"mig#111#eu-west-3#{_PY_FN['function_arn']}"]["migration_target"] == "python3.13"


# --- pure helpers: job id, constants, build result, PR predicate -------------

_MIG_KEY = f"mig#111#eu-west-3#{_PY_FN['function_arn']}"
_BATCH_A = "aaaaaaaa-1111-2222-3333-444444444444"
_BATCH_B = "bbbbbbbb-1111-2222-3333-444444444444"


def test_derive_job_id_is_per_batch_and_deterministic():
    import re
    a = derive_job_id(_MIG_KEY, _BATCH_A)
    b = derive_job_id(_MIG_KEY, _BATCH_B)
    assert a != b
    assert derive_job_id(_MIG_KEY, _BATCH_A) == a            # replay-safe
    assert a.endswith("-aaaaaaaa")
    # Safe as a git ref component and an S3 key segment.
    assert re.fullmatch(r"[A-Za-z0-9-]+", a)
    assert not a.startswith("-")


def test_constants_are_derived():
    assert TRANSFORM_CALLBACK_TIMEOUT.to_seconds() == (
        QUEUE_TIMEOUT_MINUTES + BUILD_TIMEOUT_MINUTES + CALLBACK_SLACK_MINUTES) * 60
    # The staleness window must outlive the callback timeout, or a legitimately
    # waiting item could be re-queued.
    assert IN_PROGRESS_STALE_AFTER > timedelta(seconds=TRANSFORM_CALLBACK_TIMEOUT.to_seconds())
    assert IN_PROGRESS_STALE_AFTER > migrate.QUEUED_STALE_AFTER


@pytest.mark.parametrize("raw, detail", [
    (None, "empty build result"),
    (b'{"status": "TRANSFORMED"}', "empty build result"),
    ("not json", "unparseable build result"),
    ("[1, 2]", "unparseable build result"),
    (json.dumps({"status": "PR_OPENED"}), "unparseable build result"),
    (json.dumps({"nope": 1}), "unparseable build result"),
    (json.dumps({"status": "TRANSFORMED"}), "transformed without a branch"),
    (json.dumps({"status": "TRANSFORMED", "branch": ""}), "transformed without a branch"),
])
def test_parse_build_result_rejects_garbage(raw, detail):
    out = parse_build_result(raw)
    assert out["parse_failed"] is True
    assert out["ok"] is False
    assert out["status"] == "FAILED" and out["failure_code"] == "TRANSFORM_FAILED"
    assert out["detail"] == detail


def test_parse_build_result_well_formed():
    ok = parse_build_result(json.dumps({"status": "TRANSFORMED", "branch": "b", "diff_summary": "x" * 2000}))
    assert ok["ok"] is True and ok["parse_failed"] is False
    assert ok["branch"] == "b" and len(ok["diff_summary"]) == 1000
    failed = parse_build_result(json.dumps({"status": "FAILED", "failure_code": "TRANSFORM_FAILED"}))
    assert failed["ok"] is False and failed["parse_failed"] is False      # build_complete already settled it
    assert failed["failure_code"] == "TRANSFORM_FAILED"
    # A failed push is its own code and flows through untouched.
    pushed = parse_build_result(json.dumps({"status": "FAILED", "failure_code": "PUSH_FAILED"}))
    assert pushed["ok"] is False and pushed["parse_failed"] is False
    assert pushed["failure_code"] == "PUSH_FAILED"
    none = parse_build_result(json.dumps({"status": "NO_CHANGES"}))
    assert none["ok"] is True and none["status"] == "NO_CHANGES" and none["parse_failed"] is False


# Shared with test_api.py: the orchestrator (parse_build_result) and the API
# progress fold must agree on what counts as a pushed branch.
TRANSFORM_HAS_BRANCH_CASES = [
    ({"status": "TRANSFORMED", "branch": "b"}, True),
    ({"status": "TRANSFORMED"}, False),
    ({"status": "TRANSFORMED", "branch": ""}, False),
    ({"status": "NO_CHANGES", "branch": "b"}, False),
    ({"status": "FAILED"}, False),
    ({}, False),
]


@pytest.mark.parametrize("result, expected", TRANSFORM_HAS_BRANCH_CASES)
def test_transform_has_branch_predicate(result, expected):
    assert transform_has_branch(result) is expected


# --- start_transform submitter: config check, render, StartBuild, flip, index -

def _resolved(**extra):
    base = {
        "ok": True, "mig_key": _MIG_KEY, "function_arn": _PY_FN["function_arn"],
        "account_id": "111", "region": "eu-west-3", "family": "python", "runtime": "python3.9",
        "repository_url": "https://git-codecommit.eu-west-3.amazonaws.com/v1/repos/lambda-migration-demo-fixture",
        "branch": "", "target": "python3.12", "transform_name": "AWS/python-version-upgrade",
        "build_command": "", "validation_commands": "python -m pytest",
        "batch_id": _BATCH_A,
    }
    return {**base, **extra}


def _queued_table(**extra):
    """A table whose mig# item is QUEUED by batch A (what resolve_and_verify wrote)."""
    return _FakeTable(items={_MIG_KEY: {"item_key": _MIG_KEY, "migration_status": "QUEUED",
                                        "migration_batch_id": _BATCH_A, **extra}})


def _submitter_clients(monkeypatch, build_id="migrate-project:abc-123"):
    clients = _patch_clients(monkeypatch, s3=MagicMock(), codebuild=MagicMock())
    clients["codebuild"].start_build.return_value = {"build": {"id": build_id}}
    return clients


def _env_value(start_build_call, name):
    for var in start_build_call.kwargs["environmentVariablesOverride"]:
        if var["name"] == name:
            return var
    return None


def test_transform_submitter_persists_callback_id_then_result(monkeypatch):
    tbl = _queued_table()
    _patch_common(monkeypatch, table=tbl)
    monkeypatch.setattr(migrate, "PROJECT_NAME", "migrate-project")
    clients = _submitter_clients(monkeypatch)
    resolved = _resolved()

    out = migrate.start_transform(resolved)("cb-1", MagicMock())
    assert out is None                                      # the callback result is what matters

    job_id = derive_job_id(_MIG_KEY, _BATCH_A)
    # The config render went to the per-run prefix.
    put = clients["s3"].put_object.call_args
    assert put.kwargs["Key"] == f"migrations/{job_id}/transform-config.yaml"
    # StartBuild carries both bounds (the constants, never literals).
    sb = clients["codebuild"].start_build.call_args
    assert sb.kwargs["projectName"] == "migrate-project"
    assert sb.kwargs["timeoutInMinutesOverride"] == BUILD_TIMEOUT_MINUTES
    assert sb.kwargs["queuedTimeoutInMinutesOverride"] == QUEUE_TIMEOUT_MINUTES
    assert _env_value(sb, "JOB_ID")["value"] == job_id
    # The IN_PROGRESS flip is guarded on QUEUED and carries build/job/callback ids.
    flip = next(u for u in tbl.updates if u.get("ConditionExpression") == "migration_status = :queued")
    assert flip["ExpressionAttributeValues"][":queued"] == "QUEUED"
    assert _updated_field(flip, "migration_status") == "IN_PROGRESS"
    assert _updated_field(flip, "migration_callback_id") == "cb-1"
    assert _updated_field(flip, "migration_build_id") == "migrate-project:abc-123"
    assert _updated_field(flip, "migration_job_id") == job_id
    assert _updated_field(flip, "migration_updated_at")
    assert tbl.items[_MIG_KEY]["migration_callback_id"] == "cb-1"
    # The build-id index row was written (unguarded) with callback + batch ids.
    idx = next(u for u in tbl.updates if u["Key"]["item_key"] == "buildidx#migrate-project:abc-123")
    assert "ConditionExpression" not in idx
    assert _updated_field(idx, "mig_item_key") == _MIG_KEY
    assert _updated_field(idx, "job_id") == job_id
    assert _updated_field(idx, "callback_id") == "cb-1"
    assert _updated_field(idx, "batch_id") == _BATCH_A


def test_transform_submitter_passes_source_branch(monkeypatch):
    tbl = _queued_table()
    _patch_common(monkeypatch, table=tbl)
    monkeypatch.setattr(migrate, "PROJECT_NAME", "migrate-project")
    clients = _submitter_clients(monkeypatch)

    migrate.start_transform(_resolved(branch="release"))("cb-1", MagicMock())
    sb = clients["codebuild"].start_build.call_args
    assert _env_value(sb, "SOURCE_BRANCH") == {"name": "SOURCE_BRANCH", "value": "release", "type": "PLAINTEXT"}

    tbl2 = _queued_table()
    _patch_common(monkeypatch, table=tbl2)
    clients = _submitter_clients(monkeypatch)
    migrate.start_transform(_resolved())("cb-2", MagicMock())
    sb = clients["codebuild"].start_build.call_args
    assert _env_value(sb, "SOURCE_BRANCH")["value"] == ""
    # The six build inputs are there; the build takes no pull-request flag (the
    # pushed branch is the end of the flow).
    for name in ("REPOSITORY_URL", "TARGET_RUNTIME", "TRANSFORM_NAME", "TRANSFORM_FALLBACK_REGION",
                 "JOB_ID", "OUTPUT_BUCKET"):
        assert _env_value(sb, name) is not None, name
    assert _env_value(sb, "OPEN_PR") is None


def test_transform_submitter_checks_project_name_before_side_effects(monkeypatch):
    tbl = _queued_table()
    _patch_common(monkeypatch, table=tbl)
    monkeypatch.setattr(migrate, "PROJECT_NAME", None)
    clients = _submitter_clients(monkeypatch)

    with pytest.raises(RuntimeError):
        migrate.start_transform(_resolved())("cb-1", MagicMock())
    clients["s3"].put_object.assert_not_called()
    clients["codebuild"].start_build.assert_not_called()
    assert tbl.status_of(_MIG_KEY) == "FAILED"
    assert tbl.items[_MIG_KEY]["migration_failure_code"] == "ACCESS_DENIED"
    assert tbl.updates[-1]["ConditionExpression"] == "migration_status = :queued"

    # A retried attempt finds the item already FAILED: the conditional failure is
    # swallowed, the RuntimeError is still raised.
    tbl.raise_conditional_for.add(_MIG_KEY)
    with pytest.raises(RuntimeError):
        migrate.start_transform(_resolved())("cb-1", MagicMock())
    clients["s3"].put_object.assert_not_called()


def test_transform_submitter_retry_reuses_build_when_item_has_this_callback(monkeypatch):
    tbl = _queued_table(migration_status="IN_PROGRESS", migration_callback_id="cb-1",
                        migration_build_id="b-1")
    _patch_common(monkeypatch, table=tbl)
    monkeypatch.setattr(migrate, "PROJECT_NAME", "migrate-project")
    clients = _submitter_clients(monkeypatch)

    migrate.start_transform(_resolved())("cb-1", MagicMock())
    clients["codebuild"].start_build.assert_not_called()
    idx = next(u for u in tbl.updates if u["Key"]["item_key"] == "buildidx#b-1")
    assert _updated_field(idx, "callback_id") == "cb-1"
    assert tbl.items[_MIG_KEY]["migration_build_id"] == "b-1"


def test_transform_submitter_flip_conditional_failure_is_swallowed(monkeypatch):
    # Replay after a successful first attempt: the item is already IN_PROGRESS
    # with another build id (no callback match) -> StartBuild runs, the guarded
    # flip is rejected and swallowed, the index row is still written.
    tbl = _queued_table(migration_status="IN_PROGRESS")
    tbl.raise_conditional_for.add(_MIG_KEY)
    _patch_common(monkeypatch, table=tbl)
    monkeypatch.setattr(migrate, "PROJECT_NAME", "migrate-project")
    _submitter_clients(monkeypatch, build_id="b-2")
    migrate.start_transform(_resolved())("cb-9", MagicMock())
    assert any(u["Key"]["item_key"] == "buildidx#b-2" for u in tbl.updates)


# --- settle steps -----------------------------------------------------------

def test_settle_failed_writes_guarded_and_swallows_conditional(monkeypatch):
    tbl = _queued_table(migration_status="IN_PROGRESS")
    _patch_common(monkeypatch, table=tbl)
    out = _run_step(migrate.settle_failed, _resolved(), "d" * 600)
    assert out == {"settled": True}
    u = tbl.updates[-1]
    assert u["ConditionExpression"] == "migration_status IN (:queued, :inprogress)"
    assert u["ExpressionAttributeValues"][":queued"] == "QUEUED"
    assert u["ExpressionAttributeValues"][":inprogress"] == "IN_PROGRESS"
    assert _updated_field(u, "migration_status") == "FAILED"
    assert _updated_field(u, "migration_failure_code") == "TRANSFORM_FAILED"
    assert len(_updated_field(u, "migration_failure_detail")) == 500
    tbl.raise_conditional_for.add(_MIG_KEY)
    assert _run_step(migrate.settle_failed, _resolved(), "x") == {"settled": False}


def test_settle_timed_out_detail_names_the_window(monkeypatch):
    tbl = _queued_table(migration_status="IN_PROGRESS")
    _patch_common(monkeypatch, table=tbl)
    out = _run_step(migrate.settle_timed_out, _resolved())
    assert out["settled"] is True
    detail = _updated_field(tbl.updates[-1], "migration_failure_detail")
    assert str(int(TRANSFORM_CALLBACK_TIMEOUT.to_seconds()) // 60) in detail
    assert "minutes" in detail
    assert _updated_field(tbl.updates[-1], "migration_failure_code") == "TIMED_OUT"
    assert tbl.updates[-1]["ConditionExpression"] == "migration_status IN (:queued, :inprogress)"


# --- _render_transform_config: validationCommands (blog-backed quality gate) -

def test_render_config_includes_validation_commands():
    """When a validation command is provided, the YAML carries a
    validationCommands line (the field atx runs to validate the upgrade)."""
    yaml_text = _render_transform_config(
        "AWS/python-version-upgrade", "", "Target runtime: python3.12",
        "python -m pytest")
    assert 'transformationName: "AWS/python-version-upgrade"' in yaml_text
    assert 'validationCommands: "python -m pytest"' in yaml_text
    assert "additionalPlanContext: |" in yaml_text


def test_render_config_omits_empty_fields():
    """No buildCommand/validationCommands line is emitted when blank: atx rejects
    an empty configuration value (clientMetadata.configuration length >= 1), so
    empty fields must be omitted, not written as ""."""
    yaml_text = _render_transform_config(
        "AWS/dotnet-lambda-upgrade", "", "Target runtime: dotnet8", "")
    assert "validationCommands" not in yaml_text
    assert "buildCommand" not in yaml_text
    assert 'buildCommand: ""' not in yaml_text
    # The required fields are still present.
    assert 'transformationName: "AWS/dotnet-lambda-upgrade"' in yaml_text
    assert 'codeRepositoryPath: "."' in yaml_text


def test_transform_runtimes_carry_validation_commands():
    """Each family in the constants map carries its validationCommands (python
    pytest per the AWS blog; nodejs/dotnet their language test runners)."""
    assert transform_runtimes.TRANSFORMS["python"]["validationCommands"] == "python -m pytest"
    assert transform_runtimes.TRANSFORMS["nodejs"]["validationCommands"] == "npm test"
    assert transform_runtimes.TRANSFORMS["dotnet"]["validationCommands"] == "dotnet test"


# --- batch: prepare_batch cap split + migbatch# index row ------------------

def _updated_field(update, field):
    """The value SET for `field` by one field-scoped _set_fields update_item."""
    names = update["ExpressionAttributeNames"]
    values = update["ExpressionAttributeValues"]
    for pn, name in names.items():
        if name == field:
            return values[pn.replace("#a", ":v")]
    return None


def _update_for_key(table, key_prefix):
    for u in table.updates:
        if str(u["Key"]["item_key"]).startswith(key_prefix):
            return u
    return None


def test_prepare_batch_caps_and_reports_over_cap(monkeypatch):
    """Over-cap selections are REPORTED (returned + written to migbatch#), never
    dropped, and no mig# item is written for them (prepare_batch only indexes)."""
    tbl = _patch_common(monkeypatch)
    # discover.canonical_arn is identity for these synthetic arns.
    monkeypatch.setattr(migrate.discover, "canonical_arn", lambda a: a)
    arns = [f"arn:aws:lambda:eu-west-3:111:function:fn-{i}" for i in range(MAX_FUNCTIONS_PER_RUN + 3)]

    out = _run_step(prepare_batch, arns, "arn:exec:batch")

    assert len(out["accepted"]) == MAX_FUNCTIONS_PER_RUN
    assert out["over_cap_count"] == 3
    assert out["total_selected"] == MAX_FUNCTIONS_PER_RUN + 3
    assert out["batch_id"]

    migbatch = _update_for_key(tbl, "migbatch#")
    assert migbatch is not None
    assert len(_updated_field(migbatch, "migration_batch_arns")) == MAX_FUNCTIONS_PER_RUN
    assert len(_updated_field(migbatch, "migration_batch_over_cap")) == 3
    assert _updated_field(migbatch, "migration_batch_total") == MAX_FUNCTIONS_PER_RUN + 3
    assert _updated_field(migbatch, "migration_batch_execution_arn") == "arn:exec:batch"
    # No mig# item is written by prepare_batch (the map units write those).
    assert not any(str(u["Key"]["item_key"]).startswith("mig#") for u in tbl.updates)


def test_prepare_batch_carries_no_pull_request_flag(monkeypatch):
    tbl = _patch_common(monkeypatch)
    monkeypatch.setattr(migrate.discover, "canonical_arn", lambda a: a)
    out = _run_step(prepare_batch, ["arn:aws:lambda:eu-west-3:111:function:fn"], "arn:exec")
    assert set(out) == {"batch_id", "started_at", "accepted", "over_cap_count", "total_selected"}
    migbatch = _update_for_key(tbl, "migbatch#")
    assert "migration_batch_open_pr" not in migbatch["ExpressionAttributeNames"].values()


# --- resolve_and_verify stamps migration_batch_id on BOTH write sites -------

def _batch_request(batch_id):
    return {"function_arn": _PY_FN["function_arn"],
            "execution_arn": "arn:exec", "batch_id": batch_id}


def test_resolve_stamps_batch_id_on_success_write(monkeypatch):
    tbl = _patch_common(monkeypatch, settings={"branchTag": "git-branch"})
    cc = MagicMock()
    cc.get_repository.return_value = {}
    cc.get_branch.return_value = {}
    monkeypatch.setattr(migrate.boto3, "client", lambda *a, **k: cc)
    out = _run_step(migrate.resolve_and_verify, _batch_request("b-1"))
    assert out["ok"] is True
    assert out["batch_id"] == "b-1"           # the submitter derives the per-run job id from it
    assert _updated_field(tbl.updates[-1], "migration_status") == "QUEUED"
    assert _updated_field(tbl.updates[-1], "migration_batch_id") == "b-1"
    assert "migration_open_pr" not in tbl.updates[-1]["ExpressionAttributeNames"].values()


def test_resolve_queued_write_source_branch_and_clears_upgrade_branch(monkeypatch):
    """The QUEUED write records the SOURCE branch under migration_source_branch
    and REMOVEs migration_branch, so the upgrade branch is exclusively what
    build_complete writes with BRANCH_READY (a re-migrated function must not
    show the previous run's branch link while QUEUED)."""
    stale = _mig_item("BRANCH_READY", migration_branch="lambda-migration/python3.12-old")
    tbl = _FakeTable(item=stale)
    _patch_common(monkeypatch, settings={"branchTag": "git-branch"}, table=tbl)
    _patch_codecommit_ok(monkeypatch)
    out = _run_step(migrate.resolve_and_verify, _batch_request("b-2"))
    assert out["ok"] is True
    assert "open_pr" not in out
    update = tbl.updates[-1]
    assert _updated_field(update, "migration_source_branch") == "main"
    removed = [f for pn, f in update["ExpressionAttributeNames"].items() if pn.startswith("#r")]
    assert "migration_branch" in removed
    assert "migration_branch" not in tbl.items[out["mig_key"]]


def test_resolve_stamps_batch_id_on_fail_write(monkeypatch):
    fn = {**_PY_FN, "tags": {}}  # no source-repo tag -> TAG_NOT_FOUND via _fail
    tbl = _patch_common(monkeypatch, fn_item=fn)
    out = _run_step(migrate.resolve_and_verify, _batch_request("b-1"))
    assert out["failure_code"] == "TAG_NOT_FOUND"
    assert _updated_field(tbl.updates[-1], "migration_status") == "REJECTED"
    assert _updated_field(tbl.updates[-1], "migration_batch_id") == "b-1"


# --- resolve_and_verify: ALREADY_IN_PROGRESS duplicate-writer guard ---------

def _iso_ago(**delta):
    """An ISO-8601 UTC timestamp `delta` in the past (the shape migrate._now writes)."""
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def _mig_item(status, **extra):
    """A pre-seeded mig# item for the function _PY_FN describes, owned by batch
    b-1 and written just now unless the test overrides those fields."""
    return {"item_key": f"mig#111#eu-west-3#{_PY_FN['function_arn']}",
            "function_arn": _PY_FN["function_arn"],
            "migration_status": status,
            "migration_batch_id": "b-1",
            "migration_updated_at": _iso_ago(seconds=10), **extra}


def _patch_codecommit_ok(monkeypatch):
    cc = MagicMock()
    cc.get_repository.return_value = {}
    cc.get_branch.return_value = {}
    monkeypatch.setattr(migrate.boto3, "client", lambda *a, **k: cc)
    return cc


def test_resolve_blocks_when_mig_item_in_progress(monkeypatch):
    tbl = _FakeTable(item=_mig_item("IN_PROGRESS", migration_build_id="p:1"))
    _patch_common(monkeypatch, table=tbl)
    cc = _patch_codecommit_ok(monkeypatch)   # would succeed: proves the exit comes first
    out = _run_step(migrate.resolve_and_verify, _batch_request("b-2"))
    assert out["ok"] is False
    assert out["failure_code"] == "ALREADY_IN_PROGRESS"
    assert out["mig_key"].startswith("mig#111#eu-west-3#")
    assert tbl.updates == []                 # NO write of any kind on the live item
    cc.get_repository.assert_not_called()


def test_resolve_blocks_when_mig_item_queued(monkeypatch):
    # Fresh QUEUED without a build id: the owning batch is between resolve and
    # start_build right now.
    tbl = _FakeTable(item=_mig_item("QUEUED"))
    _patch_common(monkeypatch, table=tbl)
    _patch_codecommit_ok(monkeypatch)
    out = _run_step(migrate.resolve_and_verify, _batch_request("b-2"))
    assert out["failure_code"] == "ALREADY_IN_PROGRESS"
    assert tbl.updates == []


def test_resolve_blocks_before_fail_write_protects_live_item(monkeypatch):
    # Without the guard this function would be TAG_NOT_FOUND and _fail would
    # write FAILED over the live IN_PROGRESS item (the #116 clobber).
    fn = {**_PY_FN, "tags": {}}
    tbl = _FakeTable(item=_mig_item("IN_PROGRESS"))
    _patch_common(monkeypatch, fn_item=fn, table=tbl)
    out = _run_step(migrate.resolve_and_verify, _batch_request("b-2"))
    assert out["failure_code"] == "ALREADY_IN_PROGRESS"
    assert tbl.updates == []


def test_resolve_replay_of_own_batch_requeues(monkeypatch):
    # Durable replay: the step died after its own QUEUED write and before the
    # checkpoint. The retry sees an item stamped with ITS batch id and must
    # re-queue, not reject itself (that would leave the function locked out).
    tbl = _FakeTable(item=_mig_item("QUEUED", migration_batch_id="b-2"))
    _patch_common(monkeypatch, settings={"branchTag": "git-branch"}, table=tbl)
    _patch_codecommit_ok(monkeypatch)
    out = _run_step(migrate.resolve_and_verify, _batch_request("b-2"))
    assert out["ok"] is True
    assert _written_status(tbl) == "QUEUED"
    assert _updated_field(tbl.updates[-1], "migration_batch_id") == "b-2"


def test_resolve_requeues_stale_queued_without_build(monkeypatch):
    # Batch b-1 wrote QUEUED, then render/start exhausted retries: the item has
    # no build id and is older than QUEUED_STALE_AFTER. api._migration_progress
    # already settles it as 'not started'; the guard must agree and let b-2 in.
    stale = _mig_item("QUEUED", migration_updated_at=_iso_ago(minutes=31))
    tbl = _FakeTable(item=stale)
    _patch_common(monkeypatch, settings={"branchTag": "git-branch"}, table=tbl)
    _patch_codecommit_ok(monkeypatch)
    out = _run_step(migrate.resolve_and_verify, _batch_request("b-2"))
    assert out["ok"] is True
    assert _written_status(tbl) == "QUEUED"
    assert _updated_field(tbl.updates[-1], "migration_batch_id") == "b-2"


def test_resolve_blocks_old_in_progress(monkeypatch):
    # A fresh IN_PROGRESS item still blocks; one with no writer for longer than
    # IN_PROGRESS_STALE_AFTER (3 h here) is NOT live any more: a live status is
    # bounded in time (operator stop, durable ceiling, double fault).
    fresh = _FakeTable(item=_mig_item("IN_PROGRESS", migration_build_id="p:1",
                                      migration_updated_at=_iso_ago(minutes=5)))
    _patch_common(monkeypatch, table=fresh)
    out = _run_step(migrate.resolve_and_verify, _batch_request("b-2"))
    assert out["failure_code"] == "ALREADY_IN_PROGRESS"
    assert fresh.updates == []

    stale = _FakeTable(item=_mig_item("IN_PROGRESS", migration_build_id="p:1",
                                      migration_updated_at=_iso_ago(hours=3)))
    _patch_common(monkeypatch, settings={"branchTag": "git-branch"}, table=stale)
    _patch_codecommit_ok(monkeypatch)
    out = _run_step(migrate.resolve_and_verify, _batch_request("b-2"))
    assert out["ok"] is True
    assert _written_status(stale) == "QUEUED"


def test_resolve_requeues_stale_in_progress(monkeypatch):
    # Both sides of the IN_PROGRESS window, and the missing-timestamp rule.
    beyond = _mig_item("IN_PROGRESS", migration_build_id="p:1",
                       migration_updated_at=(datetime.now(timezone.utc) - IN_PROGRESS_STALE_AFTER
                                             - timedelta(minutes=1)).isoformat())
    assert migrate._is_live_migration(beyond, "b-2") is False
    tbl = _FakeTable(item=beyond)
    _patch_common(monkeypatch, settings={"branchTag": "git-branch"}, table=tbl)
    _patch_codecommit_ok(monkeypatch)
    out = _run_step(migrate.resolve_and_verify, _batch_request("b-2"))
    assert out["ok"] is True
    assert _written_status(tbl) == "QUEUED"
    assert _updated_field(tbl.updates[-1], "migration_batch_id") == "b-2"

    within = {**beyond, "migration_updated_at": (datetime.now(timezone.utc) - IN_PROGRESS_STALE_AFTER
                                                 + timedelta(minutes=1)).isoformat()}
    assert migrate._is_live_migration(within, "b-2") is True
    tbl = _FakeTable(item=within)
    _patch_common(monkeypatch, table=tbl)
    out = _run_step(migrate.resolve_and_verify, _batch_request("b-2"))
    assert out["failure_code"] == "ALREADY_IN_PROGRESS"
    assert tbl.updates == []

    # No provable timestamp on IN_PROGRESS: not live.
    assert migrate._is_live_migration(
        {"migration_status": "IN_PROGRESS", "migration_batch_id": "b-1", "migration_build_id": "p:1"},
        "b-2") is False
    assert migrate._is_live_migration(
        {"migration_status": "IN_PROGRESS", "migration_batch_id": "b-1",
         "migration_updated_at": "garbage"}, "b-2") is False


def test_is_live_migration_queued_without_timestamp_is_not_live():
    # Both create paths stamp migration_updated_at; a QUEUED item without one (or
    # with garbage) cannot be shown live and must not lock the function out.
    assert migrate._is_live_migration(
        {"migration_status": "QUEUED", "migration_batch_id": "b-1"}, "b-2") is False
    assert migrate._is_live_migration(
        {"migration_status": "QUEUED", "migration_batch_id": "b-1",
         "migration_updated_at": "not-a-date"}, "b-2") is False


def test_is_live_migration_boundary():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    item = {"migration_status": "QUEUED", "migration_batch_id": "b-1"}
    just_inside = {**item, "migration_updated_at": (now - timedelta(minutes=29)).isoformat()}
    just_outside = {**item, "migration_updated_at": (now - timedelta(minutes=31)).isoformat()}
    assert migrate._is_live_migration(just_inside, "b-2", now=now) is True
    assert migrate._is_live_migration(just_outside, "b-2", now=now) is False
    # A QUEUED item WITH a build id is not subject to the QUEUED window.
    assert migrate._is_live_migration({**just_outside, "migration_build_id": "p:1"}, "b-2", now=now) is True
    # IN_PROGRESS has its own (longer) window.
    in_progress = {**item, "migration_status": "IN_PROGRESS", "migration_build_id": "p:1"}
    inside = {**in_progress, "migration_updated_at": (now - IN_PROGRESS_STALE_AFTER + timedelta(minutes=1)).isoformat()}
    outside = {**in_progress, "migration_updated_at": (now - IN_PROGRESS_STALE_AFTER - timedelta(minutes=1)).isoformat()}
    assert migrate._is_live_migration(inside, "b-2", now=now) is True
    assert migrate._is_live_migration(outside, "b-2", now=now) is False
    # Own batch id: never live (replay).
    assert migrate._is_live_migration({**inside, "migration_batch_id": "b-2"}, "b-2", now=now) is False
    # Settled statuses are never live, whatever the stamps. The two legacy
    # values (REHEARSED / PR_OPENED) stay here to prove a legacy settled item is
    # re-migratable (D8).
    for status in ("BRANCH_READY", "REJECTED", "FAILED", "NO_CHANGES", "REHEARSED", "PR_OPENED"):
        assert migrate._is_live_migration({**item, "migration_status": status}, "b-2", now=now) is False


@pytest.mark.parametrize("status", ["BRANCH_READY", "REJECTED", "FAILED", "NO_CHANGES",
                                    "REHEARSED", "PR_OPENED"])   # the last two: legacy rows (D8)
def test_resolve_proceeds_when_mig_item_settled(monkeypatch, status):
    tbl = _FakeTable(item=_mig_item(status))
    _patch_common(monkeypatch, settings={"branchTag": "git-branch"}, table=tbl)
    _patch_codecommit_ok(monkeypatch)
    out = _run_step(migrate.resolve_and_verify, _batch_request("b-2"))
    assert out["ok"] is True
    assert _written_status(tbl) == "QUEUED"


def test_resolve_no_existing_item_still_queues(monkeypatch):
    tbl = _FakeTable()   # get_item -> {} (no mig# item yet)
    _patch_common(monkeypatch, settings={"branchTag": "git-branch"}, table=tbl)
    _patch_codecommit_ok(monkeypatch)
    out = _run_step(migrate.resolve_and_verify, _batch_request("b-2"))
    assert out["ok"] is True
    assert _written_status(tbl) == "QUEUED"
    assert "ConditionExpression" not in tbl.updates[-1]


def test_resolve_requeue_clears_previous_run_outcome(monkeypatch):
    """Regression (seen live): a function migrated again kept the previous run's
    failure code (NO_CHANGES item still saying TRANSFORM_FAILED). The QUEUED
    write must REMOVE every per-run outcome field; request fields are re-SET."""
    stale = _mig_item("FAILED", migration_failure_code="TRANSFORM_FAILED",
                      migration_failure_detail="old", migration_pr_url="https://old",
                      migration_build_id="p:old", migration_callback_id="cb-old",
                      migration_diff_summary="old diff")
    tbl = _FakeTable(item=stale)
    _patch_common(monkeypatch, settings={"branchTag": "git-branch"}, table=tbl)
    _patch_codecommit_ok(monkeypatch)
    out = _run_step(migrate.resolve_and_verify, _batch_request("b-2"))
    assert out["ok"] is True
    item = tbl.items[out["mig_key"]]
    assert item["migration_status"] == "QUEUED"
    for field in migrate._RUN_OUTCOME_FIELDS:
        assert field not in item, field
    assert "REMOVE" in tbl.updates[-1]["UpdateExpression"]


# --- summarize_migration: bounded fold --------------------------------------

class _Item:
    def __init__(self, index, status, result):
        self.index, self.status, self.result = index, status, result


class _Batch:
    def __init__(self, items):
        self.all = items


def _ok(index, result):
    return _Item(index, BatchItemStatus.SUCCEEDED, result)


def _failed(index):
    return _Item(index, BatchItemStatus.FAILED, None)


def _batch_dict(accepted, **extra):
    return {"batch_id": "b-1", "started_at": "t", "total_selected": len(accepted),
            "over_cap_count": 0, "accepted": accepted, **extra}


_SUMMARY_KEYS = {"batch_id", "started_at", "total_selected", "over_cap_skipped", "resolved",
                 "rejected", "rejected_by_code", "branches_ready", "no_changes", "failed"}


def test_summarize_migration_bounded_counts():
    batch = _batch_dict(["a", "b", "c"], total_selected=4, over_cap_count=1)
    mig_batch = _Batch([
        _ok(0, {"stage": "transform", "ok": True, "status": "BRANCH_READY", "branch": "b"}),
        _ok(1, {"stage": "resolve", "ok": False, "failure_code": "TAG_NOT_FOUND"}),  # rejected
        _failed(2),                                                                  # crashed -> UNKNOWN
    ])
    out = summarize_migration(batch, mig_batch)
    assert set(out) == _SUMMARY_KEYS
    assert out["batch_id"] == "b-1"
    assert out["total_selected"] == 4
    assert out["over_cap_skipped"] == 1
    assert out["resolved"] == 1
    assert out["branches_ready"] == 1
    assert out["rejected"] == 2
    assert out["rejected_by_code"] == {"TAG_NOT_FOUND": 1, "UNKNOWN": 1}
    assert out["failed"] == 0 and out["no_changes"] == 0
    for gone in ("pr_opened", "pr_failed", "open_pr", "transformed", "transform_failed"):
        assert gone not in out


def test_summarize_migration_new_keys():
    batch = _batch_dict(["a", "b", "c", "d", "e", "f", "g"])
    out = summarize_migration(batch, _Batch([
        _ok(0, {"stage": "transform", "ok": True, "status": "BRANCH_READY", "branch": "b0"}),
        _ok(1, {"stage": "transform", "ok": True, "status": "BRANCH_READY", "branch": "b1"}),
        _ok(2, {"stage": "transform", "ok": True, "status": "NO_CHANGES"}),
        _ok(3, {"stage": "transform", "ok": False, "status": "FAILED", "failure_code": "TIMED_OUT"}),
        _ok(4, {"stage": "transform", "ok": False, "status": "FAILED", "failure_code": "PUSH_FAILED"}),
        _ok(5, {"stage": "transform", "ok": False, "status": "FAILED", "failure_code": "TRANSFORM_FAILED"}),
        _ok(6, {"stage": "resolve", "ok": False, "failure_code": "BRANCH_NOT_FOUND"}),
    ]))
    assert out["resolved"] == 6
    assert out["rejected"] == 1
    assert out["branches_ready"] == 2
    assert out["no_changes"] == 1
    assert out["failed"] == 3
    # Invariants: everything accepted is either rejected or resolved, and every
    # resolved unit has exactly one outcome.
    assert out["rejected"] + out["resolved"] == 7
    assert out["resolved"] == out["branches_ready"] + out["no_changes"] + out["failed"]


def test_summarize_migration_tolerates_unknown_unit_status():
    # Legacy tolerance (D8): a unit result status this fold does not know (e.g.
    # "TRANSFORMED" from an in-flight execution of the previous orchestrator) is
    # counted as resolved only, never as a failure, and never raises.
    out = summarize_migration(_batch_dict(["a", "b"]), _Batch([
        _ok(0, {"stage": "transform", "ok": True, "status": "TRANSFORMED"}),
        _ok(1, {"stage": "transform", "ok": True, "status": "BRANCH_READY", "branch": "b"}),
    ]))
    assert out["resolved"] == 2
    assert out["branches_ready"] == 1
    assert out["failed"] == 0 and out["no_changes"] == 0


def test_rejection_is_rejected_not_failed_end_to_end(monkeypatch):
    """A TAG_NOT_FOUND rejection: the mig# item is REJECTED (not FAILED), the fold
    counts it under rejected/rejected_by_code and NOT under failures, and the
    settled REJECTED item is re-migratable (_is_live_migration False)."""
    fn = {**_PY_FN, "tags": {}}
    tbl = _patch_common(monkeypatch, fn_item=fn)
    out = _run_step(migrate.resolve_and_verify, _batch_request("b-9"))
    assert out["failure_code"] == "TAG_NOT_FOUND"
    assert _updated_field(tbl.updates[-1], "migration_status") == "REJECTED"
    assert _updated_field(tbl.updates[-1], "migration_failure_code") == "TAG_NOT_FOUND"
    assert "FAILED" not in [_updated_field(u, "migration_status") for u in tbl.updates]

    summary = summarize_migration(_batch_dict(["a"], batch_id="b-9"), _Batch([
        _ok(0, {"stage": "resolve", "ok": False, "failure_code": out["failure_code"]}),
    ]))
    assert summary["rejected"] == 1
    assert summary["rejected_by_code"] == {"TAG_NOT_FOUND": 1}
    assert summary["failed"] == 0
    assert summary["branches_ready"] == 0
    assert summary["resolved"] == 0

    written = {"migration_status": "REJECTED", "migration_batch_id": "b-9",
               "migration_updated_at": migrate._now()}
    assert migrate._is_live_migration(written, "b-10") is False


def test_summarize_migration_counts_already_in_progress():
    # The fold is generic over failure codes: the no-write rejection counts too.
    out = summarize_migration(_batch_dict(["a", "b"], batch_id="b-2"), _Batch([
        _ok(0, {"stage": "resolve", "ok": False, "failure_code": "ALREADY_IN_PROGRESS"}),
        _ok(1, {"stage": "transform", "ok": True, "status": "BRANCH_READY", "branch": "b"}),
    ]))
    assert out["rejected_by_code"] == {"ALREADY_IN_PROGRESS": 1}
    assert out["rejected"] == 1
    assert out["branches_ready"] == 1


# --- batch handler fan-out + per-item tolerance -----------------------------

class _FakeMapCtx:
    """A fake DurableContext driving the handler off the runtime, matching the
    real signatures: .step runs the step body (step_fn(*args)(StepContext)) and
    records the names in order (or raises a configured outcome, simulating a
    StepError after retries); .wait_for_callback mints one cb-<n> id per wait
    like the service, runs the submitter, and returns the configured outcome
    (a raw str, or raises the configured exception); .map applies the closure to
    each item and returns a _Batch of _Items (one failed item never raises,
    except UnrecoverableError subclasses, which the SDK lets escape)."""
    def __init__(self, callback_outcomes=None, step_outcomes=None):
        self.callback_outcomes = dict(callback_outcomes or {})
        self.step_outcomes = dict(step_outcomes or {})
        self.callbacks = []
        self.step_names = []
        self.logger = MagicMock()
        self.execution_context = ExecutionContext(durable_execution_arn="arn:exec:fake")
        self._n = 0

    def create_callback(self, name=None, config=None):
        cb_id = f"cb-{self._n}"
        self._n += 1
        return MagicMock(callback_id=cb_id)

    def wait_for_callback(self, submitter, name=None, config=None):
        cb_id = f"cb-{self._n}"
        self._n += 1
        self.callbacks.append((name, cb_id))
        submitter(cb_id, MagicMock())
        outcome = self.callback_outcomes.get(name)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def step(self, thunk, name=None, config=None):
        self.step_names.append(name)
        outcome = self.step_outcomes.get(name)
        if isinstance(outcome, Exception):
            raise outcome
        return thunk(MagicMock())

    def map(self, items, fn, name=None, config=None):
        results = []
        for i, item in enumerate(items):
            try:
                results.append(_ok(i, fn(self, item, i, items)))
            except UnrecoverableError:
                raise
            except Exception:
                results.append(_failed(i))
        return _Batch(results)


_FN_A = "arn:aws:lambda:eu-west-3:111:function:good-a"
_FN_B = "arn:aws:lambda:eu-west-3:111:function:bad-b"
_FN_C = "arn:aws:lambda:eu-west-3:111:function:good-c"


def _mig_key_for(arn):
    return f"mig#111#eu-west-3#{arn}"


def _batch_harness(monkeypatch, rejected=(), build_ids=None):
    """Drive _run_batch with the REAL start_transform submitter and settle steps
    against fakes. resolve_and_verify is replaced by a thunk that writes the
    QUEUED mig# item (so the guarded writes downstream are meaningful) and
    returns a full resolved dict. Returns (table, clients)."""
    monkeypatch.setattr(migrate.discover, "canonical_arn", lambda a: a)
    tbl = _FakeTable()
    monkeypatch.setattr(migrate, "_table", lambda: tbl)
    monkeypatch.setattr(migrate, "get_region", lambda: "eu-west-3")
    monkeypatch.setattr(migrate.api, "_scan_inventory", lambda prefix="fn#": [])
    monkeypatch.setattr(migrate, "PROJECT_NAME", "migrate-project")
    monkeypatch.setattr(migrate, "_transform_fallback_region", lambda: "eu-central-1")

    def _resolve(request):
        arn = request["function_arn"]
        if arn in rejected:
            return {"ok": False, "mig_key": _mig_key_for(arn), "failure_code": "TAG_NOT_FOUND"}
        mig_key = _mig_key_for(arn)
        assert "open_pr" not in request        # the request carries no pull-request flag any more
        migrate._set_fields(mig_key, {
            "migration_status": "QUEUED", "migration_batch_id": request["batch_id"],
            "migration_updated_at": migrate._now()})
        return _resolved(mig_key=mig_key, function_arn=arn, batch_id=request["batch_id"])

    monkeypatch.setattr(migrate, "resolve_and_verify", lambda request: (lambda sc: _resolve(request)))

    clients = _patch_clients(monkeypatch, s3=MagicMock(), codebuild=MagicMock(), codecommit=MagicMock())
    ids = iter(build_ids or (f"migrate-project:{i}" for i in range(100)))
    clients["codebuild"].start_build.side_effect = lambda **kw: {"build": {"id": next(ids)}}
    return tbl, clients


def _transformed_raw(branch="lambda-migration/python3.12-x"):
    return json.dumps({"status": "TRANSFORMED", "branch": branch, "diff_summary": "2 files"})


def _unit(ctx_results, index):
    """The per-unit result the fake map recorded for `index`."""
    return next(i.result for i in ctx_results.all if i.index == index)


def _run_capturing(ctx, event):
    """_run_batch, also returning the _Batch the map produced (per-unit results)."""
    captured = {}
    real_map = ctx.map

    def _map(items, fn, name=None, config=None):
        captured["batch"] = real_map(items, fn, name=name, config=config)
        return captured["batch"]

    ctx.map = _map
    summary = migrate._run_batch(event, ctx)
    return summary, captured["batch"]


def test_batch_handler_fans_out_and_tolerates_rejections(monkeypatch):
    """A 3-ARN batch (one good, one TAG_NOT_FOUND, one good) fans out to three
    units; the rejection does not fail the batch; the summary reflects the counts."""
    tbl, _ = _batch_harness(monkeypatch, rejected=(_FN_B,))
    ctx = _FakeMapCtx(callback_outcomes={"transform-0": _transformed_raw(), "transform-2": _transformed_raw()})
    event = {"function_arns": [_FN_A, _FN_B, _FN_C]}   # no execution_arn in the payload

    summary = migrate._run_batch(event, ctx)

    assert summary["total_selected"] == 3
    assert summary["resolved"] == 2
    assert summary["branches_ready"] == 2
    assert summary["rejected"] == 1
    assert summary["rejected_by_code"] == {"TAG_NOT_FOUND": 1}
    assert summary["failed"] == 0
    assert ctx.step_names[0] == "prepare-batch"
    assert ctx.callbacks == [("transform-0", "cb-0"), ("transform-2", "cb-1")]
    assert "settle-0" not in ctx.step_names and "settle-2" not in ctx.step_names
    # The CodeCommit client is never touched by the orchestrator after resolve:
    # the build pushes the branch and the flow ends there.
    assert not any(n.startswith("pull-request-") for n in ctx.step_names)


def test_batch_handler_single_arn_backcompat(monkeypatch):
    """A single-ARN event (function_arn) still runs as a one-unit batch."""
    tbl, _ = _batch_harness(monkeypatch)
    ctx = _FakeMapCtx(callback_outcomes={"transform-0": _transformed_raw()})
    summary = migrate._run_batch({"function_arn": _FN_A}, ctx)
    assert summary["total_selected"] == 1
    assert summary["branches_ready"] == 1


def test_prepare_batch_stores_execution_arn_from_context(monkeypatch):
    tbl, _ = _batch_harness(monkeypatch)
    ctx = _FakeMapCtx(callback_outcomes={"transform-0": _transformed_raw()})
    migrate._run_batch({"function_arns": [_FN_A]}, ctx)   # no execution_arn in the event
    migbatch = _update_for_key(tbl, "migbatch#")
    assert _updated_field(migbatch, "migration_batch_execution_arn") == "arn:exec:fake"
    # The mig# item carries the same ARN through the resolve request.
    assert tbl.items[_mig_key_for(_FN_A)]["migration_status"] == "IN_PROGRESS"


def test_transform_callback_transformed_is_ok(monkeypatch):
    tbl, _ = _batch_harness(monkeypatch)
    ctx = _FakeMapCtx(callback_outcomes={"transform-0": _transformed_raw()})
    summary, batch = _run_capturing(ctx, {"function_arns": [_FN_A]})
    assert _unit(batch, 0) == {"stage": "transform", "ok": True, "status": "BRANCH_READY",
                               "branch": "lambda-migration/python3.12-x"}
    assert summary["branches_ready"] == 1
    assert ctx.step_names == ["prepare-batch", "resolve-0"]   # no settle on the success path
    assert tbl.items[_mig_key_for(_FN_A)]["migration_callback_id"] == "cb-0"
    # The harness has no build_complete: in production it writes BRANCH_READY on
    # the item before completing the callback, so the orchestrator never does.
    assert tbl.items[_mig_key_for(_FN_A)]["migration_status"] == "IN_PROGRESS"


def test_transform_callback_failed_result_is_ok_false_batch_continues(monkeypatch):
    tbl, _ = _batch_harness(monkeypatch)
    ctx = _FakeMapCtx(callback_outcomes={
        "transform-0": json.dumps({"status": "FAILED", "failure_code": "TRANSFORM_FAILED"}),
        "transform-1": _transformed_raw(),
    })
    summary, batch = _run_capturing(ctx, {"function_arns": [_FN_A, _FN_C]})
    unit0, unit1 = _unit(batch, 0), _unit(batch, 1)
    assert unit0 == {"stage": "transform", "ok": False, "status": "FAILED", "failure_code": "TRANSFORM_FAILED"}
    assert unit1["ok"] is True and unit1["status"] == "BRANCH_READY"
    assert summary["failed"] == 1 and summary["branches_ready"] == 1
    assert ctx.callbacks == [("transform-0", "cb-0"), ("transform-1", "cb-1")]
    assert tbl.items[_mig_key_for(_FN_A)]["migration_callback_id"] == "cb-0"
    assert tbl.items[_mig_key_for(_FN_C)]["migration_callback_id"] == "cb-1"


def test_transform_callback_timeout_is_ok_false_timed_out(monkeypatch):
    tbl, _ = _batch_harness(monkeypatch)
    ctx = _FakeMapCtx(callback_outcomes={"transform-0": CallbackTimeoutError("Callback timed out")})
    summary = migrate._run_batch({"function_arns": [_FN_A]}, ctx)
    assert summary["failed"] == 1 and summary["branches_ready"] == 0
    assert ctx.step_names == ["prepare-batch", "resolve-0", "settle-0"]
    item = tbl.items[_mig_key_for(_FN_A)]
    assert item["migration_status"] == "FAILED" and item["migration_failure_code"] == "TIMED_OUT"
    settle = tbl.updates[-1]
    assert settle["ConditionExpression"] == "migration_status IN (:queued, :inprogress)"
    assert settle["ExpressionAttributeValues"][":queued"] == "QUEUED"
    assert settle["ExpressionAttributeValues"][":inprogress"] == "IN_PROGRESS"


def test_transform_submitter_error_is_settled_transform_failed(monkeypatch):
    tbl, _ = _batch_harness(monkeypatch)
    ctx = _FakeMapCtx(callback_outcomes={"transform-0": CallbackSubmitterError("submitter failed")})
    summary = migrate._run_batch({"function_arns": [_FN_A]}, ctx)
    assert summary["failed"] == 1
    assert ctx.step_names == ["prepare-batch", "resolve-0", "settle-0"]
    item = tbl.items[_mig_key_for(_FN_A)]
    assert item["migration_status"] == "FAILED" and item["migration_failure_code"] == "TRANSFORM_FAILED"


def test_transform_transformed_without_branch_is_transform_failed_and_settled(monkeypatch):
    tbl, _ = _batch_harness(monkeypatch)
    ctx = _FakeMapCtx(callback_outcomes={"transform-0": json.dumps({"status": "TRANSFORMED"})})
    summary = migrate._run_batch({"function_arns": [_FN_A]}, ctx)
    assert summary["failed"] == 1 and summary["branches_ready"] == 0
    assert ctx.step_names == ["prepare-batch", "resolve-0", "settle-0"]
    item = tbl.items[_mig_key_for(_FN_A)]
    assert item["migration_status"] == "FAILED" and item["migration_failure_code"] == "TRANSFORM_FAILED"
    assert item["migration_failure_detail"] == "transformed without a branch"


def test_transform_failed_payload_does_not_run_settle(monkeypatch):
    # build_complete already settled the item; the orchestrator does not write twice.
    tbl, _ = _batch_harness(monkeypatch)
    ctx = _FakeMapCtx(callback_outcomes={
        "transform-0": json.dumps({"status": "FAILED", "failure_code": "TRANSFORM_FAILED"})})
    summary = migrate._run_batch({"function_arns": [_FN_A]}, ctx)
    assert summary["failed"] == 1
    assert "settle-0" not in ctx.step_names


def test_transform_push_failed_payload_is_failed_with_push_failed_code(monkeypatch):
    # The build pushed nothing and reported PUSH_FAILED; build_complete wrote
    # FAILED/PUSH_FAILED already, the orchestrator folds the code as-is.
    tbl, _ = _batch_harness(monkeypatch)
    ctx = _FakeMapCtx(callback_outcomes={
        "transform-0": json.dumps({"status": "FAILED", "failure_code": "PUSH_FAILED",
                                   "branch": "lambda-migration/python3.12-x",
                                   "diff_summary": "git push failed: denied"})})
    summary, batch = _run_capturing(ctx, {"function_arns": [_FN_A]})
    assert _unit(batch, 0) == {"stage": "transform", "ok": False, "status": "FAILED",
                               "failure_code": "PUSH_FAILED"}
    assert summary["failed"] == 1 and summary["branches_ready"] == 0
    assert "settle-0" not in ctx.step_names


def test_transform_no_changes_is_neutral_and_unsettled(monkeypatch):
    tbl, _ = _batch_harness(monkeypatch)
    ctx = _FakeMapCtx(callback_outcomes={"transform-0": json.dumps({"status": "NO_CHANGES"})})
    summary, batch = _run_capturing(ctx, {"function_arns": [_FN_A]})
    assert _unit(batch, 0) == {"stage": "transform", "ok": True, "status": "NO_CHANGES"}
    assert summary["no_changes"] == 1 and summary["branches_ready"] == 0 and summary["failed"] == 0
    assert ctx.step_names == ["prepare-batch", "resolve-0"]      # no settle


@pytest.mark.parametrize("exc", [StepInterruptedError("x"), NonDeterministicExecutionError("x")])
def test_settle_step_invocation_error_escapes(monkeypatch, exc):
    # SDK control-flow errors MUST escape _migrate_one so the SDK can retry the
    # invocation or fail the execution; a bare `except Exception` would settle
    # an item as FAILED whose build is still running / whose settle is re-run on
    # the retried invocation.
    tbl, _ = _batch_harness(monkeypatch)
    ctx = _FakeMapCtx(callback_outcomes={"transform-0": CallbackTimeoutError("Callback timed out")},
                      step_outcomes={"settle-0": exc})
    with pytest.raises(type(exc)):
        migrate._run_batch({"function_arns": [_FN_A]}, ctx)
    assert tbl.items[_mig_key_for(_FN_A)]["migration_status"] == "IN_PROGRESS"
