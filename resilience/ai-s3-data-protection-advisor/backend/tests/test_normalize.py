import json

from s3dp.derive import normalize as n

OK_PAB = {"ok": {"BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True,
                 "RestrictPublicBuckets": True}}


def raw(**over):
    base = {"versioning": {"ok": {"status": "Enabled", "mfa_delete": None}}, "object_lock": {"absent": True},
            "replication": {"absent": True}, "encryption": {"absent": True}, "lifecycle": {"absent": True},
            "public_access_block": OK_PAB, "policy": {"absent": True}, "policy_status": {"absent": True},
            "ownership": {"ok": "BucketOwnerEnforced"}, "tags": {"absent": True},
            "logging": {"ok": {"enabled": False, "target": None}}, "eventbridge": {"ok": False}}
    base.update(over)
    return base


def test_versioning_states():
    assert n.versioning(raw()["versioning"]) == ("Enabled", False)
    assert n.versioning({"ok": {"status": None, "mfa_delete": None}}) == ("Never enabled", False)
    assert n.versioning({"ok": {"status": "Suspended", "mfa_delete": "Enabled"}}) == ("Suspended", True)
    assert n.versioning({"error": "AccessDenied"}) == ("NotVerified", None)


def test_object_lock_strings():
    assert n.object_lock({"absent": True}) == "None"
    assert n.object_lock({"error": "AccessDenied"}) == "NotVerified"
    assert n.object_lock({"ok": {"enabled": True, "mode": "GOVERNANCE", "days": 30, "years": None}}) == "GOVERNANCE 30d"
    assert n.object_lock({"ok": {"enabled": True, "mode": "COMPLIANCE", "days": None, "years": 7}}) == "COMPLIANCE 7y"
    assert n.object_lock({"ok": {"enabled": True, "mode": None, "days": None, "years": None}}).startswith("Enabled")


def test_replication_cross_and_same_region():
    regions = {"src": "eu-west-1", "dst": "eu-central-1", "same": "eu-west-1"}
    crr = {"ok": [{"status": "Enabled", "destination": "dst", "storage_class": "STANDARD", "delete_markers": True,
                   "rtc": False}]}
    assert n.replication(crr, "eu-west-1", regions) == {"destination": "dst", "destination_region": "eu-central-1",
                                                       "delete_markers": True, "rtc": False}
    srr = {"ok": [{"status": "Enabled", "destination": "same", "storage_class": "STANDARD", "delete_markers": False,
                   "rtc": False}]}
    assert n.replication(srr, "eu-west-1", regions)["srr"] is True
    other = {"ok": [{"status": "Enabled", "destination": "elsewhere", "storage_class": "STANDARD",
                     "delete_markers": False, "rtc": False}]}
    assert n.replication(other, "eu-west-1", regions)["inferred"] is True
    assert n.replication({"ok": [{"status": "Disabled", "destination": "dst", "storage_class": "S",
                                  "delete_markers": False, "rtc": False}]}, "eu-west-1", regions) == "None"
    assert n.replication({"absent": True}, "eu-west-1", regions) == "None"
    assert n.replication({"error": "AccessDenied"}, "eu-west-1", regions) == "NotVerified"


def test_encryption_and_lifecycle():
    assert n.encryption({"absent": True}) == ("SSE-S3", False)
    assert n.encryption({"ok": {"algorithm": "aws:kms", "bucket_key": False}}) == ("SSE-KMS", False)
    assert n.encryption({"ok": {"algorithm": "aws:kms:dsse", "bucket_key": True}}) == ("DSSE-KMS", True)
    assert n.encryption({"error": "AccessDenied"}) == ("NotVerified", False)
    rules = {"ok": [{"Status": "Enabled", "NoncurrentVersionExpiration": {"NoncurrentDays": 30}},
                    {"Status": "Enabled", "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}},
                    {"Status": "Disabled", "Transitions": [{"Days": 30, "StorageClass": "GLACIER"}]}]}
    assert n.lifecycle(rules) == {"lifecycle": True, "noncurrent_expiration": True, "abort_mpu": True,
                                  "transitions": False}
    assert n.lifecycle({"absent": True}) == {"lifecycle": False, "noncurrent_expiration": False, "abort_mpu": False,
                                             "transitions": False}
    assert n.lifecycle({"error": "AccessDenied"}) == {"lifecycle": None, "noncurrent_expiration": None,
                                                      "abort_mpu": None, "transitions": None}


def test_bpa_falls_back_to_account():
    assert n.bucket_bpa(OK_PAB, "off") == "on"
    assert n.bucket_bpa({"absent": True}, "on") == "on"
    assert n.bucket_bpa({"absent": True}, "off") == "off"
    assert n.bucket_bpa({"error": "AccessDenied"}, "on") == "on"
    assert n.bucket_bpa({"error": "AccessDenied"}, "unknown") == "unknown"


def test_policy_delete_deny_and_tls():
    doc = {"Statement": [
        {"Effect": "Deny", "Principal": "*", "Action": ["s3:DeleteObject", "s3:DeleteObjectVersion"], "Resource": "*"},
        {"Effect": "Deny", "Principal": "*", "Action": "s3:*", "Resource": "*",
         "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]}
    assert n.policy({"ok": json.dumps(doc)}) == {"delete_deny": True, "tls_only": True}
    assert n.policy({"absent": True}) == {"delete_deny": False, "tls_only": False}
    assert n.policy({"error": "AccessDenied"}) == {"delete_deny": None, "tls_only": None}


def test_normalize_bucket_records_errors():
    b = n.normalize_bucket({"name": "x", "account": "111122223333", "region": "eu-west-1",
                            "s3": raw(object_lock={"error": "AccessDenied"}), "metrics": None},
                           regions={"x": "eu-west-1"}, account_bpa="on", backup={})
    assert b["object_lock"] == "NotVerified"
    assert b["errors"] == {"object_lock": "AccessDenied"}
    assert b["size_tb"] is None and b["storage_class"] == "UNKNOWN"
    assert b["aws_backup"] == "None"


PARTIAL_PAB = {"ok": dict(OK_PAB["ok"], RestrictPublicBuckets=False)}
ALL_FALSE_PAB = {"ok": dict.fromkeys(OK_PAB["ok"], False)}


def test_bpa_effective_bucket_or_account():
    assert n.bucket_bpa({"error": "AccessDenied"}, "off") == "unknown"
    assert n.bucket_bpa(PARTIAL_PAB, "unknown") == "unknown"
    assert n.bucket_bpa(PARTIAL_PAB, "on") == "on"
    assert n.bucket_bpa(ALL_FALSE_PAB, "unknown") == "unknown"
    assert n.bucket_bpa({"absent": True}, "off") == "off"
    assert n.bucket_bpa(PARTIAL_PAB, "off") == "off"
    assert n.bucket_bpa(OK_PAB, "unknown") == "on"


def _pol(*stmts, raw_doc=None):
    return n.policy({"ok": json.dumps(raw_doc if raw_doc is not None else {"Statement": list(stmts)})})


def deny(action=None, cond=None, not_action=None):
    s = {"Effect": "Deny", "Principal": "*", "Resource": "*"}
    if action is not None:
        s["Action"] = action
    if not_action is not None:
        s["NotAction"] = not_action
    if cond:
        s["Condition"] = cond
    return s


def test_policy_delete_guard_needs_a_delete_action():
    assert _pol(deny("s3:PutObject"))["delete_deny"] is False
    assert _pol(deny("s3:Get*"))["delete_deny"] is False
    assert _pol(deny("s3:Delete*"))["delete_deny"] is True
    assert _pol(deny("S3:DELETEOBJECT*"))["delete_deny"] is True
    assert _pol(deny(["s3:GetObject", "s3:DeleteBucket"]))["delete_deny"] is False     # bucket deletion only
    assert _pol(deny("s3:DeleteObjectV*"))["delete_deny"] is False                      # versions only
    assert _pol(deny("s3:DeleteObject"), deny("s3:DeleteObjectVersion"))["delete_deny"] is True   # split statements
    assert _pol(deny("s3:DeleteBucketPolicy"))["delete_deny"] is False


def _on(resource, action=("s3:DeleteObject", "s3:DeleteObjectVersion"), cond=None):
    s = dict(deny(list(action), cond), Resource=resource)
    return n.policy({"ok": json.dumps({"Statement": [s]})}, "records-prod")["delete_deny"]


def test_delete_guard_must_cover_every_object_of_the_bucket():
    # Review F05: a s3:DeleteBucket deny on the bucket ARN is not an object guard.
    assert n.policy({"ok": json.dumps({"Statement": [{"Effect": "Deny", "Principal": "*", "Action": "s3:DeleteBucket",
                                                      "Resource": "arn:aws:s3:::records-prod"}]})},
                    "records-prod") == {"delete_deny": False, "tls_only": False}
    assert _on("arn:aws:s3:::records-prod/*") is True                   # object-wide, object and version deletes
    assert _on(["arn:aws:s3:::records-prod", "arn:aws:s3:::records-prod/*"]) is True
    assert _on("arn:aws:s3:::*") is True and _on("*") is True
    assert _on("arn:aws:s3:::records-prod/*", action=("s3:DeleteObjectVersion",)) is False   # version-wide only
    assert _on("arn:aws:s3:::records-prod/exports/*") is False          # prefix-limited
    assert _on("arn:aws:s3:::other-bucket/*") is False
    assert _on("arn:aws:s3:::records-prod") is False                    # bucket ARN: no object matches it
    assert _on("arn:aws:s3:::records-prod/*", cond={"StringNotEquals": {"aws:SourceVpce": "vpce-1"}}) is False
    assert _on("arn:aws:s3:::records-prod/*", cond={"ArnNotLike": {"aws:PrincipalArn": "arn:aws:iam::1:role/r"}}) is True
    not_res = {"Effect": "Deny", "Principal": "*", "Action": "s3:Delete*", "NotResource": "arn:aws:s3:::x/keep/*"}
    assert n.policy({"ok": json.dumps({"Statement": [not_res]})}, "records-prod")["delete_deny"] is False


def test_policy_wildcard_deny_counts_only_without_perimeter_condition():
    assert _pol(deny("s3:*"))["delete_deny"] is True
    assert _pol(deny("*"))["delete_deny"] is True
    assert _pol(deny("s3:*", {"ArnNotLike": {"aws:PrincipalArn": "arn:aws:iam::1:role/admin"}}))["delete_deny"] is True
    assert _pol(deny("s3:*", {"StringNotEquals": {"aws:PrincipalTag/team": "ops"}}))["delete_deny"] is True
    assert _pol(deny("s3:*", {"StringNotEquals": {"aws:SourceVpce": "vpce-1"}}))["delete_deny"] is False
    assert _pol(deny("s3:*", {"NotIpAddress": {"aws:SourceIp": "10.0.0.0/8"}}))["delete_deny"] is False
    assert _pol(deny("s3:*", {"StringNotEquals": {"aws:PrincipalOrgID": "o-1"}}))["delete_deny"] is False
    assert _pol(deny("s3:*", {"Bool": {"aws:SecureTransport": "false"}}))["delete_deny"] is False


def test_policy_not_action():
    assert _pol(deny(not_action=["s3:GetObject", "s3:PutObject"]))["delete_deny"] is True
    assert _pol(deny(not_action="s3:Delete*"))["delete_deny"] is False


def test_policy_statement_shapes():
    one = {"Statement": deny(["s3:DeleteObject", "s3:DeleteObjectVersion"])}
    assert _pol(raw_doc=one) == {"delete_deny": True, "tls_only": False}
    assert _pol(raw_doc=[1, 2]) == {"delete_deny": None, "tls_only": None}
    assert _pol(raw_doc={"Statement": ["oops"]}) == {"delete_deny": None, "tls_only": None}


def test_policy_tls_variants():
    assert _pol(deny("s3:*", {"BoolIfExists": {"aws:securetransport": "false"}}))["tls_only"] is True
    assert _pol(deny("s3:*", {"Bool": {"aws:SecureTransport": ["false"]}}))["tls_only"] is True
    assert _pol(deny("s3:*", {"Bool": {"aws:SecureTransport": "true"}}))["tls_only"] is False


def test_ownership_absent_is_object_writer():
    assert n.ownership({"absent": True}) == "ObjectWriter"
    assert n.ownership({"error": "AccessDenied"}) is None
    assert n.ownership({"ok": "BucketOwnerEnforced"}) == "BucketOwnerEnforced"


def test_replication_prefers_cross_then_inferred_then_same():
    regions = {"same": "eu-west-1", "cross": "us-east-1"}

    def rule(dest):
        return {"status": "Enabled", "destination": dest, "storage_class": "STANDARD", "delete_markers": False,
                "rtc": False}
    assert n.replication({"ok": [rule("same"), rule("other-acct"), rule("cross")]}, "eu-west-1",
                         regions)["destination"] == "cross"
    r = n.replication({"ok": [rule("same"), rule("other-acct")]}, "eu-west-1", regions)
    assert r["destination"] == "other-acct" and r["inferred"] is True
    r = n.replication({"ok": [rule("same")]}, None, regions)
    assert r.get("inferred") is True and "srr" not in r


def test_unrecognised_algorithm_is_not_verified():
    assert n.encryption({"ok": {"algorithm": None, "bucket_key": False}}) == ("NotVerified", False)
    assert n.encryption({"ok": {"algorithm": "rot13", "bucket_key": True}}) == ("NotVerified", False)


def test_lifecycle_scoped_rules_are_not_bucket_wide():
    scoped = {"ok": [{"Status": "Enabled", "Filter": {"Prefix": "tmp/"}, "NoncurrentVersionExpiration": {"NoncurrentDays": 1},
                      "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}}]}
    out = n.lifecycle(scoped)
    assert out["noncurrent_expiration"] is False and out["abort_mpu"] is False and out["lifecycle"] is True
    wide = {"ok": [{"Status": "Enabled", "Filter": {"Prefix": ""}, "AbortIncompleteMultipartUpload": {}},
                   {"Status": "Enabled", "Filter": {}, "NoncurrentVersionExpiration": {"NoncurrentDays": 30}}]}
    assert n.lifecycle(wide)["abort_mpu"] is True and n.lifecycle(wide)["noncurrent_expiration"] is True
    marker = {"ok": [{"Status": "Enabled", "Expiration": {"ExpiredObjectDeleteMarker": True}}]}
    assert n.lifecycle(marker)["noncurrent_expiration"] is False


def test_normalize_bucket_backup_plans_and_tags_read():
    b = n.normalize_bucket({"name": "x", "account": "1", "region": "eu-west-1",
                            "s3": raw(tags={"error": "AccessDenied"}),
                            "metrics": {"size_series": [1.0, 2.0]}},
                           regions={"x": "eu-west-1"}, account_bpa="on", backup={"x": ["daily", "continuous"]})
    assert b["aws_backup"] == "daily, continuous" and b["aws_backup_plans"] == ["daily", "continuous"]
    assert b["tags_read"] is False and b["tags"] == {}
    assert b["size_series"] == [1.0, 2.0]
    ok = n.normalize_bucket({"name": "x", "account": "1", "region": "eu-west-1", "s3": raw(), "metrics": None},
                            regions={}, account_bpa="on", backup={})
    assert ok["tags_read"] is True and ok["aws_backup_plans"] == [] and ok["size_series"] == []


# ---- regional protection credit (review F01, F02)

from s3dp import run as run_seq
from s3dp.engine import scoring


def _rule(dest, scope=None):
    return {"status": "Enabled", "destination": dest, "storage_class": None, "delete_markers": True, "rtc": False,
            "scope": scope}


REGIONS = {"src": "eu-west-1", "dst-cross": "eu-central-1", "dst-same": "eu-west-1"}


def test_only_a_verified_bucket_wide_cross_region_copy_is_regional_protection():
    cases = {
        "cross": ([_rule("dst-cross")], "protected"),
        "same": ([_rule("dst-same")], "unprotected"),
        "unknown destination region": ([_rule("other-account-bucket")], "unknown"),
        "prefix": ([_rule("dst-cross", {"prefix": "exports/", "tags": {}})], "partial"),
        "tag": ([_rule("dst-cross", {"prefix": None, "tags": {"replicate": "yes"}})], "partial"),
        "bucket-wide beside a filtered rule": ([_rule("dst-cross", {"prefix": "a/", "tags": {}}),
                                                _rule("dst-cross")], "protected"),
        "two filtered rules": ([_rule("dst-cross", {"prefix": "a/", "tags": {}}),
                                _rule("dst-cross", {"prefix": "b/", "tags": {}})], "partial"),
    }
    for name, (rules, want) in cases.items():
        rep = n.replication({"ok": rules}, "eu-west-1", REGIONS)
        assert n.regional_copy(rep) == want, name
    rep = n.replication({"ok": [_rule("dst-cross", {"prefix": "exports/", "tags": {}})]}, "eu-west-1", REGIONS)
    assert rep["filter"] == "prefix exports/"
    assert n.regional_copy("None") == "unprotected" and n.regional_copy("NotVerified") == "unknown"


def _raw_pair(dest_region, scope=None, tags=None):
    """A regulated production source replicating to a destination in `dest_region`, as a raw scan."""
    def item(name, region, s3):
        return {"name": name, "account": "111122223333", "region": region, "s3": s3, "metrics": None}
    src = raw(replication={"ok": [_rule("records-copy", scope)]},
              tags={"ok": tags or {"data-classification": "regulated"}})
    return {"hub_account": "111122223333", "scan_date": "2026-11-26T00:00:00Z",
            "accounts": {"111122223333": {"name": "acme", "account_bpa": "on", "role_names": [], "stack_names": [],
                                          "config": {"recording": True, "s3_rules": []}, "security_hub": True}},
            "regions": {"111122223333": {"eu-west-1": {}, dest_region: {}}},
            "buckets": [item("records-prod", "eu-west-1", src), item("records-copy", dest_region, raw())],
            "organization": None, "run": {"scope": "1 account (acme)", "unavailable": [], "api_errors": 0}}


def _scored(raw_scan):
    inv, data, _ = run_seq.score(raw_scan, {}, {"classification": False})
    cards = {c["row"]["bucket"]: c for c in next(t for t in data["tabs"] if t["id"] == "resources")["cards"]}
    return {b["name"]: b for b in inv["buckets"]}, cards


def test_same_region_replication_leaves_the_regional_gap_open():
    buckets, cards = _scored(_raw_pair("eu-west-1"))
    src, copy = buckets["records-prod"], buckets["records-copy"]
    assert src["regional_required"] is True and src["replication"]["srr"] is True
    assert scoring.coverage(src)["regional_disruption"] == "unprotected"
    assert copy["replication"] == {"replica_of": "records-prod", "source_region": "eu-west-1", "srr": True}
    assert scoring.coverage(copy)["regional_disruption"] == "unprotected"     # the marker alone earns nothing
    row = cards["records-prod"]["row"]
    assert row["rd"] == "unprotected" and "another region" in row["action"]
    assert row["gap"] == ["regional_disruption"]                              # counted as a required gap
    rd_cell = next(c for c in row["controls"] if c["control"] == "Cross-region replication")["cells"]["rd"]
    assert rd_cell[0] == "missing"


def test_cross_region_replication_closes_it_and_a_filter_keeps_it_partial():
    buckets, cards = _scored(_raw_pair("eu-central-1"))
    assert scoring.coverage(buckets["records-prod"])["regional_disruption"] == "protected"
    assert buckets["records-copy"]["replication"] == {"replica_of": "records-prod", "source_region": "eu-west-1"}
    assert scoring.coverage(buckets["records-copy"])["regional_disruption"] == "protected"
    buckets, cards = _scored(_raw_pair("eu-central-1", scope={"prefix": "exports/", "tags": {}}))
    row = cards["records-prod"]["row"]
    assert row["rd"] == "partial" and "only prefix exports/" in row["action"]
    rd_cell = next(c for c in row["controls"] if c["control"] == "Cross-region replication")["cells"]["rd"]
    assert rd_cell[0] == "partial" and "prefix exports/" in rd_cell[1]


def _flags(**on):
    return {k: k in on for k in n.PAB_FLAGS}


def test_bpa_combines_account_and_bucket_flag_by_flag():
    # Review F09: the account blocks ACLs, the bucket blocks policies: all four are in effect.
    acct = _flags(BlockPublicAcls=1, IgnorePublicAcls=1)
    bucket = {"ok": _flags(BlockPublicPolicy=1, RestrictPublicBuckets=1)}
    assert n.bucket_bpa(bucket, "off", acct) == "on"
    # RestrictPublicBuckets set nowhere: off.
    assert n.bucket_bpa({"ok": _flags(BlockPublicPolicy=1)}, "off", acct) == "off"
    # Bucket unread: the account's two flags are known, the other two are not: unknown, not off.
    assert n.bucket_bpa({"error": "AccessDenied"}, "off", acct) == "unknown"
    assert n.bucket_bpa({"error": "AccessDenied"}, "on", dict.fromkeys(n.PAB_FLAGS, True)) == "on"
    # Account unread: a bucket with all four on needs nothing from it; a bucket missing one stays unknown.
    assert n.bucket_bpa(OK_PAB, "unknown", None) == "on"
    assert n.bucket_bpa({"ok": _flags(BlockPublicPolicy=1)}, "unknown", None) == "unknown"
    assert n.bucket_bpa({"absent": True}, "off", dict.fromkeys(n.PAB_FLAGS, False)) == "off"
