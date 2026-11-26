"""Raw scan -> engine inventory. Pure function: no AWS calls."""
from s3dp.derive import costs, findings, normalize, roles, vendors, write_pattern
from s3dp.model import EXCLUDED_ROLES, MOCK_TAG, REGULATED_NAMING, ROLE_LABEL, has_word

IAC_TAGS = ("aws:cloudformation:stack-name", "terraform", "managed-by", "managed_by")
REGULATED_TAGS = {"regulated", "restricted", "confidential", "pii", "legal"}


def _normalized(raw):
    regions = {b["name"]: b["region"] for b in raw["buckets"]}
    out = []
    for item in raw["buckets"]:
        a = item["account"]
        bk = raw["regions"].get(a, {}).get(item["region"], {}).get("backup") or {}
        # protected_s3 None (or no AWS Backup data for the region) = not read: the bucket's coverage is NotVerified.
        b = normalize.normalize_bucket(item, regions, raw["accounts"][a]["account_bpa"], bk.get("protected_s3"))
        out.append(b)
    return out


def undecided(raw):
    """Buckets no rule classifies: name, tags and relationships only (what is sent to the model)."""
    norm = _normalized(raw)
    out = []
    for a, meta in raw["accounts"].items():
        mine = [b for b in norm if b["account"] == a]
        assigned = roles.assign(mine, meta["name"])          # once per account: assign() is O(buckets)
        for b in mine:
            if assigned[b["name"]][0] is None:
                out.append({"name": b["name"], "account_name": meta["name"], "region": b["region"],
                            "tags": {k: v for k, v in b["tags"].items() if k != MOCK_TAG}, "size_tb": b["size_tb"], "storage_class": b["storage_class"]})
    return out


def _config(accounts):
    """AWS Config signals. An account whose config (or one of its fields) is None could not be read: it is listed
    under recording_unknown / s3_rules_unknown and never counted as "not recording" or "no rules".
    auto_remediation is not collected in v1: None (unknown)."""
    cfg = {a: m.get("config") or {} for a, m in accounts.items()}
    return {"recording_accounts": [a for a, c in cfg.items() if c.get("recording")],
            "recording_unknown": [a for a, c in cfg.items() if c.get("recording") is None],
            "s3_rules": sorted({r for c in cfg.values() for r in c.get("s3_rules") or []}),
            "s3_rules_unknown": [a for a, c in cfg.items() if c.get("s3_rules") is None],
            "auto_remediation": None}


POSTURE_SUMS = ("plans_total", "plans_with_copy_actions", "restore_testing_plans", "frameworks")


def _backup_posture(raw):
    """Estate AWS Backup posture. A value that could not be read in any region makes the estate value None
    ("not readable"), never a partial sum; unreadable_regions lists [account, region, [settings]]. When no region has
    AWS Backup data (for example an account without buckets), every field is None: nothing was read."""
    if not any(info.get("backup") for regs in raw["regions"].values() for info in regs.values()):
        return dict(dict.fromkeys(("vaults", *POSTURE_SUMS)), air_gapped_vault_present=None, opt_in_off=None,
                    opt_in_unknown=None, unreadable_regions=None)
    vaults, sums = [], dict.fromkeys(POSTURE_SUMS, 0)
    opt_off, opt_unknown, unreadable = [], [], []
    for a, regs in raw["regions"].items():
        for r, info in regs.items():
            bk = info.get("backup")
            if not bk:
                unreadable.append([a, r, ["vaults", *POSTURE_SUMS, "s3_opt_in"]])
                vaults = None
                sums = dict.fromkeys(POSTURE_SUMS)
                opt_unknown.append([a, r])
                continue
            missing = []
            if bk.get("vaults") is None:
                missing.append("vaults")
                vaults = None
            elif vaults is not None:
                vaults += [dict(v, account=a, region=r) for v in bk["vaults"]]
            for k in POSTURE_SUMS:
                if bk.get(k) is None:
                    missing.append(k)
                    sums[k] = None
                elif sums[k] is not None:
                    sums[k] += bk[k]
            if bk.get("s3_opt_in") is False:
                opt_off.append([a, r])
            elif bk.get("s3_opt_in") is None:
                opt_unknown.append([a, r])
                missing.append("s3_opt_in")
            if missing:
                unreadable.append([a, r, missing])
    return dict(vaults=vaults, **sums,
                air_gapped_vault_present=None if vaults is None else any(v.get("air_gapped") for v in vaults),
                opt_in_off=opt_off, opt_in_unknown=opt_unknown, unreadable_regions=unreadable)


def build(raw, ai_roles=None):
    """ai_roles: {bucket: (role, reason, confidence)} from the classifier, applied only where rules decided nothing."""
    ai_roles = ai_roles or {}
    norm = _normalized(raw)
    accounts = raw["accounts"]
    for a, meta in accounts.items():
        mine = [b for b in norm if b["account"] == a]
        assigned = roles.assign(mine, meta["name"])
        for b in mine:
            role, reason = assigned[b["name"]]
            if role:
                b.update(role=role, role_source="rules", role_reason=reason)
            elif b["name"] in ai_roles and not (ai_roles[b["name"]][2] == "low"
                                                and ai_roles[b["name"]][0] in EXCLUDED_ROLES):
                r, why, conf = ai_roles[b["name"]]
                b.update(role=r, role_source="ai", role_reason=f"{why} (confidence: {conf})")
            elif b["name"] in ai_roles:
                # A low-confidence guess must never take a bucket out of scope: it stays a role to confirm.
                r, why, _ = ai_roles[b["name"]]
                b.update(role="unknown", role_source="default",
                         role_reason=f"{reason}; the generative AI assistant suggested {ROLE_LABEL[r]} with low "
                                     f"confidence ({why}), not applied")
            else:
                b.update(role="unknown", role_source="default", role_reason=reason)
    for b in norm:
        if isinstance(b["replication"], str) and any(
                isinstance(o["replication"], dict) and o["replication"].get("destination") == b["name"] for o in norm):
            src = next(o["name"] for o in norm if isinstance(o["replication"], dict)
                       and o["replication"].get("destination") == b["name"])
            b["replication"] = {"replica_of": src}
        cls = (b["tags"].get("data-classification") or b["tags"].get("DataClassification") or "").lower()
        b["regional_required"] = b["role"] == "production" and (
            cls in REGULATED_TAGS or has_word(b["name"], REGULATED_NAMING))
        if b["versioning"] in ("Never enabled", "Suspended"):     # unread versioning: no cost estimate
            b["vce"] = write_pattern.vce(b["by_class"], b["objects_series"], b["requests"],
                                         size_series_changed=write_pattern.size_changed(b["size_series"]),
                                         size_tb=b["size_tb"])
        else:
            b["vce"] = None
        pat = write_pattern.classify(b["objects_series"], b["size_series"], b["requests"]) \
            if b["objects_series"] else None
        b["write_pattern"] = write_pattern.SENTENCE[pat["pattern"]] if pat else None
    continuous = set()        # continuous rules are not distinguished in v1 (plan selection only)
    for b in norm:
        b["findings"] = findings.for_bucket(b, continuous_plans=continuous)
    cost_items, savings = costs.items([b for b in norm if b["role"] not in ("non_production", "tooling")])
    for b in norm:
        if b["name"] in savings:
            b["cost_saving"] = savings[b["name"]]
    third_party = vendors.estate([b["name"] for b in norm],
                                 [r for m in accounts.values() for r in (m.get("role_names") or [])],
                                 [s for m in accounts.values() for s in (m.get("stack_names") or [])])
    for b in norm:
        hit = vendors.match(b["name"])
        if hit and b["role"] == "backup_target":
            b["vendor"] = hit[0]
    posture = _backup_posture(raw)
    org = raw.get("organization")      # AWS Organizations data is not collected in v1: None = not read
    governance = {
        "organizations": None if org is None else {
            "enabled": bool(org), "scps": org.get("scps", []), "backup_policies": org.get("backup_policies", 0),
            "tag_policies": org.get("tag_policies", [])},
        "config": _config(accounts),
        "security_hub": {"enabled_accounts": [a for a, m in accounts.items() if m.get("security_hub")],
                         "unknown_accounts": [a for a, m in accounts.items() if m.get("security_hub") is None]},
        "backup_audit_manager_frameworks": posture["frameworks"],
        "central_backup_account": None,
        "tags": {"owner": [b["name"] for b in norm if any(k.lower() == "owner" for k in b["tags"])],
                 "data-classification": [b["name"] for b in norm if any(
                     k.lower() in ("data-classification", "dataclassification") for k in b["tags"])]},
        "iac_managed": [b["name"] for b in norm if any(k.lower() in IAC_TAGS or k.lower().startswith("aws:cloudformation")
                                                       for k in b["tags"])],
        "regulated_naming": list(REGULATED_NAMING),
        "restore_tests_last_12_months": posture["restore_testing_plans"],
    }
    return {
        "customer": accounts[raw["hub_account"]]["name"],
        "hub_account": raw["hub_account"],
        "scan_date": raw["scan_date"],
        "accounts": {a: {"name": m["name"], "account_bpa": m["account_bpa"]} for a, m in accounts.items()},
        "third_party": third_party,
        "backup_posture": {k: v for k, v in posture.items() if k != "frameworks"},
        "buckets": norm,
        "cost_optimization": cost_items,
        "governance": governance,
        "run": raw.get("run", {}),
    }
