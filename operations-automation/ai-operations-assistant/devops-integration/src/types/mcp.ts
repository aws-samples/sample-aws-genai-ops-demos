/**
 * MCP (Model Context Protocol) and JSON-RPC 2.0 type definitions
 * for the GOAT Network Agent ↔ DevOps Agent integration.
 *
 * Defines the transport-layer interfaces used by the MCP server
 * to communicate with the DevOps Agent via JSON-RPC 2.0 messaging.
 *
 * Requirements: 1.1, 1.4, 1.5
 */

import type { JSONSchema } from "./index";

// ─── JSON-RPC 2.0 Error Codes ───────────────────────────────────────────────

/**
 * Standard JSON-RPC 2.0 error codes.
 * @see https://www.jsonrpc.org/specification#error_object
 */
export const JSON_RPC_ERROR_CODES = {
  /** Invalid JSON was received by the server */
  PARSE_ERROR: -32700,
  /** The JSON sent is not a valid Request object */
  INVALID_REQUEST: -32600,
  /** The method does not exist or is not available */
  METHOD_NOT_FOUND: -32601,
  /** Invalid method parameter(s) */
  INVALID_PARAMS: -32602,
  /** Internal JSON-RPC error */
  INTERNAL_ERROR: -32603,
} as const;

// ─── JSON-RPC 2.0 Interfaces ────────────────────────────────────────────────

/**
 * JSON-RPC 2.0 request message.
 * When `id` is absent, the message is a notification (no response expected).
 */
export interface JsonRpcRequest {
  jsonrpc: "2.0";
  /** Request identifier. Absent for notifications. */
  id?: string | number | null;
  /** The method to invoke */
  method: string;
  /** Method parameters */
  params?: Record<string, unknown>;
}

/**
 * JSON-RPC 2.0 response message.
 * Contains either a `result` or an `error`, never both.
 */
export interface JsonRpcResponse {
  jsonrpc: "2.0";
  /** Must match the `id` of the corresponding request */
  id: string | number | null;
  /** Result on success */
  result?: unknown;
  /** Error on failure */
  error?: JsonRpcError;
}

/**
 * JSON-RPC 2.0 error object returned in error responses.
 */
export interface JsonRpcError {
  /** A number indicating the error type */
  code: number;
  /** A short description of the error */
  message: string;
  /** Additional information about the error */
  data?: unknown;
}

// ─── MCP Protocol Types ─────────────────────────────────────────────────────

/**
 * Result payload for a tools/call response.
 * Contains an array of content blocks and an optional error flag.
 */
export interface CallToolResult {
  content: CallToolContent[];
  isError?: boolean;
}

/**
 * A single content block within a CallToolResult.
 * Currently only "text" type is supported.
 */
export interface CallToolContent {
  type: "text";
  text: string;
}

/**
 * MCP tool definition exposed via tools/list.
 * Maps an action from the Action_Registry to the MCP tool format.
 */
export interface McpToolDefinition {
  name: string;
  description: string;
  inputSchema: JSONSchema;
}

/**
 * Parameters for a tools/call JSON-RPC request.
 */
export interface ToolsCallParams {
  name: string;
  arguments?: Record<string, unknown>;
}

// ─── MCP Method Types ───────────────────────────────────────────────────────

/**
 * Supported MCP methods for the JSON-RPC router.
 */
export type McpMethod =
  | "initialize"
  | "tools/list"
  | "tools/call"
  | "ping"
  | "notifications/initialized";
