import * as cdk from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { KubectlV36Layer } from '@aws-cdk/lambda-layer-kubectl-v36';
import { Construct } from 'constructs';
import { spawnSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';

export interface FailureSimulatorApiStackProps extends cdk.StackProps {
  environment: string;
  projectName: string;
  vpc: ec2.Vpc;
  privateComputeSubnets: ec2.ISubnet[];
  eksSecurityGroup: ec2.ISecurityGroup;
  eksClusterName: string;
  alarmName: string;
  /** DevOps Agent region (cross-stack from DevOpsAgentStack or context fallback) */
  devOpsAgentRegion: string;
  /** DevOps Agent Space ID (cross-stack reference from DevOpsAgentStack) */
  devOpsAgentSpaceId: string;
}

// One place for the Lambda Python version: runtime of both functions and the
// pip wheel target must agree.
const PYTHON_RUNTIME = lambda.Runtime.PYTHON_3_14;
const PYTHON_VERSION = '3.14';
const LAMBDA_DIR = path.join(__dirname, '..', 'lambda', 'failure-simulator-api');
// Single source of truth for the Lab: engine handlers, UI cards and README all
// read this file. It is copied into the Lambda bundle at synth time.
const SCENARIOS_FILE = path.join(__dirname, '..', '..', 'lab', 'scenarios.yaml');
const STAGE_DIR = path.join(__dirname, '..', '.lab-stage');

/** Assemble the Lambda source tree: lambda/*.py + requirements.txt + lab/scenarios.yaml (never tests). */
function stageLab(): string {
  fs.rmSync(STAGE_DIR, { recursive: true, force: true });
  fs.mkdirSync(STAGE_DIR, { recursive: true });
  for (const file of fs.readdirSync(LAMBDA_DIR)) {
    if (file.endsWith('.py') || file === 'requirements.txt') {
      fs.copyFileSync(path.join(LAMBDA_DIR, file), path.join(STAGE_DIR, file));
    }
  }
  fs.copyFileSync(SCENARIOS_FILE, path.join(STAGE_DIR, 'scenarios.yaml'));
  return STAGE_DIR;
}

/**
 * Bundle without Docker: pip resolves Linux/x86_64 wheels for the Lambda runtime
 * (the durable SDK is pure Python, PyYAML ships manylinux wheels), then the staged
 * sources are copied alongside. Returning false lets CDK fall back to the Docker image.
 */
function bundleLabLocally(stageDir: string, outputDir: string): boolean {
  const pipArgs = [
    '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
    '--platform', 'manylinux2014_x86_64', '--only-binary=:all:',
    '--python-version', PYTHON_VERSION, '--implementation', 'cp',
    '--target', outputDir,
    '-r', path.join(stageDir, 'requirements.txt'),
  ];
  let installed = false;
  for (const python of ['python', 'python3']) {
    const run = spawnSync(python, pipArgs, { stdio: ['ignore', 'pipe', 'pipe'] });
    if (run.status === 0) {
      installed = true;
      break;
    }
  }
  if (!installed) {
    return false;
  }
  for (const file of fs.readdirSync(stageDir)) {
    if (file.endsWith('.py') || file.endsWith('.yaml')) {
      fs.copyFileSync(path.join(stageDir, file), path.join(outputDir, file));
    }
  }
  return true;
}

/**
 * DevOps Agent Lab backend: one code bundle, two Lambda functions.
 *  - engine: Lambda durable function running one injected scenario end to end
 *    (inject -> wait up to autoRevertSeconds for a rollback -> revert). Invoked
 *    through the `live` alias (durable functions need a qualified ARN).
 *  - api: plain function behind API Gateway serving the Lab UI (live cluster
 *    probes, scenario definitions, start/stop of engine executions, DevOps Agent
 *    usage and tasks).
 * No state store: the cluster says what is broken, the execution history says
 * where the run is.
 */
export class FailureSimulatorApiStack extends cdk.Stack {
  public readonly apiEndpoint: string;
  public readonly apiId: string;
  public readonly apiStageName: string;

  constructor(scope: Construct, id: string, props: FailureSimulatorApiStackProps) {
    super(scope, id, props);

    const {
      environment,
      projectName,
      vpc,
      privateComputeSubnets,
      eksSecurityGroup,
      eksClusterName,
      alarmName,
      devOpsAgentRegion,
      devOpsAgentSpaceId,
    } = props;

    // -----------------------------------------------------------------------
    // Security Group — needs to reach EKS API + internet
    // -----------------------------------------------------------------------
    const lambdaSg = new ec2.SecurityGroup(this, 'FailureSimulatorLambdaSg', {
      vpc,
      securityGroupName: `${projectName}-${environment}-failure-simulator-sg`,
      description: 'Security group for the DevOps Agent Lab Lambda functions',
      allowAllOutbound: true,
    });

    new ec2.CfnSecurityGroupIngress(this, 'EksIngressFromFailureSimulator', {
      groupId: eksSecurityGroup.securityGroupId,
      ipProtocol: 'tcp',
      fromPort: 443,
      toPort: 443,
      sourceSecurityGroupId: lambdaSg.securityGroupId,
      description: 'Allow Lab Lambda functions to reach the EKS API server',
    });

    // -----------------------------------------------------------------------
    // IAM Role — shared by both functions. deploy-all grants this ONE role an
    // EKS access entry (see FailureSimulatorLambdaRoleArn output).
    // -----------------------------------------------------------------------
    const lambdaRole = new iam.Role(this, 'FailureSimulatorLambdaRole', {
      roleName: `${projectName}-${environment}-failure-simulator-role`,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaVPCAccessExecutionRole'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicDurableExecutionRolePolicy'),
      ],
    });

    lambdaRole.addToPolicy(new iam.PolicyStatement({
      sid: 'EksDescribeCluster',
      actions: ['eks:DescribeCluster'],
      resources: [cdk.Stack.of(this).formatArn({ service: 'eks', resource: 'cluster', resourceName: eksClusterName })],
    }));

    lambdaRole.addToPolicy(new iam.PolicyStatement({
      sid: 'StsGetCallerIdentity',
      actions: ['sts:GetCallerIdentity'],
      resources: ['*'],
    }));

    lambdaRole.addToPolicy(new iam.PolicyStatement({
      sid: 'CloudWatchAlarmsAndMetrics',
      actions: ['cloudwatch:DescribeAlarms', 'cloudwatch:PutMetricData'],
      resources: ['*'],
    }));

    lambdaRole.addToPolicy(new iam.PolicyStatement({
      sid: 'DevOpsAgentReadOnly',
      actions: ['aidevops:GetAccountUsage', 'aidevops:ListBacklogTasks', 'aidevops:ListExecutions', 'aidevops:ListJournalRecords'],
      resources: ['*'],
    }));

    // -----------------------------------------------------------------------
    // kubectl Lambda Layer — keep within one minor version of the cluster
    // (see eksKubernetesVersion in bin/app.ts).
    // -----------------------------------------------------------------------
    const kubectlLayer = new KubectlV36Layer(this, 'KubectlLayer');

    // -----------------------------------------------------------------------
    // Shared code bundle
    // -----------------------------------------------------------------------
    const stageDir = stageLab();
    const labCode = lambda.Code.fromAsset(stageDir, {
      bundling: {
        image: PYTHON_RUNTIME.bundlingImage,
        command: [
          'bash', '-c',
          'pip install --quiet -r requirements.txt -t /asset-output && cp *.py *.yaml /asset-output/',
        ],
        local: { tryBundle: (outputDir: string) => bundleLabLocally(stageDir, outputDir) },
      },
    });

    const sharedEnvironment = {
      EKS_CLUSTER_NAME: eksClusterName,
      K8S_NAMESPACE: 'payment-demo',
      DEPLOYMENT_NAME: 'payment-processor',
      ALARM_NAME: alarmName,
      DNS_ALARM_NAME: `${projectName}-${environment}-dns-resolution-errors`,
      METRICS_NAMESPACE: `${projectName}/${environment}`,
    };

    const networking = {
      vpc,
      vpcSubnets: { subnets: privateComputeSubnets },
      securityGroups: [lambdaSg],
    };

    // -----------------------------------------------------------------------
    // Engine — Lambda durable function (one execution per injected scenario)
    // -----------------------------------------------------------------------
    const engineFunctionName = `${projectName}-${environment}-lab-engine`;
    // The API drives the engine, and both share the role above. Granting that with
    // engineFunction.functionArn would make the role's policy depend on the function
    // while the function depends on the policy (circular). Build the ARN from the
    // fixed name instead.
    const engineFunctionArn = cdk.Stack.of(this).formatArn({
      service: 'lambda', resource: 'function', resourceName: engineFunctionName,
      arnFormat: cdk.ArnFormat.COLON_RESOURCE_NAME,
    });
    lambdaRole.addToPolicy(new iam.PolicyStatement({
      sid: 'DriveLabEngine',
      actions: [
        'lambda:InvokeFunction',
        'lambda:GetDurableExecution',
        'lambda:GetDurableExecutionHistory',
        'lambda:ListDurableExecutionsByFunction',
        'lambda:SendDurableExecutionCallbackSuccess',
      ],
      resources: [engineFunctionArn, `${engineFunctionArn}:*`],
    }));

    const engineFunction = new lambda.Function(this, 'LabEngineFunction', {
      functionName: engineFunctionName,
      description: 'DevOps Agent Lab engine: inject -> wait for rollback -> revert (Lambda durable function)',
      runtime: PYTHON_RUNTIME,
      handler: 'engine.handler',
      code: labCode,
      layers: [kubectlLayer],
      role: lambdaRole,
      timeout: cdk.Duration.minutes(2),
      memorySize: 512,
      environment: sharedEnvironment,
      durableConfig: {
        // Longest scenario auto-revert is 10 minutes; leave room for retries.
        executionTimeout: cdk.Duration.hours(1),
        retentionPeriod: cdk.Duration.days(7),
      },
      ...networking,
    });

    // Durable functions must be invoked through a qualified ARN.
    const engineAlias = new lambda.Alias(this, 'LabEngineLiveAlias', {
      aliasName: 'live',
      version: engineFunction.currentVersion,
    });

    // -----------------------------------------------------------------------
    // API function — behind API Gateway, drives the engine
    // -----------------------------------------------------------------------
    const apiFunction = new lambda.Function(this, 'FailureSimulatorLambda', {
      functionName: `${projectName}-${environment}-failure-simulator`,
      description: 'DevOps Agent Lab API: scenarios, live status, inject/rollback, usage, agent tasks',
      runtime: PYTHON_RUNTIME,
      handler: 'index.handler',
      code: labCode,
      layers: [kubectlLayer],
      role: lambdaRole,
      timeout: cdk.Duration.seconds(60),
      memorySize: 512,
      environment: {
        ...sharedEnvironment,
        ENGINE_FUNCTION_ARN: engineAlias.functionArn,
        TRIGGER_LAMBDA_NAME: `${projectName}-${environment}-devops-trigger`,
        DEVOPS_AGENT_REGION: devOpsAgentRegion,
        DEVOPS_AGENT_SPACE_ID: devOpsAgentSpaceId,
      },
      ...networking,
    });

    // -----------------------------------------------------------------------
    // API Gateway
    // -----------------------------------------------------------------------
    const api = new apigateway.RestApi(this, 'FailureSimulatorApi', {
      restApiName: `${projectName}-${environment}-failure-simulator-api`,
      description: 'DevOps Agent Lab API for the EKS demo',
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type', 'Authorization'],
      },
      deployOptions: {
        stageName: 'prod',
        loggingLevel: apigateway.MethodLoggingLevel.INFO,
        metricsEnabled: true,
      },
    });

    const integration = new apigateway.LambdaIntegration(apiFunction);
    const admin = api.root.addResource('admin');
    admin.addResource('status').addMethod('GET', integration);
    admin.addResource('usage').addMethod('GET', integration);
    admin.addResource('tasks').addMethod('GET', integration);

    // /admin/scenarios lists definitions; /admin/scenarios/{id}/inject drives one.
    // Scenario ids come from lab/scenarios.yaml, so the route is a path parameter.
    const scenarios = admin.addResource('scenarios');
    scenarios.addMethod('GET', integration);
    const inject = scenarios.addResource('{scenarioId}').addResource('inject');
    inject.addMethod('POST', integration);
    inject.addMethod('DELETE', integration);

    this.apiEndpoint = api.url;
    this.apiId = api.restApiId;
    this.apiStageName = 'prod';

    // -----------------------------------------------------------------------
    // Outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'FailureSimulatorApiEndpoint', {
      description: 'DevOps Agent Lab API Gateway endpoint URL',
      value: api.url,
    });

    new cdk.CfnOutput(this, 'FailureSimulatorLambdaRoleArn', {
      description: 'Lab Lambda IAM Role ARN (add to EKS access entries)',
      value: lambdaRole.roleArn,
    });

    new cdk.CfnOutput(this, 'LabEngineAliasArn', {
      description: 'Qualified ARN of the Lab engine durable function (invoke with --durable-execution-name)',
      value: engineAlias.functionArn,
    });
  }
}
