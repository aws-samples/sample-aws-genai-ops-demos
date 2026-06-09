"""
RunCrawlerLambda — third task in the Transformation_Workflow.

REPLACED: Instead of running a Glue Crawler (which creates its own table
when the schema doesn't match the pre-declared pcap_logs table), this
Lambda now directly registers the Hive-style partition using
glue:BatchCreatePartition. This is deterministic, fast (~1s vs ~60s for
a Crawler), and always targets the correct table.

The partition is keyed by capture_id (the single partition column on the
pcap_logs table). The S3 location points to
s3://{bucket}/parquet/capture_id={capture_id}/ where the
ConvertPcapToParquet Lambda wrote the Parquet files.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict

import boto3
from botocore.exceptions import ClientError

_GLUE = None
GLUE_DATABASE_ENV = "GLUE_DATABASE"
DATA_BUCKET_NAME_ENV = "DATA_BUCKET_NAME"


def _get_glue_client():
    global _GLUE
    if _GLUE is None:
        _GLUE = boto3.client("glue")
    return _GLUE


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """Register the Glue partition for the processed capture.

    Input (from Step Functions):
        capture_id: str — the capture whose parquet files are ready.

    The Lambda reads GLUE_DATABASE and DATA_BUCKET_NAME from env vars
    (set by CDK). The table name is always 'pcap_logs'.
    """
    capture_id = event.get("capture_id")
    if not capture_id:
        raise ValueError("RunCrawler: 'capture_id' is required in event")

    database = os.environ.get(GLUE_DATABASE_ENV)
    bucket = os.environ.get(DATA_BUCKET_NAME_ENV)
    if not database:
        raise RuntimeError(
            f"RunCrawler: {GLUE_DATABASE_ENV} environment variable not set"
        )
    if not bucket:
        raise RuntimeError(
            f"RunCrawler: {DATA_BUCKET_NAME_ENV} environment variable not set"
        )

    table_name = "pcap_logs"
    partition_location = f"s3://{bucket}/parquet/capture_id={capture_id}/"

    glue = _get_glue_client()

    # Get the table's storage descriptor to use as the partition template
    try:
        table_resp = glue.get_table(DatabaseName=database, Name=table_name)
    except ClientError as exc:
        raise RuntimeError(
            f"RunCrawler: glue:GetTable failed for {database}.{table_name}: {exc}"
        ) from exc

    table_sd = table_resp["Table"]["StorageDescriptor"]

    # Build partition storage descriptor (same as table but with the
    # partition-specific S3 location)
    partition_sd = {
        "Columns": table_sd["Columns"],
        "Location": partition_location,
        "InputFormat": table_sd.get(
            "InputFormat",
            "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
        ),
        "OutputFormat": table_sd.get(
            "OutputFormat",
            "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
        ),
        "SerdeInfo": table_sd.get("SerdeInfo", {
            "SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe",
        }),
    }

    # Try to create the partition (idempotent — if it already exists, update it)
    try:
        glue.batch_create_partition(
            DatabaseName=database,
            TableName=table_name,
            PartitionInputList=[
                {
                    "Values": [capture_id],
                    "StorageDescriptor": partition_sd,
                }
            ],
        )
        action = "created"
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "AlreadyExistsException":
            # Partition already exists — update its location
            glue.update_partition(
                DatabaseName=database,
                TableName=table_name,
                PartitionValueList=[capture_id],
                PartitionInput={
                    "Values": [capture_id],
                    "StorageDescriptor": partition_sd,
                },
            )
            action = "updated"
        else:
            raise RuntimeError(
                f"RunCrawler: glue:BatchCreatePartition failed: {exc}"
            ) from exc

    return {
        "capture_id": capture_id,
        "partition_action": action,
        "partition_location": partition_location,
        "table": f"{database}.{table_name}",
    }
