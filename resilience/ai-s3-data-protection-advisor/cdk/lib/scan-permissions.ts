// Mirrors backend/s3dp/permissions.py (backend/tests/test_permissions.py checks both lists are equal).
export const SCAN_ACTIONS = [
  'sts:GetCallerIdentity',
  's3:ListAllMyBuckets',
  's3:GetBucketLocation',
  's3:GetBucketVersioning',
  's3:GetBucketObjectLockConfiguration',
  's3:GetReplicationConfiguration',
  's3:GetEncryptionConfiguration',
  's3:GetLifecycleConfiguration',
  's3:GetBucketPublicAccessBlock',
  's3:GetBucketPolicy',
  's3:GetBucketPolicyStatus',
  's3:GetBucketOwnershipControls',
  's3:GetBucketTagging',
  's3:GetBucketLogging',
  's3:GetBucketNotification',
  's3:GetAccountPublicAccessBlock',
  'iam:ListAccountAliases',
  'iam:ListRoles',
  'ce:GetCostAndUsage',
  'backup:ListProtectedResources',
  'backup:ListBackupPlans',
  'backup:GetBackupPlan',
  'backup:ListBackupSelections',
  'backup:GetBackupSelection',
  'backup:ListBackupVaults',
  'backup:DescribeRegionSettings',
  'backup:ListRestoreTestingPlans',
  'backup:ListFrameworks',
  'cloudformation:ListStacks',
  'config:DescribeConfigurationRecorders',
  'config:DescribeConfigurationRecorderStatus',
  'config:DescribeConfigRules',
  'securityhub:GetEnabledStandards',
  'cloudwatch:ListMetrics',
  'cloudwatch:GetMetricData',
];

export const BEDROCK_ACTIONS = ['bedrock:InvokeModel'];

// Hub pipeline role only, multi-account deployments only (mirrors ORG_ACTIONS in permissions.py).
export const ORG_ACTIONS = ['organizations:ListAccounts'];

// Fixed name: the hub builds each member account's role ARN from the account id (mirrors permissions.py).
export const SPOKE_ROLE_NAME = 'S3DataProtectionScanRole';

// Fixed, region-suffixed hub role name: the spoke trust policy pins aws:PrincipalArn to it, and two hub
// deployments in different regions of one account must not collide (IAM is global).
export const hubRoleName = (region: string): string => `S3DataProtectionHub-${region}`;
