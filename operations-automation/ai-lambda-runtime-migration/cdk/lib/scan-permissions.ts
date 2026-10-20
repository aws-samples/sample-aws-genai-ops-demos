// What a Discover+Assess run needs to read in an account (design §2.1, §5).
//
// Single source of truth for two roles that must stay identical:
//  - the hub pipeline role (scans the hub account itself)
//  - the read-only spoke role (assumed by the hub in every member account for
//    the opt-in multi-account scope; its StackSet rollout is Phase 2, but the
//    hub-side trust and action list live here now so the two cannot drift).
// Adding a discovery/assessment call means adding it here, so the hub role and
// any future spoke role grant exactly the same permissions.

/** Read-only calls Discover makes to find and enrich deprecated-runtime functions. */
export const DISCOVER_READ_ACTIONS: string[] = [
  // Trusted Advisor deprecated-runtime check (L4dfs2Q4C5) via the Support API.
  'support:DescribeTrustedAdvisorCheckResult',
  // Per-account Support tier inference (ceiling of the openable case severities),
  // so a Basic/Developer account reports "TA not checked" instead of empty.
  'support:DescribeSeverityLevels',
  // Lambda configuration + tags for each flagged function (enrichment).
  'lambda:GetFunction',
  'lambda:GetFunctionConfiguration',
  'lambda:ListTags',
  // CloudWatch logs/metrics enrichment (last invocation, 14-day invocation count).
  'logs:DescribeLogStreams',
  'cloudwatch:GetMetricStatistics',
];

/** Name of the read-only role the hub assumes in each spoke account (Phase 2
 *  rollout). Must match SPOKE_ROLE_NAME in the pipeline function environment. */
export const SPOKE_ROLE_NAME = 'LambdaRuntimeMigrationScanRole';

/** Base name of the hub pipeline role. The DEPLOYED role name is region-suffixed
 *  (see hubPipelineRoleName), so global IAM role names never collide across
 *  regions. Spoke trust policies must pin the region-suffixed name. */
export const HUB_PIPELINE_ROLE_NAME = 'lambda-migration-pipeline-role';

/** Actual, region-suffixed name of the deployed hub pipeline role.
 *
 * Single source of truth for that name: PipelineStack (hub) names the role with
 * it, and SpokeStack pins its `aws:PrincipalArn` trust condition to it, so the
 * hub role name and the spoke trust can never drift. `region` is always
 * `this.region` of the stack — never a hardcoded region literal. */
export function hubPipelineRoleName(region: string): string {
  return `${HUB_PIPELINE_ROLE_NAME}-${region}`;
}
