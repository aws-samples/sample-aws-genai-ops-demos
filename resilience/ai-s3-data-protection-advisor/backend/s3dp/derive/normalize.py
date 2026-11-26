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

from s3dp.model import TIB

ENC = {"AES256": "SSE-S3", "aws:kms": "SSE-KMS", "aws:kms:dsse": "DSSE-KMS"}
DELETE_TARGETS = ("s3:deleteobject", "s3:deleteobjectversion", "s3:deletebucket")
WILDCARD_ACTIONS = ("s3:*", "*")
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


def replication(r, region, regions):
    """Several enabled rules: prefer a known cross-region destination, then one whose region is not visible
    (inferred), then same-region. A source region that is not known makes the rule inferred."""
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

    rule = min(rules, key=kind)
    dest_region = regions.get(rule["destination"])
    out = {"destination": rule["destination"], "destination_region": dest_region,
           "delete_markers": rule["delete_markers"], "rtc": rule["rtc"]}
    k = kind(rule)
    if k == 2:
        out["srr"] = True
    elif k == 1:
        out["inferred"] = True
    return out


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


def _bucket_pab(r):
    if "ok" in r:
        return "on" if all(r["ok"].get(k) is True for k in PAB_FLAGS) else "off"
    if "absent" in r:
        return "off"
    return "unknown"


def bucket_bpa(r, account_bpa):
    """Effective Block Public Access: on if the bucket or the account blocks everything; else unknown if either
    could not be read; else off."""
    bucket = _bucket_pab(r)
    account = account_bpa if account_bpa in ("on", "off") else "unknown"
    if "on" in (bucket, account):
        return "on"
    if "unknown" in (bucket, account):
        return "unknown"
    return "off"


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


def _denies_delete(s):
    cond = s.get("Condition") or {}
    if "NotAction" in s:
        excluded = [a.lower() for a in _as_list(s["NotAction"]) if isinstance(a, str)]
        return not any(_match(p, "s3:deleteobject") for p in excluded) and _principal_scoped(cond)
    acts = [a.lower() for a in _as_list(s.get("Action")) if isinstance(a, str)]
    explicit = any(a not in WILDCARD_ACTIONS and any(_match(a, t) for t in DELETE_TARGETS) for a in acts)
    if explicit:
        return "aws:securetransport" not in _condition_keys(cond if isinstance(cond, dict) else {})
    return any(a in WILDCARD_ACTIONS for a in acts) and _principal_scoped(cond)


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


def policy(r):
    """delete_deny: a Deny that removes s3:DeleteObject / s3:DeleteObjectVersion / s3:DeleteBucket, either by an
    explicit delete action pattern (s3:Delete*, s3:DeleteObject*...), or by s3:* / * (or NotAction not covering
    s3:DeleteObject) with no condition or only principal-scoped conditions. A perimeter Deny (aws:SourceVpce,
    aws:SourceIp, aws:PrincipalOrgID, aws:SecureTransport...) on s3:* is not a delete guard.
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
    return {"delete_deny": any(_denies_delete(s) for s in deny), "tls_only": any(_tls_deny(s) for s in deny)}


def ownership(r):
    """Object Ownership: no OwnershipControls means the pre-2023 default ObjectWriter (ACLs enabled)."""
    if "absent" in r:
        return "ObjectWriter"
    return r.get("ok")


def _dominant_class(by_class):
    return max(by_class, key=by_class.get) if by_class else "UNKNOWN"


def normalize_bucket(item, regions, account_bpa, backup):
    """item = {name, account, region, s3: collect_bucket() output, metrics: metrics.assemble() entry or None}.
    backup: the region's protected_s3 ({bucket: [plan names]}), or None when it could not be read."""
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
        "bucket_bpa": bucket_bpa(s3["public_access_block"], account_bpa),
        "is_public": s3["policy_status"].get("ok", False if "absent" in s3["policy_status"] else None),
        "ownership": ownership(s3["ownership"]),
        "policy": policy(s3["policy"]),
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
