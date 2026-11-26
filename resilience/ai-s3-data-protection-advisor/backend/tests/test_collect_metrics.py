from datetime import datetime, timezone

from s3dp.collect import metrics


def test_queries_cover_every_storage_type_and_objects():
    listed = [("b1", "StandardStorage"), ("b1", "GlacierStorage"), ("b2", "StandardStorage")]
    q = metrics.build_queries(listed, request_buckets={"b2"})
    ids = {x["Id"]: x for x in q}
    assert len([x for x in q if x["MetricStat"]["Metric"]["MetricName"] == "BucketSizeBytes"]) == 3
    assert len([x for x in q if x["MetricStat"]["Metric"]["MetricName"] == "NumberOfObjects"]) == 2
    assert len([x for x in q if x["MetricStat"]["Metric"]["MetricName"] == "PutRequests"]) == 1
    assert all(len(i) <= 255 and i[0].islower() for i in ids)


def test_assemble_takes_newest_complete_day_and_keeps_missing_as_none():
    d1, d2 = datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2026, 11, 2, tzinfo=timezone.utc)
    index = {"q0": ("b1", "size", "StandardStorage"), "q1": ("b1", "size", "GlacierStorage"),
             "q2": ("b1", "objects", None), "q3": ("b2", "objects", None)}
    results = [
        {"Id": "q0", "Timestamps": [d2, d1], "Values": [100.0, 90.0]},
        {"Id": "q1", "Timestamps": [d2], "Values": [50.0]},
        {"Id": "q2", "Timestamps": [d1, d2], "Values": [10.0, 12.0]},
        {"Id": "q3", "Timestamps": [], "Values": []},
    ]
    out = metrics.assemble(index, results, ["b1", "b2"])
    assert out["b1"]["size_bytes"] == 150
    assert out["b1"]["by_class"] == {"STANDARD": 100, "GLACIER": 50}
    assert out["b1"]["objects_series"] == [10.0, 12.0]
    assert out["b1"]["size_as_of"] == "2026-11-02"
    assert out["b2"]["size_bytes"] is None and out["b2"]["size_status"] == "unavailable"


def _ts(day):
    return datetime(2026, 11, day, tzinfo=timezone.utc)


def test_assemble_uses_newest_day_common_to_all_storage_types():
    index = {"q0": ("b", "size", "StandardStorage"), "q1": ("b", "size", "GlacierStorage")}
    results = [{"Id": "q0", "Timestamps": [_ts(2), _ts(3)], "Values": [100.0, 110.0]},
               {"Id": "q1", "Timestamps": [_ts(2)], "Values": [50.0]}]
    out = metrics.assemble(index, results, ["b"])["b"]
    assert out["size_bytes"] == 150 and out["size_as_of"] == "2026-11-02"


def test_assemble_no_common_day_is_partial():
    index = {"q0": ("b", "size", "StandardStorage"), "q1": ("b", "size", "GlacierStorage")}
    results = [{"Id": "q0", "Timestamps": [_ts(3)], "Values": [100.0]},
               {"Id": "q1", "Timestamps": [_ts(2)], "Values": [50.0]}]
    out = metrics.assemble(index, results, ["b"])["b"]
    assert out["size_bytes"] is None and out["size_status"] == "partial"


def test_assemble_incomplete_series_is_partial():
    index = {"q0": ("b", "size", "StandardStorage")}
    results = [{"Id": "q0", "Timestamps": [_ts(3)], "Values": [100.0], "StatusCode": "PartialData"}]
    out = metrics.assemble(index, results, ["b"])["b"]
    assert out["size_bytes"] is None and out["size_status"] == "partial"


def test_collect_region_failure_marks_every_bucket_unavailable():
    import boto3
    from botocore.stub import Stubber
    c = boto3.client("cloudwatch", region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="x")
    st = Stubber(c)
    st.add_client_error("list_metrics", "AccessDenied", http_status_code=403)
    with st:
        out = metrics.collect_region(c, ["a", "b"])
    assert set(out) == {"a", "b"}
    assert all(v["size_bytes"] is None and v["size_status"] == "unavailable" and v["error"] == "AccessDenied"
               for v in out.values())


def test_collect_region_keeps_final_status_across_pages():
    import boto3
    from botocore.stub import Stubber
    c = boto3.client("cloudwatch", region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="x")
    st = Stubber(c)
    dim = [{"Name": "BucketName", "Value": "b"}, {"Name": "StorageType", "Value": "StandardStorage"}]
    st.add_response("list_metrics", {"Metrics": [{"Dimensions": dim}]}, {"Namespace": "AWS/S3", "MetricName": "BucketSizeBytes"})
    st.add_response("list_metrics", {"Metrics": []}, {"Namespace": "AWS/S3", "MetricName": "PutRequests"})
    st.add_response("get_metric_data", {"MetricDataResults": [
        {"Id": "q0", "Timestamps": [_ts(3)], "Values": [10.0], "StatusCode": "PartialData"}], "NextToken": "t"}, None)
    st.add_response("get_metric_data", {"MetricDataResults": [
        {"Id": "q0", "Timestamps": [_ts(4)], "Values": [11.0], "StatusCode": "PartialData"},
        {"Id": "q1", "Timestamps": [], "Values": [], "StatusCode": "Complete"}]}, None)
    with st:
        out = metrics.collect_region(c, ["b"], now=datetime(2026, 11, 10, tzinfo=timezone.utc))
    assert out["b"]["size_bytes"] is None and out["b"]["size_status"] == "partial"


def test_assemble_size_series_sums_common_days_oldest_first():
    index = {"q0": ("b", "size", "StandardStorage"), "q1": ("b", "size", "GlacierStorage"),
             "q2": ("c", "size", "StandardStorage")}
    results = [{"Id": "q0", "Timestamps": [_ts(4), _ts(2), _ts(3)], "Values": [120.0, 100.0, 110.0]},
               {"Id": "q1", "Timestamps": [_ts(3), _ts(4)], "Values": [50.0, 55.0]},
               {"Id": "q2", "Timestamps": [_ts(3)], "Values": [9.0], "StatusCode": "PartialData"}]
    out = metrics.assemble(index, results, ["b", "c", "d"])
    assert out["b"]["size_series"] == [160.0, 175.0]
    assert out["c"]["size_series"] == [] and out["d"]["size_series"] == []
    assert out["b"]["size_bytes"] == 175
