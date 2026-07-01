import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as agentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import { Construct } from 'constructs';
import * as path from 'path';

/**
 * McpServerStack — Payment Transaction Insights MCP server via AgentCore Gateway.
 *
 * Uses the stable L1 constructs from aws-cdk-lib/aws-bedrockagentcore (CfnGateway,
 * CfnGatewayTarget) — no alpha dependency required. The Cognito user pool, resource
 * server, app client and domain that back the Gateway's OAuth (M2M / client
 * credentials) authorizer are provisioned here explicitly.
 *
 * Creates:
 *   1. Read-only PostgreSQL credentials in Secrets Manager
 *   2. Security group for the Lambda (egress to RDS + HTTPS)
 *   3. Lambda function that queries the payment database
 *   4. Cognito user pool + client for the Gateway's CUSTOM_JWT authorizer
 *   5. AgentCore Gateway (MCP) exposing the Lambda as MCP tools
 *   6. Gateway target with tool schemas for the 4 query tools
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
    // Cognito — M2M (client credentials) OAuth for the Gateway CUSTOM_JWT authorizer
    // DevOps Agent obtains a token from this user pool and presents it to the Gateway.
    // -----------------------------------------------------------------------
    const userPool = new cognito.UserPool(this, 'GatewayUserPool', {
      userPoolName: `${projectName}-${environment}-mcp-gateway`,
      signInCaseSensitive: false,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const resourceServerId = `${projectName}-${environment}-mcp-tools`;
    const readScope = new cognito.ResourceServerScope({ scopeName: 'read', scopeDescription: 'Read access to gateway tools' });
    const writeScope = new cognito.ResourceServerScope({ scopeName: 'write', scopeDescription: 'Write access to gateway tools' });

    const resourceServer = userPool.addResourceServer('GatewayResourceServer', {
      identifier: resourceServerId,
      scopes: [readScope, writeScope],
    });

    const userPoolClient = userPool.addClient('GatewayClient', {
      generateSecret: true,
      oAuth: {
        flows: { clientCredentials: true },
        scopes: [
          cognito.OAuthScope.resourceServer(resourceServer, readScope),
          cognito.OAuthScope.resourceServer(resourceServer, writeScope),
        ],
      },
    });

    // OAuth2 token endpoint domain (deploy script reads UserPool.Domain to build the token URL)
    userPool.addDomain('GatewayDomain', {
      cognitoDomain: {
        domainPrefix: `${projectName}-${environment}-mcp-${cdk.Aws.ACCOUNT_ID}`,
      },
    });

    // -----------------------------------------------------------------------
    // Gateway service role — assumed by AgentCore to invoke the Lambda target
    // -----------------------------------------------------------------------
    const gatewayName = `${projectName}-${environment}-mcp-gateway`;

    const gatewayRole = new iam.Role(this, 'GatewayServiceRole', {
      roleName: `${projectName}-${environment}-mcp-gateway-role`,
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
        conditions: {
          StringEquals: { 'aws:SourceAccount': this.account },
          ArnLike: {
            'aws:SourceArn': `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:gateway/${gatewayName}*`,
          },
        },
      }),
      description: `Service role for Bedrock AgentCore Gateway ${gatewayName}`,
    });
    mcpLambda.grantInvoke(gatewayRole);

    // -----------------------------------------------------------------------
    // AgentCore Gateway (L1) — MCP protocol, CUSTOM_JWT (Cognito) authorizer
    // -----------------------------------------------------------------------
    const discoveryUrl = `https://cognito-idp.${this.region}.amazonaws.com/${userPool.userPoolId}/.well-known/openid-configuration`;

    const gateway = new agentcore.CfnGateway(this, 'TxnInsightsGateway', {
      name: gatewayName,
      description: 'Payment Transaction Insights MCP Gateway for DevOps Agent',
      roleArn: gatewayRole.roleArn,
      protocolType: 'MCP',
      protocolConfiguration: {
        mcp: {
          supportedVersions: ['2025-03-26'],
          searchType: 'SEMANTIC',
          instructions: 'Read-only payment transaction insights for incident investigation',
        },
      },
      authorizerType: 'CUSTOM_JWT',
      authorizerConfiguration: {
        customJwtAuthorizer: {
          discoveryUrl,
          allowedClients: [userPoolClient.userPoolClientId],
        },
      },
    });

    // -----------------------------------------------------------------------
    // Gateway target — Lambda exposing the 4 read-only query tools
    // -----------------------------------------------------------------------
    const toolSchema: agentcore.CfnGatewayTarget.ToolDefinitionProperty[] = [
      {
        name: 'get_transaction_summary',
        description: 'Get transaction counts grouped by status for the last N minutes. Use minutes=60 during incidents to compare current state against the pre-incident baseline. A healthy system shows steady CAPTURED transactions. A spike in PENDING/AUTHORIZED with no CAPTURED indicates processing is stalled.',
        inputSchema: {
          type: 'object',
          properties: {
            minutes: { type: 'number', description: 'Look-back window in minutes (default: 30, max: 60). Use 60 during incidents for baseline comparison.' },
          },
        },
      },
      {
        name: 'get_recent_failures',
        description: 'Get details of recent failed transactions with error codes, merchant info, and amounts. Use minutes=60 during incidents to capture the full failure window. Useful for understanding which merchants are affected during an outage.',
        inputSchema: {
          type: 'object',
          properties: {
            minutes: { type: 'number', description: 'Look-back window in minutes (default: 30, max: 60). Use 60 during incidents.' },
            limit: { type: 'number', description: 'Maximum number of results (default: 20, max: 50)' },
          },
        },
      },
      {
        name: 'get_processing_gap',
        description: 'Check how long since the last successfully captured transaction. A large gap (5+ minutes) indicates payment processing has stalled — the absence-of-activity signal that is not visible in logs or metrics.',
        inputSchema: {
          type: 'object',
          properties: {},
        },
      },
      {
        name: 'get_incident_impact',
        description: 'Analyze the business impact of a past incident over an absolute time range. Returns transaction counts by status, dollar amounts, affected merchants, and baseline comparison (same-duration window before the incident). Also includes state transition analysis (average capture latency, never-captured transactions) when available. Use this for post-incident reviews.',
        inputSchema: {
          type: 'object',
          properties: {
            start_time: { type: 'string', description: 'Incident start time in ISO 8601 format (e.g. 2026-04-24T14:00:00Z)' },
            end_time: { type: 'string', description: 'Incident end time in ISO 8601 format (e.g. 2026-04-24T14:25:00Z)' },
            baseline_hours: { type: 'number', description: 'Hours of data before the incident to use as baseline (default: 1, min: 0.5, max: 24). Use 0.5 for recent traffic comparison.' },
          },
          required: ['start_time', 'end_time'],
        },
      },
    ];

    const gatewayTarget = new agentcore.CfnGatewayTarget(this, 'McpGatewayTarget', {
      gatewayIdentifier: gateway.attrGatewayIdentifier,
      name: 'transaction-insights',
      credentialProviderConfigurations: [
        { credentialProviderType: 'GATEWAY_IAM_ROLE' },
      ],
      targetConfiguration: {
        mcp: {
          lambda: {
            lambdaArn: mcpLambda.functionArn,
            toolSchema: { inlinePayload: toolSchema },
          },
        },
      },
    });
    gatewayTarget.addDependency(gateway);

    // -----------------------------------------------------------------------
    // Expose values
    // -----------------------------------------------------------------------
    this.gatewayArn = gateway.attrGatewayArn;

    new cdk.CfnOutput(this, 'McpGatewayArn', {
      description: 'AgentCore Gateway ARN',
      value: gateway.attrGatewayArn,
    });

    new cdk.CfnOutput(this, 'McpGatewayId', {
      description: 'AgentCore Gateway ID',
      value: gateway.attrGatewayIdentifier,
    });

    new cdk.CfnOutput(this, 'McpSecretArn', {
      description: 'Secrets Manager ARN for MCP read-only DB credentials',
      value: mcpDbSecret.secretArn,
    });

    new cdk.CfnOutput(this, 'McpLambdaFunctionName', {
      description: 'MCP Lambda function name',
      value: mcpLambda.functionName,
    });
  }
}
