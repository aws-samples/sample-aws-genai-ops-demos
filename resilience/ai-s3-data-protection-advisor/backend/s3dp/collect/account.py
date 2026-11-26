"""Account-level signals, read-only. Every function returns a value (unknown is None or "unknown") and does not
raise on API or transport errors, except `s3_spend_by_region`, which raises by design: the caller handles it."""
from datetime import date, timedelta

from botocore.exceptions import BotoCoreError, ClientError


def _code(e):
    return e.response.get("Error", {}).get("Code", "Unknown")


PAB_FLAGS = ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")


def account_bpa_flags(s3control, account_id):
    """The four account-level settings {flag: bool} (all False when none is set), or None when unread. S3 combines
    them flag by flag with the bucket's, so each is kept."""
    try:
        c = s3control.get_public_access_block(AccountId=account_id)["PublicAccessBlockConfiguration"]
        return {k: c.get(k) is True for k in PAB_FLAGS}
    except BotoCoreError:
        return None
    except ClientError as e:
        return dict.fromkeys(PAB_FLAGS, False) if _code(e) == "NoSuchPublicAccessBlockConfiguration" else None


def bpa_state(flags):
    """'on' when all four settings are true, 'off' when any is false, 'unknown' when unread."""
    return "unknown" if flags is None else "on" if all(flags.values()) else "off"


def account_bpa(s3control, account_id):
    return bpa_state(account_bpa_flags(s3control, account_id))


def account_alias(iam):
    """The account alias, or None when there is none or it could not be read (the two are not told apart)."""
    try:
        aliases = iam.list_account_aliases().get("AccountAliases", [])
        return aliases[0] if aliases else None
    except (ClientError, BotoCoreError):
        return None


def role_names(iam):
    try:
        return [r["RoleName"] for p in iam.get_paginator("list_roles").paginate() for r in p["Roles"]]
    except (ClientError, BotoCoreError):
        return None


STACK_STATUSES_KEPT = (
    "CREATE_IN_PROGRESS", "CREATE_FAILED", "CREATE_COMPLETE", "ROLLBACK_IN_PROGRESS", "ROLLBACK_FAILED",
    "ROLLBACK_COMPLETE", "DELETE_IN_PROGRESS", "DELETE_FAILED", "UPDATE_IN_PROGRESS",
    "UPDATE_COMPLETE_CLEANUP_IN_PROGRESS", "UPDATE_COMPLETE", "UPDATE_FAILED", "UPDATE_ROLLBACK_IN_PROGRESS",
    "UPDATE_ROLLBACK_FAILED", "UPDATE_ROLLBACK_COMPLETE_CLEANUP_IN_PROGRESS", "UPDATE_ROLLBACK_COMPLETE",
    "REVIEW_IN_PROGRESS", "IMPORT_IN_PROGRESS", "IMPORT_COMPLETE", "IMPORT_ROLLBACK_IN_PROGRESS",
    "IMPORT_ROLLBACK_FAILED", "IMPORT_ROLLBACK_COMPLETE")   # every StackStatus except DELETE_COMPLETE


def stack_names(cfn):
    """Names of the stacks that exist (any status but DELETE_COMPLETE); None when unreadable."""
    try:
        return [s["StackName"] for p in cfn.get_paginator("list_stacks").paginate(
            StackStatusFilter=list(STACK_STATUSES_KEPT)) for s in p["StackSummaries"]]
    except (ClientError, BotoCoreError):
        return None


def config_status(cfg):
    """{'recording': bool|None, 's3_rules': [normalized rule names] | None}. None means the call failed (unknown)."""
    try:
        recorders = cfg.describe_configuration_recorders().get("ConfigurationRecorders")
        if recorders:
            statuses = cfg.describe_configuration_recorder_status().get("ConfigurationRecordersStatus", [])
            recording = any(s.get("recording") for s in statuses)
        else:
            recording = False
    except (ClientError, BotoCoreError):
        return {"recording": None, "s3_rules": None}
    try:
        rules = [x.get("Source", {}).get("SourceIdentifier") or x["ConfigRuleName"]
                 for p in cfg.get_paginator("describe_config_rules").paginate() for x in p["ConfigRules"]]
    except (ClientError, BotoCoreError):
        return {"recording": recording, "s3_rules": None}
    return {"recording": recording, "s3_rules": sorted({x.lower().replace("_", "-") for x in rules if "s3" in x.lower()})}


def security_hub_enabled(sh):
    """True when enabled, False only when Security Hub is not enabled (InvalidAccessException), else None."""
    try:
        sh.get_enabled_standards()
        return True
    except ClientError as e:
        return False if _code(e) == "InvalidAccessException" else None
    except BotoCoreError:
        return None


def s3_spend_by_region(ce, today):
    """Last full month S3 spend per region (USD), regions with spend only. Raises ClientError if denied (the caller handles it)."""
    first = date.fromisoformat(today).replace(day=1)
    start = (first - timedelta(days=1)).replace(day=1)
    kwargs = {"TimePeriod": {"Start": start.isoformat(), "End": first.isoformat()}, "Granularity": "MONTHLY",
              "Metrics": ["UnblendedCost"], "GroupBy": [{"Type": "DIMENSION", "Key": "REGION"}],
              "Filter": {"Dimensions": {"Key": "SERVICE", "Values": ["Amazon Simple Storage Service"]}}}
    out = {}
    while True:
        r = ce.get_cost_and_usage(**kwargs)
        for period in r["ResultsByTime"]:
            for g in period["Groups"]:
                amount = float(g["Metrics"]["UnblendedCost"]["Amount"])
                if amount > 0 and g["Keys"][0] != "NoRegion":
                    out[g["Keys"][0]] = out.get(g["Keys"][0], 0) + amount
        if not r.get("NextPageToken"):
            return out
        kwargs["NextPageToken"] = r["NextPageToken"]
