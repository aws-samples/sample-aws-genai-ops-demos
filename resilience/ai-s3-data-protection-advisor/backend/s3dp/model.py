"""Vocabulary shared by every layer. Prices are US East (N. Virginia) list prices per GB-month, used only for
estimates that the report labels as such."""
import re

RISKS = ("human_error", "malicious_activity", "regional_disruption")
RISK_LABEL = {
    "human_error": "Human or application errors",
    "malicious_activity": "Malicious activity",
    "regional_disruption": "Regional disruption",
}
ROLES = ("production", "backup_target", "replica", "audit_trail", "access_logs", "non_production", "tooling", "unknown")
ROLE_LABEL = {
    "production": "production", "backup_target": "backup target", "replica": "replica",
    "audit_trail": "audit trail", "access_logs": "access logs", "non_production": "non-production",
    "tooling": "tooling", "unknown": "unknown",
}
EXCLUDED_ROLES = ("non_production", "tooling", "access_logs")
PRICE = {"STANDARD": 0.023, "STANDARD_IA": 0.0125, "ONEZONE_IA": 0.01, "INTELLIGENT_TIERING": 0.023,
         "GLACIER_IR": 0.004, "GLACIER": 0.0036, "DEEP_ARCHIVE": 0.00099}
PRICE_NOTE = "US East (N. Virginia) list prices, before discounts, credits and taxes"

# CloudWatch BucketSizeBytes StorageType dimension -> storage class
STORAGE_TYPE_CLASS = {
    "StandardStorage": "STANDARD",
    "StandardIAStorage": "STANDARD_IA", "StandardIASizeOverhead": "STANDARD_IA",
    "StandardIAObjectOverhead": "STANDARD_IA",
    "OneZoneIAStorage": "ONEZONE_IA", "OneZoneIASizeOverhead": "ONEZONE_IA",
    "ReducedRedundancyStorage": "STANDARD",
    "IntelligentTieringFAStorage": "INTELLIGENT_TIERING", "IntelligentTieringIAStorage": "INTELLIGENT_TIERING",
    "IntelligentTieringAAStorage": "INTELLIGENT_TIERING", "IntelligentTieringAIAStorage": "INTELLIGENT_TIERING",
    "IntelligentTieringDAAStorage": "INTELLIGENT_TIERING",
    "GlacierInstantRetrievalStorage": "GLACIER_IR", "GlacierIRSizeOverhead": "GLACIER_IR",
    "GlacierStorage": "GLACIER", "GlacierStagingStorage": "GLACIER", "GlacierObjectOverhead": "GLACIER",
    "GlacierS3ObjectOverhead": "GLACIER",
    "DeepArchiveStorage": "DEEP_ARCHIVE", "DeepArchiveObjectOverhead": "DEEP_ARCHIVE",
    "DeepArchiveS3ObjectOverhead": "DEEP_ARCHIVE", "DeepArchiveStagingStorage": "DEEP_ARCHIVE",
}
TIB = 1024 ** 4
GB = 1024 ** 3
REGULATED_NAMING = ("legal", "contract", "clinical", "patient", "payment", "invoice", "pii", "hr", "medical", "finance")
CRITICAL_NAMING = ("clinical", "patient", "pii", "hr", "customer", "recording", "lims", "oracle", "sql",
                   "legal", "contract", "payment", "invoice")
NONPROD_NAMING = ("test", "dev", "sandbox", "sbx", "poc", "qa")
# Marker tag of the mock estate (s3dp/seed.py). Never sent to the model: it would decide the role on its own.
MOCK_TAG = "s3dp-demo"


AI_NOTICE = "Text written by a generative AI assistant and estimates based on list prices: verify before acting."
RULES_NOTICE = ("Rules-based report: no generative AI was used. Estimates are based on list prices: "
                "verify before acting.")
AI_OFF = {"classification": False, "narrative": "off"}


def ai_used(ai):
    """ai: run.ai / meta.ai ({"classification": bool, "narrative": "pending" | "complete" | "partial" |
    "unavailable" | "off"}). True when the generative AI assistant classified buckets or wrote report text."""
    ai = ai or AI_OFF
    return bool(ai.get("classification")) or ai.get("narrative") in ("pending", "complete", "partial")


def ai_notice(data):
    return AI_NOTICE if ai_used(data.get("meta", {}).get("ai")) else RULES_NOTICE


def name_tokens(name):
    """Lower-case tokens of a resource name, split on non-alphanumerics and camelCase
    (N2WSCpmRole -> n2ws, cpm, role; AcmeProduction -> acme, production)."""
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", name or "")
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def has_word(name, words):
    """True if a word of `words` is in `name`: words of 4 characters or fewer match whole name tokens only,
    longer words match anywhere (case-insensitive)."""
    low, tokens = (name or "").lower(), set(name_tokens(name))
    return any((w in tokens) if len(w) <= 4 else (w in low) for w in words)


def size_label(tb):
    """Size in TiB (size_tb is bytes / 1024**4) as a short GiB / TiB / PiB label; None means not measured."""
    if tb is None:
        return "Not measured"
    if tb >= 1024:
        pib = tb / 1024
        return f"{pib:.0f} PiB" if pib >= 10 else f"{pib:.2f}".rstrip("0").rstrip(".") + " PiB"
    if tb >= 10:
        return f"{tb:.0f} TiB"
    if tb >= 1:
        return f"{tb:.1f} TiB"
    return f"{tb * 1024:.0f} GiB" if tb > 0 else "empty"
