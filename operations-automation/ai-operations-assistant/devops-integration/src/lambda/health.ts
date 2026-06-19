/**
 * Health Check Endpoint for the GOAT Network Agent ↔ DevOps Agent Tool Interface.
 *
 * Returns operational status of the integration, including component health
 * for the Network Agent, Integration Lambda, and Capture State Table.
 *
 * Requirements: 2.7
 */

import type { APIGatewayProxyEvent, APIGatewayProxyResult } from "aws-lambda";

// ─── Health Check Types ─────────────────────────────────────────────────────

/**
 * Component-level status for the Network Agent.
 */
export interface NetworkAgentHealth {
  status: "available" | "unavailable" | "unknown";
  agent_id?: string;
}

/**
 * Component-level status for the Integration Lambda itself.
 */
export interface IntegrationLambdaHealth {
  status: "available";
  version: string;
}

/**
 * Component-level status for the Capture State DynamoDB table.
 */
export interface CaptureStateTableHealth {
  status: "available" | "unavailable" | "unknown";
}

/**
 * Structured health check response returned by the health endpoint.
 */
export interface HealthCheckResponse {
  status: "healthy" | "degraded" | "unhealthy";
  timestamp: string;
  components: {
    network_agent: NetworkAgentHealth;
    integration_lambda: IntegrationLambdaHealth;
    capture_state_table: CaptureStateTableHealth;
  };
  region: string;
}

// ─── Constants ──────────────────────────────────────────────────────────────

/** Current version of the integration Lambda */
const INTEGRATION_VERSION = "1.0.0";

// ─── Helper Functions ───────────────────────────────────────────────────────

/**
 * Masks an agent ID for security, showing only the first 4 characters.
 * Returns undefined if the input is empty or undefined.
 */
export function maskAgentId(agentId: string | undefined): string | undefined {
  if (!agentId || agentId.length === 0) {
    return undefined;
  }
  if (agentId.length <= 4) {
    return agentId + "***";
  }
  return agentId.substring(0, 4) + "***";
}

/**
 * Checks the Network Agent configuration availability by verifying
 * that required environment variables are present.
 */
export function checkNetworkAgentHealth(): NetworkAgentHealth {
  const agentId = process.env.NETWORK_AGENT_ID;
  const agentAliasId = process.env.NETWORK_AGENT_ALIAS_ID;

  if (agentId && agentAliasId) {
    return {
      status: "available",
      agent_id: maskAgentId(agentId),
    };
  }

  if (agentId || agentAliasId) {
    // Partially configured — we can't be sure it's usable
    return {
      status: "unknown",
      agent_id: maskAgentId(agentId),
    };
  }

  // Neither env var is set
  return {
    status: "unavailable",
  };
}

/**
 * Checks the Capture State Table availability by verifying
 * that the CAPTURE_STATE_TABLE environment variable is present.
 */
export function checkCaptureStateTableHealth(): CaptureStateTableHealth {
  const tableName = process.env.CAPTURE_STATE_TABLE;

  if (tableName) {
    return { status: "available" };
  }

  return { status: "unknown" };
}

/**
 * Determines the overall system status from component statuses.
 *
 * - If all components are "available" → "healthy"
 * - If network_agent is "unavailable" → "unhealthy"
 * - If network_agent is "unknown" → "degraded"
 */
export function determineOverallStatus(
  networkAgent: NetworkAgentHealth,
  captureStateTable: CaptureStateTableHealth
): "healthy" | "degraded" | "unhealthy" {
  if (networkAgent.status === "unavailable") {
    return "unhealthy";
  }

  if (networkAgent.status === "unknown") {
    return "degraded";
  }

  if (captureStateTable.status === "unavailable") {
    return "degraded";
  }

  // network_agent is "available" and nothing else is "unavailable"
  return "healthy";
}

// ─── Health Check Core Logic ────────────────────────────────────────────────

/**
 * Performs the health check and returns a structured response.
 *
 * Checks:
 * - Network Agent runtime availability (via env var configuration)
 * - Integration Lambda status (always available if this code runs)
 * - Capture State Table availability (via env var)
 *
 * @returns Structured JSON health check response
 */
export async function healthCheck(): Promise<HealthCheckResponse> {
  const networkAgentHealth = checkNetworkAgentHealth();
  const captureStateTableHealth = checkCaptureStateTableHealth();

  const overallStatus = determineOverallStatus(networkAgentHealth, captureStateTableHealth);
  const region = process.env.AWS_REGION ?? process.env.AWS_DEFAULT_REGION ?? "unknown";

  return {
    status: overallStatus,
    timestamp: new Date().toISOString(),
    components: {
      network_agent: networkAgentHealth,
      integration_lambda: {
        status: "available",
        version: INTEGRATION_VERSION,
      },
      capture_state_table: captureStateTableHealth,
    },
    region,
  };
}

// ─── Lambda Handler ─────────────────────────────────────────────────────────

/**
 * Lambda handler wrapper for the health check endpoint.
 *
 * Invokes healthCheck() and returns the result as an HTTP 200 JSON response.
 * Always returns 200 regardless of component health — the `status` field
 * in the response body indicates the actual health state.
 *
 * @param event - API Gateway proxy event (unused, included for interface compliance)
 * @returns API Gateway proxy result with health check JSON
 */
export async function healthHandler(
  event: APIGatewayProxyEvent
): Promise<APIGatewayProxyResult> {
  try {
    const healthResponse = await healthCheck();

    return {
      statusCode: 200,
      headers: {
        "Content-Type": "application/json",
        "X-Request-Source": "devops_agent",
      },
      body: JSON.stringify(healthResponse),
    };
  } catch (error: unknown) {
    console.error("Health check failed:", error);

    return {
      statusCode: 500,
      headers: {
        "Content-Type": "application/json",
        "X-Request-Source": "devops_agent",
      },
      body: JSON.stringify({
        status: "unhealthy",
        timestamp: new Date().toISOString(),
        components: {
          network_agent: { status: "unknown" },
          integration_lambda: { status: "available", version: INTEGRATION_VERSION },
          capture_state_table: { status: "unknown" },
        },
        region: process.env.AWS_REGION ?? process.env.AWS_DEFAULT_REGION ?? "unknown",
      }),
    };
  }
}
