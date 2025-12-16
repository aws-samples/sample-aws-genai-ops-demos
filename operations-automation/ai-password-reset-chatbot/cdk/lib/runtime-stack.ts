import * as cdk from 'aws-cdk-lib';
import * as bedrockagentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';

export interface PasswordResetRuntimeStackProps extends cdk.StackProps {
  userPoolId: string;
  userPoolClientId: string;
}

/**
 * Runtime Stack
 * 
 * Creates the AgentCore Runtime for the Password Reset Chatbot.
 * 
 * KEY DIFFERENCE FROM ORIGINAL SAMPLE:
 * - NO JWT authorizer - allows anonymous access to the chatbot
 * - Passes User Pool ID/Client ID as environment variables for Cognito API calls
 */
export class PasswordResetRuntimeStack extends cdk.Stack {
  public readonly agentRuntimeArn: string;

  constructor(scope: Construct, id: string, props: PasswordResetRuntimeStackProps) {
    super(scope, id, props);

    // Import resources from infra stack
    const sourceBucketName = cdk.Fn.importValue('PasswordResetSourceBucketName');
    const buildProjectName = cdk.Fn.importValue('PasswordResetBuildProjectName');
    const buildProjectArn = cdk.Fn.importValue('PasswordResetBuildProjectArn');

    const sourceBucket = s3.Bucket.fromBucketName(this, 'SourceBucket', sourceBucketName);

    // Use existing ECR repository
    const agentRepository = ecr.Repository.fromRepositoryName(
      this, 'AgentRepository', 'password_reset_agent_repository'
    );

    // Import existing IAM role
    const agentRole = iam.Role.fromRoleArn(
      this, 'AgentRuntimeRole', cdk.Fn.importValue('PasswordResetRuntimeRoleArn')
    );

    const region = cdk.Stack.of(this).region;


    // Upload agent source files
    const agentSourceUpload = new s3deploy.BucketDeployment(this, 'AgentSourceUpload', {
      sources: [s3deploy.Source.asset('../agent', {
        exclude: [
          'venv/**', '__pycache__/**', '*.pyc', '.git/**',
          'node_modules/**', '.DS_Store', '*.log', 'build/**', 'dist/**',
        ]
      })],
      destinationBucket: sourceBucket,
      destinationKeyPrefix: 'agent-source/',
      prune: false,
      retainOnDelete: false,
    });

    // Trigger CodeBuild
    const buildTrigger = new cr.AwsCustomResource(this, 'TriggerCodeBuild', {
      onCreate: {
        service: 'CodeBuild',
        action: 'startBuild',
        parameters: { projectName: buildProjectName },
        physicalResourceId: cr.PhysicalResourceId.of(`build-${Date.now()}`),
      },
      onUpdate: {
        service: 'CodeBuild',
        action: 'startBuild',
        parameters: { projectName: buildProjectName },
        physicalResourceId: cr.PhysicalResourceId.of(`build-${Date.now()}`),
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          actions: ['codebuild:StartBuild', 'codebuild:BatchGetBuilds'],
          resources: [buildProjectArn],
        }),
      ]),
      timeout: cdk.Duration.minutes(5),
    });
    buildTrigger.node.addDependency(agentSourceUpload);

    // Lambda to wait for build completion
    const buildWaiterFunction = new lambda.Function(this, 'BuildWaiterFunction', {
      runtime: lambda.Runtime.NODEJS_22_X,
      handler: 'index.handler',
      code: lambda.Code.fromInline(`
const { CodeBuildClient, BatchGetBuildsCommand } = require('@aws-sdk/client-codebuild');
exports.handler = async (event) => {
  console.log('Event:', JSON.stringify(event));
  if (event.RequestType === 'Delete') return sendResponse(event, 'SUCCESS', { Status: 'DELETED' });
  const buildId = event.ResourceProperties.BuildId;
  const client = new CodeBuildClient({});
  const startTime = Date.now();
  const maxWaitMs = 14 * 60 * 1000;
  while (Date.now() - startTime < maxWaitMs) {
    const response = await client.send(new BatchGetBuildsCommand({ ids: [buildId] }));
    const status = response.builds[0].buildStatus;
    console.log('Build status:', status);
    if (status === 'SUCCEEDED') return sendResponse(event, 'SUCCESS', { Status: 'SUCCEEDED' });
    if (['FAILED', 'FAULT', 'TIMED_OUT', 'STOPPED'].includes(status))
      return sendResponse(event, 'FAILED', {}, 'Build failed: ' + status);
    await new Promise(r => setTimeout(r, 30000));
  }
  return sendResponse(event, 'FAILED', {}, 'Build timeout');
};
async function sendResponse(event, status, data, reason) {
  const body = JSON.stringify({
    Status: status, Reason: reason || 'See logs', PhysicalResourceId: event.PhysicalResourceId || event.RequestId,
    StackId: event.StackId, RequestId: event.RequestId, LogicalResourceId: event.LogicalResourceId, Data: data
  });
  const https = require('https'), url = require('url'), parsed = url.parse(event.ResponseURL);
  return new Promise((resolve, reject) => {
    const req = https.request({ hostname: parsed.hostname, port: 443, path: parsed.path, method: 'PUT',
      headers: { 'Content-Type': '', 'Content-Length': body.length }
    }, res => { console.log('Status:', res.statusCode); resolve(data); });
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}
      `),
      timeout: cdk.Duration.minutes(15),
      memorySize: 256,
    });


    buildWaiterFunction.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['codebuild:BatchGetBuilds'],
      resources: [buildProjectArn],
    }));

    const buildWaiter = new cdk.CustomResource(this, 'BuildWaiter', {
      serviceToken: buildWaiterFunction.functionArn,
      properties: { BuildId: buildTrigger.getResponseField('build.id') },
    });
    buildWaiter.node.addDependency(buildTrigger);

    // Create AgentCore Runtime - NO JWT AUTHORIZER (anonymous access)
    const agentRuntime = new bedrockagentcore.CfnRuntime(this, 'AgentRuntime', {
      agentRuntimeName: 'password_reset_agent',
      description: 'Password Reset Chatbot - Anonymous access enabled',
      roleArn: agentRole.roleArn,

      agentRuntimeArtifact: {
        containerConfiguration: {
          containerUri: `${agentRepository.repositoryUri}:latest`,
        },
      },

      networkConfiguration: {
        networkMode: 'PUBLIC',
      },

      protocolConfiguration: 'HTTP',

      // NO authorizerConfiguration - this enables anonymous access
      // The agent can be invoked without any authentication token

      environmentVariables: {
        LOG_LEVEL: 'INFO',
        IMAGE_VERSION: new Date().toISOString(),
        // Pass Cognito config to agent for ForgotPassword/ConfirmForgotPassword calls
        USER_POOL_ID: props.userPoolId,
        USER_POOL_CLIENT_ID: props.userPoolClientId,
      },

      tags: {
        Environment: 'dev',
        Application: 'password-reset-chatbot',
      },
    });

    agentRuntime.node.addDependency(buildWaiter);
    this.agentRuntimeArn = agentRuntime.attrAgentRuntimeArn;

    new cdk.CfnOutput(this, 'AgentRuntimeArn', {
      value: agentRuntime.attrAgentRuntimeArn,
      description: 'AgentCore Runtime ARN',
      exportName: 'PasswordResetRuntimeArn',
    });

    new cdk.CfnOutput(this, 'Region', {
      value: region,
      description: 'AWS Region',
      exportName: 'PasswordResetRegion',
    });

    new cdk.CfnOutput(this, 'UserPoolId', {
      value: props.userPoolId,
      description: 'Cognito User Pool ID (for testing)',
      exportName: 'PasswordResetUserPoolIdOutput',
    });
  }
}
