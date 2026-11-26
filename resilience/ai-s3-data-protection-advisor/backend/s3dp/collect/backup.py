"""AWS Backup posture for one region, read-only."""
from botocore.exceptions import BotoCoreError, ClientError



def _code(e):
    return e.response.get("Error", {}).get("Code", "Unknown") if isinstance(e, ClientError) else type(e).__name__


def _safe(errors, name, fn, default):
    """Run fn; on an API or transport failure record the code under `name` and return `default`."""
    try:
        return fn()
    except (ClientError, BotoCoreError) as e:
        errors[name] = _code(e)
        return default


def _paged(bk, op, key, **kw):
    return [x for page in bk.get_paginator(op).paginate(**kw) for x in page.get(key, [])]


def _s3_names(resources):
    """Bucket names of the S3 ARNs in a selection."""
    return [arn.split(":::", 1)[1] for arn in resources if ":s3:::" in arn]


def _plans(bk, errors):
    """(plans_total, plans with copy actions, {bucket: set of plan names} from the plans' selections).

    plans_total is None when the plan list could not be read; plans with copy actions is None when the plan list or
    any plan body could not be read."""
    plans = _safe(errors, "plans", lambda: _paged(bk, "list_backup_plans", "BackupPlansList"), None)
    if plans is None:
        return None, None, {}
    s3_plans, with_copy = {}, 0
    for plan in plans:
        pid, pname = plan["BackupPlanId"], plan["BackupPlanName"]
        body = _safe(errors, f"plan:{pname}", lambda: bk.get_backup_plan(BackupPlanId=pid)["BackupPlan"], None)
        if body is None:
            with_copy = None
        elif with_copy is not None and any(r.get("CopyActions") for r in body.get("Rules", [])):
            with_copy += 1
        selections = _safe(errors, f"selections:{pname}", lambda: _paged(
            bk, "list_backup_selections", "BackupSelectionsList", BackupPlanId=pid), [])
        for sel in selections:
            sid = sel["SelectionId"]
            s = _safe(errors, f"selection:{pname}:{sid}", lambda: bk.get_backup_selection(
                BackupPlanId=pid, SelectionId=sid)["BackupSelection"], {})
            for name in _s3_names(s.get("Resources", [])):
                s3_plans.setdefault(name, set()).add(pname)
    return len(plans), with_copy, s3_plans


def _protected_s3(protected, s3_plans):
    if protected is None:
        return None
    names = [r["ResourceArn"].split(":::", 1)[1] for r in protected if r.get("ResourceType") == "S3"]
    return {n: sorted(s3_plans.get(n, set())) or ["(plan not resolved)"] for n in names}


def _vaults(bk, errors):
    vaults = _safe(errors, "vaults", lambda: _paged(bk, "list_backup_vaults", "BackupVaultList"), None)
    if vaults is None:
        return None
    return [{"name": v["BackupVaultName"], "locked": bool(v.get("Locked")),
             "recovery_points": v.get("NumberOfRecoveryPoints"),
             "air_gapped": v.get("VaultType") == "LOGICALLY_AIR_GAPPED_BACKUP_VAULT"}
            for v in vaults]


def _settings(bk, errors):
    """(s3_opt_in, restore testing plan count, framework count), each None when it could not be read."""
    opt = _safe(errors, "region_settings", lambda: bk.describe_region_settings().get("ResourceTypeOptInPreference", {}),
                None)
    restore = _safe(errors, "restore_testing", lambda: _paged(bk, "list_restore_testing_plans", "RestoreTestingPlans"), None)
    frameworks = _safe(errors, "frameworks", lambda: _frameworks(bk), None)   # list_frameworks has no paginator
    count = lambda x: None if x is None else len(x)
    return None if opt is None else bool(opt.get("S3", False)), count(restore), count(frameworks)


def _frameworks(bk):
    out, kw = [], {}
    while True:
        r = bk.list_frameworks(**kw)
        out += r.get("Frameworks", [])
        if not r.get("NextToken"):
            return out
        kw["NextToken"] = r["NextToken"]


def collect_region(bk):
    """Backup posture of one region. A failed call is listed in `errors` (key = call name) and the value it feeds is
    None (not read), never an empty list or a zero: protected_s3, plans_total, plans_with_copy_actions, vaults,
    s3_opt_in, restore_testing_plans, frameworks."""
    errors = {}
    protected = _safe(errors, "protected_resources", lambda: _paged(bk, "list_protected_resources", "Results"), None)
    plans_total, with_copy, s3_plans = _plans(bk, errors)
    protected_s3 = _protected_s3(protected, s3_plans)
    vaults = _vaults(bk, errors)
    s3_opt_in, restore_testing_plans, frameworks = _settings(bk, errors)
    return {"protected_s3": protected_s3, "plans_total": plans_total, "plans_with_copy_actions": with_copy,
            "vaults": vaults, "s3_opt_in": s3_opt_in, "restore_testing_plans": restore_testing_plans,
            "frameworks": frameworks, "errors": errors}
