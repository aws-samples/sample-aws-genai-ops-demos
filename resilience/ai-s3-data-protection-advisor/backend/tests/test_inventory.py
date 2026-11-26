import json
from pathlib import Path

from s3dp.derive import inventory

RAW = json.loads((Path(__file__).parent / "fixtures" / "raw_small.json").read_text())


def test_inventory_shape_and_derivations():
    inv = inventory.build(RAW)
    by = {b["name"]: b for b in inv["buckets"]}
    assert inv["hub_account"] == "111122223333" and inv["customer"] == "acme-main"
    assert by["orders-prod-eu-west-1"]["role"] == "production"
    assert by["orders-prod-eu-west-1"]["aws_backup"] == "daily"
    assert by["acme-access-logs"]["role"] == "access_logs"
    assert by["blob-store-42"]["role"] == "unknown" and by["blob-store-42"]["role_source"] == "default"
    assert by["orders-prod-us-east-1"]["vce"]["basis"] == "prior"
    assert any(f["check"] == "S3-BKP-01" for f in by["orders-prod-us-east-1"]["findings"])
    assert inv["third_party"][0]["vendor"] == "Veeam"
    assert inv["backup_posture"]["opt_in_off"] == [["111122223333", "us-east-1"]]
    assert inv["governance"]["iac_managed"] == ["orders-prod-eu-west-1"]
    assert inv["governance"]["tags"]["owner"] == ["orders-prod-eu-west-1"]
    assert inv["accounts"]["111122223333"] == {"name": "acme-main", "account_bpa": "on"}


def test_ai_roles_override_unknown_only():
    inv = inventory.build(RAW, ai_roles={"blob-store-42": ("production", "Name looks like a shared data store", "medium"),
                                         "orders-prod-eu-west-1": ("tooling", "ignored", "high")})
    by = {b["name"]: b for b in inv["buckets"]}
    assert by["blob-store-42"]["role"] == "production" and by["blob-store-42"]["role_source"] == "ai"
    assert by["orders-prod-eu-west-1"]["role"] == "production"


def test_undecided_lists_buckets_for_ai():
    assert [x["name"] for x in inventory.undecided(RAW)] == ["blob-store-42"]


def _raw_with(name, **metrics):
    raw = json.loads(json.dumps(RAW))
    item = next(b for b in raw["buckets"] if b["name"] == "orders-prod-us-east-1")
    item["name"] = name
    item["metrics"].update(metrics)
    raw["buckets"] = [item]
    return raw


def test_static_needs_size_series_in_inventory():
    flat = _raw_with("orders-prod-us-east-1", objects_series=[500, 500, 500])
    b = inventory.build(flat)["buckets"][0]
    assert b["vce"]["churn_pct"] == 10.0 and b["write_pattern"].startswith("Unknown")
    flat["buckets"][0]["metrics"]["size_series"] = [549755813888] * 3
    b = inventory.build(flat)["buckets"][0]
    assert b["vce"]["churn_pct"] == 0 and b["write_pattern"].startswith("Static")
    assert b["vce"]["confidence"] == "medium"


def test_regional_required_uses_whole_word_matching():
    by = {b["name"]: b for b in inventory.build(_raw_with("chrome-prod"))["buckets"]}
    assert by["chrome-prod"]["regional_required"] is False
    by = {b["name"]: b for b in inventory.build(_raw_with("hr-prod"))["buckets"]}
    assert by["hr-prod"]["regional_required"] is True


def test_new_normalized_fields_flow_through():
    by = {b["name"]: b for b in inventory.build(RAW)["buckets"]}
    assert by["orders-prod-eu-west-1"]["aws_backup_plans"] == ["daily"]
    assert by["orders-prod-eu-west-1"]["tags_read"] is True


def _raw_backup_unreadable():
    raw = json.loads(json.dumps(RAW))
    raw["regions"]["111122223333"]["eu-west-1"]["backup"].update(
        protected_s3=None, plans_total=None, plans_with_copy_actions=None, vaults=None, restore_testing_plans=None,
        frameworks=None, errors={"protected_resources": "AccessDeniedException"})
    return raw


def test_unreadable_aws_backup_is_not_verified_never_none():
    inv = inventory.build(_raw_backup_unreadable())
    by = {b["name"]: b for b in inv["buckets"]}
    assert by["orders-prod-eu-west-1"]["aws_backup"] == "NotVerified"
    assert by["orders-prod-eu-west-1"]["aws_backup_plans"] is None
    assert by["orders-prod-us-east-1"]["aws_backup"] == "None"          # us-east-1 was read
    p = inv["backup_posture"]
    assert p["vaults"] is None and p["plans_total"] is None and p["plans_with_copy_actions"] is None
    assert p["restore_testing_plans"] is None and p["air_gapped_vault_present"] is None
    assert inv["governance"]["backup_audit_manager_frameworks"] is None
    assert inv["governance"]["restore_tests_last_12_months"] is None
    assert p["unreadable_regions"] == [["111122223333", "eu-west-1", [
        "vaults", "plans_total", "plans_with_copy_actions", "restore_testing_plans", "frameworks"]]]


def test_region_without_backup_data_is_unreadable():
    raw = json.loads(json.dumps(RAW))
    del raw["regions"]["111122223333"]["us-east-1"]["backup"]
    inv = inventory.build(raw)
    by = {b["name"]: b for b in inv["buckets"]}
    assert by["orders-prod-us-east-1"]["aws_backup"] == "NotVerified"
    assert inv["backup_posture"]["plans_total"] is None
    assert inv["backup_posture"]["opt_in_unknown"] == [["111122223333", "us-east-1"]]


def test_unreadable_config_is_unknown_not_absent():
    raw = json.loads(json.dumps(RAW))
    raw["accounts"]["222233334444"] = dict(raw["accounts"]["111122223333"], name="acme-other", config=None)
    raw["accounts"]["111122223333"]["config"] = {"recording": True, "s3_rules": None}
    cfg = inventory.build(raw)["governance"]["config"]
    assert cfg["recording_accounts"] == ["111122223333"] and cfg["recording_unknown"] == ["222233334444"]
    assert cfg["s3_rules"] == [] and sorted(cfg["s3_rules_unknown"]) == ["111122223333", "222233334444"]
    assert cfg["auto_remediation"] is None


def test_empty_account_backup_posture_is_not_readable():
    raw = {"scan_date": "2026-11-25T10:00:00Z", "hub_account": "111122223333",
           "accounts": {"111122223333": {"name": "empty", "account_bpa": "on", "role_names": [], "stack_names": [],
                                         "config": {"recording": None, "s3_rules": None}, "security_hub": None}},
           "regions": {"111122223333": {}}, "buckets": [], "organization": None, "run": {}}
    inv = inventory.build(raw)
    assert all(v is None for v in inv["backup_posture"].values()), inv["backup_posture"]
    assert inv["governance"]["backup_audit_manager_frameworks"] is None
    from s3dp.engine import scoring
    data, _ = scoring.build(inv)
    ops = next(t for t in data["tabs"] if t["id"] == "operational-review")["resources"]
    pillar = next(r for r in ops if r["name"] == "AWS Backup posture")["pillars"][0]
    assert pillar["total"] == 0 and all(c.get("unknown") for c in pillar["checks"])


def _undecided_raw(n):
    raw = json.loads(json.dumps(RAW))
    item = next(b for b in raw["buckets"] if b["name"] == "blob-store-42")
    raw["buckets"] = [dict(json.loads(json.dumps(item)), name=f"blob-store-{i:04d}") for i in range(n)]
    return raw


def test_low_confidence_ai_answer_never_excludes_a_bucket():
    raw = json.loads(json.dumps(RAW))
    ai = {"blob-store-42": ("non_production", "Looks like a scratch bucket", "low")}
    b = next(x for x in inventory.build(raw, ai)["buckets"] if x["name"] == "blob-store-42")
    assert b["role"] == "unknown" and b["role_source"] == "default"
    assert "generative AI assistant" in b["role_reason"] and "low confidence" in b["role_reason"]
    ai = {"blob-store-42": ("non_production", "Looks like a scratch bucket", "medium")}
    b = next(x for x in inventory.build(raw, ai)["buckets"] if x["name"] == "blob-store-42")
    assert b["role"] == "non_production" and b["role_source"] == "ai"
    ai = {"blob-store-42": ("production", "Order data", "low")}       # a protective low-confidence role is kept
    b = next(x for x in inventory.build(raw, ai)["buckets"] if x["name"] == "blob-store-42")
    assert b["role"] == "production"


def test_undecided_assigns_roles_once_per_account(monkeypatch):
    import time
    from s3dp.derive import roles
    calls = []
    real = roles.assign
    monkeypatch.setattr(roles, "assign", lambda *a, **k: calls.append(1) or real(*a, **k))
    raw = _undecided_raw(2000)
    start = time.perf_counter()
    out = inventory.undecided(raw)
    assert time.perf_counter() - start < 2
    assert len(out) == 2000 and len(calls) == 1


def test_versioning_cost_only_for_unversioned_or_suspended_buckets():
    raw = json.loads(json.dumps(RAW))
    by_name = {x["name"]: x for x in raw["buckets"]}
    by_name["blob-store-42"]["metrics"] = by_name["orders-prod-us-east-1"]["metrics"]     # sized, versioning unread
    b = {x["name"]: x for x in inventory.build(raw)["buckets"]}
    assert b["blob-store-42"]["versioning"] == "NotVerified" and b["blob-store-42"]["vce"] is None
    assert b["orders-prod-us-east-1"]["versioning"] == "Never enabled" and b["orders-prod-us-east-1"]["vce"]
