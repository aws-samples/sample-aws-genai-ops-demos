import boto3
from botocore.stub import Stubber

from s3dp.collect import scan
from s3dp.derive import inventory

ACCOUNT, REGION, BUCKET = "111122223333", "eu-west-1", "orders-prod"


class FakeSession:
    """client(name, region_name=None, config=None) returns pre-stubbed clients, keyed by (name, region)."""
    region_name = REGION

    def __init__(self):
        self.stubs = {}

    def stub(self, name, region=None):
        c = boto3.client(name, region_name=region or REGION, aws_access_key_id="x", aws_secret_access_key="x")
        self.stubs[(name, region)] = (c, Stubber(c))
        return self.stubs[(name, region)][1]

    def client(self, name, region_name=None, config=None):
        return self.stubs[(name, region_name)][0]

    def get_partition_for_region(self, region):
        return "aws"


def _session():
    s = FakeSession()
    s.stub("s3").add_response("list_buckets", {"Buckets": [{"Name": BUCKET, "BucketRegion": REGION}]})
    s.stub("ce", "us-east-1").add_client_error("get_cost_and_usage", "AccessDeniedException", http_status_code=400)
    iam = s.stub("iam")
    iam.add_response("list_account_aliases", {"AccountAliases": ["acme"], "IsTruncated": False})
    iam.add_response("list_roles", {"Roles": [], "IsTruncated": False})
    s.stub("s3control").add_response("get_public_access_block", {"PublicAccessBlockConfiguration": dict.fromkeys(
        ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"), True)})
    bk = s.stub("backup", REGION)
    for op in ("list_protected_resources", "list_backup_plans", "list_backup_vaults", "describe_region_settings",
               "list_restore_testing_plans", "list_frameworks"):
        bk.add_client_error(op, "AccessDeniedException", http_status_code=403)
    s.stub("cloudformation", REGION).add_response("list_stacks", {"StackSummaries": []})
    s.stub("config", REGION).add_client_error("describe_configuration_recorders", "AccessDeniedException",
                                              http_status_code=400)
    s.stub("securityhub", REGION).add_client_error("get_enabled_standards", "InvalidAccessException",
                                                   http_status_code=401)
    cw = s.stub("cloudwatch", REGION)
    cw.add_response("list_metrics", {"Metrics": []})
    cw.add_response("list_metrics", {"Metrics": []})
    rs3 = s.stub("s3", REGION)
    rs3.add_response("get_bucket_versioning", {"Status": "Enabled"})
    for op, code in (("get_object_lock_configuration", "ObjectLockConfigurationNotFoundError"),
                     ("get_bucket_replication", "ReplicationConfigurationNotFoundError"),
                     ("get_bucket_encryption", "ServerSideEncryptionConfigurationNotFoundError"),
                     ("get_bucket_lifecycle_configuration", "NoSuchLifecycleConfiguration"),
                     ("get_public_access_block", "NoSuchPublicAccessBlockConfiguration"),
                     ("get_bucket_policy", "NoSuchBucketPolicy"), ("get_bucket_policy_status", "NoSuchBucketPolicy"),
                     ("get_bucket_ownership_controls", "OwnershipControlsNotFoundError"),
                     ("get_bucket_tagging", "NoSuchTagSet")):
        rs3.add_client_error(op, code, http_status_code=404)
    rs3.add_response("get_bucket_logging", {})
    rs3.add_response("get_bucket_notification_configuration", {})
    return s


def test_collect_account_survives_denied_services():
    s = _session()
    for _, st in s.stubs.values():
        st.activate()
    raw = scan.collect_account(s, ACCOUNT, today="2026-11-25")
    for _, st in s.stubs.values():
        st.assert_no_pending_responses()

    run = raw["run"]
    assert run["regions_scanned"] == [REGION]                      # Cost Explorer denied: regions from buckets
    labels = [u[0] for u in run["unavailable"]]
    assert "S3 spend per region (Cost Explorer)" in labels
    assert f"AWS Config ({REGION})" in labels
    assert any(x.startswith(f"AWS Backup ({REGION})") for x in labels)
    assert run["api_errors"] >= 8                                  # CE + 6 Backup calls + Config

    meta = raw["accounts"][ACCOUNT]
    assert meta["name"] == "acme" and meta["account_bpa"] == "on"
    assert meta["config"]["recording"] is None and meta["config"]["s3_rules"] is None   # unknown kept, not False/[]
    assert meta["config"]["unreadable_regions"] == [REGION]
    assert meta["security_hub"] is False

    region = raw["regions"][ACCOUNT][REGION]
    assert region["outcome"].startswith("partial") and "backup" in region["errors"]
    assert region["backup"]["protected_s3"] is None

    inv = inventory.build(raw)
    (b,) = inv["buckets"]
    assert b["name"] == BUCKET and b["versioning"] == "Enabled"
    assert b["aws_backup"] == "NotVerified"
    assert inv["governance"]["config"]["recording_unknown"] == [ACCOUNT]
    assert inv["backup_posture"]["vaults"] is None
