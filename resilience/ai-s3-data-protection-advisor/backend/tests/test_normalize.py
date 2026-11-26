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
    assert _pol(deny(["s3:GetObject", "s3:DeleteBucket"]))["delete_deny"] is True
    assert _pol(deny("s3:DeleteObjectV*"))["delete_deny"] is True
    assert _pol(deny("s3:DeleteBucketPolicy"))["delete_deny"] is False


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
    one = {"Statement": deny("s3:DeleteObject")}
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
