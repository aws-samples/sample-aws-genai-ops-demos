"""Run store: reports bucket (S3) and runs table (DynamoDB). Clients are passed in so tests can fake them."""
import json
import os
from decimal import Decimal

from botocore.exceptions import ClientError


def _to_ddb(v):
    # DynamoDB rejects floats; Decimal(str(x)) avoids binary noise such as 0.1 -> 0.1000000000000000055
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("DynamoDB cannot store NaN or infinity")
        return Decimal(str(v))
    if isinstance(v, dict):
        return {k: _to_ddb(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_ddb(x) for x in v]
    return v


def _from_ddb(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, dict):
        return {k: _from_ddb(x) for k, x in v.items()}
    if isinstance(v, (list, set)):
        return [_from_ddb(x) for x in v] if isinstance(v, list) else sorted(_from_ddb(x) for x in v)
    return v


class Store:
    def __init__(self, s3, ddb, bucket, table):
        """s3: boto3 S3 client. ddb: boto3 DynamoDB resource (it converts plain Python values). bucket, table: names."""
        self.s3, self.bucket, self.table = s3, bucket, ddb.Table(table)

    def put_json(self, key, obj):
        self.put_bytes(key, json.dumps(obj, default=str).encode(), "application/json")

    def get_json(self, key):
        return json.loads(self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read())

    def put_bytes(self, key, body, content_type):
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)

    def exists(self, key):
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def update_run(self, run_id, **attrs):
        """Upsert only the given attributes, so the API and the pipeline never overwrite each other's."""
        attrs = {k: _to_ddb(v) for k, v in attrs.items() if v is not None}
        if not attrs:
            return
        names = {f"#a{i}": k for i, k in enumerate(attrs)}      # "status" and others are DynamoDB reserved words
        values = {f":v{i}": v for i, v in enumerate(attrs.values())}
        expr = "SET " + ", ".join(f"#a{i} = :v{i}" for i in range(len(attrs)))
        self.table.update_item(Key={"run_id": run_id}, UpdateExpression=expr,
                               ExpressionAttributeNames=names, ExpressionAttributeValues=values)

    def get_run(self, run_id):
        item = self.table.get_item(Key={"run_id": run_id}).get("Item")
        return _from_ddb(item) if item else None

    def list_runs(self, limit=50):
        items, kw = [], {}
        while True:
            r = self.table.scan(**kw)
            items += r.get("Items", [])
            if not r.get("LastEvaluatedKey"):
                break
            kw = {"ExclusiveStartKey": r["LastEvaluatedKey"]}
        items = [_from_ddb(i) for i in items]
        # run ids start with a UTC timestamp, so they break ties (and order records the API has not stamped yet)
        items.sort(key=lambda i: (i.get("started_at") or "", i["run_id"]), reverse=True)
        return items[:limit]


def s3_client(**kw):
    """S3 client whose presigned URLs use the regional host (<bucket>.s3.<region>.amazonaws.com), also in
    us-east-1: the dashboard's CSP connect-src allows only that host."""
    import boto3
    from botocore.config import Config
    config = Config(signature_version="s3v4", s3={"addressing_style": "virtual", "us_east_1_regional_endpoint": "regional"})
    return boto3.client("s3", config=config, **kw)


def from_env():
    import boto3
    s3 = s3_client()
    return Store(s3, boto3.resource("dynamodb"), os.environ["REPORTS_BUCKET"], os.environ["RUNS_TABLE"])
