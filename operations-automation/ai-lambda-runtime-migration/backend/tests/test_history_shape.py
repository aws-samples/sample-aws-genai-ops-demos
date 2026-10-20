"""Integration test on the real durable SDK runner: pins the operation NAMES a prepare-batch -> map 'migrate' ->
resolve-<i> -> wait_for_callback transform-<i> handler really records, and the
SHAPE of the transform-<i> ContextSucceeded payload (a JSON string whose
content is JSON), against aws_durable_execution_sdk_python 2.x and its local
test runner. api._migration_progress is then fed the recorded history so its
anchored regexes and _decode_context_result are proven on real events, not on
hand-written fixtures.

The handler is minimal and self-contained: no migrate import, no boto3, no AWS.
The runner may print a 'BackgroundThreadError: Checkpoint creation failed /
Invalid checkpoint token' traceback for the invocation it suspended at the
callback; that is runner noise, the execution still SUCCEEDS. Assert on
result.status, never on logs.
"""
import json

from aws_durable_execution_sdk_python import (
    durable_execution, durable_step, durable_wait_for_callback, DurableContext, StepContext,
)
from aws_durable_execution_sdk_python.config import Duration, MapConfig, WaitForCallbackConfig
from aws_durable_execution_sdk_python.types import WaitForCallbackContext
from aws_durable_execution_sdk_python_testing import DurableFunctionTestRunner

import api
from tests.test_api import _HistoryClient, _context_string_payload

_CALLBACK_RESULT = {"status": "TRANSFORMED", "branch": "b"}

EXPECTED_OPERATION_NAMES = {
    "prepare-batch",
    "migrate",
    "map-item-0",
    "resolve-0",
    "transform-0",
    "transform-0 create callback id",
    "transform-0 submitter",
}


@durable_step
def prepare_batch(step: StepContext) -> dict:
    return {"accepted": ["a"], "total_selected": 1}


@durable_step
def resolve(step: StepContext, index: int) -> dict:
    return {"ok": True}


@durable_wait_for_callback
def start_transform(callback_id: str, ctx: WaitForCallbackContext) -> None:
    # No-op submitter: the test completes the callback from the outside.
    return None


@durable_execution
def handler(event: dict, context: DurableContext) -> dict:
    batch = context.step(prepare_batch(), name="prepare-batch")

    def _migrate_one(ctx: DurableContext, _item: str, index: int, _all) -> dict:
        ctx.step(resolve(index), name=f"resolve-{index}")
        raw = ctx.wait_for_callback(
            start_transform(), name=f"transform-{index}",
            config=WaitForCallbackConfig(timeout=Duration.from_minutes(5)))
        return {"raw": raw}

    context.map(batch["accepted"], _migrate_one, name="migrate",
                config=MapConfig(max_concurrency=5))
    return {"ok": True}


def test_history_shape_on_real_runner():
    with DurableFunctionTestRunner(handler, poll_interval=0.1) as runner:
        arn = runner.run_async(input=json.dumps({}))
        cb = runner.wait_for_callback(arn, name="transform-0 create callback id", timeout=30)
        runner.send_callback_success(cb, json.dumps(_CALLBACK_RESULT).encode())
        result = runner.wait_for_result(arn, timeout=60)
        events = [e.to_dict() for e in
                  runner._executor.get_execution_history(arn, include_execution_data=True).events]

    assert result.status.name == "SUCCEEDED"

    ops = result.get_all_operations()
    assert {op.name for op in ops} == EXPECTED_OPERATION_NAMES

    # The transform-<i> operation is a WaitForCallback CONTEXT whose result is
    # the raw callback string, serialized as a bare JSON string (section 2.4).
    transform = next(op for op in ops if op.name == "transform-0")
    assert transform.sub_type.name == "WAIT_FOR_CALLBACK"
    assert transform.result == _context_string_payload(_CALLBACK_RESULT)
    assert api._decode_context_result(transform.result) == _CALLBACK_RESULT

    # The recorded history folds into three successful phases.
    progress = api._migration_progress(_HistoryClient(events), arn, "SUCCEEDED")
    phases = progress["phases"]
    assert [p["label"] for p in phases] == [
        "Prepare the migration batch",
        "Resolve source repositories",
        "Transform with AWS Transform",
    ]
    assert [(p["status"], p["done"], p["failed"]) for p in phases] == [("success", 1, 0)] * 3
    assert phases[2]["branchesReady"] == 1
    assert progress["rejected_by_code"] == {}
    assert progress["total_selected"] == 1
    assert progress["accepted"] == 1
