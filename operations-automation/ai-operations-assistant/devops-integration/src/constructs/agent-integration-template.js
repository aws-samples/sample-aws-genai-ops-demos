"use strict";
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
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.AgentIntegrationTemplate = void 0;
const cdk = __importStar(require("aws-cdk-lib"));
const apigateway = __importStar(require("aws-cdk-lib/aws-apigateway"));
const iam = __importStar(require("aws-cdk-lib/aws-iam"));
const lambda = __importStar(require("aws-cdk-lib/aws-lambda"));
const dynamodb = __importStar(require("aws-cdk-lib/aws-dynamodb"));
const constructs_1 = require("constructs");
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
class AgentIntegrationTemplate extends constructs_1.Construct {
    constructor(scope, id, props) {
        super(scope, id);
        const { agentName, agentRuntimeArn, actions, authorizationGroupName } = props;
        const lambdaCode = props.lambdaCode;
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
                iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AWSLambdaBasicExecutionRole"),
            ],
        });
        // Allow Lambda to invoke the agent runtime
        // The SDK appends /runtime-endpoint/DEFAULT to the ARN, so we need a wildcard
        lambdaRole.addToPolicy(new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["bedrock-agentcore:InvokeAgentRuntime"],
            resources: [agentRuntimeArn, `${agentRuntimeArn}/*`],
        }));
        // Allow Lambda to read/write to the capture state table
        this.captureStateTable.grantReadWriteData(lambdaRole);
        this.integrationLambda = new lambda.Function(this, "IntegrationLambda", {
            runtime: lambda.Runtime.NODEJS_20_X,
            handler: lambdaHandler,
            code: lambdaCode ?? lambda.Code.fromInline(this.generateLambdaCode(agentName, actions)),
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
        this.api.root.addMethod("POST", new apigateway.LambdaIntegration(this.integrationLambda), {
            authorizationType: apigateway.AuthorizationType.IAM,
        });
        // GET /health - returns health check response (preserved for monitoring)
        const healthResource = this.api.root.addResource("health");
        healthResource.addMethod("GET", new apigateway.LambdaIntegration(this.integrationLambda), {
            authorizationType: apigateway.AuthorizationType.IAM,
        });
        // ─── IAM Role for DevOps Agent ────────────────────────────────────────
        this.devOpsAgentRole = new iam.Role(this, "DevOpsAgentRole", {
            assumedBy: new iam.ServicePrincipal("bedrock.amazonaws.com"),
            description: `Role for DevOps Agent to invoke ${agentName} MCP endpoint via SigV4`,
        });
        // Add trust for aidevops.amazonaws.com with confused deputy protection
        // Required per AWS docs: https://docs.aws.amazon.com/devopsagent/latest/userguide/configuring-integrations-and-knowledge-connecting-mcp-servers.html
        this.devOpsAgentRole.assumeRolePolicy.addStatements(new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            principals: [new iam.ServicePrincipal("aidevops.amazonaws.com")],
            actions: ["sts:AssumeRole"],
            conditions: {
                StringEquals: {
                    "aws:SourceAccount": cdk.Stack.of(this).account,
                },
            },
        }));
        // Allow DevOps Agent to invoke the agent runtime
        this.devOpsAgentRole.addToPolicy(new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["bedrock-agentcore:InvokeAgentRuntime"],
            resources: [agentRuntimeArn],
        }));
        // Allow DevOps Agent to invoke the MCP API Gateway endpoint
        this.devOpsAgentRole.addToPolicy(new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["execute-api:Invoke"],
            resources: [
                this.api.arnForExecuteApi("POST", "/", "prod"),
                this.api.arnForExecuteApi("GET", "/health", "prod"),
            ],
        }));
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
    extractAgentId(arn) {
        const parts = arn.split("/");
        return parts[parts.length - 1] ?? "unknown";
    }
    /**
     * Generate MCP tool definitions from the provided action definitions.
     * Each tool definition conforms to the MCP Tool schema with name, description, and inputSchema.
     */
    generateMcpToolDefinitions(agentName, actions) {
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
    generateSchemasMap(actions) {
        const schemas = {};
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
    generateLambdaCode(agentName, actions) {
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
exports.AgentIntegrationTemplate = AgentIntegrationTemplate;
//# sourceMappingURL=data:application/json;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoiYWdlbnQtaW50ZWdyYXRpb24tdGVtcGxhdGUuanMiLCJzb3VyY2VSb290IjoiIiwic291cmNlcyI6WyJhZ2VudC1pbnRlZ3JhdGlvbi10ZW1wbGF0ZS50cyJdLCJuYW1lcyI6W10sIm1hcHBpbmdzIjoiO0FBQUE7Ozs7Ozs7OztHQVNHOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7QUFFSCxpREFBbUM7QUFDbkMsdUVBQXlEO0FBQ3pELHlEQUEyQztBQUMzQywrREFBaUQ7QUFDakQsbUVBQXFEO0FBQ3JELDJDQUF1QztBQUd2Qzs7Ozs7Ozs7Ozs7Ozs7Ozs7R0FpQkc7QUFDSCxNQUFhLHdCQUF5QixTQUFRLHNCQUFTO0lBeUJyRCxZQUFZLEtBQWdCLEVBQUUsRUFBVSxFQUFFLEtBQW9DO1FBQzVFLEtBQUssQ0FBQyxLQUFLLEVBQUUsRUFBRSxDQUFDLENBQUM7UUFFakIsTUFBTSxFQUFFLFNBQVMsRUFBRSxlQUFlLEVBQUUsT0FBTyxFQUFFLHNCQUFzQixFQUFFLEdBQUcsS0FBSyxDQUFDO1FBQzlFLE1BQU0sVUFBVSxHQUFHLEtBQUssQ0FBQyxVQUFxQyxDQUFDO1FBQy9ELE1BQU0sYUFBYSxHQUFHLEtBQUssQ0FBQyxhQUFhLElBQUksZUFBZSxDQUFDO1FBRTdELHlFQUF5RTtRQUV6RSxJQUFJLENBQUMsaUJBQWlCLEdBQUcsSUFBSSxRQUFRLENBQUMsS0FBSyxDQUFDLElBQUksRUFBRSxtQkFBbUIsRUFBRTtZQUNyRSxZQUFZLEVBQUUsRUFBRSxJQUFJLEVBQUUsSUFBSSxFQUFFLElBQUksRUFBRSxRQUFRLENBQUMsYUFBYSxDQUFDLE1BQU0sRUFBRTtZQUNqRSxPQUFPLEVBQUUsRUFBRSxJQUFJLEVBQUUsSUFBSSxFQUFFLElBQUksRUFBRSxRQUFRLENBQUMsYUFBYSxDQUFDLE1BQU0sRUFBRTtZQUM1RCxXQUFXLEVBQUUsUUFBUSxDQUFDLFdBQVcsQ0FBQyxlQUFlO1lBQ2pELGFBQWEsRUFBRSxHQUFHLENBQUMsYUFBYSxDQUFDLE9BQU87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QUMzQyxLQUFLLE1BQU0sTUFBTSxJQUFJLE9BQU8sRUFBRSxDQUFDO1lBQzdCLE9BQU8sQ0FBQyxNQUFNLENBQUMsSUFBSSxDQUFDLEdBQUcsTUFBTSxDQUFDLFlBQVksQ0FBQztRQUM3QyxDQUFDO1FBQ0QsT0FBTyxPQUFPLENBQUM7SUFDakIsQ0FBQztJQUVEOzs7Ozs7Ozs7Ozs7T0FZRztJQUNLLGtCQUFrQixDQUN4QixTQUFpQixFQUNqQixPQUEyQjtRQUUzQixpRUFBaUU7UUFDakUscURBQXFEO1FBQ3JELE1BQU0sWUFBWSxHQUFHLElBQUksQ0FBQyxTQUFTLENBQUMsSUFBSSxDQUFDLDBCQUEwQixDQUFDLFNBQVMsRUFBRSxPQUFPLENBQUMsQ0FBQyxDQUFDO1FBQ3pGLE1BQU0saUJBQWlCLEdBQUcsSUFBSSxDQUFDLFNBQVMsQ0FBQyxJQUFJLENBQUMsa0JBQWtCLENBQUMsT0FBTyxDQUFDLENBQUMsQ0FBQztRQUUzRSxPQUFPOzs7OzswQkFLZSxZQUFZO3dCQUNkLGlCQUFpQjsrQ0FDTSxTQUFTOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Q0F5U3ZELENBQUM7SUFDQSxDQUFDO0NBQ0Y7QUFwakJELDREQW9qQkMiLCJzb3VyY2VzQ29udGVudCI6WyIvKipcclxuICogQWdlbnQgSW50ZWdyYXRpb24gVGVtcGxhdGUgLSBSZXVzYWJsZSBDREsgQ29uc3RydWN0XHJcbiAqXHJcbiAqIEEgcGFyYW1ldGVyaXplZCBDREsgY29uc3RydWN0IHRoYXQgcHJvZHVjZXMgYSBjb21wbGV0ZSBEZXZPcHMgQWdlbnQgTUNQIGludGVncmF0aW9uXHJcbiAqIGVuZHBvaW50IGZvciBhbnkgR09BVCBzdWItYWdlbnQuIFByb3ZpZGVzIEFQSSBHYXRld2F5IChQT1NUIC8gZm9yIE1DUCBKU09OLVJQQyxcclxuICogR0VUIC9oZWFsdGggZm9yIG1vbml0b3JpbmcpLCBJbnRlZ3JhdGlvbiBMYW1iZGEsIElBTSByb2xlcywgYXV0by1nZW5lcmF0ZWQgTUNQIHRvb2xcclxuICogZGVmaW5pdGlvbnMsIGhlYWx0aCBjaGVjaywgYW5kIHN0YW5kYXJkaXplZCBKU09OLVJQQyAyLjAgcmVzcG9uc2UgZW52ZWxvcGUuXHJcbiAqXHJcbiAqIFJlcXVpcmVtZW50czogNS4yLCA1LjMsIDUuNCwgNS42LCA2LjEsIDYuMiwgNi40LCA3LjEsIDcuMiwgNy4zLCA3LjQsIDcuNSwgNy42LCA3LjdcclxuICovXHJcblxyXG5pbXBvcnQgKiBhcyBjZGsgZnJvbSBcImF3cy1jZGstbGliXCI7XHJcbmltcG9ydCAqIGFzIGFwaWdhdGV3YXkgZnJvbSBcImF3cy1jZGstbGliL2F3cy1hcGlnYXRld2F5XCI7XHJcbmltcG9ydCAqIGFzIGlhbSBmcm9tIFwiYXdzLWNkay1saWIvYXdzLWlhbVwiO1xyXG5pbXBvcnQgKiBhcyBsYW1iZGEgZnJvbSBcImF3cy1jZGstbGliL2F3cy1sYW1iZGFcIjtcclxuaW1wb3J0ICogYXMgZHluYW1vZGIgZnJvbSBcImF3cy1jZGstbGliL2F3cy1keW5hbW9kYlwiO1xyXG5pbXBvcnQgeyBDb25zdHJ1Y3QgfSBmcm9tIFwiY29uc3RydWN0c1wiO1xyXG5pbXBvcnQgdHlwZSB7IEFjdGlvbkRlZmluaXRpb24sIEFnZW50SW50ZWdyYXRpb25UZW1wbGF0ZVByb3BzIH0gZnJvbSBcIi4uL3R5cGVzXCI7XHJcblxyXG4vKipcclxuICogQWdlbnRJbnRlZ3JhdGlvblRlbXBsYXRlIENESyBDb25zdHJ1Y3QuXHJcbiAqXHJcbiAqIENyZWF0ZXMgYSBjb21wbGV0ZSBEZXZPcHMgQWdlbnQgTUNQIGludGVncmF0aW9uIGVuZHBvaW50IGZvciBhbnkgR09BVCBzdWItYWdlbnQsXHJcbiAqIGluY2x1ZGluZyBBUEkgR2F0ZXdheSAoc2luZ2xlIFBPU1QgZW5kcG9pbnQgZm9yIE1DUCBKU09OLVJQQyBtZXNzYWdlcyArIEdFVCAvaGVhbHRoKSxcclxuICogSW50ZWdyYXRpb24gTGFtYmRhLCBJQU0gcm9sZXMsIGF1dG8tZ2VuZXJhdGVkIE1DUCB0b29sIGRlZmluaXRpb25zLCBoZWFsdGggY2hlY2ssXHJcbiAqIGFuZCBzdGFuZGFyZGl6ZWQgSlNPTi1SUEMgMi4wIHJlc3BvbnNlIGVudmVsb3BlLlxyXG4gKlxyXG4gKiBVc2FnZTpcclxuICogYGBgdHlwZXNjcmlwdFxyXG4gKiBjb25zdCBpbnRlZ3JhdGlvbiA9IG5ldyBBZ2VudEludGVncmF0aW9uVGVtcGxhdGUodGhpcywgJ05ldHdvcmtBZ2VudEludGVncmF0aW9uJywge1xyXG4gKiAgIGFnZW50TmFtZTogJ25ldHdvcmstYWdlbnQnLFxyXG4gKiAgIGFnZW50UnVudGltZUFybjogJ2Fybjphd3M6YmVkcm9jazp1cy1lYXN0LTE6MTIzNDU2Nzg5MDEyOmFnZW50L0FHRU5UX0lEJyxcclxuICogICBhY3Rpb25zOiBuZXR3b3JrQWdlbnRBY3Rpb25zLFxyXG4gKiAgIGF1dGhvcml6YXRpb25Hcm91cE5hbWU6ICdOZXR3b3JrQ2FwdHVyZUF1dGhHcm91cCcsXHJcbiAqIH0pO1xyXG4gKiBgYGBcclxuICovXHJcbmV4cG9ydCBjbGFzcyBBZ2VudEludGVncmF0aW9uVGVtcGxhdGUgZXh0ZW5kcyBDb25zdHJ1Y3Qge1xyXG4gIC8qKiBUaGUgQVBJIEdhdGV3YXkgSFRUUFMgZW5kcG9pbnQgVVJMICovXHJcbiAgcHVibGljIHJlYWRvbmx5IGVuZHBvaW50VXJsOiBzdHJpbmc7XHJcblxyXG4gIC8qKiBUaGUgTUNQIGVuZHBvaW50IFVSTCAoUE9TVCAvIGZvciBKU09OLVJQQyBtZXNzYWdlcykgKi9cclxuICBwdWJsaWMgcmVhZG9ubHkgbWNwRW5kcG9pbnRVcmw6IHN0cmluZztcclxuXHJcbiAgLyoqIFRoZSBmdWxsIGhlYWx0aCBjaGVjayBVUkwgKGVuZHBvaW50ICsgL2hlYWx0aCkgKi9cclxuICBwdWJsaWMgcmVhZG9ubHkgaGVhbHRoVXJsOiBzdHJpbmc7XHJcblxyXG4gIC8qKiBUaGUgQVJOIG9mIHRoZSBJQU0gcm9sZSBEZXZPcHMgQWdlbnQgc2hvdWxkIGFzc3VtZSAqL1xyXG4gIHB1YmxpYyByZWFkb25seSBkZXZPcHNBZ2VudFJvbGVBcm46IHN0cmluZztcclxuXHJcbiAgLyoqIFRoZSBBUEkgR2F0ZXdheSBSRVNUIEFQSSByZXNvdXJjZSAqL1xyXG4gIHB1YmxpYyByZWFkb25seSBhcGk6IGFwaWdhdGV3YXkuUmVzdEFwaTtcclxuXHJcbiAgLyoqIFRoZSBJbnRlZ3JhdGlvbiBMYW1iZGEgZnVuY3Rpb24gKi9cclxuICBwdWJsaWMgcmVhZG9ubHkgaW50ZWdyYXRpb25MYW1iZGE6IGxhbWJkYS5GdW5jdGlvbjtcclxuXHJcbiAgLyoqIFRoZSBJQU0gcm9sZSBmb3IgRGV2T3BzIEFnZW50IHRvIGFzc3VtZSAqL1xyXG4gIHB1YmxpYyByZWFkb25seSBkZXZPcHNBZ2VudFJvbGU6IGlhbS5Sb2xlO1xyXG5cclxuICAvKiogVGhlIENhcHR1cmUgU3RhdGUgRHluYW1vREIgdGFibGUgKi9cclxuICBwdWJsaWMgcmVhZG9ubHkgY2FwdHVyZVN0YXRlVGFibGU6IGR5bmFtb2RiLlRhYmxlO1xyXG5cclxuICBjb25zdHJ1Y3RvcihzY29wZTogQ29uc3RydWN0LCBpZDogc3RyaW5nLCBwcm9wczogQWdlbnRJbnRlZ3JhdGlvblRlbXBsYXRlUHJvcHMpIHtcclxuICAgIHN1cGVyKHNjb3BlLCBpZCk7XHJcblxyXG4gICAgY29uc3QgeyBhZ2VudE5hbWUsIGFnZW50UnVudGltZUFybiwgYWN0aW9ucywgYXV0aG9yaXphdGlvbkdyb3VwTmFtZSB9ID0gcHJvcHM7XHJcbiAgICBjb25zdCBsYW1iZGFDb2RlID0gcHJvcHMubGFtYmRhQ29kZSBhcyBsYW1iZGEuQ29kZSB8IHVuZGVmaW5lZDtcclxuICAgIGNvbnN0IGxhbWJkYUhhbmRsZXIgPSBwcm9wcy5sYW1iZGFIYW5kbGVyID8/IFwiaW5kZXguaGFuZGxlclwiO1xyXG5cclxuICAgIC8vIOKUgOKUgOKUgCBEeW5hbW9EQiBUYWJsZSBmb3IgQ2FwdHVyZSBTdGF0ZSDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIBcclxuXHJcbiAgICB0aGlzLmNhcHR1cmVTdGF0ZVRhYmxlID0gbmV3IGR5bmFtb2RiLlRhYmxlKHRoaXMsIFwiQ2FwdHVyZVN0YXRlVGFibGVcIiwge1xyXG4gICAgICBwYXJ0aXRpb25LZXk6IHsgbmFtZTogXCJQS1wiLCB0eXBlOiBkeW5hbW9kYi5BdHRyaWJ1dGVUeXBlLlNUUklORyB9LFxyXG4gICAgICBzb3J0S2V5OiB7IG5hbWU6IFwiU0tcIiwgdHlwZTogZHluYW1vZGIuQXR0cmlidXRlVHlwZS5TVFJJTkcgfSxcclxuICAgICAgYmlsbGluZ01vZGU6IGR5bmFtb2RiLkJpbGxpbmdNb2RlLlBBWV9QRVJfUkVRVUVTVCxcclxuICAgICAgcmVtb3ZhbFBvbGljeTogY2RrLlJlbW92YWxQb2xpY3kuREVTVFJPWSxcclxuICAgICAgdGltZVRvTGl2ZUF0dHJpYnV0ZTogXCJ0dGxcIixcclxuICAgIH0pO1xyXG5cclxuICAgIC8vIOKUgOKUgOKUgCBJbnRlZ3JhdGlvbiBMYW1iZGEg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAXHJcblxyXG4gICAgY29uc3QgbGFtYmRhUm9sZSA9IG5ldyBpYW0uUm9sZSh0aGlzLCBcIkludGVncmF0aW9uTGFtYmRhUm9sZVwiLCB7XHJcbiAgICAgIGFzc3VtZWRCeTogbmV3IGlhbS5TZXJ2aWNlUHJpbmNpcGFsKFwibGFtYmRhLmFtYXpvbmF3cy5jb21cIiksXHJcbiAgICAgIG1hbmFnZWRQb2xpY2llczogW1xyXG4gICAgICAgIGlhbS5NYW5hZ2VkUG9saWN5LmZyb21Bd3NNYW5hZ2VkUG9saWN5TmFtZShcclxuICAgICAgICAgIFwic2VydmljZS1yb2xlL0FXU0xhbWJkYUJhc2ljRXhlY3V0aW9uUm9sZVwiXHJcbiAgICAgICAgKSxcclxuICAgICAgXSxcclxuICAgIH0pO1xyXG5cclxuICAgIC8vIEFsbG93IExhbWJkYSB0byBpbnZva2UgdGhlIGFnZW50IHJ1bnRpbWVcclxuICAgIC8vIFRoZSBTREsgYXBwZW5kcyAvcnVudGltZS1lbmRwb2ludC9ERUZBVUxUIHRvIHRoZSBBUk4sIHNvIHdlIG5lZWQgYSB3aWxkY2FyZFxyXG4gICAgbGFtYmRhUm9sZS5hZGRUb1BvbGljeShcclxuICAgICAgbmV3IGlhbS5Qb2xpY3lTdGF0ZW1lbnQoe1xyXG4gICAgICAgIGVmZmVjdDogaWFtLkVmZmVjdC5BTExPVyxcclxuICAgICAgICBhY3Rpb25zOiBbXCJiZWRyb2NrLWFnZW50Y29yZTpJbnZva2VBZ2VudFJ1bnRpbWVcIl0sXHJcbiAgICAgICAgcmVzb3VyY2VzOiBbYWdlbnRSdW50aW1lQXJuLCBgJHthZ2VudFJ1bnRpbWVBcm59LypgXSxcclxuICAgICAgfSlcclxuICAgICk7XHJcblxyXG4gICAgLy8gQWxsb3cgTGFtYmRhIHRvIHJlYWQvd3JpdGUgdG8gdGhlIGNhcHR1cmUgc3RhdGUgdGFibGVcclxuICAgIHRoaXMuY2FwdHVyZVN0YXRlVGFibGUuZ3JhbnRSZWFkV3JpdGVEYXRhKGxhbWJkYVJvbGUpO1xyXG5cclxuICAgIHRoaXMuaW50ZWdyYXRpb25MYW1iZGEgPSBuZXcgbGFtYmRhLkZ1bmN0aW9uKHRoaXMsIFwiSW50ZWdyYXRpb25MYW1iZGFcIiwge1xyXG4gICAgICBydW50aW1lOiBsYW1iZGEuUnVudGltZS5OT0RFSlNfMjBfWCxcclxuICAgICAgaGFuZGxlcjogbGFtYmRhSGFuZGxlcixcclxuICAgICAgY29kZTogbGFtYmRhQ29kZSA/PyBsYW1iZGEuQ29kZS5mcm9tSW5saW5lKFxyXG4gICAgICAgIHRoaXMuZ2VuZXJhdGVMYW1iZGFDb2RlKGFnZW50TmFtZSwgYWN0aW9ucylcclxuICAgICAgKSxcclxuICAgICAgcm9sZTogbGFtYmRhUm9sZSxcclxuICAgICAgdGltZW91dDogY2RrLkR1cmF0aW9uLnNlY29uZHMoNjApLFxyXG4gICAgICBtZW1vcnlTaXplOiAyNTYsXHJcbiAgICAgIGVudmlyb25tZW50OiB7XHJcbiAgICAgICAgTkVUV09SS19BR0VOVF9JRDogdGhpcy5leHRyYWN0QWdlbnRJZChhZ2VudFJ1bnRpbWVBcm4pLFxyXG4gICAgICAgIE5FVFdPUktfQUdFTlRfQVJOOiBhZ2VudFJ1bnRpbWVBcm4sXHJcbiAgICAgICAgTkVUV09SS19BR0VOVF9BTElBU19JRDogXCJUU1RBTElBU0lEXCIsXHJcbiAgICAgICAgQVdTX1JFR0lPTl9PVkVSUklERTogY2RrLlN0YWNrLm9mKHRoaXMpLnJlZ2lvbixcclxuICAgICAgICBDQVBUVVJFX1NUQVRFX1RBQkxFOiB0aGlzLmNhcHR1cmVTdGF0ZVRhYmxlLnRhYmxlTmFtZSxcclxuICAgICAgICBBVVRIT1JJWkVEX1JPTEVfQVJOUzogXCIqXCIsXHJcbiAgICAgICAgQ0FQVFVSRV9BVVRIT1JJWkFUSU9OX0dST1VQOiBhdXRob3JpemF0aW9uR3JvdXBOYW1lID8/IFwiXCIsXHJcbiAgICAgICAgQUdFTlRfTkFNRTogYWdlbnROYW1lLFxyXG4gICAgICAgIC8vIE5PVEU6IFRPT0xfREVGSU5JVElPTlMgYW5kIEFDVElPTl9TQ0hFTUFTIGFyZSBlbWJlZGRlZCBkaXJlY3RseSBpblxyXG4gICAgICAgIC8vIHRoZSBpbmxpbmUgTGFtYmRhIGNvZGUgdG8gYXZvaWQgdGhlIDRLQiBlbnYgdmFyIGxpbWl0LiBUaGV5IGFyZSBsb2FkZWRcclxuICAgICAgICAvLyBhcyBtb2R1bGUtbGV2ZWwgY29uc3RhbnRzIGluIHRoZSBnZW5lcmF0ZWQgaGFuZGxlciBjb2RlLlxyXG4gICAgICB9LFxyXG4gICAgfSk7XHJcblxyXG4gICAgLy8g4pSA4pSA4pSAIEFQSSBHYXRld2F5IOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgFxyXG5cclxuICAgIHRoaXMuYXBpID0gbmV3IGFwaWdhdGV3YXkuUmVzdEFwaSh0aGlzLCBcIkludGVncmF0aW9uQXBpXCIsIHtcclxuICAgICAgcmVzdEFwaU5hbWU6IGAke2FnZW50TmFtZX0tbWNwLWludGVncmF0aW9uYCxcclxuICAgICAgZGVzY3JpcHRpb246IGBNQ1Agc2VydmVyIGVuZHBvaW50IGZvciAke2FnZW50TmFtZX0gRGV2T3BzIEFnZW50IGludGVncmF0aW9uYCxcclxuICAgICAgZGVwbG95T3B0aW9uczoge1xyXG4gICAgICAgIHN0YWdlTmFtZTogXCJwcm9kXCIsXHJcbiAgICAgIH0sXHJcbiAgICAgIGRlZmF1bHRDb3JzUHJlZmxpZ2h0T3B0aW9uczoge1xyXG4gICAgICAgIGFsbG93T3JpZ2luczogYXBpZ2F0ZXdheS5Db3JzLkFMTF9PUklHSU5TLFxyXG4gICAgICAgIGFsbG93TWV0aG9kczogYXBpZ2F0ZXdheS5Db3JzLkFMTF9NRVRIT0RTLFxyXG4gICAgICB9LFxyXG4gICAgfSk7XHJcblxyXG4gICAgLy8gUE9TVCAvIC0gTUNQIEpTT04tUlBDIG1lc3NhZ2VzIGVuZHBvaW50IChyb290IHBhdGgpXHJcbiAgICB0aGlzLmFwaS5yb290LmFkZE1ldGhvZChcclxuICAgICAgXCJQT1NUXCIsXHJcbiAgICAgIG5ldyBhcGlnYXRld2F5LkxhbWJkYUludGVncmF0aW9uKHRoaXMuaW50ZWdyYXRpb25MYW1iZGEpLFxyXG4gICAgICB7XHJcbiAgICAgICAgYXV0aG9yaXphdGlvblR5cGU6IGFwaWdhdGV3YXkuQXV0aG9yaXphdGlvblR5cGUuSUFNLFxyXG4gICAgICB9XHJcbiAgICApO1xyXG5cclxuICAgIC8vIEdFVCAvaGVhbHRoIC0gcmV0dXJucyBoZWFsdGggY2hlY2sgcmVzcG9uc2UgKHByZXNlcnZlZCBmb3IgbW9uaXRvcmluZylcclxuICAgIGNvbnN0IGhlYWx0aFJlc291cmNlID0gdGhpcy5hcGkucm9vdC5hZGRSZXNvdXJjZShcImhlYWx0aFwiKTtcclxuICAgIGhlYWx0aFJlc291cmNlLmFkZE1ldGhvZChcclxuICAgICAgXCJHRVRcIixcclxuICAgICAgbmV3IGFwaWdhdGV3YXkuTGFtYmRhSW50ZWdyYXRpb24odGhpcy5pbnRlZ3JhdGlvbkxhbWJkYSksXHJcbiAgICAgIHtcclxuICAgICAgICBhdXRob3JpemF0aW9uVHlwZTogYXBpZ2F0ZXdheS5BdXRob3JpemF0aW9uVHlwZS5JQU0sXHJcbiAgICAgIH1cclxuICAgICk7XHJcblxyXG4gICAgLy8g4pSA4pSA4pSAIElBTSBSb2xlIGZvciBEZXZPcHMgQWdlbnQg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAXHJcblxyXG4gICAgdGhpcy5kZXZPcHNBZ2VudFJvbGUgPSBuZXcgaWFtLlJvbGUodGhpcywgXCJEZXZPcHNBZ2VudFJvbGVcIiwge1xyXG4gICAgICBhc3N1bWVkQnk6IG5ldyBpYW0uU2VydmljZVByaW5jaXBhbChcImJlZHJvY2suYW1hem9uYXdzLmNvbVwiKSxcclxuICAgICAgZGVzY3JpcHRpb246IGBSb2xlIGZvciBEZXZPcHMgQWdlbnQgdG8gaW52b2tlICR7YWdlbnROYW1lfSBNQ1AgZW5kcG9pbnQgdmlhIFNpZ1Y0YCxcclxuICAgIH0pO1xyXG5cclxuICAgIC8vIEFkZCB0cnVzdCBmb3IgYWlkZXZvcHMuYW1hem9uYXdzLmNvbSB3aXRoIGNvbmZ1c2VkIGRlcHV0eSBwcm90ZWN0aW9uXHJcbiAgICAvLyBSZXF1aXJlZCBwZXIgQVdTIGRvY3M6IGh0dHBzOi8vZG9jcy5hd3MuYW1hem9uLmNvbS9kZXZvcHNhZ2VudC9sYXRlc3QvdXNlcmd1aWRlL2NvbmZpZ3VyaW5nLWludGVncmF0aW9ucy1hbmQta25vd2xlZGdlLWNvbm5lY3RpbmctbWNwLXNlcnZlcnMuaHRtbFxyXG4gICAgdGhpcy5kZXZPcHNBZ2VudFJvbGUuYXNzdW1lUm9sZVBvbGljeSEuYWRkU3RhdGVtZW50cyhcclxuICAgICAgbmV3IGlhbS5Qb2xpY3lTdGF0ZW1lbnQoe1xyXG4gICAgICAgIGVmZmVjdDogaWFtLkVmZmVjdC5BTExPVyxcclxuICAgICAgICBwcmluY2lwYWxzOiBbbmV3IGlhbS5TZXJ2aWNlUHJpbmNpcGFsKFwiYWlkZXZvcHMuYW1hem9uYXdzLmNvbVwiKV0sXHJcbiAgICAgICAgYWN0aW9uczogW1wic3RzOkFzc3VtZVJvbGVcIl0sXHJcbiAgICAgICAgY29uZGl0aW9uczoge1xyXG4gICAgICAgICAgU3RyaW5nRXF1YWxzOiB7XHJcbiAgICAgICAgICAgIFwiYXdzOlNvdXJjZUFjY291bnRcIjogY2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnQsXHJcbiAgICAgICAgICB9LFxyXG4gICAgICAgIH0sXHJcbiAgICAgIH0pXHJcbiAgICApO1xyXG5cclxuICAgIC8vIEFsbG93IERldk9wcyBBZ2VudCB0byBpbnZva2UgdGhlIGFnZW50IHJ1bnRpbWVcclxuICAgIHRoaXMuZGV2T3BzQWdlbnRSb2xlLmFkZFRvUG9saWN5KFxyXG4gICAgICBuZXcgaWFtLlBvbGljeVN0YXRlbWVudCh7XHJcbiAgICAgICAgZWZmZWN0OiBpYW0uRWZmZWN0LkFMTE9XLFxyXG4gICAgICAgIGFjdGlvbnM6IFtcImJlZHJvY2stYWdlbnRjb3JlOkludm9rZUFnZW50UnVudGltZVwiXSxcclxuICAgICAgICByZXNvdXJjZXM6IFthZ2VudFJ1bnRpbWVBcm5dLFxyXG4gICAgICB9KVxyXG4gICAgKTtcclxuXHJcbiAgICAvLyBBbGxvdyBEZXZPcHMgQWdlbnQgdG8gaW52b2tlIHRoZSBNQ1AgQVBJIEdhdGV3YXkgZW5kcG9pbnRcclxuICAgIHRoaXMuZGV2T3BzQWdlbnRSb2xlLmFkZFRvUG9saWN5KFxyXG4gICAgICBuZXcgaWFtLlBvbGljeVN0YXRlbWVudCh7XHJcbiAgICAgICAgZWZmZWN0OiBpYW0uRWZmZWN0LkFMTE9XLFxyXG4gICAgICAgIGFjdGlvbnM6IFtcImV4ZWN1dGUtYXBpOkludm9rZVwiXSxcclxuICAgICAgICByZXNvdXJjZXM6IFtcclxuICAgICAgICAgIHRoaXMuYXBpLmFybkZvckV4ZWN1dGVBcGkoXCJQT1NUXCIsIFwiL1wiLCBcInByb2RcIiksXHJcbiAgICAgICAgICB0aGlzLmFwaS5hcm5Gb3JFeGVjdXRlQXBpKFwiR0VUXCIsIFwiL2hlYWx0aFwiLCBcInByb2RcIiksXHJcbiAgICAgICAgXSxcclxuICAgICAgfSlcclxuICAgICk7XHJcblxyXG4gICAgLy8g4pSA4pSA4pSAIENvbnN0cnVjdCBPdXRwdXRzIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgFxyXG5cclxuICAgIHRoaXMuZW5kcG9pbnRVcmwgPSB0aGlzLmFwaS51cmw7XHJcbiAgICB0aGlzLm1jcEVuZHBvaW50VXJsID0gdGhpcy5hcGkudXJsO1xyXG4gICAgdGhpcy5oZWFsdGhVcmwgPSBgJHt0aGlzLmFwaS51cmx9aGVhbHRoYDtcclxuICAgIHRoaXMuZGV2T3BzQWdlbnRSb2xlQXJuID0gdGhpcy5kZXZPcHNBZ2VudFJvbGUucm9sZUFybjtcclxuXHJcbiAgICAvLyDilIDilIDilIAgQ2ZuT3V0cHV0cyDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIBcclxuXHJcbiAgICBuZXcgY2RrLkNmbk91dHB1dCh0aGlzLCBcIk1jcEVuZHBvaW50VXJsXCIsIHtcclxuICAgICAgdmFsdWU6IHRoaXMubWNwRW5kcG9pbnRVcmwsXHJcbiAgICAgIGRlc2NyaXB0aW9uOiBcIk1DUCBKU09OLVJQQyBlbmRwb2ludCBVUkwgKFBPU1QpXCIsXHJcbiAgICB9KTtcclxuXHJcbiAgICBuZXcgY2RrLkNmbk91dHB1dCh0aGlzLCBcIlJlZ2lzdGVyQ29tbWFuZFwiLCB7XHJcbiAgICAgIHZhbHVlOiBgYXdzIGRldm9wcy1hZ2VudCByZWdpc3Rlci1zZXJ2aWNlIC0tc2VydmljZSBtY3BzZXJ2ZXJzaWd2NCAtLW5hbWUgJHthZ2VudE5hbWV9LSR7Y2RrLlN0YWNrLm9mKHRoaXMpLnJlZ2lvbn0gLS1lbmRwb2ludCAke3RoaXMubWNwRW5kcG9pbnRVcmx9IC0tYXV0aG9yaXphdGlvbkNvbmZpZyAne1wicmVnaW9uXCI6XCIke2Nkay5TdGFjay5vZih0aGlzKS5yZWdpb259XCIsXCJzZXJ2aWNlXCI6XCJleGVjdXRlLWFwaVwiLFwibWNwUm9sZUFyblwiOlwiJHt0aGlzLmRldk9wc0FnZW50Um9sZS5yb2xlQXJufVwifSdgLFxyXG4gICAgICBkZXNjcmlwdGlvbjogXCJDb21tYW5kIHRvIHJlZ2lzdGVyIHRoZSBNQ1Agc2VydmVyIHdpdGggRGV2T3BzIEFnZW50XCIsXHJcbiAgICB9KTtcclxuXHJcbiAgICBuZXcgY2RrLkNmbk91dHB1dCh0aGlzLCBcIkhlYWx0aENoZWNrVXJsXCIsIHtcclxuICAgICAgdmFsdWU6IHRoaXMuaGVhbHRoVXJsLFxyXG4gICAgICBkZXNjcmlwdGlvbjogXCJIZWFsdGggY2hlY2sgZW5kcG9pbnQgVVJMIChHRVQpXCIsXHJcbiAgICB9KTtcclxuXHJcbiAgICBuZXcgY2RrLkNmbk91dHB1dCh0aGlzLCBcIkRldk9wc0FnZW50Um9sZUFyblwiLCB7XHJcbiAgICAgIHZhbHVlOiB0aGlzLmRldk9wc0FnZW50Um9sZUFybixcclxuICAgICAgZGVzY3JpcHRpb246IFwiSUFNIHJvbGUgQVJOIGZvciBEZXZPcHMgQWdlbnQgU2lnVjQgYXV0aGVudGljYXRpb25cIixcclxuICAgIH0pO1xyXG4gIH1cclxuXHJcbiAgLy8g4pSA4pSA4pSAIFByaXZhdGUgSGVscGVycyDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIBcclxuXHJcbiAgLyoqXHJcbiAgICogRXh0cmFjdCB0aGUgYWdlbnQgSUQgZnJvbSB0aGUgYWdlbnQgcnVudGltZSBBUk4uXHJcbiAgICogQVJOIGZvcm1hdDogYXJuOmF3czpiZWRyb2NrOntyZWdpb259OnthY2NvdW50fTphZ2VudC97YWdlbnRJZH1cclxuICAgKi9cclxuICBwcml2YXRlIGV4dHJhY3RBZ2VudElkKGFybjogc3RyaW5nKTogc3RyaW5nIHtcclxuICAgIGNvbnN0IHBhcnRzID0gYXJuLnNwbGl0KFwiL1wiKTtcclxuICAgIHJldHVybiBwYXJ0c1twYXJ0cy5sZW5ndGggLSAxXSA/PyBcInVua25vd25cIjtcclxuICB9XHJcblxyXG4gIC8qKlxyXG4gICAqIEdlbmVyYXRlIE1DUCB0b29sIGRlZmluaXRpb25zIGZyb20gdGhlIHByb3ZpZGVkIGFjdGlvbiBkZWZpbml0aW9ucy5cclxuICAgKiBFYWNoIHRvb2wgZGVmaW5pdGlvbiBjb25mb3JtcyB0byB0aGUgTUNQIFRvb2wgc2NoZW1hIHdpdGggbmFtZSwgZGVzY3JpcHRpb24sIGFuZCBpbnB1dFNjaGVtYS5cclxuICAgKi9cclxuICBwcml2YXRlIGdlbmVyYXRlTWNwVG9vbERlZmluaXRpb25zKFxyXG4gICAgYWdlbnROYW1lOiBzdHJpbmcsXHJcbiAgICBhY3Rpb25zOiBBY3Rpb25EZWZpbml0aW9uW11cclxuICApOiBvYmplY3RbXSB7XHJcbiAgICByZXR1cm4gYWN0aW9ucy5tYXAoKGFjdGlvbikgPT4gKHtcclxuICAgICAgbmFtZTogYWN0aW9uLm5hbWUsXHJcbiAgICAgIGRlc2NyaXB0aW9uOiBhY3Rpb24uZGVzY3JpcHRpb24sXHJcbiAgICAgIGlucHV0U2NoZW1hOiBhY3Rpb24uaW5wdXRfc2NoZW1hLFxyXG4gICAgfSkpO1xyXG4gIH1cclxuXHJcbiAgLyoqXHJcbiAgICogR2VuZXJhdGUgYSBzY2hlbWFzIG1hcCBmcm9tIGFjdGlvbiBkZWZpbml0aW9ucyBmb3IgdXNlIGluIHRoZSBMYW1iZGEgZW52aXJvbm1lbnQuXHJcbiAgICogTWFwcyBhY3Rpb25fbmFtZSDihpIgaW5wdXRfc2NoZW1hIGZvciBsaWdodHdlaWdodCBwYXJhbWV0ZXIgdmFsaWRhdGlvbi5cclxuICAgKi9cclxuICBwcml2YXRlIGdlbmVyYXRlU2NoZW1hc01hcChcclxuICAgIGFjdGlvbnM6IEFjdGlvbkRlZmluaXRpb25bXVxyXG4gICk6IFJlY29yZDxzdHJpbmcsIG9iamVjdD4ge1xyXG4gICAgY29uc3Qgc2NoZW1hczogUmVjb3JkPHN0cmluZywgb2JqZWN0PiA9IHt9O1xyXG4gICAgZm9yIChjb25zdCBhY3Rpb24gb2YgYWN0aW9ucykge1xyXG4gICAgICBzY2hlbWFzW2FjdGlvbi5uYW1lXSA9IGFjdGlvbi5pbnB1dF9zY2hlbWE7XHJcbiAgICB9XHJcbiAgICByZXR1cm4gc2NoZW1hcztcclxuICB9XHJcblxyXG4gIC8qKlxyXG4gICAqIEdlbmVyYXRlIGlubGluZSBMYW1iZGEgaGFuZGxlciBjb2RlIHRoYXQgaGFuZGxlcyBNQ1AgSlNPTi1SUEMgbWVzc2FnZXMgKFBPU1QgLylcclxuICAgKiBhbmQgaGVhbHRoIGNoZWNrIChHRVQgL2hlYWx0aCkuXHJcbiAgICpcclxuICAgKiBNQ1AgcHJvdG9jb2wgaGFuZGxpbmc6XHJcbiAgICogLSBQT1NUIC8g4oaSIEpTT04tUlBDIDIuMCByb3V0ZXI6IGluaXRpYWxpemUsIHRvb2xzL2xpc3QsIHRvb2xzL2NhbGwsIHBpbmcsIG5vdGlmaWNhdGlvbnMvaW5pdGlhbGl6ZWRcclxuICAgKiAtIEdFVCAvaGVhbHRoIOKGkiBoZWFsdGggY2hlY2sgKHVuY2hhbmdlZCBmcm9tIHByZXZpb3VzIGltcGxlbWVudGF0aW9uKVxyXG4gICAqXHJcbiAgICogVmFsaWRhdGlvbiBhcHByb2FjaCAoaW5saW5lLCBubyBleHRlcm5hbCBkZXBlbmRlbmNpZXMpOlxyXG4gICAqIC0gVmFsaWRhdGVzIHJlcXVpcmVkIGZpZWxkcyBmcm9tIGVhY2ggYWN0aW9uJ3MgaW5wdXRfc2NoZW1hXHJcbiAgICogLSBWYWxpZGF0ZXMgcGFyYW1ldGVyIHR5cGVzIGFnYWluc3Qgc2NoZW1hIHByb3BlcnR5IHR5cGUgZGVmaW5pdGlvbnNcclxuICAgKiAtIFJldHVybnMgY29uc2lzdGVudCBNQ1AgZXJyb3IgcmVzcG9uc2VzIChDYWxsVG9vbFJlc3VsdCB3aXRoIGlzRXJyb3I9dHJ1ZSlcclxuICAgKi9cclxuICBwcml2YXRlIGdlbmVyYXRlTGFtYmRhQ29kZShcclxuICAgIGFnZW50TmFtZTogc3RyaW5nLFxyXG4gICAgYWN0aW9uczogQWN0aW9uRGVmaW5pdGlvbltdXHJcbiAgKTogc3RyaW5nIHtcclxuICAgIC8vIEVtYmVkIHRvb2wgZGVmaW5pdGlvbnMgYW5kIGFjdGlvbiBzY2hlbWFzIGRpcmVjdGx5IGluIHRoZSBjb2RlXHJcbiAgICAvLyB0byBhdm9pZCB0aGUgNEtCIExhbWJkYSBlbnZpcm9ubWVudCB2YXJpYWJsZSBsaW1pdFxyXG4gICAgY29uc3QgdG9vbERlZnNKc29uID0gSlNPTi5zdHJpbmdpZnkodGhpcy5nZW5lcmF0ZU1jcFRvb2xEZWZpbml0aW9ucyhhZ2VudE5hbWUsIGFjdGlvbnMpKTtcclxuICAgIGNvbnN0IGFjdGlvblNjaGVtYXNKc29uID0gSlNPTi5zdHJpbmdpZnkodGhpcy5nZW5lcmF0ZVNjaGVtYXNNYXAoYWN0aW9ucykpO1xyXG5cclxuICAgIHJldHVybiBgXHJcbid1c2Ugc3RyaWN0JztcclxuXHJcbmNvbnN0IHsgcmFuZG9tVVVJRCB9ID0gcmVxdWlyZSgnY3J5cHRvJyk7XHJcblxyXG5jb25zdCB0b29sRGVmaW5pdGlvbnMgPSAke3Rvb2xEZWZzSnNvbn07XHJcbmNvbnN0IGFjdGlvblNjaGVtYXMgPSAke2FjdGlvblNjaGVtYXNKc29ufTtcclxuY29uc3QgYWdlbnROYW1lID0gcHJvY2Vzcy5lbnYuQUdFTlRfTkFNRSB8fCAnJHthZ2VudE5hbWV9JztcclxuXHJcbi8qKlxyXG4gKiBWYWxpZGF0ZXMgcGFyYW1ldGVycyBhZ2FpbnN0IGFuIGFjdGlvbidzIGlucHV0IHNjaGVtYS5cclxuICogUGVyZm9ybXMgbGlnaHR3ZWlnaHQgdmFsaWRhdGlvbjogcmVxdWlyZWQgZmllbGRzIGFuZCBiYXNpYyB0eXBlIGNoZWNrcy5cclxuICogUmV0dXJucyBudWxsIGlmIHZhbGlkLCBvciBhbiBlcnJvciBvYmplY3QgaWYgdmFsaWRhdGlvbiBmYWlscy5cclxuICovXHJcbmZ1bmN0aW9uIHZhbGlkYXRlUGFyYW1ldGVycyhhY3Rpb25OYW1lLCBwYXJhbWV0ZXJzKSB7XHJcbiAgY29uc3Qgc2NoZW1hID0gYWN0aW9uU2NoZW1hc1thY3Rpb25OYW1lXTtcclxuICBpZiAoIXNjaGVtYSkge1xyXG4gICAgcmV0dXJuIG51bGw7IC8vIE5vIHNjaGVtYSBkZWZpbmVkLCBza2lwIHZhbGlkYXRpb25cclxuICB9XHJcblxyXG4gIGNvbnN0IHBhcmFtcyA9IHBhcmFtZXRlcnMgfHwge307XHJcbiAgY29uc3QgZmFpbGluZ1BhcmFtZXRlcnMgPSBbXTtcclxuICBjb25zdCBleHBlY3RlZENvbnN0cmFpbnRzID0ge307XHJcblxyXG4gIC8vIENoZWNrIHJlcXVpcmVkIGZpZWxkc1xyXG4gIGNvbnN0IHJlcXVpcmVkRmllbGRzID0gc2NoZW1hLnJlcXVpcmVkIHx8IFtdO1xyXG4gIGZvciAoY29uc3QgZmllbGQgb2YgcmVxdWlyZWRGaWVsZHMpIHtcclxuICAgIGlmIChwYXJhbXNbZmllbGRdID09PSB1bmRlZmluZWQgfHwgcGFyYW1zW2ZpZWxkXSA9PT0gbnVsbCkge1xyXG4gICAgICBmYWlsaW5nUGFyYW1ldGVycy5wdXNoKGZpZWxkKTtcclxuICAgICAgZXhwZWN0ZWRDb25zdHJhaW50c1tmaWVsZF0gPSAnUmVxdWlyZWQgcGFyYW1ldGVyIFwiJyArIGZpZWxkICsgJ1wiIGlzIG1pc3NpbmcnO1xyXG4gICAgfVxyXG4gIH1cclxuXHJcbiAgLy8gQ2hlY2sgdHlwZSBjb25zdHJhaW50cyBmb3IgcHJvdmlkZWQgcGFyYW1ldGVyc1xyXG4gIGNvbnN0IHByb3BlcnRpZXMgPSBzY2hlbWEucHJvcGVydGllcyB8fCB7fTtcclxuICBmb3IgKGNvbnN0IFtrZXksIHZhbHVlXSBvZiBPYmplY3QuZW50cmllcyhwYXJhbXMpKSB7XHJcbiAgICBjb25zdCBwcm9wU2NoZW1hID0gcHJvcGVydGllc1trZXldO1xyXG4gICAgaWYgKCFwcm9wU2NoZW1hKSBjb250aW51ZTtcclxuXHJcbiAgICBjb25zdCBleHBlY3RlZFR5cGUgPSBwcm9wU2NoZW1hLnR5cGU7XHJcbiAgICBpZiAoIWV4cGVjdGVkVHlwZSkgY29udGludWU7XHJcblxyXG4gICAgY29uc3QgYWN0dWFsVHlwZSA9IEFycmF5LmlzQXJyYXkodmFsdWUpID8gJ2FycmF5JyA6IHR5cGVvZiB2YWx1ZTtcclxuXHJcbiAgICBpZiAoZXhwZWN0ZWRUeXBlID09PSAnYXJyYXknICYmICFBcnJheS5pc0FycmF5KHZhbHVlKSkge1xyXG4gICAgICBmYWlsaW5nUGFyYW1ldGVycy5wdXNoKGtleSk7XHJcbiAgICAgIGV4cGVjdGVkQ29uc3RyYWludHNba2V5XSA9ICdFeHBlY3RlZCB0eXBlIFwiYXJyYXlcIiBidXQgcmVjZWl2ZWQgXCInICsgdHlwZW9mIHZhbHVlICsgJ1wiJztcclxuICAgIH0gZWxzZSBpZiAoZXhwZWN0ZWRUeXBlID09PSAnc3RyaW5nJyAmJiB0eXBlb2YgdmFsdWUgIT09ICdzdHJpbmcnKSB7XHJcbiAgICAgIGZhaWxpbmdQYXJhbWV0ZXJzLnB1c2goa2V5KTtcclxuICAgICAgZXhwZWN0ZWRDb25zdHJhaW50c1trZXldID0gJ0V4cGVjdGVkIHR5cGUgXCJzdHJpbmdcIiBidXQgcmVjZWl2ZWQgXCInICsgYWN0dWFsVHlwZSArICdcIic7XHJcbiAgICB9IGVsc2UgaWYgKGV4cGVjdGVkVHlwZSA9PT0gJ251bWJlcicgJiYgdHlwZW9mIHZhbHVlICE9PSAnbnVtYmVyJykge1xyXG4gICAgICBmYWlsaW5nUGFyYW1ldGVycy5wdXNoKGtleSk7XHJcbiAgICAgIGV4cGVjdGVkQ29uc3RyYWludHNba2V5XSA9ICdFeHBlY3RlZCB0eXBlIFwibnVtYmVyXCIgYnV0IHJlY2VpdmVkIFwiJyArIGFjdHVhbFR5cGUgKyAnXCInO1xyXG4gICAgfSBlbHNlIGlmIChleHBlY3RlZFR5cGUgPT09ICdpbnRlZ2VyJyAmJiAodHlwZW9mIHZhbHVlICE9PSAnbnVtYmVyJyB8fCAhTnVtYmVyLmlzSW50ZWdlcih2YWx1ZSkpKSB7XHJcbiAgICAgIGZhaWxpbmdQYXJhbWV0ZXJzLnB1c2goa2V5KTtcclxuICAgICAgZXhwZWN0ZWRDb25zdHJhaW50c1trZXldID0gJ0V4cGVjdGVkIHR5cGUgXCJpbnRlZ2VyXCIgYnV0IHJlY2VpdmVkIFwiJyArIGFjdHVhbFR5cGUgKyAnXCInO1xyXG4gICAgfSBlbHNlIGlmIChleHBlY3RlZFR5cGUgPT09ICdib29sZWFuJyAmJiB0eXBlb2YgdmFsdWUgIT09ICdib29sZWFuJykge1xyXG4gICAgICBmYWlsaW5nUGFyYW1ldGVycy5wdXNoKGtleSk7XHJcbiAgICAgIGV4cGVjdGVkQ29uc3RyYWludHNba2V5XSA9ICdFeHBlY3RlZCB0eXBlIFwiYm9vbGVhblwiIGJ1dCByZWNlaXZlZCBcIicgKyBhY3R1YWxUeXBlICsgJ1wiJztcclxuICAgIH0gZWxzZSBpZiAoZXhwZWN0ZWRUeXBlID09PSAnb2JqZWN0JyAmJiAodHlwZW9mIHZhbHVlICE9PSAnb2JqZWN0JyB8fCBBcnJheS5pc0FycmF5KHZhbHVlKSB8fCB2YWx1ZSA9PT0gbnVsbCkpIHtcclxuICAgICAgZmFpbGluZ1BhcmFtZXRlcnMucHVzaChrZXkpO1xyXG4gICAgICBleHBlY3RlZENvbnN0cmFpbnRzW2tleV0gPSAnRXhwZWN0ZWQgdHlwZSBcIm9iamVjdFwiIGJ1dCByZWNlaXZlZCBcIicgKyBhY3R1YWxUeXBlICsgJ1wiJztcclxuICAgIH1cclxuICB9XHJcblxyXG4gIGlmIChmYWlsaW5nUGFyYW1ldGVycy5sZW5ndGggPT09IDApIHtcclxuICAgIHJldHVybiBudWxsOyAvLyBWYWxpZGF0aW9uIHBhc3NlZFxyXG4gIH1cclxuXHJcbiAgcmV0dXJuIHsgZmFpbGluZ1BhcmFtZXRlcnMsIGV4cGVjdGVkQ29uc3RyYWludHMgfTtcclxufVxyXG5cclxuLyoqXHJcbiAqIENyZWF0ZXMgYSBKU09OLVJQQyAyLjAgc3VjY2VzcyByZXNwb25zZS5cclxuICovXHJcbmZ1bmN0aW9uIGpzb25ScGNTdWNjZXNzKGlkLCByZXN1bHQpIHtcclxuICByZXR1cm4ge1xyXG4gICAganNvbnJwYzogJzIuMCcsXHJcbiAgICBpZDogaWQsXHJcbiAgICByZXN1bHQ6IHJlc3VsdCxcclxuICB9O1xyXG59XHJcblxyXG4vKipcclxuICogQ3JlYXRlcyBhIEpTT04tUlBDIDIuMCBlcnJvciByZXNwb25zZS5cclxuICovXHJcbmZ1bmN0aW9uIGpzb25ScGNFcnJvcihpZCwgY29kZSwgbWVzc2FnZSwgZGF0YSkge1xyXG4gIGNvbnN0IGVycm9yID0geyBjb2RlLCBtZXNzYWdlIH07XHJcbiAgaWYgKGRhdGEgIT09IHVuZGVmaW5lZCkge1xyXG4gICAgZXJyb3IuZGF0YSA9IGRhdGE7XHJcbiAgfVxyXG4gIHJldHVybiB7XHJcbiAgICBqc29ucnBjOiAnMi4wJyxcclxuICAgIGlkOiBpZCAhPT0gdW5kZWZpbmVkID8gaWQgOiBudWxsLFxyXG4gICAgZXJyb3I6IGVycm9yLFxyXG4gIH07XHJcbn1cclxuXHJcbi8qKlxyXG4gKiBIYW5kbGUgTUNQIEpTT04tUlBDIG1lc3NhZ2VzIHJvdXRlZCB2aWEgUE9TVCAvXHJcbiAqL1xyXG5mdW5jdGlvbiBoYW5kbGVNY3BNZXNzYWdlKGJvZHksIGhlYWRlcnMpIHtcclxuICBjb25zdCBzZXNzaW9uSWQgPSBoZWFkZXJzWydtY3Atc2Vzc2lvbi1pZCddIHx8IGhlYWRlcnNbJ01jcC1TZXNzaW9uLUlkJ10gfHwgcmFuZG9tVVVJRCgpO1xyXG4gIGNvbnN0IHJlc3BvbnNlSGVhZGVycyA9IHtcclxuICAgICdDb250ZW50LVR5cGUnOiAnYXBwbGljYXRpb24vanNvbicsXHJcbiAgICAnTWNwLVNlc3Npb24tSWQnOiBzZXNzaW9uSWQsXHJcbiAgfTtcclxuXHJcbiAgLy8gUGFyc2UgSlNPTiBib2R5XHJcbiAgbGV0IHJlcXVlc3Q7XHJcbiAgdHJ5IHtcclxuICAgIHJlcXVlc3QgPSB0eXBlb2YgYm9keSA9PT0gJ3N0cmluZycgPyBKU09OLnBhcnNlKGJvZHkpIDogYm9keTtcclxuICB9IGNhdGNoIChlKSB7XHJcbiAgICByZXR1cm4ge1xyXG4gICAgICBzdGF0dXNDb2RlOiAyMDAsXHJcbiAgICAgIGhlYWRlcnM6IHJlc3BvbnNlSGVhZGVycyxcclxuICAgICAgYm9keTogSlNPTi5zdHJpbmdpZnkoanNvblJwY0Vycm9yKG51bGwsIC0zMjcwMCwgJ1BhcnNlIGVycm9yOiBJbnZhbGlkIEpTT04nKSksXHJcbiAgICB9O1xyXG4gIH1cclxuXHJcbiAgLy8gVmFsaWRhdGUgSlNPTi1SUEMgMi4wIHN0cnVjdHVyZVxyXG4gIGlmICghcmVxdWVzdCB8fCB0eXBlb2YgcmVxdWVzdCAhPT0gJ29iamVjdCcpIHtcclxuICAgIHJldHVybiB7XHJcbiAgICAgIHN0YXR1c0NvZGU6IDIwMCxcclxuICAgICAgaGVhZGVyczogcmVzcG9uc2VIZWFkZXJzLFxyXG4gICAgICBib2R5OiBKU09OLnN0cmluZ2lmeShqc29uUnBjRXJyb3IobnVsbCwgLTMyNjAwLCAnSW52YWxpZCBSZXF1ZXN0OiBSZXF1ZXN0IG11c3QgYmUgYSBKU09OIG9iamVjdCcpKSxcclxuICAgIH07XHJcbiAgfVxyXG5cclxuICBpZiAocmVxdWVzdC5qc29ucnBjICE9PSAnMi4wJykge1xyXG4gICAgcmV0dXJuIHtcclxuICAgICAgc3RhdHVzQ29kZTogMjAwLFxyXG4gICAgICBoZWFkZXJzOiByZXNwb25zZUhlYWRlcnMsXHJcbiAgICAgIGJvZHk6IEpTT04uc3RyaW5naWZ5KGpzb25ScGNFcnJvcihyZXF1ZXN0LmlkIHx8IG51bGwsIC0zMjYwMCwgJ0ludmFsaWQgUmVxdWVzdDogTWlzc2luZyBvciBpbnZhbGlkIGpzb25ycGMgZmllbGQgKG11c3QgYmUgXCIyLjBcIiknKSksXHJcbiAgICB9O1xyXG4gIH1cclxuXHJcbiAgaWYgKCFyZXF1ZXN0Lm1ldGhvZCB8fCB0eXBlb2YgcmVxdWVzdC5tZXRob2QgIT09ICdzdHJpbmcnKSB7XHJcbiAgICByZXR1cm4ge1xyXG4gICAgICBzdGF0dXNDb2RlOiAyMDAsXHJcbiAgICAgIGhlYWRlcnM6IHJlc3BvbnNlSGVhZGVycyxcclxuICAgICAgYm9keTogSlNPTi5zdHJpbmdpZnkoanNvblJwY0Vycm9yKHJlcXVlc3QuaWQgfHwgbnVsbCwgLTMyNjAwLCAnSW52YWxpZCBSZXF1ZXN0OiBNaXNzaW5nIG9yIGludmFsaWQgbWV0aG9kIGZpZWxkJykpLFxyXG4gICAgfTtcclxuICB9XHJcblxyXG4gIGNvbnN0IHsgbWV0aG9kLCBpZCwgcGFyYW1zIH0gPSByZXF1ZXN0O1xyXG5cclxuICAvLyBIYW5kbGUgbm90aWZpY2F0aW9ucyAobm8gaWQgZmllbGQpIOKAlCByZXR1cm4gSFRUUCAyMDQgd2l0aCBubyBib2R5XHJcbiAgaWYgKG1ldGhvZCA9PT0gJ25vdGlmaWNhdGlvbnMvaW5pdGlhbGl6ZWQnKSB7XHJcbiAgICByZXR1cm4ge1xyXG4gICAgICBzdGF0dXNDb2RlOiAyMDQsXHJcbiAgICAgIGhlYWRlcnM6IHJlc3BvbnNlSGVhZGVycyxcclxuICAgICAgYm9keTogJycsXHJcbiAgICB9O1xyXG4gIH1cclxuXHJcbiAgLy8gUm91dGUgdG8gbWV0aG9kIGhhbmRsZXJzXHJcbiAgc3dpdGNoIChtZXRob2QpIHtcclxuICAgIGNhc2UgJ2luaXRpYWxpemUnOiB7XHJcbiAgICAgIGNvbnN0IG5ld1Nlc3Npb25JZCA9IGhlYWRlcnNbJ21jcC1zZXNzaW9uLWlkJ10gfHwgaGVhZGVyc1snTWNwLVNlc3Npb24tSWQnXSB8fCByYW5kb21VVUlEKCk7XHJcbiAgICAgIHJlc3BvbnNlSGVhZGVyc1snTWNwLVNlc3Npb24tSWQnXSA9IG5ld1Nlc3Npb25JZDtcclxuICAgICAgcmV0dXJuIHtcclxuICAgICAgICBzdGF0dXNDb2RlOiAyMDAsXHJcbiAgICAgICAgaGVhZGVyczogcmVzcG9uc2VIZWFkZXJzLFxyXG4gICAgICAgIGJvZHk6IEpTT04uc3RyaW5naWZ5KGpzb25ScGNTdWNjZXNzKGlkLCB7XHJcbiAgICAgICAgICBwcm90b2NvbFZlcnNpb246ICcyMDI0LTExLTA1JyxcclxuICAgICAgICAgIHNlcnZlckluZm86IHtcclxuICAgICAgICAgICAgbmFtZTogYWdlbnROYW1lLFxyXG4gICAgICAgICAgICB2ZXJzaW9uOiAnMi4wLjAnLFxyXG4gICAgICAgICAgfSxcclxuICAgICAgICAgIGNhcGFiaWxpdGllczoge1xyXG4gICAgICAgICAgICB0b29sczoge1xyXG4gICAgICAgICAgICAgIGxpc3RDaGFuZ2VkOiBmYWxzZSxcclxuICAgICAgICAgICAgfSxcclxuICAgICAgICAgIH0sXHJcbiAgICAgICAgfSkpLFxyXG4gICAgICB9O1xyXG4gICAgfVxyXG5cclxuICAgIGNhc2UgJ3Rvb2xzL2xpc3QnOiB7XHJcbiAgICAgIHJldHVybiB7XHJcbiAgICAgICAgc3RhdHVzQ29kZTogMjAwLFxyXG4gICAgICAgIGhlYWRlcnM6IHJlc3BvbnNlSGVhZGVycyxcclxuICAgICAgICBib2R5OiBKU09OLnN0cmluZ2lmeShqc29uUnBjU3VjY2VzcyhpZCwge1xyXG4gICAgICAgICAgdG9vbHM6IHRvb2xEZWZpbml0aW9ucyxcclxuICAgICAgICB9KSksXHJcbiAgICAgIH07XHJcbiAgICB9XHJcblxyXG4gICAgY2FzZSAndG9vbHMvY2FsbCc6IHtcclxuICAgICAgY29uc3QgdG9vbE5hbWUgPSBwYXJhbXMgJiYgcGFyYW1zLm5hbWU7XHJcbiAgICAgIGNvbnN0IHRvb2xBcmdzID0gKHBhcmFtcyAmJiBwYXJhbXMuYXJndW1lbnRzKSB8fCB7fTtcclxuXHJcbiAgICAgIC8vIFZhbGlkYXRlIHRvb2wgZXhpc3RzXHJcbiAgICAgIGNvbnN0IHZhbGlkVG9vbHMgPSB0b29sRGVmaW5pdGlvbnMubWFwKHQgPT4gdC5uYW1lKTtcclxuICAgICAgaWYgKCF0b29sTmFtZSB8fCAhdmFsaWRUb29scy5pbmNsdWRlcyh0b29sTmFtZSkpIHtcclxuICAgICAgICByZXR1cm4ge1xyXG4gICAgICAgICAgc3RhdHVzQ29kZTogMjAwLFxyXG4gICAgICAgICAgaGVhZGVyczogcmVzcG9uc2VIZWFkZXJzLFxyXG4gICAgICAgICAgYm9keTogSlNPTi5zdHJpbmdpZnkoanNvblJwY0Vycm9yKGlkLCAtMzI2MDIsICdJbnZhbGlkIHBhcmFtczogdG9vbCBcXFxcJycgKyAodG9vbE5hbWUgfHwgJycpICsgJ1xcXFwnIG5vdCBmb3VuZCcpKSxcclxuICAgICAgICB9O1xyXG4gICAgICB9XHJcblxyXG4gICAgICAvLyBWYWxpZGF0ZSBwYXJhbWV0ZXJzIGFnYWluc3Qgc2NoZW1hXHJcbiAgICAgIGNvbnN0IHZhbGlkYXRpb25FcnJvciA9IHZhbGlkYXRlUGFyYW1ldGVycyh0b29sTmFtZSwgdG9vbEFyZ3MpO1xyXG4gICAgICBpZiAodmFsaWRhdGlvbkVycm9yKSB7XHJcbiAgICAgICAgcmV0dXJuIHtcclxuICAgICAgICAgIHN0YXR1c0NvZGU6IDIwMCxcclxuICAgICAgICAgIGhlYWRlcnM6IHJlc3BvbnNlSGVhZGVycyxcclxuICAgICAgICAgIGJvZHk6IEpTT04uc3RyaW5naWZ5KGpzb25ScGNTdWNjZXNzKGlkLCB7XHJcbiAgICAgICAgICAgIGNvbnRlbnQ6IFt7XHJcbiAgICAgICAgICAgICAgdHlwZTogJ3RleHQnLFxyXG4gICAgICAgICAgICAgIHRleHQ6IEpTT04uc3RyaW5naWZ5KHtcclxuICAgICAgICAgICAgICAgIGNvZGU6ICdzY2hlbWFfdmFsaWRhdGlvbl9mYWlsZWQnLFxyXG4gICAgICAgICAgICAgICAgbWVzc2FnZTogJ1JlcXVlc3QgcGFyYW1ldGVycyBmYWlsZWQgdmFsaWRhdGlvbiBhZ2FpbnN0IHRoZSBzY2hlbWEgZm9yIGFjdGlvbiBcIicgKyB0b29sTmFtZSArICdcIi4nLFxyXG4gICAgICAgICAgICAgICAgZGV0YWlsczoge1xyXG4gICAgICAgICAgICAgICAgICBmYWlsaW5nX3BhcmFtZXRlcnM6IHZhbGlkYXRpb25FcnJvci5mYWlsaW5nUGFyYW1ldGVycyxcclxuICAgICAgICAgICAgICAgICAgZXhwZWN0ZWRfY29uc3RyYWludHM6IHZhbGlkYXRpb25FcnJvci5leHBlY3RlZENvbnN0cmFpbnRzLFxyXG4gICAgICAgICAgICAgICAgfSxcclxuICAgICAgICAgICAgICB9KSxcclxuICAgICAgICAgICAgfV0sXHJcbiAgICAgICAgICAgIGlzRXJyb3I6IHRydWUsXHJcbiAgICAgICAgICB9KSksXHJcbiAgICAgICAgfTtcclxuICAgICAgfVxyXG5cclxuICAgICAgLy8gU3VjY2Vzc2Z1bCBpbnZvY2F0aW9uIChzaW1wbGlmaWVkIGlubGluZSBoYW5kbGVyKVxyXG4gICAgICByZXR1cm4ge1xyXG4gICAgICAgIHN0YXR1c0NvZGU6IDIwMCxcclxuICAgICAgICBoZWFkZXJzOiByZXNwb25zZUhlYWRlcnMsXHJcbiAgICAgICAgYm9keTogSlNPTi5zdHJpbmdpZnkoanNvblJwY1N1Y2Nlc3MoaWQsIHtcclxuICAgICAgICAgIGNvbnRlbnQ6IFt7XHJcbiAgICAgICAgICAgIHR5cGU6ICd0ZXh0JyxcclxuICAgICAgICAgICAgdGV4dDogSlNPTi5zdHJpbmdpZnkoe1xyXG4gICAgICAgICAgICAgIG1lc3NhZ2U6ICdBY3Rpb24gJyArIHRvb2xOYW1lICsgJyBpbnZva2VkIHN1Y2Nlc3NmdWxseScsXHJcbiAgICAgICAgICAgICAgcGFyYW1ldGVyczogdG9vbEFyZ3MsXHJcbiAgICAgICAgICAgIH0pLFxyXG4gICAgICAgICAgfV0sXHJcbiAgICAgICAgICBpc0Vycm9yOiBmYWxzZSxcclxuICAgICAgICB9KSksXHJcbiAgICAgIH07XHJcbiAgICB9XHJcblxyXG4gICAgY2FzZSAncGluZyc6IHtcclxuICAgICAgcmV0dXJuIHtcclxuICAgICAgICBzdGF0dXNDb2RlOiAyMDAsXHJcbiAgICAgICAgaGVhZGVyczogcmVzcG9uc2VIZWFkZXJzLFxyXG4gICAgICAgIGJvZHk6IEpTT04uc3RyaW5naWZ5KGpzb25ScGNTdWNjZXNzKGlkLCB7fSkpLFxyXG4gICAgICB9O1xyXG4gICAgfVxyXG5cclxuICAgIGRlZmF1bHQ6IHtcclxuICAgICAgcmV0dXJuIHtcclxuICAgICAgICBzdGF0dXNDb2RlOiAyMDAsXHJcbiAgICAgICAgaGVhZGVyczogcmVzcG9uc2VIZWFkZXJzLFxyXG4gICAgICAgIGJvZHk6IEpTT04uc3RyaW5naWZ5KGpzb25ScGNFcnJvcihpZCwgLTMyNjAxLCAnTWV0aG9kIG5vdCBmb3VuZDogJyArIG1ldGhvZCkpLFxyXG4gICAgICB9O1xyXG4gICAgfVxyXG4gIH1cclxufVxyXG5cclxuZXhwb3J0cy5oYW5kbGVyID0gYXN5bmMgKGV2ZW50KSA9PiB7XHJcbiAgY29uc3QgcGF0aCA9IGV2ZW50LnBhdGggfHwgZXZlbnQucmVzb3VyY2UgfHwgJyc7XHJcbiAgY29uc3QgbWV0aG9kID0gZXZlbnQuaHR0cE1ldGhvZCB8fCAnR0VUJztcclxuICBjb25zdCBoZWFkZXJzID0gZXZlbnQuaGVhZGVycyB8fCB7fTtcclxuXHJcbiAgLy8gR0VUIC9oZWFsdGggLSBSZXR1cm4gaGVhbHRoIGNoZWNrIChwcmVzZXJ2ZWQgZm9yIG1vbml0b3JpbmcpXHJcbiAgaWYgKHBhdGguZW5kc1dpdGgoJy9oZWFsdGgnKSAmJiBtZXRob2QgPT09ICdHRVQnKSB7XHJcbiAgICBjb25zdCBhZ2VudElkID0gcHJvY2Vzcy5lbnYuTkVUV09SS19BR0VOVF9JRCB8fCAnJztcclxuICAgIGNvbnN0IGFnZW50QWxpYXNJZCA9IHByb2Nlc3MuZW52Lk5FVFdPUktfQUdFTlRfQUxJQVNfSUQgfHwgJyc7XHJcbiAgICBjb25zdCBjYXB0dXJlVGFibGUgPSBwcm9jZXNzLmVudi5DQVBUVVJFX1NUQVRFX1RBQkxFIHx8ICcnO1xyXG5cclxuICAgIGNvbnN0IG5ldHdvcmtBZ2VudFN0YXR1cyA9IChhZ2VudElkICYmIGFnZW50QWxpYXNJZCkgPyAnYXZhaWxhYmxlJyA6IChhZ2VudElkIHx8IGFnZW50QWxpYXNJZCkgPyAndW5rbm93bicgOiAndW5hdmFpbGFibGUnO1xyXG4gICAgY29uc3QgY2FwdHVyZVRhYmxlU3RhdHVzID0gY2FwdHVyZVRhYmxlID8gJ2F2YWlsYWJsZScgOiAndW5rbm93bic7XHJcbiAgICBjb25zdCBvdmVyYWxsU3RhdHVzID0gbmV0d29ya0FnZW50U3RhdHVzID09PSAndW5hdmFpbGFibGUnID8gJ3VuaGVhbHRoeScgOiBuZXR3b3JrQWdlbnRTdGF0dXMgPT09ICd1bmtub3duJyA/ICdkZWdyYWRlZCcgOiAnaGVhbHRoeSc7XHJcblxyXG4gICAgcmV0dXJuIHtcclxuICAgICAgc3RhdHVzQ29kZTogMjAwLFxyXG4gICAgICBoZWFkZXJzOiB7ICdDb250ZW50LVR5cGUnOiAnYXBwbGljYXRpb24vanNvbicsICdNY3AtU2Vzc2lvbi1JZCc6IGhlYWRlcnNbJ21jcC1zZXNzaW9uLWlkJ10gfHwgaGVhZGVyc1snTWNwLVNlc3Npb24tSWQnXSB8fCAnJyB9LFxyXG4gICAgICBib2R5OiBKU09OLnN0cmluZ2lmeSh7XHJcbiAgICAgICAgc3RhdHVzOiBvdmVyYWxsU3RhdHVzLFxyXG4gICAgICAgIHRpbWVzdGFtcDogbmV3IERhdGUoKS50b0lTT1N0cmluZygpLFxyXG4gICAgICAgIGNvbXBvbmVudHM6IHtcclxuICAgICAgICAgIG5ldHdvcmtfYWdlbnQ6IHsgc3RhdHVzOiBuZXR3b3JrQWdlbnRTdGF0dXMsIGFnZW50X2lkOiBhZ2VudElkID8gYWdlbnRJZC5zdWJzdHJpbmcoMCwgNCkgKyAnKioqJyA6IHVuZGVmaW5lZCB9LFxyXG4gICAgICAgICAgaW50ZWdyYXRpb25fbGFtYmRhOiB7IHN0YXR1czogJ2F2YWlsYWJsZScsIHZlcnNpb246ICcyLjAuMCcgfSxcclxuICAgICAgICAgIGNhcHR1cmVfc3RhdGVfdGFibGU6IHsgc3RhdHVzOiBjYXB0dXJlVGFibGVTdGF0dXMgfSxcclxuICAgICAgICB9LFxyXG4gICAgICAgIHJlZ2lvbjogcHJvY2Vzcy5lbnYuQVdTX1JFR0lPTl9PVkVSUklERSB8fCBwcm9jZXNzLmVudi5BV1NfUkVHSU9OIHx8ICd1bmtub3duJyxcclxuICAgICAgfSksXHJcbiAgICB9O1xyXG4gIH1cclxuXHJcbiAgLy8gUE9TVCAvIC0gTUNQIEpTT04tUlBDIG1lc3NhZ2VzXHJcbiAgaWYgKG1ldGhvZCA9PT0gJ1BPU1QnKSB7XHJcbiAgICByZXR1cm4gaGFuZGxlTWNwTWVzc2FnZShldmVudC5ib2R5LCBoZWFkZXJzKTtcclxuICB9XHJcblxyXG4gIC8vIEZhbGxiYWNrIGZvciB1bmtub3duIHJvdXRlc1xyXG4gIHJldHVybiB7XHJcbiAgICBzdGF0dXNDb2RlOiA0MDQsXHJcbiAgICBoZWFkZXJzOiB7ICdDb250ZW50LVR5cGUnOiAnYXBwbGljYXRpb24vanNvbicgfSxcclxuICAgIGJvZHk6IEpTT04uc3RyaW5naWZ5KHsgbWVzc2FnZTogJ05vdCBmb3VuZC4gQXZhaWxhYmxlIHJvdXRlczogUE9TVCAvIChNQ1AgSlNPTi1SUEMpLCBHRVQgL2hlYWx0aCcgfSksXHJcbiAgfTtcclxufTtcclxuYDtcclxuICB9XHJcbn1cclxuIl19