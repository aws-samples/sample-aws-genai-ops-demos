/**
 * Integration Lambda Handler (Request Router) for the GOAT Network Agent ↔ DevOps Agent Integration.
 *
 * Wires together validation, authorization, rate limiting, idempotency, and agent proxy.
 * Routes requests based on action_name, applies capture-specific checks for capture actions,
 * and returns a standardized response envelope.
 *
 * Requirements: 1.1, 1.2, 2.5
 */

import type { APIGatewayProxyEvent, APIGatewayProxyResult, Context } from "aws-lambda";
import { validateRequest } from "./validation";
import { actionRequiresAuthorization, checkCaptureAuthorization } from "./authorization";
import { checkCaptureRateLimit } from "./rate-limiter";
import { generateIdempotencyToken } from "./idempotency";
import { invokeNetworkAgent, AgentProxyResult } from "./agent-proxy";
import { DevOpsAgentInvocation, DevOpsAgentResponse } from "../types/index";
import { ErrorDescription, ErrorCode, ERROR_HTTP_STATUS } from "../types/errors";
import { getActionsByCategory } from "../schemas/action-schemas";

// ─── Capture Action Names ───────────────────────────────────────────────────

/** Actions classified as "capture" category that require rate limiting */
const CAPTURE_ACTIONS = ["start_capture", "stop_capture", "transform_capture"];

// ─── Response Helpers ───────────────────────────────────────────────────────

/**
 * Creates a successful DevOpsAgentResponse envelope.
 */
function createSuccessResponse(actionName: string, result: unknown): DevOpsAgentResponse {
  return {
    action_name: actionName,
    status: "success",
    timestamp: new Date().toISOString(),
    result: result as DevOpsAgentResponse["result"],
  };
}

/**
 * Creates an error DevOpsAgentResponse envelope.
 */
function createErrorResponse(actionName: string, error: ErrorDescription): DevOpsAgentResponse {
  return {
    action_name: actionName,
    status: "error",
    timestamp: new Date().toISOString(),
    error,
  };
}

/**
 * Determines if a result from the agent proxy is an error (ErrorDescription has 'code' property).
 */
function isErrorDescription(result: AgentProxyResult | ErrorDescription): result is ErrorDescription {
  return "code" in result;
}

/**
 * Checks if a given action is a capture action requiring rate limiting.
 */
function isCaptureAction(actionName: string): boolean {
  return CAPTURE_ACTIONS.includes(actionName);
}

// ─── Core Processing Logic ──────────────────────────────────────────────────

/**
 * Processes a DevOps Agent invocation through the full request pipeline.
 *
 * This is the pure business logic function, separated from Lambda event parsing
 * for easier unit testing.
 *
 * Pipeline:
 * 1. Validate request payload against action schema
 * 2. Check authorization for capture actions
 * 3. Enforce rate limit for capture actions
 * 4. Generate idempotency token for start_capture
 * 5. Invoke Network Agent via proxy
 * 6. Return standardized response envelope
 *
 * @param invocation - The parsed DevOps Agent invocation request
 * @param callerIdentity - The IAM identity ARN of the invoking role (for authorization)
 * @returns A standardized DevOpsAgentResponse
 */
export async function processInvocation(
  invocation: DevOpsAgentInvocation,
  callerIdentity?: string
): Promise<DevOpsAgentResponse> {
  const { action_name, parameters, session_id } = invocation;

  // Log invocation for audit purposes
  console.log(JSON.stringify({
    event: "invocation_received",
    action_name,
    session_id,
    source: "devops_agent",
    timestamp: new Date().toISOString(),
  }));

  // Step 1: Schema validation
  const validationResult = validateRequest(invocation);
  if (!validationResult.valid) {
    return createErrorResponse(action_name, validationResult.error);
  }

  // Step 2: Authorization check (for actions requiring it)
  if (actionRequiresAuthorization(action_name)) {
    const identity = callerIdentity ?? "";
    const authError = checkCaptureAuthorization(identity);
    if (authError) {
      return createErrorResponse(action_name, authError);
    }
  }

  // Step 3: Rate limit check (for capture actions) — fail-open if DynamoDB is unavailable
  if (isCaptureAction(action_name)) {
    try {
      const rateLimitError = await checkCaptureRateLimit();
      if (rateLimitError) {
        return createErrorResponse(action_name, rateLimitError);
      }
    } catch (e) {
      // Rate limiter uses DynamoDB which may not be configured for this deployment.
      // Fail open — the Network Agent has its own rate limiting.
      console.warn("Rate limit check failed (proceeding without rate limit):", e);
    }
  }

  // Step 4: Idempotency token generation (for start_capture)
  let effectiveParameters = { ...parameters };
  if (action_name === "start_capture") {
    const idempotencyToken = generateIdempotencyToken(session_id);
    effectiveParameters = {
      ...effectiveParameters,
      idempotency_token: idempotencyToken,
      source: "devops_agent",
    };
  }

  // Step 5: Invoke Network Agent via proxy
  const agentResult = await invokeNetworkAgent(action_name, effectiveParameters);

  // Step 6: Build response envelope
  if (isErrorDescription(agentResult)) {
    return createErrorResponse(action_name, agentResult);
  }

  // Parse the agent output — try JSON first, fallback to raw string
  let parsedResult: unknown;
  try {
    parsedResult = JSON.parse(agentResult.output);
  } catch {
    parsedResult = { data: { output: agentResult.output } };
  }

  return createSuccessResponse(action_name, parsedResult);
}

// ─── Lambda Handler ─────────────────────────────────────────────────────────

/**
 * AWS Lambda handler function for the Integration Lambda.
 *
 * Parses the incoming API Gateway event to extract the DevOpsAgentInvocation,
 * processes it through the request pipeline, and returns an HTTP response.
 *
 * @param event - API Gateway proxy event
 * @param context - Lambda execution context
 * @returns API Gateway proxy result with the response envelope
 */
export async function handler(
  event: APIGatewayProxyEvent,
  context: Context
): Promise<APIGatewayProxyResult> {
  const startTime = Date.now();

  try {
    // Parse the request body
    if (!event.body) {
      return buildHttpResponse(400, createErrorResponse("unknown", {
        code: ErrorCode.SCHEMA_VALIDATION_FAILED,
        message: "Request body is required",
      }));
    }

    let invocation: DevOpsAgentInvocation;
    try {
      invocation = JSON.parse(event.body) as DevOpsAgentInvocation;
    } catch {
      return buildHttpResponse(400, createErrorResponse("unknown", {
        code: ErrorCode.SCHEMA_VALIDATION_FAILED,
        message: "Request body must be valid JSON",
      }));
    }

    // Validate required top-level fields
    if (!invocation.action_name || !invocation.session_id) {
      return buildHttpResponse(400, createErrorResponse(invocation.action_name ?? "unknown", {
        code: ErrorCode.SCHEMA_VALIDATION_FAILED,
        message: "Request must include action_name and session_id fields",
      }));
    }

    // Extract caller identity from the API Gateway request context
    const callerIdentity = extractCallerIdentity(event);

    // Process the invocation
    const response = await processInvocation(invocation, callerIdentity);

    // Determine HTTP status code
    const httpStatus = response.status === "success"
      ? 200
      : getHttpStatusForError(response.error);

    // Log completion for audit
    console.log(JSON.stringify({
      event: "invocation_completed",
      action_name: invocation.action_name,
      session_id: invocation.session_id,
      status: response.status,
      duration_ms: Date.now() - startTime,
      source: "devops_agent",
      request_id: context.awsRequestId,
    }));

    return buildHttpResponse(httpStatus, response);
  } catch (error: unknown) {
    // Unexpected error — log but don't expose internals
    console.error("Unexpected handler error:", error);

    const errorResponse = createErrorResponse("unknown", {
      code: ErrorCode.NETWORK_AGENT_ERROR,
      message: "An unexpected error occurred while processing the request.",
    });

    return buildHttpResponse(500, errorResponse);
  }
}

// ─── Internal Helpers ───────────────────────────────────────────────────────

/**
 * Extracts the caller's IAM identity ARN from the API Gateway event.
 */
function extractCallerIdentity(event: APIGatewayProxyEvent): string {
  // API Gateway with IAM auth populates requestContext.identity.userArn
  return event.requestContext?.identity?.userArn ?? "";
}

/**
 * Determines the appropriate HTTP status code from an error description.
 */
function getHttpStatusForError(error?: ErrorDescription): number {
  if (!error) return 500;
  return ERROR_HTTP_STATUS[error.code] ?? 500;
}

/**
 * Builds a standardized API Gateway proxy result.
 */
function buildHttpResponse(
  statusCode: number,
  body: DevOpsAgentResponse
): APIGatewayProxyResult {
  return {
    statusCode,
    headers: {
      "Content-Type": "application/json",
      "X-Request-Source": "devops_agent",
    },
    body: JSON.stringify(body),
  };
}
