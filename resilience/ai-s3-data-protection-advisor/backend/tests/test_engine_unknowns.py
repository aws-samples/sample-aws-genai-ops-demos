"""Settings that could not be read are unknown in the engine: never absent, never a finding, never a gap."""
import json
from pathlib import Path

from s3dp.derive import inventory
from s3dp.engine import scoring

RAW = json.loads((Path(__file__).parent / "fixtures" / "raw_small.json").read_text())


def _raw():
    return json.loads(json.dumps(RAW))


def _backup_unreadable(raw):
    for r in raw["regions"]["111122223333"].values():
        r["backup"].update(protected_s3=None, plans_total=None, plans_with_copy_actions=None, vaults=None,
                           restore_testing_plans=None, frameworks=None, s3_opt_in=None)
    return raw


def _tab(data, tid):
    return next(t for t in data["tabs"] if t["id"] == tid)


def _rows(data):
    return {c["row"]["bucket"]: c["row"] for c in _tab(data, "resources")["cards"]}


def test_backup_not_verified_in_controls_matrix_and_evidence():
    inv = inventory.build(_backup_unreadable(_raw()))
    data, _ = scoring.build(inv)
    row = _rows(data)["orders-prod-eu-west-1"]
    backup = next(c for c in row["controls"] if c["control"].startswith("Backup copy"))
    assert all(cell == ["unknown", "Not verified"] for cell in backup["cells"].values())
    assert row["third_party"] == ""              # third-party sentence only when AWS Backup is known absent
    ev = {e["label"]: e["value"] for e in row["evidence"]}
    assert ev["AWS Backup"].startswith("Not verified")


def test_backup_not_verified_is_not_a_failed_exposure_check():
    inv = inventory.build(_backup_unreadable(_raw()))
    data, _ = scoring.build(inv)
    exposures = next(r for r in _tab(data, "operational-review")["resources"] if r["name"].startswith("Critical"))
    assert not any("backup coverage" in c["description"] for p in exposures["pillars"] for c in p["checks"])
    vals = scoring.setting_values(dict(inv["buckets"][0], role="production"), inv)
    assert vals["AWS Backup coverage"] == (None, "not verified")


def test_backup_posture_not_readable_raises_nothing():
    inv = inventory.build(_backup_unreadable(_raw()))
    data, _ = scoring.build(inv)
    ops = _tab(data, "operational-review")["resources"]
    pillar = next(r for r in ops if r["name"] == "AWS Backup posture")["pillars"][0]
    assert pillar["total"] == 0 and all(c.get("unknown") for c in pillar["checks"])
    assert all(c["label"].endswith("not readable") and "severity" not in c for c in pillar["checks"])
    recs = [r["recommendation"] for r in _tab(data, "recommendations")["items"]]
    assert not any("restore testing" in r or "Vault Lock" in r or "Opt S3 in" in r for r in recs)
    gov = _tab(data, "governance")
    assert not any(f["title"] == "Recoverability is assumed, not tested" for f in gov["findings"])
    rec = next(d for d in gov["dimensions"] if d["id"] == "recovery")
    assert "Vault Lock: not readable; plans with copy rules: not readable" in rec["evidence"]
    assert any("Restore testing plans: not readable" in e for e in rec["evidence"])
    det = next(d for d in gov["dimensions"] if d["id"] == "detective")
    assert "AWS Backup Audit Manager frameworks: not readable" in det["evidence"]
    second = next(p for p in gov["policies"] if p["rule"].startswith("Production data has a second copy"))
    assert "orders-prod-eu-west-1" in second.get("unconfirmed", []) and "orders-prod-eu-west-1" not in second["failing"]
    cov = next(s for s in _tab(data, "instructions")["sections"] if s["title"] == "Coverage and confidence")
    assert ["Rows with AWS Backup not verified", "4"] in cov["table"]["rows"]


def test_vault_with_unreadable_recovery_points_does_not_crash():
    raw = _raw()
    raw["regions"]["111122223333"]["eu-west-1"]["backup"]["vaults"][0]["recovery_points"] = None
    data, _ = scoring.build(inventory.build(raw))
    rec = next(r for r in _tab(data, "recommendations")["items"] if "Vault Lock" in r["recommendation"])
    assert "count not readable" in rec["why"]


def test_unreadable_config_says_not_readable_and_raises_no_finding():
    raw = _raw()
    raw["accounts"]["111122223333"]["config"] = None
    raw["accounts"]["111122223333"]["security_hub"] = None
    data, _ = scoring.build(inventory.build(raw))
    gov = _tab(data, "governance")
    det = next(d for d in gov["dimensions"] if d["id"] == "detective")
    assert det["evidence"][:3] == ["AWS Config recording in 0 of 1 accounts (not readable in 1)", "S3 rules: not readable",
                                   "Security Hub: not readable"]
    assert not any(f["title"] == "No continuous check of the data protection baseline" for f in gov["findings"])


def test_unknown_delete_marker_replication():
    inv = inventory.build(_raw())
    b = next(x for x in inv["buckets"] if x["name"] == "orders-prod-eu-west-1")
    b["replication"] = {"destination": "orders-prod-copy", "destination_region": "eu-central-1",
                        "delete_markers": None, "rtc": False}
    data, _ = scoring.build(inv)
    row = _rows(data)["orders-prod-eu-west-1"]
    crr = next(c for c in row["controls"] if c["control"] == "Cross-region replication")
    assert crr["cells"]["ma"] == ["unknown", "Delete-marker replication not verified"]
    ev = {e["label"]: e["value"] for e in row["evidence"]}
    assert ev["Replication"].endswith("delete-marker replication unknown")


# ---------- Real-scan behaviour ----------
def _gov(inv):
    data, _ = scoring.build(inv)
    return _tab(data, "governance")


def test_drift_between_iac_templates_is_not_called_outside_iac():
    from s3dp.engine import governance
    assert governance._drift_origin(["a", "b"], []) == \
        "all drifted buckets (a, b) are IaC-managed: the drift is between IaC templates."
    assert governance._drift_origin(["a", "b"], ["b"]) == \
        "drifted buckets (b) were created outside IaC; IaC-managed but drifted (templates differ): a."
    inv = inventory.build(_raw())
    inv["governance"]["iac_managed"] = [b["name"] for b in inv["buckets"]]
    f = next(x for x in _gov(inv)["findings"] if x["title"] == "Protection depends on who created the bucket")
    assert "between IaC templates" in f["evidence"] and "created outside IaC" not in f["evidence"]


def test_uncollectable_signals_do_not_lower_levels():
    inv = inventory.build(_raw())
    gov = _gov(inv)
    rec = next(d for d in gov["dimensions"] if d["id"] == "recovery")
    det = next(d for d in gov["dimensions"] if d["id"] == "detective")
    assert rec["evidence"][0] == "Central backup account: not collectable from configuration"
    assert "Automatic remediation: not collectable" in det["evidence"]
    # all collected recovery signals pass: the uncollectable central backup account does not cap the level
    inv["backup_posture"]["vaults"][0]["locked"] = True
    inv["backup_posture"]["plans_with_copy_actions"] = 1
    inv["governance"]["restore_tests_last_12_months"] = 1
    inv["buckets"] = [b for b in inv["buckets"] if b["role"] != "backup_target"]
    rec = next(d for d in _gov(inv)["dimensions"] if d["id"] == "recovery")
    assert rec["level"] == 5
    # detective: level 4 reachable from posture reporting while automatic remediation is unknown
    inv["governance"]["backup_audit_manager_frameworks"] = 2
    det = next(d for d in _gov(inv)["dimensions"] if d["id"] == "detective")
    assert det["level"] == 4
    inv["governance"]["config"]["auto_remediation"] = False
    det = next(d for d in _gov(inv)["dimensions"] if d["id"] == "detective")
    assert det["level"] == 3


def test_unknown_role_buckets_are_listed_not_counted():
    data, _ = scoring.build(inventory.build(_raw()))
    ops = _tab(data, "operational-review")["resources"]
    estate = ops[0]["pillars"]
    assert all("blob-store-42" not in str(p) for p in estate)
    assert sum(p["total"] for p in estate) == 3 * 2       # orders-prod-eu-west-1 and orders-prod-us-east-1 only
    acct = next(r for r in ops if r["name"].startswith("acme-main"))["pillars"][0]
    assert acct["total"] == 2 and not any(c["label"] == "blob-store-42" for c in acct["checks"])
    info = next(c for c in acct["checks"] if c["label"].startswith("Role to confirm"))
    assert info["label"] == "Role to confirm: 1 buckets" and info["severity"] == "INFO" and "blob-store-42" in info["description"]
    inv = inventory.build(_raw())
    assert all(m["name"] != "blob-store-42" for f in scoring.find_families(inv["buckets"], inv) for m in f["members"])


def test_instructions_without_grouping_and_vce_and_glossary():
    data, _ = scoring.build(inventory.build(_raw()))
    tab = _tab(data, "instructions")
    text = json.dumps(tab)
    assert "after grouping identical sets" not in text and "grouped sets" not in text
    assert "assumed from a pattern" not in text and "checked on a sample" not in text
    assert "Storage Lens" not in text
    vce = _rows(data)["orders-prod-us-east-1"]["vce"]
    assert vce.count("$") == 3, vce


def test_cost_saving_float_is_formatted_as_whole_dollars():
    inv = inventory.build(_raw())
    b = next(x for x in inv["buckets"] if x["name"] == "orders-prod-eu-west-1")
    b["cost_saving"] = {"monthly_usd": 1234.5678, "action": "Evaluate S3 Intelligent-Tiering (save up to ~$1,235/month)"}
    row = _rows(scoring.build(inv)[0])["orders-prod-eu-west-1"]
    assert "Cost saving: ~$1,235/month" in row["reasons"]
    assert "Save ~$1,235/month" in row["chips"]


# ---------- Consistency: other derive fields that can be unknown ----------
def test_unreadable_delete_deny_policy_is_not_a_missing_guard():
    inv = inventory.build(_raw())
    b = next(x for x in inv["buckets"] if x["name"] == "orders-prod-eu-west-1")
    b.update(policy={"delete_deny": None, "tls_only": None}, mfa_delete=False)
    row = _rows(scoring.build(inv)[0])["orders-prod-eu-west-1"]
    guard = next(c for c in row["controls"] if c["control"].startswith("Delete guard"))
    assert guard["cells"]["ma"] == ["unknown", "Not verified: delete-deny policy"] and guard["value"] == "not verified"


def test_inferred_replication_region_is_not_printed_as_none():
    inv = inventory.build(_raw())
    b = next(x for x in inv["buckets"] if x["name"] == "orders-prod-eu-west-1")
    b["replication"] = {"destination": "other-account-copy", "destination_region": None, "delete_markers": False,
                        "rtc": False, "inferred": True}
    data, _ = scoring.build(inv)
    assert "(None)" not in json.dumps(data) and "CRR → None" not in json.dumps(data)


def test_unknown_coverage_is_never_a_failed_exposure_or_rule():
    inv = inventory.build(_raw())
    b = next(x for x in inv["buckets"] if x["name"] == "orders-prod-us-east-1")
    b.update(versioning="NotVerified", object_lock="NotVerified", replication="NotVerified", findings=[])
    data, _ = scoring.build(inv)
    exposures = next(r for r in _tab(data, "operational-review")["resources"] if r["name"].startswith("Critical"))
    assert not any(c["label"].startswith("orders-prod-us-east-1") for p in exposures["pillars"] for c in p["checks"])
    pol = {p["rule"]: p for p in _tab(data, "governance")["policies"]}
    ver = pol["All production buckets have versioning enabled"]
    assert "orders-prod-us-east-1" in ver["unconfirmed"] and "orders-prod-us-east-1" not in ver["failing"]
    second = pol["Production data has a second copy (AWS Backup or cross-region replication)"]
    assert "orders-prod-us-east-1" in second["unconfirmed"]


def test_unreadable_account_bpa_and_tags_are_unconfirmed():
    inv = inventory.build(_raw())
    inv["accounts"]["111122223333"]["account_bpa"] = "unknown"
    for b in inv["buckets"]:
        b["tags_read"] = False
    data, _ = scoring.build(inv)
    acct = next(r for r in _tab(data, "operational-review")["resources"] if r["name"].startswith("acme-main"))
    public = acct["pillars"][1]
    assert public["total"] == 0 and public["checks"][0]["label"] == "Account-level Block Public Access: not readable"
    gov = _tab(data, "governance")
    pol = {p["rule"]: p for p in gov["policies"]}
    bpa = pol["Account-level Block Public Access is on in every account"]
    assert bpa["failing"] == [] and bpa["unconfirmed"] == ["111122223333 (acme-main)"]
    tags = pol["Every production bucket has an owner and a data-classification tag"]
    assert tags["failing"] == [] and tags["total"] == 0
    prev = next(d for d in gov["dimensions"] if d["id"] == "preventive")
    assert prev["evidence"][0] == "Account-level BPA on in 0 of 1 accounts (not readable in 1)"
    drift = next(f for f in gov["findings"] if f["title"] == "Protection depends on who created the bucket")
    assert "created outside IaC" not in drift["evidence"] and "tags not readable" in drift["evidence"]


def test_unreadable_tags_raise_no_tag_finding():
    inv = inventory.build(_raw())
    for b in inv["buckets"]:
        b["tags_read"] = False
    gov = _tab(scoring.build(inv)[0], "governance")
    titles = {f["title"] for f in gov["findings"]}
    assert "Many buckets have no accountable owner" not in titles
    assert "Data classification is not machine-readable" not in titles
