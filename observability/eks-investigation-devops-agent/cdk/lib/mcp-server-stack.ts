import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as agentcore from '@aws-cdk/aws-bedrock-agentcore-alpha';
import { Construct } from 'constructs';
import * as path from 'path';

/**
 * McpServerStack — Payment Transaction Insights MCP server via AgentCore Gateway.
 *
 * Creates:
 *   1. Read-only PostgreSQL credentials in Secrets Manager
 *   2. Security group for the Lambda (egress to RDS + HTTPS)
 *   3. Lambda function that queries the payment database
 *   4. AgentCore Gateway exposing the Lambda as MCP tools
 *   5. Gateway target with tool schemas for the 4 query tools
 */
export interface McpServerStackProps extends cdk.StackProps {
  environment: string;
  projectName: string;
  vpc: ec2.IVpc;
  privateComputeSubnets: ec2.ISubnet[];
  databaseSecurityGroup: ec2.ISecurityGroup;
  rdsEndpoint: string;
}

export class McpServerStack extends cdk.Stack {
  public readonly gatewayArn: string;
  public readonly gatewayEndpoint: string;

  constructor(scope: Construct, id: string, props: McpServerStackProps) {
    super(scope, id, props);

    const { environment, projectName, vpc, privateComputeSubnets, databaseSecurityGroup, rdsEndpoint } = props;

    // -----------------------------------------------------------------------
    // Secrets Manager — read-only DB credentials
    // -----------------------------------------------------------------------
    const mcpDbSecret = new secretsmanager.Secret(this, 'McpDbSecret', {
      secretName: `${projectName}-${environment}-mcp-readonly-credentials`,
      description: 'Read-only PostgreSQL credentials for the MCP server',
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ username: 'mcp_readonly' }),
        generateStringKey: 'password',
        excludePunctuation: true,
        passwordLength: 20,
      },
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // -----------------------------------------------------------------------
    // Security Group — Lambda needs egress to RDS and HTTPS
    // -----------------------------------------------------------------------
    const mcpLambdaSg = new ec2.SecurityGroup(this, 'McpLambdaSecurityGroup', {
      vpc,
      securityGroupName: `${projectName}-${environment}-mcp-lambda-sg`,
      description: 'Security group for MCP transaction insights Lambda',
      allowAllOutbound: false,
    });

    new ec2.CfnSecurityGroupEgress(this, 'McpLambdaEgressToRds', {
      groupId: mcpLambdaSg.securityGroupId,
      ipProtocol: 'tcp',
      fromPort: 5432,
      toPort: 5432,
      destinationSecurityGroupId: databaseSecurityGroup.securityGroupId,
      description: 'Allow MCP Lambda to reach RDS PostgreSQL',
    });

    new ec2.CfnSecurityGroupEgress(this, 'McpLambdaEgressHttps', {
      groupId: mcpLambdaSg.securityGroupId,
      ipProtocol: 'tcp',
      fromPort: 443,
      toPort: 443,
      cidrIp: '0.0.0.0/0',
      description: 'Allow HTTPS for AWS API calls (Secrets Manager)',
    });

    new ec2.CfnSecurityGroupIngress(this, 'DbIngressFromMcpLambda', {
      groupId: databaseSecurityGroup.securityGroupId,
      ipProtocol: 'tcp',
      fromPort: 5432,
      toPort: 5432,
      sourceSecurityGroupId: mcpLambdaSg.securityGroupId,
      description: 'Allow PostgreSQL from MCP Lambda',
    });

    // -----------------------------------------------------------------------
    // Lambda Function — queries payment database (read-only)
    // -----------------------------------------------------------------------
    const mcpLambdaRole = new iam.Role(this, 'McpLambdaRole', {
      roleName: `${projectName}-${environment}-mcp-lambda-role`,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaVPCAccessExecutionRole'),
      ],
    });

    mcpLambdaRole.addToPolicy(new iam.PolicyStatement({
      sid: 'ReadMcpDbSecret',
      effect: iam.Effect.ALLOW,
      actions: ['secretsmanager:GetSecretValue'],
      resources: [mcpDbSecret.secretArn],
    }));

    const mcpLambda = new lambda.Function(this, 'McpTransactionInsightsLambda', {
      functionName: `${projectName}-${environment}-mcp-transaction-insights`,
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'mcp-transaction-insights')),
      role: mcpLambdaRole,
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      vpc,
      vpcSubnets: { subnets: privateComputeSubnets },
      securityGroups: [mcpLambdaSg],
      environment: {
        DB_HOST: rdsEndpoint,
        DB_PORT: '5432',
        DB_NAME: 'paymentdb',
        DB_SSL_MODE: 'require',
        DB_SECRET_ARN: mcpDbSecret.secretArn,
      },
    });

    // -----------------------------------------------------------------------
    // VPC Endpoint for AgentCore Gateway (private access)
    // Ensures the Gateway MCP endpoint is only reachable from within the VPC.
    // DevOps Agent connects via a private connection (VPC Lattice).
    // -----------------------------------------------------------------------
    const gatewayVpcEndpoint = new ec2.InterfaceVpcEndpoint(this, 'AgentCoreGatewayEndpoint', {
      vpc,
      service: new ec2.InterfaceVpcEndpointService(`com.amazonaws.${cdk.Aws.REGION}.bedrock-agentcore.gateway`),
      subnets: { subnets: privateComputeSubnets },
      privateDnsEnabled: true,
    });

    // -----------------------------------------------------------------------
    // AgentCore Gateway — exposes Lambda as MCP tools
    // -----------------------------------------------------------------------
    const gateway = new agentcore.Gateway(this, 'TxnInsightsGateway', {
      gatewayName: `${projectName}-${environment}-mcp-gateway`,
      description: 'Payment Transaction Insights MCP Gateway for DevOps Agent',
    });

    // Tool schema for the 4 read-only query tools
    const toolSchema = agentcore.ToolSchema.fromInline([
      {
        name: 'get_transaction_summary',
        description: 'Get transaction counts grouped by status for the last N minutes. Use minutes=60 during incidents to compare current state against the pre-incident baseline. A healthy system shows steady CAPTURED transactions. A spike in PENDING/AUTHORIZED with no CAPTURED indicates processing is stalled.',
        inputSchema: {
          type: agentcore.SchemaDefinitionType.OBJECT,
          properties: {
            minutes: { type: agentcore.SchemaDefinitionType.NUMBER, description: 'Look-back window in minutes (default: 30, max: 60). Use 60 during incidents for baseline comparison.' },
          },
        },
      },
      {
        name: 'get_recent_failures',
        description: 'Get details of recent failed transactions with error codes, merchant info, and amounts. Use minutes=60 during incidents to capture the full failure window. Useful for understanding which merchants are affected during an outage.',
        inputSchema: {
          type: agentcore.SchemaDefinitionType.OBJECT,
          properties: {
            minutes: { type: agentcore.SchemaDefinitionType.NUMBER, description: 'Look-back window in minutes (default: 30, max: 60). Use 60 during incidents.' },
            limit: { type: agentcore.SchemaDefinitionType.NUMBER, description: 'Maximum number of results (default: 20, max: 50)' },
          },
        },
      },
      {
        name: 'get_processing_gap',
        description: 'Check how long since the last successfully captured transaction. A large gap (5+ minutes) indicates payment processing has stalled — the absence-of-activity signal that is not visible in logs or metrics.',
        inputSchema: {
          type: agentcore.SchemaDefinitionType.OBJECT,
          properties: {},
        },
      },
      {
        name: 'get_incident_impact',
        description: 'Analyze the business impact of a past incident over an absolute time range. Returns transaction counts by status, dollar amounts, affected merchants, and baseline comparison (same-duration window before the incident). Also includes state transition analysis (average capture latency, never-captured transactions) when available. Use this for post-incident reviews.',
        inputSchema: {
          type: agentcore.SchemaDefinitionType.OBJECT,
          properties: {
            start_time: { type: agentcore.SchemaDefinitionType.STRING, description: 'Incident start time in ISO 8601 format (e.g. 2026-04-24T14:00:00Z)' },
            end_time: { type: agentcore.SchemaDefinitionType.STRING, description: 'Incident end time in ISO 8601 format (e.g. 2026-04-24T14:25:00Z)' },
            baseline_hours: { type: agentcore.SchemaDefinitionType.NUMBER, description: 'Hours of data before the incident to use as baseline (default: 1, min: 0.5, max: 24). Use 0.5 for recent traffic comparison.' },
          },
          required: ['start_time', 'end_time'],
        },
      },
    ]);

    const lambdaConfig = agentcore.LambdaTargetConfiguration.create(mcpLambda, toolSchema);

    new agentcore.GatewayTarget(this, 'McpGatewayTarget', {
      gateway,
      gatewayTargetName: 'transaction-insights',
      targetConfiguration: lambdaConfig,
      credentialProviderConfigurations: [
        agentcore.GatewayCredentialProvider.fromIamRole(),
      ],
    });

    // -----------------------------------------------------------------------
    // Expose values
    // -----------------------------------------------------------------------
    this.gatewayArn = gateway.gatewayArn;

    new cdk.CfnOutput(this, 'McpGatewayArn', {
      description: 'AgentCore Gateway ARN',
      value: gateway.gatewayArn,
    });

    new cdk.CfnOutput(this, 'McpGatewayId', {
      description: 'AgentCore Gateway ID',
      value: gateway.gatewayId,
    });

    new cdk.CfnOutput(this, 'McpSecretArn', {
      description: 'Secrets Manager ARN for MCP read-only DB credentials',
      value: mcpDbSecret.secretArn,
    });

    new cdk.CfnOutput(this, 'McpLambdaFunctionName', {
      description: 'MCP Lambda function name',
      value: mcpLambda.functionName,
    });

    new cdk.CfnOutput(this, 'McpVpcEndpointId', {
      description: 'VPC Endpoint ID for AgentCore Gateway',
      value: gatewayVpcEndpoint.vpcEndpointId,
    });

    new cdk.CfnOutput(this, 'McpVpcId', {
      description: 'VPC ID for private connection',
      value: vpc.vpcId,
    });

    new cdk.CfnOutput(this, 'McpSubnetIds', {
      description: 'Subnet IDs for private connection',
      value: privateComputeSubnets.map(s => s.subnetId).join(','),
    });
  }
}
