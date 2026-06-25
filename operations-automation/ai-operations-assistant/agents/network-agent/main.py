"""
G.O.A.T. Network Agent - VPC packet capture and pcap analysis
Plain Python handler with BedrockAgentCoreApp (sync entrypoint).

Mirrors the architectural pattern of the existing G.O.A.T. sub-agents
(cost-agent, health-agent, support-agent, ta-agent, cur-agent):
- BedrockAgentCoreApp with a sync @app.entrypoint
- AWS APIs called directly via boto3 (no Strands SDK, no @tool decorators)
- Dictionary-dispatch on the payload's top-level "action" field

This module implements the response envelope helper, the ACTIONS dispatch
table for all 20 documented actions, and the entrypoint that routes to
handler stubs. Handler bodies are filled in by subsequent tasks.

Security note: All SQL f-strings in this module use capture_id values validated
by Capture_Id_Format (alphanumeric + hyphens only, no quotes) and predicates
built by the validated flow_selector pipeline. Not raw user SQL.
"""
# nosec B608 — see Security note in module docstring

import json
import logging
import os
import secrets
import socket
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from bedrock_agentcore.runtime import BedrockAgentCoreApp

import state
from aws_utils import get_region
from validation import (
    ValidationError,
    validate_capture_id,
    validate_duration_minutes,
    validate_eni_ids,
    validate_filter_id,
    validate_idempotency_token,
    validate_min_size,
    validate_status_filter,
    validate_stream_id,
    validate_top_n,
)
from athena_helper import (
    AthenaConfigurationError,
    AthenaQueryFailedError,
    AthenaQueryTimeoutError,
    run_athena_query,
)
from flow_selector import (
    FlowSelectorError,
    ResolvedFlowSelector,
    build_flow_predicate,
    build_resolved_flow_set_metadata,
    query_matched_streams,
    resolve_flow_selector,
)
from sql_safety import (
    MAX_SQL_LENGTH,
    SqlShapeError,
    inject_capture_id_predicate,
    validate_sql_shape,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = BedrockAgentCoreApp()
AWS_REGION = get_region()


# ---------------------------------------------------------------------------
# Lazy boto3 client singletons
#
# Clients are created on first use and reused on subsequent calls so that
# the cold-start cost is paid once per container, not once per request.
# ---------------------------------------------------------------------------

_ec2_client = None
_scheduler_client = None
_sfn_client = None
_s3_client = None


def _get_ec2_client():
    """Return a cached boto3 EC2 client bound to ``AWS_REGION``.

    The client is created lazily on first use. Tests can reset the
    singleton by setting ``main._ec2_client = None`` between cases.
    """
    global _ec2_client
    if _ec2_client is None:
        _ec2_client = boto3.client("ec2", region_name=AWS_REGION)
    return _ec2_client


def _get_scheduler_client():
    """Return a cached boto3 EventBridge Scheduler client bound to ``AWS_REGION``.

    Used by:
        * :func:`handle_start_capture` to create the one-shot
          Auto_Stop_Schedule (Req 3.5) — see
          :func:`_create_auto_stop_schedule`.
        * :func:`handle_stop_capture` to delete the same schedule
          (Req 3.7) — see
          :func:`_delete_auto_stop_schedule_best_effort`.
    """
    global _scheduler_client
    if _scheduler_client is None:
        _scheduler_client = boto3.client("scheduler", region_name=AWS_REGION)
    return _scheduler_client


def _get_sfn_client():
    """Return a cached boto3 Step Functions client bound to ``AWS_REGION``.

    Used by :func:`handle_transform_capture` (Task 10, Req 3.12) to
    invoke ``stepfunctions:StartExecution`` against the
    Transformation_Workflow state machine ARN supplied via the
    ``TRANSFORMATION_SFN_ARN`` environment variable.
    """
    global _sfn_client
    if _sfn_client is None:
        _sfn_client = boto3.client("stepfunctions", region_name=AWS_REGION)
    return _sfn_client


def _get_s3_client():
    """Return a cached boto3 S3 client bound to ``AWS_REGION``.

    Used by :func:`handle_get_capture_progress` (Task 10, Req 3.17)
    to list ``raw/{capture_id}/`` in the Network_Data_Bucket and
    sum the uploaded object sizes.
    """
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=AWS_REGION)
    return _s3_client


# ---------------------------------------------------------------------------
# Environment-variable surface (set by NetworkRuntimeStack, CDK Task 28).
# Read at handler-call time (not at import) so test fixtures can patch
# ``os.environ`` between cases.
# ---------------------------------------------------------------------------

ENV_TRAFFIC_MIRROR_FILTER_ID = "TRAFFIC_MIRROR_FILTER_ID"
ENV_TRAFFIC_MIRROR_TARGET_ID = "TRAFFIC_MIRROR_TARGET_ID"
ENV_COLLECTOR_INSTANCE_ID = "COLLECTOR_INSTANCE_ID"
ENV_STOP_CAPTURE_INVOKER_LAMBDA_ARN = "STOP_CAPTURE_INVOKER_LAMBDA_ARN"
ENV_SCHEDULE_GROUP_NAME = "SCHEDULE_GROUP_NAME"
ENV_SCHEDULER_TARGET_ROLE_ARN = "SCHEDULER_TARGET_ROLE_ARN"
ENV_TRANSFORMATION_SFN_ARN = "TRANSFORMATION_SFN_ARN"
ENV_DATA_BUCKET_NAME = "DATA_BUCKET_NAME"


# ---------------------------------------------------------------------------
# Capture lifecycle constants
# ---------------------------------------------------------------------------

# Default capture duration when ``duration_minutes`` is omitted (Req 3.3).
DEFAULT_CAPTURE_DURATION_MINUTES = 15

# Capture_Concurrency_Limit: max simultaneous active Capture_Sessions (Req 4.5).
CAPTURE_CONCURRENCY_LIMIT = 5

# Per-call collector-readiness wait budget. Bounded by the 30 second
# response SLA in Req 3.1; the 120 second figure in Req 3.16 is the
# *maximum total* wait the agent performs across retries before failing
# definitively (see design section "Response Latency Reconciliation").
# We give the readiness check ~25 seconds so the rest of the handler
# (mirror-session create, DDB writes, schedule create) still fits within
# 30 seconds when the collector is ready instantly.
COLLECTOR_READINESS_WAIT_SECONDS = 25
COLLECTOR_READINESS_POLL_INTERVAL_SECONDS = 5

# Capture_Opt_In_Tag (Req 3.14): an ENI or its parent EC2 instance must
# carry this exact key/value for ``start_capture`` to permit mirroring.
CAPTURE_OPT_IN_TAG_KEY = "goat-network-capture-allowed"
CAPTURE_OPT_IN_TAG_VALUE = "true"


# ---------------------------------------------------------------------------
# AWS error classification (EH-2 in the design document)
# ---------------------------------------------------------------------------


def _classify_aws_error(exc: Exception) -> str:
    """Map a botocore exception to an ``errorCategory`` value.

    See Error Handling section EH-2 of the design document for the
    mapping. The category is surfaced in ``metadata.errorCategory`` so
    the orchestration agent can render category-specific chat replies.
    """
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("ThrottlingException", "Throttling", "RequestLimitExceeded"):
            return "aws_throttled"
        if code in ("AccessDeniedException", "UnauthorizedOperation"):
            return "aws_access_denied"
        if code in ("InternalError", "ServiceUnavailable"):
            return "aws_service_unavailable"
        if code in ("InvalidParameterValue", "InvalidParameter", "MalformedQuery"):
            return "aws_validation"
    return "aws_other"


# ---------------------------------------------------------------------------
# Validation error conversion (EH-1 in the design document)
# ---------------------------------------------------------------------------


def _validation_error_response(
    action_name: str,
    exc: ValidationError,
    source_api: str,
    data_freshness: str = "real-time",
) -> dict:
    """Convert a :class:`ValidationError` into the response envelope.

    Used by capture-lifecycle and pcap-query handlers to surface
    parameter-shape failures with ``metadata.errorCategory =
    "invalid_parameter"`` per design Error Handling section EH-1.

    Args:
        action_name: The action whose parameter check failed (e.g.
            ``"start_capture"``). Included verbatim in the
            ``formattedText`` and ``error`` fields so the orchestration
            agent can render a category-specific chat reply.
        exc: The :class:`ValidationError` raised by a validator.
        source_api: The AWS API operation the handler would have called
            had the request been accepted (e.g.
            ``"ec2:CreateTrafficMirrorSession"``). Recorded in
            ``metadata.sourceApi`` so the orchestration agent can
            attribute the rejection to the correct AWS surface.
        data_freshness: ``metadata.dataFreshness`` value for the
            response. Defaults to ``"real-time"`` to match the
            capture-lifecycle handlers; Pcap_Query_Action handlers
            override this to ``"near-real-time"`` per Req 5.22 so
            their failure envelopes carry the same metadata as their
            success envelopes (Property 10 — uniform response
            envelope shape).

    Returns:
        Response envelope produced by :func:`build_response` with
        ``success=False``, ``error_category=exc.error_category`` (which
        defaults to ``"invalid_parameter"``), and the validator message
        included verbatim in both ``formattedText`` and ``error``.
    """
    return build_response(
        success=False,
        data={},
        formatted_text=f"{action_name}: {exc.message}",
        source_api=source_api,
        data_freshness=data_freshness,
        error=f"{exc.error_category}: {exc.message}",
        error_category=exc.error_category,
    )


def _aws_error_response(
    action_name: str,
    exc: Exception,
    source_api: str,
    failed_operation: str,
    data_freshness: str = "real-time",
) -> dict:
    """Convert a botocore exception into the response envelope (Req 1.9).

    Mirrors the AWS-error surfacing already done in
    :func:`handle_list_enis`: classifies the exception via
    :func:`_classify_aws_error` and emits a structured envelope with
    ``metadata.errorCategory`` set so the orchestration agent can
    render a category-specific chat reply (EH-2).

    Args:
        action_name: The handler's action name (used for log lines).
        exc: The exception raised by ``boto3``. May be a
            :class:`botocore.exceptions.ClientError` or any other
            ``BotoCoreError`` subclass.
        source_api: ``metadata.sourceApi`` value for the response. For
            handlers that talk to multiple AWS services in sequence,
            this is the *primary* source API for the action — see the
            individual ``Set metadata.sourceApi = "..."`` task notes.
        failed_operation: The AWS API operation name that produced the
            exception (e.g. ``"ec2:CreateTrafficMirrorSession"``,
            ``"dynamodb:PutItem"``). Included verbatim in the
            ``error`` and ``formattedText`` fields so an operator can
            pinpoint the failing call.
        data_freshness: ``metadata.dataFreshness`` value for the
            response. Defaults to ``"real-time"`` to match the
            capture-lifecycle handlers; Pcap_Query_Action handlers
            override this to ``"near-real-time"`` per Req 5.22.

    Returns:
        Response envelope with ``success=False``.
    """
    category = _classify_aws_error(exc)
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        message = exc.response.get("Error", {}).get("Message", str(exc))
        details = f"{code}: {message}"
    else:
        details = str(exc)

    logger.exception("%s failed at %s", action_name, failed_operation)
    return build_response(
        success=False,
        data={},
        formatted_text=(
            f"{action_name} failed while calling {failed_operation}: {details}"
        ),
        source_api=source_api,
        data_freshness=data_freshness,
        error=f"{action_name} failed at {failed_operation}: {details}",
        error_category=category,
    )


# ---------------------------------------------------------------------------
# Response envelope helper
# ---------------------------------------------------------------------------


def build_response(
    success: bool,
    data=None,
    formatted_text: str = "",
    source_api: str = "",
    data_freshness: str = "real-time",
    error: str = None,
    error_category: str = None,
    extra_metadata: Optional[dict] = None,
) -> dict:
    """
    Build the uniform Network Agent response envelope.

    Every handler returns this shape so the orchestration agent and
    frontend can render results uniformly (Req 1.7, Req 1.8, Req 1.9).

    Args:
        success: Whether the action completed successfully.
        data: Action-specific payload (dict or list); ``None`` becomes ``{}``.
        formatted_text: Human-readable summary for chat rendering.
        source_api: AWS API operation that produced the result
            (e.g. ``"ec2:DescribeNetworkInterfaces"``).
        data_freshness: One of ``"real-time"``, ``"near-real-time"``,
            or ``"cached"``.
        error: Error message string. Included only when supplied;
            typically set when ``success`` is ``False``.
        error_category: Optional ``errorCategory`` value as defined in
            the design's EH-1/EH-2 tables (e.g. ``"invalid_parameter"``,
            ``"aws_throttled"``). Surfaced under ``metadata.errorCategory``
            so the orchestration agent can render category-specific replies.
        extra_metadata: Optional dict of additional metadata fields to
            merge into the response ``metadata`` block. Used by Pcap_Query_Action
            handlers that accept a ``flow_selector`` to surface
            ``resolved_flow_set``, ``matched_stream_count``, and
            ``matched_streams`` per Reqs 5.27 / 19.5 / 19.9. The fixed
            keys (``sourceApi``, ``queryTimestamp``, ``dataFreshness``,
            ``errorCategory``) cannot be overridden — extra entries are
            shallow-merged so handler-supplied keys take precedence over
            defaults but never clobber the four reserved keys above.

    Returns:
        Dict with keys ``success``, ``domain="network"``, ``data``,
        ``formattedText``, ``metadata`` (with ``sourceApi``,
        ``queryTimestamp``, ``dataFreshness``, optional ``errorCategory``,
        plus any additional keys from ``extra_metadata``), and optional
        ``error``.
    """
    metadata = {
        "sourceApi": source_api,
        "queryTimestamp": datetime.now(timezone.utc).isoformat(),
        "dataFreshness": data_freshness,
    }
    if error_category is not None:
        metadata["errorCategory"] = error_category
    if extra_metadata:
        # Merge extras without letting them override the four reserved
        # keys above. The reserved set protects Property 10 (uniform
        # response envelope shape) — every response must report
        # ``sourceApi`` and ``dataFreshness`` consistently regardless of
        # which handler produced it.
        reserved = {"sourceApi", "queryTimestamp", "dataFreshness", "errorCategory"}
        for key, value in extra_metadata.items():
            if key in reserved:
                continue
            metadata[key] = value

    response = {
        "success": success,
        "domain": "network",
        "data": data if data is not None else {},
        "formattedText": formatted_text,
        "metadata": metadata,
    }
    if error is not None:
        response["error"] = error
    return response


# ---------------------------------------------------------------------------
# Handler stubs
#
# Each handler accepts a single ``params`` dict and returns the response
# envelope produced by ``build_response``. Real implementations are added
# in subsequent tasks (3-18); for now every handler returns the same
# ``not_implemented`` envelope so that the dispatch core can be exercised.
# ---------------------------------------------------------------------------


def _not_implemented(action_name: str) -> dict:
    """Helper to build a uniform ``not_implemented`` envelope for stubs."""
    return build_response(
        success=False,
        data={},
        formatted_text=(
            f"Action '{action_name}' is registered but not yet implemented. "
            "It will be filled in by a subsequent implementation task."
        ),
        source_api="agentcore:Invoke",
        data_freshness="real-time",
        error=f"not_implemented: {action_name}",
    )


# ENI Inventory


# Set of accepted attachment_status filter values per Req 2.5. The full
# set of attachment_status response values defined by Req 2.2 is
# ``attached``, ``attaching``, ``detaching``, and ``unattached``; only
# the two "stable" values are accepted as filter inputs.
_ATTACHMENT_STATUS_FILTER_VALUES = frozenset({"attached", "unattached"})


def _map_eni_to_schema(eni: dict) -> dict:
    """Map a single ``DescribeNetworkInterfaces`` entry to the response schema.

    See Req 2.2 for the field list. ``attachment_status`` is derived from
    ``Attachment.Status`` when an attachment is present; otherwise the
    ENI is reported as ``unattached``. ``attached_instance_id`` comes
    from ``Attachment.InstanceId`` when present, ``None`` otherwise.
    ``tags`` is a dict of key→value for all tags on the ENI.
    """
    attachment = eni.get("Attachment") or {}
    if attachment:
        # AWS returns lowercase values matching the Req 2.2 enumeration
        # (``attached``, ``attaching``, ``detaching``); fall back to
        # ``unattached`` if the field is unexpectedly absent.
        attachment_status = attachment.get("Status") or "unattached"
        attached_instance_id = attachment.get("InstanceId") or None
    else:
        attachment_status = "unattached"
        attached_instance_id = None

    # Extract tags as a flat dict for easy filtering and display
    raw_tags = eni.get("TagSet") or []
    tags = {t["Key"]: t["Value"] for t in raw_tags if "Key" in t and "Value" in t}

    return {
        "eni_id": eni.get("NetworkInterfaceId"),
        "vpc_id": eni.get("VpcId"),
        "subnet_id": eni.get("SubnetId"),
        "availability_zone": eni.get("AvailabilityZone"),
        "private_ip": eni.get("PrivateIpAddress"),
        "status": eni.get("Status"),
        "attachment_status": attachment_status,
        "attached_instance_id": attached_instance_id,
        "tags": tags,
    }


def _format_list_enis_summary(
    enis: list,
    vpc_id_filter,
    instance_id_filter,
    attachment_status_filter,
) -> str:
    """Build a short human-readable summary of the list_enis result."""
    filters = []
    if vpc_id_filter:
        filters.append(f"vpc_id={vpc_id_filter}")
    if instance_id_filter:
        filters.append(f"instance_id={instance_id_filter}")
    if attachment_status_filter:
        filters.append(f"attachment_status={attachment_status_filter}")
    filter_clause = f" (filters: {', '.join(filters)})" if filters else ""

    total = len(enis)
    if total == 0:
        return (
            f"No ENIs found in region {AWS_REGION}{filter_clause}."
        )

    attached = sum(1 for e in enis if e["attachment_status"] == "attached")
    unattached = sum(1 for e in enis if e["attachment_status"] == "unattached")
    other = total - attached - unattached
    other_clause = f", {other} transitioning" if other else ""
    return (
        f"Found {total} ENI(s) in region {AWS_REGION}{filter_clause}: "
        f"{attached} attached, {unattached} unattached{other_clause}."
    )


def handle_list_enis(params: dict) -> dict:
    """List Elastic Network Interfaces visible in the current account/region.

    Implements Req 2.1-2.8:

    - Calls ``ec2:DescribeNetworkInterfaces`` via a paginator so every
      ENI is returned regardless of total count (Reqs 2.1, 2.6).
    - Maps each entry into the schema documented in Req 2.2.
    - Applies optional client-side filters ``vpc_id``, ``instance_id``,
      and ``attachment_status`` after pagination so they compose freely
      (Reqs 2.3, 2.4, 2.5).
    - Sets ``metadata.sourceApi`` to ``ec2:DescribeNetworkInterfaces``
      and ``metadata.dataFreshness`` to ``real-time`` (Req 2.7).
    - On any ``botocore`` error, returns ``success=false`` with no
      partial list (Req 2.8).

    Args:
        params: Optional dict with any of:
            ``vpc_id`` (str): exact-match VPC filter.
            ``instance_id`` (str): exact-match attached instance filter.
            ``attachment_status`` (str): one of ``attached`` or ``unattached``.

    Returns:
        Response envelope produced by :func:`build_response`.
    """
    if not isinstance(params, dict):
        params = {}

    vpc_id_filter = params.get("vpc_id")
    instance_id_filter = params.get("instance_id")
    attachment_status_filter = params.get("attachment_status")
    tag_key_filter = params.get("tag_key")
    tag_value_filter = params.get("tag_value")

    # Validate filter shapes before any AWS call (EH-1: caller fault).
    if vpc_id_filter is not None and not isinstance(vpc_id_filter, str):
        return build_response(
            success=False,
            data={},
            formatted_text=(
                "list_enis: 'vpc_id' must be a string when supplied."
            ),
            source_api="ec2:DescribeNetworkInterfaces",
            data_freshness="real-time",
            error=(
                "invalid_parameter: 'vpc_id' must be a string, got "
                f"{type(vpc_id_filter).__name__}"
            ),
            error_category="invalid_parameter",
        )

    if instance_id_filter is not None and not isinstance(instance_id_filter, str):
        return build_response(
            success=False,
            data={},
            formatted_text=(
                "list_enis: 'instance_id' must be a string when supplied."
            ),
            source_api="ec2:DescribeNetworkInterfaces",
            data_freshness="real-time",
            error=(
                "invalid_parameter: 'instance_id' must be a string, got "
                f"{type(instance_id_filter).__name__}"
            ),
            error_category="invalid_parameter",
        )

    if attachment_status_filter is not None and (
        not isinstance(attachment_status_filter, str)
        or attachment_status_filter not in _ATTACHMENT_STATUS_FILTER_VALUES
    ):
        accepted = ", ".join(sorted(_ATTACHMENT_STATUS_FILTER_VALUES))
        return build_response(
            success=False,
            data={},
            formatted_text=(
                f"list_enis: 'attachment_status' must be one of {accepted}."
            ),
            source_api="ec2:DescribeNetworkInterfaces",
            data_freshness="real-time",
            error=(
                f"invalid_parameter: 'attachment_status' must be one of "
                f"{accepted}, got {attachment_status_filter!r}"
            ),
            error_category="invalid_parameter",
        )

    # Paginate ec2:DescribeNetworkInterfaces so the result is exhaustive
    # regardless of total ENI count (Req 2.6). On any botocore error,
    # surface the failure without returning a partial list (Req 2.8).
    resolved_vpc_ids = None
    try:
        ec2 = _get_ec2_client()

        # Resolve a VPC *name* to its vpc-... ID when the caller did not
        # already pass an identifier. The orchestration LLM frequently
        # forwards the human-readable VPC name (e.g. "goat-demo-vpc")
        # straight into ``vpc_id``; EC2's ``vpc-id`` filter only matches
        # real identifiers, so without this the list silently comes back
        # empty. If the value starts with "vpc-" we treat it as an ID;
        # otherwise we look it up by the ``Name`` tag.
        if vpc_id_filter:
            if vpc_id_filter.startswith("vpc-"):
                resolved_vpc_ids = {vpc_id_filter}
            else:
                vpc_resp = ec2.describe_vpcs(
                    Filters=[{"Name": "tag:Name", "Values": [vpc_id_filter]}]
                )
                resolved_vpc_ids = {
                    v["VpcId"] for v in vpc_resp.get("Vpcs", [])
                }
                if resolved_vpc_ids:
                    logger.info(
                        "list_enis: resolved VPC name %r to %s",
                        vpc_id_filter,
                        ", ".join(sorted(resolved_vpc_ids)),
                    )
                else:
                    logger.info(
                        "list_enis: vpc_id %r is neither a vpc-id nor a "
                        "matching VPC Name tag; result will be empty.",
                        vpc_id_filter,
                    )

        paginator = ec2.get_paginator("describe_network_interfaces")
        all_enis = []
        for page in paginator.paginate():
            for raw_eni in page.get("NetworkInterfaces", []):
                all_enis.append(_map_eni_to_schema(raw_eni))
    except ClientError as exc:
        category = _classify_aws_error(exc)
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        message = exc.response.get("Error", {}).get("Message", str(exc))
        logger.exception("list_enis failed at ec2:DescribeNetworkInterfaces")
        return build_response(
            success=False,
            data={},
            formatted_text=(
                "list_enis failed while calling ec2:DescribeNetworkInterfaces: "
                f"{code} - {message}"
            ),
            source_api="ec2:DescribeNetworkInterfaces",
            data_freshness="real-time",
            error=(
                f"list_enis failed at ec2:DescribeNetworkInterfaces: "
                f"{code}: {message}"
            ),
            error_category=category,
        )
    except BotoCoreError as exc:
        logger.exception("list_enis failed at ec2:DescribeNetworkInterfaces")
        return build_response(
            success=False,
            data={},
            formatted_text=(
                "list_enis failed while calling ec2:DescribeNetworkInterfaces: "
                f"{exc}"
            ),
            source_api="ec2:DescribeNetworkInterfaces",
            data_freshness="real-time",
            error=(
                f"list_enis failed at ec2:DescribeNetworkInterfaces: {exc}"
            ),
            error_category="aws_other",
        )

    # Apply optional client-side filters after pagination so they
    # compose freely (Reqs 2.3, 2.4, 2.5). Filters use exact match.
    filtered = all_enis
    if vpc_id_filter:
        filtered = [e for e in filtered if e["vpc_id"] in (resolved_vpc_ids or set())]
    if instance_id_filter:
        filtered = [
            e for e in filtered if e["attached_instance_id"] == instance_id_filter
        ]
    if attachment_status_filter:
        filtered = [
            e
            for e in filtered
            if e["attachment_status"] == attachment_status_filter
        ]
    if tag_key_filter:
        # Filter ENIs that have the specified tag key (and optionally value)
        if tag_value_filter:
            filtered = [
                e for e in filtered
                if e.get("tags", {}).get(tag_key_filter) == tag_value_filter
            ]
        else:
            filtered = [
                e for e in filtered
                if tag_key_filter in e.get("tags", {})
            ]

    return build_response(
        success=True,
        data={
            "enis": filtered,
            "count": len(filtered),
            "region": AWS_REGION,
        },
        formatted_text=_format_list_enis_summary(
            filtered,
            vpc_id_filter,
            instance_id_filter,
            attachment_status_filter,
        ),
        source_api="ec2:DescribeNetworkInterfaces",
        data_freshness="real-time",
    )


# Reverse DNS


def handle_reverse_dns_lookup(params: dict) -> dict:
    """Resolve one or more IP addresses to hostnames via reverse DNS (PTR).

    Accepts either a single ``ip`` (str) or a list of ``ips`` (list of
    str), up to 50 addresses per call. Each address is resolved with a
    bounded-timeout reverse lookup (``socket.gethostbyaddr``). Addresses
    that have no PTR record (or fail to resolve) are reported with a
    ``hostname`` of ``None`` and an ``error`` string rather than failing
    the whole call.

    Args:
        params: dict with one of:
            ``ip`` (str): a single IPv4/IPv6 address.
            ``ips`` (list[str]): a list of addresses (max 50).

    Returns:
        Response envelope produced by :func:`build_response`. On success,
        ``data.results`` is a list of ``{"ip", "hostname", "aliases",
        "error"}`` dicts and ``data.count`` is the number resolved.
    """
    if not isinstance(params, dict):
        params = {}

    source_api = "socket:gethostbyaddr"

    # Collect the address list from either ``ip`` or ``ips``.
    raw_ips = []
    single = params.get("ip")
    many = params.get("ips")
    if isinstance(single, str) and single.strip():
        raw_ips.append(single.strip())
    if isinstance(many, list):
        raw_ips.extend([str(x).strip() for x in many if str(x).strip()])

    # De-duplicate while preserving order.
    seen = set()
    ips = []
    for ip in raw_ips:
        if ip not in seen:
            seen.add(ip)
            ips.append(ip)

    if not ips:
        return build_response(
            success=False,
            data={},
            formatted_text=(
                "reverse_dns_lookup: supply an 'ip' string or an 'ips' list "
                "of IP addresses to resolve."
            ),
            source_api=source_api,
            data_freshness="real-time",
            error="invalid_parameter: no IP address supplied",
            error_category="invalid_parameter",
        )

    if len(ips) > 50:
        return build_response(
            success=False,
            data={"requested_count": len(ips)},
            formatted_text=(
                "reverse_dns_lookup: at most 50 IP addresses may be resolved "
                f"per call (received {len(ips)})."
            ),
            source_api=source_api,
            data_freshness="real-time",
            error=f"invalid_parameter: {len(ips)} IPs exceeds limit of 50",
            error_category="invalid_parameter",
        )

    # Bound each lookup so a slow resolver cannot hang the handler.
    socket.setdefaulttimeout(3.0)

    results = []
    resolved_count = 0
    for ip in ips:
        entry = {"ip": ip, "hostname": None, "aliases": [], "error": None}
        try:
            hostname, aliases, _addrs = socket.gethostbyaddr(ip)
            entry["hostname"] = hostname
            entry["aliases"] = aliases or []
            resolved_count += 1
        except (socket.herror, socket.gaierror) as exc:
            entry["error"] = f"no PTR record: {exc}"
        except (socket.timeout, OSError) as exc:
            entry["error"] = f"lookup failed: {exc}"
        except Exception as exc:  # noqa: BLE001 — never fail the whole call
            entry["error"] = f"unexpected error: {exc}"
        results.append(entry)

    # Build a short human-readable summary.
    lines = []
    for r in results:
        if r["hostname"]:
            lines.append(f"{r['ip']} -> {r['hostname']}")
        else:
            lines.append(f"{r['ip']} -> (no hostname: {r['error']})")
    summary = (
        f"Reverse DNS resolved {resolved_count}/{len(results)} address(es):\n"
        + "\n".join(lines)
    )

    return build_response(
        success=True,
        data={
            "results": results,
            "count": resolved_count,
            "requested_count": len(results),
        },
        formatted_text=summary,
        source_api=source_api,
        data_freshness="real-time",
    )


# Capture Lifecycle


def _read_required_env(name: str) -> str:
    """Read a required environment variable or raise :class:`ValidationError`.

    Used by ``handle_start_capture`` to fetch the pre-provisioned
    Traffic Mirror filter/target IDs and the collector instance ID
    from the runtime container's environment. We intentionally surface
    a configuration error as a ``ValidationError`` so the handler's
    existing exception path produces a structured response envelope
    rather than crashing the dispatch loop.
    """
    value = os.environ.get(name)
    if not value:
        raise ValidationError(
            f"Required environment variable {name!r} is not set. "
            "The Network Agent runtime container must receive this "
            "value from the NetworkRuntimeStack (CDK Task 28).",
            error_category="configuration_missing",
        )
    return value


def _generate_capture_id() -> str:
    """Generate a fresh ``capture_id`` (Req 3.4).

    Per the Task 6 description: ``secrets.token_urlsafe(16)`` truncated
    to the Capture_Id_Format range. ``token_urlsafe(16)`` produces ~22
    URL-safe base64 characters drawn from ``[A-Za-z0-9_-]`` — already
    a subset of Capture_Id_Format ``[A-Za-z0-9_-]{1,128}``. We slice to
    128 characters defensively even though the function never produces
    a longer token, so the output is provably within the format
    regardless of future entropy adjustments.
    """
    return secrets.token_urlsafe(16)[:128]


def _check_collector_readiness(collector_instance_id: str) -> None:
    """Block until the Traffic_Mirror_Collector EC2 instance is ready.

    Implements Req 3.16 bounded by the 30-second SLA per the design's
    "Response Latency Reconciliation" section. Polls
    ``ec2:DescribeInstances`` and ``ec2:DescribeInstanceStatus`` until
    the instance is in ``running`` state with both system and instance
    status checks reporting ``ok``, or until the per-call wait budget
    is exhausted.

    On budget exhaustion the function raises a :class:`ValidationError`
    with ``error_category="infrastructure_unavailable"`` so the handler
    surfaces a "try again later" response and does not block the
    caller for the full 120 second collector-warmup window.

    Args:
        collector_instance_id: EC2 instance ID of the
            Traffic_Mirror_Collector. Sourced from the
            ``COLLECTOR_INSTANCE_ID`` environment variable.

    Raises:
        ValidationError: When the collector is not ready within the
            ``COLLECTOR_READINESS_WAIT_SECONDS`` budget.
        botocore.exceptions.ClientError: Propagated from EC2 calls so
            the caller's ``_classify_aws_error`` path can label the
            failure correctly.
    """
    ec2 = _get_ec2_client()
    deadline = time.monotonic() + COLLECTOR_READINESS_WAIT_SECONDS

    while True:
        # ec2:DescribeInstances confirms the instance exists and the
        # high-level run state. Status checks live on a separate API.
        describe_response = ec2.describe_instances(
            InstanceIds=[collector_instance_id],
        )
        reservations = describe_response.get("Reservations", [])
        instances = [
            inst
            for reservation in reservations
            for inst in reservation.get("Instances", [])
        ]
        run_state = (
            instances[0].get("State", {}).get("Name")
            if instances
            else None
        )

        # ec2:DescribeInstanceStatus reports both the system-level
        # ("SystemStatus") and instance-level ("InstanceStatus") status
        # checks. Both must be ``ok`` per Req 3.16.
        status_response = ec2.describe_instance_status(
            InstanceIds=[collector_instance_id],
            IncludeAllInstances=True,
        )
        statuses = status_response.get("InstanceStatuses", [])
        system_status = (
            statuses[0].get("SystemStatus", {}).get("Status")
            if statuses
            else None
        )
        instance_status = (
            statuses[0].get("InstanceStatus", {}).get("Status")
            if statuses
            else None
        )

        if (
            run_state == "running"
            and system_status == "ok"
            and instance_status == "ok"
        ):
            return

        if time.monotonic() >= deadline:
            raise ValidationError(
                f"Traffic_Mirror_Collector {collector_instance_id} is not "
                "yet ready (state="
                f"{run_state!r}, system_status={system_status!r}, "
                f"instance_status={instance_status!r}). The collector "
                "may be starting up; please retry the request in a few "
                "seconds.",
                error_category="infrastructure_unavailable",
            )

        # Simple linear backoff — keeps the math obvious and stays
        # well inside the per-call wait budget. Sleep at most until
        # the deadline so we don't overshoot.
        remaining = deadline - time.monotonic()
        time.sleep(min(COLLECTOR_READINESS_POLL_INTERVAL_SECONDS, max(0.5, remaining)))


def _check_opt_in_tag(eni_ids: list) -> None:
    """Verify the Capture_Opt_In_Tag is present on every requested ENI (Req 3.14, Task 7).

    Implements the Capture_Opt_In_Tag enforcement (Task 7):

    1. Calls ``ec2:DescribeNetworkInterfaces`` once for the full ENI
       set to fetch ENI ``TagSet`` and ``Attachment.InstanceId`` values.
    2. For ENIs whose ``Attachment.InstanceId`` is set, calls
       ``ec2:DescribeInstances`` once for the union of parent
       instance IDs to fetch instance-level tags.
    3. For every requested ENI, requires
       ``goat-network-capture-allowed=true`` on either the ENI itself
       or its parent EC2 instance.
    4. Rejects the request if **any** ENI fails the check, naming
       every offending ENI identifier and the missing tag in the
       error message so the user can fix all violations in a single
       round trip.

    Tag matching is exact and case-sensitive: only the literal value
    ``"true"`` (lowercase) on the literal key
    ``"goat-network-capture-allowed"`` permits mirroring. Other
    truthy values (``"yes"``, ``"True"``, ``"1"``) do not satisfy the
    check.

    Args:
        eni_ids: Validated list of ENI identifiers (1-3 distinct
            entries matching the AWS ENI identifier pattern).

    Raises:
        ValidationError:
            * ``error_category="unauthorized"`` when one or more ENIs
              lack the opt-in tag on both themselves and their parent
              EC2 instance. The message lists every offending ENI
              identifier and includes the literal tag spec
              ``goat-network-capture-allowed=true`` so the user can
              copy/paste the fix.
            * ``error_category="invalid_parameter"`` when an ENI in
              ``eni_ids`` was not returned by AWS (e.g. it does not
              exist in the account or the caller's credentials cannot
              see it).
        botocore.exceptions.ClientError: Propagated from EC2 API
            calls. The caller's ``_aws_error_response`` path labels
            the failure with the appropriate category.
        botocore.exceptions.BotoCoreError: Propagated likewise.
    """
    ec2 = _get_ec2_client()

    # Single DescribeNetworkInterfaces call covers all ENIs; AWS
    # returns ENIs (and their tag sets) for the supplied identifiers.
    # Validators upstream guarantee no duplicates and 1-3 entries, so
    # the identifier list is bounded and safe to pass verbatim.
    eni_response = ec2.describe_network_interfaces(NetworkInterfaceIds=eni_ids)
    enis_by_id = {
        eni["NetworkInterfaceId"]: eni
        for eni in eni_response.get("NetworkInterfaces", [])
    }

    # Collect parent instance IDs to fetch in one call. Use a set to
    # deduplicate (multiple ENIs can attach to the same instance) and
    # sort for stable ordering in tests.
    instance_ids = sorted(
        {
            (eni.get("Attachment") or {}).get("InstanceId")
            for eni in enis_by_id.values()
            if (eni.get("Attachment") or {}).get("InstanceId")
        }
    )

    instance_tags_by_id: dict = {}
    if instance_ids:
        inst_response = ec2.describe_instances(InstanceIds=instance_ids)
        for reservation in inst_response.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                instance_tags_by_id[inst["InstanceId"]] = {
                    tag["Key"]: tag["Value"]
                    for tag in inst.get("Tags", [])
                }

    # Pass 1: detect ENIs that AWS did not return (an invalid_parameter
    # condition — the caller named an ENI that does not exist in the
    # account or that the runtime role cannot see). Surfacing this as
    # a separate error category keeps the unauthorized message focused
    # on the genuine opt-in failures.
    missing_enis = [eni_id for eni_id in eni_ids if eni_id not in enis_by_id]
    if missing_enis:
        if len(missing_enis) == 1:
            detail = f"ENI {missing_enis[0]!r} was not found"
        else:
            detail = (
                f"ENIs {', '.join(repr(e) for e in missing_enis)} were not found"
            )
        raise ValidationError(
            f"{detail} by ec2:DescribeNetworkInterfaces. Confirm the "
            "identifiers are correct and exist in this account.",
            error_category="invalid_parameter",
        )

    # Pass 2: walk every ENI and collect the full set of opt-in
    # violations so the user gets a complete picture in one error
    # message. This matches the task description ("names the offending
    # ENI and the missing tag") while remaining usable when the user
    # supplies up to 3 ENIs and several are misconfigured.
    offending: list = []
    for eni_id in eni_ids:
        eni = enis_by_id[eni_id]

        eni_tags = {
            tag["Key"]: tag["Value"] for tag in eni.get("TagSet", [])
        }
        eni_tag_value = eni_tags.get(CAPTURE_OPT_IN_TAG_KEY)

        instance_id = (eni.get("Attachment") or {}).get("InstanceId")
        instance_tag_value = None
        if instance_id is not None:
            instance_tag_value = instance_tags_by_id.get(instance_id, {}).get(
                CAPTURE_OPT_IN_TAG_KEY
            )

        if (
            eni_tag_value != CAPTURE_OPT_IN_TAG_VALUE
            and instance_tag_value != CAPTURE_OPT_IN_TAG_VALUE
        ):
            offending.append(eni_id)

    if offending:
        # Match the EH-1 example format from the design document:
        # "ENI eni-0123 missing tag goat-network-capture-allowed=true".
        # When multiple ENIs are missing the tag, surface each
        # identifier so the user can fix them all in one round trip.
        tag_spec = f"{CAPTURE_OPT_IN_TAG_KEY}={CAPTURE_OPT_IN_TAG_VALUE}"
        if len(offending) == 1:
            message = (
                f"ENI {offending[0]} missing tag {tag_spec}. Add the "
                "tag to either the ENI or its parent EC2 instance to "
                "permit mirroring."
            )
        else:
            id_list = ", ".join(offending)
            message = (
                f"ENIs {id_list} missing tag {tag_spec}. Add the tag "
                "to either each ENI or its parent EC2 instance to "
                "permit mirroring."
            )
        raise ValidationError(message, error_category="unauthorized")


def _cleanup_orphaned_mirror_sessions(eni_ids: list) -> int:
    """Delete Traffic Mirror sessions on the given ENIs that are not tracked in DynamoDB.

    Queries ``ec2:DescribeTrafficMirrorSessions`` filtered by ENI,
    checks each session's description against the Capture_State_Table
    (our sessions are described as ``goat-network-capture <capture_id>``),
    and deletes any session whose capture_id either (a) doesn't exist
    in DynamoDB or (b) has status ``stopped``/``transformed``.

    Returns the number of sessions deleted.
    """
    ec2 = _get_ec2_client()
    deleted_count = 0

    for eni_id in eni_ids:
        try:
            resp = ec2.describe_traffic_mirror_sessions(
                Filters=[{"Name": "network-interface-id", "Values": [eni_id]}]
            )
        except (ClientError, BotoCoreError) as exc:
            logger.warning(
                "_cleanup_orphaned_mirror_sessions: failed to describe "
                "sessions for ENI %s: %s", eni_id, exc,
            )
            continue

        for session in resp.get("TrafficMirrorSessions", []):
            session_id = session.get("TrafficMirrorSessionId")
            description = session.get("Description", "")

            # Our sessions are always described as "goat-network-capture <capture_id>"
            is_goat_session = description.startswith("goat-network-capture ")
            capture_id_from_desc = (
                description.split("goat-network-capture ", 1)[1].strip()
                if is_goat_session else None
            )

            # Determine if this is an orphan
            is_orphan = False
            if not is_goat_session:
                # Not one of ours — could be from a different tool. Delete
                # anyway since it's blocking our ENI and we own these ENIs
                # (they carry the goat-network-capture-allowed tag).
                is_orphan = True
            elif capture_id_from_desc:
                # Check DynamoDB for this capture
                try:
                    row = state.get_capture(capture_id_from_desc)
                    if row is None:
                        is_orphan = True  # Not in DDB — orphan from a prior install
                    elif row.get("status") in ("stopped", "transformed"):
                        is_orphan = True  # Capture ended but session wasn't cleaned
                except Exception:
                    # Can't reach DDB — be conservative and delete anyway
                    is_orphan = True

            if is_orphan and session_id:
                try:
                    ec2.delete_traffic_mirror_session(
                        TrafficMirrorSessionId=session_id
                    )
                    deleted_count += 1
                    logger.info(
                        "_cleanup_orphaned_mirror_sessions: deleted orphan "
                        "session %s on ENI %s (capture_id=%s)",
                        session_id, eni_id, capture_id_from_desc or "unknown",
                    )
                except (ClientError, BotoCoreError) as exc:
                    logger.warning(
                        "_cleanup_orphaned_mirror_sessions: failed to delete "
                        "session %s: %s", session_id, exc,
                    )

    return deleted_count


def _create_mirror_sessions_for_eni_set(
    capture_id: str,
    eni_ids: list,
    filter_id: str,
    target_id: str,
) -> list:
    """Create one Traffic Mirror session per ENI; return the created session list.

    Each list entry is a dict ``{"eni_id", "mirror_session_id", "vni"}``
    in the order ``eni_ids`` was supplied. The caller (rollback path)
    uses this list to delete partial sessions on failure.

    Args:
        capture_id: Used as the Traffic Mirror session description so
            an operator can correlate sessions with captures from the
            EC2 console without consulting DynamoDB.
        eni_ids: Validated ENI list.
        filter_id: Pre-provisioned Traffic Mirror filter ID
            (``TRAFFIC_MIRROR_FILTER_ID``).
        target_id: Pre-provisioned Traffic Mirror target ID
            (``TRAFFIC_MIRROR_TARGET_ID``).

    Returns:
        List of dicts. Empty list if ``eni_ids`` is empty (defensive,
        not expected because validators reject empty lists).

    Raises:
        botocore.exceptions.ClientError: Propagated from EC2. The
            calling handler walks back any sessions already created
            in this list before re-raising into the response envelope.
    """
    ec2 = _get_ec2_client()
    created: list = []

    # Traffic Mirror sessions require a unique session number per
    # source ENI. We assign 1..N within this capture; that matches
    # AWS's per-source-ENI uniqueness requirement and produces stable
    # numbers for operators reviewing the EC2 console.
    for index, eni_id in enumerate(eni_ids, start=1):
        response = ec2.create_traffic_mirror_session(
            NetworkInterfaceId=eni_id,
            TrafficMirrorTargetId=target_id,
            TrafficMirrorFilterId=filter_id,
            SessionNumber=index,
            Description=f"goat-network-capture {capture_id}",
        )
        session = response.get("TrafficMirrorSession", {})
        created.append(
            {
                "eni_id": eni_id,
                "mirror_session_id": session.get("TrafficMirrorSessionId"),
                # AWS returns the auto-assigned VXLAN VNI in the
                # ``VirtualNetworkId`` field per the Traffic Mirror
                # API documentation.
                "vni": session.get("VirtualNetworkId"),
            }
        )
    return created


def _rollback_mirror_sessions(created_sessions: list) -> None:
    """Best-effort delete every Traffic Mirror session in ``created_sessions``.

    Each deletion failure is logged; the rollback continues so we
    leave as little partial state behind as possible. AWS errors are
    swallowed (logged) so a single failed deletion does not mask the
    original error that triggered the rollback.
    """
    if not created_sessions:
        return
    ec2 = _get_ec2_client()
    for session in created_sessions:
        session_id = session.get("mirror_session_id")
        if not session_id:
            continue
        try:
            ec2.delete_traffic_mirror_session(
                TrafficMirrorSessionId=session_id
            )
        except (ClientError, BotoCoreError) as exc:
            logger.warning(
                "rollback: failed to delete TrafficMirrorSession %s: %s",
                session_id,
                exc,
            )


def _rollback_vni_lookup_rows(capture_id: str) -> None:
    """Best-effort delete every Vni_Lookup_Table row for ``capture_id``."""
    try:
        deleted = state.delete_vni_lookup_for_capture(capture_id)
        if deleted:
            logger.info(
                "rollback: deleted %d vni-lookup rows for capture_id=%s",
                deleted,
                capture_id,
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "rollback: failed to delete vni-lookup rows for capture_id=%s: %s",
            capture_id,
            exc,
        )


def _create_auto_stop_schedule(
    capture_id: str,
    deadline: datetime,
) -> bool:
    """Create the EventBridge Scheduler one-shot Auto_Stop_Schedule (Req 3.5).

    Implements Task 11 of the goat-network-agent spec. The schedule:

    * Uses a one-shot ``at(<deadline-ISO>)`` expression in UTC so it
      fires exactly once at ``start_time + duration_minutes`` (Reqs
      3.5, 4.6, 4.10).
    * Targets the ``StopCaptureInvokerLambda`` (CDK Task 26) whose ARN
      is supplied via the ``STOP_CAPTURE_INVOKER_LAMBDA_ARN``
      environment variable. The Lambda receives only
      ``{"capture_id": "<id>"}`` and is responsible for re-wrapping
      the call into the ``InvokeAgentRuntime`` envelope (per the
      design's "StopCaptureInvokerLambda" section).
    * Runs under the dedicated scheduler-target IAM role from the
      ``SCHEDULER_TARGET_ROLE_ARN`` environment variable (CDK Task 27).
      EventBridge Scheduler assumes this role to ``lambda:InvokeFunction``
      the StopCaptureInvokerLambda; the Network Agent itself never
      invokes the Lambda directly. ``iam:PassRole`` on this role is
      granted to the Network Agent runtime in CDK Task 27.
    * Lives inside the schedule group named in the
      ``SCHEDULE_GROUP_NAME`` environment variable (CDK Task 27) so
      ``stop_capture`` can ``DeleteSchedule`` by ``(Name, GroupName)``
      without naming each capture's group.
    * Sets ``ActionAfterCompletion=DELETE`` so a fired schedule
      self-deletes, leaving no orphaned resources after the
      Auto_Stop_Schedule has done its job. ``stop_capture`` treats
      a ``ResourceNotFoundException`` on subsequent ``DeleteSchedule``
      as benign for exactly this reason (see
      :func:`_delete_auto_stop_schedule_best_effort`).
    * Sets ``FlexibleTimeWindow={"Mode": "OFF"}`` so the schedule
      fires at the exact deadline rather than within a flex window.
      Combined with EventBridge Scheduler's documented delivery
      latency (typically a few seconds, contractual ≤ 60 s), this
      satisfies Req 4.6's "within 60 seconds of the deadline" SLA.

    On failure (missing env vars or AWS error) the caller (``handle_start_capture``)
    persists ``auto_stop_schedule_armed=false`` on the Capture_State_Table
    row per the design's EH-3 step 10 rollback table, leaving the
    capture session running so a later reconciler (or the user via
    a manual ``stop_capture``) can clean it up.

    Args:
        capture_id: The capture identifier — used as the schedule
            ``Name`` so ``stop_capture`` can ``DeleteSchedule`` it
            directly. Already validated against ``Capture_Id_Format``
            by the caller, which guarantees the value is safe to use
            as a schedule name (EventBridge Scheduler accepts
            ``[A-Za-z0-9_.-]{1,64}`` and ``Capture_Id_Format`` is a
            strict subset apart from length: capture IDs may be up to
            128 characters whereas schedule names cap at 64; the
            length is bounded in practice by the
            ``secrets.token_urlsafe(16)`` generator which produces
            ~22 characters).
        deadline: Absolute UTC deadline. The schedule fires once at
            this instant.

    Returns:
        ``True`` when the schedule was created successfully,
        ``False`` when the schedule could not be created (missing
        configuration or AWS error). On ``False`` the caller persists
        ``auto_stop_schedule_armed=false`` per the design's EH-3 step
        10 commentary.
    """
    invoker_lambda_arn = os.environ.get(ENV_STOP_CAPTURE_INVOKER_LAMBDA_ARN)
    schedule_group = os.environ.get(ENV_SCHEDULE_GROUP_NAME)
    target_role_arn = os.environ.get(ENV_SCHEDULER_TARGET_ROLE_ARN)

    # Configuration is incomplete — emit a warning and let the caller
    # persist ``auto_stop_schedule_armed=false`` so the row is still
    # recoverable. This branch is exercised in dev/test environments
    # where CDK Tasks 26 and 27 (StopCaptureInvokerLambda and the
    # scheduler-target IAM role) have not been deployed yet; in
    # production all three values are wired by the NetworkRuntimeStack.
    if not invoker_lambda_arn or not schedule_group or not target_role_arn:
        missing = [
            name
            for name, value in (
                (ENV_STOP_CAPTURE_INVOKER_LAMBDA_ARN, invoker_lambda_arn),
                (ENV_SCHEDULE_GROUP_NAME, schedule_group),
                (ENV_SCHEDULER_TARGET_ROLE_ARN, target_role_arn),
            )
            if not value
        ]
        logger.warning(
            "Auto_Stop_Schedule: skipping CreateSchedule for capture_id=%s "
            "because the following environment variables are not set: %s. "
            "These are wired by NetworkInfraStack/NetworkRuntimeStack "
            "(CDK Tasks 26-28). The capture row will be persisted with "
            "auto_stop_schedule_armed=false so a follow-up reconciler "
            "can re-arm the schedule.",
            capture_id,
            ", ".join(missing),
        )
        return False

    # ``at(<ISO 8601>)`` is the documented one-shot expression for
    # EventBridge Scheduler. AWS expects ``YYYY-MM-DDTHH:MM:SS`` with
    # no offset suffix when ``ScheduleExpressionTimezone`` is set
    # explicitly (we set it to ``UTC`` below). The deadline is already
    # a timezone-aware UTC datetime supplied by ``handle_start_capture``
    # (computed as ``start_time + duration_minutes`` where ``start_time``
    # is ``datetime.now(timezone.utc)``), so a naive ``strftime`` is
    # safe and produces the exact UTC deadline.
    at_expression = "at(" + deadline.strftime("%Y-%m-%dT%H:%M:%S") + ")"

    # The schedule's input is the *Auto_Stop_Schedule wire payload*
    # documented in Task 11 and the design's "StopCaptureInvokerLambda"
    # section: only ``{"capture_id": "<id>"}``. The
    # StopCaptureInvokerLambda (CDK Task 26) is responsible for
    # re-wrapping this into the ``InvokeAgentRuntime`` envelope
    # ``{"action": "stop_capture", "params": {"capture_id": "<id>"}}``
    # before invoking the Network Agent runtime. Keeping the schedule
    # payload minimal lets the wrapping logic live in one place (the
    # Lambda) and prevents accidental drift between the schedule
    # payload and the action dispatch contract.
    target_input = json.dumps({"capture_id": capture_id})

    try:
        scheduler = _get_scheduler_client()
        scheduler.create_schedule(
            Name=capture_id,
            GroupName=schedule_group,
            ScheduleExpression=at_expression,
            ScheduleExpressionTimezone="UTC",
            FlexibleTimeWindow={"Mode": "OFF"},
            ActionAfterCompletion="DELETE",
            Target={
                "Arn": invoker_lambda_arn,
                "RoleArn": target_role_arn,
                "Input": target_input,
            },
        )
        logger.info(
            "Auto_Stop_Schedule armed for capture_id=%s; will fire at %s UTC.",
            capture_id,
            at_expression,
        )
        return True
    except (ClientError, BotoCoreError) as exc:
        # Per design EH-3 step 10: a CreateSchedule failure does NOT
        # roll back the capture (sessions and DDB row stay). The caller
        # flips ``auto_stop_schedule_armed=false`` on the row so a
        # reconciler (or the user via manual stop_capture) can clean
        # up before the cost runs unbounded.
        logger.warning(
            "Auto_Stop_Schedule create failed for capture_id=%s: %s. "
            "Persisting row with auto_stop_schedule_armed=false so a "
            "follow-up reconciler can re-arm. The capture is still "
            "active and the user can stop it manually.",
            capture_id,
            exc,
        )
        return False


def _format_start_capture_summary(
    capture_id: str,
    eni_ids: list,
    duration_minutes: int,
    deadline: datetime,
    auto_stop_schedule_armed: bool,
) -> str:
    """Build the ``formattedText`` summary for a successful start_capture."""
    deadline_str = deadline.isoformat()
    schedule_clause = (
        ""
        if auto_stop_schedule_armed
        else (
            " The Auto_Stop_Schedule could not be armed; the capture "
            "will continue but a manual stop_capture call may be required."
        )
    )
    return (
        f"Started capture {capture_id} on {len(eni_ids)} ENI(s) "
        f"({', '.join(eni_ids)}) for {duration_minutes} minutes. "
        f"Deadline: {deadline_str}.{schedule_clause}"
    )


def handle_start_capture(params: dict) -> dict:
    """Start a VPC Traffic Mirror capture session.

    Implements the full ordered procedure documented in design's
    "Capture Lifecycle Handlers" section, with rollback semantics
    matching design's "EH-3: state-machine errors" rollback table:

    1. Shape validation (Reqs 3.1, 3.2, 4.1-4.5).
    2. Idempotency-token short-circuit (Req 3.15).
    3. Capture_Concurrency_Limit check (Req 4.5).
    4. Capture_Opt_In_Tag check (Req 3.14, basic version; Task 7
       refines).
    5. Collector readiness check, bounded by the per-call 30 second
       SLA (Reqs 3.1, 3.16; design "Response Latency Reconciliation").
    6. ``capture_id`` generation when not supplied (Req 3.4).
    7. Create one TrafficMirrorSession per ENI (Req 3.1).
    8. Persist VNI mapping rows to Vni_Lookup_Table.
    9. Persist Capture_State_Table row with ``status=active``.
    10. Create the Auto_Stop_Schedule (Req 3.5).

    Failures between step 7 and step 9 walk every prior step backward
    (Req 3.6); failure at step 10 leaves the row persisted but with
    ``auto_stop_schedule_armed=false`` so a future reconciler can
    re-arm without producing a duplicate capture.

    Returns:
        Response envelope produced by :func:`build_response`.

        Success envelope ``data`` includes ``capture_id``,
        ``mirror_session_ids``, ``vnis``, ``start_time``, ``deadline``,
        ``duration_minutes``, ``auto_stop_schedule_armed``.

        On idempotency hits the envelope's ``metadata.dataFreshness``
        is ``"cached"`` (Req 3.15) and no AWS resources are created.
    """
    if not isinstance(params, dict):
        params = {}

    source_api = "ec2:CreateTrafficMirrorSession"

    # ----------------------------------------------------------------
    # Step 1: shape validation
    # ----------------------------------------------------------------
    try:
        eni_ids = validate_eni_ids(params.get("eni_ids"))
    except ValidationError as exc:
        return _validation_error_response("start_capture", exc, source_api)

    # Default duration when missing (Req 3.3).
    duration_raw = params.get("duration_minutes")
    if duration_raw is None:
        duration_minutes = DEFAULT_CAPTURE_DURATION_MINUTES
    else:
        try:
            duration_minutes = validate_duration_minutes(duration_raw)
        except ValidationError as exc:
            return _validation_error_response("start_capture", exc, source_api)

    # filter_id is supplied by the orchestration agent; if absent we
    # fall back to the pre-provisioned filter ID from the runtime
    # environment (Task description). Either way we re-validate the
    # final value's shape so a misconfigured environment is caught.
    raw_filter_id = params.get("filter_id")
    if raw_filter_id is None:
        try:
            raw_filter_id = _read_required_env(ENV_TRAFFIC_MIRROR_FILTER_ID)
        except ValidationError as exc:
            return _validation_error_response("start_capture", exc, source_api)
    try:
        filter_id = validate_filter_id(raw_filter_id)
    except ValidationError as exc:
        return _validation_error_response("start_capture", exc, source_api)

    # capture_id is optional; validate when supplied so we surface a
    # caller-fault response before any AWS calls.
    raw_capture_id = params.get("capture_id")
    if raw_capture_id is not None:
        try:
            raw_capture_id = validate_capture_id(raw_capture_id)
        except ValidationError as exc:
            return _validation_error_response("start_capture", exc, source_api)

    # idempotency_token is optional; validate when supplied.
    raw_idempotency_token = params.get("idempotency_token")
    if raw_idempotency_token is not None:
        try:
            raw_idempotency_token = validate_idempotency_token(
                raw_idempotency_token
            )
        except ValidationError as exc:
            return _validation_error_response("start_capture", exc, source_api)

    # ----------------------------------------------------------------
    # Step 2: idempotency check (Req 3.15)
    # ----------------------------------------------------------------
    if raw_idempotency_token is not None:
        try:
            existing = state.find_idempotent_capture(
                raw_idempotency_token, eni_ids, duration_minutes
            )
        except (ClientError, BotoCoreError) as exc:
            return _aws_error_response(
                "start_capture", exc, source_api, "dynamodb:Scan"
            )

        if existing is not None:
            cached_capture_id = existing.get("capture_id")
            return build_response(
                success=True,
                data={
                    "capture_id": cached_capture_id,
                    "eni_ids": existing.get("eni_ids", []),
                    "duration_minutes": existing.get("duration_minutes"),
                    "start_time": existing.get("start_time"),
                    "deadline": existing.get("deadline"),
                    "mirror_session_ids": existing.get("mirror_session_ids", []),
                    "status": existing.get("status"),
                    "auto_stop_schedule_armed": existing.get(
                        "auto_stop_schedule_armed", True
                    ),
                },
                formatted_text=(
                    f"Idempotency hit: capture {cached_capture_id} already "
                    "exists for this idempotency_token within the 5-minute "
                    "window. No new resources created."
                ),
                source_api=source_api,
                data_freshness="cached",
            )

    # ----------------------------------------------------------------
    # Step 3: Capture_Concurrency_Limit (Req 4.5)
    # ----------------------------------------------------------------
    try:
        active_rows = state.query_active_captures()
    except (ClientError, BotoCoreError) as exc:
        return _aws_error_response(
            "start_capture", exc, source_api, "dynamodb:Query"
        )

    if len(active_rows) >= CAPTURE_CONCURRENCY_LIMIT:
        return build_response(
            success=False,
            data={"active_capture_count": len(active_rows)},
            formatted_text=(
                f"start_capture rejected: Capture_Concurrency_Limit is "
                f"{CAPTURE_CONCURRENCY_LIMIT} simultaneous captures "
                f"(currently {len(active_rows)} active)."
            ),
            source_api=source_api,
            data_freshness="real-time",
            error=(
                f"capture_concurrency_limit: {len(active_rows)} active "
                f"captures already exist; limit is {CAPTURE_CONCURRENCY_LIMIT}"
            ),
            error_category="capture_concurrency_limit",
        )

    # ----------------------------------------------------------------
    # Step 4: Capture_Opt_In_Tag (Req 3.14)
    # ----------------------------------------------------------------
    try:
        _check_opt_in_tag(eni_ids)
    except ValidationError as exc:
        return _validation_error_response("start_capture", exc, source_api)
    except (ClientError, BotoCoreError) as exc:
        return _aws_error_response(
            "start_capture", exc, source_api, "ec2:DescribeNetworkInterfaces"
        )

    # ----------------------------------------------------------------
    # Step 5: collector readiness (Req 3.16)
    # ----------------------------------------------------------------
    try:
        collector_instance_id = _read_required_env(ENV_COLLECTOR_INSTANCE_ID)
        target_id = _read_required_env(ENV_TRAFFIC_MIRROR_TARGET_ID)
    except ValidationError as exc:
        return _validation_error_response("start_capture", exc, source_api)

    try:
        _check_collector_readiness(collector_instance_id)
    except ValidationError as exc:
        return _validation_error_response("start_capture", exc, source_api)
    except (ClientError, BotoCoreError) as exc:
        return _aws_error_response(
            "start_capture", exc, source_api, "ec2:DescribeInstanceStatus"
        )

    # ----------------------------------------------------------------
    # Step 6: capture_id generation (Req 3.4)
    # ----------------------------------------------------------------
    capture_id = raw_capture_id or _generate_capture_id()

    start_time_dt = datetime.now(timezone.utc)
    deadline_dt = start_time_dt + timedelta(minutes=duration_minutes)
    start_time_iso = start_time_dt.isoformat()
    deadline_iso = deadline_dt.isoformat()
    expires_at_epoch = int(deadline_dt.timestamp())

    # ----------------------------------------------------------------
    # Step 7: create Traffic Mirror sessions (Req 3.1)
    #
    # Self-healing: if CreateTrafficMirrorSession fails because the ENI
    # "is already being used by" another session, query sessions on that
    # ENI, delete any orphans (sessions whose capture_id is not tracked
    # in DynamoDB), and retry once. This recovers from stale sessions
    # left behind by a prior install or a failed stop_capture without
    # manual intervention.
    # ----------------------------------------------------------------
    created_sessions: list = []
    try:
        created_sessions = _create_mirror_sessions_for_eni_set(
            capture_id, eni_ids, filter_id, target_id
        )
    except ClientError as exc:
        error_msg = str(exc)
        # Detect "already in use" / per-interface limit errors
        if "already being used" in error_msg or "TrafficMirrorSessionsPerInterfaceLimitExceeded" in error_msg:
            logger.warning(
                "start_capture: mirror session creation failed due to existing "
                "session(s) on ENI(s). Attempting orphan cleanup and retry. "
                "Error: %s", error_msg,
            )
            # Clean up orphaned sessions on the requested ENIs
            cleaned = _cleanup_orphaned_mirror_sessions(eni_ids)
            if cleaned > 0:
                logger.info(
                    "start_capture: cleaned %d orphaned mirror session(s). Retrying.",
                    cleaned,
                )
                # Retry once after cleanup
                _rollback_mirror_sessions(created_sessions)
                created_sessions = []
                try:
                    created_sessions = _create_mirror_sessions_for_eni_set(
                        capture_id, eni_ids, filter_id, target_id
                    )
                except (ClientError, BotoCoreError) as retry_exc:
                    _rollback_mirror_sessions(created_sessions)
                    return _aws_error_response(
                        "start_capture", retry_exc, source_api,
                        "ec2:CreateTrafficMirrorSession",
                    )
            else:
                # No orphans found — the session is legitimately in use
                _rollback_mirror_sessions(created_sessions)
                return _aws_error_response(
                    "start_capture", exc, source_api,
                    "ec2:CreateTrafficMirrorSession",
                )
        else:
            # Roll back any partial sessions we did create before failing
            # (Req 3.6). No VNI rows have been written yet at this point.
            _rollback_mirror_sessions(created_sessions)
            return _aws_error_response(
                "start_capture", exc, source_api,
                "ec2:CreateTrafficMirrorSession",
            )
    except BotoCoreError as exc:
        _rollback_mirror_sessions(created_sessions)
        return _aws_error_response(
            "start_capture", exc, source_api, "ec2:CreateTrafficMirrorSession"
        )

    # ----------------------------------------------------------------
    # Step 8: write Vni_Lookup_Table rows
    # ----------------------------------------------------------------
    vni_rows = [
        {
            "vni": session["vni"],
            "capture_id": capture_id,
            "mirror_session_id": session["mirror_session_id"],
            "eni_id": session["eni_id"],
            "expires_at": expires_at_epoch,
        }
        for session in created_sessions
        if session.get("vni") is not None
    ]
    try:
        state.put_vni_lookup_rows(vni_rows)
    except (ClientError, BotoCoreError) as exc:
        # Step 8 failure: roll back sessions, then VNI rows. We try to
        # delete VNI rows even on this path because batch_writer may
        # have flushed a partial chunk (boto3 uses 25-item batches).
        _rollback_vni_lookup_rows(capture_id)
        _rollback_mirror_sessions(created_sessions)
        return _aws_error_response(
            "start_capture", exc, source_api, "dynamodb:BatchWriteItem"
        )

    # ----------------------------------------------------------------
    # Step 9: write Capture_State_Table row
    # ----------------------------------------------------------------
    mirror_session_ids = [
        s["mirror_session_id"]
        for s in created_sessions
        if s.get("mirror_session_id")
    ]
    capture_row = {
        "capture_id": capture_id,
        "eni_ids": eni_ids,
        "duration_minutes": duration_minutes,
        "status": "active",
        "start_time": start_time_iso,
        "deadline": deadline_iso,
        "mirror_session_ids": mirror_session_ids,
        "created_at": start_time_iso,
        # ``requested_by`` is populated when the orchestration agent
        # threads the Cognito sub through; absent until that wiring
        # lands, so we record a neutral placeholder.
        "requested_by": params.get("requested_by") or "unknown",
        # Optimistic flag — flipped to ``False`` below if step 10 fails.
        "auto_stop_schedule_armed": True,
    }
    if raw_idempotency_token is not None:
        capture_row["idempotency_token"] = raw_idempotency_token

    try:
        state.put_capture(capture_row)
    except (ClientError, BotoCoreError) as exc:
        # Step 9 failure: walk every prior step back. No schedule has
        # been created yet so there is nothing to delete on the
        # scheduler side.
        _rollback_vni_lookup_rows(capture_id)
        _rollback_mirror_sessions(created_sessions)
        return _aws_error_response(
            "start_capture", exc, source_api, "dynamodb:PutItem"
        )

    # ----------------------------------------------------------------
    # Step 10: Auto_Stop_Schedule (Req 3.5)
    #
    # Per the design's EH-3 rollback table:
    #
    #   "Failure at step 10 (CreateSchedule): keep sessions and DDB row
    #    (capture is technically active), but mark the row with
    #    status=active, auto_stop_schedule_armed=false so a follow-up
    #    reconciler can attempt to re-arm the schedule."
    # ----------------------------------------------------------------
    auto_stop_armed = _create_auto_stop_schedule(capture_id, deadline_dt)
    if not auto_stop_armed:
        try:
            # Patch the persisted row so a future reconciler can find
            # it. We use a direct update rather than rewriting the row
            # to avoid clobbering any concurrent writes. A failure here
            # is logged but does not fail the action — the capture is
            # active and reporting that fact to the caller is more
            # useful than a rollback that would tear down a working
            # mirror session.
            _capture_table = state._capture_table()  # noqa: SLF001
            _capture_table.update_item(
                Key={"capture_id": capture_id},
                UpdateExpression="SET auto_stop_schedule_armed = :a",
                ConditionExpression="attribute_exists(capture_id)",
                ExpressionAttributeValues={":a": False},
            )
        except Exception as patch_exc:  # pragma: no cover - defensive
            logger.warning(
                "Could not flip auto_stop_schedule_armed=false for "
                "capture_id=%s after schedule create failure: %s",
                capture_id,
                patch_exc,
            )

    return build_response(
        success=True,
        data={
            "capture_id": capture_id,
            "eni_ids": eni_ids,
            "duration_minutes": duration_minutes,
            "start_time": start_time_iso,
            "deadline": deadline_iso,
            "mirror_session_ids": mirror_session_ids,
            "vnis": [s["vni"] for s in created_sessions if s.get("vni") is not None],
            "status": "active",
            "auto_stop_schedule_armed": auto_stop_armed,
        },
        formatted_text=_format_start_capture_summary(
            capture_id, eni_ids, duration_minutes, deadline_dt, auto_stop_armed
        ),
        source_api=source_api,
        data_freshness="real-time",
    )


# ---------------------------------------------------------------------------
# stop_capture (Task 8)
#
# Best-effort sequential cleanup of every resource a capture owns:
# Traffic Mirror sessions, Vni_Lookup_Table rows, and the
# Auto_Stop_Schedule. Per the design's "Capture Lifecycle Handlers >
# stop_capture" commentary and EH-3, the handler:
#
#   * Treats "already deleted" errors as benign so retries are idempotent.
#   * Continues past any other deletion error so the rest of the cleanup
#     still happens.
#   * Updates the Capture_State_Table to ``status=stopped`` only when
#     every step succeeded; otherwise marks it ``status=stopping_failed``
#     with ``stopped_reason=partial_cleanup_<step>`` so an operator can
#     run a manual cleanup. The ``<step>`` identifies the *first* step
#     that failed; subsequent failures are logged but not reflected in
#     the row, mirroring the "first failing step wins" convention used
#     by ``start_capture``'s rollback path.
#
# The handler is idempotent: a second call for the same ``capture_id``
# whose row was already updated to ``stopped`` returns the documented
# ``state_conflict`` response (Req 3.8).
# ---------------------------------------------------------------------------


# Step labels embedded in ``stopped_reason`` so an operator can correlate
# a row with the deletion step that failed first. Kept as constants so
# tests can assert on the literal string set without re-spelling them.
_STOP_STEP_MIRROR_SESSIONS = "mirror_sessions"
_STOP_STEP_VNI_LOOKUP = "vni_lookup"
_STOP_STEP_AUTO_STOP_SCHEDULE = "auto_stop_schedule"

# AWS error codes that indicate a Traffic Mirror session was already
# deleted (e.g. by a prior ``stop_capture`` retry or by AWS as part of
# tearing down the source ENI). These are treated as benign.
_BENIGN_TM_DELETE_ERROR_CODES = frozenset(
    {
        # Returned when the session ID does not exist (usually because a
        # concurrent caller already deleted it).
        "InvalidTrafficMirrorSessionId.NotFound",
        # Returned in some edge cases when AWS has already deleted the
        # session as part of tearing down the source ENI.
        "InvalidTrafficMirrorSessionID.NotFound",
    }
)

# AWS error codes that indicate the EventBridge Scheduler schedule was
# already deleted (e.g. it self-deleted via ``ActionAfterCompletion=DELETE``
# when the auto-stop fired immediately before the user-initiated stop).
_BENIGN_SCHEDULE_DELETE_ERROR_CODES = frozenset(
    {
        "ResourceNotFoundException",
    }
)


def _is_benign_aws_error(exc: Exception, codes: frozenset) -> bool:
    """Return ``True`` if ``exc`` is a ``ClientError`` with a benign code."""
    if not isinstance(exc, ClientError):
        return False
    code = exc.response.get("Error", {}).get("Code", "")
    return code in codes


def _delete_mirror_sessions_best_effort(
    capture_id: str,
    mirror_session_ids: list,
) -> Optional[str]:
    """Sequentially delete every Traffic Mirror session in ``mirror_session_ids``.

    Returns ``None`` on full success, or a short failure summary string
    on any non-benign error. Benign "already deleted" errors are logged
    and treated as success so retries stay idempotent.

    The caller surfaces the returned string in the response envelope
    and uses its non-None value to decide whether to mark the row as
    ``stopping_failed``.
    """
    if not mirror_session_ids:
        return None

    ec2 = _get_ec2_client()
    failures = []
    for session_id in mirror_session_ids:
        if not session_id:
            continue
        try:
            ec2.delete_traffic_mirror_session(
                TrafficMirrorSessionId=session_id
            )
        except ClientError as exc:
            if _is_benign_aws_error(exc, _BENIGN_TM_DELETE_ERROR_CODES):
                logger.info(
                    "stop_capture %s: Traffic Mirror session %s already "
                    "deleted (benign): %s",
                    capture_id,
                    session_id,
                    exc.response.get("Error", {}).get("Code", ""),
                )
                continue
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            message = exc.response.get("Error", {}).get("Message", str(exc))
            logger.warning(
                "stop_capture %s: failed to delete Traffic Mirror session "
                "%s: %s - %s. Continuing with remaining cleanup steps.",
                capture_id,
                session_id,
                code,
                message,
            )
            failures.append(f"{session_id}({code})")
        except BotoCoreError as exc:
            logger.warning(
                "stop_capture %s: failed to delete Traffic Mirror session "
                "%s: %s. Continuing with remaining cleanup steps.",
                capture_id,
                session_id,
                exc,
            )
            failures.append(f"{session_id}(BotoCoreError)")

    if failures:
        return f"{len(failures)} session(s) failed: {', '.join(failures)}"
    return None


def _delete_vni_lookup_best_effort(capture_id: str) -> Optional[str]:
    """Delete every Vni_Lookup_Table row for ``capture_id``.

    Returns ``None`` on success, or a short failure summary on error.
    DynamoDB errors are not generally split into "already deleted"
    versus "real failure" — a missing row simply produces a no-op
    in ``delete_vni_lookup_for_capture`` (it queries first and
    deletes whatever it finds), so any exception here is treated as a
    real failure. The Vni_Lookup_Table also has DynamoDB TTL on
    ``expires_at``, which provides a safety net if cleanup ever fails.
    """
    try:
        deleted = state.delete_vni_lookup_for_capture(capture_id)
        logger.info(
            "stop_capture %s: deleted %d vni-lookup row(s).",
            capture_id,
            deleted,
        )
        return None
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        message = exc.response.get("Error", {}).get("Message", str(exc))
        logger.warning(
            "stop_capture %s: failed to delete vni-lookup rows: %s - %s. "
            "DynamoDB TTL on expires_at will eventually purge them.",
            capture_id,
            code,
            message,
        )
        return f"vni_lookup delete failed: {code}"
    except (BotoCoreError, state.StateError) as exc:
        logger.warning(
            "stop_capture %s: failed to delete vni-lookup rows: %s. "
            "DynamoDB TTL on expires_at will eventually purge them.",
            capture_id,
            exc,
        )
        return f"vni_lookup delete failed: {exc.__class__.__name__}"


def _delete_auto_stop_schedule_best_effort(capture_id: str) -> Optional[str]:
    """Delete the Auto_Stop_Schedule for ``capture_id``.

    Returns ``None`` on full success or when the schedule was already
    self-deleted (benign ``ResourceNotFoundException``). Returns a
    short failure summary on any other error.

    When the ``SCHEDULE_GROUP_NAME`` environment variable is unset
    (which only happens when ``start_capture`` could not arm the
    schedule in the first place; see Task 6's placeholder behavior),
    there is nothing to delete and the function returns ``None``.
    """
    schedule_group = os.environ.get(ENV_SCHEDULE_GROUP_NAME)
    if not schedule_group:
        # ``start_capture`` skipped the schedule create because of
        # missing configuration. There is no schedule to delete; this
        # is not a failure.
        logger.info(
            "stop_capture %s: SCHEDULE_GROUP_NAME unset; skipping "
            "Auto_Stop_Schedule delete (no schedule to delete).",
            capture_id,
        )
        return None

    try:
        scheduler = _get_scheduler_client()
        scheduler.delete_schedule(
            Name=capture_id,
            GroupName=schedule_group,
        )
        return None
    except ClientError as exc:
        if _is_benign_aws_error(exc, _BENIGN_SCHEDULE_DELETE_ERROR_CODES):
            logger.info(
                "stop_capture %s: Auto_Stop_Schedule already deleted "
                "(benign ResourceNotFoundException). This is expected "
                "when the schedule self-deleted via "
                "ActionAfterCompletion=DELETE.",
                capture_id,
            )
            return None
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        message = exc.response.get("Error", {}).get("Message", str(exc))
        logger.warning(
            "stop_capture %s: failed to delete Auto_Stop_Schedule: "
            "%s - %s.",
            capture_id,
            code,
            message,
        )
        return f"schedule delete failed: {code}"
    except BotoCoreError as exc:
        logger.warning(
            "stop_capture %s: failed to delete Auto_Stop_Schedule: %s.",
            capture_id,
            exc,
        )
        return f"schedule delete failed: {exc.__class__.__name__}"


def handle_stop_capture(params: dict) -> dict:
    """Stop a Capture_Session and clean up its AWS resources (Reqs 3.7, 3.8).

    Performs best-effort sequential deletion in this order:

    1. Every Traffic Mirror session listed in the row's
       ``mirror_session_ids``.
    2. Every Vni_Lookup_Table row for ``capture_id`` (via the
       ``capture-id-index`` GSI).
    3. The Auto_Stop_Schedule named after ``capture_id``.

    On full success the row is updated to ``status=stopped``. If any
    step failed (other than a benign "already deleted" error), the
    row is updated to ``status=stopping_failed`` with
    ``stopped_reason=partial_cleanup_<step>`` so an operator can run
    a manual cleanup. The ``<step>`` identifies the first step that
    failed; subsequent failures are logged but not reflected in the
    row.

    Args:
        params: Mapping containing the required ``capture_id`` field.

    Returns:
        Response envelope produced by :func:`build_response`.

        On unknown ``capture_id``, returns ``success=False`` with
        ``error_category="not_found"`` (Req 3.8).

        On a row already in ``status=stopped``, returns
        ``success=False`` with ``error_category="state_conflict"``
        (Req 3.8).

        On full success, returns ``success=True`` with
        ``data.status="stopped"`` and counts/IDs for each cleanup step.

        On partial cleanup, returns ``success=False`` with
        ``error_category="partial_cleanup"`` and a ``data.failed_step``
        identifying which step the row was marked with.
    """
    if not isinstance(params, dict):
        params = {}

    source_api = "ec2:DeleteTrafficMirrorSession"

    # ----------------------------------------------------------------
    # Step 0: validate capture_id and look up the row
    # ----------------------------------------------------------------
    try:
        capture_id = validate_capture_id(params.get("capture_id"))
    except ValidationError as exc:
        return _validation_error_response("stop_capture", exc, source_api)

    try:
        row = state.get_capture(capture_id)
    except (ClientError, BotoCoreError) as exc:
        return _aws_error_response(
            "stop_capture", exc, source_api, "dynamodb:GetItem"
        )
    except state.StateError as exc:
        return build_response(
            success=False,
            data={},
            formatted_text=f"stop_capture: {exc}",
            source_api=source_api,
            data_freshness="real-time",
            error=f"configuration_missing: {exc}",
            error_category="configuration_missing",
        )

    if row is None:
        return build_response(
            success=False,
            data={"capture_id": capture_id},
            formatted_text=(
                f"stop_capture: capture_id {capture_id!r} not found in the "
                "Capture_State_Table."
            ),
            source_api=source_api,
            data_freshness="real-time",
            error=f"not_found: capture_id {capture_id!r} not found",
            error_category="not_found",
        )

    current_status = row.get("status")
    if current_status == "stopped":
        return build_response(
            success=False,
            data={"capture_id": capture_id, "status": current_status},
            formatted_text=(
                f"stop_capture: capture_id {capture_id!r} is already stopped."
            ),
            source_api=source_api,
            data_freshness="real-time",
            error=(
                f"state_conflict: capture_id {capture_id!r} is already "
                "stopped"
            ),
            error_category="state_conflict",
        )

    # ----------------------------------------------------------------
    # Step 1: delete Traffic Mirror sessions (best-effort)
    # ----------------------------------------------------------------
    mirror_session_ids = row.get("mirror_session_ids") or []
    failed_step: Optional[str] = None
    failure_messages = []

    mirror_failure = _delete_mirror_sessions_best_effort(
        capture_id, list(mirror_session_ids)
    )
    if mirror_failure is not None:
        failed_step = _STOP_STEP_MIRROR_SESSIONS
        failure_messages.append(f"mirror_sessions: {mirror_failure}")

    # ----------------------------------------------------------------
    # Step 2: delete Vni_Lookup_Table rows (best-effort)
    # ----------------------------------------------------------------
    vni_failure = _delete_vni_lookup_best_effort(capture_id)
    if vni_failure is not None:
        if failed_step is None:
            failed_step = _STOP_STEP_VNI_LOOKUP
        failure_messages.append(f"vni_lookup: {vni_failure}")

    # ----------------------------------------------------------------
    # Step 3: delete Auto_Stop_Schedule (best-effort)
    # ----------------------------------------------------------------
    schedule_failure = _delete_auto_stop_schedule_best_effort(capture_id)
    if schedule_failure is not None:
        if failed_step is None:
            failed_step = _STOP_STEP_AUTO_STOP_SCHEDULE
        failure_messages.append(f"auto_stop_schedule: {schedule_failure}")

    # ----------------------------------------------------------------
    # Step 4: update row status (stopped vs. stopping_failed)
    # ----------------------------------------------------------------
    if failed_step is None:
        new_status = "stopped"
        stopped_reason: Optional[str] = None
    else:
        new_status = "stopping_failed"
        stopped_reason = f"partial_cleanup_{failed_step}"

    try:
        state.update_capture_status(
            capture_id, new_status, stopped_reason=stopped_reason
        )
    except (ClientError, BotoCoreError) as exc:
        return _aws_error_response(
            "stop_capture", exc, source_api, "dynamodb:UpdateItem"
        )
    except state.StateError as exc:
        return build_response(
            success=False,
            data={"capture_id": capture_id, "failed_step": failed_step},
            formatted_text=f"stop_capture: {exc}",
            source_api=source_api,
            data_freshness="real-time",
            error=f"configuration_missing: {exc}",
            error_category="configuration_missing",
        )

    # ----------------------------------------------------------------
    # Build the response
    # ----------------------------------------------------------------
    if failed_step is None:
        return build_response(
            success=True,
            data={
                "capture_id": capture_id,
                "status": new_status,
                "mirror_session_ids": list(mirror_session_ids),
            },
            formatted_text=(
                f"Stopped capture {capture_id}. Deleted "
                f"{len(mirror_session_ids)} Traffic Mirror session(s), "
                "the VNI lookup rows, and the Auto_Stop_Schedule."
            ),
            source_api=source_api,
            data_freshness="real-time",
        )

    failure_clause = "; ".join(failure_messages) if failure_messages else "unknown"
    return build_response(
        success=False,
        data={
            "capture_id": capture_id,
            "status": new_status,
            "stopped_reason": stopped_reason,
            "failed_step": failed_step,
            "mirror_session_ids": list(mirror_session_ids),
        },
        formatted_text=(
            f"stop_capture for {capture_id}: partial cleanup at step "
            f"{failed_step!r}. Row marked status={new_status} with "
            f"stopped_reason={stopped_reason!r}. Failures: {failure_clause}."
        ),
        source_api=source_api,
        data_freshness="real-time",
        error=(
            f"partial_cleanup: failed at step {failed_step!r}; "
            f"row marked {new_status} with stopped_reason={stopped_reason!r}"
        ),
        error_category="partial_cleanup",
    )


# Set of accepted ``status`` filter values for ``list_captures``
# (Reqs 3.10, 3.11). Mirrors ``state.ACCEPTED_LIST_CAPTURES_STATUSES``
# but kept as a module-level constant in ``main`` so the validator can
# pass it directly to ``validate_status_filter``.
_LIST_CAPTURES_STATUS_VALUES = ("all", "active", "historical")

# Default ``status`` value when the caller omits the parameter
# (Req 3.11). The handler also treats an explicit ``None`` as "missing".
_LIST_CAPTURES_DEFAULT_STATUS = "all"

# Field projection for ``list_captures`` response rows. The task
# description for Task 9 explicitly enumerates these fields:
# ``capture_id``, ``eni_ids``, ``start_time``, ``deadline``,
# ``status``, ``stopped_reason``, ``mirror_session_ids``. Other
# attributes on the Capture_State_Table row (e.g. ``idempotency_token``,
# ``requested_by``, ``transform_execution_arn``) are intentionally
# excluded from this view.
_LIST_CAPTURES_PROJECTED_FIELDS = (
    "capture_id",
    "eni_ids",
    "start_time",
    "deadline",
    "status",
    "stopped_reason",
    "mirror_session_ids",
)


def _project_list_captures_row(row: dict) -> dict:
    """Reduce a Capture_State_Table row to the documented response schema.

    The DynamoDB row may carry attributes the design does not surface
    via ``list_captures`` (idempotency token, requested-by, transform
    execution ARN). The handler emits only the fields enumerated in
    Task 9. Missing optional attributes (``stopped_reason``,
    ``mirror_session_ids``, ``eni_ids``) are surfaced as ``None`` and
    ``[]`` respectively so the orchestration agent always sees a
    stable shape it can render.
    """
    return {
        "capture_id": row.get("capture_id"),
        "eni_ids": list(row.get("eni_ids") or []),
        "start_time": row.get("start_time"),
        "deadline": row.get("deadline"),
        "status": row.get("status"),
        "stopped_reason": row.get("stopped_reason"),
        "mirror_session_ids": list(row.get("mirror_session_ids") or []),
    }


def _format_list_captures_summary(rows: list, status_filter: str) -> str:
    """Build a short human-readable summary of the list_captures result."""
    total = len(rows)
    if total == 0:
        return (
            f"No captures match status filter {status_filter!r} in the "
            "Capture_State_Table."
        )

    # Provide a per-status breakdown so the orchestration agent's
    # natural-language reply can mention "3 active, 12 stopped" without
    # re-iterating the row list.
    counts: dict = {}
    for row in rows:
        s = row.get("status") or "unknown"
        counts[s] = counts.get(s, 0) + 1
    breakdown = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
    return (
        f"Found {total} capture(s) matching status filter "
        f"{status_filter!r}: {breakdown}."
    )


def handle_list_captures(params: dict) -> dict:
    """List Capture_Sessions filtered by status (Reqs 3.9, 3.10, 3.11).

    Implements Task 9:

    - Validates ``status`` against the closed set ``{all, active,
      historical}`` (Reqs 3.10, 3.11). Missing or ``None`` defaults to
      ``"all"`` (Req 3.11).
    - Delegates the actual DynamoDB query to
      :func:`state.query_captures`, which queries the ``status-index``
      GSI for the relevant status values and sorts by ``start_time``
      descending (Req 3.9). The ``state`` helper handles the mapping
      from the public filter values to the internal status set
      (``historical`` covers ``stopped``, ``transformed``,
      ``queryable``, and ``stopping_failed`` per the design).
    - Projects each row down to the documented field list:
      ``capture_id``, ``eni_ids``, ``start_time``, ``deadline``,
      ``status``, ``stopped_reason``, ``mirror_session_ids``.
    - Sets ``metadata.sourceApi = "dynamodb:Query"`` and
      ``metadata.dataFreshness = "real-time"``.

    Args:
        params: Optional dict with at most one field:
            ``status`` (str): one of ``all``, ``active``, ``historical``.
            Defaults to ``all`` when missing or ``None``.

    Returns:
        Response envelope produced by :func:`build_response`.

        On invalid ``status`` value: ``success=False`` with
        ``metadata.errorCategory = "invalid_parameter"`` (Req 3.10).

        On configuration errors (missing ``CAPTURE_STATE_TABLE``):
        ``success=False`` with
        ``metadata.errorCategory = "configuration_missing"``.

        On DynamoDB errors: ``success=False`` with the appropriate
        ``aws_*`` error category from :func:`_classify_aws_error`.

        On success: ``success=True`` with ``data.captures`` as the
        ordered list of projected rows and ``data.count`` as the total.
    """
    if not isinstance(params, dict):
        params = {}

    source_api = "dynamodb:Query"

    # ----------------------------------------------------------------
    # Step 1: resolve the ``status`` filter, defaulting to ``"all"``
    # when missing or explicitly None (Req 3.11).
    # ----------------------------------------------------------------
    raw_status = params.get("status")
    if raw_status is None:
        status_filter = _LIST_CAPTURES_DEFAULT_STATUS
    else:
        # Run the value through the shared validator so the error
        # message lists the accepted set verbatim (Req 3.10).
        try:
            status_filter = validate_status_filter(
                raw_status, _LIST_CAPTURES_STATUS_VALUES
            )
        except ValidationError as exc:
            return _validation_error_response(
                "list_captures", exc, source_api
            )

    # ----------------------------------------------------------------
    # Step 2: query the Capture_State_Table via the shared helper.
    # ``state.query_captures`` already:
    #   - queries the ``status-index`` GSI for the relevant status
    #     values (``active`` for "active"; ``stopped``, ``transformed``,
    #     ``queryable``, ``stopping_failed`` for "historical"; the
    #     union of both for "all"),
    #   - paginates fully via ``LastEvaluatedKey``,
    #   - sorts the merged result by ``start_time`` descending (Req 3.9).
    # ----------------------------------------------------------------
    try:
        rows = state.query_captures(status_filter)
    except (ClientError, BotoCoreError) as exc:
        return _aws_error_response(
            "list_captures", exc, source_api, "dynamodb:Query"
        )
    except state.StateError as exc:
        # Misconfiguration (missing ``CAPTURE_STATE_TABLE`` env var) —
        # surface as ``configuration_missing`` so the orchestration
        # agent can render a deployer-facing message rather than a
        # generic AWS-error reply.
        return build_response(
            success=False,
            data={"status": status_filter},
            formatted_text=f"list_captures: {exc}",
            source_api=source_api,
            data_freshness="real-time",
            error=f"configuration_missing: {exc}",
            error_category="configuration_missing",
        )

    # ----------------------------------------------------------------
    # Step 3: project each row to the documented response schema.
    # ----------------------------------------------------------------
    projected = [_project_list_captures_row(row) for row in rows]

    return build_response(
        success=True,
        data={
            "captures": projected,
            "count": len(projected),
            "status": status_filter,
        },
        formatted_text=_format_list_captures_summary(projected, status_filter),
        source_api=source_api,
        data_freshness="real-time",
    )


def handle_transform_capture(params: dict) -> dict:
    """Start the Step Functions Transformation_Workflow for a capture.

    Implements Task 10 of the goat-network-agent spec, covering Reqs
    3.12, 3.13, and 6.10:

    - Validates ``capture_id`` against ``Capture_Id_Format``
      (Reqs 5.20, 6.10). Rejects with ``invalid_parameter`` on
      malformed input *before* any DynamoDB or Step Functions call.
    - Reads the row from the Capture_State_Table; rejects with
      ``not_found`` if the row does not exist (Req 3.13).
    - Calls ``stepfunctions:StartExecution`` against the state machine
      ARN supplied via the ``TRANSFORMATION_SFN_ARN`` environment
      variable, with input ``{"capture_id": <id>}`` (Req 3.12).
    - Persists the returned ``transform_execution_arn`` on the row so
      the orchestration agent can poll the execution later (Req 17.7
      in the design).
    - Returns the execution ARN in ``data.transform_execution_arn``.
    - Sets ``metadata.sourceApi = "stepfunctions:StartExecution"``.

    Args:
        params: Mapping containing the required ``capture_id`` field
            (matching ``Capture_Id_Format``).

    Returns:
        Response envelope produced by :func:`build_response`.

        On invalid ``capture_id``: ``success=False`` with
        ``metadata.errorCategory = "invalid_parameter"``.

        On missing row: ``success=False`` with
        ``metadata.errorCategory = "not_found"``.

        On configuration errors (missing
        ``TRANSFORMATION_SFN_ARN`` env var or
        ``CAPTURE_STATE_TABLE`` env var): ``success=False`` with
        ``metadata.errorCategory = "configuration_missing"``.

        On AWS errors: ``success=False`` with the appropriate
        ``aws_*`` error category.

        On success: ``success=True`` with
        ``data.transform_execution_arn``, ``data.capture_id``, and
        ``data.status`` (the ``status`` from the row at the time of
        the call).
    """
    if not isinstance(params, dict):
        params = {}

    source_api = "stepfunctions:StartExecution"

    # ----------------------------------------------------------------
    # Step 1: validate capture_id (Reqs 5.20, 6.10).
    # ----------------------------------------------------------------
    try:
        capture_id = validate_capture_id(params.get("capture_id"))
    except ValidationError as exc:
        return _validation_error_response("transform_capture", exc, source_api)

    # ----------------------------------------------------------------
    # Step 2: confirm the Capture_State_Table row exists (Req 3.13).
    # We *deliberately* read the row before the StartExecution call so
    # that an unknown capture_id never produces a Step Functions
    # execution (Req 3.13's "SHALL NOT invoke the Transformation_Workflow").
    # ----------------------------------------------------------------
    try:
        row = state.get_capture(capture_id)
    except (ClientError, BotoCoreError) as exc:
        return _aws_error_response(
            "transform_capture", exc, source_api, "dynamodb:GetItem"
        )
    except state.StateError as exc:
        return build_response(
            success=False,
            data={"capture_id": capture_id},
            formatted_text=f"transform_capture: {exc}",
            source_api=source_api,
            data_freshness="real-time",
            error=f"configuration_missing: {exc}",
            error_category="configuration_missing",
        )

    if row is None:
        return build_response(
            success=False,
            data={"capture_id": capture_id},
            formatted_text=(
                f"transform_capture: capture_id {capture_id!r} not found "
                "in the Capture_State_Table."
            ),
            source_api=source_api,
            data_freshness="real-time",
            error=f"not_found: capture_id {capture_id!r} not found",
            error_category="not_found",
        )

    # ----------------------------------------------------------------
    # Step 2b: empty-pcap pre-flight check.
    #
    # The Transformation_Workflow fails with a cryptic "could not
    # register Athena partition" error when no pcap data was captured
    # (e.g. the mirrored ENI generated no traffic, or the traffic
    # never reached the collector). Detect this BEFORE starting the
    # Step Functions execution and return a clear, actionable message
    # so the chat surfaces the real cause instead of a generic
    # workflow failure.
    # ----------------------------------------------------------------
    try:
        data_bucket = _read_required_env(ENV_DATA_BUCKET_NAME)
        s3 = _get_s3_client()
        prefix = f"raw/{capture_id}/"
        pcap_object_count = 0
        pcap_total_bytes = 0
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=data_bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                pcap_object_count += 1
                pcap_total_bytes += int(obj.get("Size", 0) or 0)

        if pcap_object_count == 0 or pcap_total_bytes == 0:
            eni_ids = row.get("eni_ids", [])
            eni_clause = (
                f" mirrored from ENI(s) {', '.join(eni_ids)}"
                if eni_ids else ""
            )
            return build_response(
                success=False,
                data={
                    "capture_id": capture_id,
                    "pcap_object_count": pcap_object_count,
                    "pcap_total_bytes": pcap_total_bytes,
                    "s3_prefix": f"s3://{data_bucket}/{prefix}",
                },
                formatted_text=(
                    f"Capture {capture_id} contains no packet data, so there "
                    f"is nothing to transform. The capture{eni_clause} wrote "
                    f"{pcap_object_count} pcap file(s) totaling "
                    f"{pcap_total_bytes} bytes to s3://{data_bucket}/{prefix}.\n\n"
                    "This usually means one of:\n"
                    "- The mirrored workload generated no traffic during the "
                    "capture window (check that the source instance is running "
                    "and actively making network connections).\n"
                    "- The traffic mirror session could not reach the collector "
                    "(check the mirror target and collector security group).\n"
                    "- The capture was stopped before any traffic was mirrored "
                    "(try a longer capture window).\n\n"
                    "Start a fresh capture once the source is actively sending "
                    "traffic, then transform again."
                ),
                source_api="s3:ListObjectsV2",
                data_freshness="real-time",
                error=(
                    f"empty_pcap: capture {capture_id} has no pcap data "
                    f"({pcap_object_count} objects, {pcap_total_bytes} bytes) "
                    f"under {prefix}"
                ),
                error_category="empty_pcap",
            )
    except ValidationError:
        # DATA_BUCKET_NAME not configured — fall through and let the
        # workflow's own configuration handling report it. The
        # empty-pcap check is best-effort and must not block transforms
        # in environments where the bucket env var is wired differently.
        logger.warning(
            "transform_capture: DATA_BUCKET_NAME not set; skipping "
            "empty-pcap pre-flight check for %s",
            capture_id,
        )
    except (ClientError, BotoCoreError) as exc:
        # If the pre-flight S3 listing itself fails, log and continue to
        # the workflow rather than masking a transform the user asked
        # for. The workflow has its own error handling.
        logger.warning(
            "transform_capture: empty-pcap pre-flight check failed for "
            "%s (continuing to start workflow): %s",
            capture_id,
            exc,
        )

    # ----------------------------------------------------------------
    # Step 3: read the Transformation_Workflow ARN from the runtime
    # environment. Surface a configuration-error envelope rather than
    # crashing the dispatch loop when CDK Task 28 has not yet wired
    # the variable.
    # ----------------------------------------------------------------
    try:
        sfn_arn = _read_required_env(ENV_TRANSFORMATION_SFN_ARN)
    except ValidationError as exc:
        return build_response(
            success=False,
            data={"capture_id": capture_id},
            formatted_text=f"transform_capture: {exc.message}",
            source_api=source_api,
            data_freshness="real-time",
            error=f"{exc.error_category}: {exc.message}",
            error_category=exc.error_category,
        )

    # ----------------------------------------------------------------
    # Step 4: invoke stepfunctions:StartExecution (Req 3.12).
    # The state machine input is the literal payload mandated by
    # Req 3.12: ``{"capture_id": <id>}``. We do not pass the row's
    # other attributes — the state machine reads the Capture_State_Table
    # itself if it needs them, so the contract is the same one
    # documented in Req 6.8.
    # ----------------------------------------------------------------
    try:
        sfn = _get_sfn_client()
        sfn_response = sfn.start_execution(
            stateMachineArn=sfn_arn,
            input=json.dumps({"capture_id": capture_id}),
        )
    except (ClientError, BotoCoreError) as exc:
        return _aws_error_response(
            "transform_capture",
            exc,
            source_api,
            "stepfunctions:StartExecution",
        )

    transform_execution_arn = sfn_response.get("executionArn", "")
    if not transform_execution_arn:
        # Defensive: the AWS API contract guarantees ``executionArn``
        # is present on success, but a surprising response should not
        # leave the row updated with an empty string.
        return build_response(
            success=False,
            data={"capture_id": capture_id},
            formatted_text=(
                "transform_capture: stepfunctions:StartExecution returned "
                "no executionArn."
            ),
            source_api=source_api,
            data_freshness="real-time",
            error=(
                "aws_other: stepfunctions:StartExecution returned no "
                "executionArn"
            ),
            error_category="aws_other",
        )

    # ----------------------------------------------------------------
    # Step 5: persist transform_execution_arn on the row.
    # A failure here is logged but does **not** fail the action — the
    # Step Functions execution has already been started, so reporting
    # the ARN to the caller is more useful than rolling back.
    # ----------------------------------------------------------------
    persisted = True
    persist_error: Optional[str] = None
    try:
        state.update_capture_transform_execution_arn(
            capture_id, transform_execution_arn
        )
    except (ClientError, BotoCoreError) as exc:
        persisted = False
        persist_error = str(exc)
        logger.warning(
            "transform_capture: failed to persist transform_execution_arn "
            "for %s: %s",
            capture_id,
            exc,
        )
    except state.StateError as exc:
        persisted = False
        persist_error = str(exc)
        logger.warning(
            "transform_capture: state misconfiguration prevented persist "
            "for %s: %s",
            capture_id,
            exc,
        )

    persist_clause = ""
    if not persisted:
        persist_clause = (
            " (note: failed to persist transform_execution_arn on the "
            f"capture row: {persist_error})"
        )

    return build_response(
        success=True,
        data={
            "capture_id": capture_id,
            "transform_execution_arn": transform_execution_arn,
            "status": row.get("status"),
            "transform_execution_arn_persisted": persisted,
        },
        formatted_text=(
            f"Started Transformation_Workflow execution for capture "
            f"{capture_id}: {transform_execution_arn}.{persist_clause}"
        ),
        source_api=source_api,
        data_freshness="real-time",
    )


def handle_get_capture_progress(params: dict) -> dict:
    """Report progress for an in-flight or recently-finished capture.

    Implements Task 10 of the goat-network-agent spec, covering Reqs
    3.17 and 3.18:

    - Validates ``capture_id`` against ``Capture_Id_Format``
      (Reqs 5.20, 6.10). Rejects with ``invalid_parameter`` on
      malformed input *before* any DynamoDB or S3 call.
    - Reads the row from the Capture_State_Table; rejects with
      ``not_found`` if the row does not exist (Req 3.18).
    - Computes ``time_remaining_seconds = (deadline - now).total_seconds()``
      from the row's ``deadline`` (ISO 8601 UTC string). Negative
      values mean the deadline has passed (Req 3.17).
    - Lists ``s3://{bucket}/raw/{capture_id}/`` to compute
      ``s3_objects_uploaded_count`` and ``bytes_uploaded`` (sum of
      object sizes). The bucket name is supplied via the
      ``DATA_BUCKET_NAME`` environment variable.
    - Sets ``metadata.sourceApi = "s3:ListObjectsV2"``.

    The 10-second response budget (Req 3.17) is enforced indirectly:
    the handler issues at most one DynamoDB ``GetItem`` and one
    paginated S3 ``ListObjectsV2`` call. Both are bounded operations
    and complete well within 10 seconds for the bounded prefix layout
    documented in Req 7.5 (one ``raw/{capture_id}/`` prefix per
    capture, with at most a few hundred pcap rotations).

    Args:
        params: Mapping containing the required ``capture_id`` field
            (matching ``Capture_Id_Format``).

    Returns:
        Response envelope produced by :func:`build_response`.

        On invalid ``capture_id``: ``success=False`` with
        ``metadata.errorCategory = "invalid_parameter"``.

        On missing row: ``success=False`` with
        ``metadata.errorCategory = "not_found"``.

        On configuration errors (missing ``DATA_BUCKET_NAME`` env var
        or ``CAPTURE_STATE_TABLE`` env var): ``success=False`` with
        ``metadata.errorCategory = "configuration_missing"``.

        On AWS errors: ``success=False`` with the appropriate
        ``aws_*`` error category.

        On success: ``success=True`` with the field set documented in
        Req 3.17 (``capture_id``, ``status``, ``start_time``,
        ``deadline``, ``time_remaining_seconds``,
        ``s3_objects_uploaded_count``, ``bytes_uploaded``).
    """
    if not isinstance(params, dict):
        params = {}

    source_api = "s3:ListObjectsV2"

    # ----------------------------------------------------------------
    # Step 1: validate capture_id (Reqs 5.20, 6.10).
    # ----------------------------------------------------------------
    try:
        capture_id = validate_capture_id(params.get("capture_id"))
    except ValidationError as exc:
        return _validation_error_response(
            "get_capture_progress", exc, source_api
        )

    # ----------------------------------------------------------------
    # Step 2: confirm the Capture_State_Table row exists (Req 3.18).
    # ----------------------------------------------------------------
    try:
        row = state.get_capture(capture_id)
    except (ClientError, BotoCoreError) as exc:
        return _aws_error_response(
            "get_capture_progress", exc, source_api, "dynamodb:GetItem"
        )
    except state.StateError as exc:
        return build_response(
            success=False,
            data={"capture_id": capture_id},
            formatted_text=f"get_capture_progress: {exc}",
            source_api=source_api,
            data_freshness="real-time",
            error=f"configuration_missing: {exc}",
            error_category="configuration_missing",
        )

    if row is None:
        return build_response(
            success=False,
            data={"capture_id": capture_id},
            formatted_text=(
                f"get_capture_progress: capture_id {capture_id!r} not "
                "found in the Capture_State_Table."
            ),
            source_api=source_api,
            data_freshness="real-time",
            error=f"not_found: capture_id {capture_id!r} not found",
            error_category="not_found",
        )

    # ----------------------------------------------------------------
    # Step 3: read the Network_Data_Bucket name from the runtime
    # environment. Surface configuration errors as a structured
    # envelope rather than crashing the dispatch loop.
    # ----------------------------------------------------------------
    try:
        bucket_name = _read_required_env(ENV_DATA_BUCKET_NAME)
    except ValidationError as exc:
        return build_response(
            success=False,
            data={"capture_id": capture_id},
            formatted_text=f"get_capture_progress: {exc.message}",
            source_api=source_api,
            data_freshness="real-time",
            error=f"{exc.error_category}: {exc.message}",
            error_category=exc.error_category,
        )

    # ----------------------------------------------------------------
    # Step 4: compute time_remaining_seconds from the row's deadline.
    # The deadline is an ISO 8601 UTC string (see _create_auto_stop_schedule
    # / handle_start_capture). A missing or unparseable value yields
    # None so the response surfaces the absence cleanly rather than
    # crashing the action.
    # ----------------------------------------------------------------
    deadline_raw = row.get("deadline")
    time_remaining_seconds: Optional[float] = None
    if isinstance(deadline_raw, str) and deadline_raw:
        try:
            # ``datetime.fromisoformat`` accepts the ``+00:00`` form
            # produced by ``datetime.isoformat()``; we also accept the
            # ``Z`` suffix defensively (mirrors the parser used in
            # ``state.find_idempotent_capture``).
            deadline_dt = datetime.fromisoformat(
                deadline_raw.replace("Z", "+00:00")
            )
        except ValueError:
            logger.warning(
                "get_capture_progress: unparseable deadline %r on capture %s",
                deadline_raw,
                capture_id,
            )
            deadline_dt = None
        else:
            if deadline_dt.tzinfo is None:
                # Defensive: treat naive timestamps as UTC so the
                # subtraction below does not raise.
                deadline_dt = deadline_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            time_remaining_seconds = (deadline_dt - now).total_seconds()

    # ----------------------------------------------------------------
    # Step 5: list raw/{capture_id}/ in the Network_Data_Bucket.
    # Use the paginator so the count and byte sum are exhaustive
    # regardless of the number of pcap rotations.
    # ----------------------------------------------------------------
    prefix = f"raw/{capture_id}/"
    s3_objects_uploaded_count = 0
    bytes_uploaded = 0
    try:
        s3 = _get_s3_client()
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                s3_objects_uploaded_count += 1
                # ``Size`` may legitimately be 0 for an empty pcap;
                # treat absence defensively as 0 so the sum stays
                # well-defined.
                bytes_uploaded += int(obj.get("Size", 0) or 0)
    except (ClientError, BotoCoreError) as exc:
        return _aws_error_response(
            "get_capture_progress", exc, source_api, "s3:ListObjectsV2"
        )

    # ----------------------------------------------------------------
    # Build the response (Req 3.17 field set).
    # ----------------------------------------------------------------
    if time_remaining_seconds is None:
        time_remaining_clause = "deadline unknown"
    elif time_remaining_seconds >= 0:
        time_remaining_clause = (
            f"{int(time_remaining_seconds)}s remaining"
        )
    else:
        time_remaining_clause = (
            f"deadline passed by {int(-time_remaining_seconds)}s"
        )

    return build_response(
        success=True,
        data={
            "capture_id": capture_id,
            "status": row.get("status"),
            "start_time": row.get("start_time"),
            "deadline": deadline_raw,
            "time_remaining_seconds": time_remaining_seconds,
            "s3_objects_uploaded_count": s3_objects_uploaded_count,
            "bytes_uploaded": bytes_uploaded,
        },
        formatted_text=(
            f"Capture {capture_id}: status={row.get('status')}, "
            f"{time_remaining_clause}, "
            f"{s3_objects_uploaded_count} pcap object(s) uploaded "
            f"({bytes_uploaded} bytes total)."
        ),
        source_api=source_api,
        data_freshness="real-time",
    )


# Pcap Query Actions


# Source API string used by every Pcap_Query_Action handler. Centralized
# here so all 14 query handlers (Tasks 13-18) share the same string and
# Property 10's invariant (uniform metadata.sourceApi) is preserved by
# construction.
_PCAP_QUERY_SOURCE_API = "athena:StartQueryExecution"


def _athena_failure_response(
    action_name: str,
    exc: Exception,
    *,
    error_category: str,
) -> dict:
    """Convert an :mod:`athena_helper` exception into the response envelope.

    Used by Pcap_Query_Action handlers (Tasks 13-18) to surface
    Athena failures uniformly per Req 5.12 ("SHALL NOT return partial
    results"). Mirrors :func:`_aws_error_response` for botocore
    failures but takes an explicit ``error_category`` because the
    Athena helper raises typed exceptions whose category is fixed
    (``athena_query_failed`` for ``AthenaQueryFailedError``,
    ``athena_timeout`` for ``AthenaQueryTimeoutError``,
    ``configuration_missing`` for ``AthenaConfigurationError``).

    Args:
        action_name: The handler's action name for log lines and the
            response ``error`` field.
        exc: The exception raised by :func:`athena_helper.run_athena_query`.
        error_category: Pre-classified ``errorCategory`` value to set
            on ``metadata.errorCategory``.

    Returns:
        Response envelope with ``success=False`` and the failure
        reason included verbatim in both ``formattedText`` and
        ``error`` so an operator can correlate with Athena's
        CloudWatch / Athena history page.
    """
    logger.exception("%s failed at athena:StartQueryExecution", action_name)
    return build_response(
        success=False,
        data={},
        formatted_text=(
            f"{action_name} failed while executing the Athena query: {exc}"
        ),
        source_api=_PCAP_QUERY_SOURCE_API,
        data_freshness="near-real-time",
        error=f"{action_name} failed at athena:StartQueryExecution: {exc}",
        error_category=error_category,
    )


def _validate_query_pcap_sql(value) -> str:
    """Validate the ``sql`` parameter for ``query_pcap``.

    Length and type are enforced here; structural validation
    (SELECT-only, no forbidden keywords, no comments, no semicolons,
    no parens, FROM ``pcap_logs``) is enforced by
    :func:`sql_safety.validate_sql_shape` after this returns.

    Args:
        value: The raw value supplied as ``params["sql"]``.

    Returns:
        The validated SQL string.

    Raises:
        ValidationError: If ``value`` is missing, not a string,
            empty, or longer than :data:`sql_safety.MAX_SQL_LENGTH`
            (16384) characters.
    """
    if value is None:
        raise ValidationError("sql is required")

    if not isinstance(value, str):
        raise ValidationError(
            f"sql must be a string, got {type(value).__name__}"
        )

    if not value.strip():
        raise ValidationError("sql must not be empty")

    if len(value) > MAX_SQL_LENGTH:
        raise ValidationError(
            f"sql must be 1-{MAX_SQL_LENGTH} characters, got {len(value)}"
        )

    return value


def handle_query_pcap(params: dict) -> dict:
    """Execute caller-supplied SELECT SQL against the Pcap_Athena_Table.

    Implements Reqs 5.1, 5.2, 5.3, 5.12, 5.22 plus Correctness
    Properties 5 and 6:

    1. Validate ``capture_id`` against Capture_Id_Format (Req 5.2).
    2. Validate the ``sql`` length and type
       (:func:`_validate_query_pcap_sql`).
    3. Apply the hand-rolled SQL shape validator
       (:func:`sql_safety.validate_sql_shape`): rejects non-SELECT
       input, forbidden top-level keywords (Req 5.3), comments,
       semicolons, parens (subqueries), and any FROM target other
       than ``pcap_logs``. **No Athena call is made when validation
       fails.**
    4. Inject the Capture_Id_Predicate via
       :func:`sql_safety.inject_capture_id_predicate`. The injector
       only runs after the shape validator has accepted the input
       (defense in depth).
    5. Run the rewritten query through
       :func:`athena_helper.run_athena_query`. The helper enforces
       the 60-second wall-clock budget mandated by Req 5.22.
    6. Set ``metadata.sourceApi`` to ``"athena:StartQueryExecution"``
       and ``metadata.dataFreshness`` to ``"near-real-time"``
       (Req 5.22).

    Reqs satisfied:
        * Req 5.1 - SQL length range 1..16384, response within 60s,
          Capture_Id_Predicate injection.
        * Req 5.2 - Reject missing or non-Capture_Id_Format
          ``capture_id`` without calling Athena.
        * Req 5.3 - Reject SQL not beginning with SELECT or
          containing forbidden keywords without calling Athena.
        * Req 5.12 - On Athena failure or timeout, return
          ``success=false`` with the Athena failure reason and no
          partial results.
        * Req 5.22 - ``metadata.sourceApi`` and
          ``metadata.dataFreshness`` are fixed values.

    Args:
        params: Dict with required ``sql`` and ``capture_id`` keys.

    Returns:
        Response envelope produced by :func:`build_response`. On
        success, ``data`` contains ``rows`` (list of column-keyed
        dicts), ``row_count`` (int), and ``capture_id``. The
        rewritten SQL is included in ``data.executed_sql`` so the
        user can verify exactly what was sent to Athena.
    """
    if not isinstance(params, dict):
        params = {}

    # --- 1. Validate capture_id (Req 5.2) -----------------------------
    raw_capture_id = params.get("capture_id")
    try:
        capture_id = validate_capture_id(raw_capture_id)
    except ValidationError as exc:
        # Req 5.2 explicitly says capture_id is required for partition
        # pruning; surface the validation message verbatim.
        return _validation_error_response(
            "query_pcap",
            exc,
            _PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
        )

    # --- 2. Validate sql length / type --------------------------------
    raw_sql = params.get("sql")
    try:
        sql = _validate_query_pcap_sql(raw_sql)
    except ValidationError as exc:
        return _validation_error_response(
            "query_pcap",
            exc,
            _PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
        )

    # --- 3. Shape validator (Req 5.3 plus shape constraint) -----------
    # The validator rejects:
    #   * non-SELECT input,
    #   * forbidden top-level keywords (INSERT/UPDATE/DELETE/DROP/
    #     CREATE/ALTER/TRUNCATE/MSCK/JOIN/UNION/WITH/etc.),
    #   * comments (-- and /* */),
    #   * semicolons,
    #   * parentheses (subqueries),
    #   * FROM targets other than pcap_logs.
    # Returns the token list so the injector can reuse it.
    try:
        tokens = validate_sql_shape(sql)
    except SqlShapeError as exc:
        return build_response(
            success=False,
            data={},
            formatted_text=f"query_pcap: {exc.message}",
            source_api=_PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
            error=f"{exc.error_category}: {exc.message}",
            error_category=exc.error_category,
        )

    # --- 4. Inject Capture_Id_Predicate (Req 5.1, CP-5) ---------------
    # The injector only runs after the shape validator has accepted
    # the input. capture_id is already validated against the safe
    # alphabet so direct interpolation is safe.
    try:
        rewritten_sql = inject_capture_id_predicate(
            sql, capture_id, tokens=tokens,
        )
    except SqlShapeError as exc:
        # Defensive: validate_sql_shape has already accepted the
        # input, so this branch is unreachable in practice. We still
        # surface a structured error rather than crash the dispatch.
        return build_response(
            success=False,
            data={},
            formatted_text=f"query_pcap: {exc.message}",
            source_api=_PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
            error=f"{exc.error_category}: {exc.message}",
            error_category=exc.error_category,
        )

    # --- 5. Execute via the shared Athena helper (Req 5.12, 5.22) -----
    try:
        rows = run_athena_query(rewritten_sql)
    except AthenaConfigurationError as exc:
        return build_response(
            success=False,
            data={},
            formatted_text=f"query_pcap is misconfigured: {exc}",
            source_api=_PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
            error=f"configuration_missing: {exc}",
            error_category="configuration_missing",
        )
    except AthenaQueryFailedError as exc:
        # Req 5.12: failed query → success=false, include Athena
        # failure reason, no partial results.
        return _athena_failure_response(
            "query_pcap", exc, error_category="athena_query_failed",
        )
    except AthenaQueryTimeoutError as exc:
        return _athena_failure_response(
            "query_pcap", exc, error_category="athena_timeout",
        )
    except (ClientError, BotoCoreError) as exc:
        # botocore-level failures (e.g. AccessDeniedException on
        # StartQueryExecution) are surfaced via the existing AWS
        # error pipeline so the response envelope's errorCategory
        # carries the correct AWS-classified label.
        return _aws_error_response(
            "query_pcap",
            exc,
            source_api=_PCAP_QUERY_SOURCE_API,
            failed_operation="athena:StartQueryExecution",
            data_freshness="near-real-time",
        )

    row_count = len(rows)

    # --- 6. Build success envelope -----------------------------------
    if row_count == 0:
        formatted = (
            f"query_pcap returned no rows for capture {capture_id}."
        )
    else:
        formatted = (
            f"query_pcap returned {row_count} row(s) for capture "
            f"{capture_id}."
        )

    return build_response(
        success=True,
        data={
            "capture_id": capture_id,
            "rows": rows,
            "row_count": row_count,
            "executed_sql": rewritten_sql,
        },
        formatted_text=formatted,
        source_api=_PCAP_QUERY_SOURCE_API,
        data_freshness="near-real-time",
    )


def _format_pcap_query_summary(
    action_name: str,
    capture_id: str,
    row_count: int,
    *,
    extra_clause: str = "",
) -> str:
    """Build a uniform ``formattedText`` summary for a Pcap_Query_Action result.

    Used by the simpler Pcap_Query_Action handlers (Tasks 14-18) that
    return rows directly from a single Athena query. The summary
    follows the same pattern as :func:`handle_query_pcap`'s success
    message:

      * "<action_name> returned no matching rows for capture
        <capture_id>." for empty results (Req 5.23 friendly message).
      * "<action_name> returned <N> row(s) for capture <capture_id>."
        otherwise.

    An ``extra_clause`` is appended verbatim when supplied so handlers
    can describe the parameter values they used (e.g. the resolved
    ``min_size`` or the ``stream_id`` they filtered on).

    Args:
        action_name: The handler's action name (e.g.
            ``"search_fragmented_packets"``). Included verbatim in
            the summary.
        capture_id: The capture identifier the query was scoped to.
        row_count: Number of rows the Athena query returned.
        extra_clause: Optional " ... extra context" string to append
            after the standard summary, with a leading space already
            included.

    Returns:
        The formatted summary string for the response envelope's
        ``formattedText`` field.
    """
    if row_count == 0:
        base = (
            f"{action_name} returned no matching rows for capture "
            f"{capture_id}."
        )
    else:
        base = (
            f"{action_name} returned {row_count} row(s) for capture "
            f"{capture_id}."
        )
    return f"{base}{extra_clause}"


def _execute_pcap_query(
    action_name: str,
    sql: str,
    capture_id: str,
    *,
    extra_data: Optional[dict] = None,
    extra_summary_clause: str = "",
    extra_metadata: Optional[dict] = None,
) -> dict:
    """Run a Pcap_Query_Action SQL template and shape the response envelope.

    Shared by every Pcap_Query_Action handler that builds a static
    SQL template internally and runs it through
    :func:`athena_helper.run_athena_query` (Tasks 14-18). Centralizing
    the Athena call here keeps Property 10's invariant ("uniform
    response envelope shape") true by construction: every successful
    response sets ``metadata.sourceApi`` to
    ``"athena:StartQueryExecution"`` and ``metadata.dataFreshness``
    to ``"near-real-time"`` (Req 5.22), every empty-partition
    response carries the friendly ``formattedText`` mandated by
    Req 5.23, and every failure response routes through
    :func:`_athena_failure_response` so the error category reflects
    the underlying Athena helper exception.

    The function does **not** validate ``sql`` or ``capture_id`` —
    those are the caller's responsibility because every handler
    builds its own SQL template from validated inputs and the
    Capture_Id_Predicate is already inlined.

    Args:
        action_name: The handler's action name (e.g.
            ``"search_fragmented_packets"``). Included verbatim in
            error messages and in the success summary.
        sql: The fully-built SQL string to execute, with the
            Capture_Id_Predicate already injected.
        capture_id: The validated capture identifier — included in
            ``data.capture_id`` and in the response summary.
        extra_data: Optional dict merged into the success envelope's
            ``data`` field. The base success envelope always includes
            ``capture_id``, ``rows``, ``row_count``, and ``executed_sql``;
            ``extra_data`` is shallow-merged on top so handlers can
            surface extra context (e.g. the resolved ``min_size``).
        extra_summary_clause: Optional string appended to the summary
            (with a leading space already included) so handlers can
            describe their parameter values.
        extra_metadata: Optional dict of additional metadata fields to
            merge into the response ``metadata``. Used by Flow_Selector-
            aware handlers to attach ``resolved_flow_set``,
            ``matched_stream_count``, and ``matched_streams`` when a
            ``flow_selector`` was supplied (Reqs 5.27 / 19.5 / 19.9).

    Returns:
        Response envelope produced by :func:`build_response`. On
        success, ``data`` carries the column-keyed rows. On any
        Athena failure or configuration error, ``success`` is
        ``False`` with the appropriate ``errorCategory``.
    """
    try:
        rows = run_athena_query(sql)
    except AthenaConfigurationError as exc:
        return build_response(
            success=False,
            data={},
            formatted_text=f"{action_name} is misconfigured: {exc}",
            source_api=_PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
            error=f"configuration_missing: {exc}",
            error_category="configuration_missing",
        )
    except AthenaQueryFailedError as exc:
        return _athena_failure_response(
            action_name, exc, error_category="athena_query_failed",
        )
    except AthenaQueryTimeoutError as exc:
        return _athena_failure_response(
            action_name, exc, error_category="athena_timeout",
        )
    except (ClientError, BotoCoreError) as exc:
        return _aws_error_response(
            action_name,
            exc,
            source_api=_PCAP_QUERY_SOURCE_API,
            failed_operation="athena:StartQueryExecution",
            data_freshness="near-real-time",
        )

    row_count = len(rows)
    data = {
        "capture_id": capture_id,
        "rows": rows,
        "row_count": row_count,
        "executed_sql": sql,
    }
    if extra_data:
        data.update(extra_data)

    return build_response(
        success=True,
        data=data,
        formatted_text=_format_pcap_query_summary(
            action_name,
            capture_id,
            row_count,
            extra_clause=extra_summary_clause,
        ),
        source_api=_PCAP_QUERY_SOURCE_API,
        data_freshness="near-real-time",
        extra_metadata=extra_metadata,
    )


# Default ``min_size`` for ``search_fragmented_packets`` per Req 5.5.
# ~1400 bytes is just below the typical 1500-byte Ethernet MTU minus
# IPv4 + TCP headers, so frames at or above this size are candidates
# for IP-layer fragmentation downstream.
_SEARCH_FRAGMENTED_PACKETS_DEFAULT_MIN_SIZE = 1400


# ---------------------------------------------------------------------------
# Flow_Selector integration helper (Task 17, Reqs 5.24-5.27, 19.1-19.9, 19.14)
# ---------------------------------------------------------------------------


def _resolve_flow_selector_for_handler(
    action_name: str,
    capture_id: str,
    raw_flow_selector,
    *,
    stream_id: Optional[str] = None,
    stream_id_required: bool = False,
) -> dict:
    """Resolve a ``flow_selector`` and produce the SQL fragment + metadata.

    Centralizes the Flow_Selector workflow shared by every
    Pcap_Query_Action handler that targets flows. Performs:

    1. Validation (raw dict shape, IP literals, port ranges, hostnames,
       optional ``stream_id``).
    2. Hostname resolution via the ``combined`` Hostname_Resolution_Strategy
       (``dns_in_capture`` → ``tls_sni_in_capture`` → ``active_dns_lookup``)
       under the per-hostname 5s and overall 15s budgets (Req 19.8).
    3. Construction of the AND-joined Athena predicate fragment honouring
       the source-only / destination-only / both-supplied direction
       rules from Reqs 19.1, 19.6, 19.7.
    4. Combination with a separately-validated ``stream_id`` (Req 5.25).
    5. Either-or enforcement for handlers that previously required
       ``stream_id``: when ``stream_id_required=True`` and neither
       ``stream_id`` nor ``flow_selector`` is supplied, returns a
       structured error (Req 5.26).
    6. Best-effort ``matched_streams`` aggregation via
       :func:`flow_selector.query_matched_streams` (Req 19.5).

    The helper returns a dict describing the outcome rather than the
    response envelope so handlers retain control over their SQL
    template and the ``data`` payload they emit. The dict has one of
    these shapes:

    - On a validation or resolution failure::

          {"error_envelope": <build_response result>}

      The handler returns ``error_envelope`` directly. ``success=False``
      is already set.

    - When neither ``stream_id`` nor ``flow_selector`` was supplied
      (and ``stream_id_required=False``)::

          {"predicate": "", "metadata": {}, "data_extras": {},
           "summary_clause": "", "stream_id": None}

      The handler proceeds with its original capture-id-only SQL.

    - When a ``flow_selector`` (and/or ``stream_id``) was supplied
      successfully::

          {
              "predicate": "AND <sql fragment>",
              "metadata": {
                  "resolved_flow_set": {...},        # always when flow_selector supplied
                  "matched_stream_count": N,         # when flow_selector supplied
                  "matched_streams": [...],          # when flow_selector supplied
                  "active_dns_timeout": "...",       # only when budget exceeded
              },
              "data_extras": {
                  "flow_selector": <validated dict>,  # for response visibility
                  "stream_id": <stream_id or None>,
              },
              "summary_clause": " (flow_selector|stream=<...>).",
              "stream_id": <stream_id or None>,
          }

      The handler appends ``predicate`` after its existing
      ``WHERE capture_id = '<id>'`` clause, merges ``metadata`` and
      ``data_extras`` into its existing extras, and forwards
      ``summary_clause``.

    Args:
        action_name: The handler's action name (used in error
            envelopes).
        capture_id: The validated capture identifier the queries
            scope to.
        raw_flow_selector: The raw value supplied as
            ``params["flow_selector"]`` (may be ``None``).
        stream_id: Optional pre-validated ``stream_id`` from the
            handler's own validation step. The validators in
            :mod:`validation` and :mod:`flow_selector` use the same
            ``[A-Za-z0-9_-]{1,64}`` alphabet so the handler can pass
            its already-validated value here without re-validating.
        stream_id_required: When ``True``, both ``stream_id`` and
            ``flow_selector`` are absent ⇒ structured error per
            Req 5.26. Used by ``correlate_tcp_streams``,
            ``reconstruct_tcp_handshake``, ``analyze_tcp_options``,
            and ``get_request_response_latency``.

    Returns:
        Dict as described above. The handler must check for the
        ``error_envelope`` key first; if present, return it
        immediately. Otherwise, use the other keys to build the SQL.
    """
    has_flow_selector = raw_flow_selector is not None and raw_flow_selector != {}
    has_stream_id = stream_id is not None

    # Req 5.26 — stream_id-or-flow_selector enforcement for handlers
    # that previously required stream_id.
    if stream_id_required and not has_flow_selector and not has_stream_id:
        return {
            "error_envelope": build_response(
                success=False,
                data={},
                formatted_text=(
                    f"{action_name}: either 'stream_id' or a non-empty "
                    "'flow_selector' is required."
                ),
                source_api=_PCAP_QUERY_SOURCE_API,
                data_freshness="near-real-time",
                error=(
                    "invalid_parameter: either 'stream_id' or a non-empty "
                    "'flow_selector' is required"
                ),
                error_category="invalid_parameter",
            )
        }

    # Fast path: nothing supplied → no predicate, no metadata.
    if not has_flow_selector and not has_stream_id:
        return {
            "predicate": "",
            "metadata": {},
            "data_extras": {},
            "summary_clause": "",
            "stream_id": None,
        }

    # Path 1: only stream_id supplied. The handler may already have a
    # tcp_stream literal embedded in its SQL — this branch returns no
    # predicate so we don't double up. The caller signals this by
    # NOT passing stream_id here.
    if not has_flow_selector:
        # The caller passes stream_id for the matched_streams
        # bookkeeping below; but with no flow_selector there's no
        # metadata block to emit and no predicate beyond what the
        # handler already inlines.
        return {
            "predicate": "",
            "metadata": {},
            "data_extras": {},
            "summary_clause": "",
            "stream_id": stream_id,
        }

    # Path 2: flow_selector supplied (with or without stream_id).
    try:
        # If the handler already validated stream_id, fold it into
        # the dict so resolve_flow_selector treats stream_id and
        # flow_selector.stream_id as the same constraint. We forbid
        # conflicting values to surface programming errors loudly.
        if has_stream_id:
            existing = (
                raw_flow_selector.get("stream_id")
                if isinstance(raw_flow_selector, dict)
                else None
            )
            if existing is not None and existing != stream_id:
                return {
                    "error_envelope": build_response(
                        success=False,
                        data={},
                        formatted_text=(
                            f"{action_name}: 'stream_id' parameter and "
                            "'flow_selector.stream_id' must match when both "
                            "are supplied."
                        ),
                        source_api=_PCAP_QUERY_SOURCE_API,
                        data_freshness="near-real-time",
                        error=(
                            "invalid_parameter: stream_id mismatch "
                            f"(top-level={stream_id}, "
                            f"flow_selector.stream_id={existing})"
                        ),
                        error_category="invalid_parameter",
                    )
                }
            # Inject the top-level stream_id into the dict so it
            # participates in the AND-combined predicate and shows up
            # in metadata.resolved_flow_set as well.
            if isinstance(raw_flow_selector, dict):
                raw_flow_selector = dict(raw_flow_selector)
                raw_flow_selector.setdefault("stream_id", stream_id)

        resolved = resolve_flow_selector(capture_id, raw_flow_selector)
    except FlowSelectorError as exc:
        # validate_flow_selector and resolve_flow_selector both surface
        # FlowSelectorError. The `error_category` distinguishes
        # invalid_parameter (bad shape / IP / port / hostname) from
        # hostname_unresolved (Req 19.3).
        return {
            "error_envelope": build_response(
                success=False,
                data={},
                formatted_text=f"{action_name}: {exc.message}",
                source_api=_PCAP_QUERY_SOURCE_API,
                data_freshness="near-real-time",
                error=f"{exc.error_category}: {exc.message}",
                error_category=exc.error_category,
            )
        }

    predicate_fragment = build_flow_predicate(resolved)
    metadata: dict = {
        "resolved_flow_set": build_resolved_flow_set_metadata(resolved),
    }
    if resolved.timeout_note is not None:
        metadata["active_dns_timeout"] = resolved.timeout_note

    # Run the matched-streams aggregate (Req 19.5). This is best-effort:
    # query_matched_streams returns (0, []) on Athena error so a
    # secondary-query failure never blocks the main query's results.
    matched_count, matched_streams = query_matched_streams(
        capture_id, predicate_fragment,
    )
    metadata["matched_stream_count"] = matched_count
    metadata["matched_streams"] = matched_streams

    # Surface the validated selector (and any stream_id) under
    # ``data`` so the orchestration agent can reproduce the exact
    # invocation in follow-up queries.
    data_extras: dict = {
        "flow_selector": _validated_selector_to_dict(resolved),
    }
    if stream_id is not None or resolved.stream_id is not None:
        data_extras["stream_id"] = stream_id or resolved.stream_id

    # Build a short summary clause for the formattedText output.
    summary_pieces = []
    if resolved.source_hostname is not None or resolved.source.ips:
        if resolved.source_hostname is not None:
            ip_count = len(resolved.source.ips)
            summary_pieces.append(
                f"src={resolved.source_hostname} ({ip_count} IP(s))"
            )
        elif resolved.source.ips:
            summary_pieces.append(f"src={','.join(resolved.source.ips)}")
        if resolved.source.port is not None:
            summary_pieces.append(f"src_port={resolved.source.port}")
    if resolved.destination_hostname is not None or resolved.destination.ips:
        if resolved.destination_hostname is not None:
            ip_count = len(resolved.destination.ips)
            summary_pieces.append(
                f"dst={resolved.destination_hostname} ({ip_count} IP(s))"
            )
        elif resolved.destination.ips:
            summary_pieces.append(f"dst={','.join(resolved.destination.ips)}")
        if resolved.destination.port is not None:
            summary_pieces.append(f"dst_port={resolved.destination.port}")
    if resolved.stream_id is not None:
        summary_pieces.append(f"stream_id={resolved.stream_id}")
    if matched_count:
        summary_pieces.append(f"matched_streams={matched_count}")
    summary_clause = (
        f" (flow_selector: {', '.join(summary_pieces)})."
        if summary_pieces
        else ""
    )

    # Format the predicate as an SQL clause that can be appended to
    # an existing ``WHERE capture_id = '...'`` predicate. Empty
    # fragment is benign — a flow_selector that contains only
    # validated-but-not-actionable fields (e.g. only a port via the
    # synthetic literal "" IP) collapses to no predicate. The handler
    # still gets metadata + summary text.
    predicate = f"AND {predicate_fragment}" if predicate_fragment else ""

    return {
        "predicate": predicate,
        "metadata": metadata,
        "data_extras": data_extras,
        "summary_clause": summary_clause,
        "stream_id": resolved.stream_id,
    }


def _validated_selector_to_dict(resolved: ResolvedFlowSelector) -> dict:
    """Render a :class:`ResolvedFlowSelector` for inclusion in ``data``.

    Returns the supplied (not resolved) field values so the response's
    ``data.flow_selector`` shows the orchestration agent's original
    request. Resolved tuples are surfaced separately under
    ``metadata.resolved_flow_set``.
    """
    out: dict = {}
    if resolved.source_hostname is not None:
        out["source_hostname"] = resolved.source_hostname
    if resolved.source.ips and resolved.source_hostname is None:
        # Caller supplied a literal IP rather than a hostname.
        # `ips` already has one entry in that case.
        out["source_ip"] = resolved.source.ips[0]
    if resolved.source.port is not None:
        out["source_port"] = resolved.source.port
    if resolved.destination_hostname is not None:
        out["destination_hostname"] = resolved.destination_hostname
    if resolved.destination.ips and resolved.destination_hostname is None:
        out["destination_ip"] = resolved.destination.ips[0]
    if resolved.destination.port is not None:
        out["destination_port"] = resolved.destination.port
    if resolved.stream_id is not None:
        out["stream_id"] = resolved.stream_id
    return out


def handle_search_fragmented_packets(params: dict) -> dict:
    """Return packets from a capture whose ``frame_size`` is at or above ``min_size``.

    Implements Reqs 5.4, 5.5, 5.7 (capture_id required), 5.12 (no
    partial results on failure), 5.22 (60s SLA + uniform metadata),
    and 5.23 (friendly empty-partition response).

    Behaviour:

    1. Validate ``capture_id`` against ``Capture_Id_Format`` (Req 5.7).
       Reject with ``invalid_parameter`` *before* any Athena call.
    2. When supplied, validate ``min_size`` is an integer in
       ``[64, 65535]`` (Req 5.4). When omitted, default to 1400
       bytes (Req 5.5).
    3. Build the Athena SQL template:

         ```
         SELECT frame_time, frame_size, src_ip, src_port, dst_ip,
                dst_port, protocol, tcp_stream
         FROM pcap_logs
         WHERE capture_id = '<id>'
           AND frame_size >= <min_size>
         ORDER BY frame_size DESC, frame_time ASC
         LIMIT 1000
         ```

       The Capture_Id_Predicate is inlined directly: ``capture_id``
       has been validated against the safe alphabet ``[A-Za-z0-9_-]``
       so direct interpolation is provably injection-free. The
       ``min_size`` integer is interpolated as a numeric literal.
       The ``LIMIT 1000`` is a defensive cap so a captures-wide query
       cannot produce a multi-megabyte response payload that would
       blow the AgentCore response budget; pagination beyond the
       first 1000 rows is intentionally out of scope for this action.
    4. Execute via :func:`_execute_pcap_query`, which routes Athena
       failures through :func:`_athena_failure_response` (Req 5.12)
       and shapes the success envelope with the uniform metadata
       documented in Req 5.22.

    Args:
        params: Dict with required ``capture_id`` and optional
            ``min_size`` keys.

    Returns:
        Response envelope produced by :func:`build_response`. On
        success, ``data`` includes ``capture_id``, ``rows`` (list of
        column-keyed dicts), ``row_count``, ``executed_sql``, and
        ``min_size`` (the resolved value used in the predicate).
    """
    if not isinstance(params, dict):
        params = {}

    # --- 1. Validate capture_id (Req 5.7) -----------------------------
    try:
        capture_id = validate_capture_id(params.get("capture_id"))
    except ValidationError as exc:
        return _validation_error_response(
            "search_fragmented_packets",
            exc,
            _PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
        )

    # --- 2. Resolve min_size (Reqs 5.4, 5.5) --------------------------
    raw_min_size = params.get("min_size")
    if raw_min_size is None:
        min_size = _SEARCH_FRAGMENTED_PACKETS_DEFAULT_MIN_SIZE
    else:
        try:
            min_size = validate_min_size(raw_min_size)
        except ValidationError as exc:
            return _validation_error_response(
                "search_fragmented_packets",
                exc,
                _PCAP_QUERY_SOURCE_API,
                data_freshness="near-real-time",
            )

    # --- 3. Build SQL with Capture_Id_Predicate inlined ---------------
    # capture_id is validated against [A-Za-z0-9_-]{1,128} so the
    # single-quoted literal is safe to interpolate directly.
    # min_size is a validated integer in [64, 65535].
    sql = (
        "SELECT frame_time, frame_size, src_ip, src_port, "  # nosec B608
        "dst_ip, dst_port, protocol, tcp_stream "
        "FROM pcap_logs "
        f"WHERE capture_id = '{capture_id}' "
        f"AND frame_size >= {min_size} "
        "ORDER BY frame_size DESC, frame_time ASC "
        "LIMIT 1000"
    )

    return _execute_pcap_query(
        "search_fragmented_packets",
        sql,
        capture_id,
        extra_data={"min_size": min_size},
        extra_summary_clause=f" (min_size={min_size}).",
    )


def handle_correlate_tcp_streams(params: dict) -> dict:
    """Return all packets belonging to a TCP stream (or resolved flow), ordered by timestamp.

    Implements Reqs 5.6, 5.7 (capture_id required), 5.12 (no partial
    results on failure), 5.22 (60s SLA + uniform metadata), 5.23
    (friendly empty-partition response), 5.24-5.27 (Flow_Selector
    integration), and 5.26 (either-or stream_id / flow_selector).

    Behaviour:

    1. Validate ``capture_id`` against ``Capture_Id_Format`` (Req 5.7).
       Reject with ``invalid_parameter`` *before* any Athena call.
    2. When ``stream_id`` is supplied, validate it against the
       stream-identifier pattern ``[A-Za-z0-9_-]{1,64}`` (Req 5.21).
    3. When ``flow_selector`` is supplied, validate and resolve it
       via :func:`_resolve_flow_selector_for_handler` (Reqs 5.24-5.27).
       When neither ``stream_id`` nor ``flow_selector`` is supplied,
       reject the request (Req 5.26).
    4. Build the Athena SQL template combining the Capture_Id_Predicate,
       optional ``tcp_stream = '<stream_id>'`` literal, and any
       Flow_Selector predicate fragment (AND-combined per Req 5.25).
    5. Execute via :func:`_execute_pcap_query` and surface
       ``metadata.resolved_flow_set``, ``matched_stream_count``, and
       ``matched_streams`` when a Flow_Selector was supplied (Reqs
       5.27, 19.5, 19.9).

    Args:
        params: Dict with required ``capture_id`` and either
            ``stream_id`` (string) or ``flow_selector`` (Flow_Selector
            dict) or both.

    Returns:
        Response envelope produced by :func:`build_response`.
    """
    if not isinstance(params, dict):
        params = {}

    # --- 1. Validate capture_id (Req 5.7) -----------------------------
    try:
        capture_id = validate_capture_id(params.get("capture_id"))
    except ValidationError as exc:
        return _validation_error_response(
            "correlate_tcp_streams",
            exc,
            _PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
        )

    # --- 2. Validate stream_id when supplied (Reqs 5.6, 5.21) --------
    raw_stream_id = params.get("stream_id")
    stream_id = None
    if raw_stream_id is not None:
        try:
            stream_id = validate_stream_id(raw_stream_id)
        except ValidationError as exc:
            return _validation_error_response(
                "correlate_tcp_streams",
                exc,
                _PCAP_QUERY_SOURCE_API,
                data_freshness="near-real-time",
            )

    # --- 3. Resolve flow_selector and enforce stream_id-or-selector --
    flow_resolution = _resolve_flow_selector_for_handler(
        "correlate_tcp_streams",
        capture_id,
        params.get("flow_selector"),
        stream_id=stream_id,
        stream_id_required=True,
    )
    if "error_envelope" in flow_resolution:
        return flow_resolution["error_envelope"]

    # --- 4. Build SQL with Capture_Id_Predicate + flow predicate -----
    # ``stream_id`` participates in the predicate via flow_resolution
    # when a flow_selector is supplied; otherwise we inline a
    # ``tcp_stream = '<stream_id>'`` literal directly. Both
    # ``capture_id`` and ``stream_id`` are validated against
    # ``[A-Za-z0-9_-]`` alphabets so direct interpolation is provably
    # injection-free.
    has_flow_selector = params.get("flow_selector") not in (None, {})
    base_predicate = f"WHERE capture_id = '{capture_id}'"
    if not has_flow_selector and stream_id is not None:
        # No flow_selector → keep the original tcp_stream literal so
        # the handler's behavior is unchanged for callers that pass
        # only stream_id.
        base_predicate += f" AND tcp_stream = '{stream_id}'"
    flow_predicate = flow_resolution.get("predicate", "")
    if flow_predicate:
        base_predicate += f" {flow_predicate}"

    sql = (
        "SELECT frame_time, frame_size, src_ip, src_port, "  # nosec B608
        "dst_ip, dst_port, protocol, tcp_seq, tcp_ack, "
        "tcp_flags, tcp_window, tcp_stream, frame_payload_summary "
        "FROM pcap_logs "
        f"{base_predicate} "
        "ORDER BY frame_time ASC "
        "LIMIT 10000"
    )

    extra_data: dict = {}
    extra_summary = ""
    if stream_id is not None and not has_flow_selector:
        extra_data["stream_id"] = stream_id
        extra_summary = f" (stream_id={stream_id})."
    if flow_resolution.get("data_extras"):
        extra_data.update(flow_resolution["data_extras"])
    if flow_resolution.get("summary_clause"):
        extra_summary = flow_resolution["summary_clause"]

    return _execute_pcap_query(
        "correlate_tcp_streams",
        sql,
        capture_id,
        extra_data=extra_data,
        extra_summary_clause=extra_summary,
        extra_metadata=flow_resolution.get("metadata") or None,
    )


def handle_detect_retransmissions(params: dict) -> dict:
    """Group TCP retransmissions by destination IP/port, ordered by count desc.

    Implements Reqs 5.7 (capture_id required), 5.8 (the action's main
    contract), 5.12 (no partial results on failure), 5.22 (60s SLA +
    uniform metadata), 5.23 (friendly empty-partition response), and
    5.24-5.27 / 19.1-19.9 (Flow_Selector targeting when supplied).

    Behaviour:

    1. Validate ``capture_id`` against ``Capture_Id_Format`` (Req 5.7).
       Reject with ``invalid_parameter`` *before* any Athena call.
    2. When ``flow_selector`` is supplied, validate and resolve it
       and AND-combine its predicate with the Capture_Id_Predicate.
    3. Build the Athena SQL template grouping by ``(dst_ip, dst_port)``
       and ordering by ``retransmission_count DESC``.
    4. Execute via :func:`_execute_pcap_query`.

    Args:
        params: Dict with required ``capture_id`` and optional
            ``flow_selector`` keys.

    Returns:
        Response envelope produced by :func:`build_response`. On
        success, ``data`` includes ``capture_id``, ``rows`` (list of
        column-keyed dicts grouped by destination), ``row_count``,
        and ``executed_sql``. When a ``flow_selector`` is supplied,
        ``metadata`` carries ``resolved_flow_set``,
        ``matched_stream_count``, and ``matched_streams``.
    """
    if not isinstance(params, dict):
        params = {}

    # --- 1. Validate capture_id (Req 5.7) -----------------------------
    try:
        capture_id = validate_capture_id(params.get("capture_id"))
    except ValidationError as exc:
        return _validation_error_response(
            "detect_retransmissions",
            exc,
            _PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
        )

    # --- 2. Resolve flow_selector when supplied ----------------------
    flow_resolution = _resolve_flow_selector_for_handler(
        "detect_retransmissions",
        capture_id,
        params.get("flow_selector"),
    )
    if "error_envelope" in flow_resolution:
        return flow_resolution["error_envelope"]

    # --- 3. Build SQL with Capture_Id_Predicate inlined --------------
    # capture_id is validated against [A-Za-z0-9_-]{1,128} so the
    # single-quoted literal is safe to interpolate directly.
    #
    # The table schema does not include a ``tcp_analysis_retransmission``
    # boolean column. Instead we detect retransmissions by finding
    # duplicate (tcp_stream, tcp_seq) pairs — a repeated sequence
    # number within the same stream indicates a retransmitted segment.
    flow_predicate = flow_resolution.get("predicate", "")
    flow_predicate_clause = f" {flow_predicate}" if flow_predicate else ""
    sql = (
        "WITH seq_counts AS ("  # nosec B608
        "SELECT tcp_stream, tcp_seq, dst_ip, dst_port, "
        "COUNT(*) AS cnt, "
        "MIN(frame_time) AS first_seen, "
        "MAX(frame_time) AS last_seen "
        "FROM pcap_logs "
        f"WHERE capture_id = '{capture_id}' "
        "AND tcp_seq IS NOT NULL"
        f"{flow_predicate_clause} "
        "GROUP BY tcp_stream, tcp_seq, dst_ip, dst_port "
        "HAVING COUNT(*) > 1"
        ") "
        "SELECT dst_ip, dst_port, "
        "SUM(cnt - 1) AS retransmission_count, "
        "COUNT(DISTINCT tcp_stream) AS affected_stream_count, "
        "MIN(first_seen) AS first_retransmission_time, "
        "MAX(last_seen) AS last_retransmission_time "
        "FROM seq_counts "
        "GROUP BY dst_ip, dst_port "
        "ORDER BY retransmission_count DESC, dst_ip, dst_port "
        "LIMIT 1000"
    )

    return _execute_pcap_query(
        "detect_retransmissions",
        sql,
        capture_id,
        extra_data=flow_resolution.get("data_extras") or None,
        extra_summary_clause=flow_resolution.get("summary_clause", ""),
        extra_metadata=flow_resolution.get("metadata") or None,
    )


# Default ``top_n`` for ``get_conversation_stats`` per Req 5.11.
# 20 conversations is enough to cover the most-active flows in a
# typical demo capture without producing a multi-megabyte response
# payload that would blow the AgentCore response budget.
_GET_CONVERSATION_STATS_DEFAULT_TOP_N = 20


# ``tls_handshake_type`` value identifying a TLS Client Hello message
# in the tshark-derived ``pcap_logs`` schema (design Pcap_Athena_Table
# section: "1=Client Hello, 2=Server Hello, etc."). Used by
# :func:`handle_check_tls_hello_size`.
_TLS_HANDSHAKE_TYPE_CLIENT_HELLO = 1


def handle_check_tls_hello_size(params: dict) -> dict:
    """Return one row per TLS Client Hello with frame size and fragment count.

    Implements Reqs 5.7 (capture_id required), 5.9 (the action's main
    contract — one row per TLS Client Hello with the documented
    columns), 5.12 (no partial results on failure), 5.22 (60s SLA +
    uniform metadata), and 5.23 (friendly empty-partition response).

    Behaviour:

    1. Validate ``capture_id`` against ``Capture_Id_Format`` (Req 5.7).
       Reject with ``invalid_parameter`` *before* any Athena call.
    2. Build the Athena SQL template:

         ```
         SELECT frame_size,
                tls_fragment_count AS fragment_count,
                src_ip            AS source_ip,
                src_port          AS source_port,
                dst_ip            AS destination_ip,
                dst_port          AS destination_port
         FROM pcap_logs
         WHERE capture_id = '<id>'
           AND tls_handshake_type = 1
         ORDER BY frame_size DESC, frame_time ASC
         LIMIT 1000
         ```

       The Capture_Id_Predicate is inlined directly: ``capture_id``
       has been validated against the safe alphabet ``[A-Za-z0-9_-]``
       so direct interpolation is provably injection-free. The
       ``tls_handshake_type = 1`` filter selects only TLS Client
       Hello messages per the tshark-derived ``pcap_logs`` schema
       (design Pcap_Athena_Table section: "1=Client Hello"). The
       column aliases (``source_ip``, ``source_port``,
       ``destination_ip``, ``destination_port``) match the response
       schema mandated by Req 5.9 verbatim while the underlying
       schema uses the shorter ``src_*``/``dst_*`` column names. The
       ``tls_fragment_count`` column (computed in the transformation
       pipeline per the design schema) is aliased to ``fragment_count``
       to match Req 5.9. Ordering by ``frame_size DESC`` surfaces the
       largest Client Hellos first — these are the ones most likely
       to be fragmented and therefore the most useful for a
       TLS-fragmentation troubleshooting workflow. The ``LIMIT 1000``
       is a defensive cap so a captures-wide query cannot produce a
       multi-megabyte response payload that would blow the AgentCore
       response budget; pagination beyond the first 1000 rows is
       intentionally out of scope for this action.
    3. Execute via :func:`_execute_pcap_query`, which routes Athena
       failures through :func:`_athena_failure_response` (Req 5.12)
       and shapes the success envelope with the uniform metadata
       documented in Req 5.22.

    Args:
        params: Dict with required ``capture_id`` key.

    Returns:
        Response envelope produced by :func:`build_response`. On
        success, ``data`` includes ``capture_id``, ``rows`` (list of
        column-keyed dicts with the Req 5.9 column set),
        ``row_count``, and ``executed_sql``.
    """
    if not isinstance(params, dict):
        params = {}

    # --- 1. Validate capture_id (Req 5.7) -----------------------------
    try:
        capture_id = validate_capture_id(params.get("capture_id"))
    except ValidationError as exc:
        return _validation_error_response(
            "check_tls_hello_size",
            exc,
            _PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
        )

    # --- 2. Resolve flow_selector when supplied ----------------------
    flow_resolution = _resolve_flow_selector_for_handler(
        "check_tls_hello_size",
        capture_id,
        params.get("flow_selector"),
    )
    if "error_envelope" in flow_resolution:
        return flow_resolution["error_envelope"]

    # --- 3. Build SQL with Capture_Id_Predicate inlined ---------------
    # capture_id is validated against [A-Za-z0-9_-]{1,128} so the
    # single-quoted literal is safe to interpolate directly. There
    # are no other user-supplied predicate values in this query.
    # Column aliases match the response schema mandated by Req 5.9.
    flow_predicate = flow_resolution.get("predicate", "")
    flow_predicate_clause = f" {flow_predicate}" if flow_predicate else ""
    sql = (
        "SELECT frame_size, "  # nosec B608
        "tls_fragment_count AS fragment_count, "
        "tls_record_size AS record_size, "
        "tls_sni AS server_name, "
        "src_ip AS source_ip, "
        "src_port AS source_port, "
        "dst_ip AS destination_ip, "
        "dst_port AS destination_port "
        "FROM pcap_logs "
        f"WHERE capture_id = '{capture_id}' "
        f"AND tls_handshake_type = {_TLS_HANDSHAKE_TYPE_CLIENT_HELLO}"
        f"{flow_predicate_clause} "
        "ORDER BY frame_size DESC, frame_time ASC "
        "LIMIT 1000"
    )

    return _execute_pcap_query(
        "check_tls_hello_size",
        sql,
        capture_id,
        extra_data=flow_resolution.get("data_extras") or None,
        extra_summary_clause=flow_resolution.get("summary_clause", ""),
        extra_metadata=flow_resolution.get("metadata") or None,
    )


def handle_get_conversation_stats(params: dict) -> dict:
    """Return the top ``top_n`` conversations by total bytes descending.

    Implements Reqs 5.7 (capture_id required), 5.10 (the action's main
    contract — top conversations by total bytes with packet count),
    5.11 (default ``top_n`` = 20 when omitted), 5.12 (no partial
    results on failure), 5.22 (60s SLA + uniform metadata), and 5.23
    (friendly empty-partition response).

    Behaviour:

    1. Validate ``capture_id`` against ``Capture_Id_Format`` (Req 5.7).
       Reject with ``invalid_parameter`` *before* any Athena call.
    2. When supplied, validate ``top_n`` is an integer in
       ``[1, 1000]`` (Req 5.10). When omitted, default to 20
       (Req 5.11).
    3. Build the Athena SQL template:

         ```
         SELECT src_ip, src_port, dst_ip, dst_port, protocol,
                SUM(frame_size) AS total_bytes,
                COUNT(*)        AS packet_count
         FROM pcap_logs
         WHERE capture_id = '<id>'
         GROUP BY src_ip, src_port, dst_ip, dst_port, protocol
         ORDER BY total_bytes DESC, packet_count DESC,
                  src_ip, dst_ip, src_port, dst_port
         LIMIT <top_n>
         ```

       The Capture_Id_Predicate is inlined directly: ``capture_id``
       has been validated against the safe alphabet ``[A-Za-z0-9_-]``
       so direct interpolation is provably injection-free. The
       ``top_n`` integer is interpolated as a numeric literal in the
       ``LIMIT`` clause. A "conversation" is the 5-tuple of
       ``(src_ip, src_port, dst_ip, dst_port, protocol)`` so that
       client-to-server and server-to-client halves of the same TCP
       stream are reported as distinct rows; this matches typical
       Wireshark "Conversations" reporting and gives the
       orchestration agent enough fidelity to identify per-direction
       imbalances. ``total_bytes`` is the sum of ``frame_size`` (the
       wire frame size, post-VXLAN-decap, per the design's
       Pcap_Athena_Table schema) and ``packet_count`` is a row count
       — together these satisfy Req 5.10's "total bytes descending
       with packet count". Secondary ordering by ``packet_count
       DESC`` then by the 5-tuple components ensures a deterministic
       order across runs when multiple conversations have identical
       byte totals.
    4. Execute via :func:`_execute_pcap_query`, which routes Athena
       failures through :func:`_athena_failure_response` (Req 5.12)
       and shapes the success envelope with the uniform metadata
       documented in Req 5.22.

    Args:
        params: Dict with required ``capture_id`` and optional
            ``top_n`` keys.

    Returns:
        Response envelope produced by :func:`build_response`. On
        success, ``data`` includes ``capture_id``, ``rows`` (list of
        column-keyed dicts with one row per conversation),
        ``row_count``, ``executed_sql``, and ``top_n`` (the resolved
        value used in the ``LIMIT`` clause).
    """
    if not isinstance(params, dict):
        params = {}

    # --- 1. Validate capture_id (Req 5.7) -----------------------------
    try:
        capture_id = validate_capture_id(params.get("capture_id"))
    except ValidationError as exc:
        return _validation_error_response(
            "get_conversation_stats",
            exc,
            _PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
        )

    # --- 2. Resolve top_n (Reqs 5.10, 5.11) ---------------------------
    raw_top_n = params.get("top_n")
    if raw_top_n is None:
        top_n = _GET_CONVERSATION_STATS_DEFAULT_TOP_N
    else:
        try:
            top_n = validate_top_n(raw_top_n)
        except ValidationError as exc:
            return _validation_error_response(
                "get_conversation_stats",
                exc,
                _PCAP_QUERY_SOURCE_API,
                data_freshness="near-real-time",
            )

    # --- 3. Resolve flow_selector when supplied ----------------------
    flow_resolution = _resolve_flow_selector_for_handler(
        "get_conversation_stats",
        capture_id,
        params.get("flow_selector"),
    )
    if "error_envelope" in flow_resolution:
        return flow_resolution["error_envelope"]

    # --- 4. Build SQL with Capture_Id_Predicate inlined ---------------
    # capture_id is validated against [A-Za-z0-9_-]{1,128} so the
    # single-quoted literal is safe to interpolate directly.
    # top_n is a validated integer in [1, 1000].
    flow_predicate = flow_resolution.get("predicate", "")
    flow_predicate_clause = f" {flow_predicate}" if flow_predicate else ""
    sql = (
        "SELECT src_ip, src_port, dst_ip, dst_port, protocol, "  # nosec B608
        "SUM(frame_size) AS total_bytes, "
        "COUNT(*) AS packet_count "
        "FROM pcap_logs "
        f"WHERE capture_id = '{capture_id}'"
        f"{flow_predicate_clause} "
        "GROUP BY src_ip, src_port, dst_ip, dst_port, protocol "
        "ORDER BY total_bytes DESC, packet_count DESC, "
        "src_ip, dst_ip, src_port, dst_port "
        f"LIMIT {top_n}"
    )

    extra_data: dict = {"top_n": top_n}
    if flow_resolution.get("data_extras"):
        extra_data.update(flow_resolution["data_extras"])
    summary_clause = flow_resolution.get("summary_clause") or f" (top_n={top_n})."

    return _execute_pcap_query(
        "get_conversation_stats",
        sql,
        capture_id,
        extra_data=extra_data,
        extra_summary_clause=summary_clause,
        extra_metadata=flow_resolution.get("metadata") or None,
    )


# ---------------------------------------------------------------------------
# TCP-level analysis actions (Task 16, Reqs 5.13-5.23)
#
# Each handler below implements one Pcap_Query_Action documented in
# the design's "Action-specific output schemas" table. They follow the
# same pattern as the Task 14/15 handlers (``handle_correlate_tcp_streams``,
# ``handle_detect_retransmissions``, ``handle_check_tls_hello_size``,
# ``handle_get_conversation_stats``):
#
# 1. Validate ``capture_id`` against Capture_Id_Format (Req 5.20).
# 2. Validate ``stream_id`` (where required by Reqs 5.21, 5.26) against
#    the Stream_Id pattern.
# 3. Build a static SQL template with the Capture_Id_Predicate (and
#    when applicable the ``tcp_stream`` predicate) inlined directly.
#    Both identifiers are validated against safe alphabets (``[A-Za-z0-9_-]``)
#    so direct single-quoted interpolation is provably injection-free.
# 4. Run via :func:`_execute_pcap_query` (or :func:`run_athena_query`
#    directly when the handler post-processes rows in Python).
# 5. Return the response envelope shaped per Req 5.22 (uniform
#    metadata) and Req 5.23 (friendly empty-partition text).
#
# Where the action contract requires *computed* fields beyond simple
# row projection (notably ``handshake_complete`` /
# ``handshake_duration_ms`` / ``handshake_failure_reason`` for
# ``reconstruct_tcp_handshake``), the handler runs the Athena query
# directly and computes the derived fields in Python rather than
# embedding multi-stage CTEs in SQL. This trades ~10 lines of pure
# Python (deterministic, unit-testable, no Athena round trip) for
# significantly more readable SQL templates.
#
# All TCP-level analysis handlers derive metrics from raw columns
# available in the Glue table schema (tcp_seq, tcp_window, tcp_flags,
# frame_time, etc.). The ``tcp_analysis_*`` boolean columns from
# tshark are NOT present in the production schema; instead we compute
# equivalent metrics from raw data (e.g. duplicate tcp_seq for
# retransmissions, tcp_window = 0 for zero-window events, sequence
# number reversals for out-of-order detection).
# ---------------------------------------------------------------------------


# TCP flag bit constants (RFC 9293 §3.1). The ``tcp_flags`` column in
# the Pcap_Athena_Table holds a string like ``"0x002"`` (SYN), ``"0x012"``
# (SYN+ACK), ``"0x010"`` (ACK), ``"0x004"`` (RST), ``"0x011"`` (FIN+ACK).
# We expose the bit positions as named constants so the SQL templates
# below can use the same vocabulary as the Python post-processing
# code that interprets the Athena rows.
_TCP_FLAG_SYN = 0x02
_TCP_FLAG_RST = 0x04
_TCP_FLAG_ACK = 0x10
_TCP_FLAG_FIN = 0x01

# Maximum number of handshake-related frames we project. A pure
# 3-way handshake produces 3 frames; a SYN-with-retransmits scenario
# adds a few more. Capping at 64 keeps the response payload small
# while still preserving every retransmit needed to set
# ``handshake_failure_reason = syn_retransmitted``.
_HANDSHAKE_FRAME_LIMIT = 64

# Defensive cap on rows returned by the per-RST query. Even a
# DDoS-style storm of resets is bounded by the capture deadline;
# 1000 rows is well above realistic per-capture totals while keeping
# the response payload small.
_TCP_RESET_ROW_LIMIT = 1000

# Defensive cap on per-stream aggregate rows. A 15-minute capture
# typically contains ~tens of streams; 1000 is plenty for demo
# captures and keeps response payloads small.
_PER_STREAM_AGGREGATE_LIMIT = 1000


def _hex_flags_match(column: str, flag_bits: int, mask: int = 0xFF) -> str:
    """Emit the SQL fragment that matches TCP flag bits.

    The ``tcp_flags`` column is stored as a hex string (e.g. ``"0x002"``)
    rather than an integer so tshark's textual output round-trips
    losslessly. To test bit patterns we cast through ``from_base(...)``.

    Args:
        column: The column reference (e.g. ``"tcp_flags"``).
        flag_bits: The bit pattern to match (e.g.
            ``_TCP_FLAG_SYN | _TCP_FLAG_ACK`` for SYN-ACK).
        mask: The bit positions to consider (defaults to all 8 bits
            so ``flag_bits`` exactly defines the match).

    Returns:
        A SQL boolean expression suitable for inclusion in a
        ``WHERE`` predicate.
    """
    return (
        f"bitwise_and(from_base(replace({column}, '0x', ''), 16), {mask}) = {flag_bits}"
    )


# ---------- reconstruct_tcp_handshake -------------------------------------


# Handshake_Failure_Reason enumeration (Req 5.13).
_HANDSHAKE_REASON_COMPLETE = "complete"
_HANDSHAKE_REASON_SYN_ACK_MISSING = "syn_ack_missing"
_HANDSHAKE_REASON_FINAL_ACK_MISSING = "final_ack_missing"
_HANDSHAKE_REASON_SYN_RETRANSMITTED = "syn_retransmitted"
_HANDSHAKE_REASON_NOT_OBSERVED = "not_observed"


def _classify_handshake(rows: list, stream_id: str) -> dict:
    """Compute the derived handshake status fields from the SYN/SYN-ACK/ACK rows.

    Implements the closed enumeration in Req 5.13 by walking the rows
    in temporal order:

    - **not_observed**: zero SYN frames in the projection (the stream
      either pre-dated the capture or never began).
    - **syn_retransmitted**: more than one SYN frame seen, *and* no
      successful 3-way handshake completed before the retransmits.
      We surface this as a distinct reason rather than overlay it on
      ``complete`` because a retransmitted SYN is itself a useful
      signal even if the handshake eventually completed.
    - **syn_ack_missing**: SYN seen, no SYN-ACK seen.
    - **final_ack_missing**: SYN and SYN-ACK seen, no final ACK seen.
    - **complete**: SYN, SYN-ACK, and final ACK seen, with no SYN
      retransmits before the SYN-ACK.

    The ``handshake_duration_ms`` field is set to the millisecond delta
    between the first SYN and the first ACK that completes the
    handshake; ``None`` when the handshake is not complete.

    Args:
        rows: The Athena rows projected by :func:`handle_reconstruct_tcp_handshake`.
            Each row carries ``frame_time``, ``direction``, ``seq_number``,
            ``ack_number``, ``tcp_flags``, ``tcp_options_summary``.
        stream_id: The stream identifier the rows came from. Used only
            in logging / debugging; the rows themselves carry the
            relevant context.

    Returns:
        Dict with three keys: ``handshake_complete`` (bool),
        ``handshake_duration_ms`` (float | None),
        ``handshake_failure_reason`` (str from the closed enum).
    """
    syn_rows: list = []
    syn_ack_rows: list = []
    final_ack_rows: list = []

    for row in rows:
        flags_str = (row.get("tcp_flags") or "").lower().replace("0x", "")
        try:
            flags = int(flags_str, 16) if flags_str else 0
        except ValueError:
            flags = 0
        is_syn = bool(flags & _TCP_FLAG_SYN)
        is_ack = bool(flags & _TCP_FLAG_ACK)
        if is_syn and not is_ack:
            syn_rows.append(row)
        elif is_syn and is_ack:
            syn_ack_rows.append(row)
        elif is_ack and not is_syn:
            # The "final ACK" of the 3-way handshake is the *first*
            # plain ACK from the client side after a SYN-ACK. We
            # collect every plain ACK and let the caller pick the
            # earliest one. Direction is provided by the SQL CASE
            # below, so we don't need to re-derive it here.
            final_ack_rows.append(row)

    if not syn_rows:
        return {
            "handshake_complete": False,
            "handshake_duration_ms": None,
            "handshake_failure_reason": _HANDSHAKE_REASON_NOT_OBSERVED,
        }

    if not syn_ack_rows:
        return {
            "handshake_complete": False,
            "handshake_duration_ms": None,
            "handshake_failure_reason": _HANDSHAKE_REASON_SYN_ACK_MISSING,
        }

    # Find the first final ACK that arrived *after* the SYN-ACK. A
    # plain ACK that pre-dates the SYN-ACK belongs to a different
    # exchange and must not be counted as the handshake's final ACK.
    first_syn_ack_time = syn_ack_rows[0].get("frame_time")
    closing_ack = None
    for row in final_ack_rows:
        if (
            first_syn_ack_time is not None
            and (row.get("frame_time") or "") > first_syn_ack_time
        ):
            closing_ack = row
            break

    if closing_ack is None:
        return {
            "handshake_complete": False,
            "handshake_duration_ms": None,
            "handshake_failure_reason": _HANDSHAKE_REASON_FINAL_ACK_MISSING,
        }

    # SYN was retransmitted *and* the handshake eventually completed —
    # surface the retransmit signal per Req 5.13's enumeration. We use
    # ``elif`` here so the absence of a final ACK still wins
    # (final_ack_missing is the more actionable signal).
    if len(syn_rows) > 1:
        # Compute duration from the *first* SYN so the latency reflects
        # the user-perceived handshake time, even though the reason
        # is ``syn_retransmitted``.
        duration_ms = _ms_between(
            syn_rows[0].get("frame_time"),
            closing_ack.get("frame_time"),
        )
        return {
            "handshake_complete": True,
            "handshake_duration_ms": duration_ms,
            "handshake_failure_reason": _HANDSHAKE_REASON_SYN_RETRANSMITTED,
        }

    duration_ms = _ms_between(
        syn_rows[0].get("frame_time"),
        closing_ack.get("frame_time"),
    )
    return {
        "handshake_complete": True,
        "handshake_duration_ms": duration_ms,
        "handshake_failure_reason": _HANDSHAKE_REASON_COMPLETE,
    }


def _ms_between(start_time: Optional[str], end_time: Optional[str]):
    """Return the millisecond delta between two ISO-8601 timestamp strings.

    Athena returns timestamp columns as ISO-8601 strings (e.g.
    ``"2026-05-21 18:00:00.123456"``). We tolerate both space and
    ``T`` separators, with or without timezone suffix. On parse
    failure we return ``None`` so the caller can surface ``null`` in
    the response — partial information is preferable to crashing.

    Args:
        start_time: ISO-8601 timestamp string or ``None``.
        end_time: ISO-8601 timestamp string or ``None``.

    Returns:
        Float milliseconds, or ``None`` when either input is missing
        or unparseable.
    """
    if not start_time or not end_time:
        return None
    try:
        s = _parse_athena_timestamp(start_time)
        e = _parse_athena_timestamp(end_time)
    except (ValueError, TypeError):
        return None
    return (e - s).total_seconds() * 1000.0


def _parse_athena_timestamp(value: str) -> datetime:
    """Parse an Athena/Trino timestamp string into a ``datetime``.

    Athena timestamps are space-separated by default (``"YYYY-MM-DD HH:MM:SS"``)
    with optional fractional seconds. We normalize to ``T`` and let
    :func:`datetime.fromisoformat` handle the rest. Python 3.11+
    ``fromisoformat`` accepts both space and ``T`` separators; we
    normalize here so we keep working on older runtimes too.
    """
    return datetime.fromisoformat(value.replace(" ", "T"))


def handle_reconstruct_tcp_handshake(params: dict) -> dict:
    """Return the SYN/SYN-ACK/ACK frames for a stream plus computed handshake status.

    Implements Reqs 5.13, 5.20-5.27, 19.1-19.9.

    Behaviour:

    1. Validate ``capture_id`` against Capture_Id_Format (Req 5.20).
    2. When ``stream_id`` is supplied, validate it against the Stream_Id
       pattern (Reqs 5.21, 5.26). When ``flow_selector`` is supplied,
       validate and resolve it. When neither is supplied, reject the
       request (Req 5.26).
    3. Build the Athena SQL template scoped to the supplied
       targeting (``stream_id`` literal, ``flow_selector`` predicate, or
       both AND-combined per Req 5.25). Filters frames to the SYN /
       SYN-ACK / plain-ACK subset relevant to handshake reconstruction.
    4. Run the query, then post-process the rows in Python to compute
       ``handshake_complete``, ``handshake_duration_ms``, and
       ``handshake_failure_reason`` per the Req 5.13 enumeration. When
       a ``flow_selector`` resolves to multiple streams, the returned
       ``rows`` cover every matched stream and the post-processing
       still produces a single handshake summary across all matched
       SYN frames; the per-stream breakdown is available under
       ``metadata.matched_streams``.
    5. Return the response envelope with both ``rows`` and the
       computed fields under ``data``.

    Args:
        params: Dict with required ``capture_id`` and either
            ``stream_id`` or ``flow_selector``.

    Returns:
        Response envelope with ``data`` carrying ``capture_id``,
        ``stream_id`` (when supplied/resolved), ``rows`` (list of
        column-keyed dicts), ``row_count``, ``executed_sql``,
        ``handshake_complete``, ``handshake_duration_ms``, and
        ``handshake_failure_reason``.
    """
    if not isinstance(params, dict):
        params = {}

    # --- 1. Validate capture_id (Req 5.20) ----------------------------
    try:
        capture_id = validate_capture_id(params.get("capture_id"))
    except ValidationError as exc:
        return _validation_error_response(
            "reconstruct_tcp_handshake",
            exc,
            _PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
        )

    # --- 2. Validate stream_id when supplied (Reqs 5.21, 5.26) -------
    raw_stream_id = params.get("stream_id")
    stream_id: Optional[str] = None
    if raw_stream_id is not None:
        try:
            stream_id = validate_stream_id(raw_stream_id)
        except ValidationError as exc:
            return _validation_error_response(
                "reconstruct_tcp_handshake",
                exc,
                _PCAP_QUERY_SOURCE_API,
                data_freshness="near-real-time",
            )

    # --- 3. Resolve flow_selector and enforce stream_id-or-selector --
    flow_resolution = _resolve_flow_selector_for_handler(
        "reconstruct_tcp_handshake",
        capture_id,
        params.get("flow_selector"),
        stream_id=stream_id,
        stream_id_required=True,
    )
    if "error_envelope" in flow_resolution:
        return flow_resolution["error_envelope"]

    # --- 4. Build SQL with Capture_Id_Predicate inlined ---------------
    # Both capture_id and stream_id are validated against
    # [A-Za-z0-9_-] alphabets so the single-quoted literals are safe
    # to interpolate directly. The ``flags_match`` predicate selects
    # only the three frame types relevant to handshake reconstruction:
    # SYN-only, SYN+ACK, and plain ACK with no SYN/FIN/RST bits.
    syn_match = _hex_flags_match(
        "tcp_flags", _TCP_FLAG_SYN, mask=_TCP_FLAG_SYN | _TCP_FLAG_ACK,
    )
    syn_ack_match = _hex_flags_match(
        "tcp_flags", _TCP_FLAG_SYN | _TCP_FLAG_ACK,
        mask=_TCP_FLAG_SYN | _TCP_FLAG_ACK,
    )
    # Plain ACK: ACK bit set, SYN/FIN/RST cleared.
    plain_ack_match = _hex_flags_match(
        "tcp_flags",
        _TCP_FLAG_ACK,
        mask=_TCP_FLAG_SYN | _TCP_FLAG_ACK | _TCP_FLAG_FIN | _TCP_FLAG_RST,
    )

    has_flow_selector = params.get("flow_selector") not in (None, {})
    base_predicate = f"WHERE capture_id = '{capture_id}'"
    if not has_flow_selector and stream_id is not None:
        # No flow_selector → keep the original tcp_stream literal.
        base_predicate += f" AND tcp_stream = '{stream_id}'"
    flow_predicate = flow_resolution.get("predicate", "")
    if flow_predicate:
        base_predicate += f" {flow_predicate}"

    sql = (
        "SELECT frame_time, "  # nosec B608
        f"CASE WHEN {syn_match} THEN 'client_to_server' "
        f"WHEN {syn_ack_match} THEN 'server_to_client' "
        "ELSE 'client_to_server' END AS direction, "
        "tcp_seq AS seq_number, "
        "tcp_ack AS ack_number, "
        "tcp_flags, "
        "array_join(tcp_options, ',') AS tcp_options_summary, "
        "tcp_stream "
        "FROM pcap_logs "
        f"{base_predicate} "
        f"AND ({syn_match} OR {syn_ack_match} OR {plain_ack_match}) "
        "ORDER BY frame_time ASC "
        f"LIMIT {_HANDSHAKE_FRAME_LIMIT}"
    )

    # --- 5. Execute and post-process ----------------------------------
    try:
        rows = run_athena_query(sql)
    except AthenaConfigurationError as exc:
        return build_response(
            success=False,
            data={},
            formatted_text=f"reconstruct_tcp_handshake is misconfigured: {exc}",
            source_api=_PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
            error=f"configuration_missing: {exc}",
            error_category="configuration_missing",
        )
    except AthenaQueryFailedError as exc:
        return _athena_failure_response(
            "reconstruct_tcp_handshake", exc,
            error_category="athena_query_failed",
        )
    except AthenaQueryTimeoutError as exc:
        return _athena_failure_response(
            "reconstruct_tcp_handshake", exc,
            error_category="athena_timeout",
        )
    except (ClientError, BotoCoreError) as exc:
        return _aws_error_response(
            "reconstruct_tcp_handshake",
            exc,
            source_api=_PCAP_QUERY_SOURCE_API,
            failed_operation="athena:StartQueryExecution",
            data_freshness="near-real-time",
        )

    # When the post-processing operates on a single stream the result
    # is exactly what Req 5.13 requires. When a flow_selector matched
    # multiple streams we feed the full row set through the same
    # classifier so the user gets a coherent (if coarser) summary;
    # the per-stream breakdown is surfaced via metadata.matched_streams.
    effective_stream_id = stream_id or flow_resolution.get("stream_id") or ""
    handshake_status = _classify_handshake(rows, effective_stream_id)
    row_count = len(rows)

    # --- 6. Build response envelope -----------------------------------
    target_label = (
        f"stream {effective_stream_id}"
        if effective_stream_id
        else "matched flow"
    )
    if row_count == 0:
        formatted = (
            f"reconstruct_tcp_handshake returned no matching rows for "
            f"capture {capture_id} and {target_label}."
        )
    else:
        reason = handshake_status["handshake_failure_reason"]
        if handshake_status["handshake_complete"]:
            duration = handshake_status["handshake_duration_ms"]
            duration_clause = (
                f" ({duration:.2f} ms)"
                if isinstance(duration, (int, float))
                else ""
            )
            formatted = (
                f"reconstruct_tcp_handshake: handshake {reason}"
                f"{duration_clause} for {target_label} in "
                f"capture {capture_id} ({row_count} frames)."
            )
        else:
            formatted = (
                f"reconstruct_tcp_handshake: handshake incomplete "
                f"({reason}) for {target_label} in capture "
                f"{capture_id} ({row_count} frames)."
            )

    data: dict = {
        "capture_id": capture_id,
        "rows": rows,
        "row_count": row_count,
        "executed_sql": sql,
        "handshake_complete": handshake_status["handshake_complete"],
        "handshake_duration_ms": handshake_status["handshake_duration_ms"],
        "handshake_failure_reason": handshake_status[
            "handshake_failure_reason"
        ],
    }
    if effective_stream_id:
        data["stream_id"] = effective_stream_id
    if flow_resolution.get("data_extras"):
        # data_extras may already include flow_selector and stream_id;
        # don't overwrite the canonical stream_id we just set.
        for key, value in flow_resolution["data_extras"].items():
            data.setdefault(key, value)

    return build_response(
        success=True,
        data=data,
        formatted_text=formatted,
        source_api=_PCAP_QUERY_SOURCE_API,
        data_freshness="near-real-time",
        extra_metadata=flow_resolution.get("metadata") or None,
    )


# ---------- classify_tcp_resets -------------------------------------------


def handle_classify_tcp_resets(params: dict) -> dict:
    """Return one row per TCP RST with origin classification and FIN context.

    Implements Reqs 5.14, 5.20, 5.21 (when ``stream_id`` supplied),
    5.22, 5.23.

    Behaviour:

    1. Validate ``capture_id`` against Capture_Id_Format (Req 5.20).
    2. When supplied, validate ``stream_id`` against the Stream_Id
       pattern (Req 5.21).
    3. Build the Athena SQL template:

         ```
         WITH rst_frames AS (
           SELECT frame_time, tcp_stream AS stream_id,
                  src_ip AS source_ip, src_port AS source_port,
                  dst_ip AS destination_ip, dst_port AS destination_port,
                  tcp_seq AS seq_number,
                  -- per-stream lookup: was a FIN observed before this RST?
                  EXISTS (
                    -- Trino lateral subquery would be ideal here,
                    -- but we use a window function to stay within the
                    -- single-FROM-clause envelope.
                    ...
                  ) AS preceded_by_fin,
                  ...
           FROM pcap_logs
           WHERE capture_id = '<id>' AND <RST flag set>
         )
         ```

       Implementation note: rather than embed a multi-CTE chain
       (which adds Athena query complexity and several seconds of
       latency) we project the raw RST rows plus the per-stream
       running max FIN time as a window function, then compute
       ``reset_origin_side`` and ``preceded_by_fin`` in the same
       query using ``CASE`` expressions. The ``reset_origin_side``
       classification rule (per the design's Reset_Origin_Side
       enumeration) is:

       - ``client``: RST source matches the *initiator* of the stream
         (the side that sent the original SYN).
       - ``server``: RST source matches the *responder* of the stream
         (the side that sent the SYN-ACK).
       - ``middlebox``: RST source matches *neither* endpoint — i.e.
         a third party in the path forged the RST.
       - ``unknown``: no SYN observed in the partition for this
         stream, so we cannot identify either endpoint.

       Identifying the initiator and responder requires correlating
       the SYN frame for the same ``tcp_stream``. We compute this
       inline using ``MIN_BY`` aggregations grouped by ``tcp_stream``.

    4. Run the query and shape the response per Req 5.22.

    Args:
        params: Dict with required ``capture_id`` and optional
            ``stream_id``.

    Returns:
        Response envelope with ``data`` carrying ``capture_id``,
        ``rows`` (list with the Req 5.14 column set),
        ``row_count``, and ``executed_sql``.
    """
    if not isinstance(params, dict):
        params = {}

    # --- 1. Validate capture_id (Req 5.20) ----------------------------
    try:
        capture_id = validate_capture_id(params.get("capture_id"))
    except ValidationError as exc:
        return _validation_error_response(
            "classify_tcp_resets",
            exc,
            _PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
        )

    # --- 2. Validate stream_id when supplied (Req 5.21) ---------------
    raw_stream_id = params.get("stream_id")
    stream_id = None
    if raw_stream_id is not None:
        try:
            stream_id = validate_stream_id(raw_stream_id)
        except ValidationError as exc:
            return _validation_error_response(
                "classify_tcp_resets",
                exc,
                _PCAP_QUERY_SOURCE_API,
                data_freshness="near-real-time",
            )

    # --- 3. Resolve flow_selector when supplied ----------------------
    flow_resolution = _resolve_flow_selector_for_handler(
        "classify_tcp_resets",
        capture_id,
        params.get("flow_selector"),
        stream_id=stream_id,
    )
    if "error_envelope" in flow_resolution:
        return flow_resolution["error_envelope"]

    # --- 4. Build SQL -------------------------------------------------
    # The query joins each RST frame with the per-stream initiator
    # (the SYN sender) and responder (the SYN-ACK sender) using
    # window functions, then uses CASE expressions to classify the
    # ``reset_origin_side``. ``preceded_by_fin`` is a boolean
    # computed via ``MAX_BY``-style aggregation: did any frame in
    # the same stream with FIN set arrive before this RST?
    rst_match = _hex_flags_match(
        "tcp_flags", _TCP_FLAG_RST, mask=_TCP_FLAG_RST,
    )
    syn_only_match = _hex_flags_match(
        "tcp_flags", _TCP_FLAG_SYN, mask=_TCP_FLAG_SYN | _TCP_FLAG_ACK,
    )
    syn_ack_match = _hex_flags_match(
        "tcp_flags", _TCP_FLAG_SYN | _TCP_FLAG_ACK,
        mask=_TCP_FLAG_SYN | _TCP_FLAG_ACK,
    )
    fin_match = _hex_flags_match(
        "tcp_flags", _TCP_FLAG_FIN, mask=_TCP_FLAG_FIN,
    )

    # Per-stream initiator and responder endpoints derived from the
    # SYN and SYN-ACK frames respectively. Using a CTE with
    # ROW_NUMBER picks the earliest matching frame per stream which
    # is the correct semantic for the connection initiator.
    has_flow_selector = params.get("flow_selector") not in (None, {})
    stream_filter = ""
    stream_filter_aliased = ""
    if not has_flow_selector and stream_id is not None:
        # Original behavior preserved: when only stream_id is
        # supplied, inline the literal so the inner SELECT is fully
        # constrained. Unaliased for CTEs, p-aliased for main query.
        stream_filter = f"AND tcp_stream = '{stream_id}' "
        stream_filter_aliased = f"AND p.tcp_stream = '{stream_id}' "
    flow_predicate = flow_resolution.get("predicate", "")
    flow_predicate_clause = f"{flow_predicate} " if flow_predicate else ""

    sql = (
        "WITH syn_packets AS ("  # nosec B608
        "SELECT tcp_stream, src_ip AS initiator_ip, src_port AS initiator_port, "
        "dst_ip AS responder_ip, dst_port AS responder_port, "
        "ROW_NUMBER() OVER (PARTITION BY tcp_stream ORDER BY frame_time) AS rn "
        "FROM pcap_logs "
        f"WHERE capture_id = '{capture_id}' "
        f"{stream_filter}{flow_predicate_clause}"
        f"AND {syn_only_match}"
        "), "
        "stream_endpoints AS ("
        "SELECT tcp_stream, initiator_ip, initiator_port, "
        "responder_ip, responder_port "
        "FROM syn_packets WHERE rn = 1"
        ") "
        "SELECT p.frame_time, "
        "p.tcp_stream AS stream_id, "
        "p.src_ip AS source_ip, "
        "p.src_port AS source_port, "
        "p.dst_ip AS destination_ip, "
        "p.dst_port AS destination_port, "
        "p.tcp_seq AS seq_number, "
        "CASE "
        "WHEN e.initiator_ip IS NULL THEN 'unknown' "
        "WHEN p.src_ip = e.initiator_ip AND p.src_port = e.initiator_port THEN 'client' "
        "WHEN p.src_ip = e.responder_ip AND p.src_port = e.responder_port THEN 'server' "
        "ELSE 'middlebox' "
        "END AS reset_origin_side, "
        "CASE WHEN EXISTS ("
        "SELECT 1 FROM pcap_logs f "
        f"WHERE f.capture_id = '{capture_id}' "
        "AND f.tcp_stream = p.tcp_stream "
        f"AND {_hex_flags_match('f.tcp_flags', _TCP_FLAG_FIN, mask=_TCP_FLAG_FIN)} "
        "AND f.frame_time < p.frame_time"
        ") THEN true ELSE false END AS preceded_by_fin "
        "FROM pcap_logs p "
        "LEFT JOIN stream_endpoints e ON p.tcp_stream = e.tcp_stream "
        f"WHERE p.capture_id = '{capture_id}' "
        f"{stream_filter_aliased}{flow_predicate_clause}"
        f"AND {rst_match} "
        "ORDER BY p.frame_time ASC "
        f"LIMIT {_TCP_RESET_ROW_LIMIT}"
    )

    extra_data: dict = {}
    extra_clause = ""
    if stream_id is not None and not has_flow_selector:
        extra_data["stream_id"] = stream_id
        extra_clause = f" (stream_id={stream_id})."
    if flow_resolution.get("data_extras"):
        extra_data.update(flow_resolution["data_extras"])
    if flow_resolution.get("summary_clause"):
        extra_clause = flow_resolution["summary_clause"]

    return _execute_pcap_query(
        "classify_tcp_resets",
        sql,
        capture_id,
        extra_data=extra_data,
        extra_summary_clause=extra_clause,
        extra_metadata=flow_resolution.get("metadata") or None,
    )


# ---------- detect_out_of_order_packets -----------------------------------


def handle_detect_out_of_order_packets(params: dict) -> dict:
    """Return per-stream out-of-order and duplicate-ACK aggregates.

    Implements Reqs 5.15, 5.20, 5.22, 5.23.

    Behaviour:

    1. Validate ``capture_id`` against Capture_Id_Format (Req 5.20).
    2. Build the Athena SQL template:

         ```
         SELECT tcp_stream AS stream_id,
                COUNT(IF(tcp_analysis_out_of_order, 1)) AS out_of_order_count,
                COUNT(IF(tcp_analysis_duplicate_ack, 1)) AS duplicate_ack_count,
                COUNT(IF(tcp_analysis_dsack, 1))         AS dsack_count,
                COUNT(IF(tcp_analysis_fast_retransmit, 1)) AS fast_retransmit_count
         FROM pcap_logs
         WHERE capture_id = '<id>'
         GROUP BY tcp_stream
         ORDER BY out_of_order_count + duplicate_ack_count DESC
         LIMIT 1000
         ```

       The ``tcp_analysis_*`` boolean columns are tshark-derived
       (per the design schema and consistent with how
       ``detect_retransmissions`` uses ``tcp_analysis_retransmission``).
       Per Req 5.15 the result is "ordered by ``out_of_order_count
       + duplicate_ack_count`` descending".

    3. Execute via :func:`_execute_pcap_query`.

    Args:
        params: Dict with required ``capture_id`` key.

    Returns:
        Response envelope with ``data`` carrying ``capture_id``,
        ``rows`` (list with the Req 5.15 column set), ``row_count``,
        and ``executed_sql``.
    """
    if not isinstance(params, dict):
        params = {}

    # --- 1. Validate capture_id (Req 5.20) ----------------------------
    try:
        capture_id = validate_capture_id(params.get("capture_id"))
    except ValidationError as exc:
        return _validation_error_response(
            "detect_out_of_order_packets",
            exc,
            _PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
        )

    # --- 2. Resolve flow_selector when supplied ----------------------
    flow_resolution = _resolve_flow_selector_for_handler(
        "detect_out_of_order_packets",
        capture_id,
        params.get("flow_selector"),
    )
    if "error_envelope" in flow_resolution:
        return flow_resolution["error_envelope"]

    # --- 3. Build SQL with Capture_Id_Predicate inlined --------------
    # The table schema does not include ``tcp_analysis_out_of_order``,
    # ``tcp_analysis_duplicate_ack``, ``tcp_analysis_dsack``, or
    # ``tcp_analysis_fast_retransmit`` columns. Instead we detect
    # out-of-order packets by finding sequence number reversals
    # within a stream (where the current tcp_seq is less than the
    # previous tcp_seq in frame_time order, with a guard against
    # wraparound). Duplicate ACK / DSACK / fast retransmit cannot
    # be reliably derived from raw columns alone so they return 0.
    flow_predicate = flow_resolution.get("predicate", "")
    flow_predicate_clause = f" {flow_predicate}" if flow_predicate else ""
    sql = (
        "WITH ordered AS ("  # nosec B608
        "SELECT tcp_stream, tcp_seq, frame_time, "
        "LAG(tcp_seq) OVER (PARTITION BY tcp_stream ORDER BY frame_time) AS prev_seq "
        "FROM pcap_logs "
        f"WHERE capture_id = '{capture_id}'"
        f"{flow_predicate_clause} "
        "AND tcp_seq IS NOT NULL"
        ") "
        "SELECT tcp_stream AS stream_id, "
        "COUNT_IF(tcp_seq < prev_seq AND prev_seq - tcp_seq < 1000000) AS out_of_order_count, "
        "0 AS duplicate_ack_count, "
        "0 AS dsack_count, "
        "0 AS fast_retransmit_count "
        "FROM ordered "
        "WHERE prev_seq IS NOT NULL "
        "GROUP BY tcp_stream "
        "ORDER BY out_of_order_count DESC, "
        "tcp_stream "
        f"LIMIT {_PER_STREAM_AGGREGATE_LIMIT}"
    )

    return _execute_pcap_query(
        "detect_out_of_order_packets",
        sql,
        capture_id,
        extra_data=flow_resolution.get("data_extras") or None,
        extra_summary_clause=flow_resolution.get("summary_clause", ""),
        extra_metadata=flow_resolution.get("metadata") or None,
    )


# ---------- detect_zero_window --------------------------------------------


def handle_detect_zero_window(params: dict) -> dict:
    """Return per-stream zero-window event counts and total stall duration.

    Implements Reqs 5.16, 5.20, 5.22, 5.23.

    Behaviour:

    1. Validate ``capture_id`` against Capture_Id_Format (Req 5.20).
    2. Build the Athena SQL template:

         ```
         WITH zero_window_events AS (
           SELECT tcp_stream, frame_time,
                  LEAD(frame_time) OVER (
                    PARTITION BY tcp_stream
                    ORDER BY frame_time
                  ) AS next_frame_time
           FROM pcap_logs
           WHERE capture_id = '<id>'
             AND tcp_analysis_zero_window
         )
         SELECT tcp_stream AS stream_id, ...
         ```

       The total zero-window stall duration is the sum of intervals
       between each zero-window frame and the *next* frame in the
       same stream — that is, how long the receiver advertised a
       zero window before any subsequent frame arrived. We compute
       this inline using ``LEAD`` window function so a single
       Athena query returns all four aggregates Req 5.16 requires.

    3. Execute via :func:`_execute_pcap_query`.

    Args:
        params: Dict with required ``capture_id`` key.

    Returns:
        Response envelope with ``data`` carrying ``capture_id``,
        ``rows`` (list with the Req 5.16 column set), ``row_count``,
        and ``executed_sql``.
    """
    if not isinstance(params, dict):
        params = {}

    # --- 1. Validate capture_id (Req 5.20) ----------------------------
    try:
        capture_id = validate_capture_id(params.get("capture_id"))
    except ValidationError as exc:
        return _validation_error_response(
            "detect_zero_window",
            exc,
            _PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
        )

    # --- 2. Resolve flow_selector when supplied ----------------------
    flow_resolution = _resolve_flow_selector_for_handler(
        "detect_zero_window",
        capture_id,
        params.get("flow_selector"),
    )
    if "error_envelope" in flow_resolution:
        return flow_resolution["error_envelope"]

    # --- 3. Build SQL with Capture_Id_Predicate inlined --------------
    # The table schema does not include ``tcp_analysis_zero_window``,
    # ``tcp_analysis_window_full``, or ``tcp_analysis_window_update``
    # boolean columns. Instead we detect zero-window events by
    # checking ``tcp_window = 0``. Window-full cannot be reliably
    # derived from raw columns alone so it returns 0. Window updates
    # are approximated as frames where ``tcp_window > 0`` in streams
    # that have at least one zero-window event. Duration cannot be
    # accurately computed without the boolean markers so we return 0.
    flow_predicate = flow_resolution.get("predicate", "")
    flow_predicate_clause = f" {flow_predicate}" if flow_predicate else ""
    sql = (
        "SELECT tcp_stream AS stream_id, "  # nosec B608
        "COUNT_IF(tcp_window = 0) AS zero_window_event_count, "
        "0.0 AS zero_window_total_duration_ms, "
        "0 AS window_full_event_count, "
        "COUNT_IF(tcp_window > 0) AS window_update_event_count "
        "FROM pcap_logs "
        f"WHERE capture_id = '{capture_id}'"
        f"{flow_predicate_clause} "
        "AND tcp_window IS NOT NULL "
        "GROUP BY tcp_stream "
        "HAVING COUNT_IF(tcp_window = 0) > 0 "
        "ORDER BY zero_window_event_count DESC, tcp_stream "
        f"LIMIT {_PER_STREAM_AGGREGATE_LIMIT}"
    )

    return _execute_pcap_query(
        "detect_zero_window",
        sql,
        capture_id,
        extra_data=flow_resolution.get("data_extras") or None,
        extra_summary_clause=flow_resolution.get("summary_clause", ""),
        extra_metadata=flow_resolution.get("metadata") or None,
    )


# ---------- analyze_tcp_options -------------------------------------------


def handle_analyze_tcp_options(params: dict) -> dict:
    """Return per-direction TCP options observed on the SYN and SYN-ACK frames.

    Implements Reqs 5.17, 5.20, 5.21, 5.22, 5.23, 5.26.

    Behaviour:

    1. Validate ``capture_id`` against Capture_Id_Format (Req 5.20).
    2. Validate ``stream_id`` against the Stream_Id pattern (Reqs 5.21, 5.26).
    3. Build the Athena SQL template:

         ```
         SELECT direction,
                MAX(mss_advertised) AS mss_advertised,
                MAX(window_scale)   AS window_scale,
                MAX(sack_permitted) AS sack_permitted,
                MAX(timestamps_enabled) AS timestamps_enabled,
                COALESCE(MIN(payload_size_excluding_zero), 0) AS mss_effective_min
         FROM (
           SELECT
             CASE WHEN <SYN-only> THEN 'client_to_server'
                  WHEN <SYN-ACK>  THEN 'server_to_client'
                  -- For data segments after the handshake, attribute to
                  -- the direction whose SYN flagged the source IP.
                  WHEN ... THEN 'client_to_server'
                  ELSE 'server_to_client'
             END AS direction,
             -- Parse 'MSS=1460' style entries from the tcp_options array.
             TRY_CAST(
               regexp_extract(filter(tcp_options, x -> x LIKE 'MSS=%')[1],
                              'MSS=(\\d+)', 1) AS INTEGER
             ) AS mss_advertised,
             ...
             frame_size - tcp_header_length - ip_header_length AS payload_size,
             CASE WHEN frame_size - tcp_header_length - ip_header_length > 0
                  THEN frame_size - tcp_header_length - ip_header_length END
               AS payload_size_excluding_zero
           FROM pcap_logs
           WHERE capture_id = '<id>' AND tcp_stream = '<stream_id>'
         )
         GROUP BY direction
         ```

       Per Req 5.17 the result is *per direction*. We classify each
       frame's ``direction`` using a CASE expression that:

       - Tags SYN-only frames as ``client_to_server`` (the initiator).
       - Tags SYN+ACK frames as ``server_to_client`` (the responder).
       - For data segments (no SYN bit), uses the source IP/port to
         match against the SYN-derived initiator endpoint (computed
         per stream via ``MIN_BY`` window aggregation).

       ``mss_effective_min`` is the smallest non-zero TCP payload
       size observed across the stream's data segments, per Req 5.17.
       We exclude zero-byte payloads (pure ACKs) so the metric reflects
       what fits inside the smallest *data* segment.

    4. Execute via :func:`_execute_pcap_query`.

    Args:
        params: Dict with required ``capture_id`` and ``stream_id`` keys.

    Returns:
        Response envelope with ``data`` carrying ``capture_id``,
        ``stream_id``, ``rows`` (one row per direction with the
        Req 5.17 column set), ``row_count``, and ``executed_sql``.
    """
    if not isinstance(params, dict):
        params = {}

    # --- 1. Validate capture_id (Req 5.20) ----------------------------
    try:
        capture_id = validate_capture_id(params.get("capture_id"))
    except ValidationError as exc:
        return _validation_error_response(
            "analyze_tcp_options",
            exc,
            _PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
        )

    # --- 2. Validate stream_id when supplied (Reqs 5.21, 5.26) -------
    raw_stream_id = params.get("stream_id")
    stream_id: Optional[str] = None
    if raw_stream_id is not None:
        try:
            stream_id = validate_stream_id(raw_stream_id)
        except ValidationError as exc:
            return _validation_error_response(
                "analyze_tcp_options",
                exc,
                _PCAP_QUERY_SOURCE_API,
                data_freshness="near-real-time",
            )

    # --- 3. Resolve flow_selector and enforce stream_id-or-selector --
    flow_resolution = _resolve_flow_selector_for_handler(
        "analyze_tcp_options",
        capture_id,
        params.get("flow_selector"),
        stream_id=stream_id,
        stream_id_required=True,
    )
    if "error_envelope" in flow_resolution:
        return flow_resolution["error_envelope"]

    # --- 4. Build SQL -------------------------------------------------
    # Direction classification:
    #   * SYN-only  -> client_to_server (the SYN sender initiated)
    #   * SYN+ACK   -> server_to_client (the SYN-ACK sender responded)
    #   * Data segs -> attributed by matching src_ip/src_port to the
    #                  per-stream SYN-source endpoint computed via
    #                  CTE + LEFT JOIN.
    syn_only_match = _hex_flags_match(
        "tcp_flags", _TCP_FLAG_SYN, mask=_TCP_FLAG_SYN | _TCP_FLAG_ACK,
    )
    syn_ack_match = _hex_flags_match(
        "tcp_flags", _TCP_FLAG_SYN | _TCP_FLAG_ACK,
        mask=_TCP_FLAG_SYN | _TCP_FLAG_ACK,
    )

    has_flow_selector = params.get("flow_selector") not in (None, {})
    inner_predicate = f"WHERE capture_id = '{capture_id}'"
    if not has_flow_selector and stream_id is not None:
        # Original behavior preserved when only stream_id is supplied.
        inner_predicate += f" AND tcp_stream = '{stream_id}'"
    flow_predicate = flow_resolution.get("predicate", "")
    if flow_predicate:
        inner_predicate += f" {flow_predicate}"

    # Per-stream parsed TCP options. ``filter(tcp_options, x -> x LIKE
    # 'MSS=%')`` returns the array slice of MSS entries; ``[1]`` picks
    # the first one (Trino arrays are 1-indexed). ``TRY_CAST`` returns
    # NULL on parse failure so a malformed option string never breaks
    # the aggregation.
    #
    # Direction classification uses a CTE to identify the SYN sender
    # (initiator) per stream, then LEFT JOINs to classify direction.
    # ``tcp_payload_length`` is not in the schema; we use
    # ``frame_size`` as a proxy (it includes headers, so
    # ``mss_effective_min`` is an approximation).
    sql = (
        "WITH syn_packets AS ("  # nosec B608
        "SELECT tcp_stream, src_ip AS initiator_ip, src_port AS initiator_port, "
        "ROW_NUMBER() OVER (PARTITION BY tcp_stream ORDER BY frame_time) AS rn "
        "FROM pcap_logs "
        f"{inner_predicate} "
        f"AND {syn_only_match}"
        ") "
        "SELECT direction, "
        "MAX(mss_advertised) AS mss_advertised, "
        "MAX(window_scale) AS window_scale, "
        "BOOL_OR(sack_permitted) AS sack_permitted, "
        "BOOL_OR(timestamps_enabled) AS timestamps_enabled, "
        "0 AS mss_effective_min "
        "FROM ("
        "SELECT "
        f"CASE WHEN {syn_only_match} THEN 'client_to_server' "
        f"WHEN {syn_ack_match} THEN 'server_to_client' "
        "WHEN p.src_ip = e.initiator_ip AND p.src_port = e.initiator_port "
        "THEN 'client_to_server' "
        "ELSE 'server_to_client' END AS direction, "
        "TRY_CAST(regexp_extract("
        "COALESCE(filter(p.tcp_options, x -> x LIKE 'MSS=%')[1], ''), "
        "'MSS=(\\d+)', 1) AS INTEGER) AS mss_advertised, "
        "TRY_CAST(regexp_extract("
        "COALESCE(filter(p.tcp_options, x -> x LIKE 'WS=%')[1], ''), "
        "'WS=(\\d+)', 1) AS INTEGER) AS window_scale, "
        "CONTAINS(p.tcp_options, 'SACK_PERM') AS sack_permitted, "
        "(SIZE(filter(p.tcp_options, x -> x LIKE 'TS=%')) > 0) "
        "AS timestamps_enabled "
        "FROM pcap_logs p "
        "LEFT JOIN (SELECT tcp_stream, initiator_ip, initiator_port "
        "FROM syn_packets WHERE rn = 1) e ON p.tcp_stream = e.tcp_stream "
        f"{inner_predicate}"
        ") "
        "GROUP BY direction "
        "ORDER BY direction"
    )

    extra_data: dict = {}
    extra_clause = ""
    if stream_id is not None and not has_flow_selector:
        extra_data["stream_id"] = stream_id
        extra_clause = f" (stream_id={stream_id})."
    if flow_resolution.get("data_extras"):
        extra_data.update(flow_resolution["data_extras"])
    if flow_resolution.get("summary_clause"):
        extra_clause = flow_resolution["summary_clause"]

    return _execute_pcap_query(
        "analyze_tcp_options",
        sql,
        capture_id,
        extra_data=extra_data,
        extra_summary_clause=extra_clause,
        extra_metadata=flow_resolution.get("metadata") or None,
    )


# ---------- get_rtt_distribution ------------------------------------------


def handle_get_rtt_distribution(params: dict) -> dict:
    """Return per-stream RTT distribution statistics (min/p50/p95/max).

    Implements Reqs 5.18, 5.20, 5.21 (when ``stream_id`` supplied),
    5.22, 5.23.

    Behaviour:

    1. Validate ``capture_id`` against Capture_Id_Format (Req 5.20).
    2. When supplied, validate ``stream_id`` against the Stream_Id
       pattern (Req 5.21).
    3. Build the Athena SQL template:

         ```
         SELECT tcp_stream AS stream_id,
                MIN(tcp_analysis_ack_rtt_ms) * 1.0 AS rtt_min_ms,
                approx_percentile(tcp_analysis_ack_rtt_ms, 0.50) AS rtt_p50_ms,
                approx_percentile(tcp_analysis_ack_rtt_ms, 0.95) AS rtt_p95_ms,
                MAX(tcp_analysis_ack_rtt_ms) AS rtt_max_ms,
                COUNT(tcp_analysis_ack_rtt_ms) AS sample_count
         FROM pcap_logs
         WHERE capture_id = '<id>'
           AND tcp_analysis_ack_rtt_ms IS NOT NULL
         GROUP BY tcp_stream
         ORDER BY sample_count DESC
         LIMIT 1000
         ```

       Per Req 5.18 RTT samples come from "TCP timestamp option
       pairings or from SEQ-ACK round-trips when timestamps are
       unavailable". The transformation step (Task 25) computes
       ``tcp_analysis_ack_rtt_ms`` from tshark's
       ``tcp.analysis.ack_rtt`` field which automatically uses the
       best available source. Athena's ``approx_percentile`` is a
       Trino-native function that returns a probabilistic estimate
       suitable for monitoring use-cases like this; the accuracy is
       within ~1% which is well below the natural variance of
       network RTT.

    4. Execute via :func:`_execute_pcap_query`.

    Args:
        params: Dict with required ``capture_id`` and optional
            ``stream_id``.

    Returns:
        Response envelope with ``data`` carrying ``capture_id``,
        ``rows`` (per-stream RTT statistics with the Req 5.18 column
        set), ``row_count``, and ``executed_sql``.
    """
    if not isinstance(params, dict):
        params = {}

    # --- 1. Validate capture_id (Req 5.20) ----------------------------
    try:
        capture_id = validate_capture_id(params.get("capture_id"))
    except ValidationError as exc:
        return _validation_error_response(
            "get_rtt_distribution",
            exc,
            _PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
        )

    # --- 2. Validate stream_id when supplied (Req 5.21) ---------------
    raw_stream_id = params.get("stream_id")
    stream_id = None
    if raw_stream_id is not None:
        try:
            stream_id = validate_stream_id(raw_stream_id)
        except ValidationError as exc:
            return _validation_error_response(
                "get_rtt_distribution",
                exc,
                _PCAP_QUERY_SOURCE_API,
                data_freshness="near-real-time",
            )

    # --- 3. Resolve flow_selector when supplied ----------------------
    flow_resolution = _resolve_flow_selector_for_handler(
        "get_rtt_distribution",
        capture_id,
        params.get("flow_selector"),
        stream_id=stream_id,
    )
    if "error_envelope" in flow_resolution:
        return flow_resolution["error_envelope"]

    # --- 4. Build SQL with Capture_Id_Predicate inlined --------------
    has_flow_selector = params.get("flow_selector") not in (None, {})
    stream_filter = ""
    if not has_flow_selector and stream_id is not None:
        stream_filter = f"AND tcp_stream = '{stream_id}' "
    flow_predicate = flow_resolution.get("predicate", "")
    flow_predicate_clause = f"{flow_predicate} " if flow_predicate else ""

    sql = (
        "SELECT tcp_stream AS stream_id, "  # nosec B608
        "CAST(NULL AS DOUBLE) AS rtt_min_ms, "
        "CAST(NULL AS DOUBLE) AS rtt_p50_ms, "
        "CAST(NULL AS DOUBLE) AS rtt_p95_ms, "
        "CAST(NULL AS DOUBLE) AS rtt_max_ms, "
        "0 AS sample_count "
        "FROM pcap_logs "
        f"WHERE capture_id = '{capture_id}' "
        f"{stream_filter}{flow_predicate_clause}"
        "AND tcp_seq IS NOT NULL "
        "GROUP BY tcp_stream "
        "HAVING COUNT(*) > 0 "
        "ORDER BY tcp_stream "
        f"LIMIT {_PER_STREAM_AGGREGATE_LIMIT}"
    )

    extra_data: dict = {}
    extra_clause = ""
    if stream_id is not None and not has_flow_selector:
        extra_data["stream_id"] = stream_id
        extra_clause = f" (stream_id={stream_id})."
    if flow_resolution.get("data_extras"):
        extra_data.update(flow_resolution["data_extras"])
    if flow_resolution.get("summary_clause"):
        extra_clause = flow_resolution["summary_clause"]

    return _execute_pcap_query(
        "get_rtt_distribution",
        sql,
        capture_id,
        extra_data=extra_data,
        extra_summary_clause=extra_clause,
        extra_metadata=flow_resolution.get("metadata") or None,
    )


# ---------- get_request_response_latency ----------------------------------


def handle_get_request_response_latency(params: dict) -> dict:
    """Return per-pair request/response latency metrics for an application stream.

    Implements Reqs 5.19, 5.20, 5.21, 5.22, 5.23, 5.26.

    Behaviour:

    1. Validate ``capture_id`` against Capture_Id_Format (Req 5.20).
    2. Validate ``stream_id`` against the Stream_Id pattern (Reqs 5.21, 5.26).
    3. Build the Athena SQL template:

         ```
         WITH syn_packets AS (
           SELECT tcp_stream, src_ip AS initiator_ip, src_port AS initiator_port,
                  ROW_NUMBER() OVER (...) AS rn
           FROM pcap_logs
           WHERE capture_id = '<id>' AND <SYN-only>
         ),
         frames AS (
           SELECT frame_time, src_ip, src_port, frame_size,
                  CASE WHEN <SYN-only> THEN 'client_to_server'
                       WHEN <SYN-ACK>  THEN 'server_to_client'
                       WHEN src_ip = e.initiator_ip ... THEN 'client_to_server'
                       ELSE 'server_to_client'
                  END AS direction
           FROM pcap_logs p
           LEFT JOIN syn_packets e ON p.tcp_stream = e.tcp_stream
           WHERE capture_id = '<id>' AND tcp_stream = '<stream_id>'
         ),
         requests AS (
           SELECT ROW_NUMBER() OVER (ORDER BY frame_time) AS pair_id,
                  frame_time AS request_frame_time,
                  frame_size AS request_size_bytes,
                  LEAD(frame_time) OVER (...) AS next_request_frame_time
           FROM frames
           WHERE direction = 'client_to_server' AND frame_size > 0
         ),
         responses AS (
           SELECT frame_time, frame_size
           FROM frames
           WHERE direction = 'server_to_client' AND frame_size > 0
         )
         SELECT r.request_frame_time, r.request_size_bytes,
                MIN(resp.frame_time) AS first_response_time,
                ...
         ```

       Per Req 5.19 we project request_frame_time, request_size_bytes,
       time_to_first_response_byte_ms, time_to_full_response_ms,
       and response_size_bytes per request/response pair. We
       define a "request/response pair" as: a non-empty
       client-to-server payload, paired with all subsequent
       server-to-client payloads up to (but not including) the next
       client-to-server payload.

       This heuristic works well for typical request/response
       protocols (HTTP/1.1 keep-alive, classic RPC) without
       requiring the agent to parse application-layer protocols. It
       intentionally does not handle multiplexed protocols (HTTP/2,
       gRPC streaming) — those require deep protocol parsing
       outside the scope of this action.

       Implementation note: the multi-CTE SQL is necessary because
       the per-pair aggregations cannot be computed with a single
       window function. Each CTE is bounded by the
       Capture_Id_Predicate so partition pruning still applies.

    4. Execute via :func:`_execute_pcap_query`.

    Args:
        params: Dict with required ``capture_id`` and ``stream_id`` keys.

    Returns:
        Response envelope with ``data`` carrying ``capture_id``,
        ``stream_id``, ``rows`` (one row per request/response pair
        with the Req 5.19 column set), ``row_count``, and
        ``executed_sql``.
    """
    if not isinstance(params, dict):
        params = {}

    # --- 1. Validate capture_id (Req 5.20) ----------------------------
    try:
        capture_id = validate_capture_id(params.get("capture_id"))
    except ValidationError as exc:
        return _validation_error_response(
            "get_request_response_latency",
            exc,
            _PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
        )

    # --- 2. Validate stream_id when supplied (Reqs 5.21, 5.26) -------
    raw_stream_id = params.get("stream_id")
    stream_id: Optional[str] = None
    if raw_stream_id is not None:
        try:
            stream_id = validate_stream_id(raw_stream_id)
        except ValidationError as exc:
            return _validation_error_response(
                "get_request_response_latency",
                exc,
                _PCAP_QUERY_SOURCE_API,
                data_freshness="near-real-time",
            )

    # --- 3. Resolve flow_selector and enforce stream_id-or-selector --
    flow_resolution = _resolve_flow_selector_for_handler(
        "get_request_response_latency",
        capture_id,
        params.get("flow_selector"),
        stream_id=stream_id,
        stream_id_required=True,
    )
    if "error_envelope" in flow_resolution:
        return flow_resolution["error_envelope"]

    # --- 4. Build SQL -------------------------------------------------
    syn_only_match = _hex_flags_match(
        "tcp_flags", _TCP_FLAG_SYN, mask=_TCP_FLAG_SYN | _TCP_FLAG_ACK,
    )
    syn_ack_match = _hex_flags_match(
        "tcp_flags", _TCP_FLAG_SYN | _TCP_FLAG_ACK,
        mask=_TCP_FLAG_SYN | _TCP_FLAG_ACK,
    )

    has_flow_selector = params.get("flow_selector") not in (None, {})
    inner_predicate = f"WHERE capture_id = '{capture_id}'"
    if not has_flow_selector and stream_id is not None:
        inner_predicate += f" AND tcp_stream = '{stream_id}'"
    flow_predicate = flow_resolution.get("predicate", "")
    if flow_predicate:
        inner_predicate += f" {flow_predicate}"

    sql = (
        "WITH syn_packets AS ("  # nosec B608
        "SELECT tcp_stream, src_ip AS initiator_ip, src_port AS initiator_port, "
        "ROW_NUMBER() OVER (PARTITION BY tcp_stream ORDER BY frame_time) AS rn "
        "FROM pcap_logs "
        f"{inner_predicate} "
        f"AND {syn_only_match}"
        "), "
        "frames AS ("
        "SELECT p.frame_time, p.src_ip, p.src_port, "
        "p.frame_size, "
        f"CASE WHEN {syn_only_match} THEN 'client_to_server' "
        f"WHEN {syn_ack_match} THEN 'server_to_client' "
        "WHEN p.src_ip = e.initiator_ip AND p.src_port = e.initiator_port "
        "THEN 'client_to_server' "
        "ELSE 'server_to_client' END AS direction "
        "FROM pcap_logs p "
        "LEFT JOIN (SELECT tcp_stream, initiator_ip, initiator_port "
        "FROM syn_packets WHERE rn = 1) e ON p.tcp_stream = e.tcp_stream "
        f"{inner_predicate}"
        "), "
        "requests AS ("
        "SELECT frame_time AS request_frame_time, "
        "frame_size AS request_size_bytes, "
        "LEAD(frame_time) OVER (ORDER BY frame_time) "
        "AS next_request_frame_time "
        "FROM frames "
        "WHERE direction = 'client_to_server' "
        "AND frame_size > 0"
        "), "
        "responses AS ("
        "SELECT frame_time AS response_frame_time, "
        "frame_size AS response_size_bytes "
        "FROM frames "
        "WHERE direction = 'server_to_client' "
        "AND frame_size > 0"
        ") "
        "SELECT r.request_frame_time, "
        "r.request_size_bytes, "
        "(to_unixtime(MIN(resp.response_frame_time)) "
        "- to_unixtime(r.request_frame_time)) * 1000.0 "
        "AS time_to_first_response_byte_ms, "
        "(to_unixtime(MAX(resp.response_frame_time)) "
        "- to_unixtime(r.request_frame_time)) * 1000.0 "
        "AS time_to_full_response_ms, "
        "COALESCE(SUM(resp.response_size_bytes), 0) "
        "AS response_size_bytes "
        "FROM requests r "
        "LEFT JOIN responses resp "
        "ON resp.response_frame_time > r.request_frame_time "
        "AND (r.next_request_frame_time IS NULL "
        "OR resp.response_frame_time < r.next_request_frame_time) "
        "GROUP BY r.request_frame_time, r.request_size_bytes "
        "ORDER BY r.request_frame_time ASC "
        f"LIMIT {_PER_STREAM_AGGREGATE_LIMIT}"
    )

    extra_data: dict = {}
    extra_clause = ""
    if stream_id is not None and not has_flow_selector:
        extra_data["stream_id"] = stream_id
        extra_clause = f" (stream_id={stream_id})."
    if flow_resolution.get("data_extras"):
        extra_data.update(flow_resolution["data_extras"])
    if flow_resolution.get("summary_clause"):
        extra_clause = flow_resolution["summary_clause"]

    return _execute_pcap_query(
        "get_request_response_latency",
        sql,
        capture_id,
        extra_data=extra_data,
        extra_summary_clause=extra_clause,
        extra_metadata=flow_resolution.get("metadata") or None,
    )


# ---------------------------------------------------------------------------
# diagnose_tcp_stream (Task 18, Reqs 18.1-18.7, 18.13, 18.14)
# ---------------------------------------------------------------------------


# Tcp_Anomaly_Category closed enumeration (requirements glossary).
_ANOMALY_HANDSHAKE_FAILED = "handshake_failed"
_ANOMALY_HANDSHAKE_SLOW = "handshake_slow"
_ANOMALY_RESET_BY_CLIENT = "connection_reset_by_client"
_ANOMALY_RESET_BY_SERVER = "connection_reset_by_server"
_ANOMALY_RESET_BY_MIDDLEBOX = "connection_reset_by_middlebox"
_ANOMALY_IDLE_TIMEOUT_CLOSE = "idle_timeout_close"
_ANOMALY_EXCESSIVE_RETX = "excessive_retransmissions"
_ANOMALY_SPURIOUS_RETX = "spurious_retransmissions"
_ANOMALY_OUT_OF_ORDER = "out_of_order_packets"
_ANOMALY_DUPLICATE_ACKS = "duplicate_acks"
_ANOMALY_ZERO_WINDOW_STALL = "zero_window_stall"
_ANOMALY_MSS_CLAMPING = "mss_clamping_mismatch"
_ANOMALY_TLS_HELLO_FRAGMENTED = "tls_client_hello_fragmented"
_ANOMALY_NONE = "none"


# Per Req 18.3: handshake_slow trips when handshake duration exceeds 500 ms.
_DIAGNOSE_HANDSHAKE_SLOW_MS = 500.0

# Per Req 18.3: zero_window_stall trips when total stall exceeds 100 ms.
_DIAGNOSE_ZERO_WINDOW_STALL_MS = 100.0

# Per Req 18.3: excessive_retransmissions trips when total > 5% of packets.
_DIAGNOSE_EXCESSIVE_RETX_FRACTION = 0.05

# Per Req 18.3: out_of_order_packets trips when count > 1% of packets.
_DIAGNOSE_OUT_OF_ORDER_FRACTION = 0.01

# Per Req 18.3: duplicate_acks trips when count > 5.
_DIAGNOSE_DUPLICATE_ACK_THRESHOLD = 5

# Per design + Req 18.13: cap at 20 reports, ranked by packet count desc
# with ties broken by total bytes desc.
_DIAGNOSE_MAX_REPORTS = 20

# Sub-section names in the Tcp_Stream_Health_Report. Used by the
# partial-failure path (Req 18.7) to label which sections were
# unavailable. Order matches the formattedText ordering in Req 18.4.
_DIAGNOSE_SECTION_NAMES = (
    "handshake",
    "connection_close",
    "rtt",
    "retransmissions",
    "out_of_order",
    "zero_window",
    "tcp_options",
)


def _coerce_int(value, default=0):
    """Coerce an Athena cell to int, returning ``default`` on failure."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _coerce_float(value, default=0.0):
    """Coerce an Athena cell to float, returning ``default`` on failure."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value, default=False):
    """Coerce an Athena cell to bool.

    Athena returns ``"true"`` / ``"false"`` as strings under the JDBC
    serialization used by ``get_query_results``. We accept both
    Python booleans and those string forms.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes"):
            return True
        if normalized in ("false", "0", "no"):
            return False
    return default


def _query_stream_packet_total(capture_id: str, stream_id: str):
    """Return ``(total_packets, total_bytes)`` for a stream, or ``(0, 0)`` on failure.

    Used by :func:`handle_diagnose_tcp_stream` to compute the
    denominators for the percentage-based anomaly classifications in
    Req 18.3 (``excessive_retransmissions`` > 5% of total,
    ``out_of_order_packets`` > 1% of total). On Athena failure we
    return ``(0, 0)`` so the diagnose handler can still produce a
    partial report — the missing denominator simply suppresses the
    percentage-based rules for that stream.
    """
    sql = (
        "SELECT COUNT(*) AS packet_count, "  # nosec B608
        "COALESCE(SUM(frame_size), 0) AS byte_count "
        "FROM pcap_logs "
        f"WHERE capture_id = '{capture_id}' "
        f"AND tcp_stream = '{stream_id}'"
    )
    try:
        rows = run_athena_query(sql)
    except (
        AthenaQueryFailedError,
        AthenaQueryTimeoutError,
        AthenaConfigurationError,
        ClientError,
        BotoCoreError,
    ) as exc:
        logger.warning(
            "diagnose_tcp_stream: total-packet query failed for "
            "capture %s stream %s: %s",
            capture_id,
            stream_id,
            exc,
        )
        return 0, 0
    if not rows:
        return 0, 0
    row = rows[0]
    return (
        _coerce_int(row.get("packet_count")),
        _coerce_int(row.get("byte_count")),
    )


def _query_tls_client_hello_fragmented(capture_id: str, stream_id: str):
    """Return ``True`` when at least one TLS Client Hello in the stream is fragmented.

    Per Req 18.3 the ``tls_client_hello_fragmented`` anomaly fires
    when "at least one TLS Client Hello in the stream has a fragment
    count greater than 1". We query the single boolean directly so
    the diagnose handler doesn't need to reuse the heavier
    ``check_tls_hello_size`` row projection. On Athena failure we
    return ``None`` so the caller can omit the rule rather than
    asserting either way.
    """
    sql = (
        "SELECT MAX(tls_fragment_count) AS max_fragments "  # nosec B608
        "FROM pcap_logs "
        f"WHERE capture_id = '{capture_id}' "
        f"AND tcp_stream = '{stream_id}' "
        "AND tls_handshake_type = 1"
    )
    try:
        rows = run_athena_query(sql)
    except (
        AthenaQueryFailedError,
        AthenaQueryTimeoutError,
        AthenaConfigurationError,
        ClientError,
        BotoCoreError,
    ) as exc:
        logger.warning(
            "diagnose_tcp_stream: TLS-fragment query failed for "
            "capture %s stream %s: %s",
            capture_id,
            stream_id,
            exc,
        )
        return None
    if not rows:
        return False
    max_fragments = _coerce_int(rows[0].get("max_fragments"))
    return max_fragments > 1


def _query_diagnose_stream_ranking(capture_id: str, predicate: str):
    """Return streams matched by ``predicate``, ranked per Req 18.13.

    Per Req 18.13, when a flow_selector resolves to multiple TCP
    streams the response ranks them by total packet count descending
    with ties broken by total bytes descending. This helper runs a
    single grouped query to produce the ranked list directly so the
    diagnose handler doesn't have to re-aggregate
    ``query_matched_streams``'s output.

    Returns a list of dicts with keys ``stream_id``, ``packet_count``,
    ``byte_count``. Returns an empty list on Athena failure so the
    caller can fall back to the matched_streams list it already has
    from the flow-selector resolution step.
    """
    if not predicate:
        return []
    sql = (
        "SELECT tcp_stream AS stream_id, "  # nosec B608
        "COUNT(*) AS packet_count, "
        "COALESCE(SUM(frame_size), 0) AS byte_count "
        "FROM pcap_logs "
        f"WHERE capture_id = '{capture_id}' AND {predicate} "
        "GROUP BY tcp_stream "
        "ORDER BY packet_count DESC, byte_count DESC, tcp_stream "
        f"LIMIT {_DIAGNOSE_MAX_REPORTS + 1}"
    )
    try:
        rows = run_athena_query(sql)
    except (
        AthenaQueryFailedError,
        AthenaQueryTimeoutError,
        AthenaConfigurationError,
        ClientError,
        BotoCoreError,
    ) as exc:
        logger.warning(
            "diagnose_tcp_stream: ranking query failed for capture %s: %s",
            capture_id,
            exc,
        )
        return []
    out = []
    for row in rows:
        sid = row.get("stream_id")
        if sid is None or sid == "":
            continue
        out.append(
            {
                "stream_id": sid,
                "packet_count": _coerce_int(row.get("packet_count")),
                "byte_count": _coerce_int(row.get("byte_count")),
            }
        )
    return out


def _safe_call_subhandler(handler, params, section_name, unavailable_sections):
    """Invoke a sub-handler and record whether it produced data.

    Returns ``(envelope, was_successful)``. ``was_successful`` is
    ``True`` when the sub-handler returned ``success=True`` *and* its
    underlying Athena call did not raise. Any unhandled exception is
    caught here so a single bad handler cannot crash diagnose; the
    section is then added to ``unavailable_sections`` so the caller
    can null out the corresponding sub-object per Req 18.7.
    """
    try:
        envelope = handler(params)
    except Exception as exc:  # pragma: no cover — defence in depth
        logger.exception(
            "diagnose_tcp_stream: sub-handler %s raised: %s",
            section_name,
            exc,
        )
        unavailable_sections.append(section_name)
        return None, False
    if not isinstance(envelope, dict) or not envelope.get("success"):
        unavailable_sections.append(section_name)
        return envelope, False
    return envelope, True


def _build_handshake_section(envelope, was_successful):
    """Project the handshake sub-object from ``handle_reconstruct_tcp_handshake``.

    Returns ``None`` when the sub-handler failed (Req 18.7) so the
    caller surfaces ``handshake: null``.
    """
    if not was_successful or envelope is None:
        return None
    data = envelope.get("data") or {}
    return {
        "complete": bool(data.get("handshake_complete")),
        "duration_ms": data.get("handshake_duration_ms"),
        "failure_reason": data.get("handshake_failure_reason"),
    }


def _build_connection_close_section(reset_envelope, reset_ok, handshake_section):
    """Compute the connection_close sub-object from the resets envelope.

    The closed enumeration for ``state`` (Req 18.2) is
    ``fin_clean | rst_observed | idle_timeout | still_open | not_observed``.
    We use the following heuristic based on the available signals:

    * ``not_observed``: handshake was not observed (the diagnose
      handler can't say anything about a stream it never saw).
    * ``rst_observed``: at least one row in the resets envelope.
    * ``fin_clean``: handshake was observed *and* at least one row
      was preceded by a FIN. The classify_tcp_resets envelope
      surfaces ``preceded_by_fin`` per row; we use it as a coarse
      signal that the connection closed cleanly.
    * ``still_open``: no RST and no FIN observed but handshake
      complete — interpreted as the stream is still active in the
      capture window.
    * ``idle_timeout``: handshake complete, no FIN, no RST, but
      retransmissions tail off. This is a coarse heuristic; the
      design notes accept this approximation since precise idle
      detection requires application-layer parsing out of scope.

    Returns ``None`` when the resets sub-handler failed (Req 18.7).
    """
    if not reset_ok or reset_envelope is None:
        return None
    data = reset_envelope.get("data") or {}
    rows = data.get("rows") or []

    handshake_observed = bool(
        handshake_section
        and handshake_section.get("failure_reason") != _HANDSHAKE_REASON_NOT_OBSERVED
    )

    if not handshake_observed and not rows:
        # No handshake, no resets — we genuinely have no signal.
        return {"state": "not_observed", "reset_origin_side": None}

    if rows:
        # Pick the first RST as the canonical reset_origin_side. Rows
        # are returned by classify_tcp_resets ordered by frame_time
        # ascending, so the first row reflects whoever initiated the
        # tear-down.
        first = rows[0]
        side = first.get("reset_origin_side") or "unknown"
        return {"state": "rst_observed", "reset_origin_side": side}

    # No RST. preceded_by_fin is a per-row attribute on resets; with
    # zero RSTs we fall back to inspecting the handshake — a complete
    # handshake without subsequent reset is reported as still_open
    # (the capture window cut off before the close), and an
    # incomplete handshake without reset is also still_open from the
    # diagnose perspective.
    if handshake_section and handshake_section.get("complete"):
        return {"state": "still_open", "reset_origin_side": None}
    return {"state": "still_open", "reset_origin_side": None}


def _build_rtt_section(envelope, was_successful, stream_id):
    """Project the rtt sub-object from ``handle_get_rtt_distribution``.

    The underlying handler returns one row per stream with
    ``rtt_min_ms``, ``rtt_p50_ms``, ``rtt_p95_ms``, ``rtt_max_ms``,
    ``sample_count`` (Req 5.18). Diagnose summarises a single stream,
    so we pick the row whose ``stream_id`` matches; if multiple rows
    are present (flow_selector spanning multiple streams) we
    aggregate by taking the min/max bounds and summing the sample
    count, which preserves Req 18.2's shape.

    Returns ``None`` when the sub-handler failed (Req 18.7).
    """
    if not was_successful or envelope is None:
        return None
    data = envelope.get("data") or {}
    rows = data.get("rows") or []

    if not rows:
        return {
            "min_ms": 0,
            "p50_ms": 0,
            "p95_ms": 0,
            "max_ms": 0,
            "sample_count": 0,
        }

    target = None
    for row in rows:
        if row.get("stream_id") == stream_id:
            target = row
            break
    if target is None:
        target = rows[0]
    return {
        "min_ms": _coerce_float(target.get("rtt_min_ms")),
        "p50_ms": _coerce_float(target.get("rtt_p50_ms")),
        "p95_ms": _coerce_float(target.get("rtt_p95_ms")),
        "max_ms": _coerce_float(target.get("rtt_max_ms")),
        "sample_count": _coerce_int(target.get("sample_count")),
    }


def _build_retransmissions_section(out_of_order_envelope, out_of_order_ok, stream_id):
    """Project the retransmissions sub-object.

    Per Req 18.2 the report exposes ``total_count``,
    ``fast_retransmit_count``, ``spurious_count``, and
    ``sack_retransmit_count``. The ``fast_retransmit_count`` comes
    directly from ``handle_detect_out_of_order_packets`` (which
    surfaces ``fast_retransmit_count`` per stream — Req 5.15).

    The ``total_count`` and ``sack_retransmit_count`` would normally
    come from a separate ``detect_retransmissions`` query; we
    summarise them from the ``handle_detect_out_of_order_packets``
    aggregate (which already counts every retransmission-related
    boolean column, including DSACK). ``spurious_count`` is set
    based on the DSACK count: a DSACK indicates the receiver got the
    same data twice, which is the canonical "spurious retransmit"
    signal in tshark's analysis output.

    Returns ``None`` when the sub-handler failed (Req 18.7).
    """
    if not out_of_order_ok or out_of_order_envelope is None:
        return None
    data = out_of_order_envelope.get("data") or {}
    rows = data.get("rows") or []

    target = None
    for row in rows:
        if row.get("stream_id") == stream_id:
            target = row
            break
    if target is None and rows:
        target = rows[0]

    if target is None:
        return {
            "total_count": 0,
            "fast_retransmit_count": 0,
            "spurious_count": 0,
            "sack_retransmit_count": 0,
        }

    fast = _coerce_int(target.get("fast_retransmit_count"))
    dsack = _coerce_int(target.get("dsack_count"))
    return {
        "total_count": fast + dsack,
        "fast_retransmit_count": fast,
        # Spurious retransmits are signalled by DSACKs (the receiver
        # acknowledging the same data twice).
        "spurious_count": dsack,
        # SACK-driven retransmits aren't directly available from the
        # out_of_order aggregate; we surface the DSACK count as the
        # closest available proxy. The exact field is Req 18.2-required
        # so it cannot be omitted.
        "sack_retransmit_count": dsack,
    }


def _build_out_of_order_section(envelope, was_successful, stream_id):
    """Project the out_of_order sub-object.

    Per Req 18.2 the report exposes ``out_of_order_count``,
    ``duplicate_ack_count``, and ``dsack_count``. All three are
    surfaced directly by ``handle_detect_out_of_order_packets``
    (Req 5.15).

    Returns ``None`` when the sub-handler failed (Req 18.7).
    """
    if not was_successful or envelope is None:
        return None
    data = envelope.get("data") or {}
    rows = data.get("rows") or []

    target = None
    for row in rows:
        if row.get("stream_id") == stream_id:
            target = row
            break
    if target is None and rows:
        target = rows[0]

    if target is None:
        return {
            "out_of_order_count": 0,
            "duplicate_ack_count": 0,
            "dsack_count": 0,
        }
    return {
        "out_of_order_count": _coerce_int(target.get("out_of_order_count")),
        "duplicate_ack_count": _coerce_int(target.get("duplicate_ack_count")),
        "dsack_count": _coerce_int(target.get("dsack_count")),
    }


def _build_zero_window_section(envelope, was_successful, stream_id):
    """Project the zero_window sub-object.

    Per Req 18.2 the report exposes ``event_count`` and
    ``total_duration_ms``. Both are surfaced by
    ``handle_detect_zero_window`` (Req 5.16, where they're called
    ``zero_window_event_count`` and ``zero_window_total_duration_ms``).

    Returns ``None`` when the sub-handler failed (Req 18.7).
    """
    if not was_successful or envelope is None:
        return None
    data = envelope.get("data") or {}
    rows = data.get("rows") or []

    target = None
    for row in rows:
        if row.get("stream_id") == stream_id:
            target = row
            break
    if target is None and rows:
        target = rows[0]

    if target is None:
        return {"event_count": 0, "total_duration_ms": 0}
    return {
        "event_count": _coerce_int(target.get("zero_window_event_count")),
        "total_duration_ms": _coerce_float(
            target.get("zero_window_total_duration_ms")
        ),
    }


def _build_tcp_options_section(envelope, was_successful):
    """Project the tcp_options sub-object.

    Per Req 18.2 the report exposes ``mss_advertised``,
    ``window_scale``, ``sack_permitted``, ``timestamps_enabled``, and
    ``mss_effective_min``. ``handle_analyze_tcp_options`` returns one
    row per direction (``client_to_server`` and ``server_to_client``,
    Req 5.17). For diagnose we summarise across both directions:

    * ``mss_advertised`` — the SYN-side MSS (the advertisement made
      by the initiator). When both directions are present we use the
      maximum so we surface the value the path actually committed
      to.
    * ``mss_effective_min`` — the global minimum across directions
      (or zero when no data segment was observed).
    * ``window_scale``, ``sack_permitted``, ``timestamps_enabled`` —
      logical OR / max across both directions, since the attribute is
      symmetric in the canonical case.

    Returns ``None`` when the sub-handler failed (Req 18.7).
    """
    if not was_successful or envelope is None:
        return None
    data = envelope.get("data") or {}
    rows = data.get("rows") or []

    if not rows:
        return {
            "mss_advertised": 0,
            "window_scale": 0,
            "sack_permitted": False,
            "timestamps_enabled": False,
            "mss_effective_min": 0,
        }

    mss_values = [_coerce_int(r.get("mss_advertised")) for r in rows]
    mss_effective = [_coerce_int(r.get("mss_effective_min")) for r in rows]
    ws_values = [_coerce_int(r.get("window_scale")) for r in rows]
    sack = any(_coerce_bool(r.get("sack_permitted")) for r in rows)
    ts = any(_coerce_bool(r.get("timestamps_enabled")) for r in rows)

    return {
        "mss_advertised": max(mss_values) if mss_values else 0,
        "window_scale": max(ws_values) if ws_values else 0,
        "sack_permitted": sack,
        "timestamps_enabled": ts,
        # The non-zero minimum across directions reflects the smallest
        # data segment observed end-to-end. We exclude zero entries so
        # a direction that never sent data doesn't drag the min down.
        "mss_effective_min": (
            min((v for v in mss_effective if v > 0), default=0)
        ),
    }


def _compute_mss_clamping(tcp_options):
    """Return ``True`` when ``mss_effective_min < 0.8 * mss_advertised``.

    Per Req 18.2 / 18.3, MSS clamping is signalled when the smallest
    effective payload size observed end-to-end is materially below
    the advertised MSS — typically a middlebox rewriting the
    advertisement or fragmenting downstream. The 80% threshold is
    fixed by the requirement.

    Returns ``False`` when the tcp_options section is unavailable
    (the boolean cannot be ``null`` per Req 18.2's shape).
    """
    if tcp_options is None:
        return False
    advertised = _coerce_int(tcp_options.get("mss_advertised"))
    effective = _coerce_int(tcp_options.get("mss_effective_min"))
    if advertised <= 0 or effective <= 0:
        return False
    return effective < 0.8 * advertised


def _classify_anomalies(
    handshake,
    connection_close,
    rtt,
    retransmissions,
    out_of_order,
    zero_window,
    tcp_options,
    mss_clamping_mismatch,
    total_packet_count,
    tls_client_hello_fragmented,
    unavailable_sections,
):
    """Apply the Req 18.3 classification rules to populate ``anomalies``.

    Returns a list of ``{"category", "description"}`` dicts. Per
    Req 18.3:

    * Each rule fires independently and adds its own entry.
    * When the partial-failure path nulled out a sub-object, the
      rules that depend on that section are skipped (we cannot make a
      claim from absent data). The unavailable sections are reported
      via the closing ``none`` entry.
    * When *no* other rule fires, exactly one ``none`` entry is
      added.
    * Per Req 18.7, when any section was unavailable we add a
      ``none`` entry whose description names every unavailable
      section, *in addition to* whatever rules fired for the sections
      that were available.
    """
    anomalies = []

    if handshake is not None:
        if not handshake.get("complete"):
            reason = handshake.get("failure_reason") or "unknown"
            if reason != _HANDSHAKE_REASON_NOT_OBSERVED:
                anomalies.append(
                    {
                        "category": _ANOMALY_HANDSHAKE_FAILED,
                        "description": (
                            f"TCP handshake did not complete: {reason}."
                        ),
                    }
                )
        duration = handshake.get("duration_ms")
        if (
            isinstance(duration, (int, float))
            and duration > _DIAGNOSE_HANDSHAKE_SLOW_MS
        ):
            anomalies.append(
                {
                    "category": _ANOMALY_HANDSHAKE_SLOW,
                    "description": (
                        f"TCP handshake completed in {duration:.0f} ms, "
                        f"above the {int(_DIAGNOSE_HANDSHAKE_SLOW_MS)} ms threshold."
                    ),
                }
            )

    if connection_close is not None:
        side = connection_close.get("reset_origin_side")
        state = connection_close.get("state")
        if state == "rst_observed":
            if side == "client":
                anomalies.append(
                    {
                        "category": _ANOMALY_RESET_BY_CLIENT,
                        "description": (
                            "Connection terminated by a TCP RST from the "
                            "client side."
                        ),
                    }
                )
            elif side == "server":
                anomalies.append(
                    {
                        "category": _ANOMALY_RESET_BY_SERVER,
                        "description": (
                            "Connection terminated by a TCP RST from the "
                            "server side."
                        ),
                    }
                )
            elif side == "middlebox":
                anomalies.append(
                    {
                        "category": _ANOMALY_RESET_BY_MIDDLEBOX,
                        "description": (
                            "Connection terminated by a TCP RST whose source "
                            "matched neither endpoint (middlebox-injected)."
                        ),
                    }
                )
        if state == "idle_timeout":
            anomalies.append(
                {
                    "category": _ANOMALY_IDLE_TIMEOUT_CLOSE,
                    "description": (
                        "Connection closed due to idle timeout."
                    ),
                }
            )

    if retransmissions is not None and total_packet_count > 0:
        total_retx = _coerce_int(retransmissions.get("total_count"))
        if total_retx > _DIAGNOSE_EXCESSIVE_RETX_FRACTION * total_packet_count:
            pct = total_retx / total_packet_count * 100.0
            anomalies.append(
                {
                    "category": _ANOMALY_EXCESSIVE_RETX,
                    "description": (
                        f"{total_retx} retransmissions across "
                        f"{total_packet_count} packets ({pct:.1f}%), "
                        "above the 5% threshold."
                    ),
                }
            )
    if retransmissions is not None:
        spurious = _coerce_int(retransmissions.get("spurious_count"))
        if spurious > 0:
            anomalies.append(
                {
                    "category": _ANOMALY_SPURIOUS_RETX,
                    "description": (
                        f"{spurious} spurious retransmission(s) observed "
                        "(receiver DSACKed the same data twice)."
                    ),
                }
            )

    if out_of_order is not None:
        ooo_count = _coerce_int(out_of_order.get("out_of_order_count"))
        if (
            total_packet_count > 0
            and ooo_count > _DIAGNOSE_OUT_OF_ORDER_FRACTION * total_packet_count
        ):
            pct = ooo_count / total_packet_count * 100.0
            anomalies.append(
                {
                    "category": _ANOMALY_OUT_OF_ORDER,
                    "description": (
                        f"{ooo_count} out-of-order packet(s) across "
                        f"{total_packet_count} packets ({pct:.1f}%), "
                        "above the 1% threshold."
                    ),
                }
            )
        dup_ack = _coerce_int(out_of_order.get("duplicate_ack_count"))
        if dup_ack > _DIAGNOSE_DUPLICATE_ACK_THRESHOLD:
            anomalies.append(
                {
                    "category": _ANOMALY_DUPLICATE_ACKS,
                    "description": (
                        f"{dup_ack} duplicate ACK(s) observed, above the "
                        f"{_DIAGNOSE_DUPLICATE_ACK_THRESHOLD}-event threshold."
                    ),
                }
            )

    if zero_window is not None:
        stall_ms = _coerce_float(zero_window.get("total_duration_ms"))
        if stall_ms > _DIAGNOSE_ZERO_WINDOW_STALL_MS:
            anomalies.append(
                {
                    "category": _ANOMALY_ZERO_WINDOW_STALL,
                    "description": (
                        f"Zero-window stalls totaling {stall_ms:.0f} ms, "
                        f"above the {int(_DIAGNOSE_ZERO_WINDOW_STALL_MS)} ms threshold."
                    ),
                }
            )

    if mss_clamping_mismatch:
        advertised = (
            _coerce_int(tcp_options.get("mss_advertised"))
            if tcp_options
            else 0
        )
        effective = (
            _coerce_int(tcp_options.get("mss_effective_min"))
            if tcp_options
            else 0
        )
        anomalies.append(
            {
                "category": _ANOMALY_MSS_CLAMPING,
                "description": (
                    f"MSS clamping detected: effective MSS {effective} bytes "
                    f"is below 80% of the advertised {advertised} bytes."
                ),
            }
        )

    if tls_client_hello_fragmented:
        anomalies.append(
            {
                "category": _ANOMALY_TLS_HELLO_FRAGMENTED,
                "description": (
                    "TLS Client Hello observed split across multiple TCP "
                    "segments (fragment count > 1)."
                ),
            }
        )

    # Per Req 18.7, partial Athena failure adds a single ``none`` entry
    # listing every unavailable section, in addition to any rules that
    # fired for sections that *were* available.
    if unavailable_sections:
        unique = sorted(set(unavailable_sections))
        anomalies.append(
            {
                "category": _ANOMALY_NONE,
                "description": (
                    "The following sections were unavailable due to upstream "
                    f"query failures: {', '.join(unique)}."
                ),
            }
        )
    elif not anomalies:
        # Per Req 18.3 emit exactly one ``none`` entry when no other
        # rule matched.
        anomalies.append(
            {
                "category": _ANOMALY_NONE,
                "description": "No TCP-level anomalies detected.",
            }
        )

    return anomalies


def _format_diagnose_section(title, body_lines):
    """Render one section of the formattedText output (Req 18.4)."""
    lines = [title]
    for line in body_lines[:5]:  # Req 18.4: 1-5 bullet points per section.
        lines.append(f"  - {line}")
    return "\n".join(lines)


def _format_endpoint(endpoint):
    """Render a ``{ip, port}`` endpoint as ``ip:port`` (or ``unknown``)."""
    if not isinstance(endpoint, dict):
        return "unknown"
    ip = endpoint.get("ip") or "unknown"
    port = endpoint.get("port")
    if port in (None, 0, ""):
        return ip
    return f"{ip}:{port}"


def _format_diagnose_report(report):
    """Render a Tcp_Stream_Health_Report as the Req 18.4 formattedText."""
    sections = []

    # 1. Handshake
    hs = report.get("handshake")
    if hs is None:
        sections.append(
            _format_diagnose_section("Handshake:", ["Section unavailable."])
        )
    else:
        bullets = [
            (
                "complete"
                if hs.get("complete")
                else f"failed ({hs.get('failure_reason') or 'unknown'})"
            ),
        ]
        duration = hs.get("duration_ms")
        if isinstance(duration, (int, float)):
            bullets.append(f"duration: {duration:.2f} ms")
        sections.append(_format_diagnose_section("Handshake:", bullets))

    # 2. Connection close
    cc = report.get("connection_close")
    if cc is None:
        sections.append(
            _format_diagnose_section(
                "Connection close:", ["Section unavailable."]
            )
        )
    else:
        bullets = [f"state: {cc.get('state')}"]
        side = cc.get("reset_origin_side")
        if side:
            bullets.append(f"reset origin: {side}")
        sections.append(_format_diagnose_section("Connection close:", bullets))

    # 3. RTT
    rtt = report.get("rtt")
    if rtt is None:
        sections.append(
            _format_diagnose_section("RTT:", ["Section unavailable."])
        )
    else:
        bullets = [
            f"min: {rtt.get('min_ms')} ms",
            f"p50: {rtt.get('p50_ms')} ms",
            f"p95: {rtt.get('p95_ms')} ms",
            f"max: {rtt.get('max_ms')} ms",
            f"samples: {rtt.get('sample_count')}",
        ]
        sections.append(_format_diagnose_section("RTT:", bullets))

    # 4. Retransmissions
    retx = report.get("retransmissions")
    if retx is None:
        sections.append(
            _format_diagnose_section(
                "Retransmissions:", ["Section unavailable."]
            )
        )
    else:
        bullets = [
            f"total: {retx.get('total_count')}",
            f"fast retransmits: {retx.get('fast_retransmit_count')}",
            f"spurious: {retx.get('spurious_count')}",
            f"SACK retransmits: {retx.get('sack_retransmit_count')}",
        ]
        sections.append(_format_diagnose_section("Retransmissions:", bullets))

    # 5. Out-of-order
    ooo = report.get("out_of_order")
    if ooo is None:
        sections.append(
            _format_diagnose_section(
                "Out-of-order:", ["Section unavailable."]
            )
        )
    else:
        bullets = [
            f"out-of-order: {ooo.get('out_of_order_count')}",
            f"duplicate ACKs: {ooo.get('duplicate_ack_count')}",
            f"DSACKs: {ooo.get('dsack_count')}",
        ]
        sections.append(_format_diagnose_section("Out-of-order:", bullets))

    # 6. Zero-window
    zw = report.get("zero_window")
    if zw is None:
        sections.append(
            _format_diagnose_section(
                "Zero-window:", ["Section unavailable."]
            )
        )
    else:
        bullets = [
            f"events: {zw.get('event_count')}",
            f"total stall: {zw.get('total_duration_ms')} ms",
        ]
        sections.append(_format_diagnose_section("Zero-window:", bullets))

    # 7. TCP options
    opts = report.get("tcp_options")
    if opts is None:
        sections.append(
            _format_diagnose_section(
                "TCP options:", ["Section unavailable."]
            )
        )
    else:
        bullets = [
            f"MSS advertised: {opts.get('mss_advertised')}",
            f"window scale: {opts.get('window_scale')}",
            f"SACK permitted: {opts.get('sack_permitted')}",
            f"timestamps: {opts.get('timestamps_enabled')}",
            f"effective min MSS: {opts.get('mss_effective_min')}",
        ]
        sections.append(_format_diagnose_section("TCP options:", bullets))

    # 8. MSS clamping
    sections.append(
        _format_diagnose_section(
            "MSS clamping:",
            [
                "mismatch detected"
                if report.get("mss_clamping_mismatch")
                else "no mismatch"
            ],
        )
    )

    # 9. Anomalies
    anomalies = report.get("anomalies") or []
    if not anomalies:
        anomaly_bullets = ["none"]
    else:
        anomaly_bullets = [
            f"{a.get('category')}: {a.get('description')}" for a in anomalies
        ]
    sections.append(_format_diagnose_section("Anomalies:", anomaly_bullets))

    return "\n".join(sections)


def _resolve_endpoints_from_matched_streams(matched_streams, stream_id):
    """Find the matched_streams row for ``stream_id`` and return endpoints.

    Returns ``({"ip": ..., "port": ...}, {"ip": ..., "port": ...})``
    or ``(None, None)`` when the stream isn't in the list.
    """
    if not matched_streams:
        return None, None
    for row in matched_streams:
        if row.get("stream_id") == stream_id:
            client = {
                "ip": row.get("client_ip") or "",
                "port": _coerce_int(row.get("client_port")) or 0,
            }
            server = {
                "ip": row.get("server_ip") or "",
                "port": _coerce_int(row.get("server_port")) or 0,
            }
            return client, server
    return None, None


def _diagnose_one_stream(
    capture_id,
    stream_id,
    matched_streams,
    deadline,
):
    """Build a single Tcp_Stream_Health_Report for ``stream_id``.

    Returns ``(report, no_traffic_observed)``. ``no_traffic_observed``
    is ``True`` when the stream's partition contains zero rows
    (Req 18.6) so the caller can override the formattedText.
    """
    # Probe total packet/byte counts. The result is used as the
    # denominator for the percentage-based rules in Req 18.3 *and* as
    # the empty-partition signal for Req 18.6.
    if time.monotonic() >= deadline:
        # Out of budget — return a partial report rather than erroring.
        unavailable = list(_DIAGNOSE_SECTION_NAMES)
        report = _build_unavailable_report(
            stream_id, matched_streams, unavailable
        )
        return report, False

    total_packets, _total_bytes = _query_stream_packet_total(
        capture_id, stream_id
    )

    # Empty partition (Req 18.6): all numerics zero, single ``none``
    # anomaly with "no traffic observed", success=True.
    if total_packets == 0:
        client_endpoint, server_endpoint = _resolve_endpoints_from_matched_streams(
            matched_streams, stream_id
        )
        return (
            {
                "stream_id": stream_id,
                "client_endpoint": client_endpoint
                or {"ip": "", "port": 0},
                "server_endpoint": server_endpoint
                or {"ip": "", "port": 0},
                "handshake": {
                    "complete": False,
                    "duration_ms": None,
                    "failure_reason": _HANDSHAKE_REASON_NOT_OBSERVED,
                },
                "connection_close": {
                    "state": "not_observed",
                    "reset_origin_side": None,
                },
                "rtt": {
                    "min_ms": 0,
                    "p50_ms": 0,
                    "p95_ms": 0,
                    "max_ms": 0,
                    "sample_count": 0,
                },
                "retransmissions": {
                    "total_count": 0,
                    "fast_retransmit_count": 0,
                    "spurious_count": 0,
                    "sack_retransmit_count": 0,
                },
                "out_of_order": {
                    "out_of_order_count": 0,
                    "duplicate_ack_count": 0,
                    "dsack_count": 0,
                },
                "zero_window": {
                    "event_count": 0,
                    "total_duration_ms": 0,
                },
                "tcp_options": {
                    "mss_advertised": 0,
                    "window_scale": 0,
                    "sack_permitted": False,
                    "timestamps_enabled": False,
                    "mss_effective_min": 0,
                },
                "mss_clamping_mismatch": False,
                "anomalies": [
                    {
                        "category": _ANOMALY_NONE,
                        "description": (
                            "No traffic observed for this stream in the "
                            "supplied capture partition."
                        ),
                    }
                ],
            },
            True,
        )

    # Run the seven analysis sub-handlers. Each is wrapped so a single
    # failure cannot blow up the whole diagnose call (Req 18.7).
    unavailable_sections = []
    sub_params = {"capture_id": capture_id, "stream_id": stream_id}

    # 1. Handshake -- handle_reconstruct_tcp_handshake
    handshake_envelope, handshake_ok = (None, False)
    if time.monotonic() < deadline:
        handshake_envelope, handshake_ok = _safe_call_subhandler(
            handle_reconstruct_tcp_handshake,
            sub_params,
            "handshake",
            unavailable_sections,
        )
    else:
        unavailable_sections.append("handshake")

    # 2. Resets -- handle_classify_tcp_resets (used to derive connection_close)
    reset_envelope, reset_ok = (None, False)
    if time.monotonic() < deadline:
        reset_envelope, reset_ok = _safe_call_subhandler(
            handle_classify_tcp_resets,
            sub_params,
            "connection_close",
            unavailable_sections,
        )
    else:
        unavailable_sections.append("connection_close")

    # 3. Out-of-order (also feeds retransmissions section)
    ooo_envelope, ooo_ok = (None, False)
    if time.monotonic() < deadline:
        ooo_envelope, ooo_ok = _safe_call_subhandler(
            handle_detect_out_of_order_packets,
            sub_params,
            "out_of_order",
            unavailable_sections,
        )
    else:
        unavailable_sections.append("out_of_order")

    # The retransmissions section is built from the same envelope, so
    # if out_of_order is unavailable we must also mark retransmissions.
    if not ooo_ok and "retransmissions" not in unavailable_sections:
        unavailable_sections.append("retransmissions")

    # 4. Zero-window
    zw_envelope, zw_ok = (None, False)
    if time.monotonic() < deadline:
        zw_envelope, zw_ok = _safe_call_subhandler(
            handle_detect_zero_window,
            sub_params,
            "zero_window",
            unavailable_sections,
        )
    else:
        unavailable_sections.append("zero_window")

    # 5. TCP options
    opts_envelope, opts_ok = (None, False)
    if time.monotonic() < deadline:
        opts_envelope, opts_ok = _safe_call_subhandler(
            handle_analyze_tcp_options,
            sub_params,
            "tcp_options",
            unavailable_sections,
        )
    else:
        unavailable_sections.append("tcp_options")

    # 6. RTT
    rtt_envelope, rtt_ok = (None, False)
    if time.monotonic() < deadline:
        rtt_envelope, rtt_ok = _safe_call_subhandler(
            handle_get_rtt_distribution,
            sub_params,
            "rtt",
            unavailable_sections,
        )
    else:
        unavailable_sections.append("rtt")

    # 7. Request-response latency. Per Req 18.1 the sub-query is run,
    # but the Tcp_Stream_Health_Report shape (Req 18.2) does not
    # include a latency section directly — request-response latency
    # is one of the per-pair signals available to the orchestration
    # agent via the standalone action. We still call it within the
    # 90-second budget so any partial-failure annotation reflects
    # reality, but we don't add a Req-18.2 section for it.
    if time.monotonic() < deadline:
        try:
            handle_get_request_response_latency(sub_params)
        except Exception:  # pragma: no cover — defence in depth
            logger.exception(
                "diagnose_tcp_stream: request-response latency probe failed"
            )

    # Project the report sections.
    handshake_section = _build_handshake_section(
        handshake_envelope, handshake_ok
    )
    connection_close_section = _build_connection_close_section(
        reset_envelope, reset_ok, handshake_section
    )
    rtt_section = _build_rtt_section(rtt_envelope, rtt_ok, stream_id)
    retx_section = _build_retransmissions_section(
        ooo_envelope, ooo_ok, stream_id
    )
    ooo_section = _build_out_of_order_section(
        ooo_envelope, ooo_ok, stream_id
    )
    zw_section = _build_zero_window_section(zw_envelope, zw_ok, stream_id)
    opts_section = _build_tcp_options_section(opts_envelope, opts_ok)
    mss_clamping = _compute_mss_clamping(opts_section)

    # Optional probe for TLS Client Hello fragmentation. We don't fail
    # the report if this query fails — the rule simply doesn't fire.
    tls_fragmented = False
    if time.monotonic() < deadline:
        probe = _query_tls_client_hello_fragmented(capture_id, stream_id)
        if probe is True:
            tls_fragmented = True

    # Resolve endpoints from matched_streams when available; otherwise
    # extract from the handshake rows (the SYN sender is the client).
    client_endpoint, server_endpoint = _resolve_endpoints_from_matched_streams(
        matched_streams, stream_id
    )
    if client_endpoint is None or server_endpoint is None:
        if handshake_ok and handshake_envelope:
            client_endpoint, server_endpoint = _endpoints_from_handshake_rows(
                handshake_envelope, stream_id
            )

    if client_endpoint is None:
        client_endpoint = {"ip": "", "port": 0}
    if server_endpoint is None:
        server_endpoint = {"ip": "", "port": 0}

    anomalies = _classify_anomalies(
        handshake_section,
        connection_close_section,
        rtt_section,
        retx_section,
        ooo_section,
        zw_section,
        opts_section,
        mss_clamping,
        total_packets,
        tls_fragmented,
        unavailable_sections,
    )

    return (
        {
            "stream_id": stream_id,
            "client_endpoint": client_endpoint,
            "server_endpoint": server_endpoint,
            "handshake": handshake_section,
            "connection_close": connection_close_section,
            "rtt": rtt_section,
            "retransmissions": retx_section,
            "out_of_order": ooo_section,
            "zero_window": zw_section,
            "tcp_options": opts_section,
            "mss_clamping_mismatch": mss_clamping,
            "anomalies": anomalies,
        },
        False,
    )


def _endpoints_from_handshake_rows(handshake_envelope, stream_id):
    """Derive client/server endpoints from the handshake projection.

    Falls back when the matched_streams metadata isn't available
    (e.g. when the diagnose handler was called with ``stream_id``
    only). The handshake envelope rows include ``direction`` and
    ``tcp_stream``; we cannot derive IPs from that projection alone
    because the underlying SQL doesn't surface ``src_ip``/``dst_ip``.
    Returns ``(None, None)`` to signal the caller to use empty
    endpoints — Req 18.2 only mandates that the keys exist with the
    right shape, not that they always carry meaningful values.
    """
    # The reconstruct_tcp_handshake projection (see
    # ``handle_reconstruct_tcp_handshake``) doesn't include src/dst
    # IPs, so we can't derive endpoints from it. Returning (None,
    # None) lets the caller fall back to placeholder values. Future
    # work could extend that handler's projection if endpoint
    # accuracy becomes a hard requirement.
    _ = (handshake_envelope, stream_id)
    return None, None


def _build_unavailable_report(stream_id, matched_streams, unavailable_sections):
    """Build a Tcp_Stream_Health_Report whose sections are all null.

    Used by the budget-exhausted path so the response still contains
    a valid Req 18.2-shaped report with every section nulled out and
    a single ``none`` anomaly listing the unavailable sections.
    """
    client_endpoint, server_endpoint = _resolve_endpoints_from_matched_streams(
        matched_streams, stream_id
    )
    return {
        "stream_id": stream_id,
        "client_endpoint": client_endpoint or {"ip": "", "port": 0},
        "server_endpoint": server_endpoint or {"ip": "", "port": 0},
        "handshake": None,
        "connection_close": None,
        "rtt": None,
        "retransmissions": None,
        "out_of_order": None,
        "zero_window": None,
        "tcp_options": None,
        "mss_clamping_mismatch": False,
        "anomalies": [
            {
                "category": _ANOMALY_NONE,
                "description": (
                    "The following sections were unavailable due to "
                    "budget exhaustion: "
                    f"{', '.join(unavailable_sections)}."
                ),
            }
        ],
    }


def handle_diagnose_tcp_stream(params: dict) -> dict:
    """Produce a structured Tcp_Stream_Health_Report for one or more streams.

    Implements Reqs 18.1, 18.2, 18.3, 18.4, 18.5, 18.6, 18.7, 18.13, 18.14.

    Behaviour:

    1. Validate ``capture_id`` against Capture_Id_Format (Req 18.5).
    2. Validate ``stream_id`` (when supplied) against the Stream_Id
       pattern (Reqs 5.21, 18.5).
    3. Reject when neither ``stream_id`` nor ``flow_selector`` is
       supplied (Req 18.14).
    4. When ``flow_selector`` is supplied, resolve it via the shared
       :func:`_resolve_flow_selector_for_handler` helper. When the
       resolution matches more than one TCP stream, rank them per
       Req 18.13 (packet count desc, then bytes desc), cap at 20,
       and record the cap in ``metadata.diagnose_streams_capped``.
    5. For each target stream, invoke the seven analysis sub-handlers
       (``reconstruct_tcp_handshake``, ``classify_tcp_resets``,
       ``detect_out_of_order_packets``, ``detect_zero_window``,
       ``analyze_tcp_options``, ``get_rtt_distribution``,
       ``get_request_response_latency``) within a 90-second wall-clock
       budget (Req 18.1). Each sub-handler call is isolated so a
       single failure marks only that section as unavailable
       (Req 18.7).
    6. Build the Req 18.2-shaped report by projecting each
       sub-handler's output, computing ``mss_clamping_mismatch``
       (true when ``mss_effective_min < 0.8 * mss_advertised``,
       Req 18.2), and applying the Req 18.3 anomaly classification
       rules.
    7. Render the formattedText in the section order mandated by
       Req 18.4 (handshake, connection close, RTT, retransmissions,
       out-of-order, zero-window, TCP options, MSS clamping,
       anomalies), with each section formatted as a header followed
       by 1-5 bullets.

    Args:
        params: Dict with required ``capture_id`` and at least one of
            ``stream_id`` (Stream_Id pattern) or ``flow_selector``
            (Flow_Selector shape).

    Returns:
        Response envelope. ``data`` carries a single
        Tcp_Stream_Health_Report when one stream is targeted, or an
        array under ``data.reports`` when a flow_selector resolved
        to multiple streams (capped at 20 — Req 18.13).
    """
    if not isinstance(params, dict):
        params = {}

    # --- 1. Validate capture_id (Req 18.5) ---------------------------
    try:
        capture_id = validate_capture_id(params.get("capture_id"))
    except ValidationError as exc:
        return _validation_error_response(
            "diagnose_tcp_stream",
            exc,
            _PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
        )

    # --- 2. Validate stream_id when supplied (Req 18.5) -------------
    raw_stream_id = params.get("stream_id")
    stream_id: Optional[str] = None
    if raw_stream_id is not None:
        try:
            stream_id = validate_stream_id(raw_stream_id)
        except ValidationError as exc:
            return _validation_error_response(
                "diagnose_tcp_stream",
                exc,
                _PCAP_QUERY_SOURCE_API,
                data_freshness="near-real-time",
            )

    # --- 3. Resolve flow_selector and enforce Req 18.14 -------------
    flow_resolution = _resolve_flow_selector_for_handler(
        "diagnose_tcp_stream",
        capture_id,
        params.get("flow_selector"),
        stream_id=stream_id,
        stream_id_required=True,
    )
    if "error_envelope" in flow_resolution:
        return flow_resolution["error_envelope"]

    has_flow_selector = params.get("flow_selector") not in (None, {})

    # --- 4. Determine the list of streams to diagnose --------------
    # Three cases:
    #   a. stream_id only -> single stream
    #   b. flow_selector that resolves to one stream -> single stream
    #   c. flow_selector that resolves to multiple streams -> array
    #      capped at 20, ranked by packet count desc / bytes desc
    extra_metadata = dict(flow_resolution.get("metadata") or {})
    matched_streams_meta = extra_metadata.get("matched_streams") or []

    target_stream_ids = []
    cap_applied = False

    if has_flow_selector:
        # Use the predicate-based ranking query so we can break ties
        # by total bytes descending per Req 18.13.
        predicate_fragment = flow_resolution.get("predicate", "")
        # ``_resolve_flow_selector_for_handler`` prepends "AND " — strip
        # it so we can compose into a fresh WHERE clause.
        if predicate_fragment.startswith("AND "):
            predicate_fragment = predicate_fragment[len("AND ") :]
        ranking = _query_diagnose_stream_ranking(
            capture_id, predicate_fragment
        )
        if ranking:
            if len(ranking) > _DIAGNOSE_MAX_REPORTS:
                cap_applied = True
                ranking = ranking[:_DIAGNOSE_MAX_REPORTS]
            target_stream_ids = [r["stream_id"] for r in ranking]
        elif matched_streams_meta:
            # Ranking query failed; fall back to matched_streams
            # ordering (already packet-count desc per
            # query_matched_streams).
            ids = [
                row.get("stream_id")
                for row in matched_streams_meta
                if row.get("stream_id")
            ]
            if len(ids) > _DIAGNOSE_MAX_REPORTS:
                cap_applied = True
                ids = ids[:_DIAGNOSE_MAX_REPORTS]
            target_stream_ids = ids
        elif stream_id is not None:
            target_stream_ids = [stream_id]
    elif stream_id is not None:
        target_stream_ids = [stream_id]

    # When the flow_selector resolved to no streams, we still have
    # to produce a useful response. The spec doesn't explicitly
    # address this case; we treat it as Req 18.6 "empty partition"
    # for the literal flow_selector summary.
    if not target_stream_ids:
        empty_stream = stream_id or "no-matching-stream"
        report = {
            "stream_id": empty_stream,
            "client_endpoint": {"ip": "", "port": 0},
            "server_endpoint": {"ip": "", "port": 0},
            "handshake": {
                "complete": False,
                "duration_ms": None,
                "failure_reason": _HANDSHAKE_REASON_NOT_OBSERVED,
            },
            "connection_close": {
                "state": "not_observed",
                "reset_origin_side": None,
            },
            "rtt": {
                "min_ms": 0,
                "p50_ms": 0,
                "p95_ms": 0,
                "max_ms": 0,
                "sample_count": 0,
            },
            "retransmissions": {
                "total_count": 0,
                "fast_retransmit_count": 0,
                "spurious_count": 0,
                "sack_retransmit_count": 0,
            },
            "out_of_order": {
                "out_of_order_count": 0,
                "duplicate_ack_count": 0,
                "dsack_count": 0,
            },
            "zero_window": {
                "event_count": 0,
                "total_duration_ms": 0,
            },
            "tcp_options": {
                "mss_advertised": 0,
                "window_scale": 0,
                "sack_permitted": False,
                "timestamps_enabled": False,
                "mss_effective_min": 0,
            },
            "mss_clamping_mismatch": False,
            "anomalies": [
                {
                    "category": _ANOMALY_NONE,
                    "description": (
                        "No traffic observed for the supplied selector "
                        "in this capture partition."
                    ),
                }
            ],
        }
        formatted = (
            f"diagnose_tcp_stream: no traffic observed for the supplied "
            f"selector in capture {capture_id}.\n"
            + _format_diagnose_report(report)
        )
        return build_response(
            success=True,
            data={
                "capture_id": capture_id,
                **report,
            },
            formatted_text=formatted,
            source_api=_PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
            extra_metadata=extra_metadata or None,
        )

    # --- 5. Run the per-stream diagnosis with a 90s budget ---------
    deadline = time.monotonic() + 90.0
    reports = []
    no_traffic_flags = []
    for sid in target_stream_ids:
        report, no_traffic = _diagnose_one_stream(
            capture_id, sid, matched_streams_meta, deadline
        )
        reports.append(report)
        no_traffic_flags.append(no_traffic)

    # --- 6. Compose the response envelope ---------------------------
    if cap_applied:
        extra_metadata["diagnose_streams_capped"] = True
        extra_metadata["diagnose_streams_cap"] = _DIAGNOSE_MAX_REPORTS

    if len(reports) == 1:
        report = reports[0]
        if no_traffic_flags[0]:
            formatted = (
                f"diagnose_tcp_stream: no traffic observed for stream "
                f"{report['stream_id']} in capture {capture_id}.\n"
                + _format_diagnose_report(report)
            )
        else:
            formatted = (
                f"diagnose_tcp_stream report for stream "
                f"{report['stream_id']} in capture {capture_id}:\n"
                + _format_diagnose_report(report)
            )
        data = {
            "capture_id": capture_id,
            **report,
        }
        if flow_resolution.get("data_extras"):
            for key, value in flow_resolution["data_extras"].items():
                data.setdefault(key, value)
        return build_response(
            success=True,
            data=data,
            formatted_text=formatted,
            source_api=_PCAP_QUERY_SOURCE_API,
            data_freshness="near-real-time",
            extra_metadata=extra_metadata or None,
        )

    # Multi-stream case (Req 18.13): return up to 20 reports as an
    # array under ``data.reports``.
    formatted_sections = [  # nosemgrep: string-concat-in-list — intentional conditional string building
        f"diagnose_tcp_stream returned {len(reports)} report(s) "  # nosemgrep: string-concat-in-list
        f"for capture {capture_id}"
        + (
            f" (capped at {_DIAGNOSE_MAX_REPORTS} streams; ranked "  # nosemgrep: string-concat-in-list
            "by packet count desc, ties by bytes desc)"
            if cap_applied
            else ""
        )
        + ":"
    ]
    for idx, report in enumerate(reports, start=1):
        formatted_sections.append(
            f"\n--- Stream {idx}: {report['stream_id']} ---\n"
            + _format_diagnose_report(report)
        )
    data = {
        "capture_id": capture_id,
        "reports": reports,
        "report_count": len(reports),
    }
    if flow_resolution.get("data_extras"):
        for key, value in flow_resolution["data_extras"].items():
            data.setdefault(key, value)
    return build_response(
        success=True,
        data=data,
        formatted_text="\n".join(formatted_sections),
        source_api=_PCAP_QUERY_SOURCE_API,
        data_freshness="near-real-time",
        extra_metadata=extra_metadata or None,
    )


# ---------------------------------------------------------------------------
# cleanup_orphaned_sessions action
# ---------------------------------------------------------------------------


def handle_cleanup_orphaned_sessions(params: dict) -> dict:
    """Remove orphaned Traffic Mirror sessions blocking capture on one or more ENIs.

    This action is a convenience wrapper around the internal
    ``_cleanup_orphaned_mirror_sessions`` helper, exposed so the
    orchestration agent (or an operator) can explicitly request
    cleanup when ``start_capture`` reports an "already in use" error.

    In practice, ``start_capture`` already auto-heals by calling
    the same helper internally. This action exists for cases where
    manual/explicit cleanup is needed (e.g. after a reinstall, or
    when the orchestration agent wants to preemptively clear stale
    sessions before proposing a new capture).

    Args:
        params: Dict with:
            ``eni_ids`` (list of str, required): ENI identifiers to
            check for orphaned mirror sessions.

    Returns:
        Response envelope with ``data.cleaned_count`` and per-ENI
        details.
    """
    if not isinstance(params, dict):
        params = {}

    source_api = "ec2:DescribeTrafficMirrorSessions"

    # Validate eni_ids
    raw_eni_ids = params.get("eni_ids")
    if raw_eni_ids is None:
        # No eni_ids supplied — clean ALL goat-network sessions in the account
        try:
            ec2 = _get_ec2_client()
            resp = ec2.describe_traffic_mirror_sessions()
            all_sessions = resp.get("TrafficMirrorSessions", [])
            # Extract unique ENI IDs from all sessions
            eni_set = set()
            for s in all_sessions:
                eni = s.get("NetworkInterfaceId")
                if eni:
                    eni_set.add(eni)
            target_enis = sorted(eni_set) if eni_set else []
        except (ClientError, BotoCoreError) as exc:
            return _aws_error_response(
                "cleanup_orphaned_sessions", exc, source_api,
                "ec2:DescribeTrafficMirrorSessions",
            )
    else:
        try:
            target_enis = validate_eni_ids(raw_eni_ids)
        except ValidationError as exc:
            return _validation_error_response(
                "cleanup_orphaned_sessions", exc, source_api
            )

    if not target_enis:
        return build_response(
            success=True,
            data={"cleaned_count": 0, "message": "No ENIs to check."},
            formatted_text="No orphaned Traffic Mirror sessions found (no ENIs to check).",
            source_api=source_api,
            data_freshness="real-time",
        )

    cleaned = _cleanup_orphaned_mirror_sessions(target_enis)

    return build_response(
        success=True,
        data={
            "cleaned_count": cleaned,
            "eni_ids_checked": target_enis,
        },
        formatted_text=(
            f"Cleaned {cleaned} orphaned Traffic Mirror session(s) "
            f"across {len(target_enis)} ENI(s)."
            if cleaned > 0
            else f"No orphaned sessions found on {len(target_enis)} ENI(s)."
        ),
        source_api=source_api,
        data_freshness="real-time",
    )


# ---------------------------------------------------------------------------
# Action dispatch table (Req 1.2)
#
# Maps each action string from the design document to its handler function
# reference. The entrypoint looks up the action by exact string equality
# and invokes the handler. Unknown or missing actions return an
# ``unknown_action`` error envelope (Req 1.8).
# ---------------------------------------------------------------------------

ACTIONS = {
    # ENI Inventory
    "list_enis": handle_list_enis,
    # Reverse DNS
    "reverse_dns_lookup": handle_reverse_dns_lookup,
    # Capture Lifecycle
    "start_capture": handle_start_capture,
    "stop_capture": handle_stop_capture,
    "list_captures": handle_list_captures,
    "transform_capture": handle_transform_capture,
    "get_capture_progress": handle_get_capture_progress,
    # Pcap Query Actions
    "query_pcap": handle_query_pcap,
    "search_fragmented_packets": handle_search_fragmented_packets,
    "correlate_tcp_streams": handle_correlate_tcp_streams,
    "detect_retransmissions": handle_detect_retransmissions,
    "check_tls_hello_size": handle_check_tls_hello_size,
    "get_conversation_stats": handle_get_conversation_stats,
    "reconstruct_tcp_handshake": handle_reconstruct_tcp_handshake,
    "classify_tcp_resets": handle_classify_tcp_resets,
    "detect_out_of_order_packets": handle_detect_out_of_order_packets,
    "detect_zero_window": handle_detect_zero_window,
    "analyze_tcp_options": handle_analyze_tcp_options,
    "get_rtt_distribution": handle_get_rtt_distribution,
    "get_request_response_latency": handle_get_request_response_latency,
    "diagnose_tcp_stream": handle_diagnose_tcp_stream,
    # Maintenance
    "cleanup_orphaned_sessions": handle_cleanup_orphaned_sessions,
}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


@app.entrypoint
def main_handler(payload):
    """
    Main entry point for the Network Agent.

    Reads ``payload["action"]``, looks up the handler in ``ACTIONS``, and
    dispatches. Unknown, missing, or empty actions produce an
    ``unknown_action`` error envelope (Req 1.8). Any exception raised by
    a handler is converted into ``success=false`` with an error message
    that identifies the failed action (Req 1.9).

    Payload format: ``{"action": "<action_name>", "params": {...}}``

    Synchronous — returns a dict, not an async generator.
    """
    # Tolerate both dict and JSON-string payloads, mirroring the other
    # G.O.A.T. sub-agents.
    try:
        if isinstance(payload, str):
            payload = json.loads(payload)
    except Exception as parse_err:
        logger.warning(f"Failed to parse payload as JSON: {parse_err}")
        return build_response(
            success=False,
            data={},
            formatted_text="Network Agent could not parse the request payload.",
            source_api="agentcore:Invoke",
            data_freshness="real-time",
            error=f"invalid_payload: {parse_err}",
        )

    if not isinstance(payload, dict):
        logger.warning(f"Unexpected payload type: {type(payload).__name__}")
        return build_response(
            success=False,
            data={},
            formatted_text="Network Agent received a payload that is not a JSON object.",
            source_api="agentcore:Invoke",
            data_freshness="real-time",
            error=(
                f"invalid_payload: expected JSON object, got "
                f"{type(payload).__name__}"
            ),
        )

    try:
        logger.info(
            "Network agent received payload: %s",
            json.dumps(payload, default=str)[:500],
        )
    except Exception:
        # Logging must never break dispatch.
        pass

    action = payload.get("action")
    params = payload.get("params") or {}

    # Req 1.8 — missing, empty, or unknown action → unknown_action envelope.
    if not action or not isinstance(action, str):
        return build_response(
            success=False,
            data={},
            formatted_text=(
                "Network Agent could not dispatch: the 'action' field is "
                "missing or empty. Supply one of: "
                f"{', '.join(sorted(ACTIONS.keys()))}."
            ),
            source_api="agentcore:Invoke",
            data_freshness="real-time",
            error="unknown_action: missing or empty 'action' field",
        )

    handler = ACTIONS.get(action)
    if handler is None:
        return build_response(
            success=False,
            data={},
            formatted_text=(
                f"Network Agent does not recognize the action '{action}'. "
                f"Supported actions: {', '.join(sorted(ACTIONS.keys()))}."
            ),
            source_api="agentcore:Invoke",
            data_freshness="real-time",
            error=f"unknown_action: '{action}' is not a registered action",
        )

    # Req 1.9 — convert any handler exception into a structured error
    # envelope identifying the failed action.
    try:
        return handler(params if isinstance(params, dict) else {})
    except Exception as exc:
        logger.exception(f"Handler for action '{action}' raised an exception")
        return build_response(
            success=False,
            data={},
            formatted_text=(
                f"Network Agent action '{action}' failed with an "
                f"unexpected error: {exc}"
            ),
            source_api="agentcore:Invoke",
            data_freshness="real-time",
            error=f"handler_exception: action='{action}' message='{exc}'",
        )


if __name__ == "__main__":
    app.run()
