import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from s3dp.collect import scan

from .test_scan import ACCOUNT, BUCKET, REGION, _session

FIX = Path(__file__).parent / "fixtures"
# scan_raw_*.json were generated from the f7a5190 scanner (the one-shot collect_account before it was split into
# units), minus scan_date. Never regenerate them from the current code: they are the oracle of "identical output".
SPEND_REGIONS = ("eu-west-1", "ap-south-1")


def _activate(s):
    for _, st in s.stubs.values():
        st.activate()
    return s


def _denied_session():
    """Bucket list denied, Cost Explorer shows spend in two regions: region-level reads only."""
    s = _session()
    s.stubs.clear()
    s.stub("s3").add_client_error("list_buckets", "AccessDenied", http_status_code=403)
    s.stub("ce", "us-east-1").add_response("get_cost_and_usage", {"ResultsByTime": [{"Groups": [
        {"Keys": [r], "Metrics": {"UnblendedCost": {"Amount": "5.0", "Unit": "USD"}}} for r in SPEND_REGIONS]}]})
    s.stub("iam").add_client_error("list_account_aliases", "AccessDenied", http_status_code=403)
    s.stub("s3control").add_client_error("get_public_access_block", "AccessDenied", http_status_code=403)
    for r in SPEND_REGIONS:
        bk = s.stub("backup", r)
        for op in ("list_protected_resources", "list_backup_plans", "list_backup_vaults", "describe_region_settings",
                   "list_restore_testing_plans", "list_frameworks"):
            bk.add_client_error(op, "AccessDeniedException", http_status_code=403)
        s.stub("s3", r)          # the f7a5190 scanner built a regional S3 client even with no bucket to read
        s.stub("cloudformation", r).add_response("list_stacks", {"StackSummaries": []})
        s.stub("config", r).add_client_error("describe_configuration_recorders", "AccessDeniedException",
                                             http_status_code=400)
        s.stub("securityhub", r).add_client_error("get_enabled_standards", "InvalidAccessException",
                                                  http_status_code=401)
    return s


def _ov(buckets, regions=None, batch=scan.BATCH, **kw):
    ov = {"today": "2026-11-25", "account_id": ACCOUNT, "buckets": buckets, "alias": None, "role_names": [],
          "account_bpa": "on", "listed": True, "run": scan._Run().to_dict(), "batch": batch}
    ov["regions"] = sorted(regions if regions is not None else {b["region"] for b in buckets})
    ov.update(kw)
    return ov


def _empty_unit(u):
    return {"n": u["n"], "region": u["region"], "info": None, "items": [], "errors": {},
            "acct": {"stack_names": [], "config_recording": [], "config_rules": [], "security_hub": []},
            "run": scan._Run().to_dict()}


@pytest.mark.parametrize("make, fixture", [(_session, "scan_raw_one_region.json"),
                                           (_denied_session, "scan_raw_denied_two_regions.json")])
def test_collect_account_output_is_unchanged(make, fixture):
    raw = scan.collect_account(_activate(make()), ACCOUNT, today="2026-11-25")
    raw.pop("scan_date")
    assert raw == json.loads((FIX / fixture).read_text())


def test_units_compose_to_the_one_shot_scan():
    one = scan.collect_account(_activate(_session()), ACCOUNT, today="2026-11-25")
    s = _activate(_session())
    ov = scan.account_overview(s, ACCOUNT, today="2026-11-25")
    units = [scan.collect_unit(s, ACCOUNT, u) for u in scan.plan_units(ov)]
    for _, st in s.stubs.values():
        st.assert_no_pending_responses()
    split = scan.assemble(ov, units)
    one.pop("scan_date"), split.pop("scan_date")
    assert split == one


def test_450_buckets_in_one_region_make_three_units():
    ov = _ov([{"name": f"b{i}", "region": REGION} for i in range(450)])
    units = scan.plan_units(ov)
    assert [len(u["names"]) for u in units] == [200, 200, 50]
    assert [u["first"] for u in units] == [True, False, False]
    assert [u["n"] for u in units] == [0, 1, 2] and {u["region"] for u in units} == {REGION}


def test_spend_only_region_gets_one_unit_without_names():
    ov = _ov([{"name": "a", "region": REGION}, {"name": "x", "region": "unknown"}], regions=[REGION, "us-west-2"])
    units = scan.plan_units(ov)
    assert [(u["region"], u["names"], u["first"]) for u in units] == [(REGION, ["a"], True),
                                                                      ("us-west-2", [], True)]


def test_failed_first_unit_reports_its_buckets_as_not_scanned():
    ov = _ov([{"name": f"b{i}", "region": REGION} for i in range(250)])
    u1 = scan.plan_units(ov)[1]
    raw = scan.assemble(ov, [None, _empty_unit(u1)])
    run = raw["run"]
    assert ["S3 settings (eu-west-1, 200 buckets not scanned)", "collection failed"] in run["unavailable"]
    assert ["AWS Backup, CloudFormation, AWS Config and Security Hub (eu-west-1)",
            "collection failed"] in run["unavailable"]
    assert run["api_errors"] == 201                  # 200 buckets + the region-level reads
    assert raw["regions"][ACCOUNT][REGION]["outcome"] == "partial: collection failed"
    assert "backup" not in raw["regions"][ACCOUNT][REGION]     # region reads not made: not read, not "off"
    assert raw["buckets"] == []
    assert raw["accounts"][ACCOUNT]["config"]["recording"] is None
    assert raw["accounts"][ACCOUNT]["config"]["unreadable_regions"] == [REGION]
    assert raw["accounts"][ACCOUNT]["security_hub"] is None


def test_failed_later_unit_marks_the_region_partial():
    ov = _ov([{"name": f"b{i}", "region": REGION} for i in range(250)])
    s = _activate(_session())
    first = scan.collect_unit(s, ACCOUNT, scan.plan_units(ov)[0] | {"names": [BUCKET]})
    raw = scan.assemble(ov, [first, None])
    outcome = raw["regions"][ACCOUNT][REGION]["outcome"]
    assert outcome.startswith("partial") and "collection failed" in outcome
    assert ["S3 settings (eu-west-1, 50 buckets not scanned)", "collection failed"] in raw["run"]["unavailable"]


def test_failed_spend_only_unit_says_region_level_reads():
    ov = _ov([], regions=["us-west-2"])
    raw = scan.assemble(ov, [None])
    assert ["Region-level reads (us-west-2)", "collection failed"] in raw["run"]["unavailable"]
    assert not any("0 buckets" in u[0] for u in raw["run"]["unavailable"])


def test_failed_first_unit_in_a_middle_region_keeps_regions_aligned():
    ov = _ov([], regions=["ap-south-1", "eu-west-1", "us-west-2"])
    units = scan.plan_units(ov)
    ok = []
    for u in (units[0], units[2]):
        res = _empty_unit(u)
        res["info"] = {"outcome": "scanned"}
        res["acct"].update(config_recording=[True], config_rules=[["rule"]], security_hub=[True])
        ok.append(res)
    raw = scan.assemble(ov, [ok[0], None, ok[1]])
    assert raw["accounts"][ACCOUNT]["config"]["unreadable_regions"] == ["eu-west-1"]
    assert list(raw["regions"][ACCOUNT]) == ["ap-south-1", "eu-west-1", "us-west-2"]
    assert raw["regions"][ACCOUNT]["eu-west-1"]["outcome"] == "partial: collection failed"
    assert raw["accounts"][ACCOUNT]["security_hub"] is True      # merge ignores the unknown region


def test_assemble_rejects_a_short_or_misordered_units_list():
    ov = _ov([{"name": f"b{i}", "region": REGION} for i in range(250)])
    units = scan.plan_units(ov)
    with pytest.raises(ValueError):
        scan.assemble(ov, [None])
    with pytest.raises(ValueError):
        scan.assemble(ov, [_empty_unit(units[1]), _empty_unit(units[0])])


def test_assemble_does_not_mutate_its_inputs():
    ov = _ov([], regions=[REGION])
    res = _empty_unit(scan.plan_units(ov)[0])
    res["info"] = {"outcome": "scanned"}
    res["errors"] = {"s3": "Boom"}
    before = json.dumps([ov, res], sort_keys=True)
    scan.assemble(ov, [res])
    assert json.dumps([ov, res], sort_keys=True) == before


def test_overview_and_unit_results_are_json_checkpoints():
    s = _activate(_session())
    ov = scan.account_overview(s, ACCOUNT, today="2026-11-25")
    json.dumps(ov)
    assert ov["buckets"] == [{"name": BUCKET, "region": REGION}] and ov["regions"] == [REGION] and ov["listed"]
    assert ov["batch"] == scan.BATCH
    for u in scan.plan_units(ov):
        res = scan.collect_unit(s, ACCOUNT, u)
        back = json.loads(json.dumps(res))
        assert back == res and res["n"] == u["n"]


def test_unit_with_a_dated_lifecycle_rule_is_json_serializable():
    s = _session()
    rs3 = s.stubs[("s3", REGION)][1]
    rs3._queue.clear()                      # replace the default bucket answers: only lifecycle matters here
    rs3.add_response("get_bucket_versioning", {"Status": "Enabled"})
    for op, code in (("get_object_lock_configuration", "ObjectLockConfigurationNotFoundError"),
                     ("get_bucket_replication", "ReplicationConfigurationNotFoundError"),
                     ("get_bucket_encryption", "ServerSideEncryptionConfigurationNotFoundError")):
        rs3.add_client_error(op, code, http_status_code=404)
    rs3.add_response("get_bucket_lifecycle_configuration", {"Rules": [{
        "ID": "r", "Status": "Enabled", "Filter": {},
        "Expiration": {"Date": datetime(2027, 1, 1, tzinfo=timezone.utc)},
        "Transitions": [{"Date": datetime(2026, 12, 1, tzinfo=timezone.utc), "StorageClass": "GLACIER"}]}]})
    for op, code in (("get_public_access_block", "NoSuchPublicAccessBlockConfiguration"),
                     ("get_bucket_policy", "NoSuchBucketPolicy"), ("get_bucket_policy_status", "NoSuchBucketPolicy"),
                     ("get_bucket_ownership_controls", "OwnershipControlsNotFoundError"),
                     ("get_bucket_tagging", "NoSuchTagSet")):
        rs3.add_client_error(op, code, http_status_code=404)
    rs3.add_response("get_bucket_logging", {})
    rs3.add_response("get_bucket_notification_configuration", {})
    _activate(s)
    ov = scan.account_overview(s, ACCOUNT, today="2026-11-25")
    res = scan.collect_unit(s, ACCOUNT, scan.plan_units(ov)[0])
    json.dumps(res)
    rule = res["items"][0]["s3"]["lifecycle"]["ok"][0]
    assert isinstance(rule["Expiration"]["Date"], str)
