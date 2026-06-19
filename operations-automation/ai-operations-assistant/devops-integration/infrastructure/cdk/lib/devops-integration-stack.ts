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
    // ─── DevOps Agent Registration (Idempotent) ────────────────────────────
    // Uses a Custom Resource Lambda that checks if the service is already
    // registered before calling register. This avoids the "AlreadyExists"
    // error that occurs with the raw AWS::DevOpsAgent::Service resource type
    // (registrations persist across stack deletions).

    const registrationServiceName = `goat-network-agent-${region}`;

    const registerLambda = new lambda.Function(this, "RegisterServiceFn", {
      runtime: lambda.Runtime.NODEJS_20_X,
      handler: "index.handler",
      timeout: cdk.Duration.seconds(60),
      code: lambda.Code.fromInline(`
const { execSync } = require('child_process');
exports.handler = async (event) => {
  const props = event.ResourceProperties;
  const requestType = event.RequestType;
  const physicalId = props.ServiceName || 'mcp-registration';

  // On Delete, we intentionally do NOT deregister — the service should persist
  if (requestType === 'Delete') {
    return { PhysicalResourceId: physicalId, Data: { Status: 'Retained' } };
  }

  try {
    // Check if already registered by listing services and grepping for our name
    const listResult = execSync(
      'aws devops-agent list-services --output json --no-cli-pager 2>/dev/null || echo "{\\"services\\":[]}"',
      { encoding: 'utf-8', timeout: 30000 }
    );
    const services = JSON.parse(listResult);
    const existing = (services.services || []).find(s =>
      s.serviceDetails?.mcpServerSigV4?.name === props.ServiceName
    );

    if (existing) {
      console.log('Service already registered:', props.ServiceName);
      return { PhysicalResourceId: physicalId, Data: { Status: 'AlreadyRegistered', ServiceId: existing.serviceId || 'unknown' } };
    }

    // Not registered — register now
    const registerCmd = [
      'aws devops-agent register-service',
      '--service mcpserversigv4',
      '--name', JSON.stringify(props.ServiceName),
      '--endpoint', JSON.stringify(props.Endpoint),
      '--authorizationConfig', JSON.stringify(JSON.stringify(props.AuthorizationConfig)),
      '--no-cli-pager'
    ].join(' ');

    console.log('Registering:', registerCmd);
    const result = execSync(registerCmd, { encoding: 'utf-8', timeout: 30000 });
    console.log('Registration result:', result);
    return { PhysicalResourceId: physicalId, Data: { Status: 'Registered' } };
  } catch (err) {
    // If registration fails with AlreadyExists, treat as success
    if (err.message && (err.message.includes('AlreadyExists') || err.message.includes('already exists'))) {
      console.log('Service already exists (caught in error handler)');
      return { PhysicalResourceId: physicalId, Data: { Status: 'AlreadyRegistered' } };
    }
    console.error('Registration failed:', err.message);
    // Don't fail the stack — registration is non-critical
    return { PhysicalResourceId: physicalId, Data: { Status: 'Failed', Error: err.message } };
  }
};
`),
    });

    registerLambda.addToRolePolicy(
      new cdk.aws_iam.PolicyStatement({
        actions: ["devops-agent:RegisterService", "devops-agent:ListServices"],
        resources: ["*"],
      })
    );

    const registrationProvider = new cdk.custom_resources.Provider(
      this,
      "RegistrationProvider",
      {
        onEventHandler: registerLambda,
      }
    );

    new cdk.CustomResource(this, "McpServiceRegistration", {
      serviceToken: registrationProvider.serviceToken,
      properties: {
        ServiceName: registrationServiceName,
        Endpoint: integration.mcpEndpointUrl,
        AuthorizationConfig: {
          region: region,
          service: "execute-api",
          mcpRoleArn: integration.devOpsAgentRoleArn,
        },
        // Force update on redeploy by including endpoint URL (changes if API GW recreated)
        EndpointHash: integration.mcpEndpointUrl,
      },
    });

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
