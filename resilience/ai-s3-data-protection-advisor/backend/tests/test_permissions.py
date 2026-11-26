"""SCAN_ACTIONS covers every AWS call the backend makes, stays read-only, and matches the design's list."""
import re
from pathlib import Path

from s3dp.permissions import BEDROCK_ACTIONS, SCAN_ACTIONS, SEED_ACTIONS

SRC = Path(__file__).resolve().parents[1] / "s3dp"
DESIGN = Path(__file__).resolve().parents[2] / "docs" / "design.md"
# boto3 method or paginator name -> IAM actions it needs (IAM action names differ from some API names).
NEEDS = {
    "get_caller_identity": ["sts:GetCallerIdentity"],
    "list_buckets": ["s3:ListAllMyBuckets"],
    "get_bucket_location": ["s3:GetBucketLocation"],
    "get_bucket_versioning": ["s3:GetBucketVersioning"],
    "get_object_lock_configuration": ["s3:GetBucketObjectLockConfiguration"],
    "get_bucket_replication": ["s3:GetReplicationConfiguration"],
    "get_bucket_encryption": ["s3:GetEncryptionConfiguration"],
    "get_bucket_lifecycle_configuration": ["s3:GetLifecycleConfiguration"],
    # S3 GetPublicAccessBlock (bucket) and S3 Control GetPublicAccessBlock (account) share the method name
    "get_public_access_block": ["s3:GetBucketPublicAccessBlock", "s3:GetAccountPublicAccessBlock"],
    "get_bucket_policy": ["s3:GetBucketPolicy"],
    "get_bucket_policy_status": ["s3:GetBucketPolicyStatus"],
    "get_bucket_ownership_controls": ["s3:GetBucketOwnershipControls"],
    "get_bucket_tagging": ["s3:GetBucketTagging"],
    "get_bucket_logging": ["s3:GetBucketLogging"],
    "get_bucket_notification_configuration": ["s3:GetBucketNotification"],
    "list_account_aliases": ["iam:ListAccountAliases"],
    "list_roles": ["iam:ListRoles"],
    "get_cost_and_usage": ["ce:GetCostAndUsage"],
    "list_protected_resources": ["backup:ListProtectedResources"],
    "list_backup_plans": ["backup:ListBackupPlans"],
    "get_backup_plan": ["backup:GetBackupPlan"],
    "list_backup_selections": ["backup:ListBackupSelections"],
    "get_backup_selection": ["backup:GetBackupSelection"],
    "list_backup_vaults": ["backup:ListBackupVaults"],
    "describe_region_settings": ["backup:DescribeRegionSettings"],
    "list_restore_testing_plans": ["backup:ListRestoreTestingPlans"],
    "list_frameworks": ["backup:ListFrameworks"],
    "list_stacks": ["cloudformation:ListStacks"],
    "describe_configuration_recorders": ["config:DescribeConfigurationRecorders"],
    "describe_configuration_recorder_status": ["config:DescribeConfigurationRecorderStatus"],
    "describe_config_rules": ["config:DescribeConfigRules"],
    "get_enabled_standards": ["securityhub:GetEnabledStandards"],
    "list_metrics": ["cloudwatch:ListMetrics"],
    "get_metric_data": ["cloudwatch:GetMetricData"],
    "converse": ["bedrock:InvokeModel"],
}
NOT_AWS = {"get_paginator", "get_partition_for_region"}


SERVICE = SRC / "service"
SEED = SRC / "seed.py"      # sandbox-only writer, never part of the scan: see SEED_ACTIONS


def _calls():
    # service/ runs under the pipeline and API roles, whose grants are written in CDK, not the scan role
    text = "\n".join(p.read_text() for p in SRC.rglob("*.py") if p != SEED and SERVICE not in p.parents)
    names = set(re.findall(r"\.((?:get|list|describe)_[a-z_]+|converse)\(", text))
    names |= set(re.findall(r"get_paginator\(\"([a-z_]+)\"\)", text))
    names |= set(re.findall(r"_paged\(\s*bk,\s*\"([a-z_]+)\"", text))
    return names - NOT_AWS


def test_every_call_is_mapped_and_allowed():
    calls = _calls()
    assert calls - set(NEEDS) == set(), "map the new AWS call to its IAM action in NEEDS and SCAN_ACTIONS"
    needed = {a for c in calls for a in NEEDS[c]}
    assert needed - set(SCAN_ACTIONS) - set(BEDROCK_ACTIONS) == set()
    assert {a for v in NEEDS.values() for a in v} <= set(SCAN_ACTIONS) | set(BEDROCK_ACTIONS)


def test_scan_actions_are_read_only_and_unique():
    assert len(SCAN_ACTIONS) == len(set(SCAN_ACTIONS))
    assert all(re.fullmatch(r"[a-z0-9]+:(Get|List|Describe)[A-Za-z]+", a) for a in SCAN_ACTIONS)
    assert BEDROCK_ACTIONS == ("bedrock:InvokeModel",)


def test_design_lists_the_same_actions():
    section = DESIGN.read_text().split("### Scan permissions", 1)[1].split("\n## ", 1)[0]
    listed = set(re.findall(r"`([a-z0-9]+:[A-Za-z]+)`", section))
    assert set(SCAN_ACTIONS) | set(BEDROCK_ACTIONS) <= listed
    assert "backup:DescribeBackupVault" not in listed


SEED_NEEDS = {
    "create_bucket": ["s3:CreateBucket", "s3:PutBucketObjectLockConfiguration"],
    "get_waiter": ["s3:ListBucket"],
    "put_public_access_block": ["s3:PutBucketPublicAccessBlock"],
    "put_bucket_tagging": ["s3:PutBucketTagging"], "get_bucket_tagging": ["s3:GetBucketTagging"],
    "put_bucket_ownership_controls": ["s3:PutBucketOwnershipControls"],
    "put_bucket_versioning": ["s3:PutBucketVersioning"],
    "put_bucket_lifecycle_configuration": ["s3:PutLifecycleConfiguration"],
    "put_bucket_policy": ["s3:PutBucketPolicy"], "delete_bucket_policy": ["s3:DeleteBucketPolicy"],
    "put_bucket_logging": ["s3:PutBucketLogging"],
    "put_bucket_replication": ["s3:PutReplicationConfiguration", "iam:PassRole"],
    "put_object": ["s3:PutObject"], "list_object_versions": ["s3:ListBucketVersions"],
    "delete_objects": ["s3:DeleteObject", "s3:DeleteObjectVersion"], "delete_bucket": ["s3:DeleteBucket"],
    "list_buckets": ["s3:ListAllMyBuckets"], "get_bucket_location": ["s3:GetBucketLocation"],
    "create_role": ["iam:CreateRole", "iam:TagRole"], "get_role": ["iam:GetRole"],
    "put_role_policy": ["iam:PutRolePolicy"], "delete_role_policy": ["iam:DeleteRolePolicy"],
    "delete_role": ["iam:DeleteRole"],
}


def test_seed_calls_are_listed_in_seed_actions_only():
    text = SEED.read_text()
    calls = set(re.findall(r"\.((?:get|list|put|create|delete)_[a-z_]+)\(", text)) - NOT_AWS
    calls |= set(re.findall(r"get_paginator\(\"([a-z_]+)\"\)", text))
    assert calls - set(SEED_NEEDS) == set(), "map the new seed call in SEED_NEEDS and SEED_ACTIONS"
    assert {a for c in calls for a in SEED_NEEDS[c]} <= set(SEED_ACTIONS)
    writes = {a for a in SEED_ACTIONS if not re.fullmatch(r"[a-z0-9]+:(Get|List|Describe)[A-Za-z]+", a)}
    assert not writes & set(SCAN_ACTIONS)


def _ts_list(name):
    ts = Path(__file__).resolve().parents[2] / "cdk" / "lib" / "scan-permissions.ts"
    body = re.search(r"export const " + name + r" = \[(.*?)\]", ts.read_text(), re.S).group(1)
    return re.findall(r"['\"]([a-z0-9]+:[A-Za-z]+)['\"]", body)


def test_cdk_mirror_equals_python_lists():
    assert _ts_list("SCAN_ACTIONS") == list(SCAN_ACTIONS)
    assert _ts_list("BEDROCK_ACTIONS") == list(BEDROCK_ACTIONS)
