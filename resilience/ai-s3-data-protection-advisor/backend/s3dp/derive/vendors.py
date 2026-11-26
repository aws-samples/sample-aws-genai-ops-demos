"""Third-party backup tool signatures (the S3 data protection check catalog's vendor signatures)."""
from s3dp.model import name_tokens

# (pattern, vendor, confidence). Patterns of 4 characters or fewer match whole name tokens only (tokens split on
# non-alphanumerics and camelCase, see model.name_tokens).
SIGNATURES = [
    ("commvault", "Commvault", "strong"), ("cv-backup", "Commvault", "strong"), ("metallic", "Commvault", "variant"),
    ("veeam", "Veeam", "strong"), ("veaam", "Veeam", "variant"), ("veem", "Veeam", "variant"),
    ("vbr", "Veeam", "variant"),
    ("rubrik", "Rubrik", "strong"), ("rubrick", "Rubrik", "variant"), ("polaris", "Rubrik", "variant"),
    ("cohesity", "Cohesity", "strong"), ("chsty", "Cohesity", "variant"),
    ("netbackup", "Veritas NetBackup", "strong"), ("veritas", "Veritas NetBackup", "strong"),
    ("nbu", "Veritas NetBackup", "strong"),
    ("druva", "Druva", "strong"), ("phoenix", "Druva", "variant"), ("clumio", "Clumio", "strong"), ("n2ws", "N2W", "strong"), ("cpm", "N2W", "strong"),
    ("msp360", "MSP360", "strong"), ("cloudberry", "MSP360", "strong"), ("acronis", "Acronis", "strong"),
    ("carbonite", "Carbonite", "strong"), ("datto", "Datto", "strong"), ("arcserve", "Arcserve", "strong"),
    ("unitrends", "Unitrends", "strong"), ("zerto", "Zerto", "strong"), ("hycu", "HYCU", "strong"),
    ("velero", "Velero (EKS)", "strong"),
    ("barman", "Database dump", "landing_zone"), ("pgbackrest", "Database dump", "landing_zone"),
    ("wal-g", "Database dump", "landing_zone"), ("walg", "Database dump", "landing_zone"),
    ("rman", "Database dump", "landing_zone"), ("oracle-archive", "Database dump", "landing_zone"),
    ("mysqldump", "Database dump", "landing_zone"), ("mariabackup", "Database dump", "landing_zone"),
    ("xtrabackup", "Database dump", "landing_zone"), ("sql-backup", "Database dump", "landing_zone"),
    ("sqlbackup", "Database dump", "landing_zone"), ("dbbackup", "Database dump", "landing_zone"),
    ("db-dump", "Database dump", "landing_zone"), ("fullbkp", "Database dump", "landing_zone"),
    ("dbbak", "Database dump", "landing_zone"),
]
RANK = {"strong": 0, "variant": 1, "landing_zone": 2}


def match(name):
    """(vendor, confidence) for the best signature in `name`, or None."""
    low = name.lower()
    tokens = set(name_tokens(name))
    hits = [(v, c) for p, v, c in SIGNATURES if (p in tokens if len(p) <= 4 else p in low)]
    return min(hits, key=lambda h: RANK[h[1]]) if hits else None


def estate(bucket_names, role_names, stack_names):
    """Vendors present in the account, with evidence. Database dump zones are not a vendor."""
    found = {}
    for kind, names in (("Bucket name", bucket_names), ("IAM role", role_names or []),
                        ("CloudFormation stack", stack_names or [])):
        for n in names:
            hit = match(n)
            if hit and hit[0] != "Database dump":
                v = found.setdefault(hit[0], {"vendor": hit[0], "evidence": [], "confidence": hit[1]})
                v["evidence"].append(f"{kind} {n}")
                if RANK[hit[1]] < RANK[v["confidence"]]:
                    v["confidence"] = hit[1]
    return [dict(v, evidence="; ".join(v["evidence"])) for v in found.values()]
