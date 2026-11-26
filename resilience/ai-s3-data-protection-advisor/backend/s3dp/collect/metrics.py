"""S3 CloudWatch metrics: daily storage metrics (free) and request metrics (when enabled).

Request metrics are found only through a filter named EntireBucket; buckets without it give requests=None (unknown).
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from botocore.exceptions import BotoCoreError, ClientError

from s3dp.model import STORAGE_TYPE_CLASS

DAY = 86400


def list_storage_types(cw):
    """[(bucket, storage_type)] for every BucketSizeBytes series in the region."""
    out = []
    for page in cw.get_paginator("list_metrics").paginate(Namespace="AWS/S3", MetricName="BucketSizeBytes"):
        for m in page["Metrics"]:
            d = {x["Name"]: x["Value"] for x in m["Dimensions"]}
            if "BucketName" in d and "StorageType" in d:
                out.append((d["BucketName"], d["StorageType"]))
    return out


def request_metric_buckets(cw):
    """Buckets with an EntireBucket request metrics filter (paid opt-in)."""
    out = set()
    for page in cw.get_paginator("list_metrics").paginate(Namespace="AWS/S3", MetricName="PutRequests"):
        for m in page["Metrics"]:
            d = {x["Name"]: x["Value"] for x in m["Dimensions"]}
            if d.get("FilterId") == "EntireBucket":
                out.add(d["BucketName"])
    return out


def build_queries(listed, request_buckets):
    """GetMetricData queries. Query ids are q<n>; the caller keeps the index returned by `index_of`."""
    q = []

    def add(metric, dims, stat):
        q.append({"Id": f"q{len(q)}", "ReturnData": True, "MetricStat": {
            "Metric": {"Namespace": "AWS/S3", "MetricName": metric,
                       "Dimensions": [{"Name": k, "Value": v} for k, v in dims]},
            "Period": DAY, "Stat": stat}})

    for bucket, st in listed:
        add("BucketSizeBytes", [("BucketName", bucket), ("StorageType", st)], "Average")
    for bucket in sorted({b for b, _ in listed}):
        add("NumberOfObjects", [("BucketName", bucket), ("StorageType", "AllStorageTypes")], "Average")
    for bucket in sorted(request_buckets):
        for metric in ("PutRequests", "DeleteRequests", "GetRequests"):
            add(metric, [("BucketName", bucket), ("FilterId", "EntireBucket")], "Sum")
    return q


def index_of(queries):
    """Query id -> (bucket, kind, storage_type)."""
    kind = {"BucketSizeBytes": "size", "NumberOfObjects": "objects", "PutRequests": "puts",
            "DeleteRequests": "deletes", "GetRequests": "gets"}
    out = {}
    for x in queries:
        m = x["MetricStat"]["Metric"]
        d = {y["Name"]: y["Value"] for y in m["Dimensions"]}
        out[x["Id"]] = (d["BucketName"], kind[m["MetricName"]], d.get("StorageType") if m["MetricName"] == "BucketSizeBytes" else None)
    return out


def assemble(index, results, buckets):
    """Per bucket: size (newest day on which every storage type with data has a point, summed over storage types),
    by-class split, object series, request sums, and size_series (daily total bytes, oldest -> newest, on the days
    where every storage type with data has a point; [] when a size series is incomplete). A size series that did not come back Complete makes the size
    "partial" (None); results may carry a "StatusCode" per query id."""
    series, incomplete = defaultdict(dict), set()
    for r in results:
        b, kind, st = index[r["Id"]]
        pts = sorted(zip(r.get("Timestamps", []), r.get("Values", [])))
        series[b][(kind, st)] = pts
        if kind == "size" and r.get("StatusCode", "Complete") != "Complete":
            incomplete.add(b)
    out = {}
    for b in buckets:
        s = series.get(b, {})
        sizes = {st: dict(pts) for (k, st), pts in s.items() if k == "size" and pts}
        common = set.intersection(*(set(d) for d in sizes.values())) if sizes else set()
        newest = max(common) if common and b not in incomplete else None
        by_class = defaultdict(int)
        if newest:
            for st, d in sizes.items():
                by_class[STORAGE_TYPE_CLASS.get(st, "UNKNOWN")] += int(d[newest])
        partial = bool(sizes) and newest is None
        objs = s.get(("objects", None), [])
        req = {k: sum(v for _, v in s[(k, None)]) for k in ("puts", "deletes", "gets") if (k, None) in s}
        out[b] = {
            "size_bytes": sum(by_class.values()) if newest else None,
            "size_series": [] if b in incomplete else [sum(d[t] for d in sizes.values()) for t in sorted(common)],
            "by_class": dict(by_class),
            "size_as_of": newest.strftime("%Y-%m-%d") if newest else None,
            "size_status": "measured" if newest else ("partial" if partial else "unavailable"),
            "objects": int(objs[-1][1]) if objs else None,
            "objects_series": [v for _, v in objs],
            "requests": req or None,
        }
    return out


def _unavailable(buckets, code):
    return {b: {"size_bytes": None, "by_class": {}, "size_as_of": None, "size_status": "unavailable",
                "objects": None, "objects_series": [], "requests": None, "size_series": [],
                "error": code} for b in buckets}


def collect_region(cw, buckets, now=None):
    """Metrics for `buckets` in one region (one CloudWatch client per region). On a CloudWatch failure every bucket
    is returned with size_status "unavailable" and an "error" code."""
    now = now or datetime.now(timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=30)
    wanted = set(buckets)
    try:
        listed = [x for x in list_storage_types(cw) if x[0] in wanted]
        queries = build_queries(listed, request_metric_buckets(cw) & wanted)
        index = index_of(queries)
        merged = defaultdict(lambda: {"Timestamps": [], "Values": [], "StatusCode": "Complete"})
        paginator = cw.get_paginator("get_metric_data")
        for i in range(0, len(queries), 500):
            for page in paginator.paginate(MetricDataQueries=queries[i:i + 500], StartTime=start, EndTime=end):
                for r in page["MetricDataResults"]:
                    m = merged[r["Id"]]
                    m["Id"] = r["Id"]
                    m["Timestamps"] += r.get("Timestamps", [])
                    m["Values"] += r.get("Values", [])
                    m["StatusCode"] = r.get("StatusCode", "Complete")   # the last page carries the final status
    except ClientError as e:
        return _unavailable(buckets, e.response.get("Error", {}).get("Code", "Unknown"))
    except BotoCoreError as e:
        return _unavailable(buckets, type(e).__name__)
    return assemble(index, list(merged.values()), buckets)
