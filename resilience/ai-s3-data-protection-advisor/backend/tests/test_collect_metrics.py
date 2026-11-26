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


# ---- incomplete object and request metrics (review F08)

from s3dp.derive import write_pattern

REQ_INDEX = {"q0": ("b", "size", "StandardStorage"), "q1": ("b", "objects", None),
             "q2": ("b", "puts", None), "q3": ("b", "deletes", None), "q4": ("b", "gets", None)}


def _req_results(status=None, drop=None):
    days = [_ts(d) for d in range(1, 8)]
    out = [{"Id": "q0", "Timestamps": days, "Values": [1e9] * 7},
           {"Id": "q1", "Timestamps": days, "Values": [1000.0] * 7}]
    for q in ("q2", "q3", "q4"):
        if q != drop:
            r = {"Id": q, "Timestamps": [], "Values": []}
            if status:
                r["StatusCode"] = status
            out.append(r)
    return out


def test_failed_or_missing_request_metrics_stay_unknown():
    for status in ("InternalError", "PartialData"):
        out = metrics.assemble(REQ_INDEX, _req_results(status), ["b"])["b"]
        assert out["requests"] is None and out["incomplete"] == ["requests"], status
        assert out["size_bytes"] == 1e9                              # the complete size series is still a measurement
    out = metrics.assemble(REQ_INDEX, _req_results(drop="q3"), ["b"])["b"]
    assert out["requests"] is None and out["incomplete"] == ["requests"]


def test_complete_zero_requests_are_a_measurement():
    out = metrics.assemble(REQ_INDEX, _req_results(), ["b"])["b"]
    assert out["requests"] == {"puts": 0, "deletes": 0, "gets": 0} and "incomplete" not in out


def test_incomplete_object_series_is_unknown():
    results = _req_results()
    results[1]["StatusCode"] = "InternalError"
    out = metrics.assemble(REQ_INDEX, results, ["b"])["b"]
    assert out["objects"] is None and out["objects_series"] == [] and out["incomplete"] == ["objects"]


def _pattern_and_cost(results):
    m = metrics.assemble(REQ_INDEX, results, ["b"])["b"]
    cost = write_pattern.vce({"STANDARD": m["size_bytes"]}, m["objects_series"], m["requests"],
                             size_series_changed=False, size_tb=m["size_bytes"] / 2**40)
    return write_pattern.classify(m["objects_series"], m["size_series"], m["requests"]), cost


def test_failed_requests_never_read_as_an_inactive_bucket_with_a_zero_cost():
    pattern, cost = _pattern_and_cost(_req_results("InternalError"))
    assert pattern == {"pattern": "static_or_inactive", "approximate": True}       # inferred, not measured
    assert (cost["basis"], cost["confidence"]) == ("prior", "medium")
    pattern, cost = _pattern_and_cost(_req_results())                               # genuine zero activity
    assert pattern == {"pattern": "inactive", "approximate": False}
    assert (cost["basis"], cost["confidence"]) == ("measured", "high")
