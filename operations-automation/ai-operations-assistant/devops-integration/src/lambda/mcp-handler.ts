/**
 * MCP JSON-RPC 2.0 Router — Lambda entry point for the GOAT Network Agent MCP server.
 *
 * Accepts JSON-RPC 2.0 messages over HTTP POST, validates structure, and routes
 * to the appropriate MCP method handler (initialize, tools/list, tools/call, ping,
 * notifications/initialized).
 *
 * Requirements: 1.1, 1.4, 1.5, 1.6, 1.7
 */

import type { APIGatewayProxyEvent, APIGatewayProxyResult } from "aws-lambda";
import type {
  JsonRpcRequest,
  JsonRpcResponse,
  JsonRpcError,
  McpMethod,
  CallToolResult,
  ToolsCallParams,
} from "../types/mcp";
import { JSON_RPC_ERROR_CODES } from "../types/mcp";
import { getOrCreateSessionId } from "./session-manager";
import { getToolDefinitions } from "./tool-definitions";
import { handleToolsCall as adapterHandleToolsCall, isToolsCallError } from "./tools-call-adapter";

// ─── Supported MCP Methods ──────────────────────────────────────────────────

const SUPPORTED_METHODS: Set<McpMethod> = new Set([
  "initialize",
  "tools/list",
  "tools/call",
  "ping",
  "notifications/initialized",
]);

// ─── Response Helpers ───────────────────────────────────────────────────────

/**
 * Creates a JSON-RPC 2.0 success response.
 */
function createJsonRpcResponse(id: string | number | null, result: unknown): JsonRpcResponse {
  return {
    jsonrpc: "2.0",
    id,
    result,
  };
}

/**
 * Creates a JSON-RPC 2.0 error response.
 */
function createJsonRpcErrorResponse(
  id: string | number | null,
  error: JsonRpcError
): JsonRpcResponse {
  return {
    jsonrpc: "2.0",
    id,
    error,
  };
}

/**
 * Builds an HTTP response with standard headers.
 */
function buildHttpResponse(
  statusCode: number,
  body: JsonRpcResponse | null,
  sessionId?: string
): APIGatewayProxyResult {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  if (sessionId) {
    headers["Mcp-Session-Id"] = sessionId;
  }

  // For HTTP 204 (notifications), return empty body
  if (statusCode === 204) {
    return {
      statusCode,
      headers,
      body: "",
    };
  }

  return {
    statusCode,
    headers,
    body: body ? JSON.stringify(body) : "",
  };
}

// ─── Method Handlers (Stubs) ────────────────────────────────────────────────

/**
 * Handles the "initialize" method.
 * Returns protocol version, server info, and capabilities.
 * (Stub — full implementation in task 2.1)
 */
function handleInitialize(_params?: Record<string, unknown>): unknown {
  return {
    protocolVersion: "2024-11-05",
    serverInfo: {
      name: "goat-network-agent",
      version: "2.0.0",
    },
    capabilities: {
      tools: {
        listChanged: false,
      },
    },
  };
}

/**
 * Handles the "tools/list" method.
 * Returns available tool definitions from the ACTION_SCHEMAS registry.
 * Requirements: 1.2, 3.1, 3.2
 */
function handleToolsList(_params?: Record<string, unknown>): unknown {
  return {
    tools: getToolDefinitions(),
  };
}

/**
 * Handles the "tools/call" method.
 * Routes to the tools-call-adapter which validates the tool name,
 * maps params to processInvocation, and invokes the Network Agent.
 */
async function handleToolsCallWrapper(
  params: Record<string, unknown> | undefined,
  sessionId: string,
  callerIdentity: string
): Promise<{ result?: unknown; error?: JsonRpcError }> {
  const toolsCallParams: ToolsCallParams = {
    name: (params?.name as string) ?? "",
    arguments: params?.arguments as Record<string, unknown> | undefined,
  };

  const response = await adapterHandleToolsCall(toolsCallParams, sessionId, callerIdentity);

  if (isToolsCallError(response)) {
    return { error: response.jsonRpcError };
  }

  return { result: response };
}

/**
 * Handles the "ping" method.
 * Returns an empty result object.
 */
function handlePing(): unknown {
  return {};
}

// ─── Lambda Handler ─────────────────────────────────────────────────────────

/**
 * AWS Lambda handler for MCP JSON-RPC 2.0 messages.
 *
 * Routing logic:
 * 1. Parse incoming JSON body (return -32700 on failure)
 * 2. Validate JSON-RPC 2.0 structure (return -32600 on failure)
 * 3. Route to method handler
 * 4. If notification (no `id`), return HTTP 204 with no body
 * 5. If request (with `id`), return JSON-RPC response with matching `id`
 *
 * @param event - API Gateway proxy event
 * @returns API Gateway proxy result
 */
export async function handler(
  event: APIGatewayProxyEvent
): Promise<APIGatewayProxyResult> {
  // Step 1: Parse incoming JSON body
  let parsed: unknown;
  try {
    if (!event.body) {
      throw new Error("Empty request body");
    }
    parsed = JSON.parse(event.body);
  } catch (err) {
    // For parse errors, resolve session ID with a fallback method
    const sessionId = getOrCreateSessionId(event.headers as Record<string, string | undefined>, "unknown");
    const errorResponse = createJsonRpcErrorResponse(null, {
      code: JSON_RPC_ERROR_CODES.PARSE_ERROR,
      message: `Parse error: ${err instanceof Error ? err.message : "Invalid JSON"}`,
    });
    return buildHttpResponse(200, errorResponse, sessionId);
  }

  const request = parsed as Record<string, unknown>;

  // Step 2: Validate JSON-RPC 2.0 structure
  if (request.jsonrpc !== "2.0") {
    const method = typeof request.method === "string" ? request.method : "unknown";
    const sessionId = getOrCreateSessionId(event.headers as Record<string, string | undefined>, method);
    const errorResponse = createJsonRpcErrorResponse(
      (request.id as string | number | null) ?? null,
      {
        code: JSON_RPC_ERROR_CODES.INVALID_REQUEST,
        message: 'Invalid Request: missing or invalid "jsonrpc" field (must be "2.0")',
      }
    );
    return buildHttpResponse(200, errorResponse, sessionId);
  }

  if (typeof request.method !== "string" || !request.method) {
    const sessionId = getOrCreateSessionId(event.headers as Record<string, string | undefined>, "unknown");
    const errorResponse = createJsonRpcErrorResponse(
      (request.id as string | number | null) ?? null,
      {
        code: JSON_RPC_ERROR_CODES.INVALID_REQUEST,
        message: 'Invalid Request: missing or invalid "method" field',
      }
    );
    return buildHttpResponse(200, errorResponse, sessionId);
  }

  const method = request.method as string;
  const id = request.id as string | number | null | undefined;
  const params = request.params as Record<string, unknown> | undefined;

  // Resolve session ID using the session manager (generates new ID for initialize if absent)
  const sessionId = getOrCreateSessionId(event.headers as Record<string, string | undefined>, method);

  // Step 3: Check if method is supported
  if (!SUPPORTED_METHODS.has(method as McpMethod)) {
    // If this is a notification (no id), return 204 even for unsupported methods
    if (id === undefined) {
      return buildHttpResponse(204, null, sessionId);
    }
    const errorResponse = createJsonRpcErrorResponse(id ?? null, {
      code: JSON_RPC_ERROR_CODES.METHOD_NOT_FOUND,
      message: `Method not found: ${method}`,
    });
    return buildHttpResponse(200, errorResponse, sessionId);
  }

  // Step 4: Handle notifications (no `id` field) — return HTTP 204
  if (id === undefined) {
    // notifications/initialized is the primary notification; acknowledge silently
    return buildHttpResponse(204, null, sessionId);
  }

  // Step 5: Route to method handler and return JSON-RPC response
  try {
    let result: unknown;

    switch (method as McpMethod) {
      case "initialize":
        result = handleInitialize(params);
        break;
      case "tools/list":
        result = handleToolsList(params);
        break;
      case "tools/call": {
        const callerIdentity = event.requestContext?.identity?.userArn ?? "";
        const toolsCallResult = await handleToolsCallWrapper(params, sessionId, callerIdentity);
        if (toolsCallResult.error) {
          const errorResponse = createJsonRpcErrorResponse(id ?? null, toolsCallResult.error);
          return buildHttpResponse(200, errorResponse, sessionId);
        }
        result = toolsCallResult.result;
        break;
      }
      case "ping":
        result = handlePing();
        break;
      default:
        // Should not reach here due to SUPPORTED_METHODS check above
        const errorResponse = createJsonRpcErrorResponse(id ?? null, {
          code: JSON_RPC_ERROR_CODES.METHOD_NOT_FOUND,
          message: `Method not found: ${method}`,
        });
        return buildHttpResponse(200, errorResponse, sessionId);
    }

    const response = createJsonRpcResponse(id ?? null, result);
    return buildHttpResponse(200, response, sessionId);
  } catch (err) {
    // Internal error during method handling
    console.error("Internal error in MCP handler:", err);
    const errorResponse = createJsonRpcErrorResponse(id ?? null, {
      code: JSON_RPC_ERROR_CODES.INTERNAL_ERROR,
      message: "Internal error",
    });
    return buildHttpResponse(200, errorResponse, sessionId);
  }
}
