"""
Engine Lambda entry point of the EKS demo: the shared durable engine (shared/devops-agent/lab) wired
to this demo's scenarios and handlers.
"""

import handlers
import scenarios
from engine import make_handler


def _resolve(scenario_id: str):
    scenario = scenarios.get(scenario_id)
    if scenario is None:
        raise ValueError(f'Unknown scenario: {scenario_id!r}')
    ops = handlers.HANDLERS.get(scenario.get('handler', ''))
    if ops is None:
        raise ValueError(f"Scenario {scenario_id!r} names unknown handler {scenario.get('handler')!r}")
    return ops.inject, ops.revert, scenarios.auto_revert_seconds(scenario)


handler = make_handler(_resolve)
