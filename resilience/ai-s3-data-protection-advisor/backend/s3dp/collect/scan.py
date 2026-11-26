"""One account end to end (raw scan), as account_overview -> collect_unit (one per region batch) -> assemble, so the
pipeline can run each as a durable step; collect_account composes them for the CLI.

Nothing here aborts the scan on an API failure: every collector call is guarded, a failure is counted in
run.api_errors, listed in run.unavailable ([what, why]) and recorded on the region (outcome, errors). Unknown values
stay None / "unknown" so derive and the engine show them as unconfirmed, never as "off" or "none".
"""
import copy
import json
from datetime import datetime, timezone

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from s3dp.collect import account as acc, backup, metrics, s3 as s3c

CFG = Config(retries={"mode": "adaptive", "max_attempts": 10})
CE_HOME = {"aws": "us-east-1", "aws-cn": "cn-northwest-1", "aws-us-gov": "us-gov-west-1"}   # Cost Explorer endpoint


def _code(e):
    return e.response.get("Error", {}).get("Code", "Unknown") if isinstance(e, ClientError) else type(e).__name__


class _Run:
    """api_errors counter and unavailable rows of one scan."""

    def __init__(self):
        self.api_errors, self.unavailable, self.settings = 0, [], {}

    def fail(self, what, why, count=1):
        self.api_errors += count
        if [what, why] not in self.unavailable:
            self.unavailable.append([what, why])

    def bucket_fail(self, bucket, failed):
        """Per-bucket setting failures, reported as one row per setting by rows()."""
        self.api_errors += len(failed)
        for setting, code in failed.items():
            self.settings.setdefault(setting, {})[bucket] = code

    def to_dict(self):
        return {"api_errors": self.api_errors, "unavailable": [list(u) for u in self.unavailable],
                "settings": {k: dict(v) for k, v in self.settings.items()}}

    @classmethod
    def from_dict(cls, d):
        run = cls()
        run.api_errors = d["api_errors"]
        run.unavailable = [list(u) for u in d["unavailable"]]
        run.settings = {k: dict(v) for k, v in d["settings"].items()}
        return run

    def merge(self, other):
        self.api_errors += other.api_errors
        for u in other.unavailable:
            if u not in self.unavailable:
                self.unavailable.append(u)
        for setting, hits in other.settings.items():
            self.settings.setdefault(setting, {}).update(hits)

    def rows(self):
        out = list(self.unavailable)
        for setting, hits in sorted(self.settings.items()):
            names = sorted(hits)
            out.append([f"S3 {setting} on {len(names)} bucket(s): {', '.join(names[:10])}"
                        f"{', ...' if len(names) > 10 else ''}", ", ".join(sorted(set(hits.values())))])
        return out

    def call(self, what, fn, default=None):
        """fn() or `default` when it raises an API or transport error (recorded)."""
        try:
            return fn()
        except (ClientError, BotoCoreError) as e:
            self.fail(what, _code(e))
            return default


def _merge(values):
    """Account value from per-region tri-state values: True if any region is True, None if no region was read,
    else False."""
    read = [v for v in values if v is not None]
    return None if not read else any(read)


def _ce_region(session):
    try:
        partition = session.get_partition_for_region(session.region_name)
    except Exception:   # unknown region name: fall back to the session region
        partition = None
    return CE_HOME.get(partition, session.region_name)


def _list_buckets(session, run):
    s3 = session.client("s3", config=CFG)
    buckets = run.call("S3 bucket list", lambda: s3c.list_buckets(s3), None)
    if buckets is None:
        return None
    unknown = []
    for b in buckets:
        if not b["region"]:
            b["region"] = s3c.bucket_region(s3, b["name"]) or "unknown"
            if b["region"] == "unknown":
                unknown.append(b["name"])
    if unknown:
        run.fail(f"S3 bucket region ({len(unknown)} buckets not scanned: {', '.join(sorted(unknown)[:10])}"
                 f"{', ...' if len(unknown) > 10 else ''})", "GetBucketLocation failed", count=len(unknown))
    return buckets


def _spend(session, run, today):
    try:
        return acc.s3_spend_by_region(session.client("ce", region_name=_ce_region(session), config=CFG), today)
    except (ClientError, BotoCoreError) as e:
        run.fail("S3 spend per region (Cost Explorer)", f"{_code(e)}: regions taken from bucket locations")
        return {}


BATCH = 200      # buckets per unit: keeps one durable step short and its checkpoint small


def _region_level(session, r, run, errors, info, acct):
    """AWS Backup, CloudFormation, AWS Config and Security Hub of one region (once per region, first unit)."""
    def client(svc):
        return session.client(svc, region_name=r, config=CFG)

    def guarded(svc, label, fn, default=None):
        try:
            return fn()
        except (ClientError, BotoCoreError) as e:
            errors[svc] = _code(e)
            run.fail(f"{label} ({r})", _code(e))
            return default

    bk = guarded("backup", "AWS Backup", lambda: backup.collect_region(client("backup")))
    if bk is not None:     # no "backup" key: derive treats the whole region as not read
        info["backup"] = bk
        if bk["errors"]:
            errors["backup"] = ", ".join(sorted(set(bk["errors"].values())))
            run.fail(f"AWS Backup ({r}): {', '.join(sorted(bk['errors']))}", errors["backup"], count=len(bk["errors"]))

    stacks = guarded("cloudformation", "CloudFormation stacks", lambda: acc.stack_names(client("cloudformation")))
    if stacks is None and "cloudformation" not in errors:
        errors["cloudformation"] = "unreadable"
        run.fail(f"CloudFormation stacks ({r})", "ListStacks failed")
    acct["stack_names"] += stacks or []

    cfg = guarded("config", "AWS Config", lambda: acc.config_status(client("config")),
                  {"recording": None, "s3_rules": None})
    if (cfg["recording"] is None or cfg["s3_rules"] is None) and "config" not in errors:
        errors["config"] = "unreadable"
        run.fail(f"AWS Config ({r})", "configuration recorder or rules could not be read")
    acct["config_recording"].append(cfg["recording"])
    acct["config_rules"].append(cfg["s3_rules"])

    sh = guarded("securityhub", "AWS Security Hub", lambda: acc.security_hub_enabled(client("securityhub")))
    if sh is None and "securityhub" not in errors:
        errors["securityhub"] = "unreadable"
        run.fail(f"AWS Security Hub ({r})", "GetEnabledStandards failed")
    acct["security_hub"].append(sh)


def _region_buckets(session, r, names, account_id, run, errors):
    """CloudWatch metrics and S3 settings of the given buckets of one region. Returns the bucket items."""
    def client(svc):
        return session.client(svc, region_name=r, config=CFG)

    def guarded(svc, label, fn, default=None):
        try:
            return fn()
        except (ClientError, BotoCoreError) as e:
            errors[svc] = _code(e)
            run.fail(f"{label} ({r})", _code(e))
            return default

    items, m = [], {}
    if names:
        m = guarded("cloudwatch", "S3 CloudWatch metrics", lambda: metrics.collect_region(client("cloudwatch"), names),
                    {}) or {}
        err = next((v["error"] for v in m.values() if isinstance(v, dict) and v.get("error")), None)
        if err and "cloudwatch" not in errors:
            errors["cloudwatch"] = err
            run.fail(f"S3 CloudWatch metrics ({r}): sizes not measured", err)
        rs3 = guarded("s3", "S3 regional client", lambda: client("s3"))
        for n in names:
            raw = s3c.collect_bucket(rs3, n) if rs3 is not None else {}
            failed = {k: v["error"] for k, v in raw.items() if isinstance(v, dict) and "error" in v}
            if failed:
                run.bucket_fail(n, failed)
            if raw:
                items.append({"name": n, "account": account_id, "region": r, "s3": raw, "metrics": m.get(n)})
    return items


def _outcome(info, errors):
    if errors:
        info["outcome"] = "partial: " + ", ".join(f"{k} {v}" for k, v in sorted(errors.items()))
        info["errors"] = errors
    return info


def account_overview(session, account_id, account_name=None, today=None, batch=BATCH):
    """Account-level reads. JSON-serializable checkpoint: {"today", "account_id", "account_name", "buckets":
    [{"name","region"}], "regions" (sorted), "alias", "role_names", "account_bpa", "listed", "batch", "run"}."""
    today = today or datetime.now(timezone.utc).date().isoformat()
    run = _Run()
    buckets = _list_buckets(session, run)
    spend = _spend(session, run, today)
    known = [b for b in buckets or [] if b["region"] != "unknown"]
    regions = sorted({b["region"] for b in known} | set(spend))

    iam = run.call("IAM client", lambda: session.client("iam", config=CFG))
    alias = acc.account_alias(iam) if iam else None
    roles = acc.role_names(iam) if iam else None
    if roles is None:
        run.fail("IAM role names (third-party backup detection)", "ListRoles failed")
    s3control = run.call("S3 Control client", lambda: session.client("s3control", config=CFG))
    bpa = acc.account_bpa(s3control, account_id) if s3control else "unknown"
    if bpa == "unknown":
        run.fail("Account-level S3 Block Public Access", "GetPublicAccessBlock failed")
    return {"today": today, "account_id": account_id, "account_name": account_name,
            "buckets": [{"name": b["name"], "region": b["region"]} for b in buckets or []], "regions": regions,
            "alias": alias, "role_names": roles, "account_bpa": bpa, "listed": buckets is not None,
            "batch": batch, "run": run.to_dict()}


def plan_units(overview, batch=None):
    """One unit per region and batch of buckets, in region order. A region with spend but no buckets still gets a
    unit (names []) for the region-level reads; "first" marks the unit that makes them. The batch size is the one
    stored in the overview (an explicit `batch` is for tests), so assemble re-plans exactly what was collected."""
    batch = batch or overview.get("batch", BATCH)
    units = []
    for r in overview["regions"]:
        names = [b["name"] for b in overview["buckets"] if b["region"] == r]
        for i in range(0, max(len(names), 1), batch):
            units.append({"n": len(units), "region": r, "names": names[i:i + batch], "first": i == 0})
    return units


def collect_unit(session, account_id, unit):
    """Reads of one unit. "errors" holds the per-service errors of this unit, which assemble merges per region."""
    r, run, errors = unit["region"], _Run(), {}
    acct = {"stack_names": [], "config_recording": [], "config_rules": [], "security_hub": []}
    info = {"outcome": "scanned"} if unit["first"] else None
    if unit["first"]:
        _region_level(session, r, run, errors, info, acct)
    items = _region_buckets(session, r, unit["names"], account_id, run, errors)
    if unit["first"]:
        _outcome(info, errors)
    # Same normalisation as raw.json: botocore returns datetimes (lifecycle dates) that a checkpoint cannot hold.
    items = json.loads(json.dumps(items, default=str))
    return {"n": unit["n"], "region": r, "info": info, "items": items, "acct": acct, "errors": errors,
            "run": run.to_dict()}


def assemble(overview, units):
    """Raw scan dict from the overview and the unit results (None for a unit that failed)."""
    overview, units = copy.deepcopy(overview), copy.deepcopy(units)    # the checkpoints stay as they were read
    plan = plan_units(overview)
    if len(units) != len(plan) or any(res is not None and res["n"] != u["n"] for u, res in zip(plan, units)):
        raise ValueError(f"expected {len(plan)} unit results in plan order, got "
                         f"{[None if r is None else r['n'] for r in units]}")
    today, account_id = overview["today"], overview["account_id"]
    run = _Run.from_dict(overview["run"])
    regions = overview["regions"]
    acct = {"stack_names": [], "config_recording": [], "config_rules": [], "security_hub": []}
    region_info, region_errors, items = {}, {}, []
    for unit, res in zip(plan, units):
        r = unit["region"]
        if res is None:
            if unit["names"]:
                run.fail(f"S3 settings ({r}, {len(unit['names'])} buckets not scanned)", "collection failed",
                         count=len(unit["names"]))
            region_errors.setdefault(r, {})["collection"] = "failed"
            if unit["first"]:        # region-level reads missing: unknown, never off
                run.fail(f"AWS Backup, CloudFormation, AWS Config and Security Hub ({r})" if unit["names"]
                         else f"Region-level reads ({r})", "collection failed")
                region_info[r] = {"outcome": "scanned"}
                acct["config_recording"].append(None)
                acct["config_rules"].append(None)
                acct["security_hub"].append(None)
            continue
        run.merge(_Run.from_dict(res["run"]))
        if unit["first"]:
            region_info[r] = res["info"]
            for k in acct:
                acct[k] += res["acct"][k]
        region_errors.setdefault(r, {})
        for k, v in res["errors"].items():
            region_errors[r].setdefault(k, v)
        items += res["items"]
    for r, errors in region_errors.items():
        if errors:
            info = region_info.setdefault(r, {"outcome": "scanned"})
            info.pop("errors", None)
            _outcome(info, errors)
    region_info = {r: region_info[r] for r in regions if r in region_info}

    rules = [x for x in acct["config_rules"] if x is not None]
    name = overview.get("account_name") or overview["alias"] or account_id
    meta = {"name": name, "account_bpa": overview["account_bpa"], "role_names": overview["role_names"],
            "stack_names": sorted(set(acct["stack_names"])),
            "config": {"recording": _merge(acct["config_recording"]),
                       "s3_rules": sorted({x for rs in rules for x in rs}) if rules else None,
                       "unreadable_regions": [r for r, v, x in zip(regions, acct["config_recording"],
                                                                   acct["config_rules"]) if v is None or x is None]},
            "security_hub": _merge(acct["security_hub"]),
            "outcome": "scanned" if overview["listed"] else "denied: S3 bucket list"}
    return {
        "scan_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hub_account": account_id,
        "accounts": {account_id: meta},
        "regions": {account_id: region_info},
        "buckets": items,
        "organization": None,
        "run": {"scope": f"1 account ({name})", "collected": today, "regions_scanned": regions,
                "regions_skipped": "none", "unavailable": run.rows(), "api_errors": run.api_errors,
                "price_region": "US East (N. Virginia) list prices"},
    }


def collect_account(session, account_id, account_name=None, today=None):
    """Raw scan of one account with `session` (boto3.Session-like: client(name, region_name=, config=),
    region_name, get_partition_for_region). Shape: see derive/inventory.build()."""
    ov = account_overview(session, account_id, account_name, today)
    return assemble(ov, [collect_unit(session, account_id, u) for u in plan_units(ov)])
