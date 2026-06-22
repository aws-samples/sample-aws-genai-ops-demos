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

import * as cdk from "aws-cdk-lib";
import * as apigateway from "aws-cdk-lib/aws-apigateway";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import { Construct } from "constructs";
import type { ActionDefinition, AgentIntegrationTemplateProps } from "../types";

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
export class AgentIntegrationTemplate extends Construct {
  /** The API Gateway HTTPS endpoint URL */
  public readonly endpointUrl: string;

  /** The MCP endpoint URL (POST / for JSON-RPC messages) */
  public readonly mcpEndpointUrl: string;

  /** The full health check URL (endpoint + /health) */
  public readonly healthUrl: string;

  /** The ARN of the IAM role DevOps Agent should assume */
  public readonly devOpsAgentRoleArn: string;

  /** The API Gateway REST API resource */
  public readonly api: apigateway.RestApi;

  /** The Integration Lambda function */
  public readonly integrationLambda: lambda.Function;

  /** The IAM role for DevOps Agent to assume */
  public readonly devOpsAgentRole: iam.Role;

  /** The Capture State DynamoDB table */
  public readonly captureStateTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props: AgentIntegrationTemplateProps) {
    super(scope, id);

    const { agentName, agentRuntimeArn, actions, authorizationGroupName } = props;
    const lambdaCode = props.lambdaCode as lambda.Code | undefined;
    const lambdaHandler = props.lambdaHandler ?? "index.handler";

    // ─── DynamoDB Table for Capture State ─────────────────────────────────

    this.captureStateTable = new dynamodb.Table(this, "CaptureStateTable", {
      partitionKey: { name: "PK", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "SK", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      timeToLiveAttribute: "ttl",
    });

    // ─── Integration Lambda ───────────────────────────────────────────────

    const lambdaRole = new iam.Role(this, "IntegrationLambdaRole", {
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "service-role/AWSLambdaBasicExecutionRole"
        ),
      ],
    });

    // Allow Lambda to invoke the agent runtime
    lambdaRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["bedrock-agent-runtime:InvokeAgentRuntime"],
        resources: [agentRuntimeArn],
      })
    );

    // Allow Lambda to read/write to the capture state table
    this.captureStateTable.grantReadWriteData(lambdaRole);

    this.integrationLambda = new lambda.Function(this, "IntegrationLambda", {
      runtime: lambda.Runtime.NODEJS_20_X,
      handler: lambdaHandler,
      code: lambdaCode ?? lambda.Code.fromInline(
        this.generateLambdaCode(agentName, actions)
      ),
      role: lambdaRole,
      timeout: cdk.Duration.seconds(60),
      memorySize: 256,
      environment: {
        NETWORK_AGENT_ID: this.extractAgentId(agentRuntimeArn),
        NETWORK_AGENT_ARN: agentRuntimeArn,
        NETWORK_AGENT_ALIAS_ID: "TSTALIASID",
        AWS_REGION_OVERRIDE: cdk.Stack.of(this).region,
        CAPTURE_STATE_TABLE: this.captureStateTable.tableName,
        AUTHORIZED_ROLE_ARNS: "*",
        CAPTURE_AUTHORIZATION_GROUP: authorizationGroupName ?? "",
        AGENT_NAME: agentName,
        // NOTE: TOOL_DEFINITIONS and ACTION_SCHEMAS are embedded directly in
        // the inline Lambda code to avoid the 4KB env var limit. They are loaded
        // as module-level constants in the generated handler code.
      },
    });

    // ─── API Gateway ──────────────────────────────────────────────────────

    this.api = new apigateway.RestApi(this, "IntegrationApi", {
      restApiName: `${agentName}-mcp-integration`,
      description: `MCP server endpoint for ${agentName} DevOps Agent integration`,
      deployOptions: {
        stageName: "prod",
      },
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
      },
    });

    // POST / - MCP JSON-RPC messages endpoint (root path)
    this.api.root.addMethod(
      "POST",
      new apigateway.LambdaIntegration(this.integrationLambda),
      {
        authorizationType: apigateway.AuthorizationType.IAM,
      }
    );

    // GET /health - returns health check response (preserved for monitoring)
    const healthResource = this.api.root.addResource("health");
    healthResource.addMethod(
      "GET",
      new apigateway.LambdaIntegration(this.integrationLambda),
      {
        authorizationType: apigateway.AuthorizationType.IAM,
      }
    );

    // ─── IAM Role for DevOps Agent ────────────────────────────────────────

    this.devOpsAgentRole = new iam.Role(this, "DevOpsAgentRole", {
      assumedBy: new iam.ServicePrincipal("bedrock.amazonaws.com"),
      description: `Role for DevOps Agent to invoke ${agentName} MCP endpoint via SigV4`,
    });

    // Add trust for aidevops.amazonaws.com with confused deputy protection
    // Required per AWS docs: https://docs.aws.amazon.com/devopsagent/latest/userguide/configuring-integrations-and-knowledge-connecting-mcp-servers.html
    this.devOpsAgentRole.assumeRolePolicy!.addStatements(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        principals: [new iam.ServicePrincipal("aidevops.amazonaws.com")],
        actions: ["sts:AssumeRole"],
        conditions: {
          StringEquals: {
            "aws:SourceAccount": cdk.Stack.of(this).account,
          },
        },
      })
    );

    // Allow DevOps Agent to invoke the agent runtime
    this.devOpsAgentRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["bedrock-agent-runtime:InvokeAgentRuntime"],
        resources: [agentRuntimeArn],
      })
    );

    // Allow DevOps Agent to invoke the MCP API Gateway endpoint
    this.devOpsAgentRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["execute-api:Invoke"],
        resources: [
          this.api.arnForExecuteApi("POST", "/", "prod"),
          this.api.arnForExecuteApi("GET", "/health", "prod"),
        ],
      })
    );

    // ─── Construct Outputs ────────────────────────────────────────────────

    this.endpointUrl = this.api.url;
    this.mcpEndpointUrl = this.api.url;
    this.healthUrl = `${this.api.url}health`;
    this.devOpsAgentRoleArn = this.devOpsAgentRole.roleArn;

    // ─── CfnOutputs ──────────────────────────────────────────────────────

    new cdk.CfnOutput(this, "McpEndpointUrl", {
      value: this.mcpEndpointUrl,
      description: "MCP JSON-RPC endpoint URL (POST)",
    });

    new cdk.CfnOutput(this, "RegisterCommand", {
      value: `aws devops-agent register-service --service mcpserversigv4 --name ${agentName}-${cdk.Stack.of(this).region} --endpoint ${this.mcpEndpointUrl} --authorizationConfig '{"region":"${cdk.Stack.of(this).region}","service":"execute-api","mcpRoleArn":"${this.devOpsAgentRole.roleArn}"}'`,
      description: "Command to register the MCP server with DevOps Agent",
    });

    new cdk.CfnOutput(this, "HealthCheckUrl", {
      value: this.healthUrl,
      description: "Health check endpoint URL (GET)",
    });

    new cdk.CfnOutput(this, "DevOpsAgentRoleArn", {
      value: this.devOpsAgentRoleArn,
      description: "IAM role ARN for DevOps Agent SigV4 authentication",
    });
  }

  // ─── Private Helpers ──────────────────────────────────────────────────────

  /**
   * Extract the agent ID from the agent runtime ARN.
   * ARN format: arn:aws:bedrock:{region}:{account}:agent/{agentId}
   */
  private extractAgentId(arn: string): string {
    const parts = arn.split("/");
    return parts[parts.length - 1] ?? "unknown";
  }

  /**
   * Generate MCP tool definitions from the provided action definitions.
   * Each tool definition conforms to the MCP Tool schema with name, description, and inputSchema.
   */
  private generateMcpToolDefinitions(
    agentName: string,
    actions: ActionDefinition[]
  ): object[] {
    return actions.map((action) => ({
      name: action.name,
      description: action.description,
      inputSchema: action.input_schema,
    }));
  }

  /**
   * Generate a schemas map from action definitions for use in the Lambda environment.
   * Maps action_name → input_schema for lightweight parameter validation.
   */
  private generateSchemasMap(
    actions: ActionDefinition[]
  ): Record<string, object> {
    const schemas: Record<string, object> = {};
    for (const action of actions) {
      schemas[action.name] = action.input_schema;
    }
    return schemas;
  }

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
  private generateLambdaCode(
    agentName: string,
    actions: ActionDefinition[]
  ): string {
    // Embed tool definitions and action schemas directly in the code
    // to avoid the 4KB Lambda environment variable limit
    const toolDefsJson = JSON.stringify(this.generateMcpToolDefinitions(agentName, actions));
    const actionSchemasJson = JSON.stringify(this.generateSchemasMap(actions));

    return `
'use strict';

const { randomUUID } = require('crypto');

const toolDefinitions = ${toolDefsJson};
const actionSchemas = ${actionSchemasJson};
const agentName = process.env.AGENT_NAME || '${agentName}';

/**
 * Validates parameters against an action's input schema.
 * Performs lightweight validation: required fields and basic type checks.
 * Returns null if valid, or an error object if validation fails.
 */
function validateParameters(actionName, parameters) {
  const schema = actionSchemas[actionName];
  if (!schema) {
    return null; // No schema defined, skip validation
  }

  const params = parameters || {};
  const failingParameters = [];
  const expectedConstraints = {};

  // Check required fields
  const requiredFields = schema.required || [];
  for (const field of requiredFields) {
    if (params[field] === undefined || params[field] === null) {
      failingParameters.push(field);
      expectedConstraints[field] = 'Required parameter "' + field + '" is missing';
    }
  }

  // Check type constraints for provided parameters
  const properties = schema.properties || {};
  for (const [key, value] of Object.entries(params)) {
    const propSchema = properties[key];
    if (!propSchema) continue;

    const expectedType = propSchema.type;
    if (!expectedType) continue;

    const actualType = Array.isArray(value) ? 'array' : typeof value;

    if (expectedType === 'array' && !Array.isArray(value)) {
      failingParameters.push(key);
      expectedConstraints[key] = 'Expected type "array" but received "' + typeof value + '"';
    } else if (expectedType === 'string' && typeof value !== 'string') {
      failingParameters.push(key);
      expectedConstraints[key] = 'Expected type "string" but received "' + actualType + '"';
    } else if (expectedType === 'number' && typeof value !== 'number') {
      failingParameters.push(key);
      expectedConstraints[key] = 'Expected type "number" but received "' + actualType + '"';
    } else if (expectedType === 'integer' && (typeof value !== 'number' || !Number.isInteger(value))) {
      failingParameters.push(key);
      expectedConstraints[key] = 'Expected type "integer" but received "' + actualType + '"';
    } else if (expectedType === 'boolean' && typeof value !== 'boolean') {
      failingParameters.push(key);
      expectedConstraints[key] = 'Expected type "boolean" but received "' + actualType + '"';
    } else if (expectedType === 'object' && (typeof value !== 'object' || Array.isArray(value) || value === null)) {
      failingParameters.push(key);
      expectedConstraints[key] = 'Expected type "object" but received "' + actualType + '"';
    }
  }

  if (failingParameters.length === 0) {
    return null; // Validation passed
  }

  return { failingParameters, expectedConstraints };
}

/**
 * Creates a JSON-RPC 2.0 success response.
 */
function jsonRpcSuccess(id, result) {
  return {
    jsonrpc: '2.0',
    id: id,
    result: result,
  };
}

/**
 * Creates a JSON-RPC 2.0 error response.
 */
function jsonRpcError(id, code, message, data) {
  const error = { code, message };
  if (data !== undefined) {
    error.data = data;
  }
  return {
    jsonrpc: '2.0',
    id: id !== undefined ? id : null,
    error: error,
  };
}

/**
 * Handle MCP JSON-RPC messages routed via POST /
 */
function handleMcpMessage(body, headers) {
  const sessionId = headers['mcp-session-id'] || headers['Mcp-Session-Id'] || randomUUID();
  const responseHeaders = {
    'Content-Type': 'application/json',
    'Mcp-Session-Id': sessionId,
  };

  // Parse JSON body
  let request;
  try {
    request = typeof body === 'string' ? JSON.parse(body) : body;
  } catch (e) {
    return {
      statusCode: 200,
      headers: responseHeaders,
      body: JSON.stringify(jsonRpcError(null, -32700, 'Parse error: Invalid JSON')),
    };
  }

  // Validate JSON-RPC 2.0 structure
  if (!request || typeof request !== 'object') {
    return {
      statusCode: 200,
      headers: responseHeaders,
      body: JSON.stringify(jsonRpcError(null, -32600, 'Invalid Request: Request must be a JSON object')),
    };
  }

  if (request.jsonrpc !== '2.0') {
    return {
      statusCode: 200,
      headers: responseHeaders,
      body: JSON.stringify(jsonRpcError(request.id || null, -32600, 'Invalid Request: Missing or invalid jsonrpc field (must be "2.0")')),
    };
  }

  if (!request.method || typeof request.method !== 'string') {
    return {
      statusCode: 200,
      headers: responseHeaders,
      body: JSON.stringify(jsonRpcError(request.id || null, -32600, 'Invalid Request: Missing or invalid method field')),
    };
  }

  const { method, id, params } = request;

  // Handle notifications (no id field) — return HTTP 204 with no body
  if (method === 'notifications/initialized') {
    return {
      statusCode: 204,
      headers: responseHeaders,
      body: '',
    };
  }

  // Route to method handlers
  switch (method) {
    case 'initialize': {
      const newSessionId = headers['mcp-session-id'] || headers['Mcp-Session-Id'] || randomUUID();
      responseHeaders['Mcp-Session-Id'] = newSessionId;
      return {
        statusCode: 200,
        headers: responseHeaders,
        body: JSON.stringify(jsonRpcSuccess(id, {
          protocolVersion: '2024-11-05',
          serverInfo: {
            name: agentName,
            version: '2.0.0',
          },
          capabilities: {
            tools: {
              listChanged: false,
            },
          },
        })),
      };
    }

    case 'tools/list': {
      return {
        statusCode: 200,
        headers: responseHeaders,
        body: JSON.stringify(jsonRpcSuccess(id, {
          tools: toolDefinitions,
        })),
      };
    }

    case 'tools/call': {
      const toolName = params && params.name;
      const toolArgs = (params && params.arguments) || {};

      // Validate tool exists
      const validTools = toolDefinitions.map(t => t.name);
      if (!toolName || !validTools.includes(toolName)) {
        return {
          statusCode: 200,
          headers: responseHeaders,
          body: JSON.stringify(jsonRpcError(id, -32602, 'Invalid params: tool \\'' + (toolName || '') + '\\' not found')),
        };
      }

      // Validate parameters against schema
      const validationError = validateParameters(toolName, toolArgs);
      if (validationError) {
        return {
          statusCode: 200,
          headers: responseHeaders,
          body: JSON.stringify(jsonRpcSuccess(id, {
            content: [{
              type: 'text',
              text: JSON.stringify({
                code: 'schema_validation_failed',
                message: 'Request parameters failed validation against the schema for action "' + toolName + '".',
                details: {
                  failing_parameters: validationError.failingParameters,
                  expected_constraints: validationError.expectedConstraints,
                },
              }),
            }],
            isError: true,
          })),
        };
      }

      // Successful invocation (simplified inline handler)
      return {
        statusCode: 200,
        headers: responseHeaders,
        body: JSON.stringify(jsonRpcSuccess(id, {
          content: [{
            type: 'text',
            text: JSON.stringify({
              message: 'Action ' + toolName + ' invoked successfully',
              parameters: toolArgs,
            }),
          }],
          isError: false,
        })),
      };
    }

    case 'ping': {
      return {
        statusCode: 200,
        headers: responseHeaders,
        body: JSON.stringify(jsonRpcSuccess(id, {})),
      };
    }

    default: {
      return {
        statusCode: 200,
        headers: responseHeaders,
        body: JSON.stringify(jsonRpcError(id, -32601, 'Method not found: ' + method)),
      };
    }
  }
}

exports.handler = async (event) => {
  const path = event.path || event.resource || '';
  const method = event.httpMethod || 'GET';
  const headers = event.headers || {};

  // GET /health - Return health check (preserved for monitoring)
  if (path.endsWith('/health') && method === 'GET') {
    const agentId = process.env.NETWORK_AGENT_ID || '';
    const agentAliasId = process.env.NETWORK_AGENT_ALIAS_ID || '';
    const captureTable = process.env.CAPTURE_STATE_TABLE || '';

    const networkAgentStatus = (agentId && agentAliasId) ? 'available' : (agentId || agentAliasId) ? 'unknown' : 'unavailable';
    const captureTableStatus = captureTable ? 'available' : 'unknown';
    const overallStatus = networkAgentStatus === 'unavailable' ? 'unhealthy' : networkAgentStatus === 'unknown' ? 'degraded' : 'healthy';

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json', 'Mcp-Session-Id': headers['mcp-session-id'] || headers['Mcp-Session-Id'] || '' },
      body: JSON.stringify({
        status: overallStatus,
        timestamp: new Date().toISOString(),
        components: {
          network_agent: { status: networkAgentStatus, agent_id: agentId ? agentId.substring(0, 4) + '***' : undefined },
          integration_lambda: { status: 'available', version: '2.0.0' },
          capture_state_table: { status: captureTableStatus },
        },
        region: process.env.AWS_REGION_OVERRIDE || process.env.AWS_REGION || 'unknown',
      }),
    };
  }

  // POST / - MCP JSON-RPC messages
  if (method === 'POST') {
    return handleMcpMessage(event.body, headers);
  }

  // Fallback for unknown routes
  return {
    statusCode: 404,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: 'Not found. Available routes: POST / (MCP JSON-RPC), GET /health' }),
  };
};
`;
  }
}
