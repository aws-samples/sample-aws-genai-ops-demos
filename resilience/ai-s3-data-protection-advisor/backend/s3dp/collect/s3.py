"""Per-bucket S3 settings, read-only. Each setting -> {"ok": value} | {"absent": True} | {"error": code}."""
from botocore.exceptions import BotoCoreError, ClientError

ABSENT = {
    "object_lock": ("ObjectLockConfigurationNotFoundError",),
    "replication": ("ReplicationConfigurationNotFoundError",),
    "encryption": ("ServerSideEncryptionConfigurationNotFoundError",),
    "lifecycle": ("NoSuchLifecycleConfiguration",),
    "public_access_block": ("NoSuchPublicAccessBlockConfiguration",),
    "policy": ("NoSuchBucketPolicy",),
    "policy_status": ("NoSuchBucketPolicy",),
    "ownership": ("OwnershipControlsNotFoundError",),
    "tags": ("NoSuchTagSet",),
}


def _read(setting, fn):
    try:
        return {"ok": fn()}
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "Unknown")
        return {"absent": True} if code in ABSENT.get(setting, ()) else {"error": code}
    except BotoCoreError as e:
        return {"error": type(e).__name__}
    except (KeyError, IndexError, TypeError):
        return {"error": "UnexpectedResponse"}   # malformed response: unknown, never absent


def _bucket_name(arn):
    return arn.split(":::", 1)[-1] if arn else None


def list_buckets(s3):
    """Every bucket of the account with its region when ListBuckets returns it (None otherwise)."""
    out, kwargs = [], {"MaxBuckets": 1000}
    while True:
        r = s3.list_buckets(**kwargs)
        out += [{"name": b["Name"], "region": b.get("BucketRegion")} for b in r.get("Buckets", [])]
        if not r.get("ContinuationToken"):
            return out
        kwargs["ContinuationToken"] = r["ContinuationToken"]


LEGACY_LOCATION = {"EU": "eu-west-1", None: "us-east-1", "": "us-east-1"}


def bucket_region(s3, name):
    """Region of a bucket; None when it could not be read (callers treat None as unknown)."""
    r = _read("location", lambda: s3.get_bucket_location(Bucket=name).get("LocationConstraint"))
    if "ok" not in r:
        return None
    return LEGACY_LOCATION.get(r["ok"], r["ok"])


def collect_bucket(s3, name):
    """All settings of one bucket. `s3` must be a client in the bucket's region."""
    p = {"Bucket": name}

    def versioning():
        r = s3.get_bucket_versioning(**p)
        return {"status": r.get("Status"), "mfa_delete": r.get("MFADelete")}

    def object_lock():
        c = s3.get_object_lock_configuration(**p)["ObjectLockConfiguration"]
        ret = c.get("Rule", {}).get("DefaultRetention", {})
        return {"enabled": c.get("ObjectLockEnabled") == "Enabled", "mode": ret.get("Mode"),
                "days": ret.get("Days"), "years": ret.get("Years")}

    def delete_markers(rule):
        if "DeleteMarkerReplication" in rule:
            return rule["DeleteMarkerReplication"].get("Status") == "Enabled"
        return True if "Filter" not in rule else None   # V1 rules replicate delete markers; V2 without the element: unknown

    def replication():
        rules = s3.get_bucket_replication(**p)["ReplicationConfiguration"].get("Rules", [])
        return [{"status": r.get("Status"), "destination": _bucket_name(r["Destination"].get("Bucket")),
                 "storage_class": r["Destination"].get("StorageClass"),   # None: same class as the source
                 "delete_markers": delete_markers(r),
                 "rtc": r["Destination"].get("ReplicationTime", {}).get("Status") == "Enabled"} for r in rules]

    def encryption():
        rule = s3.get_bucket_encryption(**p)["ServerSideEncryptionConfiguration"]["Rules"][0]
        return {"algorithm": rule.get("ApplyServerSideEncryptionByDefault", {}).get("SSEAlgorithm"),
                "bucket_key": bool(rule.get("BucketKeyEnabled"))}

    def logging():
        le = s3.get_bucket_logging(**p).get("LoggingEnabled")
        return {"enabled": bool(le), "target": le.get("TargetBucket") if le else None}

    return {
        "versioning": _read("versioning", versioning),
        "object_lock": _read("object_lock", object_lock),
        "replication": _read("replication", replication),
        "encryption": _read("encryption", encryption),
        "lifecycle": _read("lifecycle", lambda: s3.get_bucket_lifecycle_configuration(**p).get("Rules", [])),
        "public_access_block": _read("public_access_block",
                                     lambda: s3.get_public_access_block(**p)["PublicAccessBlockConfiguration"]),
        "policy": _read("policy", lambda: s3.get_bucket_policy(**p)["Policy"]),
        "policy_status": _read("policy_status",
                               lambda: s3.get_bucket_policy_status(**p)["PolicyStatus"].get("IsPublic")),
        "ownership": _read("ownership", lambda: s3.get_bucket_ownership_controls(**p)["OwnershipControls"]["Rules"][0]
                           ["ObjectOwnership"]),
        "tags": _read("tags", lambda: {t["Key"]: t["Value"] for t in s3.get_bucket_tagging(**p).get("TagSet", [])}),
        "logging": _read("logging", logging),
        "eventbridge": _read("eventbridge", lambda: "EventBridgeConfiguration" in
                             s3.get_bucket_notification_configuration(**p)),
    }
