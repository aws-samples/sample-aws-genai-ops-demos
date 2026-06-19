/**
 * MCP Session Manager
 *
 * Manages the Mcp-Session-Id header lifecycle for session continuity
 * across JSON-RPC requests. Handles session ID extraction from incoming
 * headers and generation of new session IDs when absent.
 *
 * Requirements: 2.4, 2.5
 */

import { v4 as uuidv4 } from "uuid";

/**
 * Header name for the MCP session identifier.
 * HTTP headers are case-insensitive, so lookup checks both forms.
 */
const MCP_SESSION_ID_HEADER = "mcp-session-id";

/**
 * Retrieves the existing session ID from request headers, or generates
 * a new UUID v4 session ID if none is present.
 *
 * Rules:
 * - If `Mcp-Session-Id` header is present (case-insensitive), return that value.
 * - If absent and method is "initialize", generate a new UUID v4.
 * - If absent for other methods, generate a new UUID v4 (graceful handling).
 *
 * @param requestHeaders - Incoming HTTP request headers (keys may be any case)
 * @param method - The JSON-RPC method name being invoked
 * @returns The session ID string to use for this request/response cycle
 */
export function getOrCreateSessionId(
  requestHeaders: Record<string, string | undefined>,
  method: string
): string {
  // HTTP headers are case-insensitive; check both original keys and lowercased
  const existingSessionId = findHeaderValue(requestHeaders, MCP_SESSION_ID_HEADER);

  if (existingSessionId) {
    return existingSessionId;
  }

  // No session ID present — generate a new one regardless of method
  // (graceful handling for both "initialize" and other methods)
  return uuidv4();
}

/**
 * Performs a case-insensitive header lookup.
 *
 * @param headers - The request headers object
 * @param targetHeader - The header name to find (lowercase)
 * @returns The header value if found and non-empty, otherwise undefined
 */
function findHeaderValue(
  headers: Record<string, string | undefined>,
  targetHeader: string
): string | undefined {
  for (const key of Object.keys(headers)) {
    if (key.toLowerCase() === targetHeader) {
      const value = headers[key];
      if (value !== undefined && value !== "") {
        return value;
      }
    }
  }
  return undefined;
}
