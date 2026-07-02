/**
 * Core TypeScript interfaces and types for the GOAT Network Agent ↔ DevOps Agent Integration.
 *
 * This module defines the primary request/response envelope, diagnostic report structure,
 * tool manifest, action definitions, and invocation audit records.
 *
 * Requirements: 1.2, 3.1, 3.4, 4.3
 */
export { StreamInfo, RootCause, TlsDetails, ConnectionDropDetails, EvidenceRef, DataSufficiencyWarning, } from "./report";
export { ErrorCode, ERROR_HTTP_STATUS, ErrorDescription, SchemaValidationErrorDetails, RateLimitErrorDetails, AuthorizationErrorDetails, RegionMismatchErrorDetails, createSchemaValidationError, createAuthorizationDeniedError, createRateLimitExceededError, createTimeoutError, createRegionMismatchError, createAuthFailedError, createNetworkAgentError, createInfraUnavailableError, } from "./errors";
import type { StreamInfo, RootCause, EvidenceRef, DataSufficiencyWarning } from "./report";
import type { ErrorDescription } from "./errors";
/**
 * Minimal JSON Schema type for action schema definitions.
 * Compatible with ajv validation library.
 */
export interface JSONSchema {
    type?: string | string[];
    properties?: Record<string, JSONSchema>;
    required?: string[];
    items?: JSONSchema;
    enum?: unknown[];
    minimum?: number;
    maximum?: number;
    minItems?: number;
    maxItems?: number;
    minLength?: number;
    maxLength?: number;
    pattern?: string;
    description?: string;
    default?: unknown;
    additionalProperties?: boolean | JSONSchema;
    oneOf?: JSONSchema[];
    anyOf?: JSONSchema[];
    allOf?: JSONSchema[];
    format?: string;
    $ref?: string;
    $schema?: string;
    title?: string;
    [key: string]: unknown;
}
/**
 * Input payload from DevOps Agent via API Gateway.
 * Represents a single action invocation request.
 */
export interface DevOpsAgentInvocation {
    /** One of 22 Network Agent actions + "full_diagnostic" */
    action_name: string;
    /** Action-specific parameters validated against the action's JSON Schema */
    parameters: Record<string, unknown>;
    /** DevOps Agent session identifier used for idempotency token generation */
    session_id: string;
}
/**
 * Output payload returned to DevOps Agent.
 * Contains a standardized response envelope with either a result or an error.
 */
export interface DevOpsAgentResponse {
    /** The action that was invoked */
    action_name: string;
    /** Whether the action completed successfully */
    status: "success" | "error";
    /** ISO 8601 timestamp of when the response was generated */
    timestamp: string;
    /** Result payload on success (DiagnosticReport or action-specific result) */
    result?: DiagnosticReport | ActionResult;
    /** Error description on failure */
    error?: ErrorDescription;
}
/**
 * Generic action result for non-diagnostic actions (e.g., list_enis, list_captures).
 */
export interface ActionResult {
    /** Action-specific data payload */
    data: Record<string, unknown>;
}
/**
 * Structured diagnostic report optimized for LLM reasoning.
 * Contains packet analysis findings, root causes, and remediation steps.
 *
 * Constraints:
 * - Total JSON ≤ 32,000 characters
 * - summary ≤ 500 chars
 * - affected_streams ≤ 50 entries
 * - root_cause_indicators ≤ 10 entries
 * - recommended_actions ≤ 10 entries
 * - raw_evidence ≤ 20 entries
 */
export interface DiagnosticReport {
    /** Natural language summary of findings (≤500 characters) */
    summary: string;
    /** List of affected TCP/UDP streams (≤50 entries) */
    affected_streams: StreamInfo[];
    /** Identified root cause indicators (≤10 entries) */
    root_cause_indicators: RootCause[];
    /** Ordered list of remediation steps (≤10 entries) */
    recommended_actions: string[];
    /** Supporting packet data references (≤20 entries) */
    raw_evidence: EvidenceRef[];
    /**
     * Overall confidence level:
     * - "high": ≥3 independent corroborating indicators
     * - "medium": 2 independent indicators
     * - "low": 1 indicator or inference from absence
     */
    confidence_level: "high" | "medium" | "low";
    /** Explanation of what packet analysis revealed beyond L3/L4 path analysis */
    comparison_with_reachability_analyzer: string;
    /** Data sufficiency warning when capture data is insufficient */
    data_sufficiency?: DataSufficiencyWarning;
    /** Present when raw_evidence was truncated due to 32K size limit */
    truncation_notice?: string;
}
/**
 * Parameters for the composite "full_diagnostic" action.
 * Orchestrates: start_capture → wait → stop_capture → transform_capture → diagnose_tcp_stream.
 *
 * Requirements: 4.3, 4.7
 */
export interface FullDiagnosticParams {
    /** Required ENI IDs to capture traffic from (1-5 ENIs) */
    eni_ids: string[];
    /** Capture duration in minutes (1-10, default 2) */
    duration_minutes?: number;
    /** Optional target host for filtering captured traffic */
    target_host?: string;
    /** Analysis focus area (default "general") */
    analysis_focus?: "tls" | "tcp_health" | "dns" | "general";
}
/**
 * Tool manifest describing available actions for DevOps Agent registration.
 * Conforms to the DevOps Agent external tool specification.
 */
export interface ToolManifest {
    /** Name of the tool (e.g., "goat-network-agent") */
    tool_name: string;
    /** Semantic version of the tool interface */
    version: string;
    /** L7 analysis capability statement */
    description: string;
    /** High-level capability categories (e.g., ["traffic_capture", "pcap_analysis", "tls_inspection"]) */
    capabilities: string[];
    /** List of all available actions with their schemas */
    actions: ActionDefinition[];
}
/**
 * Definition of a single action exposed through the tool interface.
 */
export interface ActionDefinition {
    /** Action name (e.g., "start_capture", "full_diagnostic") */
    name: string;
    /** Human-readable description of what the action does */
    description: string;
    /** JSON Schema for the action's input parameters */
    input_schema: JSONSchema;
    /** JSON Schema for the action's output payload */
    output_schema: JSONSchema;
    /** Category of the action */
    category: "capture" | "analysis" | "utility";
    /** Whether the action requires Capture_Authorization_Group membership */
    requires_authorization: boolean;
}
/**
 * Audit record for tool invocations stored in the GOATData DynamoDB table.
 * PK: DEVOPS_INVOCATION#{invocation_id}, SK: TIMESTAMP#{timestamp}
 */
export interface InvocationRecord {
    /** Unique UUID for this invocation */
    invocation_id: string;
    /** DevOps Agent session identifier */
    session_id: string;
    /** The action that was invoked */
    action_name: string;
    /** ISO 8601 timestamp of the invocation */
    timestamp: string;
    /** Whether the invocation succeeded or failed */
    status: "success" | "error";
    /** Duration of the invocation in milliseconds */
    duration_ms: number;
    /** Source of the invocation (always "devops_agent" for this integration) */
    source: "devops_agent";
    /** AWS region where the invocation was processed */
    region: string;
}
/**
 * Props for the reusable Agent Integration Template CDK construct.
 * Enables any GOAT sub-agent to connect to DevOps Agent with minimal boilerplate.
 */
export interface AgentIntegrationTemplateProps {
    /** Name of the agent being integrated */
    agentName: string;
    /** ARN of the agent's Bedrock runtime */
    agentRuntimeArn: string;
    /** List of action definitions the agent exposes */
    actions: ActionDefinition[];
    /** Optional authorization group name for protected actions */
    authorizationGroupName?: string;
    /**
     * Optional Lambda code asset to use instead of the inline stub.
     * When provided, the Lambda deploys this bundled code (with esbuild or tsc output)
     * instead of the inline JavaScript handler. Use this to deploy the real
     * mcp-handler.ts with processInvocation wired to the Network Agent.
     */
    lambdaCode?: unknown;
    /**
     * Handler function reference within the bundled code (e.g., "mcp-handler.handler").
     * Only used when lambdaCode is provided. Defaults to "index.handler" for inline code.
     */
    lambdaHandler?: string;
}
