"""Mock estate: seed into an in-memory S3, read it back with the real collectors, derive the inventory, and check
that every expectation holds. Also: production guard, cleanup scope, mismatch detection."""
import json

import pytest
from botocore.exceptions import ClientError

from s3dp import cli, seed
from s3dp.collect import s3 as s3c
from s3dp.derive import inventory

ACCOUNT = "111122223333"


def _err(code):
    return ClientError({"Error": {"Code": code}}, "op")


class World:
    def __init__(self):
        self.buckets, self.roles = {}, {}


class FakeS3:
    def __init__(self, world, region):
        self.w, self.region = world, region

    def _b(self, name):
        if name not in self.w.buckets:
            raise _err("NoSuchBucket")
        return self.w.buckets[name]

    # writes
    def create_bucket(self, Bucket, CreateBucketConfiguration=None, ObjectLockEnabledForBucket=False):
        region = (CreateBucketConfiguration or {}).get("LocationConstraint", "us-east-1")
        assert region == self.region, "bucket created with a client of another region"
        if Bucket in self.w.buckets:
            raise _err("BucketAlreadyOwnedByYou")
        self.w.buckets[Bucket] = {"region": region, "versioning": "Enabled" if ObjectLockEnabledForBucket else None,
                                  "lock": ObjectLockEnabledForBucket, "objects": [], "ownership": "BucketOwnerEnforced"}

    def get_waiter(self, name):
        assert name == "bucket_exists"
        outer = self

        class W:
            def wait(self, Bucket):
                outer._b(Bucket)
        return W()

    def put_public_access_block(self, Bucket, PublicAccessBlockConfiguration):
        self._b(Bucket)["pab"] = PublicAccessBlockConfiguration

    def put_bucket_tagging(self, Bucket, Tagging):
        self._b(Bucket)["tags"] = Tagging["TagSet"]

    def put_bucket_ownership_controls(self, Bucket, OwnershipControls):
        self._b(Bucket)["ownership"] = OwnershipControls["Rules"][0]["ObjectOwnership"]

    def put_bucket_versioning(self, Bucket, VersioningConfiguration):
        self._b(Bucket)["versioning"] = VersioningConfiguration["Status"]

    def put_bucket_lifecycle_configuration(self, Bucket, LifecycleConfiguration):
        self._b(Bucket)["lifecycle"] = LifecycleConfiguration["Rules"]

    def put_bucket_policy(self, Bucket, Policy):
        doc = json.loads(Policy)
        assert all(s["Effect"] == "Deny" or s["Principal"] != "*" for s in doc["Statement"]), "public grant"
        self._b(Bucket)["policy"] = Policy

    def delete_bucket_policy(self, Bucket):
        self._b(Bucket).pop("policy", None)

    def put_bucket_logging(self, Bucket, BucketLoggingStatus):
        if not BucketLoggingStatus:
            self._b(Bucket).pop("logging", None)
            return
        assert BucketLoggingStatus["LoggingEnabled"]["TargetBucket"] in self.w.buckets, "log target created first"
        self._b(Bucket)["logging"] = BucketLoggingStatus["LoggingEnabled"]

    def put_bucket_replication(self, Bucket, ReplicationConfiguration):
        b = self._b(Bucket)
        assert b["versioning"] == "Enabled"
        dest = ReplicationConfiguration["Rules"][0]["Destination"]["Bucket"].split(":::")[1]
        assert self.w.buckets[dest]["versioning"] == "Enabled", "destination versioned before replication"
        b["replication"] = ReplicationConfiguration

    def put_object(self, Bucket, Key, Body, ChecksumAlgorithm=None):
        assert ChecksumAlgorithm or not self._b(Bucket)["lock"]
        b = self._b(Bucket)
        b["objects"].append({"Key": Key, "VersionId": str(len(b["objects"]))})

    def list_buckets(self, MaxBuckets=None, Prefix="", ContinuationToken=None):
        return {"Buckets": [{"Name": n, "BucketRegion": b["region"]} for n, b in self.w.buckets.items()
                            if n.startswith(Prefix)]}

    def get_bucket_location(self, Bucket):
        r = self._b(Bucket)["region"]
        return {"LocationConstraint": None if r == "us-east-1" else r}

    def get_paginator(self, name):
        assert name == "list_object_versions"
        outer = self

        class P:
            def paginate(self, Bucket):
                return [{"Versions": list(outer._b(Bucket)["objects"])}]
        return P()

    def delete_objects(self, Bucket, Delete):
        gone = {(o["Key"], o["VersionId"]) for o in Delete["Objects"]}
        b = self._b(Bucket)
        b["objects"] = [o for o in b["objects"] if (o["Key"], o["VersionId"]) not in gone]
        return {}

    def delete_bucket(self, Bucket):
        b = self._b(Bucket)
        if b["objects"]:
            raise _err("BucketNotEmpty")
        if "policy" in b and "DeleteBucket" in b["policy"]:
            raise _err("AccessDenied")
        del self.w.buckets[Bucket]

    # reads used by collect.s3.collect_bucket
    def get_bucket_versioning(self, Bucket):
        v = self._b(Bucket)["versioning"]
        return {"Status": v} if v else {}

    def get_object_lock_configuration(self, Bucket):
        if not self._b(Bucket)["lock"]:
            raise _err("ObjectLockConfigurationNotFoundError")
        return {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}

    def get_bucket_replication(self, Bucket):
        if "replication" not in self._b(Bucket):
            raise _err("ReplicationConfigurationNotFoundError")
        return {"ReplicationConfiguration": self._b(Bucket)["replication"]}

    def get_bucket_encryption(self, Bucket):
        return {"ServerSideEncryptionConfiguration": {"Rules": [
            {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}}

    def get_bucket_lifecycle_configuration(self, Bucket):
        if "lifecycle" not in self._b(Bucket):
            raise _err("NoSuchLifecycleConfiguration")
        return {"Rules": self._b(Bucket)["lifecycle"]}

    def get_public_access_block(self, Bucket):
        if "pab" not in self._b(Bucket):
            raise _err("NoSuchPublicAccessBlockConfiguration")
        return {"PublicAccessBlockConfiguration": self._b(Bucket)["pab"]}

    def get_bucket_policy(self, Bucket):
        b = self._b(Bucket)
        if "policy" not in b:
            raise _err("NoSuchBucketPolicy")
        if any(s["Effect"] == "Deny" and s["Action"] == "s3:GetBucketPolicy"
               for s in json.loads(b["policy"])["Statement"]):
            raise _err("AccessDenied")
        return {"Policy": b["policy"]}

    def get_bucket_policy_status(self, Bucket):
        if "policy" not in self._b(Bucket):
            raise _err("NoSuchBucketPolicy")
        return {"PolicyStatus": {"IsPublic": False}}

    def get_bucket_ownership_controls(self, Bucket):
        return {"OwnershipControls": {"Rules": [{"ObjectOwnership": self._b(Bucket)["ownership"]}]}}

    def get_bucket_tagging(self, Bucket):
        if not self._b(Bucket).get("tags"):
            raise _err("NoSuchTagSet")
        return {"TagSet": self._b(Bucket)["tags"]}

    def get_bucket_logging(self, Bucket):
        le = self._b(Bucket).get("logging")
        return {"LoggingEnabled": le} if le else {}

    def get_bucket_notification_configuration(self, Bucket):
        return {}


class FakeIAM:
    def __init__(self, world, alias=None):
        self.w, self.alias = world, alias

    def list_account_aliases(self):
        return {"AccountAliases": [self.alias] if self.alias else []}

    def create_role(self, RoleName, AssumeRolePolicyDocument, Description, Tags):
        if RoleName in self.w.roles:
            raise _err("EntityAlreadyExists")
        self.w.roles[RoleName] = {"Arn": f"arn:aws:iam::{ACCOUNT}:role/{RoleName}", "Tags": Tags, "policies": {}}
        return {"Role": self.w.roles[RoleName]}

    def get_role(self, RoleName):
        if RoleName not in self.w.roles:
            raise _err("NoSuchEntity")
        return {"Role": self.w.roles[RoleName]}

    def put_role_policy(self, RoleName, PolicyName, PolicyDocument):
        self.w.roles[RoleName]["policies"][PolicyName] = PolicyDocument

    def delete_role_policy(self, RoleName, PolicyName):
        if PolicyName not in self.w.roles[RoleName]["policies"]:
            raise _err("NoSuchEntity")
        del self.w.roles[RoleName]["policies"][PolicyName]

    def delete_role(self, RoleName):
        assert not self.w.roles[RoleName]["policies"]
        del self.w.roles[RoleName]


class Session:
    def __init__(self, world, region="eu-west-1", alias=None):
        self.w, self.region_name, self.alias = world, region, alias

    def get_partition_for_region(self, region):
        return "aws-cn" if region.startswith("cn-") else "aws"

    def client(self, svc, region_name=None, **_):
        if svc == "iam":
            return FakeIAM(self.w, self.alias)
        assert svc == "s3"
        return FakeS3(self.w, region_name or self.region_name)


def _raw(world):
    """The parts of collect_account() that the inventory needs, read with the real bucket collector."""
    regions = sorted({b["region"] for b in world.buckets.values()})
    empty_backup = {"protected_s3": {}, "plans_total": 0, "plans_with_copy_actions": 0, "vaults": [],
                    "s3_opt_in": False, "restore_testing_plans": 0, "frameworks": 0, "errors": {}}
    return {
        "scan_date": "2026-11-26T10:00:00Z", "hub_account": ACCOUNT,
        "accounts": {ACCOUNT: {"name": "mock-lab", "account_bpa": "unknown", "role_names": [], "stack_names": [],
                               "config": {"recording": None, "s3_rules": None}, "security_hub": None,
                               "outcome": "scanned"}},
        "regions": {ACCOUNT: {r: {"outcome": "scanned", "backup": dict(empty_backup)} for r in regions}},
        "buckets": [{"name": n, "account": ACCOUNT, "region": b["region"],
                     "s3": s3c.collect_bucket(FakeS3(world, b["region"]), n), "metrics": None}
                    for n, b in world.buckets.items()],
        "organization": None,
        "run": {"scope": "1 account (mock-lab)", "collected": "2026-11-26", "regions_scanned": regions,
                "regions_skipped": "none", "unavailable": [], "api_errors": 0,
                "price_region": "US East (N. Virginia) list prices"},
    }


def _seeded(region="eu-west-1", secondary="us-east-1"):
    w = World()
    seed.seed(Session(w, region), ACCOUNT, region, secondary, log=lambda *_: None, sleep=lambda *_: None)
    return w


def test_scan_of_the_seeded_estate_matches_every_expectation():
    w = _seeded()
    assert len(w.buckets) == len(seed.SPEC)
    assert all(b["pab"] == seed.PAB for b in w.buckets.values())
    rows, ok = seed.check(inventory.build(_raw(w)))
    assert [r for r in rows if r[4] != "ok"] == []
    assert ok and len(rows) > 60


def test_seed_is_idempotent():
    w = _seeded()
    seed.seed(Session(w), ACCOUNT, "eu-west-1", "us-east-1", log=lambda *_: None, sleep=lambda *_: None)
    assert seed.check(inventory.build(_raw(w)))[1]


def test_us_east_1_primary_creates_without_location_constraint():
    w = _seeded("us-east-1", seed.default_secondary("us-east-1"))
    assert {b["region"] for b in w.buckets.values()} == {"us-east-1", "us-west-2"}
    assert seed.check(inventory.build(_raw(w)))[1]


def test_check_reports_a_mismatch_and_missing_buckets():
    w = _seeded()
    w.buckets[seed.prefix(ACCOUNT) + "orders-prod"]["versioning"] = "Enabled"
    del w.buckets[seed.prefix(ACCOUNT) + "shared-data"]
    rows, ok = seed.check(inventory.build(_raw(w)))
    assert not ok
    bad = {(r[0], r[1], r[4]) for r in rows if r[4] != "ok"}
    assert ("orders-prod", "versioning", "MISMATCH") in bad
    assert ("orders-prod", "S3-BKP-01", "MISMATCH") in bad
    assert ("shared-data", "bucket", "MISSING") in bad


def test_check_without_mock_buckets():
    rows, ok = seed.check({"buckets": []})
    assert not ok and rows[0][4] == "MISSING"


def test_plan_needs_two_regions():
    with pytest.raises(ValueError):
        seed.plan(ACCOUNT, "eu-west-1", "eu-west-1")
    assert all(len(n) <= 63 for n, _, _ in seed.plan(ACCOUNT, "ap-southeast-2", "ap-northeast-1"))


@pytest.mark.parametrize("alias", ["acme-prod", "payments-production", "corp-prd"])
def test_production_alias_is_refused(alias):
    with pytest.raises(SystemExit, match="sandbox"):
        seed.check_account(alias)


@pytest.mark.parametrize("alias", [None, "acme-sandbox", "acme-preprod", "mock-lab"])
def test_non_production_alias_is_accepted(alias):
    seed.check_account(alias)


def test_cleanup_deletes_only_tagged_mock_buckets_and_role():
    w = _seeded()
    s = Session(w)
    other = FakeS3(w, "eu-west-1")
    other.create_bucket(Bucket=seed.prefix(ACCOUNT) + "not-ours",
                        CreateBucketConfiguration={"LocationConstraint": "eu-west-1"})
    other.create_bucket(Bucket="unrelated-tagged", CreateBucketConfiguration={"LocationConstraint": "eu-west-1"})
    other.put_bucket_tagging(Bucket="unrelated-tagged", Tagging={"TagSet": [{"Key": seed.TAG_KEY,
                                                                              "Value": seed.TAG_VALUE}]})
    found = seed.mock_buckets(s, ACCOUNT)
    assert len(found) == len(seed.SPEC)
    seed.cleanup(s, ACCOUNT, found, log=lambda *_: None, sleep=lambda *_: None)
    assert set(w.buckets) == {seed.prefix(ACCOUNT) + "not-ours", "unrelated-tagged"}
    assert w.roles == {}


def test_untagged_role_is_never_reused_or_deleted():
    w = World()
    w.roles[seed.ROLE_NAME] = {"Arn": "arn", "Tags": [], "policies": {}}
    with pytest.raises(SystemExit, match="without"):
        seed.seed(Session(w), ACCOUNT, "eu-west-1", "us-east-1", log=lambda *_: None, sleep=lambda *_: None)
    seed.cleanup(Session(w), ACCOUNT, [], log=lambda *_: None)
    assert seed.ROLE_NAME in w.roles


def test_seed_check_command(tmp_path, capsys):
    w = _seeded()
    (tmp_path / "inventory.json").write_text(json.dumps(inventory.build(_raw(w)), default=str))
    cli.main(["seed-check", "--out", str(tmp_path)])
    assert "checks match the mock estate" in capsys.readouterr().out
    w.buckets[seed.prefix(ACCOUNT) + "ledger-prod"].pop("policy")
    (tmp_path / "inventory.json").write_text(json.dumps(inventory.build(_raw(w)), default=str))
    with pytest.raises(SystemExit):
        cli.main(["seed-check", "--out", str(tmp_path)])
    assert "MISMATCH" in capsys.readouterr().out


def test_cleanup_finds_buckets_missing_from_a_lagging_list(monkeypatch):
    w = _seeded()
    s = Session(w)
    real = FakeS3.list_buckets
    monkeypatch.setattr(FakeS3, "list_buckets", lambda self, **kw: {"Buckets": real(self, **kw)["Buckets"][:3]})
    assert len(seed.mock_buckets(s, ACCOUNT)) == len(seed.SPEC)


def test_cleanup_turns_logging_off_and_surfaces_delete_errors(monkeypatch):
    w = _seeded()
    s = Session(w)
    found = seed.mock_buckets(s, ACCOUNT)
    monkeypatch.setattr(FakeS3, "delete_objects", lambda self, Bucket, Delete: {"Errors": [
        {"Key": "mock/object-0.txt", "Code": "AccessDenied"}]})
    with pytest.raises(SystemExit, match="AccessDenied"):
        seed.cleanup(s, ACCOUNT, found, log=lambda *_: None, sleep=lambda *_: None)
    assert not any("logging" in b for b in w.buckets.values())


def test_role_cleanup_tolerates_a_missing_inline_policy():
    w = _seeded()
    w.roles[seed.ROLE_NAME]["policies"].clear()
    seed.cleanup(Session(w), ACCOUNT, seed.mock_buckets(Session(w), ACCOUNT), log=lambda *_: None,
                 sleep=lambda *_: None)
    assert w.roles == {}


def test_other_partitions_are_refused():
    with pytest.raises(SystemExit, match="commercial"):
        seed.check_partition(Session(World()), "cn-north-1")
    seed.check_partition(Session(World()), "eu-west-1")


def test_mock_tag_is_not_sent_to_the_model():
    sent = {x["name"]: x for x in inventory.undecided(_raw(_seeded()))}
    assert sent[seed.prefix(ACCOUNT) + "shared-data"]["tags"] == {"owner": "data-platform", "application": "customer-analytics"}
