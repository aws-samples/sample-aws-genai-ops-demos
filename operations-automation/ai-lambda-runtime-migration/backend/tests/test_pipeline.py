"""Unit tests for the durable pipeline's pure surface (design §10).

The durable runtime itself (checkpointing against the Lambda service) is not
driven here: aws-durable-execution-sdk-python 2.0.0 does not ship the named
LocalDurableTestRunner / DurableFunctionTestRunner, and the decorated handler
requires the Lambda checkpoint service. These tests cover everything that is
unit-testable off the runtime: spec validation (field-named errors incl. region
over-cap rejection), cell building, and the map-folding that implements
"one failed item does not fail the run" (CompletionConfig tolerated failure).
"""
import pytest

from aws_durable_execution_sdk_python.concurrency.models import BatchItemStatus

import pipeline
from pipeline import (
    normalize_spec, SpecValidationError, discover_cells_for,
    summarize_discover, summarize_assess, select_candidates, MAX_REGIONS,
)


# --- fakes ------------------------------------------------------------------

class _Item:
    def __init__(self, index, status, result):
        self.index, self.status, self.result = index, status, result


class _Batch:
    """Minimal stand-in for the SDK BatchResult: exposes .all of items."""
    def __init__(self, items):
        self.all = items


def _ok(index, result):
    return _Item(index, BatchItemStatus.SUCCEEDED, result)


def _failed(index):
    return _Item(index, BatchItemStatus.FAILED, None)


# --- normalize_spec ---------------------------------------------------------

def test_normalize_spec_defaults_empty_scope():
    spec = normalize_spec({})
    assert spec["tags"] == []
    assert spec["accounts"] == []
    # Empty regions means "the single deploy region": normalize_spec leaves it
    # empty and start_run defaults it to the own region. Trusted Advisor is
    # account-wide, so one cell per account covers every region. An explicit
    # list from the caller is still honored (see the full-scope test).
    assert spec["regions"] == []
    assert spec["refresh_origin"] == "manual"


def test_normalize_spec_valid_full_scope():
    spec = normalize_spec({
        "scope": {
            "tags": [{"key": "BU", "values": ["payments"]}],
            "regions": ["us-east-1", "eu-west-1"],
            "accounts": [{"id": "123456789012", "name": "prod"}],
        },
        "refresh_origin": "manual",
    })
    assert spec["tags"] == [{"key": "BU", "values": ["payments"]}]
    assert spec["accounts"] == [{"id": "123456789012", "name": "prod"}]
    assert spec["regions"] == ["us-east-1", "eu-west-1"]


def test_normalize_spec_rejects_region_overcap():
    regions = [f"us-east-{i}" for i in range(1, MAX_REGIONS + 2)]  # 6 valid-shaped tokens
    with pytest.raises(SpecValidationError) as exc:
        normalize_spec({"scope": {"regions": regions}})
    assert exc.value.field == "scope.regions"
    assert "at most" in exc.value.reason


def test_normalize_spec_rejects_bad_region_token():
    with pytest.raises(SpecValidationError) as exc:
        normalize_spec({"scope": {"regions": ["not_a_region"]}})
    assert exc.value.field == "scope.regions[0]"


def test_normalize_spec_rejects_bad_account_id():
    with pytest.raises(SpecValidationError) as exc:
        normalize_spec({"scope": {"accounts": [{"id": "123"}]}})
    assert exc.value.field == "scope.accounts[0].id"


def test_normalize_spec_rejects_empty_tag_key():
    with pytest.raises(SpecValidationError) as exc:
        normalize_spec({"scope": {"tags": [{"key": "", "values": []}]}})
    assert exc.value.field == "scope.tags[0].key"


def test_normalize_spec_rejects_non_string_tag_values():
    with pytest.raises(SpecValidationError) as exc:
        normalize_spec({"scope": {"tags": [{"key": "env", "values": [1, 2]}]}})
    assert exc.value.field == "scope.tags[0].values"


def test_normalize_spec_rejects_unknown_origin():
    with pytest.raises(SpecValidationError) as exc:
        normalize_spec({"refresh_origin": "whenever"})
    assert exc.value.field == "refresh_origin"


# --- discover_cells_for -----------------------------------------------------

def test_discover_cells_hub_only_when_hub_is_the_sole_candidate():
    # The hub-only single-account default is resolved upstream (start_run), so
    # the hub reaches discover_cells_for as an eligible candidate with id "".
    spec = normalize_spec({"scope": {"regions": ["us-east-1"]}})
    cells = discover_cells_for(spec, spec["regions"], [{"id": "", "name": ""}])
    assert len(cells) == 1
    assert cells[0]["account_id"] == ""
    assert cells[0]["region"] == "us-east-1"


def test_discover_cells_empty_candidates_scans_nothing():
    # No TA-eligible account => zero discover cells (never a silent hub fallback).
    spec = normalize_spec({"scope": {"regions": ["us-east-1"]}})
    assert discover_cells_for(spec, spec["regions"], []) == []


def test_discover_cells_cross_product():
    spec = normalize_spec({"scope": {
        "regions": ["us-east-1", "eu-west-1"],
        "accounts": [{"id": "111111111111"}, {"id": "222222222222"}],
    }})
    cells = discover_cells_for(spec, spec["regions"], spec["accounts"])
    assert len(cells) == 4
    assert {(c["account_id"], c["region"]) for c in cells} == {
        ("111111111111", "us-east-1"), ("111111111111", "eu-west-1"),
        ("222222222222", "us-east-1"), ("222222222222", "eu-west-1"),
    }


# --- map folding: one failed item does not fail the run ---------------------

def test_summarize_discover_tolerates_one_failed_cell():
    cells = [
        {"account_id": "", "region": "us-east-1"},
        {"account_id": "", "region": "eu-west-1"},
    ]
    batch = _Batch([
        _ok(0, {"account_id": "", "discovered": 3}),
        _failed(1),  # the eu-west-1 cell failed
    ])
    summary = summarize_discover(cells, batch)
    assert summary["cells_total"] == 2
    assert summary["cells_succeeded"] == 1
    assert summary["discovered"] == 3
    assert summary["failed_cells"] == ["hub/eu-west-1"]
    # Only the successful scope is handed to Assess.
    assert summary["scanned_scopes"] == [{"account_id": "", "region": "us-east-1"}]


def test_summarize_discover_reports_failed_accounts():
    cells = [
        {"account_id": "111111111111", "region": "us-east-1"},
        {"account_id": "222222222222", "region": "us-east-1"},
    ]
    batch = _Batch([_ok(0, {"account_id": "111111111111", "discovered": 1}), _failed(1)])
    summary = summarize_discover(cells, batch)
    assert summary["accounts_scanned"] == ["111111111111"]
    assert summary["accounts_failed"] == ["222222222222"]


def test_summarize_assess_counts_assessed_skipped_failed():
    targets = [{"item_key": "a"}, {"item_key": "b"}, {"item_key": "c"}]
    batch = _Batch([
        _ok(0, {"status": "ASSESSED"}),
        _ok(1, {"status": "SKIPPED"}),
        _failed(2),
    ])
    summary = summarize_assess(targets, batch)
    assert summary == {"total": 3, "assessed": 1, "skipped": 1, "failed": 1}


# --- select_candidates: Support-tier fold (eligible kept, rest reported) -----

def _tier(account_id, name, tier, eligible, reason=None):
    return {"account_id": account_id, "account_name": name, "tier": tier,
            "ta_eligible": eligible, "reason": reason}


def test_select_candidates_filters_to_ta_eligible():
    accounts = [{"id": "111111111111", "name": "biz"},
                {"id": "222222222222", "name": "basic"}]
    batch = _Batch([
        _ok(0, _tier("111111111111", "biz", "business", True)),
        _ok(1, _tier("222222222222", "basic", "basic", False)),
    ])
    sel = select_candidates(accounts, batch)
    # Only the eligible account becomes a discover candidate...
    assert [c["id"] for c in sel["candidates"]] == ["111111111111"]
    assert sel["candidates"][0]["tier"] == "business"
    # ...but BOTH accounts survive in tiers so the summary can report them.
    assert len(sel["tiers"]) == 2
    assert sel["support_errors"] == []


def test_select_candidates_keeps_non_eligible_for_not_checked():
    accounts = [{"id": "222222222222", "name": "dev"}]
    batch = _Batch([_ok(0, _tier("222222222222", "dev", "developer", False))])
    sel = select_candidates(accounts, batch)
    assert sel["candidates"] == []            # not scanned
    assert sel["tiers"][0]["ta_eligible"] is False
    assert sel["tiers"][0]["tier"] == "developer"  # still reported downstream


def test_select_candidates_isolates_support_api_error():
    """A failed support unit (None result) is isolated: recorded in
    support_errors, reported in tiers as unknown, and NOT treated as a
    candidate — never raised to fail the run."""
    accounts = [{"id": "111111111111", "name": "ok"},
                {"id": "333333333333", "name": "boom"}]
    batch = _Batch([
        _ok(0, _tier("111111111111", "ok", "enterprise", True)),
        _failed(1),  # Support API errored on this account every retry
    ])
    sel = select_candidates(accounts, batch)
    assert [c["id"] for c in sel["candidates"]] == ["111111111111"]
    assert sel["support_errors"] == ["333333333333"]
    boom = next(t for t in sel["tiers"] if t["account_id"] == "333333333333")
    assert boom["ta_eligible"] is False
    assert boom["tier"] == "unknown"


def test_select_candidates_hub_error_labeled_hub():
    accounts = [{"id": "", "name": ""}]
    batch = _Batch([_failed(0)])
    sel = select_candidates(accounts, batch)
    assert sel["support_errors"] == ["hub"]
    assert sel["candidates"] == []


def test_discover_cells_carry_tier_from_candidate():
    spec = normalize_spec({"scope": {"regions": ["us-east-1"]}})
    cells = discover_cells_for(spec, ["us-east-1"],
                               [{"id": "111111111111", "name": "biz", "tier": "business"}])
    assert cells[0]["support_tier"] == "business"


# --- summarize_step: rollup failure is logged, never raised -----------------

def _run_step(step_fn, *args):
    """Execute a @durable_step body off the runtime (fake StepContext)."""
    from unittest.mock import MagicMock
    return step_fn(*args)(MagicMock())


def test_summarize_step_logs_rollup_failure_and_does_not_raise(monkeypatch, caplog):
    """The rollup write used to fail silently (except: pass) on EVERY scan, so the
    Statistics series stayed empty with no trace. It must still never fail the
    run, but it must leave an ERROR line with the traceback."""
    import logging

    def _boom():
        raise RuntimeError("ValidationException: Empty attribute name")
    monkeypatch.setattr(pipeline.discover, "read_fleet_inventory", _boom)
    run = {"run_id": "r1", "started_at": "t0", "regions": ["us-east-1"],
           "accounts": [{"id": "111111111111", "name": "hub"}]}
    spec = {"refresh_origin": "manual"}
    with caplog.at_level(logging.ERROR, logger="pipeline"):
        out = _run_step(pipeline.summarize_step, run, spec, {}, {}, {"tiers": []})
    assert out["run_id"] == "r1"
    assert out["accounts"] == ["111111111111"]
    assert any("rollup write failed" in rec.getMessage() and rec.exc_info for rec in caplog.records)


def test_summarize_step_rollup_carries_migration_run_axis(monkeypatch):
    """The fleet snapshot the step hands to build_rollup includes the mig# items,
    so the persisted rollup carries the run_* keys the Statistics page draws."""
    arn = "arn:aws:lambda:us-east-1:111111111111:function:f"
    snapshot = [
        {"item_key": f"fn#111111111111#us-east-1#{arn}", "function_arn": arn,
         "account_id": "111111111111", "region": "us-east-1", "runtime": "python3.8",
         "alert_status": "Red", "migration_status": "ASSESSED"},
        {"item_key": f"mig#111111111111#us-east-1#{arn}", "function_arn": arn,
         "migration_status": "BRANCH_READY"},
    ]
    written = []
    monkeypatch.setattr(pipeline.discover, "read_fleet_inventory", lambda: snapshot)
    monkeypatch.setattr(pipeline.discover, "put_rollup", lambda record: written.append(record))
    run = {"run_id": "r1", "started_at": "t0", "regions": ["us-east-1"],
           "accounts": [{"id": "111111111111", "name": "hub"}]}
    _run_step(pipeline.summarize_step, run, {"refresh_origin": "manual"}, {}, {}, {"tiers": []})
    assert len(written) == 1
    assert written[0]["total_active"] == 1
    assert written[0]["run_branches_ready"] == 1
    assert written[0]["run_not_started"] == 0


def test_summarize_discover_sums_rekeyed_rows():
    cells = [{"account_id": "111111111111", "region": "us-east-1"}]
    batch = _Batch([_ok(0, {"account_id": "111111111111", "discovered": 3,
                            "regions_seen": ["us-east-1"], "rekeyed": 2})])
    assert summarize_discover(cells, batch)["rekeyed"] == 2
