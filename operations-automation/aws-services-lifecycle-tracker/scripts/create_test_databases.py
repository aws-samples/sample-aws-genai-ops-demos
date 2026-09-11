#!/usr/bin/env python3
"""
Create (or tear down) a small fleet of RDS-family test databases on older
engine versions, so the account scanners have real resources to match against
the extracted deprecation facts.

Design goals
- Frugal: smallest instance classes; Aurora as Serverless v2 with min 0 ACU
  (scales to zero when idle); 20 GB gp3; single-AZ; no backups where the
  service allows 0; no public access; no Secrets Manager (random password
  that is never stored - nobody connects to these).
- No RDS Extended Support fees: only versions still inside standard support
  are used (the fee is ~$0.10/vCPU-hour once a major version leaves standard
  support). The lifecycle tracker flags them anyway because their end of
  standard support is less than a year away ('end_of_support_date').
- Idempotent: existing resources are left alone; re-running only creates what
  is missing. --teardown deletes everything the script owns (by tag).
- Every resource is tagged auto-delete=false (protects it from account
  janitors) plus Project/Purpose tags.

Usage
    python scripts/create_test_databases.py            # create what is missing
    python scripts/create_test_databases.py --status   # show fleet status
    python scripts/create_test_databases.py --teardown # delete the whole fleet
    python scripts/create_test_databases.py --only lt-mysql-8-4 lt-neptune-1-2

Region comes from AWS_REGION / AWS_DEFAULT_REGION / `aws configure get region`.
Requires boto3 and IAM permissions on rds, docdb, neptune, ec2 (default VPC).
"""
import argparse
import secrets
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

# Shared region helper (repo convention); fall back to boto3's own resolution
# when the script is run outside the repo layout.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from shared.utils import get_region  # type: ignore
except Exception:  # pragma: no cover
    def get_region() -> str:
        return boto3.session.Session().region_name or "us-east-1"

PREFIX = "lt-"  # lifecycle-tracker test fleet
TAGS = [
    {"Key": "auto-delete", "Value": "false"},
    {"Key": "Project", "Value": "aws-services-lifecycle-tracker"},
    {"Key": "Purpose", "Value": "lifecycle-test-fleet"},
]
SUBNET_GROUP = "lifecycle-test-fleet"

# ---------------------------------------------------------------------------
# Fleet definition (engine versions chosen in Sept 2026; see README section)
#   kind: rds-instance | aurora-cluster | docdb-cluster | neptune-cluster
# ---------------------------------------------------------------------------
FLEET = [
    # --- RDS instances (db.t4g.micro is the smallest class) -----------------
    {"id": "lt-mysql-8-4", "kind": "rds-instance", "engine": "mysql", "version": "8.4.5",
     "instance_class": "db.t4g.micro", "note": "standard support ends 2026-10-31"},
    {"id": "lt-mariadb-10-6", "kind": "rds-instance", "engine": "mariadb", "version": "10.6.22",
     "instance_class": "db.t4g.micro", "note": "standard support ends 2026-11-30"},
    {"id": "lt-mariadb-11-4", "kind": "rds-instance", "engine": "mariadb", "version": "11.4.7",
     "instance_class": "db.t4g.micro", "note": "standard support ends 2026-10-31"},
    {"id": "lt-postgres-14", "kind": "rds-instance", "engine": "postgres", "version": "14.18",
     "instance_class": "db.t4g.micro", "note": "standard support ends 2026-10-31"},
    {"id": "lt-postgres-18", "kind": "rds-instance", "engine": "postgres", "version": "18.6",
     "instance_class": "db.t4g.micro", "note": "control: supported until 2027-09-30"},
    {"id": "lt-sqlserver-2017", "kind": "rds-instance", "engine": "sqlserver-ex", "version": "14.00.3540.1.v1",
     "instance_class": "db.t3.small", "license": "license-included",
     "note": "SQL Server 2017 Express (smallest class for Express is db.t3.small)"},
    # --- Aurora Serverless v2, min 0 ACU (scale-to-zero needs MySQL 3.08+ / PG 13.15+, 14.12+, 15.7+, 16.3+)
    {"id": "lt-aurora-mysql-3-08", "kind": "aurora-cluster", "engine": "aurora-mysql",
     "version": "8.0.mysql_aurora.3.08.2", "note": "minor 3.08 standard support ended 2026-08-31 (no fee: major 3 is supported until 2028)"},
    {"id": "lt-aurora-mysql-3-10", "kind": "aurora-cluster", "engine": "aurora-mysql",
     "version": "8.0.mysql_aurora.3.10.5", "note": "control: LTS, supported until 2028-04-30"},
    {"id": "lt-aurora-postgres-14", "kind": "aurora-cluster", "engine": "aurora-postgresql",
     "version": "14.17", "note": "standard support ends 2026-11-30"},
    {"id": "lt-aurora-postgres-15", "kind": "aurora-cluster", "engine": "aurora-postgresql",
     "version": "15.10", "note": "control: LTS, supported until 2028-02-29"},
    # --- DocumentDB (db.t3.medium is the smallest class). 3.6 is in Extended Support (fees) -> use 4.0
    {"id": "lt-docdb-4-0", "kind": "docdb-cluster", "engine": "docdb", "version": "4.0.0",
     "instance_class": "db.t3.medium", "note": "supported (no fee-free deprecated DocumentDB version exists)"},
    # --- Neptune (db.t4g.medium is the smallest class)
    {"id": "lt-neptune-1-2", "kind": "neptune-cluster", "engine": "neptune", "version": "1.2.1.2",
     "instance_class": "db.t4g.medium", "note": "end of life 2026-12-04"},
]


def _password() -> str:
    # Letters/digits only: RDS forbids '/', '@', '"' and spaces
    return "Lt" + secrets.token_hex(14) + "9"


def _default_vpc_subnets(ec2) -> list:
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        sys.exit("No default VPC in this region - create one (aws ec2 create-default-vpc) or adapt the script.")
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpcs[0]["VpcId"]]}])["Subnets"]
    return [s["SubnetId"] for s in subnets]


def _ensure_subnet_group(client, subnet_ids: list, label: str) -> None:
    try:
        client.describe_db_subnet_groups(DBSubnetGroupName=SUBNET_GROUP)
        return
    except ClientError as e:
        if e.response["Error"]["Code"] != "DBSubnetGroupNotFoundFault":
            raise
    client.create_db_subnet_group(
        DBSubnetGroupName=SUBNET_GROUP,
        DBSubnetGroupDescription="Lifecycle tracker test fleet (default VPC)",
        SubnetIds=subnet_ids,
        Tags=TAGS,
    )
    print(f"  created {label} subnet group {SUBNET_GROUP}")


def _instance_exists(client, identifier: str) -> dict | None:
    try:
        return client.describe_db_instances(DBInstanceIdentifier=identifier)["DBInstances"][0]
    except ClientError as e:
        if e.response["Error"]["Code"] == "DBInstanceNotFound":
            return None
        raise


def _cluster_exists(client, identifier: str) -> dict | None:
    try:
        return client.describe_db_clusters(DBClusterIdentifier=identifier)["DBClusters"][0]
    except ClientError as e:
        if e.response["Error"]["Code"] == "DBClusterNotFoundFault":
            return None
        raise


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def create_rds_instance(rds, spec: dict) -> None:
    kwargs = dict(
        DBInstanceIdentifier=spec["id"],
        Engine=spec["engine"],
        EngineVersion=spec["version"],
        DBInstanceClass=spec["instance_class"],
        AllocatedStorage=20,
        StorageType="gp3",
        MasterUsername="ltadmin",
        MasterUserPassword=_password(),
        DBSubnetGroupName=SUBNET_GROUP,
        PubliclyAccessible=False,
        MultiAZ=False,
        BackupRetentionPeriod=0,
        AutoMinorVersionUpgrade=False,   # keep the exact version we are testing
        DeletionProtection=False,
        CopyTagsToSnapshot=True,
        Tags=TAGS,
    )
    if spec.get("license"):
        kwargs["LicenseModel"] = spec["license"]
    rds.create_db_instance(**kwargs)


def create_aurora_cluster(rds, spec: dict) -> None:
    rds.create_db_cluster(
        DBClusterIdentifier=spec["id"],
        Engine=spec["engine"],
        EngineVersion=spec["version"],
        MasterUsername="ltadmin",
        MasterUserPassword=_password(),
        DBSubnetGroupName=SUBNET_GROUP,
        BackupRetentionPeriod=1,          # Aurora minimum
        DeletionProtection=False,
        StorageEncrypted=True,
        ServerlessV2ScalingConfiguration={
            "MinCapacity": 0,             # scale to zero when idle
            "MaxCapacity": 1,
            "SecondsUntilAutoPause": 300,
        },
        Tags=TAGS,
    )
    rds.create_db_instance(
        DBInstanceIdentifier=f"{spec['id']}-1",
        DBClusterIdentifier=spec["id"],
        Engine=spec["engine"],
        DBInstanceClass="db.serverless",
        PubliclyAccessible=False,
        AutoMinorVersionUpgrade=False,
        Tags=TAGS,
    )


def create_docdb_cluster(docdb, spec: dict) -> None:
    docdb.create_db_cluster(
        DBClusterIdentifier=spec["id"],
        Engine="docdb",
        EngineVersion=spec["version"],
        MasterUsername="ltadmin",
        MasterUserPassword=_password(),
        DBSubnetGroupName=SUBNET_GROUP,
        BackupRetentionPeriod=1,
        DeletionProtection=False,
        StorageEncrypted=True,
        Tags=TAGS,
    )
    docdb.create_db_instance(
        DBInstanceIdentifier=f"{spec['id']}-1",
        DBClusterIdentifier=spec["id"],
        Engine="docdb",
        DBInstanceClass=spec["instance_class"],
        AutoMinorVersionUpgrade=False,
        Tags=TAGS,
    )


def create_neptune_cluster(neptune, spec: dict) -> None:
    neptune.create_db_cluster(
        DBClusterIdentifier=spec["id"],
        Engine="neptune",
        EngineVersion=spec["version"],
        DBSubnetGroupName=SUBNET_GROUP,
        BackupRetentionPeriod=1,
        DeletionProtection=False,
        StorageEncrypted=True,
        Tags=TAGS,
    )
    neptune.create_db_instance(
        DBInstanceIdentifier=f"{spec['id']}-1",
        DBClusterIdentifier=spec["id"],
        Engine="neptune",
        DBInstanceClass=spec["instance_class"],
        AutoMinorVersionUpgrade=False,
        Tags=TAGS,
    )


def create_fleet(region: str, only: list) -> None:
    ec2 = boto3.client("ec2", region_name=region)
    rds = boto3.client("rds", region_name=region)
    docdb = boto3.client("docdb", region_name=region)
    neptune = boto3.client("neptune", region_name=region)

    subnet_ids = _default_vpc_subnets(ec2)
    _ensure_subnet_group(rds, subnet_ids, "RDS")
    _ensure_subnet_group(docdb, subnet_ids, "DocumentDB")
    _ensure_subnet_group(neptune, subnet_ids, "Neptune")

    for spec in FLEET:
        if only and spec["id"] not in only:
            continue
        kind = spec["kind"]
        client = {"rds-instance": rds, "aurora-cluster": rds, "docdb-cluster": docdb, "neptune-cluster": neptune}[kind]
        existing = _instance_exists(client, spec["id"]) if kind == "rds-instance" else _cluster_exists(client, spec["id"])
        if existing:
            status = existing.get("DBInstanceStatus") or existing.get("Status")
            print(f"  = {spec['id']:<24} exists ({status}) - {spec['engine']} {spec['version']}")
            continue
        try:
            {"rds-instance": create_rds_instance, "aurora-cluster": create_aurora_cluster,
             "docdb-cluster": create_docdb_cluster, "neptune-cluster": create_neptune_cluster}[kind](client, spec)
            print(f"  + {spec['id']:<24} creating  {spec['engine']} {spec['version']}  ({spec['note']})")
        except ClientError as e:
            print(f"  ! {spec['id']:<24} FAILED: {e.response['Error']['Code']}: {e.response['Error']['Message']}")
        time.sleep(1)  # be gentle with the API


# ---------------------------------------------------------------------------
# Status / teardown
# ---------------------------------------------------------------------------

def fleet_status(region: str) -> None:
    rds = boto3.client("rds", region_name=region)
    docdb = boto3.client("docdb", region_name=region)
    neptune = boto3.client("neptune", region_name=region)
    print(f"{'identifier':<26}{'kind':<10}{'engine':<19}{'version':<28}{'class':<15}status")
    for spec in FLEET:
        client = {"rds-instance": rds, "aurora-cluster": rds, "docdb-cluster": docdb, "neptune-cluster": neptune}[spec["kind"]]
        if spec["kind"] == "rds-instance":
            r = _instance_exists(client, spec["id"])
            status, version, cls = (r["DBInstanceStatus"], r["EngineVersion"], r["DBInstanceClass"]) if r else ("-", "", "")
        else:
            r = _cluster_exists(client, spec["id"])
            status, version = (r["Status"], r["EngineVersion"]) if r else ("-", "")
            cls = "db.serverless" if spec["kind"] == "aurora-cluster" else spec.get("instance_class", "")
            inst = _instance_exists(client, f"{spec['id']}-1")
            if inst:
                status += f" / instance {inst['DBInstanceStatus']}"
        print(f"{spec['id']:<26}{spec['kind'].split('-')[0]:<10}{spec['engine']:<19}{version:<28}{cls:<15}{status}")


def teardown_fleet(region: str, only: list) -> None:
    rds = boto3.client("rds", region_name=region)
    docdb = boto3.client("docdb", region_name=region)
    neptune = boto3.client("neptune", region_name=region)
    for spec in FLEET:
        if only and spec["id"] not in only:
            continue
        client = {"rds-instance": rds, "aurora-cluster": rds, "docdb-cluster": docdb, "neptune-cluster": neptune}[spec["kind"]]
        try:
            if spec["kind"] == "rds-instance":
                if _instance_exists(client, spec["id"]):
                    client.delete_db_instance(DBInstanceIdentifier=spec["id"], SkipFinalSnapshot=True,
                                              DeleteAutomatedBackups=True)
                    print(f"  - {spec['id']} deleting")
            else:
                if _instance_exists(client, f"{spec['id']}-1"):
                    client.delete_db_instance(DBInstanceIdentifier=f"{spec['id']}-1")
                if _cluster_exists(client, spec["id"]):
                    client.delete_db_cluster(DBClusterIdentifier=spec["id"], SkipFinalSnapshot=True)
                    print(f"  - {spec['id']} deleting (cluster + instance)")
        except ClientError as e:
            print(f"  ! {spec['id']} {e.response['Error']['Code']}: {e.response['Error']['Message']}")
    print("Subnet groups are left in place (free); delete with: aws rds|docdb|neptune delete-db-subnet-group "
          f"--db-subnet-group-name {SUBNET_GROUP}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--teardown", action="store_true", help="delete the fleet (no final snapshots)")
    parser.add_argument("--status", action="store_true", help="show the fleet status and exit")
    parser.add_argument("--only", nargs="*", default=[], help="restrict to these identifiers")
    parser.add_argument("--region", default=None, help="override the region")
    args = parser.parse_args()

    region = args.region or get_region()
    print(f"Region: {region}   fleet size: {len(FLEET)}   tags: {', '.join(t['Key'] + '=' + t['Value'] for t in TAGS)}\n")
    if args.status:
        fleet_status(region)
    elif args.teardown:
        teardown_fleet(region, args.only)
    else:
        create_fleet(region, args.only)
        print("\nCreation takes 5-15 minutes per resource. Check with: python scripts/create_test_databases.py --status")
    return 0


if __name__ == "__main__":
    sys.exit(main())
