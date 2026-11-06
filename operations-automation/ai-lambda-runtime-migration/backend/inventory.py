"""Read side of the inventory table, shared by the API Lambda and the migration
orchestrator.

The table holds several kinds of rows behind key prefixes: function rows
('fn#...'), migration-run items ('mig#...'), the immutable per-scan rollup
series ('rollup#...') and the single Git/detection settings row
('settings#git'). This module owns the table accessor, the prefix-filtered scan
and the settings read, plus the pure tag-to-repository resolver, so the
orchestrator never has to import the API module to read what it needs.

Only reads live here. Writes stay with their owner (api.put_settings, the
discover/assess/migrate steps), each with its own guard.
"""

import os

import boto3

from aws_utils import get_region  # staged copy of shared/utils/aws_utils.py
from common import decimals_to_native

TABLE_NAME = os.environ.get("TABLE_NAME", "lambda-runtime-migration")

_ddb = None


def table():
    """The inventory DynamoDB Table, created lazily so importing this module
    performs no AWS call."""
    global _ddb
    if _ddb is None:
        _ddb = boto3.resource("dynamodb", region_name=get_region())
    return _ddb.Table(TABLE_NAME)


def scan_inventory(prefix: str = "fn#") -> list:
    """Scan the table, keeping only item_key rows under `prefix`.

    Callers pass the prefix they want so rollup or settings rows never leak into
    the functions table, and get_stats can reuse the same raw scan for the
    'rollup#' rows. Pass prefix='' for the whole table. Decimals are converted to
    native numbers.
    """
    tbl = table()
    items, kwargs = [], {}
    while True:
        resp = tbl.scan(**kwargs)
        items.extend(r for r in resp.get("Items", [])
                     if not prefix or str(r.get("item_key", "")).startswith(prefix))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return decimals_to_native(items)


# One row holds the demo's repository-detection configuration. It lives in the
# same table behind a dedicated key prefix, so the inventory scan (prefix "fn#")
# and the rollup read (prefix "rollup#") never see it.
SETTINGS_KEY = "settings#git"

# The ONLY keys ever persisted, and exactly the keys the migration reads
# (migrate.resolve_and_verify). Nothing else is stored, so no credential ever
# reaches this table.
#
# repoTag   = the resource tag key whose value is the CodeCommit repository name.
# branchTag = an OPTIONAL resource tag key whose value is the source branch.
SETTINGS_ALLOWED = ("repoTag", "branchTag")


def read_settings() -> dict:
    """The stored repository-detection config, or {} when unset.

    An absent row is an empty dict so callers render / apply defaults (repoTag
    falls back to "source-repo", branchTag to the repository's default branch).
    """
    resp = table().get_item(Key={"item_key": SETTINGS_KEY})
    return decimals_to_native(resp.get("Item")) or {}


def resolve_repo_from_tags(tags, repo_tag, branch_tag):
    """Resolve a function's source repository + branch from its resource tags.

    Pure and side-effect free (no AWS, no I/O): the single source of truth for
    how the two configured tags combine, used by the migration orchestrator.
    Never raises — a non-dict `tags`, a missing key, or a non-string value all
    degrade to None.

    - repository: value of tags[repo_tag] when present and non-empty, else None.
      The value is the CodeCommit repository name as is; nothing is prepended
      (a CodeCommit name cannot contain "/").
    - branch: value of tags[branch_tag] when branch_tag is set and present with
      a non-empty value, else None.

    Returns {"repository": str|None, "branch": str|None}.
    """
    safe_tags = tags if isinstance(tags, dict) else {}

    repository = None
    raw_repo = safe_tags.get(repo_tag) if repo_tag else None
    if raw_repo is not None:
        value = str(raw_repo).strip()
        if value:
            repository = value

    branch = None
    if branch_tag:
        raw_branch = safe_tags.get(branch_tag)
        if raw_branch is not None:
            value = str(raw_branch).strip()
            if value:
                branch = value

    return {"repository": repository, "branch": branch}
