import * as cdk from 'aws-cdk-lib';
import * as bedrockagentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';

/**
 * Configuration for a domain-specific RuntimeStack.
 */
export interface DomainRuntimeConfig {
  /** Short domain name, e.g. "cost", "health" */
  domainName: string;
  /** PascalCase prefix matching the InfraStack exports, e.g. "GOATCostAgent" */
  exportPrefix: string;
  /** ECR repository name matching the InfraStack, e.g. "goat-cost-agent-repository" */
  ecrRepoName: string;
  /** AgentCore runtime name, e.g. "goat_cost_agent" */
  runtimeName: string;
  /** Human-readable description for the AgentCore runtime */
  runtimeDescription: string;
  /** Relative path to agent source directory from CDK root */
  agentSourcePath: string;
  /** Additional environment variables for the AgentCore runtime */
  environmentVariables?: Record<string, string>;
}

export interface BaseRuntimeStackProps extends cdk.StackProps {
  /** Domain runtime configuration */
  config: DomainRuntimeConfig;
}

/**
 * G.O.A.T. BaseRuntimeStack — Shared base class for all domain RuntimeStacks.
 *
 * Imports from InfraStack via Fn.importValue(), uploads agent source to S3,
 * triggers CodeBuild via AwsCustomResource, waits for build via BuildWaiterFunction
 * Lambda, creates AgentCore CfnRuntime, and exports AgentRuntimeArn.
 *
 * Follows the lifecycle tracker runtime-stack.ts pattern.
 */
export class BaseRuntimeStack extends cdk.Stack {
  public readonly agentRuntimeArn: string;

  constructor(scope: Construct, id: string, props: BaseRuntimeStackProps) {
    super(scope, id, props);

    const { config } = props;
    const { domainName, exportPrefix, ecrRepoName, runtimeName, runtimeDescription, agentSourcePath } = config;

    // -----------------------------------------------------------------------
    // Import from InfraStack via Fn.importValue()
    // -----------------------------------------------------------------------
    const sourceBucketName = cdk.Fn.importValue(`${exportPrefix}SourceBucketName`);
    const buildProjectName = cdk.Fn.importValue(`${exportPrefix}BuildProjectName`);
    const buildProjectArn = cdk.Fn.importValue(`${exportPrefix}BuildProjectArn`);
    const agentRoleArn = cdk.Fn.importValue(`${exportPrefix}RuntimeRoleArn`);

    const sourceBucket = s3.Bucket.fromBucketName(this, 'SourceBucket', sourceBucketName);
    const agentRepository = ecr.Repository.fromRepositoryName(this, 'AgentRepo', ecrRepoName);
    const agentRole = iam.Role.fromRoleArn(this, 'AgentRole', agentRoleArn);

    // -----------------------------------------------------------------------
    // Upload agent source to S3
    // -----------------------------------------------------------------------
    const agentSourceUpload = new s3deploy.BucketDeployment(this, 'AgentSourceUpload', {
      sources: [s3deploy.Source.asset(agentSourcePath, {
        exclude: ['venv/**', '__pycache__/**', '*.pyc', '.git/**',
          'node_modules/**', '.DS_Store', '*.log', 'build/**', 'dist/**'],
      })],
      destinationBucket: sourceBucket,
      destinationKeyPrefix: 'agent-source/',
      prune: false,
      retainOnDelete: false,
    });

    // -----------------------------------------------------------------------
    // Trigger CodeBuild via AwsCustomResource
    // -----------------------------------------------------------------------
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
          actions: ['codebuild:StartBuild', 'codebuild:BatchGetBuilds'],
          resources: [buildProjectArn],
        }),
      ]),
    });
    buildTrigger.node.addDependency(agentSourceUpload);

    // -----------------------------------------------------------------------
    // Wait for build via custom Lambda (BuildWaiterFunction)
    // -----------------------------------------------------------------------
    const buildWaiterFunction = new lambda.Function(this, 'BuildWaiterFunction', {
      runtime: lambda.Runtime.NODEJS_22_X,
      handler: 'index.handler',
      code: lambda.Code.fromInline(getBuildWaiterCode()),
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
      properties: {
        BuildId: buildTrigger.getResponseField('build.id'),
      },
    });
    buildWaiter.node.addDependency(buildTrigger);

    // -----------------------------------------------------------------------
    // Create AgentCore CfnRuntime
    // -----------------------------------------------------------------------
    const envVars: Record<string, string> = {
      LOG_LEVEL: 'INFO',
      IMAGE_VERSION: new Date().toISOString(),
      ...config.environmentVariables,
    };

    const agentRuntime = new bedrockagentcore.CfnRuntime(this, 'AgentRuntime', {
      agentRuntimeName: runtimeName,
      description: runtimeDescription,
      roleArn: agentRole.roleArn,
      agentRuntimeArtifact: {
        containerConfiguration: {
          containerUri: `${agentRepository.repositoryUri}:latest`,
        },
      },
      networkConfiguration: { networkMode: 'PUBLIC' },
      protocolConfiguration: 'HTTP',
      environmentVariables: envVars,
    });
    agentRuntime.node.addDependency(buildWaiter);

    this.agentRuntimeArn = agentRuntime.attrAgentRuntimeArn;

    // -----------------------------------------------------------------------
    // Stack Outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'AgentRuntimeArn', {
      value: agentRuntime.attrAgentRuntimeArn,
      description: `${domainName} agent runtime ARN`,
      exportName: `${exportPrefix}RuntimeArn`,
    });
  }
}

/**
 * Returns the inline Node.js code for the BuildWaiterFunction Lambda.
 * Polls CodeBuild until the build completes or times out.
 */
function getBuildWaiterCode(): string {
  return [
    'const { CodeBuildClient, BatchGetBuildsCommand } = require("@aws-sdk/client-codebuild");',
    '',
    'exports.handler = async (event) => {',
    '  console.log("Event:", JSON.stringify(event));',
    '  if (event.RequestType === "Delete") {',
    '    return sendResponse(event, "SUCCESS", { Status: "DELETED" });',
    '  }',
    '  const buildId = event.ResourceProperties.BuildId;',
    '  const maxWaitMinutes = 14;',
    '  const pollIntervalSeconds = 10;',
    '  console.log("Waiting for build:", buildId);',
    '  const client = new CodeBuildClient({});',
    '  const startTime = Date.now();',
    '  const maxWaitMs = maxWaitMinutes * 60 * 1000;',
    '  while (Date.now() - startTime < maxWaitMs) {',
    '    try {',
    '      const response = await client.send(new BatchGetBuildsCommand({ ids: [buildId] }));',
    '      const build = response.builds[0];',
    '      const status = build.buildStatus;',
    '      console.log("Build status: " + status);',
    '      if (status === "SUCCEEDED") {',
    '        return await sendResponse(event, "SUCCESS", { Status: "SUCCEEDED" });',
    '      } else if (["FAILED", "FAULT", "TIMED_OUT", "STOPPED"].includes(status)) {',
    '        return await sendResponse(event, "FAILED", {}, "Build failed with status: " + status);',
    '      }',
    '      await new Promise(resolve => setTimeout(resolve, pollIntervalSeconds * 1000));',
    '    } catch (error) {',
    '      console.error("Error:", error);',
    '      return await sendResponse(event, "FAILED", {}, error.message);',
    '    }',
    '  }',
    '  return await sendResponse(event, "FAILED", {}, "Build timeout after " + maxWaitMinutes + " minutes");',
    '};',
    '',
    'async function sendResponse(event, status, data, reason) {',
    '  const responseBody = JSON.stringify({',
    '    Status: status,',
    '    Reason: reason || "See CloudWatch Log Stream: " + event.LogStreamName,',
    '    PhysicalResourceId: event.PhysicalResourceId || event.RequestId,',
    '    StackId: event.StackId,',
    '    RequestId: event.RequestId,',
    '    LogicalResourceId: event.LogicalResourceId,',
    '    Data: data',
    '  });',
    '  console.log("Response:", responseBody);',
    '  const https = require("https");',
    '  const url = require("url");',
    '  const parsedUrl = url.parse(event.ResponseURL);',
    '  return new Promise((resolve, reject) => {',
    '    const options = {',
    '      hostname: parsedUrl.hostname,',
    '      port: 443,',
    '      path: parsedUrl.path,',
    '      method: "PUT",',
    '      headers: { "Content-Type": "", "Content-Length": responseBody.length }',
    '    };',
    '    const request = https.request(options, (response) => {',
    '      console.log("Status: " + response.statusCode);',
    '      resolve(data);',
    '    });',
    '    request.on("error", (error) => {',
    '      console.error("Error:", error);',
    '      reject(error);',
    '    });',
    '    request.write(responseBody);',
    '    request.end();',
    '  });',
    '}',
  ].join('\n');
}
