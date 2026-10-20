"""
Migration durable orchestrator (Phase 2).

One Migrate click = one batch durable execution. It runs these durable steps
per function, in order:

    prepare-batch -> [ resolve-<i> -> transform-<i> [-> settle-<i>] ] per function

- prepare-batch mints the batch identity and records the execution ARN.
- resolve-<i> turns the function's tags into a clonable CodeCommit URL, verifies
  the source repo (and branch) exist, picks the target runtime and writes the
  QUEUED mig# item.
- transform-<i> is a durable wait_for_callback: its submitter renders the AWS
  Transform CONFIG (transform-config.yaml, seeded with the Phase-1 assessment)
  to S3 - the only S3 write here, it is never function source - starts the
  CodeBuild job that runs AWS Transform, and persists the callback id on the
  mig# item. The execution stays alive across the build with a durable callback;
  observed fixture builds take 1-4 minutes and every build is bounded by
  BUILD_TIMEOUT_MINUTES (timeoutInMinutesOverride). The build pushes the upgrade
  branch when the transformation produced a diff. An EventBridge rule on the
  build's terminal state invokes backend/build_complete.py, which writes
  BRANCH_READY / NO_CHANGES / FAILED on the mig# item and completes the callback
  with the build's result.json. The pushed branch is where the flow ENDS: nothing
  here opens a pull request, `main` is never written.
- settle-<i> runs only when the orchestrator itself must write the terminal
  status (callback timeout, submitter failure, rejected build result).

Mirrors backend/pipeline.py's durable style: every piece of non-determinism
(clock, boto3, DynamoDB, S3, CodeBuild, CodeCommit) lives INSIDE a step; the
code outside steps is pure. Step results are bounded identifiers; the large
assessment text and the rendered YAML are written to S3 inside the step and
never cross a durable checkpoint.

Migration state lives in a SEPARATE DynamoDB item keyed
mig#<account>#<region>#<canonical_arn> in the same table, so a Phase-1 scan
refresh (which rewrites fn# rows) never clobbers it. Its migration_status enum:
QUEUED / IN_PROGRESS (live), REJECTED (triaged out before any build: no repo
tag, source/branch not found, unsupported runtime, access denied - a business
rejection, not a transform failure), FAILED (a real failure: the build could
not start, the transform failed, the push failed, timed out), NO_CHANGES (the
transform found nothing to change: neutral, not a failure), BRANCH_READY (the
upgrade branch is pushed; migration_branch names it). Any status outside
QUEUED / IN_PROGRESS is settled (re-migratable); a value this build does not
know is passed through unchanged. REJECTED is deliberately distinct
from FAILED so an untagged function never counts toward the transform-failure
numbers. Every mig# write is a field-scoped update_item (never a whole-item PutItem); the
two create paths are unguarded, every later transition is guarded on the live
statuses (_LIVE_VALUES). Before either create, resolve_and_verify reads the
mig# item (GetItem) and rejects the function with ALREADY_IN_PROGRESS - without
writing - when a live QUEUED / IN_PROGRESS item owned by another batch exists,
so a second batch over the same function can never clobber the batch that owns
it. A live status is bounded in time: the item is not live when it carries this
batch's own id (replay), is QUEUED with no build id for longer than
QUEUED_STALE_AFTER, or is IN_PROGRESS with no writer for longer than
IN_PROGRESS_STALE_AFTER, so a function can never be locked out permanently.
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

from aws_durable_execution_sdk_python import (
    durable_execution, durable_step, durable_wait_for_callback, DurableContext, StepContext,
    CallbackError, CallbackTimeoutError,
)
from aws_durable_execution_sdk_python.config import (
    Duration, MapConfig, CompletionConfig, StepConfig, WaitForCallbackConfig,
)
from aws_durable_execution_sdk_python.retries import RetryStrategyConfig, create_retry_strategy
# WaitForCallbackContext is NOT exported at the package root.
from aws_durable_execution_sdk_python.types import WaitForCallbackContext

from aws_utils import get_region, get_transform_region  # staged copy of shared/utils/aws_utils.py
from common import results_by_index, DIFF_SUMMARY_MAX_CHARS, FAILURE_DETAIL_MAX_CHARS
import api
import discover
import transform_runtimes

logger = logging.getLogger(__name__)

TABLE_NAME = os.environ.get("TABLE_NAME", "lambda-runtime-migration")
BUCKET_NAME = os.environ.get("BUCKET_NAME", "")
PROJECT_NAME = os.environ.get("PROJECT_NAME")  # CodeBuild project, set by TransformStack

# One Migrate click = one batch durable execution fanning out over the selected ARNs.
MIGRATE_CONCURRENCY = 5         # map max_concurrency: <=5 CodeBuild StartBuild in flight at once.
MAX_FUNCTIONS_PER_RUN = 50      # per-run cap: selections beyond this are reported "not started
                                # (exceeds per-run cap)", never silently dropped.

# CodeBuild timeoutInMinutesOverride. Observed on the mock fixture (all builds
# of the transform project to date): successful atx runs take 1-4 minutes. 60
# min is ~20x the slowest observed build and leaves room for a real multi-file
# repository; it is a per-build ceiling, not an expected duration.
BUILD_TIMEOUT_MINUTES = 60
# CodeBuild queuedTimeoutInMinutesOverride. Queue time is NOT covered by the
# build timeout (two independent bounds in the StartBuild model; the project
# default is 8 h). Bounding it is what makes every downstream window provable:
# a build that sits on the account's concurrent-build limit is killed by
# CodeBuild after this many minutes and reported through build_complete.
QUEUE_TIMEOUT_MINUTES = 10
# EventBridge delivery + build_complete runtime after the build ends.
CALLBACK_SLACK_MINUTES = 5
TRANSFORM_CALLBACK_TIMEOUT = Duration.from_minutes(
    QUEUE_TIMEOUT_MINUTES + BUILD_TIMEOUT_MINUTES + CALLBACK_SLACK_MINUTES)       # 75 min, derived
# Worst-case wall time for the step retries around the wait (resolve 3 attempts
# and the settle step, <=30 s backoff each, plus API latency).
STEP_RETRY_SLACK_MINUTES = 10
# An IN_PROGRESS item whose migration_updated_at is older than this has no live
# writer left (see _is_live_migration). Derived, never a literal.
IN_PROGRESS_STALE_AFTER = timedelta(minutes=QUEUE_TIMEOUT_MINUTES + BUILD_TIMEOUT_MINUTES
                                    + CALLBACK_SLACK_MINUTES + STEP_RETRY_SLACK_MINUTES)   # 85 min

# Placeholder bindings for every guarded write on a live item (_set_fields only
# binds its own :vN values; conditions must pass these as extra_values).
_LIVE_VALUES = {":queued": "QUEUED", ":inprogress": "IN_PROGRESS"}

# A function whose mig# item is in one of these states is owned by a running
# batch and must not be re-queued: a second batch would StartBuild twice and its
# writes would clobber the live item. resolve_and_verify rejects it with
# ALREADY_IN_PROGRESS instead. Settled states (BRANCH_READY, REJECTED, FAILED,
# NO_CHANGES, or any value this build does not know) and absent items may be
# (re-)migrated. A live
# status is bounded in time (QUEUED_STALE_AFTER, IN_PROGRESS_STALE_AFTER): no
# writer can be guaranteed to run (operator stop, durable ceiling, double
# fault), so liveness is proven by a fresh migration_updated_at, never assumed.
LIVE_MIGRATION_STATUSES = ("QUEUED", "IN_PROGRESS")

# A QUEUED item with NO build id is only live while the batch that wrote it is
# still between resolve and the transform submitter's StartBuild. If the
# submitter exhausted its retries the item stays QUEUED-without-build forever;
# after this bound the guard lets a new batch re-queue it. Comfortably longer
# than resolve (3 attempts, <=30 s backoff) + submitter (2 attempts, <=30 s
# backoff), still well under 30 minutes.
QUEUED_STALE_AFTER = timedelta(minutes=30)

# additionalPlanContext bound on the advisory paragraph (imported by the unit
# test so renderer and test share one source of truth). The directive paragraph
# is never truncated.
MAX_CONTEXT = 4000

# Header of the advisory paragraph carrying the Phase-1 findings.
_ADVISORY_HEADER = "Phase 1 assessment (advisory):"


def _transform_fallback_region() -> str:
    """The AWS Transform control-plane region for this deploy region, from the
    shared helper (staged aws_utils.py). Used as the build's fallback only: the
    build tries the deploy region first."""
    return get_transform_region(get_region())


def _table():
    """The single inventory table in the Lambda's own region.

    The orchestrator may read a function in any region, but the one table lives
    in the deploy region (same rule as assess._table / discover._table)."""
    return boto3.resource("dynamodb", region_name=get_region()).Table(TABLE_NAME)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------

def repository_url(repo_name: str, region: str = None) -> str:
    """Derive the CodeCommit HTTPS clone URL for a resolved repository name.

    https://git-codecommit.<region>.amazonaws.com/v1/repos/<repo_name>
    Region defaults to the Lambda's own region (never hardcoded). Pure given a
    region. The orchestrator derives this instead of importing the mock stack's
    clone-URL output, so it stays independent of the opt-in mock stack.
    """
    region = region or get_region()
    return f"https://git-codecommit.{region}.amazonaws.com/v1/repos/{repo_name}"


def _cap_advisory(block: str, budget: int) -> str:
    """Cap the advisory paragraph at `budget` chars, cutting at the last item
    (newline) or sentence ('. ') boundary when one exists past the header, and
    appending an ellipsis. Empty when the budget cannot hold the header and one
    item. Pure."""
    if len(block) <= budget:
        return block
    if budget <= len(_ADVISORY_HEADER) + 1:
        return ""
    cut = block[: budget - 1]
    boundary = max(cut.rfind("\n"), cut.rfind(". "))
    if boundary > len(_ADVISORY_HEADER):
        cut = cut[:boundary]
    return cut.rstrip() + "\u2026"


def render_additional_plan_context(fn_item: dict, family: str, target: str) -> str:
    """Compose additionalPlanContext as directives, the way AWS Transform
    documents the field for AWS-managed transformations (short imperative
    statements, not a report).

    Contract (the unit test pins this, not implementer taste):
      - Paragraph 1, every family: the SCOPE DIRECTIVE. 'Upgrade the AWS Lambda
        function "<name>" (handler <handler>, runtime <runtime>) in this
        repository to <target>. Only change what this function needs ...'. The
        name is the last segment of the canonical ARN; the handler / runtime
        parenthesis parts are omitted when the fn# row lacks them. Never
        truncated.
      - Paragraph 2, Python ONLY: the family contextPrefix (what a generic
        language upgrade cannot infer about a Lambda function). Node.js and
        .NET use Lambda-specific managed transformations and get none.
      - Paragraph 3: '<_ADVISORY_HEADER>' + one '- <Label>: <value>' line per
        NON-EMPTY finding: Deprecated APIs, Breaking changes, Dependency issues
        (lists joined with ', ') and Migration risks (a STRING, rendered as-is,
        NEVER ', '.join()'d). assessment_summary is never rendered. The whole
        paragraph is omitted when there is no finding, and is the only part
        capped (MAX_CONTEXT, cut at an item/sentence boundary + ellipsis).
    Pure.
    """
    fn_item = fn_item or {}

    canonical = discover.canonical_arn(fn_item.get("function_arn", "") or "")
    parts = canonical.split(":")
    name = parts[6] if len(parts) >= 7 else canonical
    handler = str(fn_item.get("handler") or "").strip()
    runtime = str(fn_item.get("runtime") or "").strip()
    facts = [f"handler {handler}" if handler else "", f"runtime {runtime}" if runtime else ""]
    facts_text = ", ".join(f for f in facts if f)
    subject = f'the AWS Lambda function "{name}"' if name else "the AWS Lambda function"
    if facts_text:
        subject += f" ({facts_text})"
    directive = (
        f"Upgrade {subject} in this repository to {target}. "
        "Only change what this function needs: its code, its dependencies and its "
        "runtime setting in the infrastructure template. Leave other functions in "
        "the repository untouched."
    )

    def _array_line(label, key):
        values = fn_item.get(key)
        if not isinstance(values, (list, tuple)):
            return None
        rendered = [str(v).strip() for v in values if str(v).strip()]
        if not rendered:
            return None
        return f"- {label}: {', '.join(rendered)}"

    def _scalar_line(label, value):
        if value is None:
            return None
        text = str(value).strip()
        # The assessment prompt asks for the literal "none" when nothing is at
        # risk: that is "no finding", not an advisory line for AWS Transform.
        if not text or text.lower() == "none":
            return None
        return f"- {label}: {text}"

    findings = [ln for ln in (
        _array_line("Deprecated APIs", "deprecated_apis"),
        _array_line("Breaking changes", "breaking_changes"),
        _array_line("Dependency issues", "dependency_issues"),
        # migration_risks is a STRING in assess.py: render as scalar, never joined.
        _scalar_line("Migration risks", fn_item.get("migration_risks")),
    ) if ln]

    paragraphs = [directive]
    prefix = transform_runtimes.TRANSFORMS.get(family, {}).get("contextPrefix")
    if prefix:
        paragraphs.append(prefix)

    if findings:
        advisory = "\n".join([_ADVISORY_HEADER, *findings])
        advisory = _cap_advisory(advisory, MAX_CONTEXT)
        if advisory:
            paragraphs.append(advisory)

    return "\n\n".join(paragraphs)


def derive_job_id(mig_key: str, batch_id: str) -> str:
    """The per-run job id: the mig# key with separators flattened (path-safe as an
    S3 key segment and a git ref component) suffixed with the batch id's first 8
    hex chars, so every batch migrating the same function gets its own S3
    prefix, upgrade branch and CodeCommit idempotency token. Deterministic
    across replays (both inputs are checkpointed values). Pure."""
    return f"{re.sub(r'[^A-Za-z0-9]+', '-', mig_key).strip('-')}-{batch_id[:8]}"


_BUILD_RESULT_STATUSES = ("TRANSFORMED", "NO_CHANGES", "FAILED")


def _parse_failed(detail: str) -> dict:
    return {"ok": False, "status": "FAILED", "failure_code": "TRANSFORM_FAILED",
            "branch": "", "diff_summary": "", "parse_failed": True, "detail": detail}


def parse_build_result(raw) -> dict:
    """Decode the raw callback string build_complete sent. Pure.

    `parse_failed` tells _migrate_one to run the guarded settle step. For a
    well-formed FAILED payload build_complete has already written FAILED, and
    for a TRANSFORMED-without-branch payload build_complete wrote
    FAILED/TRANSFORM_FAILED itself (same predicate), so the settle is a guarded
    no-op in both cases; it still runs so a payload build_complete never saw
    (empty / unparseable callback) is settled. `failure_code` passes through
    as-is, so PUSH_FAILED flows to the unit result. Callback.result() returns
    None when the callback succeeded with no Result, so None/non-str is guarded
    before json.loads."""
    if raw is None or not isinstance(raw, str):
        return _parse_failed("empty build result")
    try:
        payload = json.loads(raw)
    except ValueError:
        return _parse_failed("unparseable build result")
    if not isinstance(payload, dict):
        return _parse_failed("unparseable build result")
    status = payload.get("status")
    if not isinstance(status, str) or status not in _BUILD_RESULT_STATUSES:
        return _parse_failed("unparseable build result")
    branch = payload.get("branch")
    branch = branch if isinstance(branch, str) else ""
    if status == "TRANSFORMED" and not branch:
        return _parse_failed("transformed without a branch")
    diff_summary = payload.get("diff_summary")
    diff_summary = diff_summary[:DIFF_SUMMARY_MAX_CHARS] if isinstance(diff_summary, str) else ""
    failure_code = payload.get("failure_code")
    failure_code = failure_code if isinstance(failure_code, str) else None
    return {
        "ok": status != "FAILED",
        "status": status,
        "branch": branch,
        "failure_code": failure_code,
        "diff_summary": diff_summary,
        "parse_failed": False,
        "detail": diff_summary,
    }


def _is_live_migration(existing: dict, batch_id: str, now: datetime = None) -> bool:
    """True when an existing mig# item is owned by a batch that is still running,
    so resolve_and_verify must reject with ALREADY_IN_PROGRESS. Pure (no I/O).

    Not live (the guard lets the function through):
    - no item, or a settled status (BRANCH_READY, REJECTED, FAILED, NO_CHANGES,
      or any value this build does not know): a REJECTED function is
      re-migratable once its tag is fixed;
    - the item is stamped with THIS batch's id: a durable replay of the step
      after its own QUEUED write must re-queue, not reject itself;
    - QUEUED with no migration_build_id and a migration_updated_at older than
      QUEUED_STALE_AFTER (or unparseable/missing): the owning batch gave up
      before StartBuild. Without this escape such an item would lock the
      function out of migration permanently;
    - IN_PROGRESS with a migration_updated_at older than IN_PROGRESS_STALE_AFTER
      (or unparseable/missing). Every legitimate writer of an IN_PROGRESS item
      (submitter flip, build_complete, settle-<i>) refreshes
      migration_updated_at within that window, and both CodeBuild phases are
      bounded by the StartBuild overrides the orchestrator owns, so a stale
      timestamp is proof of an absent writer (operator stop, durable ceiling,
      double fault). A live status is bounded in time.
    """
    status = existing.get("migration_status")
    if status not in LIVE_MIGRATION_STATUSES:
        return False
    if batch_id and existing.get("migration_batch_id") == batch_id:
        return False
    if status == "QUEUED" and not existing.get("migration_build_id"):
        return not _older_than(existing, QUEUED_STALE_AFTER, now)
    if status == "IN_PROGRESS":
        return not _older_than(existing, IN_PROGRESS_STALE_AFTER, now)
    return True


def _older_than(existing: dict, window: timedelta, now: datetime = None) -> bool:
    """True when migration_updated_at is missing/unparseable (no provable
    timestamp: cannot be shown live) or older than `window`."""
    try:
        updated_at = datetime.fromisoformat(str(existing.get("migration_updated_at") or ""))
    except ValueError:
        return True
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return now - updated_at > window


def _render_transform_config(transform_name: str, build_command: str,
                             additional_plan_context: str,
                             validation_commands: str = "") -> str:
    """Render transform-config.yaml. Pure (no I/O).

    Only non-empty fields are emitted: atx rejects an empty configuration value
    ('clientMetadata.configuration ... Member must have length >= 1'), so
    buildCommand/validationCommands are omitted when blank rather than written
    as "". validationCommands is the command atx runs after the transform to
    validate the upgrade (blog-backed field)."""
    body = (
        f'transformationName: "{transform_name}"\n'
        'codeRepositoryPath: "."\n'
    )
    if build_command:
        body += f'buildCommand: "{build_command}"\n'
    if validation_commands:
        body += f'validationCommands: "{validation_commands}"\n'
    body += "additionalPlanContext: |\n"
    indented = "\n".join(f"  {line}" for line in additional_plan_context.splitlines())
    return body + (indented + "\n" if indented else "")


# ---------------------------------------------------------------------------
# mig# writes — all field-scoped update_item, never a whole-item PutItem
# ---------------------------------------------------------------------------

# Per-run outcome fields on a mig# item. A re-queue REMOVEs them so a function
# migrated again never shows the previous run's failure code, previous upgrade
# branch or build (a NO_CHANGES item must not keep a TRANSFORM_FAILED code from
# the run before). Identity and request fields (repo, source branch, target...)
# are re-SET.
_RUN_OUTCOME_FIELDS = (
    "migration_failure_code", "migration_failure_detail",
    "migration_branch", "migration_build_id", "migration_callback_id",
    "migration_diff_summary",
)


def _set_fields(key: str, fields: dict, condition: str = None,
                extra_values: dict = None, remove: tuple = ()):
    """Field-scoped update_item: SET only the owned fields with #aN/:vN placeholders,
    optionally REMOVE the attributes named in `remove` (absent ones are a no-op).

    Mirrors assess.assess_one's field-scoped write. On a non-existent key,
    update_item creates the item with only these fields — which is exactly the
    create-on-first-write behavior the orchestrator relies on.
    """
    set_parts, attr_names, attr_values = [], {}, {}
    for idx, (k, v) in enumerate(fields.items()):
        pn, pv = f"#a{idx}", f":v{idx}"
        set_parts.append(f"{pn} = {pv}")
        attr_names[pn] = k
        attr_values[pv] = v
    remove_parts = []
    for idx, k in enumerate(remove):
        if k in fields:
            continue                      # SET wins; one attribute per clause
        pn = f"#r{idx}"
        remove_parts.append(pn)
        attr_names[pn] = k
    if extra_values:
        attr_values.update(extra_values)
    expression = "SET " + ", ".join(set_parts)
    if remove_parts:
        expression += " REMOVE " + ", ".join(remove_parts)
    kwargs = {
        "Key": {"item_key": key},
        "UpdateExpression": expression,
        "ExpressionAttributeNames": attr_names,
        "ExpressionAttributeValues": attr_values,
    }
    if condition:
        kwargs["ConditionExpression"] = condition
    _table().update_item(**kwargs)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_conditional_failure(exc: Exception) -> bool:
    return (isinstance(exc, ClientError)
            and (exc.response.get("Error") or {}).get("Code") == "ConditionalCheckFailedException")


def _set_live_fields(key: str, fields: dict, condition: str) -> bool:
    """A guarded write on a live item: _set_fields with the _LIVE_VALUES bindings.
    True on write, False when the guard rejected it (another writer settled the
    item first, or this is a replay); any other ClientError propagates."""
    # Bind ONLY the placeholders the condition references: DynamoDB rejects an
    # ExpressionAttributeValues entry the expression never uses
    # ("Value provided in ExpressionAttributeValues unused in expressions"), and
    # the submitter's condition is ":queued" alone while _LIVE_VALUES also
    # carries ":inprogress".
    used = {p: v for p, v in _LIVE_VALUES.items() if p in condition}
    try:
        _set_fields(key, fields, condition=condition, extra_values=used)
        return True
    except ClientError as exc:
        if _is_conditional_failure(exc):
            return False
        raise


# ---------------------------------------------------------------------------
# Steps (all non-determinism lives here)
# ---------------------------------------------------------------------------

@durable_step
def resolve_and_verify(step: StepContext, request: dict) -> dict:
    """Resolve the repository, verify it (and the branch) exist, pick the target.

    First reads the mig# item: when it is live (QUEUED / IN_PROGRESS, owned by
    ANOTHER batch and, for QUEUED-without-build, younger than QUEUED_STALE_AFTER;
    see _is_live_migration) the step early-exits with ALREADY_IN_PROGRESS and
    writes NOTHING. Otherwise, on a business rejection (no repo tag, source or
    branch not found, unsupported runtime, access denied) writes the UNGUARDED
    create terminal REJECTED mig# item (NOT FAILED: nothing was transformed, so
    it must not count toward transform failures) and signals the orchestrator to
    end. On success writes the UNGUARDED create QUEUED mig# item and returns the
    build inputs.
    """
    function_arn = request["function_arn"]
    execution_arn = request.get("execution_arn", "")
    batch_id = request.get("batch_id", "")

    canonical = discover.canonical_arn(function_arn)

    # Read the fn# item to recover account/region and tags/runtime/assessment.
    fn_item = None
    scan = api._scan_inventory("fn#")
    for row in scan:
        if discover.canonical_arn(row.get("function_arn", "")) == canonical:
            fn_item = row
            break

    # account/region come from the fn# item (hub rows may carry an empty
    # account_id when STS was unreachable at scan time), so the mig# key
    # byte-matches what the GET path composes.
    account_id = (fn_item or {}).get("account_id", "") or ""
    region = (fn_item or {}).get("region", "") or get_region()
    mig_key = discover.mig_item_key(account_id, region, canonical)

    # Duplicate-writer guard: a live QUEUED / IN_PROGRESS mig# item belongs to a
    # batch that is still running. Reject WITHOUT writing: writing REJECTED over
    # it (as _fail would) would clobber another batch's live item, and re-queuing
    # it would StartBuild twice. Settled states (BRANCH_READY, REJECTED, FAILED,
    # NO_CHANGES, or any value this build does not know) and absent items fall
    # through and may be re-migrated;
    # so do this batch's own item (durable replay) and a stale QUEUED item with
    # no build id (see _is_live_migration). Residual: the read and the QUEUED
    # create below are two calls, so two batches resolving the same function
    # within the same second can both pass; accepted for the demo.
    existing = _table().get_item(Key={"item_key": mig_key}).get("Item") or {}
    if _is_live_migration(existing, batch_id):
        return {
            "ok": False,
            "mig_key": mig_key,
            "failure_code": "ALREADY_IN_PROGRESS",
            "failure_detail": "A migration for this function is already queued or in progress.",
        }

    def _fail(code: str, detail: str) -> dict:
        # A pre-build business rejection: the function was triaged out, nothing
        # was transformed. REJECTED, never FAILED, so it does not inflate the
        # transform-failure counts (FAILED is reserved for start_build /
        # build_complete outcomes).
        _set_fields(mig_key, {
            "function_arn": canonical,
            "account_id": account_id,
            "region": region,
            "migration_status": "REJECTED",
            "migration_failure_code": code,
            "migration_failure_detail": detail,
            "migration_execution_arn": execution_arn,
            "migration_batch_id": batch_id,
            "migration_updated_at": _now(),
        })
        return {"ok": False, "mig_key": mig_key, "failure_code": code}

    if fn_item is None:
        # Should be unreachable (api.start_migration 404s first), but be safe.
        return _fail("SOURCE_NOT_FOUND", "No function row for this ARN.")

    runtime = fn_item.get("runtime", "") or ""
    transform = transform_runtimes.transform_for_runtime(runtime)
    if transform is None:
        return _fail("RUNTIME_UNSUPPORTED",
                     f"No managed transformation for runtime '{runtime}'.")
    family = transform_runtimes._family(runtime)

    # Resolve repo + branch from the fn# item's tags and the stored Settings.
    settings = api.get_settings({})["settings"]
    repo_tag = settings.get("repoTag") or "source-repo"
    branch_tag = settings.get("branchTag")
    org = settings.get("org")
    resolved = api.resolve_repo_from_tags(fn_item.get("tags"), repo_tag, branch_tag, org)
    repo_name = resolved.get("repository")
    branch = resolved.get("branch")

    if not repo_name:
        return _fail("TAG_NOT_FOUND",
                     "No source-repo tag resolved for this function.")

    # Verify the CodeCommit repo (and branch) exist, in the function's region.
    codecommit = boto3.client("codecommit", region_name=region)
    try:
        codecommit.get_repository(repositoryName=repo_name)
    except Exception as exc:  # noqa: BLE001 - classify by error code
        code = _classify_codecommit_error(exc)
        if code == "ACCESS_DENIED":
            return _fail("ACCESS_DENIED",
                         "Access denied reading the source repository.")
        return _fail("SOURCE_NOT_FOUND",
                     f"Source repository '{repo_name}' was not found.")

    if branch:
        try:
            codecommit.get_branch(repositoryName=repo_name, branchName=branch)
        except Exception as exc:  # noqa: BLE001
            code = _classify_codecommit_error(exc)
            if code == "ACCESS_DENIED":
                return _fail("ACCESS_DENIED",
                             "Access denied reading the source branch.")
            return _fail("BRANCH_NOT_FOUND",
                         f"Branch '{branch}' was not found in '{repo_name}'.")

    # The managed transformation owns the target: each family's defaultTarget is
    # the runtime its transformation is built for, so it is the only target used
    # (the assessment does not suggest one).
    target = transform["defaultTarget"]

    repo_clone_url = repository_url(repo_name, region)

    # UNGUARDED create: the QUEUED mig# item (nothing to clobber on first write).
    _set_fields(mig_key, {
        "function_arn": canonical,
        "account_id": account_id,
        "region": region,
        "migration_status": "QUEUED",
        "migration_repo": repo_clone_url,
        # The branch the build clones and cuts the upgrade branch from. Named
        # migration_source_branch so migration_branch is exclusively the UPGRADE
        # branch build_complete writes with BRANCH_READY (the drawer renders it).
        "migration_source_branch": branch or "",
        "migration_transform": transform["transformName"],
        "migration_target": target,
        "migration_execution_arn": execution_arn,
        "migration_batch_id": batch_id,
        "migration_updated_at": _now(),
    }, remove=_RUN_OUTCOME_FIELDS)

    return {
        "ok": True,
        "mig_key": mig_key,
        "function_arn": canonical,
        "account_id": account_id,
        "region": region,
        "family": family,
        "runtime": runtime,
        "repository_url": repo_clone_url,
        "branch": branch or "",
        "target": target,
        "transform_name": transform["transformName"],
        "build_command": transform.get("buildCommand", ""),
        "validation_commands": transform.get("validationCommands", ""),
        "batch_id": batch_id,       # the submitter derives the per-run job id from it
    }


def _classify_codecommit_error(exc) -> str:
    """Map a CodeCommit ClientError to a failure family: ACCESS_DENIED or NOT_FOUND."""
    code = ""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = (response.get("Error") or {}).get("Code", "") or ""
    code_l = code.lower()
    if "accessdenied" in code_l or "authorization" in code_l:
        return "ACCESS_DENIED"
    return "NOT_FOUND"


@durable_wait_for_callback
def start_transform(callback_id: str, ctx: WaitForCallbackContext, resolved: dict) -> None:
    """The transform-<i> wait_for_callback submitter: render the AWS Transform
    config to S3, start the CodeBuild job that runs AWS Transform, persist the
    callback id on the mig# item. build_complete completes the callback with
    the build's result.json; the orchestrator folds the result.

    The return value is unused (the callback result is). Raising fails the
    submitter -> CallbackSubmitterError -> caught as CallbackError in
    _migrate_one -> ok:false, TRANSFORM_FAILED. Order matters: the config check
    comes before any side effect, the StartBuild re-read makes a retried
    attempt reuse the build it already started.
    """
    mig_key = resolved["mig_key"]
    batch_id = resolved["batch_id"]

    # 1. Config check FIRST: a misconfigured deploy never writes to S3. Unreachable
    #    from the UI (start_migration 503s when MIGRATE_FUNCTION_ARN is unset, and
    #    the two deploy together). The guarded write is swallowed on a retried
    #    attempt (the item is already FAILED); the RuntimeError is raised in every
    #    case so the wait is aborted with the intended error.
    if not PROJECT_NAME:
        written = _set_live_fields(mig_key, {
            "migration_status": "FAILED",
            "migration_failure_code": "ACCESS_DENIED",
            "migration_failure_detail": "CodeBuild project is not configured.",
            "migration_updated_at": _now(),
        }, condition="migration_status = :queued")
        if not written:
            logger.info("mig item %s already settled; CodeBuild project still not configured", mig_key)
        raise RuntimeError("CodeBuild project is not configured")

    # 2. Per-run job id (S3 prefix, upgrade branch, CodeCommit idempotency token).
    job_id = derive_job_id(mig_key, batch_id)

    # 3. Re-read the fn# item for the assessment fields, render the Transform
    #    CONFIG (not function source) and put it to S3. The YAML never crosses a
    #    durable checkpoint.
    canonical = resolved["function_arn"]
    fn_item = {}
    for row in api._scan_inventory("fn#"):
        if discover.canonical_arn(row.get("function_arn", "")) == canonical:
            fn_item = row
            break
    additional = render_additional_plan_context(fn_item, resolved["family"], resolved["target"])
    yaml_text = _render_transform_config(
        resolved["transform_name"], resolved.get("build_command", ""), additional,
        resolved.get("validation_commands", ""))
    s3_key = f"migrations/{job_id}/transform-config.yaml"
    boto3.client("s3", region_name=get_region()).put_object(
        Bucket=BUCKET_NAME, Key=s3_key, Body=yaml_text.encode("utf-8"),
        ContentType="text/yaml")

    # 4. Re-read the mig# item: a retried attempt after a successful StartBuild +
    #    flip (the buildidx# write failed last time) must reuse that build.
    existing = _table().get_item(Key={"item_key": mig_key}).get("Item") or {}
    if existing.get("migration_callback_id") == callback_id and existing.get("migration_build_id"):
        build_id = existing["migration_build_id"]
        logger.info("reusing build %s for callback %s", build_id, callback_id)
    else:
        env_override = [
            {"name": "REPOSITORY_URL", "value": resolved["repository_url"], "type": "PLAINTEXT"},
            {"name": "TARGET_RUNTIME", "value": resolved["target"], "type": "PLAINTEXT"},
            {"name": "TRANSFORM_NAME", "value": resolved["transform_name"], "type": "PLAINTEXT"},
            # The build tries the deploy region first and only uses this fallback if
            # atx reports the deploy region is not an AWS Transform region. Computed
            # from the deploy region's geo by the shared helper (no static choice in
            # this module); see shared/utils/aws_utils.get_transform_region.
            {"name": "TRANSFORM_FALLBACK_REGION", "value": _transform_fallback_region(), "type": "PLAINTEXT"},
            {"name": "JOB_ID", "value": job_id, "type": "PLAINTEXT"},
            {"name": "OUTPUT_BUCKET", "value": BUCKET_NAME, "type": "PLAINTEXT"},
            # The branch the build clones and cuts the upgrade branch from (empty
            # = the repo's default branch), so the branch diff is the upgrade and
            # nothing else.
            {"name": "SOURCE_BRANCH", "value": resolved.get("branch", ""), "type": "PLAINTEXT"},
        ]
        # Both CodeBuild phases are bounded here (queue time is NOT covered by
        # the build timeout); the callback timeout and the staleness window
        # derive from these two numbers.
        resp = boto3.client("codebuild", region_name=get_region()).start_build(
            projectName=PROJECT_NAME,
            environmentVariablesOverride=env_override,
            timeoutInMinutesOverride=BUILD_TIMEOUT_MINUTES,
            queuedTimeoutInMinutesOverride=QUEUE_TIMEOUT_MINUTES,
        )
        build_id = (resp.get("build") or {}).get("id", "")

    # 5. GUARDED QUEUED -> IN_PROGRESS (+ build, job and callback ids). A
    #    conditional failure is a replay after a successful first attempt: the
    #    item already carries this build (idempotent).
    written = _set_live_fields(mig_key, {
        "migration_status": "IN_PROGRESS",
        "migration_build_id": build_id,
        "migration_job_id": job_id,
        "migration_callback_id": callback_id,
        "migration_updated_at": _now(),
    }, condition="migration_status = :queued")
    if not written:
        logger.info("mig item %s already IN_PROGRESS for build %s (replay)", mig_key, build_id)

    # 6. Idempotent, unguarded build-id -> mig-item index row (authoritative
    #    lookup for build_complete). The callback id is duplicated here so
    #    build_complete can complete the callback even if the mig# read fails;
    #    the batch id lets it prove ownership before writing.
    if build_id:
        _set_fields(f"buildidx#{build_id}", {
            "mig_item_key": mig_key,
            "job_id": job_id,
            "callback_id": callback_id,
            "batch_id": batch_id,
        })


_SETTLE_CONDITION = "migration_status IN (:queued, :inprogress)"


def _settle(mig_key: str, code: str, detail: str) -> dict:
    """Guarded terminal FAILED write by the orchestrator. A conditional failure
    means build_complete already settled the item."""
    written = _set_live_fields(mig_key, {
        "migration_status": "FAILED",
        "migration_failure_code": code,
        "migration_failure_detail": detail[:FAILURE_DETAIL_MAX_CHARS],
        "migration_updated_at": _now(),
    }, condition=_SETTLE_CONDITION)
    if not written:
        logger.info("mig item %s already settled; not writing FAILED/%s", mig_key, code)
    else:
        logger.info("mig item %s settled FAILED/%s", mig_key, code)
    return {"settled": written}


@durable_step
def settle_timed_out(step: StepContext, resolved: dict) -> dict:
    """settle-<i> after a CallbackTimeoutError: FAILED / TIMED_OUT, guarded on the
    live statuses so a late build_complete cannot be overwritten and vice versa."""
    minutes = int(TRANSFORM_CALLBACK_TIMEOUT.to_seconds()) // 60
    return _settle(resolved["mig_key"], "TIMED_OUT",
                   f"AWS Transform did not report back within {minutes} minutes.")


@durable_step
def settle_failed(step: StepContext, resolved: dict, detail: str) -> dict:
    """settle-<i> after a submitter failure or a rejected build result:
    FAILED / TRANSFORM_FAILED, guarded on the live statuses."""
    return _settle(resolved["mig_key"], "TRANSFORM_FAILED", detail)


# ---------------------------------------------------------------------------
# Batch identity step + pure fold
# ---------------------------------------------------------------------------

@durable_step
def prepare_batch(step: StepContext, arns: list, execution_arn: str) -> dict:
    """Mint the batch identity ONCE (the only uuid/clock in this handler), apply
    the per-run cap, and write the migbatch# index row. execution_arn is the
    real durable execution ARN (context.execution_context.durable_execution_arn).

    Mirrors pipeline.start_run: non-determinism (uuid, clock, DynamoDB) is inside
    the step. The returned payload is bounded (accepted/over_cap are both <= cap)."""
    batch_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    # The mig# item is keyed on the CANONICAL arn, so the index carries canonical ARNs.
    canon = [discover.canonical_arn(a) for a in arns]
    accepted = canon[:MAX_FUNCTIONS_PER_RUN]
    over_cap = canon[len(accepted):]        # reported, NEVER silently dropped
    # Field-scoped update_item (never a whole-item PutItem); the create is unguarded.
    _set_fields(f"migbatch#{batch_id}", {
        "batch_id": batch_id,
        "migration_batch_execution_arn": execution_arn,
        "migration_batch_started_at": started_at,
        "migration_batch_arns": accepted,       # bounded: <= cap canonical ARNs
        "migration_batch_over_cap": over_cap,   # bounded: <= selection - cap
        "migration_batch_total": len(canon),
    })
    return {
        "batch_id": batch_id,
        "started_at": started_at,
        "accepted": accepted,
        "over_cap_count": len(over_cap),
        "total_selected": len(canon),
    }


def summarize_migration(batch: dict, mig_batch) -> dict:
    """Fold the batch map result into bounded counts. PURE — called in the
    handler OUTSIDE any step (mirrors pipeline.summarize_assess/summarize_discover);
    no BatchResult crosses a durable checkpoint.

    Invariants: rejected + resolved == len(accepted), and
    resolved == branches_ready + no_changes + failed (+ tolerated unknown
    statuses, see below)."""
    results = results_by_index(mig_batch, len(batch["accepted"]))
    # Rejections (the resolve stage's REJECTED mig# items + the no-write
    # ALREADY_IN_PROGRESS exit): TAG_NOT_FOUND / SOURCE_NOT_FOUND /
    # RUNTIME_UNSUPPORTED / ACCESS_DENIED / BRANCH_NOT_FOUND / ALREADY_IN_PROGRESS /
    # UNKNOWN -> count (<=7 keys).
    rejected_by_code = {}
    resolved = branches_ready = no_changes = failed = 0
    for res in results:
        if res is None:
            # Unit absent: a unit that crashed past its retries (tolerated).
            # Folded as UNKNOWN so the summary total still balances (the
            # authoritative live counts come from the history).
            rejected_by_code["UNKNOWN"] = rejected_by_code.get("UNKNOWN", 0) + 1
            continue
        stage = res.get("stage")
        if stage == "resolve" and not res.get("ok"):
            code = res.get("failure_code") or "UNKNOWN"
            rejected_by_code[code] = rejected_by_code.get(code, 0) + 1
            continue
        resolved += 1
        status = res.get("status")
        if not res.get("ok"):
            failed += 1
        elif status == "BRANCH_READY":
            branches_ready += 1
        elif status == "NO_CHANGES":
            no_changes += 1
        # Unknown-value guard: any other status (a unit result shape this fold
        # does not know) is counted as resolved only, never as a failure, and
        # never raises.
    return {
        "batch_id": batch["batch_id"],
        "started_at": batch["started_at"],
        "total_selected": batch["total_selected"],
        "over_cap_skipped": batch["over_cap_count"],
        "resolved": resolved,
        "rejected": sum(rejected_by_code.values()),
        "rejected_by_code": rejected_by_code,
        "branches_ready": branches_ready,
        "no_changes": no_changes,
        "failed": failed,
    }


# ---------------------------------------------------------------------------
# Handler — ONE batch durable execution fanning out over the selected ARNs
# ---------------------------------------------------------------------------

# resolve can hit a flaky CodeCommit read. Mirror pipeline's _DISCOVER_RETRY /
# _ASSESS_RETRY StepConfig shape.
_RESOLVE_RETRY = StepConfig(retry_strategy=create_retry_strategy(RetryStrategyConfig(
    max_attempts=3, initial_delay=Duration.from_seconds(5), max_delay=Duration.from_seconds(30))))
# The transform submitter (S3 put + StartBuild): WaitForCallbackConfig takes the
# bare strategy callable, not a StepConfig.
_SUBMIT_RETRY_STRATEGY = create_retry_strategy(RetryStrategyConfig(
    max_attempts=2, initial_delay=Duration.from_seconds(5), max_delay=Duration.from_seconds(30)))

# One bad function must never abort the batch: the summary/phases report it.
_TOLERATE_ALL = CompletionConfig(tolerated_failure_percentage=100)


def _run_batch(event: dict, context: DurableContext) -> dict:
    """The full batch orchestration body (one contiguous unit: pure normalization
    -> prepare-batch step -> context.map fan-out -> pure summarize_migration).
    Kept as a plain function so the step/pure/closure boundaries can be exercised
    off the durable runtime in tests; the decorated handler just delegates to it.

    Only CallbackError subclasses are caught around the wait: never Exception.
    UnrecoverableError subclasses (StepInterruptedError,
    NonDeterministicExecutionError, ...) MUST escape so the SDK can retry the
    invocation or fail the execution; a bare `except Exception` would settle an
    item as FAILED whose build is still running."""
    event = event or {}

    # --- PURE normalization (before any step so bad input never checkpoints) ---
    raw_arns = event.get("function_arns")
    if not raw_arns and event.get("function_arn"):
        raw_arns = [event["function_arn"]]          # single-ARN back-compat
    arns = [a for a in (raw_arns or []) if isinstance(a, str) and a]
    # dedupe preserving order (a double-selected ARN must not run twice)
    seen, deduped = set(), []
    for a in arns:
        if a not in seen:
            seen.add(a)
            deduped.append(a)
    arns = deduped
    # Plain execution metadata, deterministic across replays (not in the payload:
    # Invoke returns the ARN, api.py never sends it).
    execution_arn = context.execution_context.durable_execution_arn

    # --- batch identity (mints id, applies cap, writes migbatch# index) ---
    batch = context.step(prepare_batch(arns, execution_arn), name="prepare-batch")

    # --- fan out: resolve -> transform (callback) -> [settle] ---
    def _migrate_one(ctx: DurableContext, function_arn: str, index: int, _all) -> dict:
        request = {
            "function_arn": function_arn,
            "execution_arn": execution_arn,
            "batch_id": batch["batch_id"],          # stamped onto the mig# item
        }
        resolved = ctx.step(resolve_and_verify(request),
                            name=f"resolve-{index}", config=_RESOLVE_RETRY)
        if not resolved.get("ok"):
            return {"stage": "resolve", "ok": False,
                    "failure_code": resolved.get("failure_code")}

        try:
            raw = ctx.wait_for_callback(
                start_transform(resolved),
                name=f"transform-{index}",
                config=WaitForCallbackConfig(timeout=TRANSFORM_CALLBACK_TIMEOUT,
                                             retry_strategy=_SUBMIT_RETRY_STRATEGY))
            transformed = parse_build_result(raw)          # PURE: json.loads + shape check
            if transformed.get("parse_failed"):
                # build_complete wrote FAILED for a well-formed FAILED payload and
                # for TRANSFORMED-without-branch; the guarded settle is then a
                # no-op. It is load-bearing only for a callback payload
                # build_complete never produced (None / garbage).
                ctx.logger.warning("transform-%d: rejected build result: %s", index, transformed["detail"])
                ctx.step(settle_failed(resolved, transformed["detail"]),
                         name=f"settle-{index}")
        except CallbackTimeoutError:
            # Re-raised identically on every replay, so this branch is deterministic.
            ctx.logger.warning("transform-%d: callback timed out", index)
            transformed = {"ok": False, "status": "FAILED", "failure_code": "TIMED_OUT"}
            ctx.step(settle_timed_out(resolved), name=f"settle-{index}")
        except CallbackError as exc:                       # submitter exhausted / external failure
            ctx.logger.error("transform-%d: %s", index, exc)
            transformed = {"ok": False, "status": "FAILED", "failure_code": "TRANSFORM_FAILED",
                           "detail": str(exc)[:FAILURE_DETAIL_MAX_CHARS]}
            ctx.step(settle_failed(resolved, transformed["detail"]),
                     name=f"settle-{index}")

        if not transformed.get("ok"):
            return {"stage": "transform", "ok": False, "status": "FAILED",
                    "failure_code": transformed.get("failure_code")}
        if transformed["status"] == "NO_CHANGES":
            return {"stage": "transform", "ok": True, "status": "NO_CHANGES"}
        # TRANSFORMED with a branch: build_complete wrote BRANCH_READY before
        # completing the callback, so no settle step runs on the success path.
        return {"stage": "transform", "ok": True, "status": "BRANCH_READY",
                "branch": transformed["branch"]}

    mig_batch = context.map(batch["accepted"], _migrate_one, name="migrate",
                            config=MapConfig(max_concurrency=MIGRATE_CONCURRENCY,
                                             completion_config=_TOLERATE_ALL))
    return summarize_migration(batch, mig_batch)       # PURE fold, NOT a step


@durable_execution
def handler(event: dict, context: DurableContext) -> dict:
    return _run_batch(event, context)
