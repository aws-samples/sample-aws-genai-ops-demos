from s3dp.derive import findings as f


def bucket(**kw):
    base = {"name": "orders-prod", "role": "production", "versioning": "Enabled", "object_lock": "None",
            "replication": "None", "encryption": "SSE-KMS", "bucket_key": True, "lifecycle": True,
            "noncurrent_expiration": True, "abort_mpu": True, "transitions": False, "bucket_bpa": "on",
            "is_public": False, "ownership": "BucketOwnerEnforced", "policy": {"delete_deny": True, "tls_only": True},
            "logging": True, "size_tb": 1.0, "regional_required": False, "vce": None, "write_pattern": None,
            "eventbridge": True, "aws_backup": "None"}
    base.update(kw)
    return base


def checks(b, **ctx):
    return {x["check"]: x for x in f.for_bucket(b, **ctx)}


def test_clean_production_bucket_has_no_findings():
    assert checks(bucket()) == {}


def test_unversioned_production_is_high_with_cost():
    c = checks(bucket(versioning="Never enabled", noncurrent_expiration=False,
                      vce={"monthly_usd": 12.5, "range": [0, 25], "churn_pct": 1.2, "basis": "bounded",
                           "retention_days": 30}))
    assert c["S3-BKP-01"]["severity"] == "HIGH"
    assert "$12.50/month" in c["S3-BKP-01"]["description"]
    assert c["S3-BKP-01"]["action"].startswith("Enable versioning")


def test_unread_settings_produce_no_finding():
    c = checks(bucket(versioning="NotVerified", policy={"delete_deny": None, "tls_only": None}, ownership=None,
                      logging=None, lifecycle=None, noncurrent_expiration=None, abort_mpu=None))
    assert c == {}


def test_public_exposure_applies_to_every_role():
    c = checks(bucket(role="non_production", bucket_bpa="off", is_public=True, versioning="Never enabled"))
    assert set(c) == {"S3-ACC-01", "S3-ACC-02"}


def test_hygiene_checks():
    c = checks(bucket(policy={"delete_deny": False, "tls_only": False}, ownership="ObjectWriter", logging=False,
                      abort_mpu=False, noncurrent_expiration=False, encryption="SSE-KMS", bucket_key=False))
    assert {"S3-DEL-01", "S3-ACC-03", "S3-ACC-04", "S3-ACC-05", "S3-LCY-03", "S3-LCY-04", "S3-ENC-03"} <= set(c)


def test_continuous_backup_without_eventbridge_is_high():
    c = checks(bucket(aws_backup="continuous-s3", aws_backup_plans=["continuous-s3"], eventbridge=False),
               continuous_plans={"continuous-s3"})
    assert c["BKP-ACC-07"]["severity"] == "HIGH"


def test_continuous_backup_matches_one_of_several_plans():
    c = checks(bucket(aws_backup="daily, continuous-s3", aws_backup_plans=["daily", "continuous-s3"],
                      eventbridge=False), continuous_plans={"continuous-s3"})
    assert "BKP-ACC-07" in c


def test_regulated_sse_s3_is_medium():
    c = checks(bucket(name="legal-contracts-prod", encryption="SSE-S3", regional_required=True))
    assert c["S3-ENC-02"]["severity"] == "MEDIUM"


def test_regulated_short_words_match_whole_tokens():
    assert "S3-ENC-02" not in checks(bucket(name="chrome-assets-prod", encryption="SSE-S3"))
    assert "S3-ENC-02" in checks(bucket(name="hr-records-prod", encryption="SSE-S3"))
    assert "S3-ENC-02" in checks(bucket(name="payments-ledger", encryption="SSE-S3"))


def test_bucket_keys_finding_only_for_sse_kms():
    assert "S3-ENC-03" not in checks(bucket(encryption="DSSE-KMS", bucket_key=False))
    assert "S3-ENC-03" in checks(bucket(encryption="SSE-KMS", bucket_key=False))


def test_bkp01_cost_shows_range_and_titles():
    v = {"monthly_usd": 12.5, "range": [6.0, 25.0], "churn_pct": 1.2, "basis": "bounded", "retention_days": 30}
    c = checks(bucket(versioning="Never enabled", vce=v))
    assert "$12.50/month, range $6.00–$25.00" in c["S3-BKP-01"]["description"]
    same = dict(v, range=[12.5, 12.5])
    assert "range" not in checks(bucket(versioning="Never enabled", vce=same))["S3-BKP-01"]["description"]
    assert checks(bucket(versioning="Never enabled", size_tb=None))["S3-BKP-01"]["title"] == \
        "Unversioned bucket (size not measured)"
    assert checks(bucket(versioning="Suspended", size_tb=None))["S3-BKP-01"]["title"] == \
        "Bucket with versioning suspended (size not measured)"
    assert checks(bucket(versioning="Never enabled", size_tb=0))["S3-BKP-01"]["title"] == "Empty unversioned bucket"
    assert checks(bucket(versioning="Never enabled", size_tb=2.5))["S3-BKP-01"]["title"] == "2.5 TiB unversioned"


def test_block_public_access_text_matches_the_effective_setting():
    f = checks(bucket(bucket_bpa="off"))["S3-ACC-01"]
    assert f["title"] == "Block Public Access is not fully enabled on the bucket or the account"
    assert "Neither" not in f["description"]
