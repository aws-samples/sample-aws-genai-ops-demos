/**
 * Region validation module for the GOAT Network Agent ↔ DevOps Agent Integration.
 *
 * Detects region mismatches between target resources and the deployed
 * Network Agent infrastructure, preventing cross-region invocations
 * that would fail at the capture layer.
 *
 * Requirements: 2.5
 */

import {
  createRegionMismatchError,
  ErrorDescription,
} from "../types/errors";

/**
 * Regular expression to extract AWS region from an ENI ID format.
 * ENI IDs are region-specific and follow the pattern: eni-<hex> in a specific region.
 * However, ENI IDs themselves don't contain the region. Region is typically
 * passed as an explicit parameter or inferred from ARN-like formats.
 *
 * ARN format: arn:aws:ec2:<region>:<account>:network-interface/eni-<id>
 */
const ENI_ARN_REGION_REGEX = /arn:aws:ec2:([a-z]{2}-[a-z]+-\d+):/;

/**
 * Regular expression to match a valid AWS region format.
 */
const AWS_REGION_REGEX = /^[a-z]{2}-[a-z]+-\d+$/;

/**
 * Checks if the target resource region differs from the deployed infrastructure region.
 *
 * If a region mismatch is detected, returns an ErrorDescription indicating
 * that capture infrastructure is not available in the target region.
 * If regions match or targetRegion is not provided, returns null (allow the request through).
 *
 * @param targetRegion - The region where the target resource resides (extracted from parameters)
 * @param deployedRegion - The region where the Network Agent infrastructure is deployed.
 *                         Defaults to process.env.AWS_REGION or process.env.DEPLOYED_REGION.
 * @returns null if no mismatch (request should proceed), or an ErrorDescription if regions differ
 */
export function checkRegionMismatch(
  targetRegion: string | undefined,
  deployedRegion?: string
): ErrorDescription | null {
  // Determine the deployed region from environment if not explicitly provided
  const effectiveDeployedRegion =
    deployedRegion ?? process.env.AWS_REGION ?? process.env.DEPLOYED_REGION;

  // If we cannot determine the deployed region, allow the request through
  // This is a configuration issue that should be caught at deployment time
  if (!effectiveDeployedRegion) {
    return null;
  }

  // If target region is not provided, allow the request through
  // (we cannot determine if there's a mismatch without knowing the target)
  if (!targetRegion) {
    return null;
  }

  // If the target region matches the deployed region, no mismatch
  if (targetRegion === effectiveDeployedRegion) {
    return null;
  }

  // Regions differ — return a region mismatch error
  return createRegionMismatchError(targetRegion, effectiveDeployedRegion);
}

/**
 * Extracts the target region from invocation parameters.
 *
 * Checks for explicit region parameter first, then attempts to extract
 * region from ENI ARNs if eni_ids are provided in ARN format.
 * If no region can be determined from parameters, returns undefined
 * (allowing the request through — the region is assumed to be the same
 * as the deployed infrastructure).
 *
 * @param parameters - The action-specific parameters from the invocation request
 * @returns The extracted target region string, or undefined if no region could be determined
 */
export function extractTargetRegion(
  parameters: Record<string, unknown>
): string | undefined {
  // Check for explicit region parameter
  if (typeof parameters.region === "string" && AWS_REGION_REGEX.test(parameters.region)) {
    return parameters.region;
  }

  // Check for target_region parameter
  if (typeof parameters.target_region === "string" && AWS_REGION_REGEX.test(parameters.target_region)) {
    return parameters.target_region;
  }

  // Attempt to extract region from eni_ids in ARN format
  const eniIds = parameters.eni_ids;
  if (Array.isArray(eniIds) && eniIds.length > 0) {
    for (const eniId of eniIds) {
      if (typeof eniId === "string") {
        const match = eniId.match(ENI_ARN_REGION_REGEX);
        if (match && match[1]) {
          return match[1];
        }
      }
    }
  }

  // Check for a single eni_id parameter (non-array)
  if (typeof parameters.eni_id === "string") {
    const match = parameters.eni_id.match(ENI_ARN_REGION_REGEX);
    if (match && match[1]) {
      return match[1];
    }
  }

  // No region could be determined from parameters — allow through
  return undefined;
}
