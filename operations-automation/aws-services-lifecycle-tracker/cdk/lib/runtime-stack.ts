import * as cdk from 'aws-cdk-lib';
import * as bedrockagentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';

export interface AgentCoreStackProps extends cdk.StackProps {
  userPool: cognito.IUserPool;
  userPoolClient: cognito.IUserPoolClient;
  lifecycleTableName?: string;
  configTableName?: string;
  notificationTopicArn?: string;
}

export class AgentCoreStack extends cdk.Stack {
  public readonly agentRuntimeArn: string;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);

    // Import resources from infra stack
    const sourceBucketName = cdk.Fn.importValue('AWSServicesLifecycleTrackerSourceBucketName');
    const buildProjectName = cdk.Fn.importValue('AWSServicesLifecycleTrackerBuildProjectName');
    const buildProjectArn = cdk.Fn.importValue('AWSServicesLifecycleTrackerBuildProjectArn');

    const sourceBucket = s3.Bucket.fromBucketName(
      this,
      'SourceBucket',
      sourceBucketName
    );

    // Use existing ECR repository
    const agentRepository = ecr.Repository.fromRepositoryName(
      this,
      'LifecycleTrackerRepository',
      'aws-services-lifecycle-tracker-repository'
    );

    // Import existing IAM role
    const agentRole = iam.Role.fromRoleArn(
      this,
      'AgentRuntimeRole',
      cdk.Fn.importValue('AWSServicesLifecycleTrackerRuntimeRoleArn')
    );

    // Get Cognito discovery URL for inbound auth
    const region = cdk.Stack.of(this).region;
    const discoveryUrl = `https://cognito-idp.${region}.amazonaws.com/${props.userPool.userPoolId}/.well-known/openid-configuration`;

    // Note: Agent source files are uploaded to S3 by the deployment script (deploy-all.ps1)
    // This ensures files are always fresh and avoids CDK asset hash caching issues

    // Step 1: Trigger CodeBuild to build the Docker image
    const buildTrigger = new cr.AwsCustomResource(this, 'TriggerCodeBuild', {
      onCreate: {
        service: 'CodeBuild',
        action: 'startBuild',
        parameters: {
          projectName: buildProjectName,
        },
        physicalResourceId: cr.PhysicalResourceId.of(`build-${Date.now()}`),
      },
      onUpdate: {
        service: 'CodeBuild',
        action: 'startBuild',
        parameters: {
          projectName: buildProjectName,
        },
        physicalResourceId: cr.PhysicalResourceId.of(`build-${Date.now()}`),
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          actions: ['codebuild:StartBuild', 'codebuild:BatchGetBuilds'],
          resources: [buildProjectArn],
        }),
      ]),
    });

    // Step 2: Wait for build to complete using a custom Lambda
    const buildWaiterFunction = new lambda.Function(this, 'BuildWaiterFunction', {
      runtime: lambda.Runtime.NODEJS_22_X,
      handler: 'index.handler',
      code: lambda.Code.fromInline(`
const { CodeBuildClient, BatchGetBuildsCommand } = require('@aws-sdk/client-codebuild');

exports.handler = async (event) => {
  console.log('Event:', JSON.stringify(event));
  
  if (event.RequestType === 'Delete') {
    return sendResponse(event, 'SUCCESS', { Status: 'DELETED' });
  }
  
  const buildId = event.ResourceProperties.BuildId;
  const maxWaitMinutes = 14; // Lambda timeout is 15 min, leave 1 min buffer
  const pollIntervalSeconds = 10;
  
  console.log('Waiting for build:', buildId);
  
  const client = new CodeBuildClient({});
  const startTime = Date.now();
  const maxWaitMs = maxWaitMinutes * 60 * 1000;
  
  while (Date.now() - startTime < maxWaitMs) {
    try {
      const response = await client.send(new BatchGetBuildsCommand({ ids: [buildId] }));
      const build = response.builds[0];
      const status = build.buildStatus;
      
      console.log(\`Build status: \${status}\`);
      
      if (status === 'SUCCEEDED') {
        return await sendResponse(event, 'SUCCESS', { Status: 'SUCCEEDED' });
      } else if (['FAILED', 'FAULT', 'TIMED_OUT', 'STOPPED'].includes(status)) {
        return await sendResponse(event, 'FAILED', {}, \`Build failed with status: \${status}\`);
      }
      
      await new Promise(resolve => setTimeout(resolve, pollIntervalSeconds * 1000));
      
    } catch (error) {
      console.error('Error:', error);
      return await sendResponse(event, 'FAILED', {}, error.message);
    }
  }
  
  return await sendResponse(event, 'FAILED', {}, \`Build timeout after \${maxWaitMinutes} minutes\`);
};

async function sendResponse(event, status, data, reason) {
  const responseBody = JSON.stringify({
    Status: status,
    Reason: reason || \`See CloudWatch Log Stream: \${event.LogStreamName}\`,
    PhysicalResourceId: event.PhysicalResourceId || event.RequestId,
    StackId: event.StackId,
    RequestId: event.RequestId,
    LogicalResourceId: event.LogicalResourceId,
    Data: data
  });
  
  console.log('Response:', responseBody);
  
  const https = require('https');
  const url = require('url');
  const parsedUrl = url.parse(event.ResponseURL);
  
  return new Promise((resolve, reject) => {
    const options = {
      hostname: parsedUrl.hostname,
      port: 443,
      path: parsedUrl.path,
      method: 'PUT',
      headers: {
        'Content-Type': '',
        'Content-Length': responseBody.length
      }
    };
    
    const request = https.request(options, (response) => {
      console.log(\`Status: \${response.statusCode}\`);
      resolve(data);
    });
    
    request.on('error', (error) => {
      console.error('Error:', error);
      reject(error);
    });
    
    request.write(responseBody);
    request.end();
  });
}
      `),
      timeout: cdk.Duration.minutes(15), // Lambda max timeout is 15 minutes
      memorySize: 256,
    });

    buildWaiterFunction.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['codebuild:BatchGetBuilds'],
      resources: [buildProjectArn],
    }));

    // Custom resource that invokes the waiter Lambda
    const buildWaiter = new cdk.CustomResource(this, 'BuildWaiter', {
      serviceToken: buildWaiterFunction.functionArn,
      properties: {
        BuildId: buildTrigger.getResponseField('build.id'),
      },
    });

    buildWaiter.node.addDependency(buildTrigger);

    // Create the AgentCore Runtime with IAM authentication (supports both scheduler and frontend)
    const agentRuntime = new bedrockagentcore.CfnRuntime(this, 'AgentRuntime', {
      agentRuntimeName: 'aws_services_lifecycle_agent',
      description: 'AWS Services Lifecycle Tracker - AI-powered deprecation extraction agent',
      roleArn: agentRole.roleArn,

      // Container configuration
      agentRuntimeArtifact: {
        containerConfiguration: {
          containerUri: `${agentRepository.repositoryUri}:latest`,
        },
      },

      // Network configuration - PUBLIC for internet access
      networkConfiguration: {
        networkMode: 'PUBLIC',
      },

      // Protocol configuration
      protocolConfiguration: 'HTTP',

      // No authorizerConfiguration = defaults to IAM authentication (SigV4)
      // This allows both EventBridge Scheduler and frontend (via Cognito Identity Pool) to use IAM
      // Frontend can use X-Amzn-Bedrock-AgentCore-Runtime-User-Id header for user-specific operations

      // Environment variables
      environmentVariables: {
        LOG_LEVEL: 'INFO',
        IMAGE_VERSION: new Date().toISOString(),
        LIFECYCLE_TABLE_NAME: props.lifecycleTableName || 'aws-services-lifecycle',
        CONFIG_TABLE_NAME: props.configTableName || 'service-extraction-config',
        ...(props.notificationTopicArn && { NOTIFICATION_TOPIC_ARN: props.notificationTopicArn }),
      },

      tags: {
        Environment: 'dev',
        Application: 'strands-agent',
      },
    });

    // Ensure AgentCore runtime is created after build completes
    agentRuntime.node.addDependency(buildWaiter);

    // Store runtime info for frontend
    this.agentRuntimeArn = agentRuntime.attrAgentRuntimeArn;





    new cdk.CfnOutput(this, 'AgentRuntimeArn', {
      value: agentRuntime.attrAgentRuntimeArn,
      description: 'AWS Services Lifecycle Tracker Runtime ARN',
      exportName: 'AWSServicesLifecycleTrackerRuntimeArn',
    });

    new cdk.CfnOutput(this, 'EndpointName', {
      value: 'DEFAULT',
      description: 'Runtime Endpoint Name (DEFAULT auto-created)',
      exportName: 'AWSServicesLifecycleTrackerEndpointName',
    });

    new cdk.CfnOutput(this, 'Region', {
      value: region,
      description: 'AWS Region for Lifecycle Tracker Runtime',
      exportName: 'AWSServicesLifecycleTrackerRegion',
    });


  }
}
