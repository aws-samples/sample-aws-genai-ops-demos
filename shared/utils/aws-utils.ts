/**
 * Shared AWS utility functions for GenAI Ops demos.
 *
 * Provides consistent region detection and AWS configuration across all demos.
 * Mirror of shared/utils/aws_utils.py -- keep the two in sync.
 */

import { execSync } from 'child_process';

/**
 * Detect AWS region using consistent priority order.
 *
 * Priority:
 * 1. AWS_DEFAULT_REGION environment variable (temporary override)
 * 2. AWS_REGION environment variable (alternative)
 * 3. AWS CLI configuration (aws configure get region)
 * 4. Fallback to us-east-1 if nothing configured
 *
 * @returns AWS region name
 */
export function getRegion(): string {
  // Check environment variables first
  let region = process.env.AWS_DEFAULT_REGION || process.env.AWS_REGION;

  if (region) {
    return region;
  }

  // Try AWS CLI configuration
  try {
    region = execSync('aws configure get region', { encoding: 'utf-8' }).trim();
    if (region) {
      return region;
    }
  } catch {
    // AWS CLI not available or no region configured
  }

  // Fallback
  return 'us-east-1';
}

/**
 * Get AWS account ID from current credentials.
 *
 * @returns AWS account ID or null if unable to determine
 */
export function getAccountId(): string | null {
  try {
    const accountId = execSync('aws sts get-caller-identity --query Account --output text', {
      encoding: 'utf-8',
    }).trim();
    return accountId || null;
  } catch {
    return null;
  }
}

/**
 * AWS Transform CUSTOM (code transformation, the `atx custom` product) regions.
 * Static on purpose: Transform availability cannot be probed at runtime, and the
 * list only picks a FALLBACK (the deploy region is always tried first). See
 * shared/utils/aws_utils.py TRANSFORM_REGIONS for the rationale and the source.
 *
 * Mirrors aws_utils.py TRANSFORM_REGIONS -- keep in sync.
 */
export const TRANSFORM_REGIONS: ReadonlySet<string> = new Set<string>([
  'us-east-1', // US East (N. Virginia)
  'ca-central-1', // Canada (Central)
  'eu-central-1', // Europe (Frankfurt)
  'eu-west-2', // Europe (London)
  'ap-south-1', // Asia Pacific (Mumbai)
  'ap-southeast-2', // Asia Pacific (Sydney)
  'ap-northeast-1', // Asia Pacific (Tokyo)
  'ap-northeast-2', // Asia Pacific (Seoul)
]);

/**
 * Pick the AWS Transform control-plane region for a deployment region.
 *
 * If the deploy region is itself a Transform region, use it; otherwise map by
 * geo to that geo's nearest Transform region (ap-south-* to Mumbai,
 * ap-southeast-* to Sydney, other ap-* to Tokyo, eu-* to Frankfurt, us-* or
 * ca-* to N. Virginia); anything else to us-east-1. Mirrors aws_utils.py
 * get_transform_region exactly.
 *
 * @param deployRegion Region to map. Defaults to the detected deploy region.
 * @returns An AWS region where AWS Transform custom is available.
 */
export function getTransformRegion(deployRegion?: string): string {
  const region = deployRegion || getRegion();

  // 1. Already a Transform region.
  if (TRANSFORM_REGIONS.has(region)) {
    return region;
  }

  // 2. Nearest by geo. Order matters: more specific ap-* prefixes first.
  if (region.startsWith('ap-south-')) {
    return 'ap-south-1';
  }
  if (region.startsWith('ap-southeast-')) {
    return 'ap-southeast-2';
  }
  if (region.startsWith('ap-')) {
    return 'ap-northeast-1';
  }
  if (region.startsWith('eu-')) {
    return 'eu-central-1';
  }
  if (region.startsWith('us-') || region.startsWith('ca-')) {
    return 'us-east-1';
  }

  // 3. Global default.
  return 'us-east-1';
}
