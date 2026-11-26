"""Bucket role classification for buckets no rule decides. Sends names, tags, size and storage class only."""
import json
import logging

from s3dp.ai.bedrock import call_tool, error_code

log = logging.getLogger(__name__)
# replica is decided by rules only (a replication destination seen in the configuration), never by the model.
ALLOWED = ("production", "backup_target", "audit_trail", "access_logs", "non_production", "tooling")
SYSTEM = (
    "You classify Amazon S3 buckets by role for a data protection review. Roles: production (business data an "
    "application depends on), backup_target (copies written by a backup tool or database dumps), audit_trail (logs "
    "kept as evidence), access_logs (operational logs), non_production (dev, test, staging, sandbox), tooling "
    "(infrastructure artifacts, build outputs, transient query results). Use only the bucket name, tags, account "
    "name, size and storage class you are given. When unsure, choose the role that needs the stronger protection and "
    "say low confidence: a low-confidence non_production, tooling or access_logs answer is not applied. Reasons are "
    "one short sentence, no speculation about content.")
SCHEMA = {"type": "object", "required": ["buckets"], "properties": {"buckets": {"type": "array", "items": {
    "type": "object", "required": ["name", "role", "reason", "confidence"], "properties": {
        "name": {"type": "string"}, "role": {"type": "string", "enum": list(ALLOWED)},
        "reason": {"type": "string"}, "confidence": {"type": "string", "enum": ["high", "medium", "low"]}}}}}}


def classify(items, client, model_id, batch=50):
    """{bucket: (role, reason, confidence)} for the buckets the model classified with a valid role."""
    out = {}
    for i in range(0, len(items), batch):
        chunk = items[i:i + batch]
        asked = {x["name"] for x in chunk}
        try:
            res = call_tool(client, model_id, SYSTEM, "Classify these buckets:\n" + json.dumps(chunk, default=str),
                            "record_roles", SCHEMA)
        except Exception as e:  # noqa: BLE001 - any model failure leaves the buckets 'unknown'
            log.warning("role classification failed for %d buckets: %s", len(chunk), error_code(e))
            continue
        for x in res.get("buckets", []):
            if x.get("name") in asked and x.get("role") in ALLOWED:
                out[x["name"]] = (x["role"], str(x.get("reason", ""))[:200], x.get("confidence", "low"))
    return out
