from s3dp.derive import roles


def b(name, **kw):
    return dict({"name": name, "tags": {}, "replication": "None"}, **kw)


def test_relationships_win_over_names():
    buckets = [b("orders-prod", replication={"destination": "orders-prod-copy", "destination_region": "eu-central-1",
                                             "delete_markers": False, "rtc": False}),
               b("orders-prod-copy"), b("app-logs-target"), b("web", log_target="app-logs-target")]
    out = roles.assign(buckets, account_name="Production")
    assert out["orders-prod-copy"][0] == "replica"
    assert out["app-logs-target"][0] == "access_logs"
    assert out["orders-prod"][0] == "production"


def test_infrastructure_vendor_and_tags():
    out = roles.assign([b("cdk-hnb659fds-assets-111122223333-eu-west-1"), b("aws-cloudtrail-logs-1111-abc"),
                        b("acme-veeam-repo"), b("athena-query-results-eu"), b("mystery", tags={"Environment": "Prod"}),
                        b("mystery2", tags={"env": "staging"}), b("payments-dev"), b("blob-store-42")],
                       account_name="Shared")
    assert out["cdk-hnb659fds-assets-111122223333-eu-west-1"][0] == "tooling"
    assert out["aws-cloudtrail-logs-1111-abc"][0] == "audit_trail"
    assert out["acme-veeam-repo"][0] == "backup_target"
    assert out["athena-query-results-eu"][0] == "tooling"
    assert out["mystery"][0] == "production"
    assert out["mystery2"][0] == "non_production"
    assert out["payments-dev"][0] == "non_production"
    assert out["blob-store-42"] == (None, "No tag, name or relationship decides the role")


def test_account_name_is_a_signal():
    assert roles.assign([b("blob-store-42")], account_name="acme-prod")["blob-store-42"][0] == "production"


def test_bucket_logging_to_itself_is_not_a_log_target():
    assert roles.assign([b("orders-prod", log_target="orders-prod")])["orders-prod"][0] == "production"


def test_non_prod_phrases_win_over_prod_tokens():
    for name in ("orders-non-prod", "orders-nonprod", "orders-pre-prod", "orders-preprod", "orders-uat",
                 "OrdersPreProd"):
        assert roles.assign([b(name)])[name][0] == "non_production", name
    assert roles.assign([b("x", tags={"Environment": "pre-prod"})])["x"][0] == "non_production"
    assert roles.assign([b("x", tags={"env": "NonProd"})])["x"][0] == "non_production"
    assert roles.assign([b("x")], account_name="acme-non-prod")["x"][0] == "non_production"
    assert roles.assign([b("x")], account_name="AcmeProduction")["x"][0] == "production"


def test_rule_order_and_patterns():
    # a replication destination wins over a cloudtrail substring name
    out = roles.assign([b("src", replication={"destination": "org-cloudtrail-copy"}), b("org-cloudtrail-copy"),
                        b("aws-cloudtrail-logs-1-x")])
    assert out["org-cloudtrail-copy"][0] == "replica" and out["aws-cloudtrail-logs-1-x"][0] == "audit_trail"
    # backup-tool names before tags; tags before tooling names
    out = roles.assign([b("acme-veeam-repo", tags={"env": "dev"}),
                        b("athena-query-results-prod", tags={"Environment": "production"})])
    assert out["acme-veeam-repo"][0] == "backup_target"
    assert out["athena-query-results-prod"][0] == "production"
    # state buckets are not tooling; sagemaker is anchored to the default bucket name
    out = roles.assign([b("acme-terraform-state"), b("acme-tfstate"), b("sagemaker-eu-west-1-111122223333"),
                        b("sagemaker-models-prod")], account_name="Shared")
    assert out["acme-terraform-state"][0] is None and out["acme-tfstate"][0] is None
    assert out["sagemaker-eu-west-1-111122223333"][0] == "tooling"
    assert out["sagemaker-models-prod"][0] == "production"
    # production signals (tag, criticality, account name, bucket name) before non-production ones
    assert roles.assign([b("orders-prod")], account_name="acme-dev")["orders-prod"] == ("production", "Bucket name")


def test_unreadable_tags_are_flagged_in_the_reason():
    out = roles.assign([b("orders-prod", tags_read=False), b("blob", tags_read=False),
                        b("acme-veeam-repo", tags_read=False)])
    assert out["orders-prod"] == ("production", "Bucket name (tags not readable)")
    assert out["blob"][1].endswith("(tags not readable)")
    assert out["acme-veeam-repo"][1].endswith("name")


def test_camel_case_bucket_tokens():
    assert roles.assign([b("AcmeProduction")])["AcmeProduction"][0] == "production"


def test_production_signals_win_over_non_production_signals():
    """Tier assignment rules of the S3 data protection review method: the more protective role wins when signals conflict."""
    out = roles.assign([b("orders", tags={"env": "dev"}), b("orders-dev"), b("ledger-dev", tags={"criticality": "high"}),
                        b("orders-prod", tags={"Environment": "staging"})], account_name="acme-prod")
    assert out["orders"] == ("production", "Account name acme-prod")
    assert out["orders-dev"][0] == "production"
    assert out["ledger-dev"] == ("production", "Environment or criticality tag")
    assert out["orders-prod"][0] == "production"
    out = roles.assign([b("x", tags={"env": "dev", "criticality": "p1"})], account_name="acme-dev")
    assert out["x"] == ("production", "Environment or criticality tag")
    out = roles.assign([b("orders", tags={"env": "dev"}), b("orders-2")], account_name="acme-dev")
    assert out["orders"] == ("non_production", "Environment tag")
    assert out["orders-2"] == ("non_production", "Account name acme-dev")


def test_non_prod_phrases_are_never_prod_signals():
    out = roles.assign([b("orders-pre-prod"), b("orders-nonprod", tags={"env": "uat"})], account_name="acme-non-prod")
    assert out["orders-pre-prod"] == ("non_production", "Account name acme-non-prod")
    assert out["orders-nonprod"] == ("non_production", "Environment tag")
    assert roles.assign([b("orders-uat")], account_name="Shared")["orders-uat"] == ("non_production", "Bucket name")
    assert roles.assign([b("orders-pre-prod")], account_name="acme-prod")["orders-pre-prod"][0] == "production"
