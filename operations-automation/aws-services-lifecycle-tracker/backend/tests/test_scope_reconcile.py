"""
Service-faithful tests for the per-scope write + bounded-read reconcile
(issue #225), backed by moto so DynamoDB semantics are real: Query by partition
key with LastEvaluatedKey pagination, BatchWriteItem, Decimal (not int) on read.

Guarded by importorskip so the suite still runs when moto is absent; install it
via `pip install -r requirements.txt` to actually exercise these.
"""
import os
import sys
from unittest.mock import patch

import pytest

pytest.importorskip("moto")
from moto import mock_aws
import boto3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import account_discovery as ad

TABLE = "aws-account-inventory"
REGION = "eu-central-1"


@pytest.fixture
def inventory_table():
    with mock_aws():
        client = boto3.resource("dynamodb", region_name=REGION)
        client.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "service_name", "KeyType": "HASH"},
                       {"AttributeName": "item_id", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "service_name", "AttributeType": "S"},
                                  {"AttributeName": "item_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield client.Table(TABLE)


def _row(service, account, region, ident, run_id):
    return {
        "service_name": service,
        "item_id": f"inventory#{account}#{region}#{ident}",
        "account_id": account,
        "region": region,
        "discovery_run_id": run_id,
        "provenance": ad.DISCOVERY_PROVENANCE,
        "status": "deprecated",
        "service_specific": {"identifier": ident, "affected_resource_details": []},
    }


def _all_item_ids(table, service):
    resp = table.query(
        KeyConditionExpression="service_name = :s",
        ExpressionAttributeValues={":s": service},
    )
    return {r["item_id"] for r in resp.get("Items", [])}


# Patch enrichment to no-ops so reconcile_from_table does not reach real AWS;
# the write/read/stale-delete path is what these tests exercise.
def _patch_enrichment():
    return [
        patch.object(ad, "collect_resource_tags", return_value={"available": False}),
        patch.object(ad, "cross_check_health", return_value={"available": False}),
        patch.object(ad, "estimate_cost_exposure", return_value={"available": False}),
    ]


# --- 6. save_scope_inventory writes only its own scope -----------------------

def test_save_scope_inventory_writes_only_its_scope(inventory_table):
    run_id = "run-A"
    items_a = [_row("lambda", "111111111111", REGION, f"fn-{i}", None) for i in range(3)]
    items_b = [_row("eks", "222222222222", REGION, f"c-{i}", None) for i in range(2)]

    out_a = ad.save_scope_inventory(items_a, run_id=run_id, region=REGION)
    out_b = ad.save_scope_inventory(items_b, run_id=run_id, region=REGION)

    assert out_a == {"saved": 3}
    assert out_b == {"saved": 2}
    assert "needs_attention" not in out_a

    for service, account, n in (("lambda", "111111111111", 3), ("eks", "222222222222", 2)):
        resp = inventory_table.query(
            KeyConditionExpression="service_name = :s",
            ExpressionAttributeValues={":s": service})
        rows = resp["Items"]
        assert len(rows) == n
        assert all(r["discovery_run_id"] == run_id for r in rows)
        assert all(r["provenance"] == ad.DISCOVERY_PROVENANCE for r in rows)
        assert all(r["account_id"] == account for r in rows)


# --- 7. save_scope_inventory refuses the facts table -------------------------

def test_save_scope_inventory_refuses_the_facts_table(inventory_table, monkeypatch):
    monkeypatch.setenv("LIFECYCLE_TABLE_NAME", TABLE)
    out = ad.save_scope_inventory([_row("lambda", "1", REGION, "fn", None)],
                                  run_id="r", table_name=TABLE, region=REGION)
    assert out["success"] is False
    assert "facts table" in out["error"]


# --- 8. reconcile removes only in-scope stale rows (+ pagination) ------------

def test_reconcile_from_table_removes_only_in_scope_stale_rows(inventory_table):
    acct_a, acct_b = "111111111111", "222222222222"
    run_old, run_new = "run-old", "run-new"

    # Scope A: a stale old-run row and > PAGE_SIZE fresh rows (exercises LastEvaluatedKey).
    with inventory_table.batch_writer() as b:
        b.put_item(Item=_row("lambda", acct_a, REGION, "stale-A", run_old))
        for i in range(ad.PAGE_SIZE + 25):
            b.put_item(Item=_row("lambda", acct_a, REGION, f"fresh-A-{i}", run_new))
        # Scope B: old-run rows that must survive (B is not in scope this run).
        b.put_item(Item=_row("eks", acct_b, REGION, "stale-B", run_old))

    scanned_scopes = [{"account_id": acct_a, "region": REGION, "service_keys": ["lambda"]}]

    # Spy on query to prove the LastEvaluatedKey loop iterated more than once.
    real_query = inventory_table.query
    calls = {"n": 0}

    def _counting_query(*a, **k):
        calls["n"] += 1
        return real_query(*a, **k)

    patches = _patch_enrichment()
    with patches[0], patches[1], patches[2], \
         patch.object(ad, "_inventory_table", return_value=(_SpyTable(inventory_table, _counting_query), TABLE)):
        totals = ad.reconcile_from_table(run_new, scanned_scopes, table_name=TABLE, region=REGION)

    assert totals["success"] is True
    assert calls["n"] > 1  # paginated: more than one Query request
    # Scope A: the stale old-run row is gone, the fresh rows survive.
    remaining_a = _all_item_ids(inventory_table, "lambda")
    assert f"inventory#{acct_a}#{REGION}#stale-A" not in remaining_a
    assert len(remaining_a) == ad.PAGE_SIZE + 25
    # Scope B: untouched (blast-radius: a scope that did not scan is left intact).
    remaining_b = _all_item_ids(inventory_table, "eks")
    assert f"inventory#{acct_b}#{REGION}#stale-B" in remaining_b


class _SpyTable:
    """Wraps a moto Table, swapping query() for a counting proxy; everything
    else (batch_writer, put/delete) passes straight through."""
    def __init__(self, table, query_fn):
        self._table = table
        self.query = query_fn

    def __getattr__(self, name):
        return getattr(self._table, name)


# --- 9. a failed scope's inventory is left intact (reconcile_inventory level) -

def test_failed_scope_inventory_is_left_intact(inventory_table):
    acct_a, acct_b = "111111111111", "222222222222"
    with inventory_table.batch_writer() as b:
        b.put_item(Item=_row("lambda", acct_a, REGION, "a-1", "old"))
        b.put_item(Item=_row("eks", acct_b, REGION, "b-1", "old"))

    # Only scope A scanned successfully this run; scope B (eks) failed and is
    # not in scanned_scopes -> its rows must survive.
    scanned_scopes = [{"account_id": acct_a, "region": REGION, "service_keys": ["lambda"]}]
    patches = _patch_enrichment()
    with patches[0], patches[1], patches[2], \
         patch.object(ad, "_inventory_table", return_value=(inventory_table, TABLE)):
        ad.reconcile_from_table("new", scanned_scopes, table_name=TABLE, region=REGION)

    assert f"inventory#{acct_b}#{REGION}#b-1" in _all_item_ids(inventory_table, "eks")


# --- 10. enrichment runs exactly once over the read-back rows ----------------

def test_reconcile_runs_enrichment_once_over_read_back_rows(inventory_table):
    run_id = "run-1"
    with inventory_table.batch_writer() as b:
        b.put_item(Item=_row("lambda", "111111111111", REGION, "a", run_id))
        b.put_item(Item=_row("eks", "222222222222", REGION, "b", run_id))

    scanned_scopes = [
        {"account_id": "111111111111", "region": REGION, "service_keys": ["lambda"]},
        {"account_id": "222222222222", "region": REGION, "service_keys": ["eks"]},
    ]

    seen = {}
    def _rec(name):
        def f(items, scanned_scopes=None):
            seen[name] = seen.get(name, 0) + 1
            seen[name + "_rows"] = len(items)
            return {"available": False}
        return f
    def _rec_cost(items):
        seen["cost"] = seen.get("cost", 0) + 1
        seen["cost_rows"] = len(items)
        return {"available": False}

    with patch.object(ad, "collect_resource_tags", side_effect=_rec("tags")), \
         patch.object(ad, "cross_check_health", side_effect=_rec("health")), \
         patch.object(ad, "estimate_cost_exposure", side_effect=_rec_cost), \
         patch.object(ad, "_inventory_table", return_value=(inventory_table, TABLE)):
        ad.reconcile_from_table(run_id, scanned_scopes, table_name=TABLE, region=REGION)

    assert seen["tags"] == 1 and seen["health"] == 1 and seen["cost"] == 1
    # each ran over the whole read-back working set (both scopes' rows), not per scope
    assert seen["tags_rows"] == 2 and seen["health_rows"] == 2 and seen["cost_rows"] == 2


# --- 11. each control row is written exactly once per run --------------------

def test_each_control_row_written_once_per_run(inventory_table):
    run_id = "run-1"
    with inventory_table.batch_writer() as b:
        b.put_item(Item=_row("lambda", "111111111111", REGION, "a", run_id))
        b.put_item(Item=_row("eks", "222222222222", REGION, "b", run_id))
        b.put_item(Item=_row("rds", "111111111111", REGION, "c", run_id))

    scanned_scopes = [
        {"account_id": "111111111111", "region": REGION, "service_keys": ["lambda"]},
        {"account_id": "222222222222", "region": REGION, "service_keys": ["eks"]},
        {"account_id": "111111111111", "region": REGION, "service_keys": ["rds", "aurora"]},
    ]

    control_writes = []
    with patch.object(ad, "_save_control_row", side_effect=lambda key, status: control_writes.append(key)), \
         patch.object(ad, "fetch_user_tags", return_value={}), \
         patch.object(ad, "session_for_account", return_value=None), \
         patch.object(ad, "_caller_identity", return_value={"partition": "aws", "account": "111111111111"}), \
         patch.object(ad, "_inventory_table", return_value=(inventory_table, TABLE)), \
         patch("health_match.match_health_events", return_value={"available": False, "reason": "test",
                                                                 "checked_at": None, "events": 0, "entities": {}}), \
         patch("health_match.health_client", return_value=None), \
         patch("health_match.support_tier", return_value={"tier": "basic"}):
        ad.reconcile_from_table(run_id, scanned_scopes, table_name=TABLE, region=REGION)

    # Each of the three control rows is written exactly once for the whole run
    # (protects HIGH-1: a per-scope regression would write them multiple times).
    assert control_writes.count(ad.TAGS_STATUS_KEY) == 1
    assert control_writes.count(ad.HEALTH_STATUS_KEY) == 1
    assert control_writes.count(ad.COST_STATUS_KEY) == 1
