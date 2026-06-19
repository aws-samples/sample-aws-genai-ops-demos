/**
 * Error code enums and error response factory functions.
 * Defines the 8 error categories from the design document and provides
 * type-safe factories for creating standardized error responses.
 *
 * Requirements: 1.2, 1.3, 1.4, 1.6, 1.7
 */

/**
 * Error codes for the DevOps Agent Tool Interface.
 * Each code maps to a specific HTTP status and trigger condition.
 */
export enum ErrorCode {
  /** Request payload fails JSON Schema validation (400) */
  SCHEMA_VALIDATION_FAILED = "schema_validation_failed",
  /** Invoking role not in Capture_Authorization_Group (403) */
  AUTHORIZATION_DENIED = "authorization_denied",
  /** Active captures ≥ 3 per tool interface (429) */
  RATE_LIMIT_EXCEEDED = "rate_limit_exceeded",
  /** Network Agent no response within 60s (504) */
  TIMEOUT = "timeout",
  /** Target resources in different region (400) */
  REGION_MISMATCH = "region_mismatch",
  /** SigV4 signature invalid or missing (401) */
  AUTH_FAILED = "auth_failed",
  /** Network Agent returns error envelope (502) */
  NETWORK_AGENT_ERROR = "network_agent_error",
  /** NetworkInfra stack exports not resolvable (deployment failure) */
  INFRA_UNAVAILABLE = "infra_unavailable",
}

/**
 * Maps error codes to their corresponding HTTP status codes.
 */
export const ERROR_HTTP_STATUS: Record<ErrorCode, number> = {
  [ErrorCode.SCHEMA_VALIDATION_FAILED]: 400,
  [ErrorCode.AUTHORIZATION_DENIED]: 403,
  [ErrorCode.RATE_LIMIT_EXCEEDED]: 429,
  [ErrorCode.TIMEOUT]: 504,
  [ErrorCode.REGION_MISMATCH]: 400,
  [ErrorCode.AUTH_FAILED]: 401,
  [ErrorCode.NETWORK_AGENT_ERROR]: 502,
  [ErrorCode.INFRA_UNAVAILABLE]: 503,
};

/**
 * Structured error description returned in the DevOps Agent response envelope.
 */
export interface ErrorDescription {
  /** Error code identifying the category of failure */
  code: ErrorCode;
  /** Human-readable error message */
  message: string;
  /** Additional structured details about the error */
  details?: Record<string, unknown>;
}

/**
 * Parameters for schema validation error details.
 */
export interface SchemaValidationErrorDetails {
  /** Parameters that failed validation */
  failing_parameters: string[];
  /** Expected schema constraints that were violated */
  expected_constraints: Record<string, string>;
}

/**
 * Parameters for rate limit error details.
 */
export interface RateLimitErrorDetails {
  /** Number of currently active captures */
  active_captures: number;
  /** Maximum allowed concurrent captures */
  limit: number;
}

/**
 * Parameters for authorization error details.
 */
export interface AuthorizationErrorDetails {
  /** Name of the required authorization group */
  required_group: string;
}

/**
 * Parameters for region mismatch error details.
 */
export interface RegionMismatchErrorDetails {
  /** Region where the target resource resides */
  target_region: string;
  /** Region where the Network Agent infrastructure is deployed */
  deployed_region: string;
}

// ─── Error Response Factory Functions ───────────────────────────────────────

/**
 * Creates a schema validation failed error description.
 * Returned when request payload does not conform to the action's JSON Schema.
 */
export function createSchemaValidationError(
  failingParameters: string[],
  expectedConstraints: Record<string, string>
): ErrorDescription {
  return {
    code: ErrorCode.SCHEMA_VALIDATION_FAILED,
    message: `Request payload validation failed for parameters: ${failingParameters.join(", ")}`,
    details: {
      failing_parameters: failingParameters,
      expected_constraints: expectedConstraints,
    } satisfies SchemaValidationErrorDetails as unknown as Record<string, unknown>,
  };
}

/**
 * Creates an authorization denied error description.
 * Returned when the invoking role is not in the Capture_Authorization_Group.
 */
export function createAuthorizationDeniedError(
  requiredGroup: string
): ErrorDescription {
  return {
    code: ErrorCode.AUTHORIZATION_DENIED,
    message: `Insufficient permissions. Membership in authorization group "${requiredGroup}" is required for capture actions.`,
    details: {
      required_group: requiredGroup,
    } satisfies AuthorizationErrorDetails as unknown as Record<string, unknown>,
  };
}

/**
 * Creates a rate limit exceeded error description.
 * Returned when active concurrent captures ≥ 3.
 */
export function createRateLimitExceededError(
  activeCaptureCount: number,
  limit: number = 3
): ErrorDescription {
  return {
    code: ErrorCode.RATE_LIMIT_EXCEEDED,
    message: `Maximum concurrent captures (${limit}) reached. Currently active: ${activeCaptureCount}.`,
    details: {
      active_captures: activeCaptureCount,
      limit,
    } satisfies RateLimitErrorDetails as unknown as Record<string, unknown>,
  };
}

/**
 * Creates a timeout error description.
 * Returned when the Network Agent does not respond within 60 seconds.
 * Does NOT expose internal system details (ARNs, IPs, stack traces).
 */
export function createTimeoutError(actionName: string): ErrorDescription {
  return {
    code: ErrorCode.TIMEOUT,
    message: `Action "${actionName}" did not complete within the allowed time limit.`,
  };
}

/**
 * Creates a region mismatch error description.
 * Returned when target resources are in a different region than the Network Agent infrastructure.
 */
export function createRegionMismatchError(
  targetRegion: string,
  deployedRegion: string
): ErrorDescription {
  return {
    code: ErrorCode.REGION_MISMATCH,
    message: `Capture infrastructure is not available in region "${targetRegion}". The Network Agent is deployed in "${deployedRegion}".`,
    details: {
      target_region: targetRegion,
      deployed_region: deployedRegion,
    } satisfies RegionMismatchErrorDetails as unknown as Record<string, unknown>,
  };
}

/**
 * Creates an authentication failed error description.
 * Returned when SigV4 signature is invalid or missing.
 */
export function createAuthFailedError(): ErrorDescription {
  return {
    code: ErrorCode.AUTH_FAILED,
    message: "Invalid or missing credentials.",
  };
}

/**
 * Creates a network agent error description.
 * Returned when the Network Agent returns an error. Sanitizes internal details.
 */
export function createNetworkAgentError(actionName: string): ErrorDescription {
  return {
    code: ErrorCode.NETWORK_AGENT_ERROR,
    message: `The underlying network agent encountered an error while processing action "${actionName}".`,
  };
}

/**
 * Creates an infrastructure unavailable error description.
 * Returned when NetworkInfra stack exports are not resolvable during deployment.
 */
export function createInfraUnavailableError(
  missingExportKey: string
): ErrorDescription {
  return {
    code: ErrorCode.INFRA_UNAVAILABLE,
    message: `Required infrastructure export "${missingExportKey}" is not available. Ensure the GOATNetworkInfra stack is deployed first.`,
    details: {
      missing_export_key: missingExportKey,
    },
  };
}
