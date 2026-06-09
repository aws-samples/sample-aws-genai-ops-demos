"""
ListRawObjectsLambda — first task in the G.O.A.T. Network Agent
Transformation_Workflow Step Functions state machine (Task 25, Reqs 6.8,
6.9, 6.12).

Purpose
-------
Given a ``capture_id`` from the workflow input, list every object under
``s3://{bucket}/raw/{capture_id}/`` and return a list of S3 keys that the
downstream ``Map`` state will fan out to ``ConvertPcapToParquetLambda``.

Workflow position::

    [ListRawObjects]  ──► Map(ConvertPcapToParquet) ──► RunCrawler ──► ValidateAthena

Input contract
--------------
The state machine accepts a single workflow input:

.. code-block:: json

    { "capture_id": "<Capture_Id_Format>" }

Output contract
---------------
On success, this Lambda returns a JSON object that the Step Functions
``Map`` state can consume directly:

.. code-block:: json

    {
        "capture_id": "abc123",
        "bucket": "goat-network-data-...",
        "raw_keys": [
            "raw/abc123/2026-04-20T12-00-00.pcap",
            "raw/abc123/2026-04-20T12-01-00.pcap"
        ],
        "raw_object_count": 2
    }

The ``raw_keys`` list is what the ``Map`` state iterates over.

Failure contract
----------------
Any exception bubbles out of the handler, causing the Step Functions task
to transition to its ``Catch`` block which routes to the workflow's
``Fail`` state with ``failed_task = "ListRawObjects"`` and
``error_reason`` set to the exception message (Req 6.9).

If the prefix exists but contains zero objects, the Lambda still returns
``success`` with an empty ``raw_keys`` list. The ``Map`` state degenerates
to a no-op and the workflow proceeds to ``RunCrawler`` and
``ValidateAthena``. ``ValidateAthena`` will then surface the empty
partition as a workflow failure (Req 6.8 mandates that a ``SELECT 1 FROM
pcap_logs WHERE capture_id = '<id>'`` validation succeed, which it cannot
on an empty partition).

Environment variables
---------------------
``DATA_BUCKET_NAME``
    Name of the Network_Data_Bucket (resolved by the InfraStack from the
    shared GOATData export or the dedicated NetworkDataStack). Sourced as
    a fixed environment variable rather than from the event so the Lambda
    cannot be coerced into listing arbitrary buckets even if the workflow
    input were tampered with.

The Lambda's IAM role is scoped to ``s3:ListBucket`` on this bucket only
(Task 25 IAM block on the InfraStack), with the ``s3:prefix`` condition
restricting reads to ``raw/`` to keep the blast radius bounded.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

import boto3
from botocore.exceptions import ClientError


_S3 = None


def _get_s3_client():
    """Lazy-init S3 client so cold start cost is paid once per Lambda
    container, not once per invocation."""
    global _S3
    if _S3 is None:
        _S3 = boto3.client("s3")
    return _S3


def _validate_capture_id(value: Any) -> str:
    """Mirror the Capture_Id_Format check in the agent's
    ``validation.py`` so a malformed ID never reaches an S3 API call. We
    keep this self-contained instead of importing from the agent package
    because Step Functions Lambdas are deployed independently of the
    agent container.

    Capture_Id_Format: 1..128 chars from ``[A-Za-z0-9_-]``.
    """
    if not isinstance(value, str):
        raise ValueError(
            f"capture_id must be a string, got {type(value).__name__}"
        )
    if not (1 <= len(value) <= 128):
        raise ValueError(
            f"capture_id length {len(value)} outside allowed range 1..128"
        )
    for ch in value:
        if not (ch.isalnum() or ch in ("_", "-")):
            raise ValueError(
                f"capture_id contains disallowed character {ch!r}; "
                "allowed character set is [A-Za-z0-9_-]"
            )
    return value


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """List every S3 object under ``raw/{capture_id}/`` for the
    Transformation_Workflow's downstream ``Map`` state to fan out.

    Args:
        event: The Step Functions task input. Must contain ``capture_id``.
        _context: Standard Lambda context object (unused).

    Returns:
        Dict with ``capture_id``, ``bucket``, ``raw_keys`` (list of S3
        object keys), and ``raw_object_count`` (integer).

    Raises:
        ValueError: When ``capture_id`` is missing or malformed.
        RuntimeError: When ``DATA_BUCKET_NAME`` is unset.
        botocore.exceptions.ClientError: Any S3 API failure surfaces
            unchanged so Step Functions captures it via ``Catch`` and the
            workflow transitions to the ``Fail`` state.
    """
    capture_id = _validate_capture_id(event.get("capture_id"))

    bucket = os.environ.get("DATA_BUCKET_NAME")
    if not bucket:
        raise RuntimeError(
            "DATA_BUCKET_NAME environment variable is unset; the "
            "ListRawObjectsLambda cannot resolve the Network_Data_Bucket"
        )

    prefix = f"raw/{capture_id}/"
    s3 = _get_s3_client()

    raw_keys: List[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    try:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                key = obj.get("Key")
                if key is None:
                    continue
                # Skip "directory marker" objects (zero-byte keys ending
                # in /). They are an artifact of console-created folders
                # and contain no pcap data.
                if key.endswith("/") and obj.get("Size", 0) == 0:
                    continue
                raw_keys.append(key)
    except ClientError as exc:
        # Re-raise so Step Functions captures it via Catch and routes
        # to the Fail state with the underlying error reason. Pre-pend
        # the failed_task name so debugging the State Machine execution
        # history is straightforward.
        raise RuntimeError(
            f"ListRawObjects: s3:ListObjectsV2 failed for "
            f"bucket={bucket} prefix={prefix}: {exc}"
        ) from exc

    return {
        "capture_id": capture_id,
        "bucket": bucket,
        "raw_keys": raw_keys,
        "raw_object_count": len(raw_keys),
    }
