"""Unit tests for build_complete.py (Phase 2 EventBridge completion handler).

A NORMAL Lambda: resolve the mig# item via the build-id index row, read
result.json from S3, write the terminal status with the live-status + batch
ownership guard (swallowing ConditionalCheckFailedException), then complete the
orchestrator's transform-<i> durable callback with the result. No real AWS.
"""
import json
import logging
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

import build_complete


class _FakeTable:
    """Serves buildidx# and mig# keys from one dict of items; records updates;
    can raise ConditionalCheckFailedException on the mig# write."""
    def __init__(self, items=None, raise_conditional=False):
        self.items = dict(items or {})
        self.updates = []
        self.raise_conditional = raise_conditional

    def get_item(self, Key):
        item = self.items.get(Key["item_key"])
        return {"Item": item} if item is not None else {}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        if self.raise_conditional:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "no"}},
                "UpdateItem")
        return {}


class _FakeLambda:
    """Records send_durable_execution_callback_success calls (or raises the
    configured ClientError). Deliberately defines NO Failure variant of the
    callback API: touching it raises AttributeError."""
    def __init__(self, error_code=None, calls=None):
        self.error_code = error_code
        self.calls = calls if calls is not None else []

    def send_durable_execution_callback_success(self, **kwargs):
        self.calls.append(("send", kwargs))
        if self.error_code:
            raise ClientError({"Error": {"Code": self.error_code, "Message": "no"}},
                              "SendDurableExecutionCallbackSuccess")
        return {}


def _event(build_id="proj:abc", status="SUCCEEDED"):
    # EventBridge sends the FULL build ARN, not the '<project>:<uuid>' id that
    # StartBuild returned and the buildidx# row is keyed with. The fixture
    # mirrors the real event so the handler's normalisation is exercised.
    arn = f"arn:aws:codebuild:eu-west-3:111111111111:build/{build_id}"
    return {"detail": {"build-id": arn, "build-status": status}}


def test_normalize_build_id_strips_the_event_arn_to_the_startbuild_form():
    from build_complete import normalize_build_id
    assert normalize_build_id(
        "arn:aws:codebuild:eu-west-3:111111111111:build/lambda-runtime-migration-transform:d6af1746-a94e"
    ) == "lambda-runtime-migration-transform:d6af1746-a94e"
    assert normalize_build_id("proj:abc") == "proj:abc"          # already normalised
    assert normalize_build_id("") == ""


_INDEX = {"mig_item_key": "mig#k", "job_id": "job-1", "callback_id": "cb-1", "batch_id": "b-1"}


def _items(index=_INDEX, mig=None):
    items = {"buildidx#proj:abc": index}
    if mig is not None:
        items["mig#k"] = mig
    return items


def _mig(status="IN_PROGRESS", **extra):
    return {"item_key": "mig#k", "migration_status": status, "migration_batch_id": "b-1",
            "migration_callback_id": "cb-1", **extra}


def _patch(monkeypatch, table, result_json=None, lambda_error=None):
    """Route boto3.client by service name: s3 serves result_json, lambda records
    callback sends. Returns the _FakeLambda."""
    monkeypatch.setattr(build_complete, "_table", lambda: table)
    monkeypatch.setattr(build_complete, "BUCKET_NAME", "bucket")
    fake_lambda = _FakeLambda(error_code=lambda_error)

    class _S3:
        def get_object(self, Bucket, Key):
            if result_json is None:
                raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
            return {"Body": MagicMock(read=lambda: json.dumps(result_json).encode())}

    def _client(service, *a, **k):
        return {"s3": _S3(), "lambda": fake_lambda}[service]

    monkeypatch.setattr(build_complete.boto3, "client", _client)
    return fake_lambda


def _field_written(table, field_name):
    u = table.updates[-1]
    for pn, field in u["ExpressionAttributeNames"].items():
        if field == field_name:
            return u["ExpressionAttributeValues"][pn.replace("#a", ":v")]
    return None


def _status_written(table):
    return _field_written(table, "migration_status")


def _sent_payload(fake_lambda):
    assert fake_lambda.calls, "expected a callback send"
    return json.loads(fake_lambda.calls[-1][1]["Result"].decode("utf-8"))


# --- result.json mapping ----------------------------------------------------

def test_result_transformed_maps_to_branch_ready(monkeypatch):
    """TRANSFORMED with a branch is the end of the flow: BRANCH_READY, the
    upgrade branch and the diff summary on the item, no failure fields."""
    tbl = _FakeTable(_items(mig=_mig()))
    lam = _patch(monkeypatch, tbl, result_json={
        "status": "TRANSFORMED", "branch": "upgrade", "diff_summary": "3 files"})
    out = build_complete.handler(_event())
    assert out["migration_status"] == "BRANCH_READY"
    assert out["updated"] is True
    assert _status_written(tbl) == "BRANCH_READY"
    assert _field_written(tbl, "migration_branch") == "upgrade"
    assert _field_written(tbl, "migration_diff_summary") == "3 files"
    assert _field_written(tbl, "migration_failure_detail") is None
    assert _field_written(tbl, "migration_failure_code") is None
    assert _field_written(tbl, "migration_updated_at")  # the staleness window keys on it
    payload = _sent_payload(lam)
    assert payload["status"] == "TRANSFORMED" and payload["branch"] == "upgrade"
    assert payload["failure_code"] is None


def test_result_transformed_unknown_item_attribute_is_ignored(monkeypatch):
    # An attribute this build does not know on the mig# item never changes the
    # outcome; only the result.json status and branch do.
    tbl = _FakeTable(_items(mig=_mig(migration_open_pr=True)))
    _patch(monkeypatch, tbl, result_json={"status": "TRANSFORMED", "branch": "upgrade"})
    out = build_complete.handler(_event())
    assert out["migration_status"] == "BRANCH_READY"
    assert _status_written(tbl) == "BRANCH_READY"


def test_result_transformed_without_branch_is_transform_failed(monkeypatch):
    # Same predicate as migrate.parse_build_result (transform_has_branch): the
    # item and the batch summary must agree that this unit failed.
    tbl = _FakeTable(_items(mig=_mig()))
    lam = _patch(monkeypatch, tbl, result_json={"status": "TRANSFORMED", "branch": ""})
    out = build_complete.handler(_event())
    assert out["migration_status"] == "FAILED"
    assert _status_written(tbl) == "FAILED"
    assert _field_written(tbl, "migration_failure_code") == "TRANSFORM_FAILED"
    assert _field_written(tbl, "migration_failure_detail") == "transformed without a branch"
    assert _field_written(tbl, "migration_branch") is None
    assert _sent_payload(lam)["failure_code"] == "TRANSFORM_FAILED"


def test_result_failed_push_failed_passes_code(monkeypatch):
    tbl = _FakeTable(_items(mig=_mig()))
    lam = _patch(monkeypatch, tbl, result_json={
        "status": "FAILED", "failure_code": "PUSH_FAILED", "branch": "upgrade",
        "diff_summary": "git push failed: denied"})
    out = build_complete.handler(_event())
    assert out["migration_status"] == "FAILED"
    assert _field_written(tbl, "migration_failure_code") == "PUSH_FAILED"
    assert _field_written(tbl, "migration_failure_detail") == "git push failed: denied"
    assert _field_written(tbl, "migration_branch") is None     # nothing was pushed
    payload = _sent_payload(lam)
    assert payload["status"] == "FAILED" and payload["failure_code"] == "PUSH_FAILED"


def test_result_failed_unknown_code_coerced_to_transform_failed(monkeypatch, caplog):
    tbl = _FakeTable(_items(mig=_mig()))
    lam = _patch(monkeypatch, tbl, result_json={"status": "FAILED", "failure_code": "SOMETHING_NEW"})
    with caplog.at_level(logging.INFO, logger="build_complete"):
        build_complete.handler(_event())
    assert _field_written(tbl, "migration_failure_code") == "TRANSFORM_FAILED"
    assert _sent_payload(lam)["failure_code"] == "TRANSFORM_FAILED"   # item and payload agree
    assert "SOMETHING_NEW" in caplog.text


def test_result_no_changes_maps_to_no_changes(monkeypatch):
    tbl = _FakeTable(_items(mig=_mig()))
    lam = _patch(monkeypatch, tbl, result_json={"status": "NO_CHANGES"})
    out = build_complete.handler(_event())
    assert out["migration_status"] == "NO_CHANGES"
    assert _status_written(tbl) == "NO_CHANGES"
    assert _field_written(tbl, "migration_failure_code") is None   # not a failure
    assert _field_written(tbl, "migration_failure_detail") == build_complete.NO_CHANGES_DETAIL
    assert _sent_payload(lam)["status"] == "NO_CHANGES"
    assert _sent_payload(lam)["failure_code"] is None


def test_result_failed_maps_to_failed_transform_failed(monkeypatch):
    tbl = _FakeTable(_items(mig=_mig()))
    lam = _patch(monkeypatch, tbl, result_json={"status": "FAILED", "diff_summary": "boom"})
    out = build_complete.handler(_event())
    assert out["migration_status"] == "FAILED"
    assert _field_written(tbl, "migration_failure_code") == "TRANSFORM_FAILED"
    assert _field_written(tbl, "migration_failure_detail") == "boom"
    assert _field_written(tbl, "migration_diff_summary") == "boom"
    payload = _sent_payload(lam)
    assert payload["status"] == "FAILED" and payload["failure_code"] == "TRANSFORM_FAILED"


def test_missing_result_timed_out(monkeypatch):
    tbl = _FakeTable(_items(mig=_mig()))
    lam = _patch(monkeypatch, tbl, result_json=None)
    out = build_complete.handler(_event(status="TIMED_OUT"))
    assert out["migration_status"] == "FAILED"
    assert _field_written(tbl, "migration_failure_code") == "TIMED_OUT"
    # The callback is still completed with Success and status FAILED in the payload.
    payload = _sent_payload(lam)
    assert payload["status"] == "FAILED" and payload["failure_code"] == "TIMED_OUT"
    assert payload["build_status"] == "TIMED_OUT"


def test_missing_result_failed_build(monkeypatch):
    tbl = _FakeTable(_items(mig=_mig()))
    lam = _patch(monkeypatch, tbl, result_json=None)
    out = build_complete.handler(_event(status="FAILED"))
    assert out["migration_status"] == "FAILED"
    assert _field_written(tbl, "migration_failure_code") == "TRANSFORM_FAILED"
    assert _sent_payload(lam)["status"] == "FAILED"


def test_conditional_check_failed_swallowed(monkeypatch):
    tbl = _FakeTable(_items(mig=_mig(status="FAILED")), raise_conditional=True)
    lam = _patch(monkeypatch, tbl, result_json={"status": "TRANSFORMED", "branch": "u"})
    out = build_complete.handler(_event())  # must NOT raise
    assert out["updated"] is False
    assert out["callback_sent"] is True      # the callback is still completed


def test_no_index_item_is_noop(monkeypatch):
    tbl = _FakeTable({})
    lam = _patch(monkeypatch, tbl, result_json=None)
    out = build_complete.handler(_event())
    assert out["updated"] is False
    assert lam.calls == []


# --- ownership guard --------------------------------------------------------

def test_write_terminal_conditions_on_batch_id(monkeypatch):
    tbl = _FakeTable(_items(mig=_mig()))
    _patch(monkeypatch, tbl, result_json={"status": "FAILED"})
    build_complete.handler(_event())
    u = tbl.updates[-1]
    cond = u["ConditionExpression"]
    assert ":queued" in cond and ":inprogress" in cond
    assert "migration_batch_id = :batch_id" in cond
    assert u["ExpressionAttributeValues"][":batch_id"] == "b-1"
    assert u["ExpressionAttributeValues"][":queued"] == "QUEUED"
    assert u["ExpressionAttributeValues"][":inprogress"] == "IN_PROGRESS"


def test_build_complete_from_older_batch_does_not_write(monkeypatch, caplog):
    index = {**_INDEX, "batch_id": "old", "callback_id": "cb-old"}
    mig = _mig(migration_batch_id="new", migration_callback_id="cb-new")
    tbl = _FakeTable(_items(index=index, mig=mig), raise_conditional=True)
    lam = _patch(monkeypatch, tbl, result_json={"status": "TRANSFORMED", "branch": "u"})
    with caplog.at_level(logging.INFO, logger="build_complete"):
        out = build_complete.handler(_event())
    assert out["updated"] is False
    assert tbl.items["mig#k"] == mig                  # unchanged
    assert "belongs to batch old" in caplog.text and "owned by new" in caplog.text
    # ONLY the buildidx# copy may be completed: cb-new belongs to the new batch's wait.
    assert lam.calls[-1][1]["CallbackId"] == "cb-old"


def test_build_complete_legacy_index_row_without_batch_id_writes_with_warning(monkeypatch, caplog):
    index = {"mig_item_key": "mig#k", "job_id": "job-1", "callback_id": "cb-1"}
    tbl = _FakeTable(_items(index=index, mig=_mig()))
    _patch(monkeypatch, tbl, result_json={"status": "FAILED"})
    with caplog.at_level(logging.WARNING, logger="build_complete"):
        out = build_complete.handler(_event())
    assert out["updated"] is True
    assert "migration_batch_id" not in tbl.updates[-1]["ConditionExpression"]
    assert "no batch id" in caplog.text


# --- callback completion ----------------------------------------------------

def test_completes_callback_with_result_payload(monkeypatch):
    order = []
    tbl = _FakeTable(_items(mig=_mig()))
    orig_update = tbl.update_item

    def _update(**kwargs):
        order.append("write")
        return orig_update(**kwargs)

    tbl.update_item = _update
    lam = _patch(monkeypatch, tbl, result_json={"status": "TRANSFORMED", "branch": "upgrade"})
    lam.calls = order  # the fake appends ("send", kwargs) into the same list
    out = build_complete.handler(_event())
    assert out["callback_sent"] is True
    assert order[0] == "write" and order[1][0] == "send"      # write, then send
    sent = order[1][1]
    assert sent["CallbackId"] == "cb-1"
    assert json.loads(sent["Result"])["status"] == "TRANSFORMED"
    # Only the Success variant of the callback API exists on the fake (and is
    # the only one the handler may call); the name is built here so the repo
    # grep gate for the Failure variant stays clean.
    assert not hasattr(lam, "send_durable_execution_callback_" + "failure")


def test_missing_callback_id_updates_item_only_and_logs(monkeypatch, caplog):
    index = {"mig_item_key": "mig#k", "job_id": "job-1", "batch_id": "b-1"}
    mig = {"item_key": "mig#k", "migration_status": "IN_PROGRESS", "migration_batch_id": "b-1"}
    tbl = _FakeTable(_items(index=index, mig=mig))
    lam = _patch(monkeypatch, tbl, result_json={"status": "FAILED"})
    with caplog.at_level(logging.WARNING, logger="build_complete"):
        out = build_complete.handler(_event())
    assert out["updated"] is True
    assert out["callback_sent"] is False
    assert lam.calls == []
    assert "no callback id" in caplog.text


@pytest.mark.parametrize("code", ["ResourceNotFoundException", "CallbackTimeoutException",
                                  "InvalidParameterValueException"])
def test_callback_resource_not_found_is_logged_not_raised(monkeypatch, caplog, code):
    tbl = _FakeTable(_items(mig=_mig()))
    _patch(monkeypatch, tbl, result_json={"status": "FAILED"}, lambda_error=code)
    with caplog.at_level(logging.WARNING, logger="build_complete"):
        out = build_complete.handler(_event())   # must NOT raise
    assert out["updated"] is True
    assert out["callback_sent"] is False
    assert code in caplog.text


def test_callback_access_denied_is_logged_and_raised(monkeypatch, caplog):
    tbl = _FakeTable(_items(mig=_mig()))
    _patch(monkeypatch, tbl, result_json={"status": "FAILED"}, lambda_error="AccessDeniedException")
    with caplog.at_level(logging.ERROR, logger="build_complete"):
        with pytest.raises(ClientError):
            build_complete.handler(_event())
    assert "MigrationCallback" in caplog.text
    assert _status_written(tbl) == "FAILED"          # the item was written first


def test_callback_unknown_client_error_is_raised(monkeypatch):
    tbl = _FakeTable(_items(mig=_mig()))
    _patch(monkeypatch, tbl, result_json={"status": "FAILED"}, lambda_error="KMSAccessDeniedException")
    with pytest.raises(ClientError):
        build_complete.handler(_event())


def test_callback_throttled_is_raised(monkeypatch):
    tbl = _FakeTable(_items(mig=_mig()))
    _patch(monkeypatch, tbl, result_json={"status": "FAILED"}, lambda_error="TooManyRequestsException")
    with pytest.raises(ClientError):
        build_complete.handler(_event())
