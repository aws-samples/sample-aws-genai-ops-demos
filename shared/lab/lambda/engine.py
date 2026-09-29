"""
Demo Lab engine: the mechanism behind "inject a failure that always reverts".

One Lambda durable function execution per injection:

    inject  ──►  await-rollback (wait_for_callback, timeout)  ──►  revert

Two roads lead to `revert`, and only two:
  * the presenter clicks Rollback: `request_rollback()` resolves the execution's callback
    and the wait returns at once;
  * nobody does: the callback times out (CallbackTimeoutError) and the revert runs anyway.

There is no state store. "Is it injected?" is the demo's business (read the live
environment); "where is the run?" is read from the execution history (`run_view()`).
Closing the browser, a cold start or an API error cannot leave the environment broken.

This module knows nothing about a demo's scenarios or UI. A demo wires it in two places:

  Durable function (engine Lambda):
      from engine import make_handler
      handler = make_handler(lambda scenario_id: (inject_fn, revert_fn, timeout_seconds))

  Control plane (API Lambda):
      from engine import Engine
      eng = Engine(engine_function_arn=os.environ['ENGINE_FUNCTION_ARN'])
      eng.start(scenario_id)            # refuses if any execution is RUNNING (one at a time)
      eng.running()                     # the RUNNING execution, if any
      eng.recent()                      # recent executions, all statuses
      eng.run_view(execution)           # steps, countdown, callback id, revert reason
      eng.request_rollback(execution)   # resolves the callback -> revert runs now
      Engine.scenario_of(execution)     # '<scenario-id>' from the execution name

Durable API quirks encoded here so no demo rediscovers them: executions are invoked
through a qualified ARN (alias) with a unique DurableExecutionName (names are
idempotency keys; we use `<scenario-id>-<epoch>`, charset [a-zA-Z0-9-_], max 64);
ListDurableExecutionsByFunction takes the UNqualified function name and at most ONE
status in its filter; the callback id is only in the history's CallbackStarted event.
"""

import json
import logging
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import boto3
from aws_durable_execution_sdk_python import DurableContext, StepContext, durable_execution
from aws_durable_execution_sdk_python.config import Duration, WaitForCallbackConfig
from aws_durable_execution_sdk_python.context import WaitForCallbackContext
from aws_durable_execution_sdk_python.exceptions import CallbackTimeoutError

logger = logging.getLogger(__name__)

STEP_INJECT = 'inject'
STEP_AWAIT = 'await-rollback'
STEP_REVERT = 'revert'

# (inject, revert, timeout_seconds) for a scenario id; raise ValueError for an unknown id.
Resolver = Callable[[str], Tuple[Callable[[], Dict[str, Any]], Callable[[], Dict[str, Any]], int]]


# ---------------------------------------------------------------------------
# Durable function
# ---------------------------------------------------------------------------

def _noop_submitter(callback_id: str, _ctx: WaitForCallbackContext) -> None:
    """Nothing to hand the callback id to: the API finds it in the execution history."""
    logger.info('Awaiting rollback, callback id %s', callback_id)


def make_handler(resolve: Resolver):
    """Build the durable Lambda handler. Payload: {"scenarioId": "..."}."""

    @durable_execution
    def handler(event: Dict[str, Any], context: DurableContext) -> Dict[str, Any]:
        scenario_id = (event or {}).get('scenarioId', '')
        inject_fn, revert_fn, timeout = resolve(scenario_id)

        def inject(_: StepContext) -> Dict[str, Any]:
            return {**(inject_fn() or {}), 'at': int(time.time())}

        def revert(_: StepContext) -> Dict[str, Any]:
            return {**(revert_fn() or {}), 'at': int(time.time())}

        injected = context.step(inject, name=STEP_INJECT)

        reason = 'manual'
        try:
            context.wait_for_callback(
                _noop_submitter,
                name=STEP_AWAIT,
                config=WaitForCallbackConfig(timeout=Duration.from_seconds(int(timeout))),
            )
        except CallbackTimeoutError:
            reason = 'auto'
            logger.info('No rollback within %ss; auto-reverting %s', timeout, scenario_id)

        reverted = context.step(revert, name=STEP_REVERT)
        return {
            'scenarioId': scenario_id,
            'injectedAt': injected.get('at'),
            'revertedAt': reverted.get('at'),
            'revertReason': reason,
            'autoRevertSeconds': int(timeout),
        }

    return handler


# ---------------------------------------------------------------------------
# Control plane
# ---------------------------------------------------------------------------

def _epoch(ts) -> Optional[int]:
    return int(ts.timestamp()) if isinstance(ts, datetime) else None


class AlreadyRunning(Exception):
    """Another execution is RUNNING; the Lab runs one scenario at a time."""

    def __init__(self, execution: Dict[str, Any]):
        super().__init__(execution.get('DurableExecutionName', 'an execution'))
        self.execution = execution


class Engine:
    def __init__(self, engine_function_arn: str, lambda_client=None):
        self.arn = engine_function_arn                     # qualified (alias) ARN
        self._lambda = lambda_client

    @property
    def client(self):
        if self._lambda is None:
            self._lambda = boto3.client('lambda')
        return self._lambda

    @property
    def unqualified_arn(self) -> str:
        parts = self.arn.split(':')
        return ':'.join(parts[:7]) if len(parts) > 7 else self.arn

    @staticmethod
    def execution_name(scenario_id: str) -> str:
        return f'{scenario_id}-{int(time.time())}'

    @staticmethod
    def scenario_of(execution: Dict[str, Any]) -> str:
        """`<scenario-id>-<epoch>` -> `<scenario-id>`."""
        name = execution.get('DurableExecutionName', '')
        return name.rsplit('-', 1)[0] if '-' in name else name

    def list(self, status: Optional[str] = None, max_items: int = 20) -> List[Dict[str, Any]]:
        if not self.arn:
            return []
        kwargs: Dict[str, Any] = {'FunctionName': self.unqualified_arn, 'MaxItems': max_items}
        if status:
            kwargs['Statuses'] = [status]
        return self.client.list_durable_executions_by_function(**kwargs).get('DurableExecutions', [])

    def running(self, scenario_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        for ex in self.list('RUNNING'):
            if scenario_id is None or self.scenario_of(ex) == scenario_id:
                return ex
        return None

    def recent(self, max_items: int = 20) -> List[Dict[str, Any]]:
        return self.list(None, max_items)

    def start(self, scenario_id: str) -> Dict[str, Any]:
        """Start one execution; raises AlreadyRunning while any execution is RUNNING."""
        busy = self.running()
        if busy:
            raise AlreadyRunning(busy)
        name = self.execution_name(scenario_id)
        resp = self.client.invoke(
            FunctionName=self.arn,
            InvocationType='Event',
            DurableExecutionName=name,
            Payload=json.dumps({'scenarioId': scenario_id}).encode(),
        )
        return {'executionName': name, 'executionArn': resp.get('DurableExecutionArn')}

    def run_view(self, execution: Dict[str, Any]) -> Dict[str, Any]:
        """Where the run is, read from the execution history (best effort, never raises).

        steps: {inject|await-rollback|revert: pending|in-progress|success|error|stopped}
        remainingSeconds while waiting, revertReason once decided, callbackId while waiting.
        """
        arn = execution['DurableExecutionArn']
        status = execution.get('Status', '')
        steps: Dict[str, str] = {}
        callback: Dict[str, Any] = {}
        revert_reason = None
        try:
            paginator = self.client.get_paginator('get_durable_execution_history')
            for page in paginator.paginate(DurableExecutionArn=arn, IncludeExecutionData=True):
                for ev in page.get('Events', []):
                    et, name = ev.get('EventType', ''), ev.get('Name') or ''
                    if et == 'StepStarted':
                        steps.setdefault(name, 'in-progress')
                    elif et == 'StepSucceeded':
                        steps[name] = 'success'
                    elif et == 'StepFailed':
                        steps[name] = 'error'
                    elif et == 'CallbackStarted':
                        d = ev.get('CallbackStartedDetails') or {}
                        callback = {'id': d.get('CallbackId'), 'startedAt': _epoch(ev.get('EventTimestamp')),
                                    'timeoutSeconds': d.get('Timeout'), 'state': 'waiting'}
                    elif et == 'CallbackSucceeded':
                        callback['state'], revert_reason = 'resolved', 'manual'
                    elif et == 'CallbackTimedOut':
                        callback['state'], revert_reason = 'timedOut', 'auto'
                    elif et == 'CallbackFailed':
                        callback['state'] = 'failed'
        except Exception as e:
            logger.warning('History read failed for %s: %s', arn, e)

        wait = {'waiting': 'in-progress', 'resolved': 'success', 'timedOut': 'success', 'failed': 'error'}.get(
            callback.get('state', ''), 'pending')
        view_steps = {
            STEP_INJECT: steps.get(STEP_INJECT, 'pending'),
            STEP_AWAIT: wait,
            STEP_REVERT: steps.get(STEP_REVERT, 'pending'),
        }
        if status != 'RUNNING':
            view_steps = {k: ('stopped' if v in ('pending', 'in-progress') else v) for k, v in view_steps.items()}

        remaining = None
        if callback.get('state') == 'waiting' and callback.get('startedAt') and callback.get('timeoutSeconds'):
            remaining = max(0, callback['startedAt'] + int(callback['timeoutSeconds']) - int(time.time()))

        return {
            'executionArn': arn,
            'executionName': execution.get('DurableExecutionName'),
            'scenarioId': self.scenario_of(execution),
            'status': status,
            'startedAt': _epoch(execution.get('StartTimestamp')),
            'endedAt': _epoch(execution.get('EndTimestamp')),
            'steps': view_steps,
            'remainingSeconds': remaining,
            'revertReason': revert_reason,
            'callbackId': callback.get('id') if callback.get('state') == 'waiting' else None,
        }

    def request_rollback(self, execution: Dict[str, Any]) -> bool:
        """Resolve the running execution's callback so `revert` runs now.
        Returns False when the execution has no open callback yet (still injecting)."""
        view = self.run_view(execution)
        if not view.get('callbackId'):
            return False
        self.client.send_durable_execution_callback_success(
            CallbackId=view['callbackId'],
            Result=json.dumps({'reason': 'manual', 'at': int(time.time())}).encode(),
        )
        return True
