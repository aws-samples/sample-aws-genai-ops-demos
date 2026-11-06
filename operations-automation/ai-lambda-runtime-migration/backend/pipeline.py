"""
Discover + Assess as ONE AWS Lambda durable function (v1.1 Phase 1).

    discover (Trusted Advisor + Lambda/CloudWatch + Nova priority, per
    account x region)  ->  assess (download code + Nova migration assessment,
    per function)

One checkpointed execution per scan, started by the "Scan functions" button
(or a scheduled trigger), so closing the browser never interrupts a run.

Replay model rules (aws-lambda-durable-functions skill): every piece of
non-determinism — clock reads, uuid, Bedrock/boto3/DynamoDB calls — lives INSIDE
a step, so replays return checkpointed results instead of re-executing. Code
outside steps is pure transformation of step outputs. Step results are bounded
(counts/keys/labels); the full function list is written to DynamoDB inside the
step and never crosses a checkpoint. No secrets are returned from steps.

Input contract:
    {
      "scope": {
        "tags":    [{"key": "BU", "values": ["payments"]}],
        "regions": ["us-east-1"],
        "accounts":[{"id": "123456789012", "name": "payments-prod"}]
      },
      "refresh_origin": "manual"
    }
"""

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import List

from aws_durable_execution_sdk_python import durable_execution, durable_step, DurableContext, StepContext
from aws_durable_execution_sdk_python.config import Duration, MapConfig, CompletionConfig, StepConfig
from aws_durable_execution_sdk_python.retries import RetryStrategyConfig, create_retry_strategy

from aws_utils import get_region  # staged copy of shared/utils/aws_utils.py
from common import results_by_index
import discover
import assess
import support
import org_accounts

logger = logging.getLogger(__name__)

MAX_REGIONS = 5
DISCOVER_CONCURRENCY = 5
ASSESS_CONCURRENCY = 5
VALID_ORIGINS = ("manual", "scheduled")
_REGION_RE = re.compile(r"^[a-z]{2}-[a-z]+-\d$")
_ACCOUNT_RE = re.compile(r"^\d{12}$")


# ---------------------------------------------------------------------------
# Spec validation (pure; runs BEFORE any step so bad input never checkpoints)
# ---------------------------------------------------------------------------

class SpecValidationError(ValueError):
    """Raised by normalize_spec with the offending field named. The API Lambda
    maps this to 400 {error: '<field>: <reason>'} before invoking the pipeline."""

    def __init__(self, field: str, reason: str):
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


def normalize_spec(event: dict) -> dict:
    """Validate and default the pipeline input. Pure. Raises SpecValidationError."""
    event = event or {}
    if not isinstance(event, dict):
        raise SpecValidationError("event", "must be a JSON object")

    scope = event.get("scope") or {}
    if not isinstance(scope, dict):
        raise SpecValidationError("scope", "must be an object")

    # --- tags ---
    tags_in = scope.get("tags")
    tags: List[dict] = []
    if tags_in is not None:
        if not isinstance(tags_in, list):
            raise SpecValidationError("scope.tags", "must be a list of objects")
        for i, flt in enumerate(tags_in):
            if not isinstance(flt, dict):
                raise SpecValidationError(f"scope.tags[{i}]", "must be an object")
            key = flt.get("key")
            if not isinstance(key, str) or not key.strip():
                raise SpecValidationError(f"scope.tags[{i}].key", "required non-empty string")
            values = flt.get("values", [])
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise SpecValidationError(f"scope.tags[{i}].values", "must be a list of strings")
            tags.append({"key": key, "values": values})

    # --- accounts ---
    accounts_in = scope.get("accounts")
    accounts: List[dict] = []
    if accounts_in is not None:
        if not isinstance(accounts_in, list):
            raise SpecValidationError("scope.accounts", "must be a list of objects")
        for i, acct in enumerate(accounts_in):
            if not isinstance(acct, dict):
                raise SpecValidationError(f"scope.accounts[{i}]", "must be an object")
            acct_id = acct.get("id")
            if not isinstance(acct_id, str) or not _ACCOUNT_RE.match(acct_id):
                raise SpecValidationError(f"scope.accounts[{i}].id", "must be a 12-digit account id")
            name = acct.get("name")
            if name is not None and not isinstance(name, str):
                raise SpecValidationError(f"scope.accounts[{i}].name", "must be a string")
            accounts.append({"id": acct_id, "name": name or ""})

    # --- regions ---
    regions_in = scope.get("regions")
    regions: List[str] = []
    if regions_in is not None:
        if not isinstance(regions_in, list) or not all(isinstance(r, str) for r in regions_in):
            raise SpecValidationError("scope.regions", "must be a list of strings")
        for i, r in enumerate(regions_in):
            if not _REGION_RE.match(r):
                raise SpecValidationError(f"scope.regions[{i}]", f"invalid region token '{r}'")
        if len(regions_in) > MAX_REGIONS:
            raise SpecValidationError(
                "scope.regions", f"at most {MAX_REGIONS} regions allowed, got {len(regions_in)}")
        regions = list(regions_in)

    # --- refresh_origin ---
    origin = event.get("refresh_origin", "manual")
    if not isinstance(origin, str) or origin not in VALID_ORIGINS:
        raise SpecValidationError("refresh_origin", f"must be one of {VALID_ORIGINS}")

    return {
        "tags": tags,
        "accounts": accounts,           # [] => hub account only
        # [] => the single deploy region (defaulted in start_run). TA is
        # account-wide, so one cell per account covers every region. A non-empty
        # list is an explicit user choice and is honored as-is.
        "regions": regions,
        "refresh_origin": origin,
    }


def discover_cells_for(spec: dict, regions: List[str], accounts: List[dict]) -> List[dict]:
    """Build one (account, region) discover cell per account x region. Pure.

    `accounts` is the TA-eligible candidate list from the support-tier step. An
    empty list therefore means "no account is TA-eligible" => NO discover cells
    (never a silent fall back to scanning the hub, which the step may itself have
    ruled out). The single-account hub default lives upstream in start_run /
    resolve_accounts, which always resolves run["accounts"] to at least the hub.
    Each cell carries the tag filter so discover_scope can honor it, and the
    account's Support tier (threaded from the support-tier step) so discover does
    not re-query the Support API — it is only ever handed TA-eligible accounts.
    """
    cells = []
    for account in accounts:
        for region in regions:
            cells.append({
                "account_id": account.get("id", ""),
                "account_name": account.get("name", ""),
                "region": region,
                "tags": spec["tags"],
                # Tier decided in the support-tier step; discover trusts it rather
                # than calling the Support API a second time. Candidates are always
                # TA-eligible, so default to "enterprise" when absent (e.g. a cell
                # built outside the fan-out in a test).
                "support_tier": account.get("tier", "enterprise"),
            })
    return cells


# ---------------------------------------------------------------------------
# Steps (all non-determinism lives here)
# ---------------------------------------------------------------------------

@durable_step
def start_run(step: StepContext, spec: dict) -> dict:
    """Mint the run identity once: run id, start time, region, resolved scope.

    The ONLY place uuid.uuid4() and datetime.now() are called.
    """
    own_region = get_region()
    # Trusted Advisor's deprecated-runtime check is ACCOUNT-WIDE: one call per
    # account returns functions across every region, so there is no region
    # fan-out. One discover cell per account is enough — keyed by the deploy
    # region — and discover_scope keys each returned function under its own true
    # region. An explicit regions list from the caller is still honored as-is
    # (e.g. to narrow a scan); when empty we default to the single deploy region.
    regions = spec["regions"] or [own_region]
    hub = org_accounts.hub_account_id(own_region)
    resolved = org_accounts.resolve_accounts(spec["accounts"], hub, own_region)
    # The hub carries its REAL id (resolve_accounts). The empty-id entry is the
    # last-resort default only, for when STS could not even name the hub.
    accounts = resolved["accounts"] or [{"id": "", "name": ""}]
    return {
        "run_id": str(uuid.uuid4()),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "function_region": own_region,
        "regions": regions,
        "accounts": accounts,
        "accounts_source": resolved["source"],
        "accounts_errors": resolved["errors"],
        "discover_total": len(regions) * len(accounts),
    }


@durable_step
def support_tier_step(step: StepContext, account: dict, region: str) -> dict:
    """Infer ONE account's Support tier (its own durable unit, isolated retry).

    The Support API is the flakiest call in the pipeline, so each account gets
    its own step: one account throttling or erroring retries alone and never
    aborts the run. Reuses discover.support_tier via support.determine_tier — no
    duplicated ceiling logic. Returns the bounded per-account record
    {account_id, account_name, tier, ta_eligible, reason}.
    """
    account_id = account.get("id", "")
    session = discover.session_for_account(account_id, region)
    return support.determine_tier(session, account_id, account.get("name", ""), region)


@durable_step
def discover_scope_step(step: StepContext, cell: dict, run_id: str, when: str) -> dict:
    """Discover one (account, region): TA + enrich + Nova priority, persist rows."""
    session = discover.session_for_account(cell.get("account_id", ""), cell["region"])
    return discover.discover_scope(session, cell, run_id, when)


@durable_step
def list_discovered_step(step: StepContext, scopes: List[dict]) -> List[dict]:
    """Read back the active rows for the scanned scopes (bounded identifiers)."""
    region = scopes[0]["region"] if scopes else get_region()
    session = discover.session_for_account("", region)
    return discover.list_discovered_arns(session, scopes)


@durable_step
def assess_one_step(step: StepContext, target: dict, when: str) -> dict:
    """Assess one function: download code + Nova migration assessment."""
    session = discover.session_for_account(target.get("account_id", ""), target["region"])
    return assess.assess_one(session, target, when)


@durable_step
def summarize_step(step: StepContext, run: dict, spec: dict,
                   disc_summary: dict, asmt_summary: dict, support_summary: dict) -> dict:
    """Assemble the bounded run summary.

    support_summary carries the per-account tier records from the support-tier
    step. Non-eligible accounts (Basic/Developer/unknown) never reach discover,
    so they are surfaced here as accounts_not_checked — preserving today's
    behavior that such accounts are reported, never silently dropped.
    """
    tiers = support_summary.get("tiers", [])
    finished_at = datetime.now(timezone.utc).isoformat()

    # Persist ONE immutable rollup row for this run inside the step (the measurement
    # series the Statistics charts read). Counts come from a fresh fleet snapshot of
    # the 'fn#' rows this run just updated. The rollup row is written to DynamoDB
    # here and NEVER returned, so the step's result stays bounded (counts only). A
    # rollup failure must not fail an otherwise-successful run, but it must be
    # logged: a silent failure here is invisible to the UI (the Statistics page
    # would just say "not enough history").
    try:
        inventory_rows = discover.read_fleet_inventory()
        rollup = discover.build_rollup(run, disc_summary, support_summary,
                                       inventory_rows, finished_at)
        discover.put_rollup(rollup)
    except Exception:  # best effort, never fail the summary
        logger.exception("rollup write failed; the Statistics series will miss this run")

    return {
        "run_id": run["run_id"],
        "refresh_origin": spec["refresh_origin"],
        "started_at": run["started_at"],
        "finished_at": finished_at,
        "regions": run["regions"],
        "accounts": [a["id"] for a in run.get("accounts", [])],
        "accounts_source": run.get("accounts_source"),
        "accounts_errors": run.get("accounts_errors", []),
        "discover": disc_summary,
        "assess": asmt_summary,
        "accounts_tiers": tiers,
        "accounts_not_checked": [
            {"account_id": t["account_id"], "account_name": t["account_name"],
             "tier": t["tier"], "reason": t.get("reason")}
            for t in tiers if not t.get("ta_eligible")
        ],
        "support_errors": support_summary.get("support_errors", []),
    }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

# TA can be flaky: let the durable step retry replace the old in-code sleeps.
_DISCOVER_RETRY = StepConfig(retry_strategy=create_retry_strategy(RetryStrategyConfig(
    max_attempts=3, initial_delay=Duration.from_seconds(5), max_delay=Duration.from_seconds(30))))
_ASSESS_RETRY = StepConfig(retry_strategy=create_retry_strategy(RetryStrategyConfig(
    max_attempts=2, initial_delay=Duration.from_seconds(5), max_delay=Duration.from_seconds(30))))
# The Support API throttles aggressively; each account's tier check retries alone.
_SUPPORT_RETRY = StepConfig(retry_strategy=create_retry_strategy(RetryStrategyConfig(
    max_attempts=3, initial_delay=Duration.from_seconds(5), max_delay=Duration.from_seconds(30))))

# A single bad function or account must never abort the run: the summary reports it.
_TOLERATE_ALL = CompletionConfig(tolerated_failure_percentage=100)


@durable_execution
def handler(event: dict, context: DurableContext) -> dict:
    spec = normalize_spec(event)                      # pure, raises before any step
    run = context.step(start_run(spec), name="start-run")
    when = run["started_at"]

    # SUPPORT TIER — one durable unit per account, decided ONCE before discover.
    # Trusted Advisor needs a Business/Enterprise plan; this step infers each
    # account's tier (reusing discover.support_tier) so discover only scans the
    # eligible accounts. A Support-API error on one account is isolated (per-step
    # retry) and tolerated (never aborts the run); the account is simply reported
    # as not TA-eligible. The home region is account-wide, so run["regions"][0]
    # is only a seed — support_home_region maps it to the partition endpoint.
    support_region = run["regions"][0]

    def _support(ctx: DurableContext, account: dict, index: int, _all) -> dict:
        return ctx.step(support_tier_step(account, support_region),
                        name=f"support-{account['id'] or 'hub'}",
                        config=_SUPPORT_RETRY)

    sup_batch = context.map(run["accounts"], _support, name="support",
                            config=MapConfig(max_concurrency=DISCOVER_CONCURRENCY,
                                             completion_config=_TOLERATE_ALL))
    sup = select_candidates(run["accounts"], sup_batch)

    # DISCOVER — one cell per (candidate account, region), per-cell failure
    # tolerated. Only TA-eligible accounts are scanned; the rest are kept in
    # sup["tiers"] and reported "not checked" by the summary.
    cells = discover_cells_for(spec, run["regions"], sup["candidates"])

    def _discover(ctx: DurableContext, cell: dict, index: int, _all) -> dict:
        return ctx.step(discover_scope_step(cell, run["run_id"], when),
                        name=f"discover-{cell['account_id'] or 'hub'}-{cell['region']}",
                        config=_DISCOVER_RETRY)

    disc_batch = context.map(cells, _discover, name="discover",
                             config=MapConfig(max_concurrency=DISCOVER_CONCURRENCY,
                                              completion_config=_TOLERATE_ALL))
    disc_summary = summarize_discover(cells, disc_batch)

    # ASSESS — fan out over the active functions discovered in successful scopes.
    arns = context.step(list_discovered_step(disc_summary["scanned_scopes"]), name="list-discovered")

    def _assess(ctx: DurableContext, target: dict, index: int, _all) -> dict:
        return ctx.step(assess_one_step(target, when),
                        name=f"assess-{target['account_id'] or 'hub'}-{index}",
                        config=_ASSESS_RETRY)

    asmt_batch = context.map(arns, _assess, name="assess",
                             config=MapConfig(max_concurrency=ASSESS_CONCURRENCY,
                                              completion_config=_TOLERATE_ALL))
    asmt_summary = summarize_assess(arns, asmt_batch)

    support_summary = {"tiers": sup["tiers"], "support_errors": sup["support_errors"]}
    return context.step(summarize_step(run, spec, disc_summary, asmt_summary, support_summary),
                        name="summarize")


# ---------------------------------------------------------------------------
# Batch result folding (pure)
# ---------------------------------------------------------------------------

def select_candidates(accounts: List[dict], batch) -> dict:
    """Fold the support-tier map into the discover candidate list + full tiers.

    Pure. Returns:
      - "candidates": the TA-eligible accounts (Business/Enterprise), each shaped
        {id, name, tier} ready for discover_cells_for — these are the ONLY
        accounts discover scans.
      - "tiers": the per-account record {account_id, account_name, tier,
        ta_eligible, reason} for EVERY account (eligible or not), so the summary
        can report non-eligible accounts as "not checked" instead of dropping them.
      - "support_errors": the account ids (or "hub") whose tier step failed — the
        error is isolated here, never raised, and the account is excluded from
        candidates (we cannot assume TA eligibility we could not confirm).
    """
    accounts = accounts or [{"id": "", "name": ""}]
    candidates, tiers, support_errors = [], [], []
    for account, result in zip(accounts, results_by_index(batch, len(accounts))):
        account_id = account.get("id", "")
        account_name = account.get("name", "")
        if result is None:
            # The Support-API call for this account failed every retry: isolate
            # and report it. It is NOT a candidate and is surfaced as unknown.
            support_errors.append(account_id or "hub")
            tiers.append({"account_id": account_id, "account_name": account_name,
                          "tier": "unknown", "ta_eligible": False,
                          "reason": "Support tier check failed"})
            continue
        tiers.append(result)
        if result.get("ta_eligible"):
            candidates.append({"id": account_id, "name": account_name,
                               "tier": result.get("tier", "enterprise")})
    return {"candidates": candidates, "tiers": tiers, "support_errors": support_errors}


def summarize_discover(cells: List[dict], batch) -> dict:
    """Fold the discover map into bounded facts + the scopes that succeeded.

    A scope that failed drops out of scanned_scopes, so Assess never fans out
    over a scope whose inventory was not refreshed."""
    scopes, failed_cells = [], []
    discovered = rekeyed = 0
    for cell, result in zip(cells, results_by_index(batch, len(cells))):
        if result is not None:
            acct = result.get("account_id", cell.get("account_id", ""))
            # Trusted Advisor is account-wide, so one cell's discover writes rows
            # across EVERY region the account's deprecated functions live in
            # (result.regions_seen), not just cell["region"]. Assess must read
            # those same regions back, so expand the scope to regions_seen —
            # otherwise list-discovered looks only in the cell region and finds
            # nothing to assess (the functions were written under other regions).
            for r in result.get("regions_seen") or [cell["region"]]:
                scopes.append({"account_id": acct, "region": r})
            discovered += result.get("discovered", 0)
            rekeyed += result.get("rekeyed", 0)
        else:
            failed_cells.append(f"{cell.get('account_id') or 'hub'}/{cell['region']}")
    # Dedupe (account, region): multiple cells can report overlapping
    # regions_seen, and list-discovered must not read the same rows twice.
    scopes = [{"account_id": a, "region": r} for (a, r) in sorted({(s["account_id"], s["region"]) for s in scopes})]
    scanned_accounts = sorted({sc["account_id"] for sc in scopes})
    all_accounts = {c.get("account_id", "") for c in cells}
    return {
        "cells_total": len(cells),
        "cells_succeeded": len(cells) - len(failed_cells),
        "failed_cells": failed_cells,
        "discovered": discovered,
        "rekeyed": rekeyed,   # rows moved to a new item_key (key-shape change), not retired
        "scanned_scopes": scopes,
        "accounts_scanned": scanned_accounts,
        "accounts_failed": sorted(all_accounts - set(scanned_accounts)),
    }


def summarize_assess(targets: List[dict], batch) -> dict:
    """Fold the assess map into bounded counts."""
    assessed, skipped, failed = 0, 0, 0
    for target, result in zip(targets, results_by_index(batch, len(targets))):
        if result is None:
            failed += 1
        elif result.get("status") == "SKIPPED":
            skipped += 1
        else:
            assessed += 1
    return {
        "total": len(targets),
        "assessed": assessed,
        "skipped": skipped,
        "failed": failed,
    }
