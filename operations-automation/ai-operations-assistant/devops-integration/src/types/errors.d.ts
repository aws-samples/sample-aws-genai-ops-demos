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
export declare enum ErrorCode {
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
    INFRA_UNAVAILABLE = "infra_unavailable"
}
/**
 * Maps error codes to their corresponding HTTP status codes.
 */
export declare const ERROR_HTTP_STATUS: Record<ErrorCode, number>;
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
/**
 * Creates a schema validation failed error description.
 * Returned when request payload does not conform to the action's JSON Schema.
 */
export declare function createSchemaValidationError(failingParameters: string[], expectedConstraints: Record<string, string>): ErrorDescription;
/**
 * Creates an authorization denied error description.
 * Returned when the invoking role is not in the Capture_Authorization_Group.
 */
export declare function createAuthorizationDeniedError(requiredGroup: string): ErrorDescription;
/**
 * Creates a rate limit exceeded error description.
 * Returned when active concurrent captures ≥ 3.
 */
export declare function createRateLimitExceededError(activeCaptureCount: number, limit?: number): ErrorDescription;
/**
 * Creates a timeout error description.
 * Returned when the Network Agent does not respond within 60 seconds.
 * Does NOT expose internal system details (ARNs, IPs, stack traces).
 */
export declare function createTimeoutError(actionName: string): ErrorDescription;
/**
 * Creates a region mismatch error description.
 * Returned when target resources are in a different region than the Network Agent infrastructure.
 */
export declare function createRegionMismatchError(targetRegion: string, deployedRegion: string): ErrorDescription;
/**
 * Creates an authentication failed error description.
 * Returned when SigV4 signature is invalid or missing.
 */
export declare function createAuthFailedError(): ErrorDescription;
/**
 * Creates a network agent error description.
 * Returned when the Network Agent returns an error. Sanitizes internal details.
 */
export declare function createNetworkAgentError(actionName: string): ErrorDescription;
/**
 * Creates an infrastructure unavailable error description.
 * Returned when NetworkInfra stack exports are not resolvable during deployment.
 */
export declare function createInfraUnavailableError(missingExportKey: string): ErrorDescription;
