"""Raw API answers -> engine vocabulary (see the plan's 'Engine vocabulary' table).

A setting that could not be read is unknown (None, "NotVerified" or "unknown"), never absent/off.

Fields added to the normalized bucket beyond the vocabulary table:
- aws_backup_plans: list of AWS Backup plan names that select the bucket, or None when the region's AWS Backup
  protected resources could not be read (aws_backup keeps the display string: "None" when no plan selects it,
  "NotVerified" when not read).
- tags_read: False when the tags could not be read (tags is then {} but means "unknown", not "no tags").
- size_series: daily total bytes, oldest -> newest, on days where every storage type has a point ([] if unknown).
"""
import fnmatch
import json
import re

from s3dp.model import TIB

ENC = {"AES256": "SSE-S3", "aws:kms": "SSE-KMS", "aws:kms:dsse": "DSSE-KMS"}
# Data deletion: removing an object (a delete marker on a versioned bucket) and permanently removing a version.
# s3:DeleteBucket only works on an empty bucket, so denying it protects no data.
OBJECT_DELETES = ("s3:deleteobject", "s3:deleteobjectversion")
PROBE_KEY = "\x00s3dp-probe\x00"     # an object key no Resource pattern names: only "every object" patterns match it
# Condition keys that scope a Deny to principals (a guard "deny deletes except for role X"), as opposed to network or
# organization perimeter keys (aws:SourceVpce, aws:SourceIp, aws:PrincipalOrgID, aws:SecureTransport...).
PRINCIPAL_KEYS = ("aws:principalarn", "aws:userid", "aws:principalaccount")
PRINCIPAL_KEY_PREFIXES = ("aws:principaltag/",)
PAB_FLAGS = ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")


def versioning(r):
    if "ok" not in r:
        return "NotVerified", None
    st = r["ok"].get("status")
    return {"Enabled": "Enabled", "Suspended": "Suspended"}.get(st, "Never enabled"), r["ok"].get("mfa_delete") == "Enabled"


def object_lock(r):
    if "absent" in r:
        return "None"
    if "ok" not in r:
        return "NotVerified"
    c = r["ok"]
    if not c.get("enabled"):
        return "None"
    if c.get("mode"):
        period = f"{c['years']}y" if c.get("years") else f"{c.get('days')}d"
        return f"{c['mode']} {period}"
    return "Enabled — no default rule; retained objects not verified"


def scope_label(scope):
    """'prefix exports/', 'tag team=data', 'prefix exports/ and 2 tags'..."""
    parts = [f"prefix {scope['prefix']}"] if scope.get("prefix") else []
    tags = scope.get("tags") or {}
    if len(tags) == 1:
        parts.append("tag " + "".join(f"{k}={v}" for k, v in tags.items()))
    elif tags:
        parts.append(f"{len(tags)} tags")
    return " and ".join(parts)


def replication(r, region, regions):
    """Several enabled rules: prefer a known cross-region destination, then one whose region is not visible
    (inferred), then same-region; within each, a rule covering the whole bucket before a filtered one. A source
    region that is not known makes the rule inferred. "filter" names the scope of a rule that does not cover the
    whole bucket (only its objects are copied)."""
    if "absent" in r:
        return "None"
    if "ok" not in r:
        return "NotVerified"
    rules = [x for x in r["ok"] if x.get("status") == "Enabled"]
    if not rules:
        return "None"

    def kind(x):
        dest_region = regions.get(x["destination"])
        if region is None or dest_region is None:
            return 1                    # inferred: region not visible (destination in another account)
        return 0 if dest_region != region else 2

    rule = min(rules, key=lambda x: (kind(x), x.get("scope") is not None))
    dest_region = regions.get(rule["destination"])
    out = {"destination": rule["destination"], "destination_region": dest_region,
           "delete_markers": rule["delete_markers"], "rtc": rule["rtc"]}
    k = kind(rule)
    if k == 2:
        out["srr"] = True
    elif k == 1:
        out["inferred"] = True
    if rule.get("scope"):
        out["filter"] = scope_label(rule["scope"])
    return out


def regional_copy(rep):
    """Regional disruption coverage of a normalized replication value: 'protected' only for a bucket-wide rule
    into another, verified region (or the destination of one); a same-region copy shares the region's fate."""
    if not isinstance(rep, dict):
        return {"None": "unprotected"}.get(rep, "unknown")
    if rep.get("srr"):
        return "unprotected"
    if rep.get("inferred"):
        return "unknown"
    return "partial" if rep.get("filter") else "protected"


def encryption(r):
    if "absent" in r:
        return "SSE-S3", False          # default encryption since January 2023
    if "ok" not in r:
        return "NotVerified", False
    alg = ENC.get(r["ok"].get("algorithm"))
    if alg is None:
        return "NotVerified", False     # unrecognised or missing algorithm: not verified
    return alg, bool(r["ok"].get("bucket_key"))


def _bucket_wide(rule):
    """A rule with no prefix/filter scope: Filter absent, empty, or Prefix "" (and no legacy top-level Prefix)."""
    if rule.get("Prefix"):
        return False
    f = rule.get("Filter")
    return not f or f == {"Prefix": ""}


def lifecycle(r):
    """noncurrent_expiration and abort_mpu count only bucket-wide rules; ExpiredObjectDeleteMarker alone is not a
    noncurrent version expiration."""
    keys = ("lifecycle", "noncurrent_expiration", "abort_mpu", "transitions")
    if "absent" in r:
        return dict.fromkeys(keys, False)
    if "ok" not in r:
        return dict.fromkeys(keys, None)
    on = [x for x in r["ok"] if x.get("Status") == "Enabled"]
    wide = [x for x in on if _bucket_wide(x)]
    return {"lifecycle": bool(on),
            "noncurrent_expiration": any("NoncurrentVersionExpiration" in x for x in wide),
            "abort_mpu": any("AbortIncompleteMultipartUpload" in x for x in wide),
            "transitions": any(x.get("Transitions") for x in on)}


def _bucket_flags(r):
    """{flag: bool} of the bucket (all False when none is set), or None when unread."""
    if "ok" in r:
        return {k: r["ok"].get(k) is True for k in PAB_FLAGS}
    if "absent" in r:
        return dict.fromkeys(PAB_FLAGS, False)
    return None


def _account_flags(account_bpa, account_flags):
    if account_flags is not None:
        return account_flags
    # Raw scans without per-flag account data: "on" is all four, "off" counts as none set (the old reading).
    return {"on": dict.fromkeys(PAB_FLAGS, True), "off": dict.fromkeys(PAB_FLAGS, False)}.get(account_bpa)


def bucket_bpa(r, account_bpa, account_flags=None):
    """Effective Block Public Access, flag by flag, the way S3 applies it: a setting is in effect when the bucket or
    the account sets it, so the two levels can complement each other. on: all four in effect; off: a setting known
    off at both levels; unknown: a setting off at one level and unread at the other."""
    bucket, account = _bucket_flags(r), _account_flags(account_bpa, account_flags)
    state = []
    for k in PAB_FLAGS:
        b, a = (None if bucket is None else bucket[k]), (None if account is None else account.get(k))
        state.append(True if True in (a, b) else None if None in (a, b) else False)
    if all(state):
        return "on"
    return "off" if False in state else "unknown"


def _as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _condition_keys(cond):
    return [k.lower() for v in cond.values() if isinstance(v, dict) for k in v]


def _principal_scoped(cond):
    """No condition, or every condition key scopes principals."""
    if not cond:
        return True
    if not isinstance(cond, dict) or not all(isinstance(v, dict) for v in cond.values()):
        return False
    return all(k in PRINCIPAL_KEYS or k.startswith(PRINCIPAL_KEY_PREFIXES) for k in _condition_keys(cond))


def _match(pattern, action):
    return fnmatch.fnmatchcase(action, pattern.lower())


def _arn_match(pattern, value):
    """IAM ARN matching: * any run of characters, ? one character, everything else literal."""
    rx = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern)
    return re.fullmatch(rx, value, re.S) is not None


def _every_object(s, bucket):
    """The statement's Resource covers every object of the bucket (bucket None: only wildcard resources do). A
    NotResource statement is not credited: what it leaves out is not checked."""
    if "NotResource" in s:
        return False
    for p in _as_list(s.get("Resource")):
        if not isinstance(p, str):
            continue
        parts = p.split(":")
        partition = parts[1] if p.startswith("arn:") and len(parts) > 5 else "aws"
        if _arn_match(p, f"arn:{partition}:s3:::{bucket or PROBE_KEY}/{PROBE_KEY}"):
            return True
    return False


def _object_deletes_denied(s, bucket):
    """Which of OBJECT_DELETES the Deny statement blocks on every object, for everyone or every principal but some.
    A perimeter condition (aws:SourceVpce, aws:SourceIp, aws:PrincipalOrgID, aws:SecureTransport...) is not a guard:
    a principal inside the perimeter can still delete."""
    if not _principal_scoped(s.get("Condition") or {}) or not _every_object(s, bucket):
        return set()
    if "NotAction" in s:
        excluded = [a.lower() for a in _as_list(s["NotAction"]) if isinstance(a, str)]
        return {t for t in OBJECT_DELETES if not any(_match(p, t) for p in excluded)}
    acts = [a.lower() for a in _as_list(s.get("Action")) if isinstance(a, str)]
    return {t for t in OBJECT_DELETES if any(_match(a, t) for a in acts)}


def _tls_deny(s):
    cond = s.get("Condition") or {}
    if not isinstance(cond, dict):
        return False
    for op, kv in cond.items():
        if op.lower() not in ("bool", "boolifexists") or not isinstance(kv, dict):
            continue
        for k, v in kv.items():
            vals = _as_list(v)
            if k.lower() == "aws:securetransport" and len(vals) == 1 and str(vals[0]).lower() == "false":
                return True
    return False


def policy(r, bucket=None):
    """delete_deny: Deny statements that together block both s3:DeleteObject and s3:DeleteObjectVersion on every
    object of the bucket (a Resource naming bucket/* or wider; a prefix is not every object), with no condition or
    only principal-scoped conditions, through explicit actions, s3:* / * or a NotAction that leaves them out. A
    s3:DeleteBucket deny alone is not a guard: it protects an empty bucket, not data.
    tls_only: a Deny with Bool/BoolIfExists aws:SecureTransport = "false"."""
    unknown = {"delete_deny": None, "tls_only": None}
    if "absent" in r:
        return {"delete_deny": False, "tls_only": False}
    if "ok" not in r:
        return unknown
    try:
        doc = r["ok"] if isinstance(r["ok"], dict) else json.loads(r["ok"])
    except (TypeError, ValueError):
        return unknown
    if not isinstance(doc, dict):
        return unknown
    stmts = _as_list(doc.get("Statement"))
    if not all(isinstance(s, dict) for s in stmts):
        return unknown
    deny = [s for s in stmts if s.get("Effect") == "Deny"]
    blocked = set().union(*(_object_deletes_denied(s, bucket) for s in deny)) if deny else set()
    return {"delete_deny": blocked >= set(OBJECT_DELETES), "tls_only": any(_tls_deny(s) for s in deny)}


def ownership(r):
    """Object Ownership: no OwnershipControls means the pre-2023 default ObjectWriter (ACLs enabled)."""
    if "absent" in r:
        return "ObjectWriter"
    return r.get("ok")


def _dominant_class(by_class):
    return max(by_class, key=by_class.get) if by_class else "UNKNOWN"


def normalize_bucket(item, regions, account_bpa, backup, account_flags=None):
    """item = {name, account, region, s3: collect_bucket() output, metrics: metrics.assemble() entry or None}.
    backup: the region's protected_s3 ({bucket: [plan names]}), or None when it could not be read.
    account_flags: the account's four Block Public Access settings, when the scan read them."""
    s3, m = item["s3"], item.get("metrics") or {}
    ver, mfa = versioning(s3["versioning"])
    enc, bucket_key = encryption(s3["encryption"])
    errors = {k: v["error"] for k, v in s3.items() if isinstance(v, dict) and "error" in v}
    size = m.get("size_bytes")
    plans = None if backup is None else list(backup.get(item["name"], []))
    b = {
        "name": item["name"], "account": item["account"], "region": item["region"],
        "versioning": ver, "mfa_delete": mfa, "object_lock": object_lock(s3["object_lock"]),
        "replication": replication(s3["replication"], item["region"], regions),
        "encryption": enc, "bucket_key": bucket_key,
        "bucket_bpa": bucket_bpa(s3["public_access_block"], account_bpa, account_flags),
        "is_public": s3["policy_status"].get("ok", False if "absent" in s3["policy_status"] else None),
        "ownership": ownership(s3["ownership"]),
        "policy": policy(s3["policy"], item["name"]),
        "logging": s3["logging"]["ok"]["enabled"] if "ok" in s3["logging"] else None,
        "log_target": s3["logging"]["ok"]["target"] if "ok" in s3["logging"] else None,
        "eventbridge": s3["eventbridge"].get("ok"),
        "tags": s3["tags"].get("ok", {}),
        "tags_read": "error" not in s3["tags"],
        "aws_backup": "NotVerified" if plans is None else ", ".join(plans) or "None",
        "aws_backup_plans": plans,
        "storage_class": _dominant_class(m.get("by_class", {})),
        "by_class": m.get("by_class", {}),
        "size_tb": None if size is None else size / TIB,
        "size_as_of": m.get("size_as_of"),
        "objects": m.get("objects"),
        "objects_series": m.get("objects_series", []),
        "size_series": m.get("size_series", []),
        "requests": m.get("requests"),
        "errors": errors,
    }
    b.update(lifecycle(s3["lifecycle"]))
    return b
