/**
 * Authorization module for the GOAT Network Agent ↔ DevOps Agent Integration.
 *
 * Verifies whether the invoking role/identity is a member of the
 * Capture_Authorization_Group before allowing capture actions.
 *
 * Requirements: 1.4
 */

import { getAuthRequiredActions } from "../schemas/action-schemas";
import {
  createAuthorizationDeniedError,
  ErrorDescription,
} from "../types/errors";

/**
 * Default authorization group name, configurable via environment variable.
 */
const DEFAULT_AUTHORIZATION_GROUP = "Capture_Authorization_Group";

/**
 * Retrieves the configured authorization group name.
 * Uses the CAPTURE_AUTHORIZATION_GROUP environment variable if set,
 * otherwise falls back to the default.
 */
export function getAuthorizationGroupName(): string {
  return (
    process.env.CAPTURE_AUTHORIZATION_GROUP || DEFAULT_AUTHORIZATION_GROUP
  );
}

/**
 * Determines whether a given action requires authorization checks.
 *
 * @param actionName - The action being invoked
 * @returns true if the action requires Capture_Authorization_Group membership
 */
export function actionRequiresAuthorization(actionName: string): boolean {
  return getAuthRequiredActions().includes(actionName);
}

/**
 * Checks whether the invoking identity is authorized to perform capture actions.
 *
 * Verifies that the IAM role ARN or identity string is a member of the
 * Capture_Authorization_Group. The group membership is determined by
 * checking if the identity's path or tags include the authorization group.
 *
 * @param identityArn - The IAM role ARN or identity context of the invoker
 * @param authorizedMembers - List of ARN patterns or identities that are members
 *                            of the authorization group. If not provided, uses
 *                            the AUTHORIZED_ROLE_ARNS environment variable
 *                            (comma-separated list of ARN patterns).
 * @returns null if authorized, or an ErrorDescription if authorization is denied
 */
export function checkCaptureAuthorization(
  identityArn: string,
  authorizedMembers?: string[]
): ErrorDescription | null {
  const groupName = getAuthorizationGroupName();

  // Get authorized members from parameter or environment variable
  const members =
    authorizedMembers ??
    (process.env.AUTHORIZED_ROLE_ARNS?.split(",").map((s) => s.trim()) ?? []);

  // If no authorized members are configured, deny all access
  if (members.length === 0) {
    return createAuthorizationDeniedError(groupName);
  }

  // Check if the identity matches any authorized member pattern
  const isAuthorized = members.some((member) => {
    // Support exact match
    if (member === identityArn) {
      return true;
    }

    // Support wildcard suffix matching (e.g., "arn:aws:iam::123456789012:role/DevOps*")
    if (member.endsWith("*")) {
      const prefix = member.slice(0, -1);
      return identityArn.startsWith(prefix);
    }

    return false;
  });

  if (!isAuthorized) {
    return createAuthorizationDeniedError(groupName);
  }

  return null;
}
