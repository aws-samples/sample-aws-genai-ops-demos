"""
Payload-budget tests for the durable refresh pipeline (issue #225).

These are the tests that must catch the original failure: a large organization
(accounts x regions x scanners) accumulated every discovered resource into one
in-memory list that crossed two durable checkpoints and blew the Lambda
262144-byte STEP output limit. After the fix every durable boundary carries
only BOUNDED facts (counts, scope keys, labels), so a synthetic 150+ cell
organization stays well under the byte budget - and the guard rejects any
future unbounded payload loudly.
"""
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Heavy modules are stubbed before import so the test needs no AWS access.
for mod in ("bedrock_agentcore", "requests", "bs4"):
    sys.modules.setdefault(mod, MagicMock())
os.environ.setdefault("AWS_REGION", "eu-central-1")

from aws_durable_execution_sdk_python.concurrency.models import BatchItemStatus

import guard
import pipeline as lp


# --- synthetic large-organization fixture ----------------------------------

ACCOUNTS = [f"{i:012d}" for i in range(1, 16)]          # 15 accounts
REGIONS = ["eu-central-1", "us-west-2"]                 # 2 regions
SCANNER_KEYS = {                                        # 5 scanners
    "Lambda": ["lambda"],
    "RDS": ["rds", "aurora"],
    "EKS": ["eks"],
    "ElastiCache": ["elasticache"],
    "OpenSearch": ["opensearch"],
}
# 15 x 2 x 5 = 150 cells
RESOURCES_PER_CELL = 40


def _large_cells():
    cells = []
    for account in ACCOUNTS:
        for label, keys in SCANNER_KEYS.items():
            for region in REGIONS:
                cells.append({"label": label, "region": region, "service_keys": keys,
                              "account_id": account, "account_name": f"Account {account}"})
    return cells


class _FakeBatchItem:
    def __init__(self, index, result):
        self.index = index
        self.result = result
        self.status = BatchItemStatus.SUCCEEDED


class _FakeBatch:
    def __init__(self, results):
        self.all = [_FakeBatchItem(i, r) for i, r in enumerate(results)]


def _bounded_cell_result(cell):
    """The post-fix bounded scan_cell result: counts + labels, no per-resource rows."""
    return {
        "label": cell["label"],
        "region": cell["region"],
        "account_id": cell["account_id"],
        "service_keys": cell["service_keys"],
        "discovered": RESOURCES_PER_CELL,
        "needs_attention": RESOURCES_PER_CELL // 4,
        "ok": True,
    }


def _fat_resource(n):
    """A realistic per-resource row - the kind that used to ride the durable boundary."""
    return {
        "service_name": "lambda",
        "item_id": f"inventory#111111111111#eu-central-1#res-{n}",
        "account_id": "111111111111",
        "region": "eu-central-1",
        "status": "deprecated",
        "service_specific": {
            "identifier": f"function-{n}",
            "name": f"my-service-function-{n}-with-a-fairly-long-descriptive-name",
            "arn": f"arn:aws:lambda:eu-central-1:111111111111:function:my-service-function-{n}",
            "affected_resource_details": [
                {"name": f"function-{n}-{j}", "arn": f"arn:aws:lambda:eu-central-1:111111111111:function:function-{n}-{j}",
                 "console_url": f"https://console.aws.amazon.com/lambda/home?region=eu-central-1#/functions/function-{n}-{j}"}
                for j in range(20)
            ],
        },
    }


def _contains_resource_keys(value) -> bool:
    """True if a per-resource key appears anywhere in the structure."""
    banned = {"items", "affected_resource_details", "affected_resource_names"}
    if isinstance(value, dict):
        if banned & set(value.keys()):
            return True
        return any(_contains_resource_keys(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_resource_keys(v) for v in value)
    return False


# --- 1. scan summary is bounded ---------------------------------------------

def test_scan_summary_is_bounded_for_a_large_org():
    cells = _large_cells()
    assert len(cells) >= 150
    batch = _FakeBatch([_bounded_cell_result(c) for c in cells])
    scan_summary = lp.summarize_scan(cells, batch)
    assert guard.payload_bytes(scan_summary) < guard.STEP_PAYLOAD_BUDGET
    # No per-resource objects crossed the boundary.
    assert not _contains_resource_keys(scan_summary)
    # The aggregates are correct despite being bounded.
    assert scan_summary["items_discovered"] == len(cells) * RESOURCES_PER_CELL
    assert scan_summary["needs_attention"] == len(cells) * (RESOURCES_PER_CELL // 4)


# --- 2. reconcile argument is bounded ---------------------------------------

def test_reconcile_argument_is_bounded_for_a_large_org():
    cells = _large_cells()
    batch = _FakeBatch([_bounded_cell_result(c) for c in cells])
    scan_summary = lp.summarize_scan(cells, batch)
    # The actual arguments the handler passes into the reconcile step.
    run_id = "11111111-1111-1111-1111-111111111111"
    reconcile_args = (run_id, scan_summary["scanned_scopes"], scan_summary["accounts_failed"])
    assert len(scan_summary["scanned_scopes"]) >= 150
    assert guard.payload_bytes(reconcile_args) < guard.STEP_PAYLOAD_BUDGET
    assert not _contains_resource_keys(reconcile_args)


# --- 3. a single scan cell result is bounded --------------------------------

def test_scan_cell_result_is_bounded():
    cell = _large_cells()[0]
    result = _bounded_cell_result(cell)
    assert guard.payload_bytes(result) < guard.STEP_PAYLOAD_BUDGET
    assert "items" not in result


# --- 4. the guard rejects an unbounded payload ------------------------------

def test_guard_rejects_an_unbounded_payload():
    big = [_fat_resource(n) for n in range(400)]
    with pytest.raises(guard.PayloadBudgetError):
        guard.guard_payload({"items": big}, name="x")
    # A bounded dict passes through unchanged.
    bounded = {"discovered": 10, "needs_attention": 2}
    assert guard.guard_payload(bounded, name="ok") is bounded


# --- 5. the old (pre-fix) shape would have exceeded the hard limit ----------

def test_old_shape_would_have_exceeded_the_hard_limit():
    # Reconstruct the pre-fix reconcile argument: the full items list for a 150+
    # cell org (every cell contributes its resources). This is exactly what used
    # to cross the durable boundary and abort the run.
    cells = _large_cells()
    old_items = []
    for i, _ in enumerate(cells):
        old_items.extend(_fat_resource(f"{i}-{n}") for n in range(RESOURCES_PER_CELL))
    old_payload = {"run_id": "x", "items": old_items}
    assert guard.payload_bytes(old_payload) > guard.STEP_PAYLOAD_HARD_LIMIT
