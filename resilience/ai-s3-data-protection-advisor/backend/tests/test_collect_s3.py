import boto3
from botocore.stub import Stubber

from s3dp.collect import s3 as col


def _client():
    c = boto3.client("s3", region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="x")
    return c, Stubber(c)


def _stub_all(st, bucket, versioning=None, lock_error="ObjectLockConfigurationNotFoundError", repl=None,
              repl_error="ReplicationConfigurationNotFoundError", policy_status=None):
    p = {"Bucket": bucket}
    st.add_response("get_bucket_versioning", versioning or {}, p)
    if lock_error:
        st.add_client_error("get_object_lock_configuration", lock_error, http_status_code=404, expected_params=p)
    if repl:
        st.add_response("get_bucket_replication", repl, p)
    else:
        st.add_client_error("get_bucket_replication", repl_error, http_status_code=404, expected_params=p)
    st.add_response("get_bucket_encryption", {"ServerSideEncryptionConfiguration": {"Rules": [
        {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "aws:kms"}, "BucketKeyEnabled": True}]}}, p)
    st.add_client_error("get_bucket_lifecycle_configuration", "NoSuchLifecycleConfiguration", http_status_code=404,
                        expected_params=p)
    st.add_response("get_public_access_block", {"PublicAccessBlockConfiguration": {
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True}}, p)
    st.add_client_error("get_bucket_policy", "NoSuchBucketPolicy", http_status_code=404, expected_params=p)
    if policy_status is not None:
        st.add_response("get_bucket_policy_status", policy_status, p)
    else:
        st.add_client_error("get_bucket_policy_status", "NoSuchBucketPolicy", http_status_code=404, expected_params=p)
    st.add_response("get_bucket_ownership_controls",
                    {"OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}}, p)
    st.add_response("get_bucket_tagging", {"TagSet": [{"Key": "Environment", "Value": "prod"}]}, p)
    st.add_response("get_bucket_logging", {}, p)
    st.add_response("get_bucket_notification_configuration", {}, p)


def test_typed_absence_is_absent_and_values_are_read():
    c, st = _client()
    _stub_all(st, "orders-prod", versioning={"Status": "Enabled", "MFADelete": "Disabled"})
    with st:
        raw = col.collect_bucket(c, "orders-prod")
    assert raw["versioning"] == {"ok": {"status": "Enabled", "mfa_delete": "Disabled"}}
    assert raw["object_lock"] == {"absent": True}
    assert raw["replication"] == {"absent": True}
    assert raw["encryption"] == {"ok": {"algorithm": "aws:kms", "bucket_key": True}}
    assert raw["lifecycle"] == {"absent": True}
    assert raw["tags"] == {"ok": {"Environment": "prod"}}
    assert raw["logging"] == {"ok": {"enabled": False, "target": None}}
    assert raw["eventbridge"] == {"ok": False}


def test_access_denied_is_an_error_not_absence():
    c, st = _client()
    _stub_all(st, "secret-bkt", lock_error="AccessDenied", repl_error="AccessDenied")
    with st:
        raw = col.collect_bucket(c, "secret-bkt")
    assert raw["object_lock"] == {"error": "AccessDenied"}
    assert raw["replication"] == {"error": "AccessDenied"}
    assert raw["versioning"] == {"ok": {"status": None, "mfa_delete": None}}


def test_replication_rules_are_flattened():
    c, st = _client()
    repl = {"ReplicationConfiguration": {"Role": "arn:aws:iam::111122223333:role/r", "Rules": [{
        "Status": "Enabled", "Destination": {"Bucket": "arn:aws:s3:::orders-prod-dr", "StorageClass": "GLACIER_IR",
                                             "ReplicationTime": {"Status": "Enabled", "Time": {"Minutes": 15}}},
        "DeleteMarkerReplication": {"Status": "Enabled"}}]}}
    _stub_all(st, "orders-prod", versioning={"Status": "Enabled"}, repl=repl)
    with st:
        raw = col.collect_bucket(c, "orders-prod")
    assert raw["replication"] == {"ok": [{"status": "Enabled", "destination": "orders-prod-dr",
                                          "storage_class": "GLACIER_IR", "delete_markers": True, "rtc": True}]}


def test_list_buckets_by_region_paginates():
    c, st = _client()
    st.add_response("list_buckets", {"Buckets": [{"Name": "a", "BucketRegion": "eu-west-1"}],
                                     "ContinuationToken": "t"}, {"MaxBuckets": 1000})
    st.add_response("list_buckets", {"Buckets": [{"Name": "b"}]}, {"MaxBuckets": 1000, "ContinuationToken": "t"})
    with st:
        assert col.list_buckets(c) == [{"name": "a", "region": "eu-west-1"}, {"name": "b", "region": None}]


def _loc(constraint):
    c, st = _client()
    resp = {} if constraint is None else {"LocationConstraint": constraint}
    st.add_response("get_bucket_location", resp, {"Bucket": "b"})
    with st:
        return col.bucket_region(c, "b")


def test_bucket_region_maps_legacy_values():
    assert _loc("EU") == "eu-west-1"
    assert _loc(None) == "us-east-1"
    assert _loc("") == "us-east-1"
    assert _loc("ap-south-1") == "ap-south-1"


def test_bucket_region_unreadable_is_none():
    c, st = _client()
    st.add_client_error("get_bucket_location", "AccessDenied", http_status_code=403)
    with st:
        assert col.bucket_region(c, "b") is None


def test_replication_defaults_are_unknown_not_assumed():
    c, st = _client()
    v1 = {"Status": "Enabled", "Prefix": "", "Destination": {"Bucket": "arn:aws:s3:::d1"}}
    v2 = {"Status": "Enabled", "Priority": 1, "Filter": {}, "Destination": {"Bucket": "arn:aws:s3:::d2"}}
    v2_off = {"Status": "Enabled", "Priority": 2, "Filter": {}, "Destination": {"Bucket": "arn:aws:s3:::d3"},
              "DeleteMarkerReplication": {"Status": "Disabled"}}
    _stub_all(st, "b", repl={"ReplicationConfiguration": {"Role": "arn:aws:iam::111122223333:role/r",
                                                          "Rules": [v1, v2, v2_off]}})
    with st:
        rules = col.collect_bucket(c, "b")["replication"]["ok"]
    assert [r["storage_class"] for r in rules] == [None, None, None]
    assert [r["delete_markers"] for r in rules] == [True, None, False]


def test_read_turns_transport_and_malformed_errors_into_unknown():
    from botocore.exceptions import EndpointConnectionError

    def boom():
        raise EndpointConnectionError(endpoint_url="https://x")

    def malformed():
        return {}["Rules"]

    assert col._read("replication", boom) == {"error": "EndpointConnectionError"}
    assert col._read("replication", malformed) == {"error": "UnexpectedResponse"}


def test_policy_status_without_is_public_is_unknown():
    c, st = _client()
    _stub_all(st, "b", policy_status={"PolicyStatus": {}})
    with st:
        assert col.collect_bucket(c, "b")["policy_status"] == {"ok": None}
