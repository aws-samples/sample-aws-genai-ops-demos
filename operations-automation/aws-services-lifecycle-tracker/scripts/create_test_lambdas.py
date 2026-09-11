#!/usr/bin/env python3
"""
Create (or tear down) a fleet of ~50 tiny Lambda functions on deprecated or
soon-to-be-deprecated runtimes, so the Lambda scanner has a realistic volume of
resources to match and the UI can be tested with many resources per runtime.

Design goals
- Free when idle: Lambda bills per invocation; these functions are never
  invoked. Smallest memory (128 MB), 3 s timeout, no triggers, no logs.
- Idempotent: existing functions are left alone; re-running only creates what
  is missing. --teardown deletes the functions and the role the script owns.
- Runtimes that Lambda no longer accepts at create time are reported and
  skipped (the "block function create" date has passed), never fatal.
- Every function is tagged auto-delete=false (protects it from account
  janitors) plus Project/Purpose tags.

Usage
    python scripts/create_test_lambdas.py            # create what is missing
    python scripts/create_test_lambdas.py --status   # show fleet status
    python scripts/create_test_lambdas.py --teardown # delete the whole fleet

Region comes from AWS_REGION / AWS_DEFAULT_REGION / `aws configure get region`.
Requires boto3 and IAM permissions on lambda and iam (role create/delete).
"""
import argparse
import io
import sys
import time
import zipfile
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

PREFIX = "lt-fn-"  # lifecycle-tracker test fleet
ROLE_NAME = "lifecycle-test-lambda-role"
TAGS = {
    "auto-delete": "false",
    "Project": "aws-services-lifecycle-tracker",
    "Purpose": "lifecycle-test-fleet",
}

# runtime -> (count, handler, file name, file body). Creation is blocked by
# Lambda once the runtime's "block function create" date passes; the script
# reports those runtimes instead of failing (dates from the Lambda runtimes
# page, Sept 2026: all below are creatable until 2027-02-01).
PY = ("lambda_function.handler", "lambda_function.py", "def handler(event, context):\n    return 'ok'\n")
NODE = ("index.handler", "index.js", "exports.handler = async () => 'ok';\n")
RUBY = ("lambda_function.handler", "lambda_function.rb", "def handler(event:, context:)\n  'ok'\nend\n")
PROVIDED = ("bootstrap", "bootstrap", "#!/bin/sh\nwhile true; do sleep 1; done\n")
# Lambda does not validate compiled artifacts at create time; a placeholder
# file is enough for a function that is never invoked.
DOTNET = ("Placeholder::Placeholder.Function::Handler", "Placeholder.dll", "placeholder")

FLEET = [
    # deprecated runtimes (6 functions each = 48)
    ("nodejs16.x", 6, NODE),
    ("nodejs18.x", 6, NODE),
    ("nodejs20.x", 6, NODE),
    ("python3.8", 6, PY),
    ("python3.9", 6, PY),
    ("ruby3.2", 6, RUBY),
    ("dotnet6", 6, DOTNET),
    ("provided.al2", 6, PROVIDED),
    # ending within a year (1 each = 2)
    ("python3.10", 1, PY),
    ("dotnet8", 1, DOTNET),
]

TRUST = (
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
    '"Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
)


def fn_name(runtime: str, i: int) -> str:
    return f"{PREFIX}{runtime.replace('.', '-')}-{i:02d}"


def zip_bytes(file_name: str, body: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo(file_name)
        info.external_attr = 0o755 << 16  # executable (needed for bootstrap)
        z.writestr(info, body)
    return buf.getvalue()


def ensure_role(iam) -> str:
    try:
        return iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
    print(f"  creating IAM role {ROLE_NAME}")
    arn = iam.create_role(
        RoleName=ROLE_NAME,
        AssumeRolePolicyDocument=TRUST,
        Description="Execution role for the lifecycle-tracker Lambda test fleet (never invoked)",
        Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()],
    )["Role"]["Arn"]
    time.sleep(10)  # IAM propagation before Lambda can assume the role
    return arn


def fleet_functions(lam):
    names = set()
    for page in lam.get_paginator("list_functions").paginate():
        for f in page["Functions"]:
            if f["FunctionName"].startswith(PREFIX):
                names.add(f["FunctionName"])
    return names


def create(lam, iam, region):
    existing = fleet_functions(lam)
    role_arn = ensure_role(iam)
    created, skipped, rejected = 0, 0, {}
    for runtime, count, (handler, file_name, body) in FLEET:
        code = zip_bytes(file_name, body)
        for i in range(1, count + 1):
            name = fn_name(runtime, i)
            if name in existing:
                skipped += 1
                continue
            if runtime in rejected:
                continue
            try:
                lam.create_function(
                    FunctionName=name,
                    Runtime=runtime,
                    Role=role_arn,
                    Handler=handler,
                    Code={"ZipFile": code},
                    Description="lifecycle-tracker test fleet - never invoked",
                    Timeout=3,
                    MemorySize=128,
                    Tags=TAGS,
                    Architectures=["x86_64"],
                )
                created += 1
                print(f"  created {name}")
            except ClientError as e:
                code_ = e.response["Error"]["Code"]
                msg = e.response["Error"]["Message"]
                if code_ == "InvalidParameterValueException" and ("role" in msg.lower() and "assumed" in msg.lower()):
                    time.sleep(5)  # role not yet propagated: retry once
                    lam.create_function(
                        FunctionName=name, Runtime=runtime, Role=role_arn, Handler=handler,
                        Code={"ZipFile": code}, Timeout=3, MemorySize=128, Tags=TAGS,
                        Description="lifecycle-tracker test fleet - never invoked",
                    )
                    created += 1
                    print(f"  created {name}")
                    continue
                rejected[runtime] = f"{code_}: {msg}"
                print(f"  REJECTED runtime {runtime}: {msg}")
    print(f"\nRegion {region}: created {created}, already present {skipped}")
    if rejected:
        print("Runtimes Lambda no longer accepts at create time:")
        for rt, why in rejected.items():
            print(f"  - {rt}: {why}")


def status(lam, region):
    by_runtime = {}
    for page in lam.get_paginator("list_functions").paginate():
        for f in page["Functions"]:
            if f["FunctionName"].startswith(PREFIX):
                by_runtime.setdefault(f.get("Runtime", "?"), []).append(f["FunctionName"])
    total = sum(len(v) for v in by_runtime.values())
    print(f"Region {region}: {total} fleet functions")
    for rt in sorted(by_runtime):
        print(f"  {rt:14} {len(by_runtime[rt])}")


def teardown(lam, iam, region):
    names = sorted(fleet_functions(lam))
    for name in names:
        lam.delete_function(FunctionName=name)
        print(f"  deleted {name}")
    try:
        iam.delete_role(RoleName=ROLE_NAME)
        print(f"  deleted role {ROLE_NAME}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
    print(f"\nRegion {region}: deleted {len(names)} functions")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true", help="show fleet status and exit")
    ap.add_argument("--teardown", action="store_true", help="delete every function and the role the script owns")
    args = ap.parse_args()

    region = get_region()
    lam = boto3.client("lambda", region_name=region)
    iam = boto3.client("iam", region_name=region)
    if args.status:
        status(lam, region)
    elif args.teardown:
        teardown(lam, iam, region)
    else:
        create(lam, iam, region)
        status(lam, region)


if __name__ == "__main__":
    main()
