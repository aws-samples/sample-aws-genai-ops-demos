"""Rule-based bucket roles. assign() -> {bucket: (role or None, reason)}.

Order: relationships (replication destination, then CloudTrail/AWS Config names, then server access log target of
another bucket); backup-tool names; then the tier assignment rules of the S3 data protection review method,
production signals before non-production ones so the more protective role wins when signals conflict: production
environment or criticality tag; tooling and log name rules; account name with prod; bucket name with prod;
non-production environment tag; non-production account name; non-production bucket name.
Non-production phrases (non-prod, nonprod, pre-prod, preprod, uat) are non-production and never read as "prod".
Terraform state buckets are not tooling: they must be protected like production.
"""
import re

from s3dp.derive import vendors
from s3dp.model import name_tokens

PROD = {"prod", "prd", "production"}
NONPROD_PHRASES = {"nonprod", "preprod", "nonproduction", "preproduction", "uat"}
NONPROD_TAG = {"dev", "development", "test", "stg", "staging", "sandbox", "alpha", "beta", "qa", "uat"}
NONPROD_ACCOUNT = {"dev", "test", "staging", "sandbox"}
NONPROD_NAME = {"dev", "test", "stg", "staging", "sandbox", "sbx", "poc", "qa"}
CRIT_TAG = {"high", "critical", "p1"}
ENV_KEYS = ("environment", "env", "stage")
TOOLING = (r"^cdk-[a-z0-9]+-assets-", r"-cdk-assets-", r"athena-results", r"query-results",
           r"^sagemaker-[a-z]{2}-[a-z]+-\d-\d{12}$", r"^aws-glue-assets-", r"^elasticbeanstalk-", r"^codepipeline-")
LOGS = (r"-logging$", r"access-logs", r"-logs?$", r"-emr-logs", r"^aws-waf-logs-", r"elb-logs", r"alb-logs")
AUDIT = (r"^aws-cloudtrail-logs-", r"cloudtrail", r"^config-bucket-", r"aws-config")


def _nonprod_phrase(text):
    """non-prod / nonprod / pre-prod / preprod / uat (also across separators and camelCase)."""
    t = name_tokens(text)
    pairs = {a + b for a, b in zip(t, t[1:]) if a in ("non", "pre") and b in ("prod", "production")}
    return bool((set(t) | pairs) & NONPROD_PHRASES)


def _env(tags):
    for k, v in tags.items():
        if k.lower() in ENV_KEYS:
            return str(v)
    return None


def _tag_signals(tags):
    """(production tag reason or None, non-production tag reason or None)."""
    env = _env(tags)
    env_low = (env or "").lower()
    crit = next((str(v).lower() for k, v in tags.items() if k.lower() == "criticality"), None)
    nonprod = bool(env) and (_nonprod_phrase(env) or env_low in NONPROD_TAG)
    prod = env_low in PROD or crit in CRIT_TAG
    return ("Environment or criticality tag" if prod else None), ("Environment tag" if nonprod else None)


def _is_prod(text):
    """prod / prd / production / critical token, not counting the tokens of a non-production phrase."""
    t = name_tokens(text)
    skip = set()
    for i, (a, b) in enumerate(zip(t, t[1:])):
        if a in ("non", "pre") and b in ("prod", "production"):
            skip |= {i, i + 1}
    return bool({x for i, x in enumerate(t) if i not in skip} & (PROD | {"critical"}))


def _is_nonprod(text, nonprod_tokens):
    return _nonprod_phrase(text) or bool(set(name_tokens(text)) & nonprod_tokens)


def assign(buckets, account_name=""):
    names = {x["name"] for x in buckets}
    replicas = {x["replication"]["destination"]: x["name"] for x in buckets
                if isinstance(x.get("replication"), dict) and x["replication"].get("destination") in names}
    log_targets = {x["log_target"] for x in buckets if x.get("log_target") and x["log_target"] != x["name"]}
    acct_prod, acct_nonprod = _is_prod(account_name or ""), _is_nonprod(account_name or "", NONPROD_ACCOUNT)
    out = {}
    for x in buckets:
        n, low, tags = x["name"], x["name"].lower(), x.get("tags") or {}
        vendor = vendors.match(n)
        after_tags = False
        if n in replicas:
            role = ("replica", f"Replication destination of {replicas[n]}")
        elif any(re.search(p, low) for p in AUDIT):
            role = ("audit_trail", "CloudTrail or AWS Config delivery bucket (name)")
        elif n in log_targets:
            role = ("access_logs", "Server access log target of another bucket")
        elif vendor:
            role = ("backup_target", f"Backup landing zone: {vendor[0]} signature in the name")
        else:
            after_tags = True
            tag_prod, tag_nonprod = _tag_signals(tags)
            if tag_prod:
                role = ("production", tag_prod)
            elif any(re.search(p, low) for p in TOOLING):
                role = ("tooling", "Infrastructure or transient output bucket (name)")
            elif any(re.search(p, low) for p in LOGS):
                role = ("access_logs", "Log destination (name)")
            elif acct_prod:
                role = ("production", f"Account name {account_name}")
            elif _is_prod(n):
                role = ("production", "Bucket name")
            elif tag_nonprod:
                role = ("non_production", tag_nonprod)
            elif acct_nonprod:
                role = ("non_production", f"Account name {account_name}")
            elif _is_nonprod(n, NONPROD_NAME):
                role = ("non_production", "Bucket name")
            else:
                role = (None, "No tag, name or relationship decides the role")
        if after_tags and x.get("tags_read") is False:
            role = (role[0], role[1] + " (tags not readable)")
        out[n] = role
    return out
