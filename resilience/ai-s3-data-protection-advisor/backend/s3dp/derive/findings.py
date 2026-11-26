"""Per-bucket findings from the S3 check catalog. Each: {check, severity, title, description, action?}.

Deliberate omissions and narrowing relative to the S3 data protection check catalog:
- S3-REP-01 (no replication) is not a per-bucket finding: the engine's regional-disruption coverage and its
  recommended action already cover it.
- S3-LCY-01 (no lifecycle policy) is covered by the more actionable S3-LCY-03 and S3-LCY-04.
- S3-MFA-01 (MFA Delete off) is not raised: MFA Delete needs the root user and is rarely advisable.
- S3-BKP-02 (versioning cost) is merged into the S3-BKP-01 description as the versioning cost estimate.
- S3-ENC-02 (SSE-S3 only) is narrowed to regulated production data.
"""
from s3dp.model import EXCLUDED_ROLES, REGULATED_NAMING, has_word, size_label


def _f(check, severity, title, description, action=None):
    x = {"check": check, "severity": severity, "title": title, "description": description}
    if action:
        x["action"] = action
    return x


def _regulated(b):
    return bool(b.get("regional_required")) or has_word(b["name"], REGULATED_NAMING)


def _cost_text(v):
    if not v:
        return ""
    lo, hi = v["range"]
    amount = f"${v['monthly_usd']:,.2f}/month"
    if (lo, hi) != (v["monthly_usd"], v["monthly_usd"]):
        amount += f", range ${lo:,.2f}–${hi:,.2f}"
    return (f" Estimated cost of versioning: {amount} (basis: {v['basis']}, {v['churn_pct']}% churn, "
            f"{v['retention_days']}-day noncurrent retention), estimate.")


def _bkp01_title(b):
    suspended = b["versioning"] == "Suspended"
    tb = b.get("size_tb")
    if tb is None:
        return "Bucket with versioning suspended (size not measured)" if suspended else \
            "Unversioned bucket (size not measured)"
    if tb == 0:
        return "Empty bucket with versioning suspended" if suspended else "Empty unversioned bucket"
    return f"{size_label(tb)} {'with versioning suspended' if suspended else 'unversioned'}"


def for_bucket(b, continuous_plans=frozenset()):
    out, role = [], b["role"]
    if b.get("bucket_bpa") == "off":
        out.append(_f("S3-ACC-01", "HIGH", "Block Public Access is not fully enabled on the bucket or the account",
                      "Not all four Block Public Access settings are on, at bucket or at account level, so a policy "
                      "or ACL change can expose the bucket.",
                      "Enable Block Public Access on the bucket (or at account level)"))
    if b.get("is_public") is True:
        out.append(_f("S3-ACC-02", "HIGH", "Bucket is public",
                      "The bucket policy status reports the bucket as public.",
                      "Remove public access unless the bucket intentionally serves public content"))
    if role in EXCLUDED_ROLES:
        return out
    prod_like = role in ("production", "backup_target", "audit_trail")
    if b["versioning"] in ("Never enabled", "Suspended") and role != "replica":
        cost = _cost_text(b.get("vce"))
        verb = "Re-enable versioning (currently Suspended)" if b["versioning"] == "Suspended" else "Enable versioning"
        out.append(_f("S3-BKP-01", "HIGH" if prod_like else "MEDIUM",
                      _bkp01_title(b),
                      "Without versioning a delete or overwrite is permanent, and the bucket cannot be added to "
                      "AWS Backup or replicated." + cost,
                      f"{verb} with a NoncurrentVersionExpiration lifecycle rule"))
    if role == "production" and b["object_lock"] == "None" and b["policy"].get("delete_deny") is False:
        out.append(_f("S3-DEL-01", "MEDIUM", "No delete guard",
                      "No Object Lock, and no bucket policy denies both s3:DeleteObject and s3:DeleteObjectVersion "
                      "on every object (a s3:DeleteBucket deny protects no data).",
                      "Add a bucket policy that denies deletes outside an approved role"))
    if role == "production" and b["policy"].get("tls_only") is False:
        out.append(_f("S3-ACC-04", "MEDIUM", "TLS not enforced",
                      "No Deny statement on aws:SecureTransport = false: requests over plain HTTP are accepted.",
                      "Add a Deny statement for aws:SecureTransport = false"))
    if b.get("ownership") in ("BucketOwnerPreferred", "ObjectWriter"):
        out.append(_f("S3-ACC-03", "MEDIUM", "ACLs enabled",
                      f"Object Ownership is {b['ownership']}: object ACLs still grant access.",
                      "Set Object Ownership to BucketOwnerEnforced"))
    if role == "production" and b.get("logging") is False:
        out.append(_f("S3-ACC-05", "LOW", "No access logging",
                      "No server access logging: no forensic trail after a deletion (CloudTrail data events not checked)."))
    if role == "production" and b["encryption"] == "SSE-S3" and _regulated(b):
        out.append(_f("S3-ENC-02", "MEDIUM", "SSE-S3 on regulated data",
                      "Regulated data encrypted with S3-managed keys: no key policy separates who can decrypt.",
                      "Switch default encryption to SSE-KMS with Bucket Keys"))
    if b["encryption"] == "SSE-KMS" and not b.get("bucket_key"):      # Bucket Keys do not apply to DSSE-KMS
        out.append(_f("S3-ENC-03", "LOW", "SSE-KMS without Bucket Keys",
                      "Every object request calls AWS KMS; Bucket Keys cut KMS request cost by up to 99%.",
                      "Enable S3 Bucket Keys"))
    if b.get("abort_mpu") is False:
        out.append(_f("S3-LCY-03", "LOW", "No incomplete multipart upload cleanup",
                      "Incomplete multipart uploads are billed until deleted.",
                      "Add an AbortIncompleteMultipartUpload lifecycle rule (7 days)"))
    if b["versioning"] in ("Enabled", "Suspended") and b.get("noncurrent_expiration") is False:
        out.append(_f("S3-LCY-04", "LOW", "Noncurrent versions never expire",
                      "Version storage grows without limit.",
                      "Add a NoncurrentVersionExpiration lifecycle rule"))
    if role == "production" and _regulated(b) and b["object_lock"] == "None":
        out.append(_f("S3-LCY-02", "MEDIUM", "No Object Lock on regulated data",
                      "Regulated data can be deleted or overwritten by anyone with write access.",
                      "Enable Object Lock in Governance mode with a default retention"))
    if set(b.get("aws_backup_plans") or []) & set(continuous_plans) and b.get("eventbridge") is False:
        out.append(_f("BKP-ACC-07", "HIGH", "Continuous backup has silently stopped",
                      "The bucket is in a continuous AWS Backup rule but EventBridge notifications are off.",
                      "Enable EventBridge notifications on the bucket"))
    return out
