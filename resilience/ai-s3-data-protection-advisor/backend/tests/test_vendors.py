from s3dp.derive import vendors


def test_bucket_name_substring_match_anywhere():
    assert vendors.match("cfm-rubrik-srvprod-standby-daily") == ("Rubrik", "strong")
    assert vendors.match("acme-veaam-offsite") == ("Veeam", "variant")
    assert vendors.match("commvault-lib-01") == ("Commvault", "strong")


def test_short_patterns_need_token_boundaries():
    assert vendors.match("unbundled-assets") is None          # 'nbu' inside a word
    assert vendors.match("corp-nbu-catalog") == ("Veritas NetBackup", "strong")
    assert vendors.match("ora-rman-archive") == ("Database dump", "landing_zone")


def test_estate_signals():
    out = vendors.estate(bucket_names=["acme-veeam-repo", "data"], role_names=["VeeamBackupRole"], stack_names=[])
    assert out == [{"vendor": "Veeam", "evidence": "Bucket name acme-veeam-repo; IAM role VeeamBackupRole",
                    "confidence": "strong"}]


def test_camel_case_names_and_new_signatures():
    assert vendors.match("N2WSCpmRole") == ("N2W", "strong")
    assert vendors.match("acme-cpm-backups") == ("N2W", "strong")
    assert vendors.match("NBUMediaServerRole") == ("Veritas NetBackup", "strong")
    assert vendors.match("HYCUBackupRole") == ("HYCU", "strong")
    assert vendors.match("acme-phoenix-store") == ("Druva", "variant")
    assert vendors.match("metallic-cloud-lib") == ("Commvault", "variant")
    assert vendors.match("polaris-exports") == ("Rubrik", "variant")
    assert vendors.match("scpmetrics") is None
