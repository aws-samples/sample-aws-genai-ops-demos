"""
Lab engine: one Lambda durable function execution per injected scenario.

    inject  ──►  await-rollback (wait_for_callback, timeout = autoRevertSeconds)  ──►  revert

Two roads lead to `revert`, and only two:
  * the presenter clicks Rollback: the API resolves the callback
    (SendDurableExecutionCallbackSuccess) and the wait returns at once;
  * nobody does: the callback times out (CallbackTimeoutError) and the revert runs anyway.

There is no state store. "Is it injected?" is read from the cluster (k8s_ops.probe);
"where is the run?" is read from the execution history (see index.py). Closing the
browser, a Lambda cold start or an API error cannot leave the cluster broken.

Invoke through the `live` alias with a unique DurableExecutionName
(`<scenario-id>-<epoch>`), payload {"scenarioId": "..."}.
"""

import logging
import time
from typing import Any, Dict

from aws_durable_execution_sdk_python import DurableContext, StepContext, durable_execution
from aws_durable_execution_sdk_python.config import Duration, WaitForCallbackConfig
from aws_durable_execution_sdk_python.context import WaitForCallbackContext
from aws_durable_execution_sdk_python.exceptions import CallbackTimeoutError

import k8s_ops
import scenarios

logger = logging.getLogger()
logger.setLevel(logging.INFO)

STEP_INJECT = 'inject'
STEP_AWAIT = 'await-rollback'
STEP_REVERT = 'revert'


def _handler_for(scenario: Dict[str, Any]) -> k8s_ops.Handler:
    name = scenario.get('handler', '')
    try:
        return k8s_ops.HANDLERS[name]
    except KeyError:
        raise ValueError(f"Scenario {scenario.get('id')!r} names unknown handler {name!r}")


def _noop_submitter(callback_id: str, _ctx: WaitForCallbackContext) -> None:
    """Nothing to hand the callback id to: the API finds it in the execution history."""
    logger.info('Awaiting rollback, callback id %s', callback_id)


@durable_execution
def handler(event: Dict[str, Any], context: DurableContext) -> Dict[str, Any]:
    scenario_id = (event or {}).get('scenarioId', '')
    scenario = scenarios.get(scenario_id)
    if scenario is None:
        raise ValueError(f'Unknown scenario: {scenario_id!r}')
    ops = _handler_for(scenario)
    timeout = scenarios.auto_revert_seconds(scenario)

    def inject(_: StepContext) -> Dict[str, Any]:
        return {**ops.inject(), 'at': int(time.time())}

    def revert(_: StepContext) -> Dict[str, Any]:
        return {**ops.revert(), 'at': int(time.time())}

    injected = context.step(inject, name=STEP_INJECT)

    reason = 'manual'
    try:
        context.wait_for_callback(
            _noop_submitter,
            name=STEP_AWAIT,
            config=WaitForCallbackConfig(timeout=Duration.from_seconds(timeout)),
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
        'autoRevertSeconds': timeout,
    }
