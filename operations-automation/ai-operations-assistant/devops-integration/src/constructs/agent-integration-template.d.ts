/**
 * Agent Integration Template - Reusable CDK Construct
 *
 * A parameterized CDK construct that produces a complete DevOps Agent MCP integration
 * endpoint for any GOAT sub-agent. Provides API Gateway (POST / for MCP JSON-RPC,
 * GET /health for monitoring), Integration Lambda, IAM roles, auto-generated MCP tool
 * definitions, health check, and standardized JSON-RPC 2.0 response envelope.
 *
 * Requirements: 5.2, 5.3, 5.4, 5.6, 6.1, 6.2, 6.4, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7
 */
import * as apigateway from "aws-cdk-lib/aws-apigateway";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import { Construct } from "constructs";
import type { AgentIntegrationTemplateProps } from "../types";
/**
 * AgentIntegrationTemplate CDK Construct.
 *
 * Creates a complete DevOps Agent MCP integration endpoint for any GOAT sub-agent,
 * including API Gateway (single POST endpoint for MCP JSON-RPC messages + GET /health),
 * Integration Lambda, IAM roles, auto-generated MCP tool definitions, health check,
 * and standardized JSON-RPC 2.0 response envelope.
 *
 * Usage:
 * ```typescript
 * const integration = new AgentIntegrationTemplate(this, 'NetworkAgentIntegration', {
 *   agentName: 'network-agent',
 *   agentRuntimeArn: 'arn:aws:bedrock:us-east-1:123456789012:agent/AGENT_ID',
 *   actions: networkAgentActions,
 *   authorizationGroupName: 'NetworkCaptureAuthGroup',
 * });
 * ```
 */
export declare class AgentIntegrationTemplate extends Construct {
    /** The API Gateway HTTPS endpoint URL */
    readonly endpointUrl: string;
    /** The MCP endpoint URL (POST / for JSON-RPC messages) */
    readonly mcpEndpointUrl: string;
    /** The full health check URL (endpoint + /health) */
    readonly healthUrl: string;
    /** The ARN of the IAM role DevOps Agent should assume */
    readonly devOpsAgentRoleArn: string;
    /** The API Gateway REST API resource */
    readonly api: apigateway.RestApi;
    /** The Integration Lambda function */
    readonly integrationLambda: lambda.Function;
    /** The IAM role for DevOps Agent to assume */
    readonly devOpsAgentRole: iam.Role;
    /** The Capture State DynamoDB table */
    readonly captureStateTable: dynamodb.Table;
    constructor(scope: Construct, id: string, props: AgentIntegrationTemplateProps);
    /**
     * Extract the agent ID from the agent runtime ARN.
     * ARN format: arn:aws:bedrock:{region}:{account}:agent/{agentId}
     */
    private extractAgentId;
    /**
     * Generate MCP tool definitions from the provided action definitions.
     * Each tool definition conforms to the MCP Tool schema with name, description, and inputSchema.
     */
    private generateMcpToolDefinitions;
    /**
     * Generate a schemas map from action definitions for use in the Lambda environment.
     * Maps action_name → input_schema for lightweight parameter validation.
     */
    private generateSchemasMap;
    /**
     * Generate inline Lambda handler code that handles MCP JSON-RPC messages (POST /)
     * and health check (GET /health).
     *
     * MCP protocol handling:
     * - POST / → JSON-RPC 2.0 router: initialize, tools/list, tools/call, ping, notifications/initialized
     * - GET /health → health check (unchanged from previous implementation)
     *
     * Validation approach (inline, no external dependencies):
     * - Validates required fields from each action's input_schema
     * - Validates parameter types against schema property type definitions
     * - Returns consistent MCP error responses (CallToolResult with isError=true)
     */
    private generateLambdaCode;
}
