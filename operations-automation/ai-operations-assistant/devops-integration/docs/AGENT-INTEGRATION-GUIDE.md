# Agent Integration Template Guide

Connect any GOAT sub-agent to AWS DevOps Agent using the reusable `AgentIntegrationTemplate` CDK construct. This guide walks through integrating a hypothetical **Security Agent** as a complete example.

## What the Template Provides

The `AgentIntegrationTemplate` is a parameterized CDK construct that produces a fully functional MCP (Model Context Protocol) server endpoint from just your action definitions:

- **API Gateway** with `POST /` for MCP JSON-RPC 2.0 messages and `GET /health` for monitoring
- **MCP protocol handler** supporting `initialize`, `tools/list`, `tools/call`, `ping`, and `notifications/initialized`
- **Auto-registration** with DevOps Agent via `AWS::DevOpsAgent::Service` CloudFormation resource (type `mcpserversigv4`)
- **IAM Role** for DevOps Agent with `aidevops.amazonaws.com` trust + confused deputy protection (scoped to `execute-api:Invoke`)
- **Auto-generated MCP tool definitions** from your action definitions (name, description with workflow context, inputSchema)
- **Hidden tool filtering** for actions not supported by the underlying agent runtime
- **Health check endpoint** reporting component status
- **Standardized JSON-RPC 2.0 response envelope** with `CallToolResult` format

## Prerequisites

- Node.js 18+ and npm
- AWS CDK v2 installed (`npm install -g aws-cdk`)
- AWS account with credentials configured
- An existing Bedrock AgentCore runtime ARN for your sub-agent
- The `devops-integration` package installed in your workspace

## Step-by-Step: Integrating a Security Agent

### Step 1: Define Action Schemas

Create a file that describes each action your agent exposes. Each action needs a name, description, input/output JSON Schemas, a category, and whether it requires authorization.

```typescript
// lib/security-agent-actions.ts
import type { ActionDefinition } from "../src/types";

export const securityAgentActions: ActionDefinition[] = [
  {
    name: "scan_security_groups",
    description: "Scan VPC security groups for overly permissive rules",
    input_schema: {
      type: "object",
      properties: {
        vpc_id: { type: "string", description: "Target VPC ID" },
        severity_threshold: {
          type: "string",
          enum: ["critical", "high", "medium", "low"],
          description: "Minimum severity to report",
        },
      },
      required: ["vpc_id"],
    },
    output_schema: {
      type: "object",
      properties: {
        findings: { type: "array", items: { type: "object" } },
        summary: { type: "string" },
      },
    },
    category: "analysis",
    requires_authorization: false,
  },
  {
    name: "check_iam_policies",
    description: "Analyze IAM policies for privilege escalation paths",
    input_schema: {
      type: "object",
      properties: {
        role_arn: { type: "string", description: "IAM role ARN to analyze" },
        include_inherited: { type: "boolean", default: true },
      },
      required: ["role_arn"],
    },
    output_schema: {
      type: "object",
      properties: {
        escalation_paths: { type: "array", items: { type: "object" } },
        risk_score: { type: "number" },
      },
    },
    category: "analysis",
    requires_authorization: true,
  },
  {
    name: "list_exposed_resources",
    description: "List publicly accessible resources in the account",
    input_schema: {
      type: "object",
      properties: {
        resource_types: {
          type: "array",
          items: { type: "string" },
          description: "Resource types to check (e.g., s3, ec2, rds)",
        },
      },
    },
    output_schema: {
      type: "object",
      properties: {
        exposed_resources: { type: "array", items: { type: "object" } },
        total_count: { type: "integer" },
      },
    },
    category: "utility",
    requires_authorization: false,
  },
];
```

### Step 2: Create the CDK Stack

Create a single CDK stack file that uses `AgentIntegrationTemplate`. This is all the infrastructure code you need — under 50 lines. The construct automatically provisions the MCP server, generates tool definitions, creates the IAM role, and registers with DevOps Agent via CloudFormation:

```typescript
// lib/security-agent-integration-stack.ts
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { AgentIntegrationTemplate } from "../src/constructs/agent-integration-template";
import { securityAgentActions } from "./security-agent-actions";

export class SecurityAgentIntegrationStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const integration = new AgentIntegrationTemplate(
      this,
      "SecurityAgentIntegration",
      {
        agentName: "security-agent",
        agentRuntimeArn: `arn:aws:bedrock:${this.region}:${this.account}:agent/SECURITY_AGENT_ID`,
        actions: securityAgentActions,
        authorizationGroupName: "SecurityAuditGroup",
      }
    );

    // Stack outputs (informational — registration is automatic via CloudFormation)
    new cdk.CfnOutput(this, "McpEndpoint", {
      value: integration.mcpEndpointUrl,
      description: "MCP JSON-RPC endpoint URL (POST)",
    });

    new cdk.CfnOutput(this, "HealthCheckUrl", {
      value: integration.healthUrl,
      description: "Health check endpoint URL (GET)",
    });

    new cdk.CfnOutput(this, "DevOpsAgentRoleArn", {
      value: integration.devOpsAgentRoleArn,
      description: "IAM role ARN for DevOps Agent SigV4 authentication",
    });
  }
}
```

**Line count**: 35 lines of configuration code (excluding imports and the action definitions file). Well under the 50-line target.

**Note**: The `AgentIntegrationTemplate` construct automatically includes an `AWS::DevOpsAgent::Service` CloudFormation resource that registers the MCP server with DevOps Agent at deploy time. No manual registration step is required.

### Step 3: Create the CDK App Entry Point

```typescript
// bin/app.ts
import * as cdk from "aws-cdk-lib";
import { getRegion } from "../../../../shared/utils/aws-utils";
import { SecurityAgentIntegrationStack } from "../lib/security-agent-integration-stack";

const app = new cdk.App();
const region = getRegion();

new SecurityAgentIntegrationStack(
  app,
  `GOATSecurityAgentIntegration-${region}`,
  {
    env: { region },
    description:
      "GOAT Security Agent MCP integration with DevOps Agent (uksb-do9bhieqqh)(tag:goat-devops-integration,operations-automation)",
  }
);

app.synth();
```

### Step 4: Deploy the Stack

```bash
cd infrastructure/cdk
npx cdk deploy "GOATSecurityAgentIntegration-${AWS_REGION}" --require-approval never
```

### Step 5: Verify Registration (Automatic)

Registration with DevOps Agent happens **automatically** during `cdk deploy` via the `AWS::DevOpsAgent::Service` CloudFormation resource included in the construct. The resource uses type `mcpserversigv4` and configures:

- **Endpoint**: The API Gateway root URL (POST /)
- **Auth**: SigV4 with the created IAM role ARN
- **Region/Service**: `execute-api` in the deployment region

After deployment, DevOps Agent will:
1. Connect to your MCP endpoint
2. Send `initialize` to negotiate protocol version
3. Send `tools/list` to discover available tools
4. Begin invoking tools via `tools/call` as needed

You can verify the registration in the DevOps Agent console or via:
```bash
aws devops-agent list-services --output table
```

## Expected Deployment Outputs

After a successful `cdk deploy`, the stack produces these outputs:

| Output Key | Example Value | Purpose |
|------------|---------------|---------|
| `McpEndpointUrl` | `https://abc123.execute-api.us-east-1.amazonaws.com/prod/` | MCP JSON-RPC endpoint (POST) for DevOps Agent |
| `HealthCheckUrl` | `https://abc123.execute-api.us-east-1.amazonaws.com/prod/health` | Operational status verification (GET) |
| `DevOpsAgentRoleArn` | `arn:aws:iam::123456789012:role/SecurityAgent-DevOpsAgentRole` | IAM role for SigV4 authentication |
| `RegisterCommand` | `aws devops-agent register-service --service mcpserversigv4 ...` | Manual registration command (backup — normally automatic) |

## Verifying the Integration

### Health Check

Verify the endpoint is operational:

```bash
# Using AWS SigV4 authentication (via awscurl or similar)
curl -s "${MCP_ENDPOINT_URL}health" | jq .
```

Expected response:

```json
{
  "status": "healthy",
  "timestamp": "2025-01-15T10:30:00.000Z",
  "components": {
    "network_agent": { "status": "available", "agent_id": "SECU***" },
    "integration_lambda": { "status": "available", "version": "2.0.0" },
    "capture_state_table": { "status": "available" }
  },
  "region": "us-east-1"
}
```

### MCP Initialize

Send an `initialize` request to negotiate protocol version:

```bash
curl -s -X POST "${MCP_ENDPOINT_URL}" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","clientInfo":{"name":"test-client","version":"1.0.0"},"capabilities":{}}}' | jq .
```

Expected response:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "serverInfo": {
      "name": "security-agent",
      "version": "2.0.0"
    },
    "capabilities": {
      "tools": {
        "listChanged": false
      }
    }
  }
}
```

The response includes the `Mcp-Session-Id` header for session continuity in subsequent requests.

### MCP Tools List

Discover available tools:

```bash
curl -s -X POST "${MCP_ENDPOINT_URL}" \
  -H "Content-Type: application/json" \
  -H "Mcp-Session-Id: ${SESSION_ID}" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | jq .
```

Expected response:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [
      {
        "name": "scan_security_groups",
        "description": "Scan VPC security groups for overly permissive rules",
        "inputSchema": {
          "type": "object",
          "properties": {
            "vpc_id": { "type": "string", "description": "Target VPC ID" },
            "severity_threshold": { "type": "string", "enum": ["critical", "high", "medium", "low"] }
          },
          "required": ["vpc_id"]
        }
      },
      {
        "name": "check_iam_policies",
        "description": "Analyze IAM policies for privilege escalation paths",
        "inputSchema": { "..." : "..." }
      },
      {
        "name": "list_exposed_resources",
        "description": "List publicly accessible resources in the account",
        "inputSchema": { "..." : "..." }
      }
    ]
  }
}
```

**Note:** Tool descriptions are enriched workflow-aware strings without a `[Category:]` prefix. The description derivation follows a priority chain: `schemaEntry.mcpDescription` → `MCP_DESCRIPTIONS[actionName]` → `schemaEntry.input.description` → name-based fallback.

### MCP Tool Invocation

Invoke a tool via `tools/call`:

```bash
curl -s -X POST "${MCP_ENDPOINT_URL}" \
  -H "Content-Type: application/json" \
  -H "Mcp-Session-Id: ${SESSION_ID}" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "scan_security_groups",
      "arguments": { "vpc_id": "vpc-0abc123def456" }
    }
  }' | jq .
```

Expected success response (JSON-RPC 2.0 with `CallToolResult`):

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"message\":\"Action scan_security_groups invoked successfully\",\"parameters\":{\"vpc_id\":\"vpc-0abc123def456\"}}"
      }
    ],
    "isError": false
  }
}
```

**Note**: The `result.content[0].text` field contains a JSON-serialized string of the tool's output. Parse it to access structured data.

## Hidden Tools

The MCP server defines 23 actions in its `ACTION_SCHEMAS` registry but only exposes **21 tools** via `tools/list`. Two tools are hidden because the underlying Network Agent runtime does not support them directly:

| Hidden Tool | Reason |
|-------------|--------|
| `full_diagnostic` | Composite action that combines multiple sub-calls. Cannot complete within the API Gateway 29s timeout. Would need async implementation (Step Functions or polling). |
| `cleanup_orphaned_sessions` | Maintenance utility that operates on the capture state DynamoDB table locally. No corresponding action exists on the Network Agent side. |

The `HIDDEN_TOOLS` set in `src/lambda/tool-definitions.ts` controls which tools are filtered from `tools/list` responses. Tools in this set can still be called via `tools/call` but will return an error from the Network Agent.

## Confirmation Flow

Three capture lifecycle tools include confirmation prompts in their descriptions:

- `start_capture`
- `stop_capture`
- `transform_capture`

Their descriptions contain: *"IMPORTANT: Before calling this tool, you MUST stop and ask the user for explicit confirmation."*

This instructs DevOps Agent to pause and request user confirmation before executing these potentially impactful operations. Note: This is model-dependent behavior — DevOps Agent respects the instruction approximately 80% of the time.

## Parameter Naming: `query_pcap`

The `query_pcap` tool uses a parameter named `sql` (not `query`). This matches what the Network Agent runtime expects:

```json
{
  "method": "tools/call",
  "params": {
    "name": "query_pcap",
    "arguments": {
      "capture_id": "cap-abc123",
      "sql": "SELECT src_ip, dst_ip, protocol FROM packets WHERE tcp_flags LIKE '%SYN%' LIMIT 10"
    }
  }
}
```

The schema in `action-schemas.ts` defines this as the `sql` property. Using `query` will result in a `schema_validation_failed` error.

## Error Responses

### Protocol-Level Errors (JSON-RPC)

These are returned when the MCP protocol itself is violated:

| Condition | Code | Response |
|-----------|------|----------|
| Invalid JSON body | -32700 | `{"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"Parse error: Invalid JSON"}}` |
| Missing `jsonrpc` or `method` field | -32600 | `{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Invalid Request: ..."}}` |
| Unknown method | -32601 | `{"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"Method not found: resources/list"}}` |
| Unknown tool name | -32602 | `{"jsonrpc":"2.0","id":1,"error":{"code":-32602,"message":"Invalid params: tool 'nonexistent' not found"}}` |

### Application-Level Errors (CallToolResult with `isError: true`)

These are returned as successful JSON-RPC responses (the protocol succeeded, but the tool operation failed):

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"code\":\"schema_validation_failed\",\"message\":\"Request parameters failed validation against the schema for action \\\"scan_security_groups\\\".\",\"details\":{\"failing_parameters\":[\"vpc_id\"],\"expected_constraints\":{\"vpc_id\":\"Required parameter \\\"vpc_id\\\" is missing\"}}}"
      }
    ],
    "isError": true
  }
}
```

Application error codes:
| Code | Meaning |
|------|---------|
| `schema_validation_failed` | Request parameters don't match the tool's inputSchema |
| `authorization_denied` | Caller not in the required authorization group |
| `rate_limit_exceeded` | Maximum concurrent captures reached |
| `timeout` | Network Agent didn't respond within 60s |
| `region_mismatch` | Request targets wrong region |
| `network_agent_error` | Upstream agent returned an error |

## Troubleshooting

### Stack deployment fails with "Export not found"

**Cause**: The dependent GOAT infrastructure stacks are not deployed in the target region.

**Fix**: Deploy the prerequisite stacks first:
```bash
cd ../goat-network-infra
npx cdk deploy "GOATNetworkInfra-${AWS_REGION}" --require-approval never
npx cdk deploy "GOATNetworkRuntime-${AWS_REGION}" --require-approval never
```

### Health check returns "unhealthy"

**Cause**: The agent runtime ARN is invalid or the Bedrock agent is not active.

**Fix**:
1. Verify the agent exists: `aws bedrock list-agents --region $AWS_REGION`
2. Confirm the agent ID in your action definitions matches an active agent
3. Check that the Lambda's IAM role has `bedrock-agentcore:InvokeAgentRuntime` permission

### MCP error -32601: "Method not found"

**Cause**: The client is calling an MCP method not supported by this server.

**Supported methods**: `initialize`, `tools/list`, `tools/call`, `ping`, `notifications/initialized`

**Fix**: Check that your client is not calling unsupported methods like `resources/list`, `prompts/list`, or `sampling/createMessage`. This server only implements the tools capability.

### MCP error -32602: "Invalid params: tool 'xxx' not found"

**Cause**: The `params.name` in a `tools/call` request doesn't match any registered tool.

**Fix**:
1. Call `tools/list` to see all available tool names
2. Verify the tool name is spelled correctly (exact match required)
3. Tool names come from the `name` field in your `ActionDefinition[]` array

### MCP error -32700: "Parse error"

**Cause**: The request body is not valid JSON.

**Fix**:
1. Ensure the `Content-Type` header is `application/json`
2. Validate your JSON payload (check for trailing commas, unquoted keys, etc.)
3. Ensure the body is UTF-8 encoded

### MCP error -32600: "Invalid Request"

**Cause**: The JSON is valid but doesn't conform to JSON-RPC 2.0 structure.

**Fix**:
1. Ensure the request includes `"jsonrpc": "2.0"` (exact string)
2. Ensure the request includes a `"method"` field (string)
3. For requests (not notifications), include an `"id"` field (number or string)

### Application error "schema_validation_failed"

**Cause**: The `arguments` in a `tools/call` request don't match the tool's `inputSchema`.

**Fix**:
1. Call `tools/list` and inspect the `inputSchema` for the target tool
2. Verify all `required` fields are present in your `arguments` object
3. Ensure parameter types match (e.g., string vs number, array vs object)

### Application error "authorization_denied"

**Cause**: The invoking role is not a member of the configured authorization group for protected actions.

**Fix**:
1. Check which actions require authorization (those with `requires_authorization: true`)
2. Add the DevOps Agent role to the authorization group configured in the template props

### 403 Forbidden on API Gateway

**Cause**: The caller is not using valid IAM/SigV4 authentication, or the resource policy blocks the role.

**Fix**:
1. Ensure you're signing requests with SigV4 credentials
2. Verify the DevOps Agent role ARN has `execute-api:Invoke` permission on the endpoint
3. Check the trust policy includes `aidevops.amazonaws.com` as a principal
4. Verify confused deputy conditions match your account/region

### Lambda timeout (504)

**Cause**: The underlying agent runtime did not respond within 60 seconds.

**Fix**:
1. Check the agent runtime health in the Bedrock console
2. Verify network connectivity between the Lambda and Bedrock service endpoint
3. Check CloudWatch Logs for the Integration Lambda for detailed error information

### DevOps Agent doesn't discover tools

**Cause**: The `AWS::DevOpsAgent::Service` CloudFormation resource may have failed to register.

**Fix**:
1. Check the CloudFormation stack events for registration errors
2. Verify the IAM role trust policy includes `aidevops.amazonaws.com`
3. Confirm the endpoint URL is reachable with SigV4 auth
4. Check that `initialize` and `tools/list` return valid responses
5. As a fallback, use the `RegisterCommand` stack output to manually register

## Template API Reference

### AgentIntegrationTemplateProps

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `agentName` | `string` | Yes | Identifier for the agent (used in MCP server info and tool descriptions) |
| `agentRuntimeArn` | `string` | Yes | Bedrock AgentCore runtime ARN |
| `actions` | `ActionDefinition[]` | Yes | List of actions the agent exposes (converted to MCP tool definitions) |
| `authorizationGroupName` | `string` | No | IAM group name for protected actions |
| `lambdaCode` | `lambda.Code` | No | Custom Lambda code asset (e.g., `Code.fromAsset('dist/')`). If omitted, generates inline handler code. |
| `lambdaHandler` | `string` | No | Lambda handler entry point (default: `index.handler`). Use `mcp-handler.handler` for the esbuild-bundled handler. |

### Construct Outputs

| Property | Type | Description |
|----------|------|-------------|
| `endpointUrl` | `string` | API Gateway base URL |
| `mcpEndpointUrl` | `string` | MCP endpoint URL (POST / for JSON-RPC messages) |
| `healthUrl` | `string` | Full health check URL (GET /health) |
| `devOpsAgentRoleArn` | `string` | IAM role ARN for DevOps Agent SigV4 auth |
| `api` | `RestApi` | API Gateway resource (for advanced customization) |
| `integrationLambda` | `Function` | Lambda resource (for adding permissions) |
| `devOpsAgentRole` | `Role` | IAM role resource (for adding policies) |
| `captureStateTable` | `Table` | DynamoDB table (for state tracking) |

### ActionDefinition Interface

| Field | Type | Description |
|-------|------|-------------|
| `name` | `string` | Action identifier (becomes MCP tool name) |
| `description` | `string` | Human-readable description (used in MCP tool definition) |
| `mcpDescription` | `string?` | Optional override description for MCP exposure (highest priority in derivation chain) |
| `input_schema` | `JSONSchema` | JSON Schema for input validation (becomes MCP `inputSchema`) |
| `output_schema` | `JSONSchema` | JSON Schema for output documentation |
| `category` | `"capture" \| "analysis" \| "utility"` | Action category (for internal classification, not exposed in MCP descriptions) |
| `requires_authorization` | `boolean` | Whether authorization group membership is required |

### MCP Protocol Details

| Aspect | Value |
|--------|-------|
| Protocol | JSON-RPC 2.0 over streamable HTTP |
| Transport | HTTPS (API Gateway with IAM auth) |
| Authentication | SigV4 with `aidevops.amazonaws.com` trusted role |
| Session | `Mcp-Session-Id` header (echoed in responses) |
| Protocol version | `2024-11-05` |
| Server capabilities | `tools` (with `listChanged: false`) |
| Tools exposed | 21 (23 in registry, 2 hidden) |
| Supported methods | `initialize`, `tools/list`, `tools/call`, `ping`, `notifications/initialized` |
| Registration type | `mcpserversigv4` via `AWS::DevOpsAgent::Service` CloudFormation resource |
| Agent invocation SDK | `@aws-sdk/client-bedrock-agentcore` (`InvokeAgentRuntimeCommand`) |
| Agent runtime env var | `NETWORK_AGENT_ARN` (full ARN of the AgentCore runtime) |
| Rate limiter | Fails open (try-catch) — Network Agent has its own rate limiting |
| Authorization | `AUTHORIZED_ROLE_ARNS=*` (API Gateway IAM auth is the real gate) |

### CfnOutputs (Stack Level)

| Output Key | Description |
|------------|-------------|
| `McpEndpointUrl` | MCP JSON-RPC endpoint URL (POST) |
| `RegisterCommand` | Full `aws devops-agent register-service` command (backup for manual registration) |
| `HealthCheckUrl` | Health check endpoint URL (GET) |
| `DevOpsAgentRoleArn` | IAM role ARN for DevOps Agent SigV4 authentication |
