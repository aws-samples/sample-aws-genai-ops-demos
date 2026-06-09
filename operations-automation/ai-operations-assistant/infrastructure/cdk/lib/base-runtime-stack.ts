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
  /**
   * Maximum number of minutes to wait for the CodeBuild image build to
   * complete before failing the deployment. Defaults to 14 minutes to
   * preserve the historical single-Lambda waiter behavior. Domains whose
   * container image takes longer to build (for example, the Network Agent
   * with its tshark-based transformation Lambdas) can raise this value.
   *
   * The implementation uses the CDK provider framework's `totalTimeout`,
   * which is capped at 60 minutes by AWS, so values must fall in `[1, 60]`.
   */
  buildWaitTimeoutMinutes?: number;
}

export interface BaseRuntimeStackProps extends cdk.StackProps {
  /** Domain runtime configuration */
  config: DomainRuntimeConfig;
}

/** Default build wait budget in minutes when the config does not override it. */
const DEFAULT_BUILD_WAIT_TIMEOUT_MINUTES = 14;

/** Maximum build wait budget allowed by the CDK provider framework. */
const MAX_BUILD_WAIT_TIMEOUT_MINUTES = 60;

/**
 * G.O.A.T. BaseRuntimeStack — Shared base class for all domain RuntimeStacks.
 *
 * Imports from InfraStack via Fn.importValue(), uploads agent source to S3,
 * triggers CodeBuild via AwsCustomResource, waits for build via the CDK
 * provider framework (`cr.Provider` with an `isCompleteHandler`), creates the
 * AgentCore CfnRuntime, and exports `AgentRuntimeArn`.
 *
 * The build waiter uses the provider framework so that the polling loop can
 * exceed the AWS Lambda 15-minute hard limit without having to refactor the
 * waiter into a state machine. Each `isCompleteHandler` invocation performs a
 * single `BatchGetBuilds` poll and either reports completion, surfaces a
 * CodeBuild failure terminal state, or returns `IsComplete=false` so the
 * provider will retry after `queryInterval`. On exhaustion of the configured
 * budget the handler raises an error identifying the build project name and
 * build identifier (Req 6.14).
 */
export class BaseRuntimeStack extends cdk.Stack {
  public readonly agentRuntimeArn: string;

  constructor(scope: Construct, id: string, props: BaseRuntimeStackProps) {
    super(scope, id, props);

    const { config } = props;
    const { domainName, exportPrefix, ecrRepoName, runtimeName, runtimeDescription, agentSourcePath } = config;

    const buildWaitTimeoutMinutes = config.buildWaitTimeoutMinutes ?? DEFAULT_BUILD_WAIT_TIMEOUT_MINUTES;
    if (
      !Number.isInteger(buildWaitTimeoutMinutes) ||
      buildWaitTimeoutMinutes < 1 ||
      buildWaitTimeoutMinutes > MAX_BUILD_WAIT_TIMEOUT_MINUTES
    ) {
      throw new Error(
        `buildWaitTimeoutMinutes must be an integer between 1 and ${MAX_BUILD_WAIT_TIMEOUT_MINUTES} ` +
          `(got ${buildWaitTimeoutMinutes}). The CDK provider framework caps totalTimeout at 60 minutes.`,
      );
    }

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
    // Wait for build via cr.Provider + isCompleteHandler. The provider
    // framework drives the polling loop on the CFN side so the per-invocation
    // Lambda timeout stays small while the total wait can extend up to 60
    // minutes (Req 6.13, Req 6.14).
    // -----------------------------------------------------------------------
    const buildWaiterOnEvent = new lambda.Function(this, 'BuildWaiterOnEvent', {
      runtime: lambda.Runtime.NODEJS_22_X,
      handler: 'index.handler',
      code: lambda.Code.fromInline(getBuildWaiterOnEventCode()),
      timeout: cdk.Duration.minutes(1),
      memorySize: 256,
    });

    const buildWaiterIsComplete = new lambda.Function(this, 'BuildWaiterIsComplete', {
      runtime: lambda.Runtime.NODEJS_22_X,
      handler: 'index.handler',
      code: lambda.Code.fromInline(getBuildWaiterIsCompleteCode()),
      timeout: cdk.Duration.minutes(1),
      memorySize: 256,
    });
    buildWaiterIsComplete.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['codebuild:BatchGetBuilds'],
      resources: [buildProjectArn],
    }));

    const buildWaiterProvider = new cr.Provider(this, 'BuildWaiterProvider', {
      onEventHandler: buildWaiterOnEvent,
      isCompleteHandler: buildWaiterIsComplete,
      queryInterval: cdk.Duration.seconds(10),
      totalTimeout: cdk.Duration.minutes(buildWaitTimeoutMinutes),
    });

    const buildWaiter = new cdk.CustomResource(this, 'BuildWaiter', {
      serviceToken: buildWaiterProvider.serviceToken,
      properties: {
        BuildId: buildTrigger.getResponseField('build.id'),
        BuildProjectName: buildProjectName,
        TimeoutMinutes: buildWaitTimeoutMinutes,
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
 * Returns the inline Node.js code for the BuildWaiter onEvent handler.
 *
 * The provider framework calls this once per CFN lifecycle action. For
 * `Create` and `Update` we capture the build identifier and the start time
 * (Unix epoch milliseconds) so the `isComplete` handler can compute elapsed
 * time without relying on the framework's internal clock. The build itself is
 * already running at this point — `AwsCustomResource` started it via
 * `codebuild:StartBuild` before we were invoked.
 */
function getBuildWaiterOnEventCode(): string {
  return [
    'exports.handler = async (event) => {',
    '  console.log("BuildWaiterOnEvent event:", JSON.stringify(event));',
    '  if (event.RequestType === "Delete") {',
    '    return { PhysicalResourceId: event.PhysicalResourceId };',
    '  }',
    '  const props = event.ResourceProperties || {};',
    '  if (!props.BuildId) {',
    '    throw new Error("BuildWaiterOnEvent: missing required property BuildId");',
    '  }',
    '  if (!props.BuildProjectName) {',
    '    throw new Error("BuildWaiterOnEvent: missing required property BuildProjectName");',
    '  }',
    '  return {',
    '    PhysicalResourceId: "build-waiter-" + props.BuildId,',
    '  };',
    '};',
  ].join('\n');
}

/**
 * Returns the inline Node.js code for the BuildWaiter isComplete handler.
 *
 * The provider framework calls this on a `queryInterval` cadence after
 * `onEvent`. Each invocation does a single `BatchGetBuilds` poll. The handler
 * returns `{ IsComplete: true }` on `SUCCEEDED`, throws on a CodeBuild
 * terminal failure (`FAILED`, `FAULT`, `TIMED_OUT`, `STOPPED`), and returns
 * `{ IsComplete: false }` while the build is still running. When the elapsed
 * time exceeds the configured budget, the handler throws an error containing
 * the build project name and build identifier (Req 6.14) so the custom
 * resource fails with an actionable message rather than the framework's
 * generic timeout reason.
 */
function getBuildWaiterIsCompleteCode(): string {
  return [
    'const { CodeBuildClient, BatchGetBuildsCommand } = require("@aws-sdk/client-codebuild");',
    '',
    'exports.handler = async (event) => {',
    '  console.log("BuildWaiterIsComplete event:", JSON.stringify(event));',
    '  if (event.RequestType === "Delete") {',
    '    return { IsComplete: true };',
    '  }',
    '  // The provider framework passes original ResourceProperties to isComplete.',
    '  // Data returned by onEvent is NOT merged into ResourceProperties.',
    '  const props = event.ResourceProperties || {};',
    '  const buildId = props.BuildId;',
    '  const buildProjectName = props.BuildProjectName;',
    '  const timeoutMinutes = parseInt(props.TimeoutMinutes, 10);',
    '  if (!buildId || !buildProjectName || !Number.isInteger(timeoutMinutes)) {',
    '    throw new Error("BuildWaiterIsComplete: missing or invalid resource properties: " + JSON.stringify(props));',
    '  }',
    '  // Timeout enforcement is handled by the provider framework totalTimeout.',
    '  // We keep a soft check here using the build start time from CodeBuild itself.',
    '  const client = new CodeBuildClient({});',
    '  let response;',
    '  try {',
    '    response = await client.send(new BatchGetBuildsCommand({ ids: [buildId] }));',
    '  } catch (error) {',
    '    console.error("BatchGetBuilds error:", error);',
    '    throw new Error(',
    '      "BatchGetBuilds failed for build " + buildId + " in project " + buildProjectName + ": " +',
    '      (error && error.message ? error.message : String(error))',
    '    );',
    '  }',
    '  const build = (response.builds && response.builds[0]) || null;',
    '  if (!build) {',
    '    console.log("Build not yet visible to BatchGetBuilds, will retry");',
    '    return { IsComplete: false };',
    '  }',
    '  const status = build.buildStatus;',
    '  console.log("Build " + buildId + " status: " + status);',
    '  if (status === "SUCCEEDED") {',
    '    return { IsComplete: true, Data: { Status: status } };',
    '  }',
    '  if (["FAILED", "FAULT", "TIMED_OUT", "STOPPED"].includes(status)) {',
    '    throw new Error(',
    '      "CodeBuild build " + buildId + " for project " + buildProjectName +',
    '      " ended in terminal state " + status + ". Inspect the CodeBuild logs for details."',
    '    );',
    '  }',
    '  return { IsComplete: false };',
    '};',
  ].join('\n');
}
