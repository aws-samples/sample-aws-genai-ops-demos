// What a scan needs to read in an account (issue #144, hub-and-spoke).
//
// Single source of truth for two roles that must stay identical:
//  - the hub pipeline role (scans the hub account itself)
//  - the spoke role LifecycleTrackerScanRole (assumed by the hub in every
//    member account, deployed by SpokeStack / the StackSet)
// Adding a scanner means adding its List/Describe calls here, nowhere else.

/** Read-only calls made by backend/account_discovery.py scanners. */
export const SCANNER_READ_ACTIONS: string[] = [
  'lambda:ListFunctions',
  'rds:DescribeDBInstances', 'rds:DescribeDBClusters',
  'eks:ListClusters', 'eks:DescribeCluster',
  'elasticache:DescribeCacheClusters',
  'es:ListDomainNames', 'es:DescribeDomain',
  'kafka:ListClustersV2',
  'neptune:DescribeDBClusters',
  'glue:GetJobs',
  'elasticbeanstalk:DescribeEnvironments',
  'ec2:DescribeInstances',
];

/** AWS Health cross-check (#141): open planned-lifecycle notices and the
 *  resources they name. Account-scoped, so the spoke needs it too. */
export const HEALTH_READ_ACTIONS: string[] = [
  'health:DescribeEvents', 'health:DescribeAffectedEntities',
];

/** Name of the read-only role the hub assumes in each spoke account. Must
 *  match SPOKE_ROLE_NAME in the pipeline function environment. */
export const SPOKE_ROLE_NAME = 'LifecycleTrackerScanRole';

/** Fixed name of the hub pipeline role, so spoke trust policies can pin it. */
export const HUB_PIPELINE_ROLE_NAME = 'aws-services-lifecycle-pipeline-role';
