#!/usr/bin/env python3
"""Give the test fleet business-like tags so the Tag filter has something to show (#164).

The test databases (create_test_databases.py) and Lambda functions
(create_test_lambdas.py) carry technical tags only. This script adds an
ownership story on top, deterministic from the resource name:

  BU=LOB1 / Team=payments   MySQL, MariaDB, odd-numbered functions
  BU=LOB2 / Team=data       PostgreSQL, Aurora PostgreSQL, even-numbered functions
  BU=LOB3 / Team=platform   SQL Server, DocumentDB, Neptune, Aurora MySQL
  (no BU)                   every 6th function: the "not tagged" bucket

Usage:
  python tag_test_fleet.py            # tag
  python tag_test_fleet.py --remove   # remove BU/Team from the fleet again

Region comes from AWS_REGION / AWS_DEFAULT_REGION / `aws configure get region`.
Requires tag:GetResources and tag:TagResources / tag:UntagResources.
"""
import argparse
import re
import sys
from pathlib import Path

import boto3

try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from shared.utils import get_region  # type: ignore
except Exception:  # pragma: no cover
    def get_region() -> str:
        return boto3.session.Session().region_name or "us-east-1"

FLEET_TAG = {"Key": "Purpose", "Values": ["lifecycle-test-fleet"]}
OWNERS = {
    "LOB1": {"BU": "LOB1", "Team": "payments"},
    "LOB2": {"BU": "LOB2", "Team": "data"},
    "LOB3": {"BU": "LOB3", "Team": "platform"},
}


def owner_for(arn: str):
    """Business unit of a fleet resource, from its name; None = leave untagged."""
    name = arn.rsplit(":", 1)[-1].split("/")[-1]
    m = re.search(r"-(\d{2})$", name)
    if m:  # Lambda functions lt-fn-<runtime>-NN
        n = int(m.group(1))
        if n % 6 == 0:
            return None
        return OWNERS["LOB1"] if n % 2 else OWNERS["LOB2"]
    if any(s in name for s in ("mysql", "mariadb")) and "aurora" not in name:
        return OWNERS["LOB1"]
    if "postgres" in name:
        return OWNERS["LOB2"]
    return OWNERS["LOB3"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--remove", action="store_true", help="remove the BU and Team tags instead")
    args = parser.parse_args()
    region = get_region()
    tagging = boto3.client("resourcegroupstaggingapi", region_name=region)

    arns = []
    for page in tagging.get_paginator("get_resources").paginate(TagFilters=[FLEET_TAG], ResourcesPerPage=100):
        arns.extend(m["ResourceARN"] for m in page.get("ResourceTagMappingList", []))
    # Aurora/DocDB/Neptune clusters and their instances are both tagged: keep both
    print(f"{len(arns)} fleet resources in {region}")

    if args.remove:
        for i in range(0, len(arns), 20):
            tagging.untag_resources(ResourceARNList=arns[i:i + 20], TagKeys=["BU", "Team"])
        print("BU and Team removed")
        return 0

    groups = {}
    skipped = []
    for arn in arns:
        owner = owner_for(arn)
        if owner is None:
            skipped.append(arn)
            continue
        groups.setdefault(owner["BU"], []).append(arn)
    for bu, group in sorted(groups.items()):
        for i in range(0, len(group), 20):
            r = tagging.tag_resources(ResourceARNList=group[i:i + 20], Tags=OWNERS[bu])
            for arn, err in (r.get("FailedResourcesMap") or {}).items():
                print(f"  failed {arn}: {err.get('ErrorMessage')}")
        print(f"  {bu} / {OWNERS[bu]['Team']}: {len(group)} resources")
    print(f"  not tagged on purpose: {len(skipped)}")
    print("Run a Refresh (scan) so the tracker picks the tags up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
