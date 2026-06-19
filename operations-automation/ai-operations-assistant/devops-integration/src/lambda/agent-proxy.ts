/**
 * Network Agent proxy for the DevOps Agent Tool Interface.
 *
 * Invokes the GOAT Network Agent via BedrockAgentRuntime, handling:
 * - 60-second timeout enforcement
 * - Error response sanitization (strips ARNs, internal IPs, stack traces, hostnames)
 * - Stream response collection and parsing
 *
 * Requirements: 1.7
 */

import {
  BedrockAgentCoreClient,
  InvokeAgentRuntimeCommand,
} from "@aws-sdk/client-bedrock-agentcore";
import { randomUUID } from "crypto";
import {
  createTimeoutError,
  createNetworkAgentError,
  ErrorDescription,
} from "../types/errors";

/** Default timeout for Network Agent invocations in milliseconds */
export const AGENT_TIMEOUT_MS = 60_000;

/**
 * Configuration for the Network Agent proxy.
 */
export interface AgentProxyConfig {
  /** Bedrock AgentCore Runtime ARN (defaults to NETWORK_AGENT_ARN env var, with fallback to constructing from NETWORK_AGENT_ID) */
  agentRuntimeArn?: string;
  /** AWS region (defaults to AWS_REGION env var) */
  region?: string;
  /** Timeout in milliseconds (defaults to 60000) */
  timeoutMs?: number;
}

/**
 * Result of a successful Network Agent invocation.
 */
export interface AgentProxyResult {
  /** Parsed text output from the Network Agent */
  output: string;
  /** Session ID used for this invocation */
  sessionId: string;
}

/**
 * Sanitizes an error message by stripping internal system details.
 *
 * Removes:
 * - AWS ARN patterns (arn:aws:...)
 * - Internal IP addresses (10.x.x.x, 172.16-31.x.x, 192.168.x.x)
 * - Stack traces (lines starting with "at " or containing file paths)
 * - Internal hostnames (*.internal, *.local, ip-*.ec2.internal)
 *
 * @param message - The raw error message to sanitize
 * @returns A sanitized string with internal details removed
 */
export function sanitizeErrorMessage(message: string): string {
  let sanitized = message;

  // Strip AWS ARN patterns (arn:aws:service:region:account:resource...)
  sanitized = sanitized.replace(
    /arn:aws[a-zA-Z-]*:[a-zA-Z0-9-]+:[a-zA-Z0-9-]*:\d{0,12}:[^\s,)}\]"']*/g,
    "[REDACTED_ARN]"
  );

  // Strip internal IP addresses:
  // 10.0.0.0 – 10.255.255.255
  sanitized = sanitized.replace(/\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/g, "[REDACTED_IP]");
  // 172.16.0.0 – 172.31.255.255
  sanitized = sanitized.replace(
    /\b172\.(1[6-9]|2[0-9]|3[0-1])\.\d{1,3}\.\d{1,3}\b/g,
    "[REDACTED_IP]"
  );
  // 192.168.0.0 – 192.168.255.255
  sanitized = sanitized.replace(/\b192\.168\.\d{1,3}\.\d{1,3}\b/g, "[REDACTED_IP]");

  // Strip internal hostnames (*.internal, *.local, ip-*.ec2.internal)
  sanitized = sanitized.replace(
    /\bip-[\d-]+\.[\w.-]*\.ec2\.internal\b/g,
    "[REDACTED_HOSTNAME]"
  );
  sanitized = sanitized.replace(
    /\b[a-zA-Z0-9][\w.-]*\.(internal|local)\b/g,
    "[REDACTED_HOSTNAME]"
  );

  // Strip stack trace lines (lines starting with "at " or containing file paths)
  sanitized = sanitized.replace(
    /^\s*at\s+.+$/gm,
    ""
  );
  // Strip lines that look like file paths (e.g., /var/task/..., C:\..., ./src/...)
  sanitized = sanitized.replace(
    /^\s*(\/[\w./\\-]+|[A-Z]:\\[\w.\\/-]+|\.\/([\w./\\-]+))\s*$/gm,
    ""
  );

  // Clean up resulting empty lines from stack trace removal
  sanitized = sanitized.replace(/\n{3,}/g, "\n").trim();

  return sanitized;
}

/**
 * Creates a BedrockAgentCoreClient instance.
 * Extracted for testability — can be overridden in tests.
 */
export function createAgentCoreClient(
  region?: string
): BedrockAgentCoreClient {
  return new BedrockAgentCoreClient({
    region: region ?? process.env.AWS_REGION,
  });
}

/**
 * Invokes the GOAT Network Agent via Bedrock AgentCore InvokeAgentRuntime.
 *
 * This function:
 * 1. Sends the action request as a JSON payload to the Network Agent runtime
 * 2. Enforces a 60-second timeout using AbortController
 * 3. Reads the response body and returns the text output on success
 * 4. Sanitizes error details (ARNs, IPs, stack traces, hostnames) on failure
 *
 * The payload format matches what the Network Agent expects:
 *   {"action": "action_name", "params": {...parameters}}
 *
 * @param actionName - The Network Agent action being invoked (e.g., "start_capture")
 * @param parameters - The action-specific parameters
 * @param config - Optional configuration overrides
 * @returns The agent's text output on success, or an ErrorDescription on failure
 */
export async function invokeNetworkAgent(
  actionName: string,
  parameters: Record<string, unknown>,
  config?: AgentProxyConfig
): Promise<AgentProxyResult | ErrorDescription> {
  const region = config?.region ?? process.env.AWS_REGION_OVERRIDE ?? process.env.AWS_REGION;
  const timeoutMs = config?.timeoutMs ?? AGENT_TIMEOUT_MS;

  // Resolve the AgentCore runtime ARN
  // Priority: config.agentRuntimeArn → NETWORK_AGENT_ARN env var → construct from NETWORK_AGENT_ID
  let agentRuntimeArn = config?.agentRuntimeArn ?? process.env.NETWORK_AGENT_ARN;
  if (!agentRuntimeArn) {
    const agentId = process.env.NETWORK_AGENT_ID;
    const account = process.env.AWS_ACCOUNT_ID;
    if (agentId && region) {
      agentRuntimeArn = `arn:aws:bedrock-agentcore:${region}:${account ?? ""}:runtime/${agentId}`;
    }
  }

  if (!agentRuntimeArn) {
    console.error("Network Agent ARN not configured: NETWORK_AGENT_ARN or NETWORK_AGENT_ID must be set");
    return createNetworkAgentError(actionName);
  }

  const client = createAgentCoreClient(region);

  // Build the payload matching what the Network Agent expects
  const payload = JSON.stringify({
    action: actionName,
    params: parameters,
  });

  const abortController = new AbortController();
  const timeoutId = setTimeout(() => abortController.abort(), timeoutMs);

  try {
    const command = new InvokeAgentRuntimeCommand({
      agentRuntimeArn,
      payload: new TextEncoder().encode(payload),
    });

    const response = await client.send(command, {
      abortSignal: abortController.signal,
    });

    // Read the response body
    let output = "";
    const responseBody = response.response;
    if (responseBody) {
      if (responseBody instanceof Uint8Array) {
        output = new TextDecoder().decode(responseBody);
      } else if (Buffer.isBuffer(responseBody)) {
        output = responseBody.toString("utf-8");
      } else if (typeof responseBody === "string") {
        output = responseBody;
      } else if (typeof responseBody === "object" && "transformToByteArray" in responseBody) {
        // SDK v3 streaming body — use transformToByteArray
        const bytes = await (responseBody as any).transformToByteArray();
        output = new TextDecoder().decode(bytes);
      } else if (typeof responseBody === "object" && "transformToString" in responseBody) {
        output = await (responseBody as any).transformToString();
      } else if (typeof responseBody === "object" && Symbol.asyncIterator in responseBody) {
        // Async iterable stream
        const chunks: Uint8Array[] = [];
        for await (const chunk of responseBody as AsyncIterable<Uint8Array>) {
          chunks.push(chunk);
        }
        const totalLength = chunks.reduce((sum, c) => sum + c.length, 0);
        const combined = new Uint8Array(totalLength);
        let offset = 0;
        for (const chunk of chunks) {
          combined.set(chunk, offset);
          offset += chunk.length;
        }
        output = new TextDecoder().decode(combined);
      } else {
        // Last resort: stringify it
        output = JSON.stringify(responseBody);
      }
    }

    if (!output) {
      return createNetworkAgentError(actionName);
    }

    return {
      output,
      sessionId: randomUUID(),
    };
  } catch (error: unknown) {
    // Check if this was an abort/timeout
    if (isTimeoutError(error)) {
      return createTimeoutError(actionName);
    }

    // Log the actual error for debugging
    console.error(`Network Agent invocation failed for action "${actionName}":`, error);

    // For any other error, return a generic Network Agent error
    return createNetworkAgentError(actionName);
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Determines whether an error is a timeout/abort error.
 */
function isTimeoutError(error: unknown): boolean {
  if (error instanceof Error) {
    // AbortController abort produces an AbortError
    if (error.name === "AbortError") return true;
    // Some SDK versions use "TimeoutError"
    if (error.name === "TimeoutError") return true;
    // Check for abort-related messages
    if (error.message?.includes("aborted") || error.message?.includes("abort")) {
      return true;
    }
  }
  return false;
}
