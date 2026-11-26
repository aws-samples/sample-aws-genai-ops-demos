"""IAM actions the scan needs. Plan 2's CDK scan and spoke roles mirror SCAN_ACTIONS; the pipeline function also
gets BEDROCK_ACTIONS on the Amazon Nova Lite model only. tests/test_permissions.py checks that every AWS call in the
backend is covered. IAM action names differ from some API names (for example GetBucketReplication needs
s3:GetReplicationConfiguration)."""

SCAN_ACTIONS = (
    "sts:GetCallerIdentity",
    # S3, per bucket
    "s3:ListAllMyBuckets",                      # ListBuckets
    "s3:GetBucketLocation",
    "s3:GetBucketVersioning",
    "s3:GetBucketObjectLockConfiguration",      # GetObjectLockConfiguration
    "s3:GetReplicationConfiguration",           # GetBucketReplication
    "s3:GetEncryptionConfiguration",            # GetBucketEncryption
    "s3:GetLifecycleConfiguration",             # GetBucketLifecycleConfiguration
    "s3:GetBucketPublicAccessBlock",            # GetPublicAccessBlock (bucket)
    "s3:GetBucketPolicy",
    "s3:GetBucketPolicyStatus",
    "s3:GetBucketOwnershipControls",
    "s3:GetBucketTagging",
    "s3:GetBucketLogging",
    "s3:GetBucketNotification",                 # GetBucketNotificationConfiguration
    # S3 Control, per account
    "s3:GetAccountPublicAccessBlock",           # S3 Control GetPublicAccessBlock
    # IAM
    "iam:ListAccountAliases",
    "iam:ListRoles",
    # Cost Explorer
    "ce:GetCostAndUsage",
    # AWS Backup, per region
    "backup:ListProtectedResources",
    "backup:ListBackupPlans",
    "backup:GetBackupPlan",
    "backup:ListBackupSelections",
    "backup:GetBackupSelection",
    "backup:ListBackupVaults",
    "backup:DescribeRegionSettings",
    "backup:ListRestoreTestingPlans",
    "backup:ListFrameworks",
    # CloudFormation, AWS Config, Security Hub, CloudWatch, per region
    "cloudformation:ListStacks",
    "config:DescribeConfigurationRecorders",
    "config:DescribeConfigurationRecorderStatus",
    "config:DescribeConfigRules",
    "securityhub:GetEnabledStandards",
    "cloudwatch:ListMetrics",
    "cloudwatch:GetMetricData",
)

BEDROCK_ACTIONS = ("bedrock:InvokeModel",)    # Converse

# Mock estate (s3dp/seed.py, sandbox only, run from a workstation). Never granted to the scan, spoke or pipeline
# roles. Bucket names are limited to s3dp-<account>-* and the role to s3dp-demo-replication.
SEED_ACTIONS = (
    "sts:GetCallerIdentity",
    "iam:ListAccountAliases",
    "s3:ListAllMyBuckets",
    "s3:GetBucketLocation",
    "s3:CreateBucket",
    "s3:ListBucket",                            # bucket_exists waiter (HeadBucket)
    "s3:PutBucketPublicAccessBlock",
    "s3:PutBucketTagging",
    "s3:GetBucketTagging",
    "s3:PutBucketOwnershipControls",
    "s3:PutBucketVersioning",
    "s3:PutLifecycleConfiguration",
    "s3:PutBucketPolicy",
    "s3:DeleteBucketPolicy",
    "s3:PutBucketLogging",
    "s3:PutReplicationConfiguration",
    "s3:PutBucketObjectLockConfiguration",      # CreateBucket with ObjectLockEnabledForBucket
    "s3:PutObject",
    "s3:ListBucketVersions",                    # ListObjectVersions
    "s3:DeleteObject",
    "s3:DeleteObjectVersion",
    "s3:DeleteBucket",
    "iam:CreateRole",
    "iam:GetRole",
    "iam:TagRole",
    "iam:PutRolePolicy",
    "iam:PassRole",                             # PutBucketReplication passes the replication role
    "iam:DeleteRolePolicy",
    "iam:DeleteRole",
)
