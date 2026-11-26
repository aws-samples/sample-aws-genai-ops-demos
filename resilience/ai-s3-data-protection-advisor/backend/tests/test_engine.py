import json
import re
from pathlib import Path

from s3dp.derive import inventory
from s3dp.engine import scoring

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = json.loads((ROOT / "sample-data" / "anycompany-inventory.json").read_text())
RAW = json.loads((Path(__file__).parent / "fixtures" / "raw_small.json").read_text())
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐❓❌✅]")
TABS = ["instructions", "resources", "operational-review", "recommendations", "inconsistencies", "governance",
        "cost-optimization"]


def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from strings(v)


def test_malicious_coverage_precedence():
    cases = [("Enabled", "None", "partial"), ("Enabled", "NotVerified", "partial"),
             ("Enabled", "Enabled — no default rule; retained objects not verified", "partial"),
             ("Enabled", "Enabled — per-object retention verified", "protected_lock"),
             ("Enabled", "GOVERNANCE 30d", "protected_governance"), ("Enabled", "COMPLIANCE 7y", "protected"),
             ("Never enabled", "NotVerified", "unprotected"), ("NotVerified", "NotVerified", "unknown")]
    for v, lock, expected in cases:
        assert scoring.malicious_coverage(v, lock) == expected


def test_sample_report_tabs_and_numbers():
    data, stats = scoring.build(SAMPLE)
    assert [t["id"] for t in data["tabs"]] == TABS
    assert stats["buckets"] == 29 and stats["exposures"] == 10 and stats["families"] == 5


def test_no_emoji_and_no_internal_wording_anywhere():
    data, _ = scoring.build(SAMPLE)
    text = list(strings(data))
    assert not [s for s in text if EMOJI.search(s)]
    for word in ("account team", "spotlight", "Opportunity", "with customer"):
        assert not [s for s in text if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", s)], word


def test_real_scan_inventory_scores():
    data, stats = scoring.build(inventory.build(RAW))
    rows = {c["row"]["bucket"]: c["row"] for c in data["tabs"][1]["cards"]}
    assert rows["orders-prod-us-east-1"]["status"] == "GAP — CONFIRM 3P"      # Veeam IAM role in the account
    assert rows["blob-store-42"]["he"] == "unknown" and rows["blob-store-42"]["status"] == "CONFIRM ROLE"
    assert rows["acme-access-logs"]["status"] == "LOG/TOOLING"
    assert rows["blob-store-42"]["size_human"] == "Not measured" and rows["blob-store-42"]["size_bytes"] is None
    inc = data["tabs"][4]["items"]
    assert any(r["setting"] == "Versioning" and r["weaker"] == ["orders-prod-us-east-1"] for r in inc)
    recs = data["tabs"][3]["items"]
    assert any("Opt S3 in to AWS Backup in us-east-1" in r["recommendation"] for r in recs)
    gov = data["tabs"][5]
    assert "questions" not in gov and all("questions" not in d for d in gov["dimensions"])


def test_third_party_backup_makes_backup_coverage_unconfirmed_in_exposures():
    data, _ = scoring.build(inventory.build(RAW))
    exp = next(r for r in data["tabs"][2]["resources"] if r["name"].startswith("Critical"))
    check = next(c for p in exp["pillars"] for c in p["checks"] if c["label"].startswith("orders-prod-us-east-1"))
    fails, _, unconfirmed = check["description"].partition("; unconfirmed: ")
    assert "backup coverage" not in fails and "backup coverage" in unconfirmed


def test_sizes_are_binary_units():
    data, _ = scoring.build(inventory.build(RAW))
    rows = {c["row"]["bucket"]: c["row"] for c in data["tabs"][1]["cards"]}
    assert rows["orders-prod-eu-west-1"]["size_human"] == "1.0 TiB"
    text = json.dumps(data, ensure_ascii=False)
    assert not re.search(r"\d (TB|GB|PB)\b", text) and "(≥100 TiB)" in text
