/**
 * GOATDevOpsIntegration CDK Stack
 *
 * Deploys the DevOps Agent integration endpoint as a new CDK stack that imports
 * existing NetworkInfra stack CfnOutput exports and instantiates the
 * AgentIntegrationTemplate with Network Agent action definitions.
 *
 * Uses NodejsFunction to deploy the real mcp-handler.ts (with esbuild bundling)
 * instead of the construct's inline Lambda stub, so that tools/call actually
 * invokes the Network Agent via processInvocation.
 *
 * Requirements: 5.1, 5.3
 */

import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import { Construct } from "constructs";
import { AgentIntegrationTemplate } from "../../../src/constructs/agent-integration-template";
import { ACTION_SCHEMAS, getActionNames } from "../../../src/schemas/action-schemas";
import type { ActionDefinition, JSONSchema } from "../../../src/types";

export class GOATDevOpsIntegrationStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const region = cdk.Stack.of(this).region;

    // ─── Import Existing Stack Exports ──────────────────────────────────────
    // Uses the actual CfnOutput export names from deployed stacks

    const agentRuntimeArn = cdk.Fn.importValue(
      "GOATNetworkAgentRuntimeArn"
    );

    // ─── Transform ACTION_SCHEMAS into ActionDefinition[] ─────────────────

    const actions: ActionDefinition[] = getActionNames().map((name) => {
      const schema = ACTION_SCHEMAS[name];
      return {
        name,
        description: `Network Agent action: ${name}`,
        input_schema: schema.input as JSONSchema,
        output_schema: schema.output as JSONSchema,
        category: schema.category,
        requires_authorization: schema.requiresAuth,
      };
    });

    // ─── Instantiate Agent Integration Template ───────────────────────────
    // Uses the real mcp-handler.ts bundled with esbuild instead of the inline stub.
    // This ensures tools/call actually invokes processInvocation → Network Agent.
    // Pre-bundle with: npx esbuild src/lambda/mcp-handler.ts --bundle --platform=node --target=node20 --outfile=dist/mcp-handler.js --external:@aws-sdk/*

    const integration = new AgentIntegrationTemplate(
      this,
      "NetworkAgentIntegration",
      {
        agentName: "goat-network-agent",
        agentRuntimeArn: agentRuntimeArn,
        actions,
        authorizationGroupName: "Capture_Authorization_Group",
        lambdaCode: lambda.Code.fromAsset(path.join(__dirname, "../../../dist")),
        lambdaHandler: "mcp-handler.handler",
      }
    );

    // ─── Register MCP Server with DevOps Agent ─────────────────────────────
    // Uses AWS::DevOpsAgent::Service with mcpserversigv4 service type
    // ─── DevOps Agent Registration ──────────────────────────────────────────
    // NOTE: Registration with DevOps Agent is handled externally.
    // The AWS::DevOpsAgent::Service resource type fails with "AlreadyExists"
    // if the service was previously registered (registration persists across
    // stack deletions). The AwsCustomResource approach requires @aws-sdk/client-devopsagent
    // which doesn't exist yet.
    //
    // Use the RegisterCommand stack output below for manual registration, or
    // use the DevOps Agent console. Once registered, the service persists
    // until explicitly deregistered.

    // ─── CDK Stack Outputs ────────────────────────────────────────────────

    new cdk.CfnOutput(this, "ToolEndpointUrl", {
      value: integration.endpointUrl,
      description: "HTTPS endpoint URL for DevOps Agent tool registration",
      exportName: `GOATDevOpsIntegration-${region}-ToolEndpointUrl`,
    });

    new cdk.CfnOutput(this, "McpEndpointUrl", {
      value: integration.mcpEndpointUrl,
      description: "MCP JSON-RPC endpoint URL (POST /)",
      exportName: `GOATDevOpsIntegration-${region}-McpEndpointUrl`,
    });

    new cdk.CfnOutput(this, "DevOpsAgentRoleArn", {
      value: integration.devOpsAgentRoleArn,
      description: "IAM role ARN for DevOps Agent to assume",
      exportName: `GOATDevOpsIntegration-${region}-DevOpsAgentRoleArn`,
    });

    new cdk.CfnOutput(this, "HealthCheckUrl", {
      value: integration.healthUrl,
      description: "Health check URL for monitoring integration availability",
      exportName: `GOATDevOpsIntegration-${region}-HealthCheckUrl`,
    });
  }
}
