import * as cdk from 'aws-cdk-lib';
import * as bedrockagentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';

export interface RuntimeStackProps extends cdk.StackProps {
  tableName: string;
  bucketName: string;
}

/**
 * Runtime Stack — Three Dedicated AgentCore Runtimes
 *
 * Creates three AgentCore Runtimes for the Lambda Runtime Migration Assistant:
 *   1. lambdaruntime_discover  — Phase 1: Discovery + Enrichment + Prioritization
 *   2. lambdaruntime_analyze   — Phase 2: Code Analysis + Assessment
 *   3. lambdaruntime_transform — Phase 3: Code Generation + Validation
 *
 * All three share the same IAM role and use direct S3 code deployment (zip).
 */
export class RuntimeStack extends cdk.Stack {
  public readonly discoverRuntimeArn: string;
  public readonly analyzeRuntimeArn: string;
  public readonly transformRuntimeArn: string;

  constructor(scope: Construct, id: string, props: RuntimeStackProps) {
    super(scope, id, props);

    const region = cdk.Stack.of(this).region;
    const account = cdk.Stack.of(this).account;

    const migrationBucket = s3.Bucket.fromBucketName(this, 'MigrationBucket', props.bucketName);
    const tableArn = `arn:aws:dynamodb:${region}:${account}:table/${props.tableName}`;

    // IAM Role for all three AgentCore Runtimes — shared permissions
    const agentRole = new iam.Role(this, 'AgentRuntimeRole', {
      roleName: `lambda-migration-agent-role-${region}`,
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      description: 'IAM role for Lambda Runtime Migration AgentCore Runtimes',
    });

    // Trusted Advisor
    agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'TrustedAdvisorAccess',
      effect: iam.Effect.ALLOW,
      actions: ['support:DescribeTrustedAdvisorCheckResult'],
      resources: ['*'],
    }));

    // Lambda API — read-only
    agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'LambdaReadAccess',
      effect: iam.Effect.ALLOW,
      actions: [
        'lambda:GetFunction',
        'lambda:GetFunctionConfiguration',
        'lambda:ListTags',
        'lambda:ListVersionsByFunction',
      ],
      resources: ['*'],
    }));

    // CloudWatch Logs — read for enrichment + write for agent observability
    agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'CloudWatchLogsRead',
      effect: iam.Effect.ALLOW,
      actions: ['logs:DescribeLogStreams', 'logs:DescribeLogGroups'],
      resources: ['*'],
    }));

    agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'CloudWatchLogsWrite',
      effect: iam.Effect.ALLOW,
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
      resources: [`arn:aws:logs:${region}:${account}:log-group:/aws/bedrock-agentcore/runtimes/*`],
    }));

    // CloudWatch Metrics
    agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'CloudWatchMetricsAccess',
      effect: iam.Effect.ALLOW,
      actions: ['cloudwatch:GetMetricStatistics'],
      resources: ['*'],
    }));

    // DynamoDB — scoped to migration table
    agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'DynamoDBAccess',
      effect: iam.Effect.ALLOW,
      actions: [
        'dynamodb:GetItem',
        'dynamodb:PutItem',
        'dynamodb:UpdateItem',
        'dynamodb:Scan',
        'dynamodb:Query',
      ],
      resources: [tableArn, `${tableArn}/index/*`],
    }));

    // S3 — scoped to migration bucket
    agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'S3Access',
      effect: iam.Effect.ALLOW,
      actions: ['s3:GetObject', 's3:PutObject'],
      resources: [`${migrationBucket.bucketArn}/*`],
    }));

    agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'S3ListAccess',
      effect: iam.Effect.ALLOW,
      actions: ['s3:ListBucket'],
      resources: [migrationBucket.bucketArn],
    }));

    // Bedrock — model inference
    agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'BedrockInvokeModel',
      effect: iam.Effect.ALLOW,
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream', 'bedrock:Converse'],
      resources: ['*'],
    }));

    // AgentCore Code Interpreter — used by transform agent for validation
    agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'CodeInterpreterAccess',
      effect: iam.Effect.ALLOW,
      actions: [
        'bedrock-agentcore:CreateCodeInterpreter',
        'bedrock-agentcore:StartCodeInterpreterSession',
        'bedrock-agentcore:InvokeCodeInterpreter',
        'bedrock-agentcore:StopCodeInterpreterSession',
        'bedrock-agentcore:DeleteCodeInterpreter',
        'bedrock-agentcore:ListCodeInterpreters',
        'bedrock-agentcore:GetCodeInterpreter',
        'bedrock-agentcore:GetCodeInterpreterSession',
        'bedrock-agentcore:ListCodeInterpreterSessions',
      ],
      resources: [
        `arn:aws:bedrock-agentcore:${region}:${account}:code-interpreter/*`,
        `arn:aws:bedrock-agentcore:${region}:aws:code-interpreter/*`,
      ],
    }));

    // Common environment variables for all runtimes
    const commonEnv = {
      TABLE_NAME: props.tableName,
      BUCKET_NAME: props.bucketName,
      AWS_DEFAULT_REGION: cdk.Aws.REGION,
    };

    const commonTags = {
      Environment: 'dev',
      Application: 'lambda-runtime-migration',
    };

    // ─── Runtime #1: Discover ───
    const discoverRuntime = new bedrockagentcore.CfnRuntime(this, 'DiscoverRuntime', {
      agentRuntimeName: 'lambdaruntime_discover',
      description: 'Phase 1: Discovery + Enrichment + AI Prioritization',
      roleArn: agentRole.roleArn,
      agentRuntimeArtifact: {
        codeConfiguration: {
          code: {
            s3: {
              bucket: props.bucketName,
              prefix: 'agent/discover/deployment_package.zip',
            },
          },
          entryPoint: ['main.py'],
          runtime: 'PYTHON_3_13',
        },
      },
      networkConfiguration: { networkMode: 'PUBLIC' },
      protocolConfiguration: 'HTTP',
      environmentVariables: commonEnv,
      tags: commonTags,
    });

    // ─── Runtime #2: Analyze ───
    const analyzeRuntime = new bedrockagentcore.CfnRuntime(this, 'AnalyzeRuntime', {
      agentRuntimeName: 'lambdaruntime_analyze',
      description: 'Phase 2: Code Analysis + Complexity Assessment',
      roleArn: agentRole.roleArn,
      agentRuntimeArtifact: {
        codeConfiguration: {
          code: {
            s3: {
              bucket: props.bucketName,
              prefix: 'agent/analyze/deployment_package.zip',
            },
          },
          entryPoint: ['main.py'],
          runtime: 'PYTHON_3_13',
        },
      },
      networkConfiguration: { networkMode: 'PUBLIC' },
      protocolConfiguration: 'HTTP',
      environmentVariables: commonEnv,
      tags: commonTags,
    });

    // ─── Runtime #3: Transform ───
    const transformRuntime = new bedrockagentcore.CfnRuntime(this, 'TransformRuntime', {
      agentRuntimeName: 'lambdaruntime_transform',
      description: 'Phase 3: Code Generation + Validation',
      roleArn: agentRole.roleArn,
      agentRuntimeArtifact: {
        codeConfiguration: {
          code: {
            s3: {
              bucket: props.bucketName,
              prefix: 'agent/transform/deployment_package.zip',
            },
          },
          entryPoint: ['main.py'],
          runtime: 'PYTHON_3_13',
        },
      },
      networkConfiguration: { networkMode: 'PUBLIC' },
      protocolConfiguration: 'HTTP',
      environmentVariables: commonEnv,
      tags: commonTags,
    });

    // Export ARNs as class properties
    this.discoverRuntimeArn = discoverRuntime.attrAgentRuntimeArn;
    this.analyzeRuntimeArn = analyzeRuntime.attrAgentRuntimeArn;
    this.transformRuntimeArn = transformRuntime.attrAgentRuntimeArn;

    // Stack outputs
    new cdk.CfnOutput(this, 'DiscoverRuntimeArn', {
      value: discoverRuntime.attrAgentRuntimeArn,
      description: 'AgentCore Runtime ARN — Discover Agent',
      exportName: `LambdaMigrationDiscoverArn-${region}`,
    });

    new cdk.CfnOutput(this, 'AnalyzeRuntimeArn', {
      value: analyzeRuntime.attrAgentRuntimeArn,
      description: 'AgentCore Runtime ARN — Analyze Agent',
      exportName: `LambdaMigrationAnalyzeArn-${region}`,
    });

    new cdk.CfnOutput(this, 'TransformRuntimeArn', {
      value: transformRuntime.attrAgentRuntimeArn,
      description: 'AgentCore Runtime ARN — Transform Agent',
      exportName: `LambdaMigrationTransformArn-${region}`,
    });

    new cdk.CfnOutput(this, 'AgentRoleArn', {
      value: agentRole.roleArn,
      description: 'IAM Role ARN for the AgentCore Runtimes',
    });
  }
}
