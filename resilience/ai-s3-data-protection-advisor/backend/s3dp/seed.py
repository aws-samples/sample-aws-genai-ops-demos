"""Mock estate for a sandbox account: small private buckets with deliberate data protection gaps, a check of a live
scan against what each bucket should show, and a cleanup.

This is the only part of the solution that writes to AWS. It refuses an account whose alias reads as production,
every bucket keeps the four Block Public Access settings on, and the cleanup deletes only buckets (and the one IAM
role) that carry the mock tag AND the account's mock prefix.

Buckets (prefix s3dp-<account>-; no "demo" or "mock" in names, so the generative AI assistant judges them like real buckets), primary region unless noted:
  orders-prod        production by name, never versioned, no bucket policy, server access logs to access-logs
  access-logs        server access log target of orders-prod
  customer-records   production by tag, versioning suspended, ACLs enabled (ObjectWriter), TLS enforced
  veeam-repo         backup landing zone, versioned, no Object Lock, noncurrent versions never expire
  rubrik-archive     backup landing zone, Object Lock enabled without a default retention, full lifecycle
  media-prod         production, versioned, replicated to media-replica, delete guard and TLS policy
  media-replica      replication destination (secondary region)
  app-prod-a         production twin, versioned (primary region)
  app-prod-b         production twin, never versioned, logs to itself (secondary region)
  ledger-prod        production, the bucket policy cannot be read (Deny s3:GetBucketPolicy): must stay unconfirmed
  analytics-dev      non-production by tag, never versioned: no versioning finding expected
  shared-data        owner and application tags only: no rule decides the role (left to the generative AI
                     assistant, or unknown without it)

Storage sizes come from daily CloudWatch metrics: they show as "not measured" until one to two days after seeding.
Scan as an IAM principal, not the root user: root can always read the ledger-prod policy, so its "unconfirmed"
expectation would not hold. Commercial partition (aws) only.
"""
import json
import time

from botocore.exceptions import ClientError

from s3dp.collect import s3 as s3c
from s3dp.derive.roles import _is_prod
from s3dp.model import MOCK_TAG

TAG_KEY, TAG_VALUE = MOCK_TAG, "mock"
ROLE_NAME = "s3dp-demo-replication"
PAB = {k: True for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")}
FULL_LIFECYCLE = [{"ID": "s3dp-demo-noncurrent", "Status": "Enabled", "Filter": {},
                   "NoncurrentVersionExpiration": {"NoncurrentDays": 30},
                   "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}}]
ABORT_ONLY = [{"ID": "s3dp-demo-abort-mpu", "Status": "Enabled", "Filter": {},
               "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}}]

# suffix -> (region slot, settings, expectations). Expectations use inventory.json bucket fields; "findings" lists
# checks that must be raised, "no_findings" checks that must not be.
SPEC = {
    "orders-prod": ("primary", {"versioning": None, "log_to": "access-logs"},
                    {"role": "production", "versioning": "Never enabled", "logging": True,
                     "log_target": "@access-logs", "policy": {"delete_deny": False, "tls_only": False},
                     "findings": ["S3-BKP-01", "S3-DEL-01", "S3-ACC-04", "S3-LCY-03"]}),
    "access-logs": ("primary", {"versioning": None, "log_sink": True},
                    {"role": "access_logs", "no_findings": ["S3-BKP-01"]}),
    "customer-records": ("primary", {"versioning": "Suspended", "ownership": "ObjectWriter", "tls": True,
                                     "tags": {"Environment": "production", "Criticality": "high"},
                                     "lifecycle": FULL_LIFECYCLE},
                         {"role": "production", "versioning": "Suspended", "ownership": "ObjectWriter",
                          "policy": {"tls_only": True}, "findings": ["S3-BKP-01", "S3-ACC-03"],
                          "no_findings": ["S3-ACC-04"]}),
    "veeam-repo": ("primary", {"versioning": "Enabled", "lifecycle": ABORT_ONLY},
                   {"role": "backup_target", "versioning": "Enabled", "object_lock": "None",
                    "noncurrent_expiration": False, "findings": ["S3-LCY-04"], "no_findings": ["S3-BKP-01"]}),
    "rubrik-archive": ("primary", {"object_lock": True, "lifecycle": FULL_LIFECYCLE},
                       {"role": "backup_target", "versioning": "Enabled", "object_lock": "Enabled*",
                        "no_findings": ["S3-LCY-04", "S3-LCY-03"]}),
    "media-prod": ("primary", {"versioning": "Enabled", "replicate_to": "media-replica", "tls": True,
                               "delete_guard": True, "lifecycle": FULL_LIFECYCLE},
                   {"role": "production", "versioning": "Enabled", "replication.destination": "@media-replica",
                    "replication.destination_region": "secondary", "policy": {"delete_deny": True, "tls_only": True},
                    "no_findings": ["S3-BKP-01", "S3-DEL-01", "S3-ACC-04"]}),
    "media-replica": ("secondary", {"versioning": "Enabled", "objects": False, "lifecycle": FULL_LIFECYCLE},
                      {"role": "replica", "replication.replica_of": "@media-prod", "no_findings": ["S3-BKP-01"]}),
    "app-prod-a": ("primary", {"versioning": "Enabled", "lifecycle": FULL_LIFECYCLE},
                   {"role": "production", "versioning": "Enabled", "no_findings": ["S3-BKP-01"]}),
    "app-prod-b": ("secondary", {"versioning": None, "log_to": "app-prod-b", "log_sink": True},
                   {"role": "production", "versioning": "Never enabled", "logging": True,
                    "log_target": "@app-prod-b", "findings": ["S3-BKP-01"]}),
    "ledger-prod": ("primary", {"versioning": "Enabled", "lifecycle": FULL_LIFECYCLE, "hide_policy": True},
                    {"role": "production", "policy": {"delete_deny": None, "tls_only": None},
                     "errors.policy": "AccessDenied", "no_findings": ["S3-DEL-01", "S3-ACC-04", "S3-BKP-01"]}),
    "analytics-dev": ("primary", {"versioning": None, "tags": {"Environment": "dev"}},
                      {"role": "non_production", "versioning": "Never enabled", "no_findings": ["S3-BKP-01"]}),
    "shared-data": ("primary", {"versioning": None, "tags": {"owner": "data-platform",
                                                              "application": "customer-analytics"}},
                    {"versioning": "Never enabled", "role_source": "@ai-or-default"}),
}
# Creation order: log targets before the buckets that log to them, replication destination before its source.
ORDER = ("access-logs", "media-replica", "app-prod-b", "orders-prod", "customer-records", "veeam-repo",
         "rubrik-archive", "media-prod", "app-prod-a", "ledger-prod", "analytics-dev", "shared-data")


def prefix(account_id):
    return f"s3dp-{account_id}-"


def default_secondary(primary):
    return "us-east-1" if primary == "us-west-2" else "us-west-2"


def plan(account_id, primary, secondary):
    """[(bucket name, region, suffix)] in creation order."""
    if primary == secondary:
        raise ValueError("The secondary region must differ from the primary region (replication and twin buckets)")
    regions = {"primary": primary, "secondary": secondary}
    return [(prefix(account_id) + s, regions[SPEC[s][0]], s) for s in ORDER]


def check_account(alias):
    """Refuse an account whose alias reads as production."""
    if alias and _is_prod(alias):
        raise SystemExit(f"Account alias {alias} reads as production: the mock estate is for sandbox accounts only")


def _code(e):
    return e.response.get("Error", {}).get("Code")


def check_partition(session, region):
    if session.get_partition_for_region(region) != "aws":
        raise SystemExit(f"{region} is not in the commercial partition: the mock estate supports aws regions only")


def _retry(fn, attempts=10, delay=3, sleep=time.sleep):
    """A bucket's new versioning state (or a new role) can be rejected by S3 for a few seconds. AccessDenied is not
    retried: it would hide a missing iam:PassRole."""
    for i in range(attempts):
        try:
            return fn()
        except ClientError as e:
            if i == attempts - 1 or _code(e) not in ("InvalidRequest", "InvalidArgument"):
                raise
            sleep(delay)


def _policy(name, account_id, cfg):
    arn = f"arn:aws:s3:::{name}"
    stmts = []
    if cfg.get("tls"):
        stmts.append({"Sid": "DenyInsecureTransport", "Effect": "Deny", "Principal": "*", "Action": "s3:*",
                      "Resource": [arn, arn + "/*"], "Condition": {"Bool": {"aws:SecureTransport": "false"}}})
    if cfg.get("delete_guard"):
        stmts.append({"Sid": "DenyBucketDelete", "Effect": "Deny", "Principal": "*", "Action": "s3:DeleteBucket",
                      "Resource": arn})
    if cfg.get("hide_policy"):
        # Hides the policy from every reader; the account can still replace or delete it (cleanup does).
        stmts.append({"Sid": "HidePolicy", "Effect": "Deny", "Principal": "*", "Action": "s3:GetBucketPolicy",
                      "Resource": arn})
    if cfg.get("log_sink"):
        stmts.append({"Sid": "S3ServerAccessLogs", "Effect": "Allow",
                      "Principal": {"Service": "logging.s3.amazonaws.com"}, "Action": "s3:PutObject",
                      "Resource": arn + "/*", "Condition": {"StringEquals": {"aws:SourceAccount": account_id}}})
    return {"Version": "2012-10-17", "Statement": stmts} if stmts else None


def _role(iam, account_id, source, dest):
    """(role ARN, True when the role was just created)."""
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "s3.amazonaws.com"}, "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"aws:SourceAccount": account_id}}}]}
    perms = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["s3:GetReplicationConfiguration", "s3:ListBucket"],
         "Resource": f"arn:aws:s3:::{source}"},
        {"Effect": "Allow", "Action": ["s3:GetObjectVersionForReplication", "s3:GetObjectVersionAcl",
                                       "s3:GetObjectVersionTagging"], "Resource": f"arn:aws:s3:::{source}/*"},
        {"Effect": "Allow", "Action": ["s3:ReplicateObject", "s3:ReplicateDelete", "s3:ReplicateTags"],
         "Resource": f"arn:aws:s3:::{dest}/*"}]}
    try:
        arn = iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(trust),
                              Description="S3 replication for the S3 data protection advisor mock estate",
                              Tags=[{"Key": TAG_KEY, "Value": TAG_VALUE}])["Role"]["Arn"]
        created = True
    except ClientError as e:
        if _code(e) != "EntityAlreadyExists":
            raise
        role = iam.get_role(RoleName=ROLE_NAME)["Role"]
        if {t["Key"]: t["Value"] for t in role.get("Tags", [])}.get(TAG_KEY) != TAG_VALUE:
            raise SystemExit(f"IAM role {ROLE_NAME} exists without the {TAG_KEY} tag: not reusing it")
        arn, created = role["Arn"], False
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="replication", PolicyDocument=json.dumps(perms))
    return arn, created


def _create(s3, name, region, cfg):
    kw = {"Bucket": name}
    if region != "us-east-1":
        kw["CreateBucketConfiguration"] = {"LocationConstraint": region}
    if cfg.get("object_lock"):
        kw["ObjectLockEnabledForBucket"] = True
    try:
        s3.create_bucket(**kw)
    except ClientError as e:
        if _code(e) != "BucketAlreadyOwnedByYou":
            raise
    s3.get_waiter("bucket_exists").wait(Bucket=name)


def seed(session, account_id, primary, secondary, log=print, sleep=time.sleep):
    """Create or update the mock estate. Returns the bucket names."""
    names = {s: n for n, _, s in plan(account_id, primary, secondary)}
    clients = {r: session.client("s3", region_name=r) for r in (primary, secondary)}
    for name, region, suffix in plan(account_id, primary, secondary):
        cfg, s3 = SPEC[suffix][1], clients[region]
        _create(s3, name, region, cfg)
        s3.put_public_access_block(Bucket=name, PublicAccessBlockConfiguration=PAB)
        s3.put_bucket_tagging(Bucket=name, Tagging={"TagSet": [
            {"Key": k, "Value": v} for k, v in {TAG_KEY: TAG_VALUE, **cfg.get("tags", {})}.items()]})
        if cfg.get("ownership"):
            s3.put_bucket_ownership_controls(Bucket=name, OwnershipControls={
                "Rules": [{"ObjectOwnership": cfg["ownership"]}]})
        status = cfg.get("versioning")
        if status == "Suspended":      # suspended means it was enabled once
            s3.put_bucket_versioning(Bucket=name, VersioningConfiguration={"Status": "Enabled"})
        if status:
            s3.put_bucket_versioning(Bucket=name, VersioningConfiguration={"Status": status})
        if cfg.get("lifecycle"):
            s3.put_bucket_lifecycle_configuration(Bucket=name, LifecycleConfiguration={"Rules": cfg["lifecycle"]})
        pol = _policy(name, account_id, cfg)
        if pol:
            s3.put_bucket_policy(Bucket=name, Policy=json.dumps(pol))
        if cfg.get("log_to"):
            s3.put_bucket_logging(Bucket=name, BucketLoggingStatus={"LoggingEnabled": {
                "TargetBucket": names[cfg["log_to"]], "TargetPrefix": f"{name}/"}})
        if cfg.get("replicate_to"):
            dest = names[cfg["replicate_to"]]
            role, created = _role(session.client("iam"), account_id, name, dest)
            if created:    # objects written before S3 can assume a new role fail replication for good
                sleep(15)
            _retry(lambda: s3.put_bucket_replication(Bucket=name, ReplicationConfiguration={
                "Role": role, "Rules": [{"ID": "s3dp-demo", "Status": "Enabled", "Priority": 1, "Filter": {},
                                         "DeleteMarkerReplication": {"Status": "Enabled"},
                                         "Destination": {"Bucket": f"arn:aws:s3:::{dest}"}}]}), sleep=sleep)
        if cfg.get("objects", True):
            for i in range(3):
                # Object Lock buckets reject a PutObject without an integrity checksum.
                s3.put_object(Bucket=name, Key=f"mock/object-{i}.txt", Body=f"mock object {i}\n".encode(),
                              ChecksumAlgorithm="CRC32")
            # Overwrite one object (a noncurrent version once versioning has propagated, which can take minutes).
            if status == "Enabled" or cfg.get("object_lock"):
                s3.put_object(Bucket=name, Key="mock/object-0.txt", Body=b"mock object 0, second version\n",
                              ChecksumAlgorithm="CRC32")
        log(f"{name} ({region})")
    return list(names.values())


def _get(b, path):
    for part in path.split("."):
        b = b.get(part) if isinstance(b, dict) else None
    return b


def check(inv, account_id=None):
    """Compare a scan's inventory.json with the mock estate. Returns (rows, ok); rows are
    [bucket suffix, field, expected, actual, "ok" | "MISMATCH" | "MISSING"]."""
    buckets = inv.get("buckets", [])
    if account_id is None:
        hit = next((b for b in buckets if b["name"].startswith("s3dp-")
                    and b["name"].endswith("-" + ORDER[0])), None)
        if hit is None:
            return [["*", "buckets", "mock estate", "none found", "MISSING"]], False
        account_id = hit["name"][len("s3dp-"):-len("-" + ORDER[0])]
    p = prefix(account_id)
    by_suffix = {b["name"][len(p):]: b for b in buckets if b["name"].startswith(p)}
    primary = by_suffix.get("orders-prod", {}).get("region")
    secondary = by_suffix.get("media-replica", {}).get("region")
    rows = []
    for suffix in ORDER:
        slot, _, exp = SPEC[suffix]
        b = by_suffix.get(suffix)
        if b is None:
            rows.append([suffix, "bucket", "scanned", "not in the scan", "MISSING"])
            continue
        want_region = primary if slot == "primary" else secondary
        rows.append([suffix, "region", want_region, b["region"], "ok" if b["region"] == want_region else "MISMATCH"])
        rows.append([suffix, "bucket_bpa", "on", b.get("bucket_bpa"), "ok" if b.get("bucket_bpa") == "on"
                     else "MISMATCH"])
        raised = {f["check"] for f in b.get("findings", [])}
        for field, want in exp.items():
            if field in ("findings", "no_findings"):
                for c in want:
                    present = c in raised
                    good = present if field == "findings" else not present
                    rows.append([suffix, c, "raised" if field == "findings" else "not raised",
                                 "raised" if present else "not raised", "ok" if good else "MISMATCH"])
                continue
            if isinstance(want, dict):
                for k, v in want.items():
                    got = _get(b, f"{field}.{k}")
                    rows.append([suffix, f"{field}.{k}", v, got, "ok" if got == v else "MISMATCH"])
                continue
            if want == "@ai-or-default":
                got = b.get(field)
                rows.append([suffix, field, "ai or default", got, "ok" if got in ("ai", "default") else "MISMATCH"])
                continue
            if isinstance(want, str) and want.startswith("@"):
                want = p + want[1:]
            elif want == "secondary":
                want = secondary
            got = _get(b, field)
            good = str(got or "").startswith(want[:-1]) if isinstance(want, str) and want.endswith("*") \
                else got == want
            rows.append([suffix, field, want, got, "ok" if good else "MISMATCH"])
    return rows, all(r[4] == "ok" for r in rows)


def _empty(s3, name):
    pages = s3.get_paginator("list_object_versions").paginate(Bucket=name)
    for page in pages:
        objs = [{"Key": v["Key"], "VersionId": v["VersionId"]}
                for v in page.get("Versions", []) + page.get("DeleteMarkers", [])]
        if objs:
            errors = s3.delete_objects(Bucket=name, Delete={"Objects": objs, "Quiet": True}).get("Errors")
            if errors:
                raise SystemExit(f"Could not empty {name}: {errors[0].get('Code')} on {errors[0].get('Key')} "
                                 f"({len(errors)} errors)")


def _tagged(session, name, region):
    try:
        tags = session.client("s3", region_name=region).get_bucket_tagging(Bucket=name)["TagSet"]
    except ClientError:
        return False
    return {t["Key"]: t["Value"] for t in tags}.get(TAG_KEY) == TAG_VALUE


def mock_buckets(session, account_id):
    """[(name, region)] of buckets with the mock prefix AND the mock tag. The mock names are also looked up
    directly: ListBuckets can lag a few minutes behind a create."""
    s3, found, kw = session.client("s3"), {}, {"Prefix": prefix(account_id), "MaxBuckets": 1000}
    while True:
        r = s3.list_buckets(**kw)
        found.update({b["Name"]: b.get("BucketRegion") for b in r.get("Buckets", [])})
        if not r.get("ContinuationToken"):
            break
        kw["ContinuationToken"] = r["ContinuationToken"]
    for suffix in ORDER:
        name = prefix(account_id) + suffix
        if name not in found:
            try:
                loc = s3.get_bucket_location(Bucket=name).get("LocationConstraint")
            except ClientError:
                continue          # NoSuchBucket, or not ours
            found[name] = s3c.LEGACY_LOCATION.get(loc, loc)
    return [(n, r or "us-east-1") for n, r in sorted(found.items()) if _tagged(session, n, r or "us-east-1")]


def cleanup(session, account_id, buckets, log=print, sleep=time.sleep):
    """Delete the given mock buckets (from mock_buckets) and the mock replication role. Log targets go last."""
    last = {prefix(account_id) + s for s in ("access-logs", "app-prod-b")}
    for name, region in buckets:      # stop new log deliveries first (late ones still arrive: see the retry)
        session.client("s3", region_name=region).put_bucket_logging(Bucket=name, BucketLoggingStatus={})
    for name, region in sorted(buckets, key=lambda x: x[0] in last):
        s3 = session.client("s3", region_name=region)
        s3.delete_bucket_policy(Bucket=name)           # lifts the delete guard and the hidden policy
        for attempt in range(10):                       # log deliveries can land minutes after logging is off
            _empty(s3, name)
            try:
                s3.delete_bucket(Bucket=name)
                break
            except ClientError as e:
                if _code(e) != "BucketNotEmpty" or attempt == 9:
                    raise
                sleep(min(30, 5 * (attempt + 1)))
        log(f"deleted {name} ({region})")
    iam = session.client("iam")
    try:
        role = iam.get_role(RoleName=ROLE_NAME)["Role"]
    except ClientError as e:
        if _code(e) == "NoSuchEntity":
            return
        raise
    if {t["Key"]: t["Value"] for t in role.get("Tags", [])}.get(TAG_KEY) == TAG_VALUE:
        try:
            iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName="replication")
        except ClientError as e:
            if _code(e) != "NoSuchEntity":
                raise
        iam.delete_role(RoleName=ROLE_NAME)
        log(f"deleted IAM role {ROLE_NAME}")
