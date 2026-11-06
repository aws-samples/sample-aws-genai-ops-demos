"""
Byte-budget invariant guard for durable step payloads (issue #225).

The refresh pipeline is ONE durable execution. Lambda durable functions cap a
STEP output payload at 262144 bytes; a payload carrying per-resource data grows
linearly with org size and crosses that limit, aborting the run. Every durable
boundary must therefore carry only BOUNDED facts (counts, keys, labels).

guard_payload() is wired at each boundary so a future unbounded payload fails
fast and loudly in tests rather than silently in production.
"""
import json

STEP_PAYLOAD_HARD_LIMIT = 262_144           # Lambda durable-functions STEP output limit
STEP_PAYLOAD_BUDGET = 200_000               # safe budget, well under the hard limit


class PayloadBudgetError(AssertionError):
    """A durable step argument or result exceeded the safe byte budget."""


def payload_bytes(value) -> int:
    return len(json.dumps(value, default=str).encode("utf-8"))


def guard_payload(value, *, name: str, budget: int = STEP_PAYLOAD_BUDGET):
    """Fail fast and loudly if a durable step payload exceeds the budget.

    Returns value unchanged so it can wrap a return/argument inline."""
    size = payload_bytes(value)
    if size > budget:
        raise PayloadBudgetError(
            f"durable payload '{name}' is {size} bytes, over the {budget}-byte "
            f"budget (hard limit {STEP_PAYLOAD_HARD_LIMIT}); it must carry bounded "
            f"facts only (issue #225)")
    return value
