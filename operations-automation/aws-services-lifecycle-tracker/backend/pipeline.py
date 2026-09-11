"""
Refresh pipeline as ONE AWS Lambda durable function (issue #139).

    extract (facts from AWS docs, per service)  ->  scan (what runs in the
    account, per service x region)  ->  reconcile inventory  ->  notify

Replaces the Step Functions state machine and the separate "Discover My
Resources" action: one execution refreshes the whole picture of the world.

Replay model rules (see .kiro/steering/aws-lambda-durable-functions): every
piece of non-determinism - clock reads, uuid, Bedrock/boto3/DynamoDB calls -
lives INSIDE a step, so replays return checkpointed results instead of
re-executing. Code outside steps is pure transformation of step outputs.

Input contract:
    {
      "mode": "full" | "extract" | "scan",      (default "full")
      "services": ["lambda", "rds", ...],        (default: all enabled)
      "regions": ["eu-central-1", ...],          (default: this function's region)
      "refresh_origin": "manual" | "Auto"        (default "manual")
    }
"""
import os
from datetime import datetime, timezone
from typing import Dict, List

from aws_durable_execution_sdk_python import durable_execution, durable_step, DurableContext, StepContext
from aws_durable_execution_sdk_python.config import Duration, MapConfig, CompletionConfig, StepConfig
from aws_durable_execution_sdk_python.retries import RetryStrategyConfig, create_retry_strategy
from aws_durable_execution_sdk_python.concurrency.models import BatchItemStatus

import account_discovery
from actions import get_all_enabled_services, slim_extraction_result
from workflow_orchestrator import extract_service_lifecycle

MODES = ("full", "extract", "scan")
DEFAULT_CONCURRENCY = 5

# Scanner label -> scanner function. Labels match account_discovery.SCANNER_SERVICE_KEYS.
SCANNERS = {
    "Lambda": account_discovery.discover_lambda_functions,
    "RDS": account_discovery.discover_rds_instances,
    "EKS": account_discovery.discover_eks_clusters,
    "ElastiCache": account_discovery.discover_elasticache_clusters,
    "OpenSearch": account_discovery.discover_opensearch_domains,
    "MSK": account_discovery.discover_msk_clusters,
    "DocumentDB": account_discovery.discover_documentdb_clusters,
    "Neptune": account_discovery.discover_neptune_clusters,
    "Glue": account_discovery.discover_glue_jobs,
    "Elastic Beanstalk": account_discovery.discover_beanstalk_environments,
    "EC2": account_discovery.discover_ec2_instances,
}


# ---------------------------------------------------------------------------
# Pure helpers (safe outside steps: deterministic, no side effects)
# ---------------------------------------------------------------------------

def normalize_spec(event: dict) -> dict:
    """Validate and default the pipeline input. Pure."""
    event = event or {}
    mode = str(event.get("mode") or "full").lower()
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got '{mode}'")
    services = event.get("services")
    if services is not None and not isinstance(services, list):
        raise ValueError("services must be a list of service keys")
    regions = event.get("regions")
    if regions is not None and not isinstance(regions, list):
        raise ValueError("regions must be a list of region names")
    return {
        "mode": mode,
        "services": services,
        "regions": regions,
        "refresh_origin": str(event.get("refresh_origin") or "manual"),
    }


def scan_cells_for(spec: dict, regions: List[str]) -> List[Dict]:
    """Build (scanner label, region) cells. Pure.

    If a services subset was requested, only scanners that emit rows for those
    service keys run (RDS scanner covers both 'rds' and 'aurora').
    """
    wanted = set(spec["services"]) if spec["services"] else None
    cells = []
    for label in SCANNERS:
        keys = account_discovery.SCANNER_SERVICE_KEYS.get(label, [])
        if wanted is not None and not (wanted & set(keys)):
            continue
        for region in regions:
            cells.append({"label": label, "region": region, "service_keys": keys})
    return cells


# ---------------------------------------------------------------------------
# Steps (all non-determinism lives here)
# ---------------------------------------------------------------------------

@durable_step
def start_run(step: StepContext, spec: dict) -> dict:
    """Mint the run identity once: run id, start time, region, service list."""
    import uuid
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    services = spec["services"] if spec["services"] else get_all_enabled_services()
    return {
        "run_id": str(uuid.uuid4()),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "function_region": region,
        "services": services,
        "regions": spec["regions"] or [region],
    }


@durable_step
def extract_cell(step: StepContext, service_name: str, refresh_origin: str) -> dict:
    """Extract one service's deprecation facts (Bedrock + DynamoDB), slim return."""
    result = extract_service_lifecycle(
        service_name=service_name,
        force_refresh=True,
        refresh_origin=refresh_origin,
    )
    return slim_extraction_result(service_name, result)


@durable_step
def scan_cell(step: StepContext, cell: dict) -> dict:
    """Run one scanner in one region; join verdicts against the facts table."""
    scanner = SCANNERS[cell["label"]]
    index = account_discovery.LifecycleIndex(region=cell["region"])
    items = scanner(cell["region"], index)
    return {
        "label": cell["label"],
        "region": cell["region"],
        "service_keys": cell["service_keys"],
        "items": items,
        "ok": True,
    }


@durable_step
def reconcile_inventory(step: StepContext, run_id: str, items: List[Dict], scanned_keys: List[str]) -> dict:
    """Upsert this run's inventory and reconcile ONLY successfully scanned scopes.

    Before writing, resources are cross-checked with AWS Health (#141) so each
    row knows which of its resources AWS has already flagged in a notice.
    """
    health = account_discovery.cross_check_health(items)
    result = account_discovery.save_to_dynamodb(
        items, run_id=run_id, scanned_services=sorted(set(scanned_keys)),
    )
    result["health"] = health
    return result


@durable_step
def summarize_and_notify(step: StepContext, run: dict, spec: dict, extract_summary: dict,
                         scan_summary: dict, reconcile_result: dict) -> dict:
    """Assemble the run summary and publish it to SNS (if a topic is configured)."""
    import boto3

    summary = {
        "run_id": run["run_id"],
        "mode": spec["mode"],
        "refresh_origin": spec["refresh_origin"],
        "started_at": run["started_at"],
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "regions": run["regions"],
        "extract": extract_summary,
        "scan": scan_summary,
        "inventory": reconcile_result,
    }

    topic_arn = os.environ.get("NOTIFICATION_TOPIC_ARN")
    if topic_arn:
        ex, sc = extract_summary, scan_summary
        lines = [
            "AWS Services Lifecycle Tracker - Refresh Complete",
            "",
            f"Mode: {spec['mode']}   Origin: {spec['refresh_origin']}   Run: {run['run_id']}",
            f"Started:  {run['started_at']}",
            f"Finished: {summary['finished_at']}",
            "",
            f"Facts (web extraction): {ex['succeeded']}/{ex['total']} services succeeded, "
            f"{ex['items_extracted']} items",
        ]
        if ex["failed"]:
            lines.append("  Failed: " + ", ".join(ex["failed"]))
        lines += [
            f"Inventory (account scan): {sc['cells_succeeded']}/{sc['cells_total']} scanner cells succeeded, "
            f"{sc['items_discovered']} assets discovered, {sc['needs_attention']} need attention",
        ]
        if sc["failed_cells"]:
            lines.append("  Failed: " + ", ".join(sc["failed_cells"]))
        if reconcile_result:
            lines.append(f"  Reconciled: {reconcile_result.get('items_saved', 0)} saved, "
                         f"{reconcile_result.get('stale_removed', 0)} stale removed")
            health = reconcile_result.get("health") or {}
            lines.append(f"  AWS Health: {health.get('flagged_resources', 0)} resources flagged"
                         if health.get("available") else f"  AWS Health: unavailable ({health.get('reason', 'n/a')})")
        boto3.client("sns", region_name=run["function_region"]).publish(
            TopicArn=topic_arn,
            Subject="AWS Lifecycle Tracker - Refresh Complete",
            Message="\n".join(lines),
        )
        summary["notified"] = True
    return summary


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

_EXTRACT_RETRY = StepConfig(retry_strategy=create_retry_strategy(RetryStrategyConfig(
    max_attempts=3, initial_delay=Duration.from_seconds(10), max_delay=Duration.from_minutes(2))))
_SCAN_RETRY = StepConfig(retry_strategy=create_retry_strategy(RetryStrategyConfig(
    max_attempts=2, initial_delay=Duration.from_seconds(5), max_delay=Duration.from_seconds(30))))

# A single bad service or scanner must never abort the run: the summary reports it.
_TOLERATE_ALL = CompletionConfig(tolerated_failure_percentage=100)

EMPTY_EXTRACT = {"total": 0, "succeeded": 0, "failed": [], "items_extracted": 0, "results": []}
EMPTY_SCAN = {"cells_total": 0, "cells_succeeded": 0, "failed_cells": [], "items_discovered": 0,
              "needs_attention": 0, "scanned_service_keys": []}


@durable_execution
def handler(event: dict, context: DurableContext) -> dict:
    spec = normalize_spec(event)
    run = context.step(start_run(spec), name="start-run")

    # Phase 1: facts
    extract_summary = dict(EMPTY_EXTRACT)
    if spec["mode"] in ("full", "extract"):
        def _extract(ctx: DurableContext, service: str, index: int, _all) -> dict:
            return ctx.step(extract_cell(service, spec["refresh_origin"]),
                            name=f"extract-{service}", config=_EXTRACT_RETRY)

        batch = context.map(run["services"], _extract, name="extract",
                            config=MapConfig(max_concurrency=DEFAULT_CONCURRENCY,
                                             completion_config=_TOLERATE_ALL))
        extract_summary = summarize_extract(run["services"], batch)

    # Phase 2: impact
    scan_summary = dict(EMPTY_SCAN)
    reconcile_result = {}
    if spec["mode"] in ("full", "scan"):
        cells = scan_cells_for(spec, run["regions"])

        def _scan(ctx: DurableContext, cell: dict, index: int, _all) -> dict:
            return ctx.step(scan_cell(cell), name=f"scan-{cell['label']}-{cell['region']}",
                            config=_SCAN_RETRY)

        batch = context.map(cells, _scan, name="scan",
                            config=MapConfig(max_concurrency=DEFAULT_CONCURRENCY,
                                             completion_config=_TOLERATE_ALL))
        scan_summary, items, scanned_keys = summarize_scan(cells, batch)
        reconcile_result = context.step(
            reconcile_inventory(run["run_id"], items, scanned_keys), name="reconcile-inventory")

    return context.step(
        summarize_and_notify(run, spec, extract_summary, scan_summary, reconcile_result),
        name="summarize-and-notify")


# ---------------------------------------------------------------------------
# Batch result folding (pure)
# ---------------------------------------------------------------------------

def _results_by_index(batch, count: int) -> List:
    """BatchResult.all as a list aligned to the input index; None where the
    item failed (or was never started because a completion policy fired)."""
    aligned = [None] * count
    for item in batch.all:
        if item.status is BatchItemStatus.SUCCEEDED and item.result is not None:
            aligned[item.index] = item.result
    return aligned


def summarize_extract(services: List[str], batch) -> dict:
    results, failed, items_total = [], [], 0
    for service, result in zip(services, _results_by_index(batch, len(services))):
        if result is None:
            result = {"service_name": service, "success": False, "items_extracted": 0,
                      "error": "step failed after retries", "duration": 0.0}
        results.append(result)
        if result["success"]:
            items_total += result["items_extracted"]
        else:
            failed.append(service)
    return {
        "total": len(services),
        "succeeded": len(services) - len(failed),
        "failed": failed,
        "items_extracted": items_total,
        "results": results,
    }


def summarize_scan(cells: List[Dict], batch):
    items, scanned_keys, failed_cells = [], [], []
    for cell, result in zip(cells, _results_by_index(batch, len(cells))):
        if result is not None:
            items.extend(result["items"])
            scanned_keys.extend(result["service_keys"])
        else:
            failed_cells.append(f"{cell['label']}@{cell['region']}")
    needs_attention = sum(1 for i in items if i.get("status") in ("deprecated", "end_of_life"))
    summary = {
        "cells_total": len(cells),
        "cells_succeeded": len(cells) - len(failed_cells),
        "failed_cells": failed_cells,
        "items_discovered": len(items),
        "needs_attention": needs_attention,
        "scanned_service_keys": sorted(set(scanned_keys)),
    }
    return summary, items, scanned_keys
