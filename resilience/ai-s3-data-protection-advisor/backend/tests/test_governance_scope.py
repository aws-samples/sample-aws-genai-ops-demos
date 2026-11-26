"""Governance signals that were not read (AWS Organizations) or have nothing in scope are unknown, never failed."""
import json
from pathlib import Path

from s3dp.derive import inventory
from s3dp.engine import scoring

RAW = json.loads((Path(__file__).parent / "fixtures" / "raw_small.json").read_text())


def _raw():
    return json.loads(json.dumps(RAW))


def _gov(inv):
    data, _ = scoring.build(inv)
    return next(t for t in data["tabs"] if t["id"] == "governance")


def _dim(gov, k):
    return next(d for d in gov["dimensions"] if d["id"] == k)


def test_organizations_not_read_is_none_in_the_inventory():
    assert inventory.build(_raw())["governance"]["organizations"] is None


def test_organizations_not_read_is_unknown_not_a_gap():
    inv = inventory.build(_raw())
    gov = _gov(inv)
    prev = _dim(gov, "preventive")
    assert any("SCPs: not readable from this account" in e for e in prev["evidence"])
    assert any("Organization backup policies: not readable from this account" in e for e in prev["evidence"])
    assert not any(f["title"] == "Nothing stops a protection from being turned off" for f in gov["findings"])
    # account-level BPA is the only known guard (1 of 4): too little evidence, the dimension is not assessed
    assert prev["level"] is None
    own = _dim(gov, "ownership")
    assert any("Tag policies: not readable from this account" in e for e in own["evidence"])


def test_known_missing_scp_guard_raises_the_finding_without_alert_claim():
    inv = inventory.build(_raw())
    inv["governance"]["organizations"] = {"enabled": True, "scps": [], "backup_policies": 0, "tag_policies": []}
    gov = _gov(inv)
    f = next(f for f in gov["findings"] if f["title"] == "Nothing stops a protection from being turned off")
    assert "alert" not in f["evidence"]
    assert _dim(gov, "preventive")["level"] == 2


def test_no_backup_targets_does_not_lower_recovery():
    inv = inventory.build(_raw())
    assert not [b for b in inv["buckets"] if b["role"] == "backup_target"]
    inv["backup_posture"]["vaults"][0]["locked"] = True
    inv["backup_posture"]["plans_with_copy_actions"] = 1
    inv["governance"]["restore_tests_last_12_months"] = 1
    gov = _gov(inv)
    rec = _dim(gov, "recovery")
    assert rec["level"] == 5
    assert "No backup targets in scope" in rec["evidence"]
    pol = next(p for p in gov["policies"] if p["rule"] == "Backup targets are immutable (Object Lock)")
    assert pol["total"] == 0 and pol["pct"] is None


def test_recovery_with_only_the_immutability_check_known_is_not_failed_by_empty_scope():
    inv = inventory.build(_raw())
    inv["backup_posture"]["vaults"] = None
    inv["backup_posture"]["plans_with_copy_actions"] = None
    inv["governance"]["restore_tests_last_12_months"] = None
    rec = _dim(_gov(inv), "recovery")
    assert rec["level"] is None and rec["current"].startswith("Not assessed")


def test_no_production_buckets_does_not_score_the_standard():
    inv = inventory.build(_raw())
    for b in inv["buckets"]:
        if b["role"] == "production":
            b["role"] = "non_production"
    gov = _gov(inv)
    std = _dim(gov, "standard")
    assert std["level"] is None and "No production buckets in scope" in std["evidence"]
    assert std["current"].startswith("Not assessed")
    levels = [d["level"] for d in gov["dimensions"] if d["level"] is not None]
    assert gov["overall"] == round(sum(levels) / len(levels), 1)


def test_no_readable_tags_does_not_score_ownership_or_iac():
    inv = inventory.build(_raw())
    for b in inv["buckets"]:
        b["tags_read"] = False
    gov = _gov(inv)
    assert _dim(gov, "ownership")["level"] is None
    assert _dim(gov, "iac")["level"] is None
    assert any("no in-scope bucket with readable tags" in e for e in _dim(gov, "ownership")["evidence"])


def test_not_assessed_dimension_text_is_not_sent_for_rewrite():
    from s3dp.ai import narrative
    inv = inventory.build(_raw())
    for b in inv["buckets"]:
        if b["role"] == "production":
            b["role"] = "non_production"
    data, _ = scoring.build(inv)
    assert not any(k.startswith("governance.standard.") for k in narrative._targets(data))
