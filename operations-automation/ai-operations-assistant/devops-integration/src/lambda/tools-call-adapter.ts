/**
 * MCP tools/call Adapter for the GOAT Network Agent ↔ DevOps Agent Integration.
 *
 * Bridges MCP tools/call params into the existing processInvocation interface
 * and transforms the result back into CallToolResult format.
 *
 * This adapter:
 * 1. Validates tool name exists in the Action_Registry
 * 2. Maps MCP params to DevOpsAgentInvocation
 * 3. Calls processInvocation unchanged
 * 4. Transforms DevOpsAgentResponse → CallToolResult
 *
 * Requirements: 1.3, 1.8, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 9.1, 9.3
 */

import type { CallToolResult, ToolsCallParams, JsonRpcError } from "../types/mcp";
import { JSON_RPC_ERROR_CODES } from "../types/mcp";
import type { DevOpsAgentInvocation } from "../types/index";
import { processInvocation } from "./handler";
import { isValidAction } from "../schemas/action-schemas";

// ─── Report Size Constraint ─────────────────────────────────────────────────

/**
 * Maximum allowed size for serialized report content in tools/call responses.
 * Matches the 32,000 character limit defined in the DiagnosticReport spec.
 *
 * Requirements: 9.2, 9.4
 */
export const MAX_REPORT_SIZE = 32_000;

/**
 * Enforces the 32,000 character size constraint on serialized tool results.
 *
 * Strategy:
 * 1. If the serialized result fits within the limit, return as-is.
 * 2. If the result looks like a DiagnosticReport (has `raw_evidence` array and `summary`),
 *    truncate `raw_evidence` oldest-first (remove from beginning) until it fits,
 *    adding a `truncation_notice` field.
 * 3. If truncation still can't fit or it's not a DiagnosticReport, hard-truncate the
 *    serialized string at 32,000 characters.
 *
 * @param result - The result object to serialize and constrain
 * @returns A JSON string of at most MAX_REPORT_SIZE characters
 *
 * Requirements: 9.2, 9.4
 */
export function enforceReportSizeConstraint(result: unknown): string {
  let serialized = JSON.stringify(result);

  if (serialized.length <= MAX_REPORT_SIZE) {
    return serialized;
  }

  // Check if it's a DiagnosticReport with raw_evidence
  if (
    result &&
    typeof result === "object" &&
    "raw_evidence" in result &&
    "summary" in result &&
    Array.isArray((result as Record<string, unknown>).raw_evidence)
  ) {
    const report = { ...(result as Record<string, unknown>) };
    const originalCount = (report.raw_evidence as unknown[]).length;

    // Truncate raw_evidence oldest-first (remove from beginning of array)
    let evidence = [...(report.raw_evidence as unknown[])];
    while (evidence.length > 0) {
      evidence = evidence.slice(1); // Remove oldest entry
      report.raw_evidence = evidence;
      report.truncation_notice = `Report truncated: raw_evidence reduced from ${originalCount} to ${evidence.length} entries to meet 32,000 character limit`;
      serialized = JSON.stringify(report);
      if (serialized.length <= MAX_REPORT_SIZE) {
        return serialized;
      }
    }
  }

  // Final fallback: hard truncate the serialized string
  return serialized.slice(0, MAX_REPORT_SIZE);
}

// ─── Response Types ─────────────────────────────────────────────────────────

/**
 * Represents a protocol-level JSON-RPC error (e.g., -32602 for invalid tool name).
 * Distinct from application-level errors which are returned as CallToolResult with isError=true.
 */
export interface ToolsCallError {
  jsonRpcError: JsonRpcError;
}

/**
 * Discriminated union for tools/call adapter responses.
 * - CallToolResult: Successful routing to business logic (may still have isError=true for app errors)
 * - ToolsCallError: Protocol-level error (invalid tool name) that should become a JSON-RPC error response
 */
export type ToolsCallResponse = CallToolResult | ToolsCallError;

/**
 * Type guard to distinguish protocol errors from tool results.
 * Protocol errors should be returned as JSON-RPC error responses (not as CallToolResult).
 */
export function isToolsCallError(result: ToolsCallResponse): result is ToolsCallError {
  return "jsonRpcError" in result;
}

// ─── Adapter Implementation ─────────────────────────────────────────────────

/**
 * Handles a tools/call request by validating the tool name, mapping params
 * to the existing invocation interface, and transforming the response.
 *
 * @param params - MCP tools/call params containing tool name and arguments
 * @param sessionId - Session identifier from Mcp-Session-Id header
 * @param callerIdentity - IAM identity ARN of the invoking role (for authorization)
 * @returns Either a CallToolResult (success/app-error) or a ToolsCallError (protocol error)
 */
export async function handleToolsCall(
  params: ToolsCallParams,
  sessionId: string,
  callerIdentity: string
): Promise<ToolsCallResponse> {
  // Step 1: Validate tool name exists in Action_Registry
  if (!isValidAction(params.name)) {
    return {
      jsonRpcError: {
        code: JSON_RPC_ERROR_CODES.INVALID_PARAMS,
        message: `Invalid params: tool '${params.name}' not found`,
      },
    };
  }

  // Step 2: Map MCP params to DevOpsAgentInvocation
  // The sessionId (from Mcp-Session-Id header) is wired into invocation.session_id.
  // processInvocation uses session_id to call generateIdempotencyToken() for start_capture
  // actions, ensuring the same MCP session always produces the same idempotency token.
  // (Requirement 4.7: Capture_Idempotency_Token determinism from Mcp-Session-Id)
  const invocation: DevOpsAgentInvocation = {
    action_name: params.name,
    parameters: params.arguments ?? {},
    session_id: sessionId,
  };

  // Step 3: Call existing processInvocation unchanged
  const response = await processInvocation(invocation, callerIdentity);

  // Step 4: Transform DevOpsAgentResponse → CallToolResult
  if (response.status === "success") {
    return {
      content: [{ type: "text", text: enforceReportSizeConstraint(response.result) }],
      isError: false,
    };
  }

  // Application-level error (validation, auth, rate limit, timeout, agent error)
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify({
          code: response.error?.code,
          message: response.error?.message,
        }),
      },
    ],
    isError: true,
  };
}
