"""The durable scan: seven steps over the plain functions in `steps`. Every non-deterministic operation (clock,
boto3 session and client creation, AWS calls) happens inside a durable step; the code between steps only moves step
results along. Step results stay small (they are checkpoints): bulk data goes to the run store.

Step 2 maps one overview step per account (`regions-<account>`), step 3 one collect step per account and unit
(`collect-<account>-<n>`). Both tolerate failures: a failed account or unit is reported, never fatal.

Input: {"run_id": "scan-YYYYMMDD-HHMMSS", "scope": "account" | "organization"} (scope defaults to "account"). Our
code never logs or stores exception messages, only type names (the SDK itself logs step exceptions)."""
import logging
import os
import re
from datetime import datetime, timezone

import boto3
from aws_durable_execution_sdk_python import DurableContext, StepContext, durable_execution, durable_step
from aws_durable_execution_sdk_python.concurrency.models import BatchItemStatus
from aws_durable_execution_sdk_python.exceptions import UnrecoverableError
from aws_durable_execution_sdk_python.config import CompletionConfig, Duration, MapConfig, StepConfig
from aws_durable_execution_sdk_python.retries import RetryStrategyConfig, create_retry_strategy

from s3dp.service import steps, store

logger = logging.getLogger(__name__)

RUN_ID = re.compile(r"^scan-\d{8}-\d{6}$")
RETRY = StepConfig(retry_strategy=create_retry_strategy(RetryStrategyConfig(
    max_attempts=3, initial_delay=Duration.from_seconds(5), max_delay=Duration.from_seconds(30))))
TOLERATE = MapConfig(max_concurrency=4, completion_config=CompletionConfig(tolerated_failure_percentage=100))


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _model():
    return os.environ.get("MODEL_ID", "")


@durable_step
def _accounts(step: StepContext, run_id, scope):
    return steps.accounts(store.from_env(), boto3.Session(), run_id, _now(), scope)


@durable_step
def _regions(step: StepContext, run, account_id):
    return steps.regions(store.from_env(), boto3.Session(), run, account_id)


@durable_step
def _collect(step: StepContext, run, account_id, n):
    return steps.collect(store.from_env(), boto3.Session(), run, account_id, n)


@durable_step
def _assemble(step: StepContext, run, per_account):
    return steps.assemble(store.from_env(), run, per_account)


@durable_step
def _classify(step: StepContext, run):
    return steps.classify(store.from_env(), boto3.Session(), run, _model())


@durable_step
def _score(step: StepContext, run):
    return steps.score(store.from_env(), run)


@durable_step
def _narrative(step: StepContext, run, ai):
    if not ai.get("classification") and ai.get("narrative") != "pending":
        return {"narrative": ai.get("narrative", "off")}      # no AI in this run: keep the stored status
    return steps.narrative(store.from_env(), boto3.Session(), run, _model())


@durable_step
def _publish(step: StepContext, run):
    return steps.publish(store.from_env(), run, _now())


@durable_step
def _fail(step: StepContext, run_id, error_code):
    steps.fail(store.from_env(), run_id, error_code, _now())


def results_by_index(batch, count):
    """BatchResult.all aligned to the input index; None where the item failed or never ran."""
    aligned = [None] * count
    for item in batch.all:
        if item.status is BatchItemStatus.SUCCEEDED and item.result is not None:
            aligned[item.index] = item.result
    return aligned


def _scope(event):
    scope = event.get("scope", "account")
    if scope not in steps.SCOPES:
        raise ValueError("scope: expected account or organization")
    if scope == "organization" and not steps.multi_account():
        raise ValueError("scope: organization scans need a multi-account deployment")
    return scope


def _run(event, context):
    event = event or {}
    run_id = event.get("run_id", "")
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ValueError("run_id: expected scan-YYYYMMDD-HHMMSS")
    scope = _scope(event)
    try:
        run = context.step(_accounts(run_id, scope), name="accounts", config=RETRY)
        accts = run["accounts"]
        overviews = results_by_index(context.map(
            accts, lambda ctx, a, i, _all: ctx.step(_regions(run, a), name=f"regions-{a}", config=RETRY),
            name="regions", config=TOLERATE), len(accts))
        # One work item per (account, unit) of every account whose overview was read.
        work = [[a, n] for a, ov in zip(accts, overviews) if ov and not ov.get("denied") for n in range(ov["units"])]
        failed = {}
        if work:
            done = results_by_index(context.map(
                work, lambda ctx, w, i, _all: ctx.step(_collect(run, w[0], w[1]), name=f"collect-{w[0]}-{w[1]}",
                                                       config=RETRY),
                name="collect", config=TOLERATE), len(work))
            for (a, n), res in zip(work, done):
                if res is None:
                    failed.setdefault(a, []).append(n)
        per_account = [None if ov is None else dict(ov, failed=failed.get(a, [])) for a, ov in zip(accts, overviews)]
        context.step(_assemble(run, per_account), name="assemble", config=RETRY)
        ai = context.step(_classify(run), name="classify", config=RETRY)
        context.step(_score(run), name="score", config=RETRY)
        context.step(_narrative(run, ai), name="narrative", config=RETRY)
        return context.step(_publish(run), name="publish", config=RETRY)
    except UnrecoverableError:
        raise       # SDK invariants (checkpoint, replay, invocation): the invocation must fail so Lambda retries
    except Exception as e:      # record the failure, then let the execution fail
        # After retries the SDK raises StepError; the original type is in error_type (fully qualified).
        code = (getattr(e, "error_type", None) or type(e).__name__).rsplit(".", 1)[-1]
        logger.error("scan failed: %s", code)
        context.step(_fail(run_id, code), name="fail", config=RETRY)     # error code only, never str(e)
        raise


handler = durable_execution(_run)
