import io
from datetime import datetime, timezone
import json
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError

from s3dp.service import labels
from s3dp.service.store import Store


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = (Body, ContentType)

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)][0])}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "404", "Message": "x"}}, "HeadObject")


class FakeTable:
    def __init__(self, page=2):
        self.items, self.page = {}, page

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues):
        for n, v in ExpressionAttributeValues.items():
            assert not isinstance(v, float), "DynamoDB rejects floats"
        item = self.items.setdefault(Key["run_id"], dict(Key))
        for part in UpdateExpression.removeprefix("SET ").split(", "):
            name, val = (x.strip() for x in part.split("="))
            item[ExpressionAttributeNames[name]] = ExpressionAttributeValues[val]

    def get_item(self, Key):
        return {"Item": self.items[Key["run_id"]]} if Key["run_id"] in self.items else {}

    def scan(self, ExclusiveStartKey=None):
        ids = sorted(self.items)
        start = ids.index(ExclusiveStartKey["run_id"]) + 1 if ExclusiveStartKey else 0
        r = {"Items": [self.items[i] for i in ids[start:start + self.page]]}
        if start + self.page < len(ids):
            r["LastEvaluatedKey"] = {"run_id": ids[start + self.page - 1]}
        return r


class FakeDdb:
    def __init__(self, table):
        self.t = table

    def Table(self, name):
        return self.t


def make():
    s3, table = FakeS3(), FakeTable()
    return Store(s3, FakeDdb(table), "b", "t"), s3, table


def test_object_round_trip_and_exists():
    st, s3, _ = make()
    st.put_json("k.json", {"a": 1, "when": datetime(2026, 11, 25, tzinfo=timezone.utc)})
    assert st.get_json("k.json") == {"a": 1, "when": "2026-11-25 00:00:00+00:00"}
    assert s3.objects[("b", "k.json")][1] == "application/json"
    st.put_bytes("r.html", b"<p>", "text/html")
    assert st.exists("r.html") and not st.exists("missing")


def test_exists_raises_other_errors():
    st, s3, _ = make()
    s3.head_object = lambda **kw: (_ for _ in ()).throw(ClientError({"Error": {"Code": "403"}}, "HeadObject"))
    with pytest.raises(ClientError):
        st.exists("k")


def test_update_run_upserts_converts_and_skips_none():
    st, _, table = make()
    st.update_run("scan-1", status="RUNNING", ratio=0.5, none=None, recs={"HIGH": 2.0})
    assert table.items["scan-1"]["ratio"] == Decimal("0.5") and "none" not in table.items["scan-1"]
    st.update_run("scan-1", execution_arn="arn")                   # a second writer keeps the first one's attributes
    run = st.get_run("scan-1")
    assert run["status"] == "RUNNING" and run["execution_arn"] == "arn" and run["ratio"] == 0.5
    assert isinstance(run["recs"]["HIGH"], int) and st.get_run("nope") is None


def test_list_runs_pages_and_orders_newest_first():
    st, _, _ = make()
    for i in range(5):
        st.update_run(f"scan-2026112{i}-000000", started_at=f"2026-11-2{i}T00:00:00Z")
    assert [r["run_id"][5:13] for r in st.list_runs()] == ["20261124", "20261123", "20261122", "20261121", "20261120"]
    assert len(st.list_runs(limit=2)) == 2


def test_update_run_rejects_nan_and_converts_number_sets():
    st, _, _ = make()
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            st.update_run("scan-1", x=bad)
    from s3dp.service.store import _from_ddb
    assert _from_ddb({"s": {Decimal(2), Decimal("1.5")}}) == {"s": [1.5, 2]}


def test_labels():
    assert len(labels.STEPS) == 7
    assert labels.label_for("accounts") == labels.STEPS[0]
    assert labels.label_for("collect-12") == labels.label_for("assemble") == labels.STEPS[2]
    assert labels.label_for("publish") == labels.STEPS[6]
    for name, i in (("accounts", 0), ("regions", 1), ("collect-0", 2), ("assemble", 2), ("classify", 3),
                    ("score", 4), ("narrative", 5), ("publish", 6)):
        assert labels.label_for(name) == labels.STEPS[i]
    assert labels.label_for("fail") is None        # the failure handler is not one of the seven steps
    assert labels.label_for("other") is None


import pytest


@pytest.mark.parametrize("region", ["us-east-1", "eu-west-3"])
def test_presigned_url_uses_the_regional_host(region):
    from s3dp.service.store import s3_client
    s3 = s3_client(region_name=region, aws_access_key_id="AKIAFAKE", aws_secret_access_key="fake")
    url = s3.generate_presigned_url("get_object", Params={"Bucket": "bkt", "Key": "runs/x/report.html"}, ExpiresIn=300)
    assert url.startswith(f"https://bkt.s3.{region}.amazonaws.com/runs/x/report.html?")
